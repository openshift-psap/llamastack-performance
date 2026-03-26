"""
Query Prometheus (thanos-querier) for metrics during the test window.
Writes prometheus_query_results.json for the MLflow logger to pick up.

Queries the same metrics that Grafana dashboards display:
- OTel application metrics (GenAI latency, request rate, DB pool, etc.)
- vLLM detailed metrics (TTFT, queue time, token throughput, etc.)
- GPU/DCGM metrics
- Cluster per-pod CPU/memory

Usage:
    python query_prometheus.py \
      --results-dir /workspace/results \
      --prometheus-url https://thanos-querier.openshift-monitoring.svc:9091 \
      --namespace llamastack-bench \
      --token-path /var/run/secrets/kubernetes.io/serviceaccount/token
"""
import json
import ssl
import argparse
import urllib.request
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--prometheus-url", default="https://thanos-querier.openshift-monitoring.svc:9091")
    parser.add_argument("--namespace", default="llamastack-bench")
    parser.add_argument("--token-path", default="/var/run/secrets/kubernetes.io/serviceaccount/token")
    return parser.parse_args()


def query_prom(url, query, token, start, end, step="15s"):
    """Query Prometheus range API and return the result."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    params = urllib.parse.urlencode({
        "query": query,
        "start": int(start),
        "end": int(end),
        "step": step,
    })
    req_url = f"{url}/api/v1/query_range?{params}"
    req = urllib.request.Request(req_url, headers={"Authorization": f"Bearer {token}"})
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)
        data = json.loads(resp.read())
        return data.get("data", {}).get("result", [])
    except Exception as e:
        print(f"  WARNING: Query failed: {e}")
        return []


def query_instant(url, query, token, time_point):
    """Query Prometheus instant API."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    params = urllib.parse.urlencode({"query": query, "time": int(time_point)})
    req_url = f"{url}/api/v1/query?{params}"
    req = urllib.request.Request(req_url, headers={"Authorization": f"Bearer {token}"})
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)
        data = json.loads(resp.read())
        return data.get("data", {}).get("result", [])
    except Exception as e:
        print(f"  WARNING: Query failed: {e}")
        return []


def avg_from_range(results):
    """Compute average value from range query results."""
    if not results:
        return 0
    all_vals = []
    for series in results:
        for ts, val in series.get("values", []):
            try:
                v = float(val)
                if v > 0 and v != float("inf") and v != float("nan"):
                    all_vals.append(v)
            except (ValueError, TypeError):
                pass
    return sum(all_vals) / len(all_vals) if all_vals else 0


def max_from_range(results):
    """Get max value from range query results."""
    if not results:
        return 0
    all_vals = []
    for series in results:
        for ts, val in series.get("values", []):
            try:
                v = float(val)
                if v > 0 and v != float("inf") and v != float("nan"):
                    all_vals.append(v)
            except (ValueError, TypeError):
                pass
    return max(all_vals) if all_vals else 0


def last_from_instant(results):
    """Get the last value from instant query results."""
    if not results:
        return 0
    try:
        return float(results[0].get("value", [0, 0])[1])
    except (ValueError, TypeError, IndexError):
        return 0


