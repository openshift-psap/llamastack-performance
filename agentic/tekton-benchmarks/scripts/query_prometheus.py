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
import re
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


def compute_step(duration_seconds):
    """Choose query step size based on test duration.
    Short tests keep fine granularity; long tests use coarser steps
    to stay within thanos-querier response limits (~11,000 points max)."""
    if duration_seconds <= 3600:       # ≤1h  → 15s step (~240 points)
        return "15s"
    elif duration_seconds <= 14400:    # ≤4h  → 30s step (~480-1800 points)
        return "30s"
    elif duration_seconds <= 86400:    # ≤24h → 60s step (~1440-… points)
        return "60s"
    else:                              # >24h → 300s step (~288-720 points/day)
        return "300s"


def compute_timeout(duration_seconds):
    """Scale HTTP timeout with test duration. Short tests use 30s,
    long tests get up to 180s to allow thanos-querier time to process."""
    if duration_seconds <= 3600:
        return 30
    elif duration_seconds <= 86400:
        return 90
    else:
        return 180


def prom_query_range(url, query, token, start, end, step="15s", timeout=30):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    params = urllib.parse.urlencode({"query": query, "start": int(start), "end": int(end), "step": step})
    req = urllib.request.Request(f"{url}/api/v1/query_range?{params}",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=timeout)
        data = json.loads(resp.read())
        if data.get("status") != "success":
            print(f"    WARNING: query status={data.get('status')}: {data.get('error', '')}")
        return data.get("data", {}).get("result", [])
    except Exception as e:
        print(f"    WARNING: query failed ({e}), retrying with larger step...")
        fallback_step_s = int(step.rstrip("s")) * 4
        fallback_params = urllib.parse.urlencode({
            "query": query, "start": int(start), "end": int(end),
            "step": f"{fallback_step_s}s"
        })
        req2 = urllib.request.Request(f"{url}/api/v1/query_range?{fallback_params}",
                                      headers={"Authorization": f"Bearer {token}"})
        try:
            resp2 = urllib.request.urlopen(req2, context=ctx, timeout=timeout * 2)
            data2 = json.loads(resp2.read())
            print(f"    Retry succeeded with step={fallback_step_s}s")
            return data2.get("data", {}).get("result", [])
        except Exception as e2:
            print(f"    ERROR: retry also failed ({e2})")
            return []


def extract_values(results, test_start=0):
    """Extract (step_seconds, value) pairs from range query results.
    step_seconds is seconds since test_start. Points before test_start are excluded."""
    all_points = []
    for series in results:
        for epoch, val in series.get("values", []):
            try:
                v = float(val)
                t = float(epoch)
                if v != float("inf") and v != float("nan") and t >= test_start:
                    all_points.append((int(t - test_start), v))
            except (ValueError, TypeError):
                pass
    return all_points


