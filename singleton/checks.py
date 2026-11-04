#!/usr/bin/env python3
"""Example AbstractCheck infrastructure with automatic Trace2 instrumentation."""
from abc import ABC, abstractmethod
import os
import subprocess
import sys
import time

from trace2 import Trace2, trace2


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


class AbstractCheck(ABC):
    @property
    def name(self):
        return type(self).__name__

    @trace2(name=lambda self: self.name)
    def run(self):
        """Run a check; failures must raise an exception."""
        result = self.execute()
        if os.environ.get("HOOK_DEMO_FAIL") == self.name:
            raise RuntimeError("requested demo failure")
        return result

    @abstractmethod
    def execute(self):
        """Implement check logic and raise on failure."""
        raise NotImplementedError


class Check1(AbstractCheck):
    def execute(self):
        time.sleep(0.02)


class Check2(AbstractCheck):
    def execute(self):
        time.sleep(0.03)


class Check3(AbstractCheck):
    def execute(self):
        time.sleep(0.04)


def run_checks(checks, *, hook="post-checkout"):
    tracer, code = Trace2(hook=hook, branch=current_branch()), 0
    try:
        for check in checks:
            try:
                check.run()
                print(f"{check.name}: OK")
            except Exception as error:
                code = 1
                print(f"{check.name}: {error}", file=sys.stderr)
    except BaseException:
        code = 1
        raise
    finally:
        tracer.finish(code)
    return code


def main():
    return run_checks([Check1(), Check2(), Check3()])


if __name__ == "__main__":
    sys.exit(main())
