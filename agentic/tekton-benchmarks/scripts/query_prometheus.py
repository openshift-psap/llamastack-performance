"""
Query Prometheus (thanos-querier) for metrics during the test window.
Writes prometheus_query_results.json with both aggregate and time-series data,
matching what Grafana dashboards display.

The MLflow logger reads this and logs:
- Aggregate metrics (avg/max for the test window)
- Time-series metrics (step-by-step, same as Grafana)

Usage:
    python query_prometheus.py \
      --results-dir /workspace/results \
      --prometheus-url https://thanos-querier.openshift-monitoring.svc:9091 \
      --namespace llamastack-bench
"""
import json
import ssl
import argparse
import urllib.request
import urllib.parse
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--prometheus-url", default="https://thanos-querier.openshift-monitoring.svc:9091")
    parser.add_argument("--namespace", default="llamastack-bench")
    parser.add_argument("--token-path", default="/var/run/secrets/kubernetes.io/serviceaccount/token")
    return parser.parse_args()


def prom_query_range(url, query, token, start, end, step="15s"):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    params = urllib.parse.urlencode({"query": query, "start": int(start), "end": int(end), "step": step})
    req = urllib.request.Request(f"{url}/api/v1/query_range?{params}",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)
        data = json.loads(resp.read())
        return data.get("data", {}).get("result", [])
    except Exception as e:
        return []


def extract_values(results):
    """Extract (step_index, value) pairs from range query results, merging all series."""
    all_points = []
    for series in results:
        for i, (ts, val) in enumerate(series.get("values", [])):
            try:
                v = float(val)
                if v != float("inf") and v != float("nan"):
                    all_points.append((i, v))
            except (ValueError, TypeError):
                pass
    return all_points


def extract_labeled_series(results, label_key):
    """Extract per-label time-series from range query results."""
    series_dict = {}
    for series in results:
        label = series.get("metric", {}).get(label_key, "unknown")
        points = []
        for i, (ts, val) in enumerate(series.get("values", [])):
            try:
                v = float(val)
                if v != float("inf") and v != float("nan"):
                    points.append((i, v))
            except (ValueError, TypeError):
                pass
        if points:
            series_dict[label] = points
    return series_dict


def avg_val(points):
    vals = [v for _, v in points if v > 0]
    return sum(vals) / len(vals) if vals else 0


def max_val(points):
    vals = [v for _, v in points]
    return max(vals) if vals else 0