def extract_labeled_series(results, label_key, test_start=0):
    """Extract per-label time-series from range query results.
    step_seconds is seconds since test_start. Points before test_start are excluded."""
    series_dict = {}
    for series in results:
        label = series.get("metric", {}).get(label_key, "unknown")
        points = []
        for epoch, val in series.get("values", []):
            try:
                v = float(val)
                t = float(epoch)
                if v != float("inf") and v != float("nan") and t >= test_start:
                    points.append((int(t - test_start), v))
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

    warmup_file = d / "warmup_seconds"
    warmup = 0
    if warmup_file.exists():
        try:
            warmup = int(warmup_file.read_text().strip())
        except ValueError:
            pass
    query_start = start - warmup if warmup > 0 else start - 60
    query_range = end - query_start
    step = compute_step(query_range)
    timeout = compute_timeout(query_range)
    is_long_test = duration > 43200  # >12h
    print(f"Test window: {duration:.0f}s ({duration/3600:.1f}h), warmup: {warmup}s, query range: {query_range:.0f}s")
    print(f"Auto-tuned: step={step}, timeout={timeout}s")
    if is_long_test:
        print(f"Long test detected: will query rate metrics with both [1m] and [5m] windows")

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
        """Query Prometheus and store results.

        MLflow groups metrics into tabs by everything before the last '/'.
        To keep all metrics of a category in ONE tab, we use:
          - tab/metric_name          (for simple metrics)
          - tab/metric_name_label    (for labeled series, underscore-joined)
        This way 'gpu/utilization_pct_0' and 'gpu/power_w_1' share the 'gpu' tab.
        """
        r = prom_query_range(url, query, token, query_start, end, step=step, timeout=timeout)
        if is_labeled and label_key:
            # name is like "gpu/utilization_pct" → tab="gpu", suffix="utilization_pct"
            parts = name.split("/", 1)
            tab = parts[0]
            suffix = parts[1] if len(parts) > 1 else ""
            labeled = extract_labeled_series(r, label_key, test_start=start)
            for label, points in labeled.items():
                clean_label = re.sub(r'[^a-zA-Z0-9_\-.]', '_', label.strip("/"))
                # Flatten: gpu/utilization_pct_0  (not gpu/utilization_pct/0)
                ts_key = safe_name(f"{tab}/{suffix}_{clean_label}")
                ts[ts_key] = [{"step": s, "value": round(v, 6)} for s, v in points]
                agg[safe_name(f"{tab}/{suffix}_{clean_label}_avg")] = round(avg_val(points), 6)
            total_points = [p for pts in labeled.values() for p in pts]
            if total_points:
                agg[f"{tab}/{suffix}_avg"] = round(avg_val(total_points), 6)
                agg[f"{tab}/{suffix}_max"] = round(max_val(total_points), 6)
        else:
            points = extract_values(r, test_start=start)
            if points:
                ts[name] = [{"step": s, "value": round(v, 6)} for s, v in points]
                agg[f"{name}_avg"] = round(avg_val(points), 6)
                agg[f"{name}_max"] = round(max_val(points), 6)

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

    # For long tests, re-query vLLM rate metrics with [5m] window
    if is_long_test:
        print("Querying vLLM rate metrics with [5m] window (full coverage)...")
        query_and_store("vllm/prompt_throughput_tps_5m",
            f'sum(rate(vllm:prompt_tokens_total{{namespace="{ns}"}}[5m]))')
        query_and_store("vllm/generation_throughput_tps_5m",
            f'sum(rate(vllm:generation_tokens_total{{namespace="{ns}"}}[5m]))')
        query_and_store("vllm/ttft_p50_s_5m",
            f'histogram_quantile(0.50, sum(rate(vllm:time_to_first_token_seconds_bucket{{namespace="{ns}"}}[5m])) by (le))')
        query_and_store("vllm/e2e_latency_p50_s_5m",
            f'histogram_quantile(0.50, sum(rate(vllm:e2e_request_latency_seconds_bucket{{namespace="{ns}"}}[5m])) by (le))')

    # --- GPU/DCGM Metrics (per-GPU series) ---
    print("Querying GPU metrics...")
    query_and_store("gpu/utilization_pct", "DCGM_FI_DEV_GPU_UTIL",
        is_labeled=True, label_key="gpu")
    query_and_store("gpu/memory_used_mib", "DCGM_FI_DEV_FB_USED",
        is_labeled=True, label_key="gpu")
    query_and_store("gpu/temperature_c", "DCGM_FI_DEV_GPU_TEMP",
        is_labeled=True, label_key="gpu")
    query_and_store("gpu/power_w", "DCGM_FI_DEV_POWER_USAGE",
        is_labeled=True, label_key="gpu")

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
    query_and_store("pg/database_size_bytes",
        f'pg_database_size_bytes{{namespace="{ns}", datname="llamastack"}}')
    query_and_store("pg/rows_fetched_per_sec",
        f'rate(pg_stat_database_tup_fetched{{namespace="{ns}", datname="llamastack"}}[1m])')
    query_and_store("pg/rows_returned_per_sec",
        f'rate(pg_stat_database_tup_returned{{namespace="{ns}", datname="llamastack"}}[1m])')
    query_and_store("pg/blk_read_time_ms_per_sec",
        f'rate(pg_stat_database_blk_read_time{{namespace="{ns}", datname="llamastack"}}[1m])')
    query_and_store("pg/blk_write_time_ms_per_sec",
        f'rate(pg_stat_database_blk_write_time{{namespace="{ns}", datname="llamastack"}}[1m])')
    query_and_store("pg/temp_bytes",
        f'pg_stat_database_temp_bytes{{namespace="{ns}", datname="llamastack"}}')
    query_and_store("pg/locks",
        f'pg_locks_count{{namespace="{ns}", datname="llamastack"}}',
        is_labeled=True, label_key="mode")
    query_and_store("pg/seq_scan_per_sec",
        f'sum(rate(pg_stat_user_tables_seq_scan{{namespace="{ns}"}}[1m]))')
    query_and_store("pg/idx_scan_per_sec",
        f'sum(rate(pg_stat_user_tables_idx_scan{{namespace="{ns}"}}[1m]))')
    query_and_store("pg/inserts_by_table",
        f'pg_stat_user_tables_n_tup_ins{{namespace="{ns}"}}',
        is_labeled=True, label_key="relname")
    query_and_store("pg/idx_scan_by_table",
        f'pg_stat_user_tables_idx_scan{{namespace="{ns}"}}',
        is_labeled=True, label_key="relname")
    query_and_store("pg/seq_scan_by_table",
        f'pg_stat_user_tables_seq_scan{{namespace="{ns}"}}',
        is_labeled=True, label_key="relname")
    query_and_store("pg/connections_by_app",
        f'pg_stat_activity_count{{namespace="{ns}"}}',
        is_labeled=True, label_key="application_name")
    query_and_store("pg/max_connections",
        f'pg_settings_max_connections{{namespace="{ns}"}}')

    # --- PostgreSQL Table Storage & Vacuum (for AC: storage growth per table) ---
    query_and_store("pg/live_rows_by_table",
        f'pg_stat_user_tables_n_live_tup{{namespace="{ns}"}}',
        is_labeled=True, label_key="relname")
    query_and_store("pg/dead_rows_by_table",
        f'pg_stat_user_tables_n_dead_tup{{namespace="{ns}"}}',
        is_labeled=True, label_key="relname")
    query_and_store("pg/autovacuum_count_by_table",
        f'pg_stat_user_tables_autovacuum_count{{namespace="{ns}"}}',
        is_labeled=True, label_key="relname")
    query_and_store("pg/table_size_bytes",
        f'pg_stat_user_tables_size_bytes{{namespace="{ns}"}}',
        is_labeled=True, label_key="relname")
    query_and_store("pg/table_total_bytes",
        f'pg_table_sizes_total_bytes{{namespace="{ns}"}}',
        is_labeled=True, label_key="relname")
    query_and_store("pg/table_data_bytes",
        f'pg_table_sizes_table_bytes{{namespace="{ns}"}}',
        is_labeled=True, label_key="relname")
    query_and_store("pg/index_bytes",
        f'pg_table_sizes_index_bytes{{namespace="{ns}"}}',
        is_labeled=True, label_key="relname")

    # --- PostgreSQL Checkpointer (PG 17+, requires stat_checkpointer collector) ---
    query_and_store("pg/checkpoints_timed",
        f'pg_stat_checkpointer_num_timed_total{{namespace="{ns}"}}')
    query_and_store("pg/checkpoints_requested",
        f'pg_stat_checkpointer_num_requested_total{{namespace="{ns}"}}')
    query_and_store("pg/checkpoint_write_time_ms",
        f'pg_stat_checkpointer_write_time_total{{namespace="{ns}"}}')
    query_and_store("pg/checkpoint_sync_time_ms",
        f'pg_stat_checkpointer_sync_time_total{{namespace="{ns}"}}')
    query_and_store("pg/checkpoint_buffers_written",
        f'pg_stat_checkpointer_buffers_written_total{{namespace="{ns}"}}')

    # --- Background Writer (buffer management) ---
    query_and_store("pg/bgwriter_buffers_clean",
        f'pg_stat_bgwriter_buffers_clean_total{{namespace="{ns}"}}')
    query_and_store("pg/bgwriter_buffers_alloc",
        f'pg_stat_bgwriter_buffers_alloc_total{{namespace="{ns}"}}')
    query_and_store("pg/bgwriter_maxwritten_clean",
        f'pg_stat_bgwriter_maxwritten_clean_total{{namespace="{ns}"}}')

    # For long tests (>12h), re-query rate metrics with [5m] window for full coverage
    # (user-workload Prometheus retains raw data for ~24h; [5m] works with compacted data)
    if is_long_test:
        print("Querying Postgres rate metrics with [5m] window (full coverage)...")
        query_and_store("pg/commits_per_sec_5m",
            f'rate(pg_stat_database_xact_commit{{namespace="{ns}", datname="llamastack"}}[5m])')
        query_and_store("pg/rollbacks_per_sec_5m",
            f'rate(pg_stat_database_xact_rollback{{namespace="{ns}", datname="llamastack"}}[5m])')
        query_and_store("pg/inserts_per_sec_5m",
            f'rate(pg_stat_database_tup_inserted{{namespace="{ns}", datname="llamastack"}}[5m])')
        query_and_store("pg/deadlocks_per_sec_5m",
            f'rate(pg_stat_database_deadlocks{{namespace="{ns}", datname="llamastack"}}[5m])')
        query_and_store("pg/rows_fetched_per_sec_5m",
            f'rate(pg_stat_database_tup_fetched{{namespace="{ns}", datname="llamastack"}}[5m])')
        query_and_store("pg/rows_returned_per_sec_5m",
            f'rate(pg_stat_database_tup_returned{{namespace="{ns}", datname="llamastack"}}[5m])')
        query_and_store("pg/blk_read_time_ms_per_sec_5m",
            f'rate(pg_stat_database_blk_read_time{{namespace="{ns}", datname="llamastack"}}[5m])')
        query_and_store("pg/blk_write_time_ms_per_sec_5m",
            f'rate(pg_stat_database_blk_write_time{{namespace="{ns}", datname="llamastack"}}[5m])')
        query_and_store("pg/seq_scan_per_sec_5m",
            f'sum(rate(pg_stat_user_tables_seq_scan{{namespace="{ns}"}}[5m]))')
        query_and_store("pg/idx_scan_per_sec_5m",
            f'sum(rate(pg_stat_user_tables_idx_scan{{namespace="{ns}"}}[5m]))')
        query_and_store("pg/checkpoints_timed_rate_5m",
            f'rate(pg_stat_checkpointer_num_timed_total{{namespace="{ns}"}}[5m])')
        query_and_store("pg/checkpoint_write_time_rate_5m",
            f'rate(pg_stat_checkpointer_write_time_total{{namespace="{ns}"}}[5m])')

    # --- Per-Pod Network I/O (namespace-scoped) ---
    print("Querying per-pod network metrics...")
    query_and_store("pod_net/rx_bytes_per_sec",
        f'sum(rate(container_network_receive_bytes_total{{namespace="{ns}"}}[5m])) by (pod)',
        is_labeled=True, label_key="pod")
    query_and_store("pod_net/tx_bytes_per_sec",
        f'sum(rate(container_network_transmit_bytes_total{{namespace="{ns}"}}[5m])) by (pod)',
        is_labeled=True, label_key="pod")
    query_and_store("pod_net/rx_packets_per_sec",
        f'sum(rate(container_network_receive_packets_total{{namespace="{ns}"}}[5m])) by (pod)',
        is_labeled=True, label_key="pod")
    query_and_store("pod_net/tx_packets_per_sec",
        f'sum(rate(container_network_transmit_packets_total{{namespace="{ns}"}}[5m])) by (pod)',
        is_labeled=True, label_key="pod")

    # --- Per-Pod Filesystem I/O (namespace-scoped) ---
    print("Querying per-pod filesystem I/O...")
    query_and_store("pod_fs/write_bytes_per_sec",
        f'sum(rate(container_fs_writes_bytes_total{{namespace="{ns}", container!="", container!="POD"}}[5m])) by (pod)',
        is_labeled=True, label_key="pod")
    query_and_store("pod_fs/read_bytes_per_sec",
        f'sum(rate(container_fs_reads_bytes_total{{namespace="{ns}", container!="", container!="POD"}}[5m])) by (pod)',
        is_labeled=True, label_key="pod")

    # --- CPU Throttling (namespace-scoped) ---
    print("Querying CPU throttling metrics...")
    query_and_store("pod_cpu/throttled_pct",
        f'sum(rate(container_cpu_cfs_throttled_periods_total{{namespace="{ns}", container!="", container!="POD"}}[5m])) by (pod) / sum(rate(container_cpu_cfs_periods_total{{namespace="{ns}", container!="", container!="POD"}}[5m])) by (pod) * 100',
        is_labeled=True, label_key="pod")

    # --- Per-Node CPU/Memory (cluster-wide, same as Grafana "Cluster CPU Usage") ---
    print("Querying per-node CPU/memory...")
    query_and_store("node_cpu/usage_cores",
        'sum(rate(node_cpu_seconds_total{mode!="idle"}[5m])) by (instance)',
        is_labeled=True, label_key="instance")
    query_and_store("node_cpu/user_cores",
        'sum(rate(node_cpu_seconds_total{mode="user"}[5m])) by (instance)',
        is_labeled=True, label_key="instance")
    query_and_store("node_cpu/system_cores",
        'sum(rate(node_cpu_seconds_total{mode="system"}[5m])) by (instance)',
        is_labeled=True, label_key="instance")
    query_and_store("node_cpu/iowait_cores",
        'sum(rate(node_cpu_seconds_total{mode="iowait"}[5m])) by (instance)',
        is_labeled=True, label_key="instance")
    query_and_store("node_memory/usage_gib",
        '(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / 1024 / 1024 / 1024',
        is_labeled=True, label_key="instance")

    # --- Per-Node Network I/O (cluster-wide) ---
    print("Querying per-node network metrics...")
    query_and_store("node_net/rx_bytes_per_sec",
        'rate(node_network_receive_bytes_total{device!~"lo|veth.*|br.*|ovs.*|tun.*"}[5m])',
        is_labeled=True, label_key="instance")
    query_and_store("node_net/tx_bytes_per_sec",
        'rate(node_network_transmit_bytes_total{device!~"lo|veth.*|br.*|ovs.*|tun.*"}[5m])',
        is_labeled=True, label_key="instance")

    # --- Per-Node Disk I/O (cluster-wide) ---
    print("Querying per-node disk metrics...")
    query_and_store("node_disk/read_bytes_per_sec",
        'sum(rate(node_disk_read_bytes_total[5m])) by (instance)',
        is_labeled=True, label_key="instance")
    query_and_store("node_disk/write_bytes_per_sec",
        'sum(rate(node_disk_written_bytes_total[5m])) by (instance)',
        is_labeled=True, label_key="instance")
    query_and_store("node_disk/io_time_seconds_per_sec",
        'sum(rate(node_disk_io_time_seconds_total[5m])) by (instance)',
        is_labeled=True, label_key="instance")

    # --- PVC Storage (cluster-wide) ---
    print("Querying PVC storage metrics...")
    query_and_store("node_storage/pvc_used_gib",
        f'kubelet_volume_stats_used_bytes{{namespace="{ns}"}} / 1024 / 1024 / 1024',
        is_labeled=True, label_key="persistentvolumeclaim")
    query_and_store("node_storage/pvc_capacity_gib",
        f'kubelet_volume_stats_capacity_bytes{{namespace="{ns}"}} / 1024 / 1024 / 1024',
        is_labeled=True, label_key="persistentvolumeclaim")
    query_and_store("node_storage/pvc_inodes_used",
        f'kubelet_volume_stats_inodes_used{{namespace="{ns}"}}',
        is_labeled=True, label_key="persistentvolumeclaim")

    # --- Endpoint Readiness (when pods joined the Service) ---
    print("Querying endpoint readiness...")
    query_and_store("endpoint/ready",
        f'kube_endpoint_address_available{{namespace="{ns}"}}',
        is_labeled=True, label_key="pod")
    query_and_store("endpoint/not_ready",
        f'kube_endpoint_address_not_ready{{namespace="{ns}"}}',
        is_labeled=True, label_key="pod")

    # --- Pod Lifecycle (scale-down investigation) ---
    print("Querying pod lifecycle metrics...")
    query_and_store("pod_lifecycle/phase",
        f'kube_pod_status_phase{{namespace="{ns}", pod=~".*llamastack.*"}}',
        is_labeled=True, label_key="pod")
    query_and_store("pod_lifecycle/ready",
        f'kube_pod_status_ready{{namespace="{ns}", pod=~".*llamastack.*", condition="true"}}',
        is_labeled=True, label_key="pod")
    query_and_store("pod_lifecycle/restarts",
        f'kube_pod_container_status_restarts_total{{namespace="{ns}", pod=~".*llamastack.*"}}',
        is_labeled=True, label_key="pod")
    query_and_store("pod_lifecycle/terminating",
        f'kube_pod_deletion_timestamp{{namespace="{ns}", pod=~".*llamastack.*"}}',
        is_labeled=True, label_key="pod")
    query_and_store("pod_lifecycle/container_terminated",
        f'kube_pod_container_status_terminated_reason{{namespace="{ns}", pod=~".*llamastack.*"}}',
        is_labeled=True, label_key="reason")
    query_and_store("pod_lifecycle/container_waiting",
        f'kube_pod_container_status_waiting_reason{{namespace="{ns}", pod=~".*llamastack.*"}}',
        is_labeled=True, label_key="reason")

    # --- Per-Pod CPU/Memory (namespace-scoped) ---
    print("Querying per-pod CPU/memory...")
    query_and_store("pod_cpu/cpu_cores",
        f'sum(rate(container_cpu_usage_seconds_total{{namespace="{ns}", container!="", container!="POD"}}[5m])) by (pod)',
        is_labeled=True, label_key="pod")
    query_and_store("pod_cpu/cpu_cores_llamastack",
        f'rate(container_cpu_usage_seconds_total{{namespace="{ns}", container="llama-stack"}}[5m])',
        is_labeled=True, label_key="pod")
    query_and_store("pod_cpu/context_switches_voluntary",
        f'rate(container_context_switches_total{{namespace="{ns}", container!="", container!="POD"}}[5m])',
        is_labeled=True, label_key="pod")
    query_and_store("pod_memory/memory_gib",
        f'sum(container_memory_working_set_bytes{{namespace="{ns}", container!="", container!="POD"}}) by (pod) / 1024 / 1024 / 1024',
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
