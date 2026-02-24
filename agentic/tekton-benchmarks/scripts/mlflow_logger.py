"""
MLflow Logger — reads all result files and batch-logs to SageMaker MLflow.

Reads Locust CSVs, time-series metrics, summary metrics, MCP metrics,
HPA metrics, and trace metrics from the shared workspace, then logs
everything via client.log_batch() for efficiency.

Usage:
    python mlflow_logger.py \
      --results-dir /workspace/results \
      --experiment llamastack-benchmarks \
      --run-name-prefix tekton \
      --param users=10 --param model=vllm-inference/llama-32-3b-instruct
"""
import os
import csv
import json
import logging
import argparse
import time
from pathlib import Path
from datetime import datetime

logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)

import mlflow
from mlflow import MlflowClient
from mlflow.entities import Metric, Param, RunTag

MAX_BATCH_SIZE = 1000


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--experiment", default="llamastack-benchmarks")
    parser.add_argument("--run-name-prefix", default="tekton")
    parser.add_argument("--param", action="append", default=[])
    return parser.parse_args()


def parse_params(param_list):
    params = {"test_type": "locust_load_test"}
    for p in param_list:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k] = v
    return params


def read_locust_stats(d):
    f = d / "locust-results_stats.csv"
    if not f.exists():
        print(f"WARNING: {f} not found")
        return {}
    metrics = {}
    field_map = {
        "Request Count": "locust/total_requests",
        "Failure Count": "locust/failure_count",
        "Average Response Time": "locust/avg_response_time_ms",
        "Min Response Time": "locust/min_response_time_ms",
        "Max Response Time": "locust/max_response_time_ms",
        "Median Response Time": "locust/median_response_time_ms",
        "Requests/s": "locust/requests_per_sec",
        "Failures/s": "locust/failures_per_sec",
    }
    with open(f, "r") as fh:
        for row in csv.DictReader(fh):
            if row.get("Name", "").strip() == "Aggregated":
                for col, name in field_map.items():
                    try:
                        metrics[name] = float(row.get(col, "0"))
                    except (ValueError, TypeError):
                        pass
                break
    print(f"Parsed {len(metrics)} Locust aggregate metrics")
    return metrics


def read_summary_metrics(d):
    """Read summary_metrics.json written by metrics_collector.py"""
    f = d / "summary_metrics.json"
    if not f.exists():
        print(f"INFO: {f} not found")
        return {}
    try:
        data = json.loads(f.read_text())
        metrics = {f"summary/{k}": v for k, v in data.items()}
        print(f"Parsed {len(metrics)} summary metrics")
        return metrics
    except Exception as e:
        print(f"WARNING: Failed to read summary metrics: {e}")
        return {}


def read_timeseries_metrics(d):
    """Read timeseries_metrics.json written by metrics_collector.py"""
    f = d / "timeseries_metrics.json"
    if not f.exists():
        print(f"INFO: {f} not found")
        return []
    try:
        samples = json.loads(f.read_text())
        print(f"Parsed {len(samples)} time-series samples")
        return samples
    except Exception as e:
        print(f"WARNING: Failed to read timeseries metrics: {e}")
        return []


def read_mcp_metrics(d):
    f = d / "mcp_metrics.csv"
    if not f.exists():
        print(f"INFO: {f} not found (MCP metrics not captured)")
        return {}
    rt, tc, it, ot, tt = [], [], [], [], []
    with open(f, "r") as fh:
        for row in csv.DictReader(fh):
            try:
                rt.append(float(row.get("response_time", 0)))
                tc.append(int(row.get("mcp_call_count", 0)))
                it.append(int(row.get("input_tokens", 0)))
                ot.append(int(row.get("output_tokens", 0)))
                tt.append(int(row.get("total_tokens", 0)))
            except (ValueError, TypeError):
                pass
    if not rt:
        return {}
    n = len(rt)
    metrics = {
        "mcp/avg_response_time_ms": sum(rt) / n,
        "mcp/avg_tool_calls_per_request": sum(tc) / n,
        "mcp/total_tool_calls": sum(tc),
        "mcp/avg_input_tokens": sum(it) / n,
        "mcp/avg_output_tokens": sum(ot) / n,
        "mcp/avg_total_tokens": sum(tt) / n,
    }
    print(f"Parsed {len(metrics)} MCP aggregate metrics from {n} requests")
    return metrics


def read_hpa_metrics(d):
    f = d / "hpa-metrics.jsonl"
    if not f.exists():
        print(f"INFO: {f} not found (HPA metrics not captured)")
        return []
    samples = []
    with open(f, "r") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    samples.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    print(f"Parsed {len(samples)} HPA metric samples")
    return samples


def read_trace_metrics(d):
    f = d / "trace_metrics.json"
    if not f.exists():
        print(f"INFO: {f} not found (trace analysis may not have run)")
        return {}, []
    try:
        data = json.loads(f.read_text())
        agg = data.get("aggregate_metrics", {})
        per_req = data.get("per_request_metrics", [])
        print(f"Parsed trace metrics: {len(agg)} aggregate, {len(per_req)} per-request")
        return agg, per_req
    except Exception as e:
        print(f"WARNING: Failed to read trace metrics: {e}")
        return {}, []