def main():
    args = parse_args()
    d = Path(args.results_dir)
    ns = args.namespace
    url = args.prometheus_url

    start_file = d / "test_start_epoch_precise"
    end_file = d / "test_end_epoch_precise"
    if not start_file.exists():
        start_file = d / "test_start_epoch"
    if not end_file.exists():
        end_file = d / "test_end_epoch"
    if not start_file.exists() or not end_file.exists():
        print("WARNING: Test timestamps not found, skipping Prometheus queries")
        (d / "prometheus_query_results.json").write_text("{}")
        return

    start = float(start_file.read_text().strip())
    end = float(end_file.read_text().strip())
    duration = end - start
    print(f"Test window: {duration:.0f}s")

    try:
        token = Path(args.token_path).read_text().strip()
    except Exception:
        print("WARNING: Could not read SA token, skipping Prometheus queries")
        (d / "prometheus_query_results.json").write_text("{}")
        return

    output = {"aggregate": {}, "timeseries": {}}
    agg = output["aggregate"]
    ts = output["timeseries"]

    def safe_name(s):
        """Sanitize metric names for MLflow (no double slashes, no leading slash in labels)."""
        return s.replace("//", "/").strip("/")

    def query_and_store(name, query, is_labeled=False, label_key=""):
        r = prom_query_range(url, query, token, start, end)
        if is_labeled and label_key:
            labeled = extract_labeled_series(r, label_key)
            for label, points in labeled.items():
                clean_label = label.strip("/").replace("/", "_")
                ts_name = safe_name(f"{name}/{clean_label}")
                ts[ts_name] = [{"step": s, "value": round(v, 6)} for s, v in points]
                agg[safe_name(f"{ts_name}/avg")] = round(avg_val(points), 6)
            total_points = [p for pts in labeled.values() for p in pts]
            if total_points:
                agg[f"{name}/avg"] = round(avg_val(total_points), 6)
                agg[f"{name}/max"] = round(max_val(total_points), 6)
        else:
            points = extract_values(r)
            if points:
                ts[name] = [{"step": s, "value": round(v, 6)} for s, v in points]
                agg[f"{name}/avg"] = round(avg_val(points), 6)
                agg[f"{name}/max"] = round(max_val(points), 6)

    # --- OTel Application Metrics ---
    print("Querying OTel application metrics...")
    query_and_store("otel/genai_request_rate",
        f'sum(rate(gen_ai_client_operation_duration_seconds_count{{namespace="{ns}"}}[1m]))')
    query_and_store("otel/genai_avg_latency_s",
        f'sum(rate(gen_ai_client_operation_duration_seconds_sum{{namespace="{ns}"}}[1m])) / sum(rate(gen_ai_client_operation_duration_seconds_count{{namespace="{ns}"}}[1m]))')
    query_and_store("otel/db_pool_used",
        f'max(db_client_connections_usage{{namespace="{ns}", service="otel-collector-collector", state="used"}})')
    query_and_store("otel/db_pool_idle",
        f'max(db_client_connections_usage{{namespace="{ns}", service="otel-collector-collector", state="idle"}})')
    query_and_store("otel/active_requests",
        f'sum(http_server_active_requests{{namespace="{ns}", service="otel-collector-collector"}})')
    query_and_store("otel/api_request_rate",
        f'sum(rate(http_server_duration_milliseconds_count{{namespace="{ns}", http_target!=""}}[1m])) by (http_target)',
        is_labeled=True, label_key="http_target")
    query_and_store("otel/cpu_utilization",
        f'process_cpu_utilization_ratio{{namespace="{ns}", service="otel-collector-collector"}}')
    query_and_store("otel/memory_rss_bytes",
        f'process_memory_usage_bytes{{namespace="{ns}", service="otel-collector-collector"}}')
    query_and_store("otel/thread_count",
        f'process_thread_count{{namespace="{ns}", service="otel-collector-collector"}}')

    # --- vLLM Metrics ---
    print("Querying vLLM metrics...")
    query_and_store("vllm/requests_running",
        f'max(vllm:num_requests_running{{namespace="{ns}"}})')
    query_and_store("vllm/requests_waiting",
        f'max(vllm:num_requests_waiting{{namespace="{ns}"}})')
    query_and_store("vllm/kv_cache_usage",
        f'max(vllm:kv_cache_usage_perc{{namespace="{ns}"}})')
    query_and_store("vllm/prompt_throughput_tps",
        f'sum(rate(vllm:prompt_tokens_total{{namespace="{ns}"}}[1m]))')
    query_and_store("vllm/generation_throughput_tps",
        f'sum(rate(vllm:generation_tokens_total{{namespace="{ns}"}}[1m]))')
    query_and_store("vllm/ttft_p50_s",
        f'histogram_quantile(0.50, sum(rate(vllm:time_to_first_token_seconds_bucket{{namespace="{ns}"}}[1m])) by (le))')
    query_and_store("vllm/ttft_p95_s",
        f'histogram_quantile(0.95, sum(rate(vllm:time_to_first_token_seconds_bucket{{namespace="{ns}"}}[1m])) by (le))')
    query_and_store("vllm/e2e_latency_p50_s",
        f'histogram_quantile(0.50, sum(rate(vllm:e2e_request_latency_seconds_bucket{{namespace="{ns}"}}[1m])) by (le))')
    query_and_store("vllm/e2e_latency_p95_s",
        f'histogram_quantile(0.95, sum(rate(vllm:e2e_request_latency_seconds_bucket{{namespace="{ns}"}}[1m])) by (le))')
    query_and_store("vllm/queue_time_p50_s",
        f'histogram_quantile(0.50, sum(rate(vllm:request_queue_time_seconds_bucket{{namespace="{ns}"}}[1m])) by (le))')
    query_and_store("vllm/inter_token_latency_p50_s",
        f'histogram_quantile(0.50, sum(rate(vllm:inter_token_latency_seconds_bucket{{namespace="{ns}"}}[1m])) by (le))')

    # --- GPU/DCGM Metrics ---
    print("Querying GPU metrics...")
    query_and_store("gpu/utilization_pct", "DCGM_FI_DEV_GPU_UTIL")
    query_and_store("gpu/memory_used_mib", "DCGM_FI_DEV_FB_USED")
    query_and_store("gpu/temperature_c", "DCGM_FI_DEV_GPU_TEMP")
    query_and_store("gpu/power_w", "DCGM_FI_DEV_POWER_USAGE")

    # --- Postgres Exporter Metrics ---
    print("Querying Postgres metrics...")
    query_and_store("pg/active_connections",
        f'sum by (state) (pg_stat_activity_count{{namespace="{ns}"}})',
        is_labeled=True, label_key="state")
    query_and_store("pg/commits_per_sec",
        f'rate(pg_stat_database_xact_commit{{namespace="{ns}", datname="llamastack"}}[1m])')
    query_and_store("pg/rollbacks_per_sec",
        f'rate(pg_stat_database_xact_rollback{{namespace="{ns}", datname="llamastack"}}[1m])')
    query_and_store("pg/inserts_per_sec",
        f'rate(pg_stat_database_tup_inserted{{namespace="{ns}", datname="llamastack"}}[1m])')
    query_and_store("pg/cache_hit_ratio",
        f'pg_stat_database_blks_hit{{namespace="{ns}", datname="llamastack"}} / (pg_stat_database_blks_hit{{namespace="{ns}", datname="llamastack"}} + pg_stat_database_blks_read{{namespace="{ns}", datname="llamastack"}} > 0)')
    query_and_store("pg/deadlocks_per_sec",
        f'rate(pg_stat_database_deadlocks{{namespace="{ns}", datname="llamastack"}}[1m])')

    # --- Per-Pod CPU/Memory ---
    print("Querying per-pod CPU/memory...")
    query_and_store("cluster/pod_cpu_cores",
        f'sum(rate(container_cpu_usage_seconds_total{{namespace="{ns}", container!="", container!="POD"}}[1m])) by (pod)',
        is_labeled=True, label_key="pod")
    query_and_store("cluster/pod_memory_bytes",
        f'sum(container_memory_working_set_bytes{{namespace="{ns}", container!="", container!="POD"}}) by (pod)',
        is_labeled=True, label_key="pod")

    # Filter empty aggregates
    output["aggregate"] = {k: v for k, v in agg.items() if v and v > 0}

    n_agg = len(output["aggregate"])
    n_ts = sum(len(v) for v in ts.values())
    print(f"\nCollected {n_agg} aggregate metrics, {len(ts)} time-series ({n_ts} total data points)")

    for k, v in sorted(output["aggregate"].items()):
        print(f"  {k}: {v}")

    out_path = d / "prometheus_query_results.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
