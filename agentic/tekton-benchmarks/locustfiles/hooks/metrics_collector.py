"""
Metrics collector for Locust.
Samples time-series metrics every second via a background greenlet
and writes all results to files in LOCUST_OUTPUT_DIR for the MLflow task to pick up.

Output files:
  - timeseries_metrics.json: per-second samples (active users, RPS, latency, etc.)
  - summary_metrics.json: final aggregate stats (avg, min, max, p50, p95, p99, etc.)

Environment variables:
  LOCUST_OUTPUT_DIR: directory to write result files (default: /tmp)
"""
import os
import json
from datetime import datetime
from collections import defaultdict
from locust import events
import gevent

_MODULE_ID = None
_run_started = False
_timeseries_buffer = []
_sampling_greenlet = None
metrics_buffer = defaultdict(list)


def _register_listeners():
    global _MODULE_ID
    import uuid
    _MODULE_ID = str(uuid.uuid4())[:8]

    existing = os.environ.get("_METRICS_HOOKS_REGISTERED", "")
    if existing:
        return
    os.environ["_METRICS_HOOKS_REGISTERED"] = _MODULE_ID

    events.test_start.add_listener(_on_test_start)
    events.request.add_listener(_on_request)
    events.test_stop.add_listener(_on_test_stop)


def _on_test_start(environment, **kwargs):
    global _run_started
    if _run_started:
        return
    _run_started = True
    _start_timeseries_sampling(environment)


def _start_timeseries_sampling(environment):
    global _sampling_greenlet

    def _sample_loop():
        second = 0
        while True:
            gevent.sleep(1)
            try:
                runner = environment.runner
                stats = runner.stats.total
                _timeseries_buffer.append({
                    "second": second,
                    "active_users": runner.user_count,
                    "target_users": runner.target_user_count or 0,
                    "requests_per_sec": round(stats.current_rps, 2),
                    "failures_per_sec": round(stats.current_fail_per_sec, 2),
                    "avg_response_time_ms": round(stats.avg_response_time, 2),
                    "total_requests": stats.num_requests,
                    "total_failures": stats.num_failures,
                    "fail_ratio": round(stats.fail_ratio, 4),
                })
            except Exception:
                pass
            second += 1

    _sampling_greenlet = gevent.spawn(_sample_loop)


def _on_request(request_type, name, response_time, response_length, exception, **kwargs):
    metrics_buffer[f"{name}_response_time_ms"].append(response_time)
    metrics_buffer[f"{name}_response_length"].append(response_length or 0)
    if exception:
        metrics_buffer[f"{name}_failures"].append(1)
    else:
        metrics_buffer[f"{name}_successes"].append(1)


def _on_test_stop(environment, **kwargs):
    global _run_started, _sampling_greenlet

    if _sampling_greenlet is not None:
        _sampling_greenlet.kill()
        _sampling_greenlet = None

    output_dir = os.environ.get("LOCUST_OUTPUT_DIR", "/tmp")
    stats = environment.runner.stats.total

    # Console summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"Total Requests: {stats.num_requests}")
    print(f"Total Failures: {stats.num_failures}")
    print(f"Failure Rate: {stats.fail_ratio * 100:.2f}%")
    print(f"Avg Response Time: {stats.avg_response_time:.2f} ms")
    print(f"Min Response Time: {stats.min_response_time:.2f} ms")
    print(f"Max Response Time: {stats.max_response_time:.2f} ms")
    print(f"Requests/sec: {stats.total_rps:.2f}")
    print("=" * 60 + "\n")

    # Write time-series to file
    ts_path = os.path.join(output_dir, "timeseries_metrics.json")
    with open(ts_path, "w") as f:
        json.dump(_timeseries_buffer, f)
    print(f"Wrote {len(_timeseries_buffer)} time-series samples to {ts_path}")

    # Compute and write summary metrics
    all_response_times = []
    for name, values in metrics_buffer.items():
        if "response_time" in name:
            all_response_times.extend(values)

    summary = {
        "total_requests": stats.num_requests,
        "total_failures": stats.num_failures,
        "failure_rate_pct": round(stats.fail_ratio * 100, 4),
        "requests_per_second": round(stats.total_rps, 2),
        "avg_response_time_ms": round(stats.avg_response_time, 2),
        "min_response_time_ms": round(stats.min_response_time, 2),
        "max_response_time_ms": round(stats.max_response_time, 2),
    }

    if all_response_times:
        sorted_rt = sorted(all_response_times)
        summary["response_time_p50_ms"] = round(sorted_rt[len(sorted_rt) // 2], 2)
        summary["response_time_p95_ms"] = round(sorted_rt[int(len(sorted_rt) * 0.95)], 2)
        summary["response_time_p99_ms"] = round(sorted_rt[int(len(sorted_rt) * 0.99)], 2)

    summary_path = os.path.join(output_dir, "summary_metrics.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Wrote summary metrics to {summary_path}")


_register_listeners()
