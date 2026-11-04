This is the **singleton** implementation. Run commands below from this folder.

# post-checkout checks with Trace2

A Linux example using the sibling **real trace2receiver library**, a Git
`post-checkout` shell hook, and dependency-free Python checks. No C Git, JGit,
EGit, or trace2receiver production sources are changed.

The final OpenTelemetry trace contains:

```text
checkout
└── child(hook:post-checkout)     # Git's real child_start/child_exit span
    ├── Check1                  # timed Python region
    ├── Check2
    └── Check3
```

The receiver's other spans, including Python's process span, remain intact.
The checks sleep for 20/30/40 ms as stand-ins for real checks; replace the
`execute()` methods of `Check1`, `Check2`, and `Check3` in `checks.py` with your work.

## Python hook API

`trace2.py` provides a process-local `Trace2` singleton and a `@trace2`
decorator. This models an AbstractCheck-based framework; it is not a copy of an
external hook framework. `checks.py` supplies `AbstractCheck`, three subclasses,
and `run_checks()`.

```python
from checks import AbstractCheck, run_checks
from trace2 import trace2

class ValidateFiles(AbstractCheck):
    def execute(self):
        validate_files()
        # Raise an exception on failure. Return values are passed through;
        # False is not automatically treated as a failure.

@trace2(name="validate-files")
def validate_files():
    pass

exit_code = run_checks([ValidateFiles()], hook="post-checkout")
```

`AbstractCheck.run()` is decorated once in the base class. Subclasses implement
`execute()` and inherit tracing; overriding `run()` bypasses that wrapper.
Decorating a helper produces a nested region under the check. Only outer regions
carry the adapter's hook-correlation marker, preserving nested helper ancestry.
The adapter sets success/error status on outer check spans; nested helper regions
retain the receiver's naming and status behavior.

For standalone functions, use `@trace2`, `@trace2()`, or
`@trace2(name="display-name")`. A callable name can derive the label from arguments,
as in the base class's `@trace2(name=lambda self: self.name)`. Function metadata,
return values and raised exceptions are preserved. Function arguments, results,
and exception messages are not serialized by the decorator; choose labels that
contain no sensitive data. Session start also omits hook command-line arguments.

The runner creates `Trace2(hook=..., branch=...)` **before** executing checks.
Repeated `Trace2()` calls return that same session without opening another sink
or changing its configuration. The first construction wins; configure it before
calling decorated functions. Always call `finish(exit_code)` from `finally`.
Finish emits exit/atexit, closes the sink, restores the inherited parent SID,
and releases the singleton so a subsequent hook run can start a fresh session.
Repeated finish calls on the old instance are harmless. The runner catches check
exceptions, runs remaining checks, and returns a nonzero exit code on failure.
Cancellation balances the current region and finishes the session before being
re-raised.

This API supports synchronous checks on the session's owning thread. It is not a
thread-safe singleton, a cross-process singleton, or an async tracing API. Do not
reuse it after fork. Worker-thread calls are rejected, and decorating async or
generator functions raises TypeError. Disabled or failed tracing still executes
checks; sink I/O errors disable telemetry. No tracing overhead improvement is
claimed or benchmarked.

The existing finite-capture adapter remains necessary for direct Git-hook
parenting. For another hook name, install the same Python runner from that hook
and pass its name to `run_checks`; only post-checkout is covered by the integration
demo. Events are emitted at entry and exit, but backend export still occurs after
session closure and capture adaptation.

## Run

Requires Linux `/proc`, Git, Python 3.10+, and Go 1.25+ with dependency download
access for the initial build. The Go module's `replace` assumes this layout:

```text
Developer/
├── trace2receiver/
└── git-project/
    └── singleton/
```

```sh
cd /home/seb/Developer/git-project/singleton
python3 run_demo.py
python3 run_demo.py --fail Check2
python3 run_demo.py --detached
python3 -m unittest -v
```

