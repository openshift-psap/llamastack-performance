"""
MLflow event hooks for Locust.
Buffers metrics and sends to MLflow at test end to avoid performance impact.
Also samples time-series metrics (user count, RPS, latency) every second
via a background greenlet.

Supports both:
- Standard MLflow: set MLFLOW_URL
- SageMaker MLflow: set MLFLOW_TRACKING_ARN + AWS credentials
"""
import os
import uuid
import logging
from datetime import datetime
from collections import defaultdict
from locust import events
import gevent

# Suppress noisy botocore/boto3 credential logging
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)

# Generate unique ID for this module instance
_MODULE_ID = str(uuid.uuid4())[:8]
print(f"DEBUG: mlflow_hooks module loaded, instance={_MODULE_ID}")

# Try to import mlflow, gracefully handle if not available
try:
    import mlflow
    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False
    print("INFO: mlflow not installed, metrics will be logged to console only")

# Try to import sagemaker_mlflow for ARN support
try:
    import sagemaker_mlflow
    SAGEMAKER_AVAILABLE = True
except ImportError:
    SAGEMAKER_AVAILABLE = False

# Buffer for metrics - avoid calling MLflow on every request
metrics_buffer = defaultdict(list)
_run_started = False

# Time-series buffer: list of dicts sampled every second
_timeseries_buffer = []
_sampling_greenlet = None


def _register_listeners():
    """Register event listeners only once using env var as cross-import guard."""
    # Use environment variable as flag (persists across module reimports)
    existing = os.environ.get("_MLFLOW_HOOKS_REGISTERED", "")
    print(f"DEBUG: _register_listeners called from module {_MODULE_ID}, existing={existing}")
    
    if existing:
        print(f"INFO: MLflow hooks already registered by {existing}, skipping")
        return
    os.environ["_MLFLOW_HOOKS_REGISTERED"] = _MODULE_ID
    
    events.test_start.add_listener(_on_test_start)
    events.request.add_listener(_on_request)
    events.test_stop.add_listener(_on_test_stop)
    print(f"INFO: MLflow event listeners registered by module {_MODULE_ID}")


def _on_test_start(environment, **kwargs):
    """Called when test starts - just mark that MLflow should be used at end."""
    global _run_started
    
    # Guard against double initialization
    if _run_started:
        return
    
    # Check if MLflow is enabled
    enable_mlflow = os.environ.get("ENABLE_MLFLOW", "false").lower() == "true"
    if not enable_mlflow:
        print("INFO: MLflow disabled (ENABLE_MLFLOW != true)")
        return
    
    # Check for tracking URI
    tracking_arn = os.environ.get("MLFLOW_TRACKING_ARN", "")
    mlflow_url = os.environ.get("MLFLOW_URL", "")
    
    if not tracking_arn and not mlflow_url:
        print("INFO: Neither MLFLOW_TRACKING_ARN nor MLFLOW_URL set, skipping MLflow")
        return
        
    if not MLFLOW_AVAILABLE:
        print("INFO: mlflow package not installed")
        return
    
    # Just mark that we should log to MLflow at test end
    # DO NOT touch MLflow API here - all interaction happens in _on_test_stop
    _run_started = True
    
    # Generate run name for later
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    users = os.environ.get("USERS", "?")
    model = os.environ.get("MODEL", "unknown").split("/")[-1]
    run_name = f"locust-{model}-{users}u-{timestamp}"
    os.environ["_MLFLOW_RUN_NAME"] = run_name
    
    print(f"MLflow will log at test end, run name: {run_name}")

    # Start time-series sampling greenlet
    _start_timeseries_sampling(environment)


