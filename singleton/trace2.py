"""Process-local Trace2 session and synchronous function decorator for Git hooks."""
import contextlib
import datetime as dt
from functools import wraps
import inspect
import json
import os
from pathlib import Path
import socket
import sys
import threading
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
    """One session per synchronous hook process; first construction configures it.

    Call finish() in the runner's finally block. Subsequent construction then
    starts a fresh session. Worker threads, async calls and fork reuse are outside
    this API's supported execution model.
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, *, hook="post-checkout", branch=None):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.hook, self.branch = hook, branch
        self._depth = 0
        self._finished = False
        self._owner_thread = threading.get_ident()
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
        self.emit("start", argv=["hook-check-demo"], t_abs=self.elapsed())
        self.emit("cmd_name", name=f"{self.hook}-checks", hierarchy=f"{self.hook}/checks")
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
    def check(self, name, branch=None):
        if self._finished:
            raise RuntimeError("Trace2 session is finished")
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("Trace2 supports synchronous checks on its owning thread only")
        if self.socket is None and self.file is None:
            yield
            return
        self._depth += 1
        depth = self._depth
        # Custom correlation metadata carried in a standard Trace2 msg field.
        hint = dict(schema="hook-check-demo/v1", owner_sid=self.parent_sid,
                    hook=self.hook, ancestor_pids=ancestor_pids(),
                    check=name, branch=self.branch if branch is None else branch)
        # Only outer check spans are reparented by the correlation adapter.
        self.emit("region_enter", nesting=depth, category="hook-check", label=name,
                  msg=json.dumps(hint, separators=(",", ":")) if depth == 1 else "")
        started, outcome = time.perf_counter(), "success"
        try:
            yield
        except BaseException:
            outcome = "error"
            raise
        finally:
            duration = time.perf_counter() - started
            self.emit("data_json", nesting=depth + 1, category="hook-check", key="result",
                      t_abs=self.elapsed(), t_rel=duration,
                      value=dict(outcome=outcome, duration_seconds=duration))
            self.emit("region_leave", nesting=depth, category="hook-check", label=name,
                      t_rel=duration)
            self._depth -= 1

    def finish(self, code=0):
        if self._finished:
            return
        if self._depth:
            raise RuntimeError("Cannot finish Trace2 while a region is active")
        self._finished = True
        self.emit("exit", code=code, t_abs=self.elapsed())
        self.emit("atexit", code=code, t_abs=self.elapsed())
        self.close()  # EOF is required for this receiver to export.
        if os.environ.get("GIT_TRACE2_PARENT_SID") == self.sid:
            os.environ["GIT_TRACE2_PARENT_SID"] = self.parent_sid
        type(self)._instance = None


def trace2(function=None, *, name=None):
    """Trace a synchronous call. name may be a string or an argument-based callable.

    Arguments, return values and exception text are never logged. Exceptions are
    re-raised unchanged. The hook owns the session and must call finish().
    """
    def decorate(func):
        if (inspect.iscoroutinefunction(func) or inspect.isgeneratorfunction(func)
                or inspect.isasyncgenfunction(func)):
            raise TypeError("@trace2 supports synchronous, non-generator functions only")

        @wraps(func)
        def wrapped(*args, **kwargs):
            label = name(*args, **kwargs) if callable(name) else name or func.__qualname__
            with Trace2().check(label):
                return func(*args, **kwargs)
        return wrapped

    return decorate(function) if function is not None else decorate