Each run builds a small receiver host, creates a fresh repository under
`output/`, installs the hook, switches from `main` to `feature/demo`, and
verifies all three check span parent IDs against the actual hook span ID.
Repositories and captures are retained for inspection. Existing user repositories
and global Git configuration are not modified. Failure cases verify the checkout
returns an error while HEAD still reflects the completed checkout: post-checkout
failure does not roll back the checkout.

Each output directory contains:

- `receiver-raw.jsonl`: original, unmodified receiver output, one OTLP batch per line.
- `otlp.json`: adapted OTLP JSON, validated using the Collector's JSON parser.
- `verification.json`: check durations, outcomes, trace/span/parent IDs and command.
- `checkout.stdout`, `checkout.stderr`, `receiver.stderr`: execution output.

Build caches default to `/tmp/hook-demo-go-build` and `/tmp/hook-demo-go-mod`.
Set `GOCACHE`/`GOMODCACHE` to reuse another cache if desired.

## What the proposed SID approach actually does

Python reads `GIT_TRACE2_PARENT_SID`, creates a unique child SID, and sends one
JSON event per line over **its own Unix stream connection**. It emits `version`,
`start`, `cmd_name`, three balanced `region_enter`/`region_leave` pairs, region
`data_json` measurements, `exit`, and `atexit`, then closes its connection.
Durations use `time.perf_counter()`. Wall-clock UTC timestamps are also present
because the receiver requires them. `finally` balances regions and closes the
session on failures. Failed checks are recorded and the remaining checks run.
Transport failures disable telemetry without failing the checks.
Instrumented subprocesses inherit Python's new SID as their parent SID.

The writer supports `GIT_TRACE2_EVENT=af_unix:stream:/absolute/path` (also the
`af_unix:/absolute/path` shorthand) and absolute JSONL files. It does not implement
Git's entire Trace2 destination/configuration API, Windows pipes, datagram
sockets, directory targets, descriptor targets, or concurrent regions. Synchronous nested regions are supported.
Configure the target explicitly through the environment for this example.

**SID ancestry alone does not attach the Python checks to the hook span.**
In the receiver at `f0a78d12a6470fcc6f2712bb15a5d03eab8059ae`:

- `trace2sids.go:extractIDsfromSID` derives process trace/parent IDs from SIDs.
- `evt_apply.go:apply__child_start` gives Git's hook child span a random span ID
  and attaches it directly to the parent process.
- A Python child session is therefore a **sibling** of that hook span.
- `trace2emitotlp.go:ToTraces` emits region spans only at `dl:verbose`.
- `rcvr_unixsocket.go:worker` assumes one process/SID per connection and exports
  at socket EOF. Sending `atexit` without closing the connection is insufficient.

The demo includes `adapter.py`, a **finite-capture correlation adapter**. Each
check's standard Trace2 `msg` field carries custom JSON metadata: protocol marker,
owner SID, hook name, check name, resulting branch ref, and Python PID ancestry.
The adapter matches an actual hook span using all of:

1. The owner's exact SID and the same OpenTelemetry trace ID.
2. `trace2.child.class=hook` and `trace2.child.hook=post-checkout`.
3. Git's reported child PID occurring in Python's captured ancestor PID list.
4. The check's time interval being contained in that hook's interval.

It requires **exactly one** match; absent or ambiguous matches fail validation.
It then changes the marked check span's parent to the real hook span ID,
sets the check's stable display name and success/error status, and copies branch
attributes onto the check and hook spans. Original parent IDs are retained as
`hook_demo.original_parent_span_id`. Raw receiver output is never overwritten.
Python's separate process span remains a sibling, so its original lifetime can
still be inspected. This is intentional demo adapter behavior, not native
Trace2 or existing receiver behavior.

For a continuously running collector, implement this adapter as a stateful
processor with bounded buffering, TTL/size limits and a policy for missing parent
sessions. This example deliberately captures a finite operation and adapts after
both processes finish. An ordinary stateless attribute transform cannot perform
this cross-session hook matching.

## Export to an OTLP backend

The receiver is a component library, not a standalone server. `collector/main.go`
hosts that component and records its downstream `consumer.Traces` calls. Python
needs no OpenTelemetry SDK: it sends Trace2 JSON to that local socket.