def _start_timeseries_sampling(environment):
    """Spawn a background greenlet that samples metrics every second."""
    global _sampling_greenlet

    def _sample_loop():
        second = 0
        while True:
            gevent.sleep(1)
            try:
                runner = environment.runner
                stats = runner.stats.total
                sample = {
                    "second": second,
                    "active_users": runner.user_count,
                    "target_users": runner.target_user_count or 0,
                    "requests_per_sec": round(stats.current_rps, 2),
                    "failures_per_sec": round(stats.current_fail_per_sec, 2),
                    "avg_response_time_ms": round(stats.avg_response_time, 2),
                    "total_requests": stats.num_requests,
                    "total_failures": stats.num_failures,
                    "fail_ratio": round(stats.fail_ratio, 4),
                }
                _timeseries_buffer.append(sample)
            except Exception:
                pass  # Runner may not be ready yet
            second += 1

    _sampling_greenlet = gevent.spawn(_sample_loop)
    print("INFO: Time-series sampling started (every 1s)")


def _on_request(request_type, name, response_time, response_length, exception, **kwargs):
    """
    Called on every request - buffer metrics (don't call MLflow here).
    """
    metrics_buffer[f"{name}_response_time_ms"].append(response_time)
    metrics_buffer[f"{name}_response_length"].append(response_length or 0)
    
    if exception:
        metrics_buffer[f"{name}_failures"].append(1)
    else:
        metrics_buffer[f"{name}_successes"].append(1)


