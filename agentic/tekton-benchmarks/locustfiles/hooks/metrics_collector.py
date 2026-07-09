"""
Metrics collector for Locust.
Samples time-series metrics every second via a background greenlet
and writes all results to files in LOCUST_OUTPUT_DIR for the MLflow task to pick up.

In distributed / --processes mode:
  - Workers do not write result files (they only generate load).
  - The master (or standalone LocalRunner) owns all file writes.
  - Summary is written on the quit event, after workers have sent final
    stats reports and Locust has merged them into the master aggregate.

Output files:
  - timeseries_metrics.json: per-second samples (active users, RPS, latency, etc.)
  - summary_metrics.json: final aggregate stats (avg, min, max, p50, p95, p99, etc.)

Environment variables:
  LOCUST_OUTPUT_DIR: directory to write result files (default: /tmp)
"""
import os
import json
import time
from locust import events
from locust.runners import WorkerRunner
import gevent

_MODULE_ID = None
_run_started = False
_summary_written = False
_environment = None
_timeseries_buffer = []
_sampling_greenlet = None


def _is_worker(environment):
    """True only for Locust worker processes; False for master and standalone."""
    return isinstance(environment.runner, WorkerRunner)


def _register_listeners():
    global _MODULE_ID
    import uuid
    _MODULE_ID = str(uuid.uuid4())[:8]

    existing = os.environ.get("_METRICS_HOOKS_REGISTERED", "")
    if existing:
        return
    os.environ["_METRICS_HOOKS_REGISTERED"] = _MODULE_ID

    events.test_start.add_listener(_on_test_start)
    events.test_stop.add_listener(_on_test_stop)
    events.quit.add_listener(_on_quit)


def _on_test_start(environment, **kwargs):
    global _run_started, _environment, _timeseries_buffer, _summary_written

    _environment = environment

    if _is_worker(environment):
        return

    if _run_started:
        return
    _run_started = True
    _summary_written = False
    _timeseries_buffer = []

    output_dir = os.environ.get("LOCUST_OUTPUT_DIR", "/tmp")
    with open(os.path.join(output_dir, "test_start_epoch_precise"), "w") as f:
        f.write(f"{time.time():.6f}")
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


def _stop_timeseries_sampling():
    global _sampling_greenlet
    if _sampling_greenlet is not None:
        _sampling_greenlet.kill()
        _sampling_greenlet = None


def _on_test_stop(environment, **kwargs):
    """Freeze the timeseries sampler at end of test; do not write files yet.

    In distributed mode, test_stop fires on the master before workers send
    their final stats reports. Summary/file writes happen later on quit.
    """
    if _is_worker(environment):
        return
    _stop_timeseries_sampling()


def _write_results(environment):
    """Write timeseries + summary from the master's final aggregated stats."""
    global _summary_written, _run_started

    if _summary_written:
        return
    if environment is None or environment.runner is None:
        return
    if _is_worker(environment):
        return

    _stop_timeseries_sampling()

    output_dir = os.environ.get("LOCUST_OUTPUT_DIR", "/tmp")
    with open(os.path.join(output_dir, "test_end_epoch_precise"), "w") as f:
        f.write(f"{time.time():.6f}")

    stats = environment.runner.stats.total

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"Total Requests: {stats.num_requests}")
    print(f"Total Failures: {stats.num_failures}")
    print(f"Failure Rate: {stats.fail_ratio * 100:.2f}%")
    print(f"Avg Response Time: {stats.avg_response_time:.2f} ms")
    min_rt = stats.min_response_time
    print(f"Min Response Time: {min_rt:.2f} ms" if min_rt is not None else "Min Response Time: n/a")
    print(f"Max Response Time: {stats.max_response_time:.2f} ms")
    print(f"Requests/sec: {stats.total_rps:.2f}")
    print("=" * 60 + "\n")

    ts_path = os.path.join(output_dir, "timeseries_metrics.json")
    with open(ts_path, "w") as f:
        json.dump(_timeseries_buffer, f)
    print(f"Wrote {len(_timeseries_buffer)} time-series samples to {ts_path}")

    summary = {
        "total_requests": stats.num_requests,
        "total_failures": stats.num_failures,
        "failure_rate_pct": round(stats.fail_ratio * 100, 4),
        "requests_per_second": round(stats.total_rps, 2),
        "avg_response_time_ms": round(stats.avg_response_time, 2),
        "min_response_time_ms": round(min_rt, 2) if min_rt is not None else 0,
        "max_response_time_ms": round(stats.max_response_time, 2),
    }

    if stats.num_requests > 0:
        summary["response_time_p50_ms"] = float(stats.get_response_time_percentile(0.50) or 0)
        summary["response_time_p95_ms"] = float(stats.get_response_time_percentile(0.95) or 0)
        summary["response_time_p99_ms"] = float(stats.get_response_time_percentile(0.99) or 0)

    summary_path = os.path.join(output_dir, "summary_metrics.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Wrote summary metrics to {summary_path}")

    _summary_written = True
    _run_started = False


def _on_quit(exit_code, **kwargs):
    """Write final metrics after Locust has merged the last worker reports.

    events.quit fires after runner.quit() (which waits for final worker stats)
    and after Locust prints the Aggregated stats table.
    """
    _write_results(_environment)


_register_listeners()
