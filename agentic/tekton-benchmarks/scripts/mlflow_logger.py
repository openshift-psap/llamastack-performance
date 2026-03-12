"""
MLflow Logger — reads all result files and batch-logs to SageMaker MLflow.

Reads summary metrics, time-series metrics, HPA metrics, and trace metrics
from the shared workspace, then logs everything via client.log_batch()
for efficiency.

Usage:
    python mlflow_logger.py \
      --results-dir /workspace/results \
      --experiment llamastack-benchmarks \
      --run-name-prefix tekton \
      --param users=10 --param model=vllm-inference/llama-32-3b-instruct
"""
import os
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



def read_summary_metrics(d):
    """Read summary_metrics.json written by metrics_collector.py"""
    f = d / "summary_metrics.json"
    if not f.exists():
        print(f"INFO: {f} not found")
        return {}
    try:
        data = json.loads(f.read_text())
        metrics = {f"locust/{k}": v for k, v in data.items()}
        print(f"Parsed {len(metrics)} locust summary metrics")
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

    summary = read_summary_metrics(results_dir)
    timeseries = read_timeseries_metrics(results_dir)
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
    if args.run_name_prefix and args.run_name_prefix != "tekton":
        run_name = args.run_name_prefix
    else:
        run_name = f"{args.run_name_prefix}-{model_short}-{users}u-{timestamp}"

    batch_params = [Param(key=k, value=str(v)) for k, v in test_params.items()]
    batch_tags = [RunTag("pipeline", "tekton"), RunTag("run_source", "tekton-pipeline")]

    now_ms = int(time.time() * 1000)
    batch_metrics = []

    # Summary metrics from metrics_collector
    for name, val in summary.items():
        batch_metrics.append(Metric(key=name, value=val, timestamp=now_ms, step=0))

    # Trace aggregate metrics
    for name, val in trace_agg.items():
        batch_metrics.append(Metric(key=name, value=val, timestamp=now_ms, step=0))

    # Time-series metrics (per-second samples from metrics_collector)
    for sample in timeseries:
        step = sample.get("second", 0)
        batch_metrics.append(Metric(key="scenario/active_users", value=sample.get("active_users", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="scenario/target_users", value=sample.get("target_users", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="scenario/rps_10s_window", value=sample.get("requests_per_sec", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="scenario/failures_per_sec_10s_window", value=sample.get("failures_per_sec", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="scenario/avg_response_time_cumulative_ms", value=sample.get("avg_response_time_ms", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="scenario/total_requests_cumulative", value=sample.get("total_requests", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="scenario/total_failures_cumulative", value=sample.get("total_failures", 0), timestamp=now_ms, step=step))
        batch_metrics.append(Metric(key="scenario/fail_ratio_cumulative_pct", value=sample.get("fail_ratio", 0) * 100, timestamp=now_ms, step=step))

    # HPA metrics (per-second samples from sidecar)
    for s in hpa:
        step = s.get("sample", 0)
        batch_metrics.append(Metric(key="hpa/pod_count", value=s.get("pod_count", 0), timestamp=now_ms, step=step))
        avg_memory_mib = s.get("avg_memory_ki", 0) / 1024
        batch_metrics.append(Metric(key="hpa/memory_avg_mib", value=avg_memory_mib, timestamp=now_ms, step=step))
        avg_cpu_millicores = s.get("avg_cpu_n", 0) / 1_000_000
        batch_metrics.append(Metric(key="hpa/cpu_avg_millicores", value=avg_cpu_millicores, timestamp=now_ms, step=step))
        h = s.get("hpa", {})
        if h:
            batch_metrics.append(Metric(key="hpa/current_replicas", value=h.get("currentReplicas") or 0, timestamp=now_ms, step=step))
            batch_metrics.append(Metric(key="hpa/desired_replicas", value=h.get("desiredReplicas") or 0, timestamp=now_ms, step=step))
            batch_metrics.append(Metric(key="hpa/cpu_percent", value=h.get("currentCPUPct") or 0, timestamp=now_ms, step=step))
            batch_metrics.append(Metric(key="hpa/memory_percent", value=h.get("currentMemoryPct") or 0, timestamp=now_ms, step=step))

    # Trace per-request time-series metrics
    trace_ts_keys = [
        ("request_duration_ms", "trace/ts/request_duration_ms"),
        ("inference_duration_ms", "trace/ts/inference_duration_ms"),
        ("list_mcp_tools_ms", "trace/ts/list_mcp_tools_ms"),
        ("invoke_mcp_tool_ms", "trace/ts/invoke_mcp_tool_ms"),
        ("db_duration_ms", "trace/ts/db_duration_ms"),
        ("db_connect_ms", "trace/ts/db_connect_ms"),
        ("db_connect_count", "trace/ts/db_connect_count"),
        ("db_insert_ms", "trace/ts/db_insert_ms"),
        ("db_insert_count", "trace/ts/db_insert_count"),
        ("db_begin_count", "trace/ts/db_begin_count"),
        ("db_commit_count", "trace/ts/db_commit_count"),
        ("db_rollback_count", "trace/ts/db_rollback_count"),
        ("mcp_http_duration_ms", "trace/ts/mcp_http_duration_ms"),
        ("ls_overhead_ms", "trace/ts/ls_overhead_ms"),
        ("ls_overhead_pct", "trace/ts/ls_overhead_pct"),
        ("input_tokens", "trace/ts/input_tokens"),
        ("output_tokens", "trace/ts/output_tokens"),
        ("tool_calls", "trace/ts/tool_calls"),
    ]
    for r in trace_per_req:
        step = r.get("step", 0)
        for src_key, metric_key in trace_ts_keys:
            val = r.get(src_key, 0)
            if val:
                batch_metrics.append(Metric(key=metric_key, value=val, timestamp=now_ms, step=step))

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
