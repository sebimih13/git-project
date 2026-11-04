#!/usr/bin/env python3
"""Run the Python checks with named regions in the enclosing Git trace."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "singleton"))

from checks import AbstractCheck, Check1, Check2, Check3, run_checks


class StagedDiff(AbstractCheck):
    def execute(self):
        # Inherit the Python session's parent SID so diff joins the same trace.
        subprocess.run(["git", "diff", "--cached", "--check"], check=True)


class PythonTests(AbstractCheck):
    def __init__(self, variant):
        self.variant = variant

    @property
    def name(self):
        return f"PythonTests:{self.variant}"

    def execute(self):
        env = os.environ.copy()
        # Git's repository-local variables belong to the commit's repository,
        # which may differ from the directory containing these test suites.
        local_vars = subprocess.check_output(
            ["git", "rev-parse", "--local-env-vars"], text=True).splitlines()
        for key in local_vars:
            env.pop(key, None)
        subprocess.run([sys.executable, "-m", "unittest", "discover", "-v"],
                       cwd=ROOT / self.variant, env=env, check=True)


def main():
    return run_checks([
        Check1(), Check2(), Check3(), StagedDiff(),
        PythonTests("original"), PythonTests("singleton"),
    ], hook="pre-commit")


if __name__ == "__main__":
    sys.exit(main())
