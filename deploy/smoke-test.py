#!/usr/bin/env python3
"""Send real Git Trace2 events and verify storage, Drilldown metrics and Grafana."""
import base64
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
TEMPO = os.environ.get("TEMPO_URL", "http://127.0.0.1:3200")
PROMETHEUS = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:9090")
GRAFANA = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000")
credentials = (
    os.environ.get("GRAFANA_ADMIN_USER", "admin") + ":"
    + os.environ.get("GRAFANA_ADMIN_PASSWORD", "admin")
)
AUTH = {"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()}


def get(url, headers=None):
    with urlopen(Request(url, headers=headers or {}), timeout=10) as response:
        return json.load(response)


def wait_for(description, check, timeout=120):
    deadline = time.monotonic() + timeout
    last_error = "no data yet"
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                print("PASS:", description, flush=True)
                return result
        except Exception as error:
            last_error = str(error)
        time.sleep(2)
    raise SystemExit(f"FAIL: {description}: {last_error}")


def main():
    socket = ROOT / "run/trace2.sock"
    wait_for("Trace2 Unix socket exists", socket.is_socket)
    start = int(time.time()) - 2
    env = dict(os.environ, GIT_TRACE2_EVENT=f"af_unix:{socket}")
    for args in (["status", "--short"], ["log", "-1", "--oneline"], ["rev-parse", "HEAD"]):
        subprocess.run(["git", "-C", str(ROOT.parent), *args], env=env,
                       check=True, stdout=subprocess.DEVNULL)

    query = '{resource.service.name="git"}'

    def search():
        params = urlencode({"q": query, "start": start, "end": int(time.time()) + 1})
        return get(TEMPO + "/api/search?" + params).get("traces")

    traces = wait_for("real Git traces searchable in Tempo", search)
    trace_id = traces[0]["traceID"]
    wait_for("trace accessible through Grafana Tempo data source",
             lambda: get(GRAFANA + "/api/datasources/proxy/uid/tempo/api/traces/" + trace_id, AUTH))

    def metrics():
        params = urlencode({"q": query + " | count_over_time()", "start": start,
                            "end": int(time.time()) + 1, "step": "5s"})
        result = get(GRAFANA + "/api/datasources/proxy/uid/tempo/api/metrics/query_range?" + params, AUTH)
        return any(float(sample.get("value", 0)) > 0
                   for series in result.get("series", []) for sample in series.get("samples", []))

    wait_for("Drilldown TraceQL metrics return nonzero samples through Grafana", metrics)
    params = urlencode({"query": 'traces_spanmetrics_calls_total{service="git"}'})
    wait_for("Tempo span metrics remote-written to Prometheus",
             lambda: get(PROMETHEUS + "/api/v1/query?" + params).get("data", {}).get("result"))
    wait_for("Traces Drilldown plugin enabled",
             lambda: get(GRAFANA + "/api/plugins/grafana-exploretraces-app/settings", AUTH).get("enabled"))
    print(f"Trace ID: {trace_id}")
    print(f"Open {GRAFANA}/a/grafana-exploretraces-app/explore (Tempo, last 15 minutes)")


if __name__ == "__main__":
    main()