def _on_test_stop(environment, **kwargs):
    """Called when test stops - flush metrics to MLflow."""
    global _run_started, _sampling_greenlet
    
    # Stop the sampling greenlet
    if _sampling_greenlet is not None:
        _sampling_greenlet.kill()
        _sampling_greenlet = None
        print(f"INFO: Time-series sampling stopped. Collected {len(_timeseries_buffer)} samples.")
    
    # Always print summary to console
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    stats = environment.runner.stats.total
    print(f"Total Requests: {stats.num_requests}")
    print(f"Total Failures: {stats.num_failures}")
    print(f"Failure Rate: {stats.fail_ratio * 100:.2f}%")
    print(f"Avg Response Time: {stats.avg_response_time:.2f} ms")
    print(f"Min Response Time: {stats.min_response_time:.2f} ms")
    print(f"Max Response Time: {stats.max_response_time:.2f} ms")
    print(f"Requests/sec: {stats.total_rps:.2f}")
    print("="*60 + "\n")
    
    if not MLFLOW_AVAILABLE or not _run_started:
        return
    
    try:
        # Setup MLflow connection (exactly like the working script)
        tracking_arn = os.environ.get("MLFLOW_TRACKING_ARN", "")
        mlflow_url = os.environ.get("MLFLOW_URL", "")
        tracking_uri = tracking_arn if tracking_arn else mlflow_url
        
        print(f"Connecting to MLflow: {tracking_uri[:50]}...")
        mlflow.set_tracking_uri(tracking_uri)
        
        experiment_name = os.environ.get("MLFLOW_EXPERIMENT", "llamastack-benchmarks")
        mlflow.set_experiment(experiment_name)
        
        # Use context manager (like the working script)
        run_name = os.environ.get("_MLFLOW_RUN_NAME", "locust-test")
        with mlflow.start_run(run_name=run_name):
            # Log test parameters
            mlflow.log_param("users", os.environ.get("USERS", "unknown"))
            mlflow.log_param("spawn_rate", os.environ.get("SPAWN_RATE", "unknown"))
            mlflow.log_param("run_time_seconds", os.environ.get("RUN_TIME_SECONDS", "unknown"))
            mlflow.log_param("host", environment.host or "unknown")
            if os.environ.get("USER_CLASS") == "ResponsesMCPUser":
                mlflow.log_param("mcp_server", os.environ.get("MCP_SERVER", "unknown"))
            mlflow.log_param("model", os.environ.get("MODEL", "unknown"))
            mlflow.log_param("load_shape", os.environ.get("LOAD_SHAPE", "steady"))
            mlflow.log_param("user_class", os.environ.get("USER_CLASS", "ResponsesMCPUser"))
            
            # Log EXTRA_ENV params (shape-specific like SPIKE_*, HPA_*, etc.)
            extra_env = os.environ.get("EXTRA_ENV", "")
            if extra_env:
                for line in extra_env.strip().splitlines():
                    line = line.strip()
                    if "=" in line:
                        key, value = line.split("=", 1)
                        mlflow.log_param(key.lower(), value)
            
            # Log CUSTOM_STAGES if present
            custom_stages = os.environ.get("CUSTOM_STAGES", "[]")
            if custom_stages and custom_stages != "[]":
                mlflow.log_param("custom_stages", custom_stages)
            
            # Log summary metrics (single values)
            all_response_times = []
            total_successes = 0
            total_failures_count = 0
            for name, values in metrics_buffer.items():
                if not values:
                    continue
                if "response_time" in name:
                    all_response_times.extend(values)
                elif "successes" in name:
                    total_successes += sum(values)
                elif "failures" in name:
                    total_failures_count += sum(values)
            
            if all_response_times:
                sorted_rt = sorted(all_response_times)
                mlflow.log_metric("response_time_avg_ms", sum(sorted_rt) / len(sorted_rt))
                mlflow.log_metric("response_time_min_ms", sorted_rt[0])
                mlflow.log_metric("response_time_max_ms", sorted_rt[-1])
                mlflow.log_metric("response_time_p50_ms", sorted_rt[len(sorted_rt)//2])
                mlflow.log_metric("response_time_p95_ms", sorted_rt[int(len(sorted_rt)*0.95)])
                mlflow.log_metric("response_time_p99_ms", sorted_rt[int(len(sorted_rt)*0.99)])
            
            mlflow.log_metric("total_requests", stats.num_requests)
            mlflow.log_metric("total_failures", stats.num_failures)
            mlflow.log_metric("failure_rate_pct", stats.fail_ratio * 100)
            mlflow.log_metric("requests_per_second", stats.total_rps)
            
            # Log time-series metrics in batches (up to 1000 per API call)
            if _timeseries_buffer:
                print(f"Logging {len(_timeseries_buffer)} time-series samples to MLflow (batched)...")
                from mlflow.entities import Metric
                client = mlflow.tracking.MlflowClient()
                run_id = mlflow.active_run().info.run_id
                
                ts_metrics = []
                timestamp_ms = int(datetime.now().timestamp() * 1000)
                for sample in _timeseries_buffer:
                    step = sample["second"]
                    ts_metrics.extend([
                        Metric("active_users", sample["active_users"], timestamp_ms, step),
                        Metric("target_users", sample["target_users"], timestamp_ms, step),
                        Metric("rps_10s_window", sample["requests_per_sec"], timestamp_ms, step),
                        Metric("failures_per_sec_10s_window", sample["failures_per_sec"], timestamp_ms, step),
                        Metric("avg_response_time_cumulative_ms", sample["avg_response_time_ms"], timestamp_ms, step),
                        Metric("total_requests_cumulative", sample["total_requests"], timestamp_ms, step),
                        Metric("total_failures_cumulative", sample["total_failures"], timestamp_ms, step),
                        Metric("fail_ratio_cumulative_pct", sample["fail_ratio"] * 100, timestamp_ms, step),
                    ])
                
                # log_batch accepts up to 1000 metrics per call
                batch_size = 1000
                for i in range(0, len(ts_metrics), batch_size):
                    batch = ts_metrics[i:i + batch_size]
                    client.log_batch(run_id, metrics=batch)
                
                print(f"Time-series logging complete ({len(ts_metrics)} metrics in {((len(ts_metrics)-1)//batch_size)+1} batch(es)).")
        
        print("MLflow run completed successfully")
        
    except Exception as e:
        print(f"WARNING: Failed to log to MLflow: {e}")


# Register listeners once when module is imported
_register_listeners()
