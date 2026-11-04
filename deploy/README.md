# Git Trace2 observability deployment

Local Linux deployment using Docker Engine and Docker Compose 2.17+ (named build
contexts required). Grafana 13.3.0 includes Traces Drilldown; Tempo 3.1.1 serves
TraceQL metrics from its live-store. Prometheus 3.13.1 stores infrastructure and
Tempo-generated span/service metrics. Named volumes retain database data.

```text
Git Trace2 → shared Unix socket → custom OTel Collector → OTLP/gRPC → Tempo
                                                                    ↓
                                               span metrics → Prometheus
                                                              ↑     ↑
                                                            Grafana
```

## Start

```bash
cd /home/seb/Developer/git-project/deploy
cp .env.example .env
# Set LOCAL_UID/LOCAL_GID to the owner of ./run if different from 1000:1000.
docker compose up -d --build
docker compose ps
```

The build uses the local `/home/seb/Developer/trace2receiver` checkout through the
`../../trace2receiver` build context, including uncommitted receiver changes.
Override `TRACE2_RECEIVER_SOURCE` in `.env` to use another checkout. This receiver
currently requires Collector 0.157.0 / stable modules 1.63.0, pinned in
`collector/builder-config.yaml`. Rebuild after receiver changes. The standard
Collector image does not contain Trace2Receiver. The older sample collector is
not used because its Collector dependencies predate the local receiver.

Services:

| Service | URL | Credentials |
| --- | --- | --- |
| Grafana | http://localhost:3000 | `admin` / `admin` by default |
| Prometheus | http://localhost:9090 | — |
| Tempo API | http://localhost:3200 | — |

Ports bind to loopback. Change port settings or Grafana credentials in `.env`
before first startup. Grafana stores its initial admin password in its database;
changing the environment afterward does not reset an existing account.
The one-time `tempo-init` container gives Tempo ownership of its data volume;
the Collector runs with your configured UID/GID. Bind mounts include SELinux labels.

## Send traces and open Drilldown

Run a command with tracing enabled for that command only:

```bash
GIT_TRACE2_EVENT=af_unix:/home/seb/Developer/git-project/deploy/run/trace2.sock \
  git -C /home/seb/Developer/git-project status
```

To capture all Git commands from a shell:

```bash
export GIT_TRACE2_EVENT=af_unix:/home/seb/Developer/git-project/deploy/run/trace2.sock
git -C /home/seb/Developer/git-project log -5 --oneline
```

1. Open http://localhost:3000 and log in.
2. Select **Drilldown → Traces**, or open
   http://localhost:3000/a/grafana-exploretraces-app/explore.
3. Select **Tempo**, **Last 15 minutes**, and filter `service.name = git`.
4. Use **All spans** to include Git's internal spans. Open the trace list to
   inspect command spans and their regions. Refresh after generating commands.

The Collector sets `service.name=git` and enables verbose Trace2 regions.
Tempo 3.x does not use the 2.x `local-blocks` processor. TraceQL metrics power
Drilldown directly; Prometheus receives the separate generated span metrics.
Git's internal spans may not form client/server service edges, so an empty
service map does not mean trace ingestion failed. Metrics can take several
seconds to appear after a command completes; allow up to two minutes on startup.

## Verify

```bash
python3 smoke-test.py
```

This sends actual Git events, finds their recent traces in Tempo, retrieves a
trace through Grafana's data source, checks nonzero TraceQL metrics through
Grafana, checks generated Prometheus metrics, and checks that the Drilldown app
is enabled. It requires Python 3 and Git on the host. For custom settings, export
`GRAFANA_URL`, `TEMPO_URL`, `PROMETHEUS_URL`, `GRAFANA_ADMIN_USER`, and
`GRAFANA_ADMIN_PASSWORD` as needed; the script does not load `.env` automatically.

```bash
docker compose logs --tail=100 collector tempo
curl -fsS http://localhost:3200/ready
```

Check the Collector's socket and logs if traces are missing. A container restart
must recreate a writable socket in `run/`; keep this directory owned by the
configured Collector UID. The host socket path must fit the Unix socket path
limit (typically 107 bytes). Docker Desktop on macOS/Windows does not provide
the same host/container Unix socket sharing as native Linux.

## Stop

```bash
docker compose down
unset GIT_TRACE2_EVENT
```

`docker compose down` retains the database volumes. Add `-v` only when you intend
to delete stored traces, metrics, and Grafana settings. This is a local development
stack with plaintext internal transport and a memory-backed Collector export
queue; it is not configured for highly available production ingestion.

References: [Traces Drilldown setup](https://grafana.com/docs/grafana/latest/visualizations/simplified-exploration/traces/get-started/),
[Tempo monolithic deployment](https://grafana.com/docs/tempo/latest/set-up-for-tracing/setup-tempo/deploy/locally/linux/),
[Tempo metrics-generator](https://grafana.com/docs/tempo/latest/metrics-from-traces/metrics-generator/).
