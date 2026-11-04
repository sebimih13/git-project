#!/usr/bin/env python3
"""Build a receiver host, create a disposable repo, checkout, and verify spans."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import time
import urllib.request

from adapter import adapt, load_batches

ROOT = Path(__file__).resolve().parent


def clean_environment():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_TRACE2", "GIT_CONFIG_", "HOOK_DEMO_"))
           and k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")}
    env.update(GIT_TRACE2_EVENT="0", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return env


def install_hook(repo):
    """Install into this demo's newly created repo; never replace a user hook."""
    hooks = repo / ".git/hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("post-checkout", "checks.py", "trace2.py"):
        target = hooks / name
        if target.exists():
            raise FileExistsError(target)
        shutil.copyfile(ROOT / name, target)
        target.chmod(0o755)


def build_collector():
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    binary = build / "collector"
    env = dict(os.environ)
    env.setdefault("GOCACHE", "/tmp/hook-demo-go-build")
    env.setdefault("GOMODCACHE", "/tmp/hook-demo-go-mod")
    subprocess.run(["go", "mod", "tidy"], cwd=ROOT / "collector", env=env, check=True)
    subprocess.run(["go", "build", "-buildvcs=false", "-o", str(binary), "."], cwd=ROOT / "collector", env=env, check=True)
    return binary


def run(args):
    binary = build_collector()
    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix=args.engine + "-", dir=output_root))
    # Keep the Unix socket path short even if --output is deeply nested.
    with tempfile.TemporaryDirectory(prefix="hook-demo-") as sockdir:
        repo = out / "repo"
        env = clean_environment()
        def git(*command):
            return subprocess.check_output(["git", "-C", str(repo), *command], env=env, text=True).strip()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], env=env, check=True)
        git("-c", "user.name=Hook Demo", "-c", "user.email=demo@example.invalid",
            "commit", "-q", "--allow-empty", "-m", "initial")
        git("branch", "feature/demo")
        install_hook(repo)
        target = git("rev-parse", "HEAD") if args.detached else "feature/demo"
        if args.engine == "git":
            command = ["git", "-C", str(repo), "checkout", "--quiet", target]
        else:
            if not args.classpath:
                raise ValueError("--classpath is required with --engine jgit")
            subprocess.run(["javac", "--release", "17", "-cp", args.classpath,
                            "-d", str(ROOT / "build"), str(ROOT / "DemoCheckout.java")], check=True)
            command = ["java", "-cp", str(ROOT / "build") + os.pathsep + args.classpath,
                       "DemoCheckout", str(repo), target]
        socket = Path(sockdir) / "trace.sock"
        raw = out / "receiver-raw.jsonl"
        with (out / "receiver.stderr").open("w") as log:
            collector = subprocess.Popen([str(binary), "--socket", str(socket), "--output", str(raw)],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(collector.stdout, selectors.EVENT_READ)
                    if not selector.select(timeout=10) or collector.stdout.readline().strip() != "READY":
                        raise RuntimeError("receiver failed to become ready; inspect receiver.stderr")
                env.update(GIT_TRACE2_EVENT=f"af_unix:stream:{socket}",
                           GIT_TRACE2_EVENT_BRIEF="0", GIT_TRACE2_EVENT_NESTING="20")
                if args.fail:
                    env["HOOK_DEMO_FAIL"] = args.fail
                completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                (out / "checkout.stdout").write_text(completed.stdout)
                (out / "checkout.stderr").write_text(completed.stderr)
                # Read until both independent receiver workers have exported.
                deadline = time.monotonic() + 10
                last_error = None
                while time.monotonic() < deadline:
                    try:
                        document = load_batches(raw)
                        adapted, checks = adapt(document)
                        if len(checks) == 3:
                            break
                    except (ValueError, KeyError) as error:
                        last_error = error
                    time.sleep(0.05)
                else:
                    raise RuntimeError(f"missing completed hook/check spans: {last_error}")
            finally:
                if collector.poll() is None:
                    collector.stdin.write("STOP\n")
                    collector.stdin.flush()
                try:
                    collector.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    collector.kill()
                    collector.wait()
            if collector.returncode != 0:
                raise RuntimeError("receiver exited unsuccessfully")
        if not args.fail and completed.returncode != 0:
            raise RuntimeError(completed.stderr)
        if args.fail and completed.returncode == 0:
            raise AssertionError("failing check must fail checkout/hook")
        expected = ["Check1", "Check2", "Check3"]
        assert [c["check"] for c in checks] == expected, checks
        assert len({c["hook_span_id"] for c in checks}) == 1
        for check in checks:
            assert check["parent_span_id"] == check["hook_span_id"]
            assert check["duration_seconds"] > 0
            assert check["outcome"] == ("error" if check["check"] == args.fail else "success")
        if args.detached:
            assert git("rev-parse", "HEAD") == target
        else:
            assert git("symbolic-ref", "HEAD") == "refs/heads/feature/demo"
        (out / "otlp.json").write_text(json.dumps(adapted, indent=2) + "\n")
        subprocess.run([str(binary), "--validate-otlp", str(out / "otlp.json")], check=True)
        report = dict(engine=args.engine, checkout_exit=completed.returncode,
                      detached=args.detached, failed_check=args.fail, checks=checks,
                      command=command, git_version=subprocess.check_output(["git", "--version"], text=True).strip(),
                      collector_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
        (out / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"Verified {args.engine}: checkout -> post-checkout")
        for check in checks:
            print(f"  {check['check']}: {check['duration_seconds']:.6f}s ({check['outcome']})")
        print(f"Artifacts: {out}")
        if args.otlp_endpoint:
            data = json.dumps(adapted).encode()
            request = urllib.request.Request(args.otlp_endpoint, data=data,
                                             headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read()
                if body and json.loads(body).get("partialSuccess"):
                    raise RuntimeError(f"OTLP partial success: {body.decode()}")
                print(f"OTLP HTTP export: {response.status}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("git", "jgit"), default="git")
    parser.add_argument("--classpath", help="JGit classes or jar plus runtime dependency jars")
    parser.add_argument("--fail", choices=("Check1", "Check2", "Check3"))
    parser.add_argument("--detached", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    parser.add_argument("--otlp-endpoint", help="Explicit OTLP/HTTP traces URL, e.g. http://localhost:4318/v1/traces")
    run(parser.parse_args())
