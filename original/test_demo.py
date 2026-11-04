import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from adapter import adapt, attributes
from checks import current_branch

ROOT = Path(__file__).resolve().parent


def attr(**values):
    return [{"key": k, "value": {"stringValue": str(v)}} for k, v in values.items()]


def fixture():
    hook = dict(name="child(hook:post-checkout)", traceId="a" * 32, spanId="b" * 16,
                parentSpanId="c" * 16, startTimeUnixNano="100", endTimeUnixNano="500",
                attributes=attr(**{"trace2.span.type": "child", "trace2.child.class": "hook",
                                   "trace2.child.hook": "post-checkout", "trace2.child.pid": "42"}))
    hint = dict(schema="hook-check-demo/v1", owner_sid="parent", hook="post-checkout",
                ancestor_pids=[43, 42, 41], check="Check2", branch="refs/heads/feature/demo")
    measurement = {"hook-check": {"result": {"outcome": "error", "duration_seconds": 0.02}}}
    check = dict(name="hook-check:Check2", traceId="a" * 32, spanId="d" * 16,
                 parentSpanId="e" * 16, startTimeUnixNano="200", endTimeUnixNano="300",
                 attributes=attr(**{"trace2.region.message": json.dumps(hint),
                                    "trace2.region.data": json.dumps(measurement)}))
    resources = [dict(resource=dict(attributes=attr(**{"trace2.cmd.sid": sid})),
                      scopeSpans=[dict(spans=[span])])
                 for sid, span in (("parent", hook), ("parent/python", check))]
    return dict(resourceSpans=resources)


class CorrelationTests(unittest.TestCase):
    def test_exact_parent_failure_status_and_preserved_evidence(self):
        document = fixture()
        original = copy.deepcopy(document)
        output, checks = adapt(document)
        self.assertEqual(document, original)
        check = output["resourceSpans"][1]["scopeSpans"][0]["spans"][0]
        self.assertEqual(check["parentSpanId"], "b" * 16)
        self.assertEqual(check["status"]["code"], 2)
        self.assertEqual(attributes(check["attributes"])["git.ref.full"], "refs/heads/feature/demo")
        self.assertEqual(checks[0]["outcome"], "error")

    def test_rejects_ambiguous_hook(self):
        document = fixture()
        hooks = document["resourceSpans"][0]["scopeSpans"][0]["spans"]
        hooks.append(copy.deepcopy(hooks[0]))
        with self.assertRaisesRegex(ValueError, "got 2"):
            adapt(document)

    def test_never_attaches_to_another_session_or_trace(self):
        for changed in ("sid", "pid", "trace", "time"):
            with self.subTest(changed=changed):
                document = fixture()
                parent = document["resourceSpans"][0]
                hook = parent["scopeSpans"][0]["spans"][0]
                if changed == "sid":
                    parent["resource"]["attributes"] = attr(**{"trace2.cmd.sid": "another-session"})
                elif changed == "pid":
                    for item in hook["attributes"]:
                        if item["key"] == "trace2.child.pid":
                            item["value"]["stringValue"] = "999"
                elif changed == "trace":
                    hook["traceId"] = "f" * 32
                else:
                    hook["endTimeUnixNano"] = "250"
                with self.assertRaisesRegex(ValueError, "got 0"):
                    adapt(document)


class HookTests(unittest.TestCase):
    def test_unavailable_branch_lookup_is_not_detached_head(self):
        with mock.patch("checks.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(current_branch())
        with mock.patch("checks.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            self.assertEqual(current_branch(), "")

    def run_checks(self, env):
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("GIT_TRACE2", "HOOK_DEMO_"))}
        base.update(env)
        return subprocess.run([sys.executable, str(ROOT / "checks.py"), "old", "new", "1"],
                              env=base, text=True, capture_output=True, timeout=10)

    def test_disabled_tracing_still_runs_all_checks(self):
        result = self.run_checks({"GIT_TRACE2_EVENT": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [f"Check{i}: OK" for i in (1, 2, 3)])

    def test_missing_socket_does_not_fail_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_checks({"GIT_TRACE2_EVENT": f"af_unix:stream:{tmp}/absent.sock",
                                      "GIT_TRACE2_PARENT_SID": "parent"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tracing disabled", result.stderr)
        self.assertIn("Check3: OK", result.stdout)

    def test_failure_balances_regions_and_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "checks.jsonl"
            result = self.run_checks({"GIT_TRACE2_EVENT": str(target),
                                      "GIT_TRACE2_PARENT_SID": "parent", "HOOK_DEMO_FAIL": "Check2"})
            events = [json.loads(line) for line in target.read_text().splitlines()]
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(events[0]["event"], "version")
        self.assertEqual(events[-1]["event"], "atexit")
        self.assertEqual(events[-1]["code"], 1)
        self.assertEqual([e["label"] for e in events if e["event"] == "region_leave"],
                         ["Check1", "Check2", "Check3"])
        outcomes = [e["value"]["outcome"] for e in events if e["event"] == "data_json"]
        self.assertEqual(outcomes, ["success", "error", "success"])
        self.assertTrue(all(e["sid"].startswith("parent/") for e in events))


if __name__ == "__main__":
    unittest.main()
