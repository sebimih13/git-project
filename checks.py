#!/usr/bin/env python3
"""Three example checks and a small, dependency-free Trace2 event writer."""
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid


def ancestor_pids():
    """Include shell wrappers between JGit and Python; Linux demo only."""
    result, pid = [], os.getpid()
    for _ in range(32):
        if pid <= 0 or pid in result:
            break
        result.append(pid)
        try:
            # comm in /proc/PID/stat may contain spaces and parentheses.
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            pid = int(fields[1])
        except (OSError, ValueError, IndexError):
            break
    return result


class Trace2:
    def __init__(self):
        self.started = time.perf_counter()
        self.parent_sid = os.environ.get("GIT_TRACE2_PARENT_SID", "")
        component = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        component += f"-P{os.getpid():08x}-{uuid.uuid4().hex[:8]}"
        self.sid = f"{self.parent_sid}/{component}"
        self.socket = None
        self.file = None
        target = os.environ.get("GIT_TRACE2_EVENT", "")
        if not target or target == "0" or not self.parent_sid:
            return
        try:
            if target.startswith("af_unix:"):
                path = target[len("af_unix:"):]
                if path.startswith("stream:"):
                    path = path[len("stream:"):]
                if not path.startswith("/"):
                    raise ValueError("use an absolute Unix stream socket path")
                self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.socket.settimeout(0.5)
                self.socket.connect(path)
            elif Path(target).is_absolute():
                self.file = open(target, "ab", buffering=0)
            else:
                raise ValueError("demo supports absolute files and Unix stream sockets")
        except (OSError, ValueError) as error:
            self.disable(error)
        self.emit("version", evt="4", exe="hook-check-demo/1")
        self.emit("start", argv=["hook-check-demo", *sys.argv[1:]], t_abs=self.elapsed())
        self.emit("cmd_name", name="post-checkout-checks", hierarchy="checkout/post-checkout-checks")
        if self.socket is not None or self.file is not None:
            os.environ["GIT_TRACE2_PARENT_SID"] = self.sid

    def elapsed(self):
        return time.perf_counter() - self.started

    def disable(self, error):
        print(f"hook demo tracing disabled: {error}", file=sys.stderr)
        self.close()

    def close(self):
        for handle in (self.socket, self.file):
            if handle is not None:
                with contextlib.suppress(OSError):
                    handle.close()
        self.socket = self.file = None

    def emit(self, event, **fields):
        if self.socket is None and self.file is None:
            return
        record = dict(event=event, sid=self.sid, thread="main",
                      time=dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                      **fields)
        data = (json.dumps(record, separators=(",", ":")) + "\n").encode()
        try:
            if self.socket is not None:
                self.socket.sendall(data)
            else:
                self.file.write(data)
        except OSError as error:
            self.disable(error)  # Telemetry failures do not fail the hook.

    @contextlib.contextmanager
    def check(self, name, branch):
        # Custom correlation metadata carried in a standard Trace2 msg field.
        hint = dict(schema="hook-check-demo/v1", owner_sid=self.parent_sid,
                    hook="post-checkout", ancestor_pids=ancestor_pids(),
                    check=name, branch=branch)
        self.emit("region_enter", nesting=1, category="hook-check", label=name,
                  msg=json.dumps(hint, separators=(",", ":")))
        started, outcome = time.perf_counter(), "success"
        try:
            yield
        except BaseException:
            outcome = "error"
            raise
        finally:
            duration = time.perf_counter() - started
            self.emit("data_json", nesting=2, category="hook-check", key="result",
                      t_abs=self.elapsed(), t_rel=duration,
                      value=dict(outcome=outcome, duration_seconds=duration))
            self.emit("region_leave", nesting=1, category="hook-check", label=name,
                      t_rel=duration)

    def finish(self, code):
        self.emit("exit", code=code, t_abs=self.elapsed())
        self.emit("atexit", code=code, t_abs=self.elapsed())
        self.close()  # EOF is required for this receiver to export.


def current_branch():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_TRACE2")}
    env["GIT_TRACE2_EVENT"] = "0"
    # This is a metadata lookup, not another operation to trace.
    try:
        result = subprocess.run(["git", "symbolic-ref", "--quiet", "HEAD"],
                                env=env, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None  # Missing metadata must not prevent checks from running.
    if result.returncode == 0:
        return result.stdout.strip()
    return "" if result.returncode == 1 else None


def Check1():
    time.sleep(0.02)


def Check2():
    time.sleep(0.03)


def Check3():
    time.sleep(0.04)


def main():
    tracer, code = Trace2(), 0
    try:
        branch = current_branch()
        for check in (Check1, Check2, Check3):
            try:
                with tracer.check(check.__name__, branch):
                    check()
                    if os.environ.get("HOOK_DEMO_FAIL") == check.__name__:
                        raise RuntimeError("requested demo failure")
                print(f"{check.__name__}: OK")
            except Exception as error:
                code = 1
                print(f"{check.__name__}: {error}", file=sys.stderr)
    except BaseException:
        code = 1
        raise
    finally:
        tracer.finish(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
