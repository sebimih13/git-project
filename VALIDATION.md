# Validation, 2026-11-03

All captures used disposable single-commit repositories with `main` and
`feature/demo` pointing at the same commit, with a real executable post-checkout
hook. Switching refs still invokes the hook. The index/worktree started clean.
Tests assert the resulting branch or detached HEAD and unchanged commit, not
only the shape of the trace. Failure cases assert nonzero command exit without
rolling back the completed checkout.

| Check | Result |
| --- | --- |
| C Git checkout, three successful checks | PASS |
| C Git Check2 failure, all checks still run | PASS |
| C Git detached HEAD | PASS |
| Freshly compiled local JGit checkout | PASS |
| JGit Check2 failure, all checks still run | PASS |
| JGit detached HEAD | PASS |
| Adapter correlation, ambiguity rejection, foreign session/trace/PID rejection | PASS |
| Disabled tracing and unavailable socket do not fail checks | PASS |
| Failure balances Python regions and session lifecycle | PASS |
| Unavailable branch metadata distinguished from detached HEAD | PASS |
| Unit tests | 7 PASS |
| Collector OTLP JSON schema parsing of final normal captures | PASS: C Git 14 spans, JGit 8 spans |
| OTLP/HTTP JSON delivery to temporary loopback test server | PASS: HTTP 200, body revalidated with Collector parser |
| Remote Grafana/Tempo ingestion | Not executed |
| Live EGit/PDE checkout | Not executed; source path inspected |

The original receiver output was inspected independently: Python's check regions
were **not** children of Git's hook span. In each adapted normal capture, all three
check `parentSpanId` values equal the real hook's `spanId`, all trace IDs match,
span durations are positive, and `git.ref.full` is
`refs/heads/feature/demo`. The adapter retains all original receiver spans and
saves its output separately.

Final normal captures:

- `output/git-79dpyt76/receiver-raw.jsonl` and `output/git-79dpyt76/otlp.json`.
- `output/jgit-1strzmw3/receiver-raw.jsonl` and `output/jgit-1strzmw3/otlp.json`.

`validation-runs.json` records retained run paths and check IDs/durations.
`validation-environment.json` records source commits, branch names, tracked-patch
hashes, local changes, OS/runtime versions, the actual installed Git executable
hash, and the loaded JGit core class hashes. Those manifests and raw captures
are evidence for these scenarios, not a claim of broad Trace2 compatibility.

The JGit core was compiled successfully with:

```sh
JAVA_HOME=/usr/lib/jvm/java-17-temurin-jdk \
  mvn -o -pl org.eclipse.jgit -am -DskipTests -Dmaven.javadoc.skip=true compile
```

The host JDK 25 initially failed in Groovy semantic analysis; switching to the
project's Java 17 toolchain resolved it. The demo's dedicated verification JVM
loaded the freshly compiled `target/classes` directory and exited normally.

Source revisions: receiver `f0a78d12a6470fcc6f2712bb15a5d03eab8059ae`,
JGit `71e4702679a4d3a6b2193ef5236f79d51a16a851`,
EGit `f6c2cc0b63956d1a8fa46bae3cc595a6e218983a`,
C Git reference `6de20f6092dcf9bdb1c8efe03db4b70c82b423dd`.
The installed Git 2.56.0 executable was used; its build provenance relative to
the reference checkout is unverified. Existing JGit/EGit local changes were
preserved; no production source changes were made for this example.

No performance gain or instrumentation overhead claim is made. Check durations
are synthetic waits demonstrating instrumentation, not optimization benchmarks.
The adapter is Linux-specific (PID ancestry from `/proc`) and processes finite
captures. JGit's shared session/receiver EOF behavior prevents promising prompt
parent-span export from an ongoing Eclipse session.

## Requested project location

The solution was copied to `/home/seb/Developer/git-project` and its local Go
module replacement updated to `../../trace2receiver`. All seven unit tests and
the C Git demo passed from this location. The new capture is
`output/git-6bu1sq6j/otlp.json` (14 schema-validated spans); each check is a direct
child of the real post-checkout hook span. Earlier captures above were retained
as original evidence from the initial workspace location.