def log_batch_chunked(client, run_id, metrics=None, params=None, tags=None):
    metrics = metrics or []
    params = params or []
    tags = tags or []
    if params or tags:
        client.log_batch(run_id=run_id, params=params[:MAX_BATCH_SIZE],
                         tags=tags[:MAX_BATCH_SIZE], metrics=[])
        print(f"Logged {len(params)} params and {len(tags)} tags")
    for i in range(0, len(metrics), MAX_BATCH_SIZE):
        chunk = metrics[i:i + MAX_BATCH_SIZE]
        client.log_batch(run_id=run_id, metrics=chunk, params=[], tags=[])
        print(f"Logged metrics chunk {i // MAX_BATCH_SIZE + 1}: {len(chunk)} metrics")
    if metrics:
        print(f"Total: {len(metrics)} metrics in {(len(metrics) + MAX_BATCH_SIZE - 1) // MAX_BATCH_SIZE} batch(es)")


def main():
    args = parse_args()
    results_dir = Path(args.results_dir)
    test_params = parse_params(args.param)
    print(f"Results dir: {results_dir}")
    print(f"Params: {test_params}")

    locust = read_locust_stats(results_dir)
    summary = read_summary_metrics(results_dir)
    timeseries = read_timeseries_metrics(results_dir)
    mcp = read_mcp_metrics(results_dir)
    hpa = read_hpa_metrics(results_dir)
    trace_agg, trace_per_req = read_trace_metrics(results_dir)

    tracking_arn = os.environ.get("MLFLOW_TRACKING_ARN", "")
    if not tracking_arn:
        print("ERROR: MLFLOW_TRACKING_ARN not set")
        return

    print(f"Connecting to MLflow: {tracking_arn[:60]}...")
    mlflow.set_tracking_uri(tracking_arn)
    mlflow.set_experiment(args.experiment)
    client = MlflowClient()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    model_short = test_params.get("model", "unknown").split("/")[-1]
    users = test_params.get("users", "?")
    run_name = f"{args.run_name_prefix}-{model_short}-{users}u-{timestamp}"

    batch_params = [Param(key=k, value=str(v)) for k, v in test_params.items()]
    batch_tags = [RunTag("pipeline", "tekton"), RunTag("run_source", "tekton-pipeline")]

    now_ms = int(time.time() * 1000)
    batch_metrics = []

    # Locust CSV aggregate metrics
    for name, val in locust.items():
        batch_metrics.append(Metric(key=name, value=val, timestamp=now_ms, step=0))

    # Summary metrics from metrics_collector
    for name, val in summary.items():
        batch_metrics.append(Metric(key=name, value=val, timestamp=now_ms, step=0))

    # MCP metrics
    for name, val in mcp.items():
        batch_metrics.append(Metric(key=name, value=val, timestamp=now_ms, step=0))

    # Trace aggregate metrics
    for name, val in trace_agg.items():
        batch_metrics.append(Metric(key=name, value=val, timestamp=now_ms, step=0))

    # Time-series metrics (per-second samples from metrics_collector)
    for sample in timeseries:
        step = sample.get("second", 0)
        batch_metrics.append(Metric(key="active_users", value=sample.get("active_users", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="target_users", value=sample.get("target_users", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="rps_10s_window", value=sample.get("requests_per_sec", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="failures_per_sec_10s_window", value=sample.get("failures_per_sec", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="avg_response_time_cumulative_ms", value=sample.get("avg_response_time_ms", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="total_requests_cumulative", value=sample.get("total_requests", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="total_failures_cumulative", value=sample.get("total_failures", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="fail_ratio_cumulative_pct", value=sample.get("fail_ratio", 0) * 100, timestamp=now_ms, step=step))

    # HPA metrics (per-second samples from sidecar)
    for s in hpa:
        step = s.get("sample", 0)
        batch_metrics.append(Metric(key="cluster/pod_count", value=s.get("pod_count", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="memory/avg_ki", value=s.get("avg_memory_ki", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="cpu/avg_nanocores", value=s.get("avg_cpu_n", 0), timestamp=now_ms, step=step))
        h = s.get("hpa", {})
        if h:
            batch_metrics.append(Metric(key="hpa/current_replicas", value=h.get("currentReplicas", 0), timestamp=now_ms, step=step))
            batch_metrics.append(Metric(key="hpa/desired_replicas", value=h.get("desiredReplicas", 0), timestamp=now_ms, step=step))
            batch_metrics.append(Metric(key="cpu/hpa_percent", value=h.get("currentCPUPct", 0), timestamp=now_ms, step=step))
            batch_metrics.append(Metric(key="memory/hpa_percent", value=h.get("currentMemoryPct", 0), timestamp=now_ms, step=step))

    # Trace per-request metrics
    for r in trace_per_req:
        step = r.get("step", 0)
        if r.get("request_duration_ms", 0) > 0:
            batch_metrics.append(Metric(key="trace/request_duration_ms", value=r["request_duration_ms"], timestamp=now_ms, step=step))
        if r.get("tool_calls", 0) > 0:
            batch_metrics.append(Metric(key="trace/tool_calls_per_request", value=r["tool_calls"], timestamp=now_ms, step=step))

    print(f"\nBatch summary: {len(batch_params)} params, {len(batch_tags)} tags, {len(batch_metrics)} metrics")

    with mlflow.start_run(run_name=run_name) as run:
        run_id = run.info.run_id
        print(f"MLflow run: {run_name} (id: {run_id})")
        log_batch_chunked(client, run_id, batch_metrics, batch_params, batch_tags)

        artifact_count = 0
        for rf in sorted(results_dir.glob("*")):
            if rf.is_file():
                mlflow.log_artifact(str(rf))
                artifact_count += 1
        print(f"Logged {artifact_count} artifacts")

    print(f"MLflow logging complete! Run: {run_name}")


if __name__ == "__main__":
    main()
