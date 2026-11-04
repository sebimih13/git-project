# Python Git hook tracing implementations

Two runnable variants are kept separately:

- [original/](original/README.md): function-based checks with an explicit Trace2
  instance and context manager, recovered from commit `66c88b8`.
- [singleton/](singleton/README.md): AbstractCheck subclasses, a process-local
  Trace2 singleton, and the `@trace2` function decorator.

Each folder contains its own hook, checks, adapter, receiver host, runner and tests.
The original implementation's only code adjustment is the Go module's relative
path to the sibling `trace2receiver` repository, because it is now one level deeper.

Run either variant from its directory:

```sh
cd original  # or: cd singleton
python3 -m unittest -v
python3 run_demo.py
python3 run_demo.py --fail Check2
```

The sibling `/home/seb/Developer/trace2receiver` checkout is required for integration
runs. Build artifacts and captures go into each variant's `build/` and `output/`
folders. Existing root `output/` and validation files refer to earlier runs and
are retained as historical evidence. No commits have been created by this split.

## Pre-commit checks and Grafana

The versioned `.githooks/pre-commit` hook runs `Check1`, `Check2`, `Check3`,
`StagedDiff` (`git diff --cached --check`), and all unittest tests in both
`original/` and `singleton/`. Each check has its own timed Trace2 region.
All checks run even if one fails; any failure blocks the commit.
The deployment smoke test remains an explicit integration check requiring the
running services; it is not part of the pre-commit unit test suites.

Install the hook in a checkout:

```sh
git config --local core.hooksPath .githooks
```

Start the [observability deployment](deploy/README.md), then enable tracing in
the shell **before** running the commit:

```sh
export GIT_TRACE2_EVENT=af_unix:/home/seb/Developer/git-project/deploy/run/trace2.sock
git commit -m "Your commit message"
```

In [Grafana Traces Drilldown](http://localhost:3000/a/grafana-exploretraces-app/explore),
select Tempo, Last 15 minutes, and `service.name = git`. Find the `git:commit`
trace. It contains Git's `child(hook:pre-commit)` span and the Python
`hook-check-demo:pre-commit-checks` process with
`region(hook_check,check1)`, `region(hook_check,check2)`,
`region(hook_check,check3)`, `region(hook_check,stageddiff)`,
`region(hook_check,pythontests_original)`, and
`region(hook_check,pythontests_singleton)`. The nested `git:diff` process shares
the trace and belongs to the Python process. The receiver leaves Python's process
as a sibling of the hook span; the finite-capture correlation adapter described
in `singleton/README.md` is not part of the running deployment.

Checks still run with tracing disabled or the Collector unavailable. Git Trace2
does not use repository-local target configuration, so setting
`git config --local trace2.eventTarget` does not replace the environment variable.
For IDE commits, the Git process must inherit that environment too.

Run the same checks manually with `python3 scripts/pre_commit.py` (without a
traced Git parent, the standalone runner does not emit a Trace2 session).