To explicitly send the **adapted** spans to an existing OTLP/HTTP JSON endpoint:

```sh
python3 run_demo.py --otlp-endpoint http://localhost:4318/v1/traces
```

Supply the full traces URL. No remote data is sent unless you pass this option.
The example does not configure Grafana, Tempo, authentication, retries, or a
production Collector deployment. Remote backend ingestion is not claimed by the
local tests. In a production collector, put the correlation processor after this
receiver and before the OTLP exporter.

## JGit

This works with the **modified local JGit implementation**, not an assertion about
every upstream JGit release. Its `FS.internalRunHookIfPresent()` marks hook
processes; `Trace2.childStart(ProcessBuilder)` sets `GIT_TRACE2_PARENT_SID` and emits
hook child events. `CheckoutCommand` invokes `Hooks.postCheckout(...)`.

Compile the current core sources with Java 17 and supply classes plus dependencies:

```sh
# In /home/seb/Developer/jgit:
JAVA_HOME=/usr/lib/jvm/java-17-temurin-jdk \
  mvn -o -pl org.eclipse.jgit -am -DskipTests -Dmaven.javadoc.skip=true compile

# In this example directory:
python3 run_demo.py --engine jgit --classpath \
"/home/seb/Developer/jgit/org.eclipse.jgit/target/classes:/home/seb/.m2/repository/org/slf4j/slf4j-api/2.0.20/slf4j-api-2.0.20.jar:/home/seb/.m2/repository/com/googlecode/javaewah/JavaEWAH/1.2.3/JavaEWAH-1.2.3.jar:/home/seb/.m2/repository/commons-codec/commons-codec/1.22.1/commons-codec-1.22.1.jar"
```

`DemoCheckout.java` is a dedicated short-lived verification JVM. It closes its
tracing session only when that JVM's work is finished. Do not copy this lifecycle
into a library command or an Eclipse job.

## EGit

The local EGit `BranchOperation` calls the same JGit checkout implementation, so
hook execution and child-SID propagation are supported by the inspected path.
This example's EGit assessment is source-verified; no live PDE/UI checkout is
claimed. Eclipse must load the matching local JGit bundle and inherit the tracing
environment before startup. An unrelated installed JGit/EGit build may differ.

For a manual capture, start `build/collector --socket /tmp/YOUR-UNIQUE.sock
--output /absolute/path/egit-raw.jsonl` interactively. Set
`GIT_TRACE2_EVENT=af_unix:stream:/tmp/YOUR-UNIQUE.sock`,
`GIT_TRACE2_EVENT_BRIEF=0` and `GIT_TRACE2_EVENT_NESTING=20` before starting Eclipse.
In a test repository, copy `post-checkout` and `checks.py` together into the active
hooks directory, make `post-checkout` executable, then switch branches in EGit.
Use a fresh test repository and preserve any existing hooks.

The important limitation is session lifetime: JGit normally keeps its socket
open for the JVM's lifetime. Python exports on hook completion, but the real hook
span is in JGit's dataset and is unavailable downstream until EOF, normally at
Eclipse shutdown. Stop Eclipse normally before stopping the receiver (send a
newline to its stdin), then run:

```sh
python3 adapter.py /absolute/path/egit-raw.jsonl --output /absolute/path/egit-otlp.json
```

Do not call `Trace2.close()` after each EGit checkout to force export; that ends
tracing for the shared instance. Timely EGit dashboards require a separate
streaming/session ownership design in the receiver/JGit integration. This example
does not implement that design.

## Branch attributes

The hook queries the resulting HEAD with tracing explicitly disabled for that
metadata lookup. Check and hook spans receive `vcs.ref.head.name`,
`vcs.ref.head.type=branch` and custom `git.ref.full`. Detached HEAD checks receive
custom `git.head.detached=true`, with no invented branch. Checkout's source branch
is not captured. The adapter does not infer or overwrite a session-wide branch
for the parent JVM: one Eclipse session can contain many branch changes.
Unavailable branch metadata is marked separately from detached HEAD and does
not prevent checks from running.