def main():
    import urllib.parse
    args = parse_args()
    d = Path(args.results_dir)
    ns = args.namespace
    url = args.prometheus_url

    # Read test timestamps
    start_file = d / "test_start_epoch_precise"
    end_file = d / "test_end_epoch_precise"
    if not start_file.exists():
        start_file = d / "test_start_epoch"
    if not end_file.exists():
        end_file = d / "test_end_epoch"
    if not start_file.exists() or not end_file.exists():
        print("WARNING: Test timestamps not found, skipping Prometheus queries")
        return

    start = float(start_file.read_text().strip())
    end = float(end_file.read_text().strip())
    print(f"Test window: {end - start:.0f}s")

    # Read SA token
    try:
        token = Path(args.token_path).read_text().strip()
    except Exception:
        print("WARNING: Could not read SA token, skipping Prometheus queries")
        return

    metrics = {}

    # --- OTel Application Metrics ---
    print("Querying OTel application metrics...")

    r = query_prom(url, f'sum(rate(gen_ai_client_operation_duration_seconds_count{{namespace="{ns}"}}[1m]))', token, start, end)
    metrics["otel/genai_request_rate_avg"] = avg_from_range(r)

    r = query_prom(url, f'sum(rate(gen_ai_client_operation_duration_seconds_sum{{namespace="{ns}"}}[1m])) / sum(rate(gen_ai_client_operation_duration_seconds_count{{namespace="{ns}"}}[1m]))', token, start, end)
    metrics["otel/genai_avg_latency_s"] = avg_from_range(r)

    r = query_prom(url, f'max(db_client_connections_usage{{namespace="{ns}", service="otel-collector-collector", state="used"}})', token, start, end)
    metrics["otel/db_pool_used_avg"] = avg_from_range(r)
    metrics["otel/db_pool_used_max"] = max_from_range(r)

    r = query_prom(url, f'max(db_client_connections_usage{{namespace="{ns}", service="otel-collector-collector", state="idle"}})', token, start, end)
    metrics["otel/db_pool_idle_avg"] = avg_from_range(r)

    r = query_prom(url, f'sum by (http_method) (http_server_active_requests{{namespace="{ns}", service="otel-collector-collector"}})', token, start, end)
    metrics["otel/active_requests_max"] = max_from_range(r)
    metrics["otel/active_requests_avg"] = avg_from_range(r)

    r = query_prom(url, f'process_cpu_utilization_ratio{{namespace="{ns}", service="otel-collector-collector"}}', token, start, end)
    metrics["otel/cpu_utilization_avg"] = avg_from_range(r)
    metrics["otel/cpu_utilization_max"] = max_from_range(r)

    r = query_prom(url, f'process_memory_usage_bytes{{namespace="{ns}", service="otel-collector-collector"}}', token, start, end)
    metrics["otel/memory_rss_avg_bytes"] = avg_from_range(r)
    metrics["otel/memory_rss_max_bytes"] = max_from_range(r)

    # --- vLLM Detailed Metrics ---
    print("Querying vLLM metrics...")

    r = query_prom(url, f'max(vllm:num_requests_running{{namespace="{ns}"}})', token, start, end)
    metrics["vllm/requests_running_avg"] = avg_from_range(r)
    metrics["vllm/requests_running_max"] = max_from_range(r)

    r = query_prom(url, f'max(vllm:num_requests_waiting{{namespace="{ns}"}})', token, start, end)
    metrics["vllm/requests_waiting_avg"] = avg_from_range(r)
    metrics["vllm/requests_waiting_max"] = max_from_range(r)

    r = query_prom(url, f'max(vllm:kv_cache_usage_perc{{namespace="{ns}"}})', token, start, end)
    metrics["vllm/kv_cache_usage_avg"] = avg_from_range(r)
    metrics["vllm/kv_cache_usage_max"] = max_from_range(r)

    r = query_prom(url, f'sum(rate(vllm:prompt_tokens_total{{namespace="{ns}"}}[1m]))', token, start, end)
    metrics["vllm/prompt_throughput_avg_tps"] = avg_from_range(r)

    r = query_prom(url, f'sum(rate(vllm:generation_tokens_total{{namespace="{ns}"}}[1m]))', token, start, end)
    metrics["vllm/generation_throughput_avg_tps"] = avg_from_range(r)

    r = query_prom(url, f'histogram_quantile(0.50, sum(rate(vllm:time_to_first_token_seconds_bucket{{namespace="{ns}"}}[1m])) by (le))', token, start, end)
    metrics["vllm/ttft_p50_s"] = avg_from_range(r)

    r = query_prom(url, f'histogram_quantile(0.95, sum(rate(vllm:time_to_first_token_seconds_bucket{{namespace="{ns}"}}[1m])) by (le))', token, start, end)
    metrics["vllm/ttft_p95_s"] = avg_from_range(r)

    r = query_prom(url, f'histogram_quantile(0.50, sum(rate(vllm:e2e_request_latency_seconds_bucket{{namespace="{ns}"}}[1m])) by (le))', token, start, end)
    metrics["vllm/e2e_latency_p50_s"] = avg_from_range(r)

    r = query_prom(url, f'histogram_quantile(0.95, sum(rate(vllm:e2e_request_latency_seconds_bucket{{namespace="{ns}"}}[1m])) by (le))', token, start, end)
    metrics["vllm/e2e_latency_p95_s"] = avg_from_range(r)

    r = query_prom(url, f'histogram_quantile(0.50, sum(rate(vllm:inter_token_latency_seconds_bucket{{namespace="{ns}"}}[1m])) by (le))', token, start, end)
    metrics["vllm/inter_token_latency_p50_s"] = avg_from_range(r)

    r = query_prom(url, f'histogram_quantile(0.50, sum(rate(vllm:request_queue_time_seconds_bucket{{namespace="{ns}"}}[1m])) by (le))', token, start, end)
    metrics["vllm/queue_time_p50_s"] = avg_from_range(r)

    r = query_prom(url, f'histogram_quantile(0.95, sum(rate(vllm:request_queue_time_seconds_bucket{{namespace="{ns}"}}[1m])) by (le))', token, start, end)
    metrics["vllm/queue_time_p95_s"] = avg_from_range(r)

    # --- GPU/DCGM Metrics ---
    print("Querying GPU metrics...")

    r = query_prom(url, 'DCGM_FI_DEV_GPU_UTIL', token, start, end)
    metrics["gpu/utilization_avg_pct"] = avg_from_range(r)
    metrics["gpu/utilization_max_pct"] = max_from_range(r)

    r = query_prom(url, 'DCGM_FI_DEV_FB_USED', token, start, end)
    metrics["gpu/memory_used_avg_mib"] = avg_from_range(r)
    metrics["gpu/memory_used_max_mib"] = max_from_range(r)

    r = query_prom(url, 'DCGM_FI_DEV_GPU_TEMP', token, start, end)
    metrics["gpu/temperature_avg_c"] = avg_from_range(r)
    metrics["gpu/temperature_max_c"] = max_from_range(r)

    r = query_prom(url, 'DCGM_FI_DEV_POWER_USAGE', token, start, end)
    metrics["gpu/power_avg_w"] = avg_from_range(r)
    metrics["gpu/power_max_w"] = max_from_range(r)

    # --- Postgres (from exporter, aggregated for test window) ---
    print("Querying Postgres exporter metrics...")

    r = query_prom(url, f'sum by (state) (pg_stat_activity_count{{namespace="{ns}"}})', token, start, end)
    metrics["pg/active_connections_avg"] = avg_from_range(r)
    metrics["pg/active_connections_max"] = max_from_range(r)

    r = query_prom(url, f'rate(pg_stat_database_xact_commit{{namespace="{ns}", datname="llamastack"}}[1m])', token, start, end)
    metrics["pg/commits_per_sec_avg"] = avg_from_range(r)

    r = query_prom(url, f'rate(pg_stat_database_xact_rollback{{namespace="{ns}", datname="llamastack"}}[1m])', token, start, end)
    metrics["pg/rollbacks_per_sec_avg"] = avg_from_range(r)

    r = query_prom(url, f'rate(pg_stat_database_tup_inserted{{namespace="{ns}", datname="llamastack"}}[1m])', token, start, end)
    metrics["pg/inserts_per_sec_avg"] = avg_from_range(r)

    # --- Per-pod CPU/Memory ---
    print("Querying per-pod CPU/memory...")

    r = query_prom(url, f'sum(rate(container_cpu_usage_seconds_total{{namespace="{ns}", container!="", container!="POD"}}[1m])) by (pod)', token, start, end)
    metrics["cluster/total_cpu_cores_avg"] = avg_from_range(r)

    r = query_prom(url, f'sum(container_memory_working_set_bytes{{namespace="{ns}", container!="", container!="POD"}})', token, start, end)
    metrics["cluster/total_memory_avg_bytes"] = avg_from_range(r)

    # Filter out zero/empty metrics
    metrics = {k: round(v, 6) for k, v in metrics.items() if v and v > 0}

    print(f"\nCollected {len(metrics)} Prometheus metrics for test window")
    for k, v in sorted(metrics.items()):
        print(f"  {k}: {v}")

    output_path = d / "prometheus_query_results.json"
    output_path.write_text(json.dumps(metrics, indent=2))
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
