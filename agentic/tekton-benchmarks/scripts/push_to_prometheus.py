"""
Push benchmark results to Prometheus Pushgateway.
Reads the same workspace files as mlflow_logger.py and pushes them
so Grafana can display all metrics without requiring MLflow.

Usage:
    python push_to_prometheus.py \
      --results-dir /workspace/results \
      --pushgateway-url http://pushgateway.llamastack-monitoring.svc:9091 \
      --run-id my-pipeline-run-123
"""
import json
import argparse
from pathlib import Path

try:
    from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
except ImportError:
    import subprocess
    subprocess.check_call(["pip", "install", "--quiet", "prometheus_client"])
    from prometheus_client import CollectorRegistry, Gauge, push_to_gateway


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--pushgateway-url", default="http://pushgateway.llamastack-monitoring.svc:9091")
    parser.add_argument("--run-id", default="unknown")
    return parser.parse_args()


def read_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        print(f"WARNING: Failed to read {path}: {e}")
        return None


def read_jsonl(path):
    if not path.exists():
        return []
    samples = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return samples


def main():
    args = parse_args()
    d = Path(args.results_dir)
    registry = CollectorRegistry()
    labels = ["run_id"]
    label_values = [args.run_id]

    pushed_count = 0
    gauges = {}

    def g(name, desc):
        if name not in gauges:
            gauges[name] = Gauge(name, desc, labels, registry=registry)
        return gauges[name]

    # --- Locust summary ---
    summary = read_json(d / "summary_metrics.json")
    if summary:
        print(f"Pushing {len(summary)} locust summary metrics")
        for key, val in summary.items():
            safe_name = "locust_" + key.replace("/", "_").replace("-", "_")
            g(safe_name, key).labels(*label_values).set(val)
            pushed_count += 1

    # --- Locust time-series (last sample as current gauge, ts_ prefix) ---
    timeseries = read_json(d / "timeseries_metrics.json")
    if timeseries and len(timeseries) > 0:
        last = timeseries[-1]
        print(f"Pushing locust time-series ({len(timeseries)} samples, using last)")
        for key in ["active_users", "target_users", "requests_per_sec", "failures_per_sec",
                     "avg_response_time_ms", "total_requests", "total_failures", "fail_ratio"]:
            if key in last:
                g(f"locust_ts_{key}", key).labels(*label_values).set(last[key])
                pushed_count += 1

    # --- HPA metrics (last sample) ---
    hpa = read_jsonl(d / "hpa-metrics.jsonl")
    if hpa:
        last = hpa[-1]
        print(f"Pushing HPA metrics ({len(hpa)} samples, using last)")
        g("hpa_pod_count", "pod count").labels(*label_values).set(last.get("pod_count", 0))
        g("hpa_avg_memory_ki", "avg memory Ki").labels(*label_values).set(last.get("avg_memory_ki", 0))
        g("hpa_avg_cpu_n", "avg cpu nanocores").labels(*label_values).set(last.get("avg_cpu_n", 0))
        h = last.get("hpa", {})
        if h:
            g("hpa_current_replicas", "current replicas").labels(*label_values).set(h.get("currentReplicas") or 0)
            g("hpa_desired_replicas", "desired replicas").labels(*label_values).set(h.get("desiredReplicas") or 0)
        pushed_count += 5

    # --- Prometheus sidecar metrics (last sample) ---
    prom = read_jsonl(d / "prometheus-metrics.jsonl")
    if prom:
        last = prom[-1]
        print(f"Pushing prom sidecar metrics ({len(prom)} samples, using last)")
        for key in ["pg_active_connections", "pg_xact_commits", "pg_xact_rollbacks",
                     "pg_cache_hit_ratio", "pg_deadlocks", "pg_rows_inserted", "pg_lock_count",
                     "vllm_requests_running", "vllm_requests_waiting",
                     "vllm_gpu_cache_pct", "vllm_throughput_tps"]:
            val = last.get(key, 0)
            g(f"sidecar_{key}", key).labels(*label_values).set(val)
            pushed_count += 1

    # --- Trace metrics ---
    trace_data = read_json(d / "trace_metrics.json")
    if trace_data:
        agg = trace_data.get("aggregate_metrics", {})
        per_req = trace_data.get("per_request_metrics", [])
        print(f"Pushing trace metrics: {len(agg)} aggregate, {len(per_req)} per-request")

        for key, val in agg.items():
            safe_name = "trace_" + key.replace("/", "_").replace("-", "_")
            g(safe_name, key).labels(*label_values).set(val)
            pushed_count += 1

        if per_req:
            last_req = per_req[-1]
            for key in ["request_duration_ms", "inference_duration_ms", "db_duration_ms",
                         "db_connect_ms", "mcp_http_duration_ms", "ls_overhead_ms",
                         "input_tokens", "output_tokens", "tool_calls"]:
                val = last_req.get(key, 0)
                g(f"trace_last_{key}", key).labels(*label_values).set(val)
                pushed_count += 1

    # --- Push everything ---
    if pushed_count == 0:
        print("No metrics found to push")
        return

    print(f"\nPushing {pushed_count} metrics to {args.pushgateway_url}")
    push_to_gateway(
        args.pushgateway_url,
        job="llamastack_benchmark",
        grouping_key={"run_id": args.run_id},
        registry=registry,
    )
    print("Done.")


if __name__ == "__main__":
    main()
