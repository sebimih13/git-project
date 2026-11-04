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
