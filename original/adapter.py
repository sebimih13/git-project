"""Correlate explicitly marked checks with the real Git hook child span.

Finite-capture adapter, not a streaming Collector processor. It preserves the
original receiver output and changes only marked check spans in a separate file.
"""
import copy
import json


def attributes(items):
    return {item["key"]: item["value"].get("stringValue", "") for item in items}


def set_attribute(span, key, value):
    items = span.setdefault("attributes", [])
    items[:] = [item for item in items if item["key"] != key]
    items.append(dict(key=key, value=dict(stringValue=str(value))))


def spans(document):
    for resource in document.get("resourceSpans", []):
        attrs = attributes(resource.get("resource", {}).get("attributes", []))
        for scope in resource.get("scopeSpans", []):
            for span in scope.get("spans", []):
                yield span, attrs


def adapt(document):
    result = copy.deepcopy(document)
    entries = list(spans(result))
    checks = []
    for span, resource in entries:
        attrs = attributes(span.get("attributes", []))
        try:
            hint = json.loads(attrs.get("trace2.region.message", "{}"))
        except (ValueError, TypeError):
            continue
        if not isinstance(hint, dict) or hint.get("schema") != "hook-check-demo/v1":
            continue
        # Never guess from timestamps or attach to another process's hook.
        candidates = []
        for hook, hook_resource in entries:
            ha = attributes(hook.get("attributes", []))
            if (ha.get("trace2.span.type") == "child"
                    and ha.get("trace2.child.class") == "hook"
                    and ha.get("trace2.child.hook") == hint["hook"]
                    and hook_resource.get("trace2.cmd.sid") == hint["owner_sid"]
                    and hook["traceId"] == span["traceId"]
                    and ha.get("trace2.child.pid") in {str(p) for p in hint["ancestor_pids"]}
                    and int(hook["startTimeUnixNano"]) <= int(span["startTimeUnixNano"])
                    and int(span["endTimeUnixNano"]) <= int(hook["endTimeUnixNano"])):
                candidates.append(hook)
        if len(candidates) != 1:
            raise ValueError(f"{hint['check']}: expected one exact hook match, got {len(candidates)}")
        hook = candidates[0]
        set_attribute(span, "hook_demo.original_parent_span_id", span.get("parentSpanId", ""))
        span["parentSpanId"] = hook["spanId"]
        span["name"] = hint["check"]
        data = json.loads(attrs.get("trace2.region.data", "{}"))
        measurement = data["hook-check"]["result"]
        set_attribute(span, "check.name", hint["check"])
        set_attribute(span, "check.outcome", measurement["outcome"])
        span["status"] = {"code": 2 if measurement["outcome"] == "error" else 1}
        branch = hint.get("branch", "")
        if isinstance(branch, str) and branch.startswith("refs/heads/"):
            for target in (span, hook):
                set_attribute(target, "git.ref.full", branch)
                set_attribute(target, "vcs.ref.head.name", branch[len("refs/heads/"):])
                set_attribute(target, "vcs.ref.head.type", "branch")
        elif branch == "":
            set_attribute(span, "git.head.detached", "true")
        else:
            set_attribute(span, "hook_demo.branch_lookup_failed", "true")
        checks.append(dict(check=hint["check"], outcome=measurement["outcome"],
                           duration_seconds=measurement["duration_seconds"],
                           trace_id=span["traceId"], span_id=span["spanId"],
                           parent_span_id=span["parentSpanId"], hook_span_id=hook["spanId"]))
    return result, sorted(checks, key=lambda check: check["check"])


def load_batches(path):
    document = {"resourceSpans": []}
    for line in path.read_text().splitlines():
        batch = json.loads(line)
        document["resourceSpans"].extend(batch.get("resourceSpans", []))
    return document


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="collector's receiver-raw.jsonl")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    document, checks = adapt(load_batches(args.input))
    args.output.write_text(json.dumps(document, indent=2) + "\n")
    print(json.dumps(checks, indent=2))
