"""
Trace Analyzer — queries Tempo for traces and computes derived metrics.

Reads test_start_epoch / test_end_epoch from the results directory,
queries Tempo for traces in that window, computes latency breakdowns
and tool call counts, and writes trace_metrics.json for the MLflow logger.

Usage:
    python trace_analyzer.py \
      --results-dir /workspace/results \
      --tempo-endpoint http://tempo:3200 \
      --service-name llamastack
"""
import json
import argparse
import statistics
import time
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call(["pip", "install", "--quiet", "requests"])
    import requests


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--tempo-endpoint", required=True)
    parser.add_argument("--service-name", default="llamastack")
    parser.add_argument("--namespace", required=True, help="Kubernetes namespace to filter traces")
    parser.add_argument("--search-window-buffer", type=int, default=120)
    parser.add_argument("--max-traces", type=int, default=1000)
    return parser.parse_args()


def _read_locust_request_count(results_dir):
    """Read total request count from Locust stats CSV."""
    stats_file = results_dir / "locust-results_stats.csv"
    if not stats_file.exists():
        return None
    try:
        import csv
        with open(stats_file) as f:
            for row in csv.DictReader(f):
                if row.get("Name", "").strip() == "Aggregated":
                    count = int(float(row.get("Request Count", 0)))
                    print(f"Locust reported {count} total requests")
                    return count
    except Exception:
        pass
    return None


def read_test_timestamps(results_dir):
    precise_start = results_dir / "test_start_epoch_precise"
    precise_end = results_dir / "test_end_epoch_precise"
    start_file = results_dir / "test_start_epoch"
    end_file = results_dir / "test_end_epoch"

    if precise_start.exists() and precise_end.exists():
        start_f = float(precise_start.read_text().strip())
        end_f = float(precise_end.read_text().strip())
        print(f"Test window (precise): {datetime.fromtimestamp(start_f)} -> {datetime.fromtimestamp(end_f)} ({end_f - start_f:.1f}s)")
        return start_f, end_f

    if not start_file.exists() or not end_file.exists():
        print("WARNING: Test timestamp files not found")
        return None, None
    start = int(start_file.read_text().strip())
    end = int(end_file.read_text().strip())
    print(f"Test window (second precision): {datetime.fromtimestamp(start)} -> {datetime.fromtimestamp(end)} ({end - start}s)")
    return float(start), float(end)


def search_traces(endpoint, service, namespace, start, end, buffer, limit):
    url = f"{endpoint}/api/search"

    # Search for /v1/responses traces — supports both old ("/v1/responses") and new ("POST /v1/responses") span names
    for span_name in ["POST /v1/responses", "/v1/responses"]:
        q = f'{{name="{span_name}" && resource.service.name="{service}" && resource.k8s.namespace.name="{namespace}"}}'
        params = {"q": q, "start": int(start - buffer), "end": int(end + buffer), "limit": limit}
        print(f"Searching Tempo via TraceQL (name={span_name}, service={service}, namespace={namespace})")
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            traces = resp.json().get("traces", [])
            if traces:
                print(f"  Found {len(traces)} traces")
                return traces
        except Exception:
            pass

    # Fallback: service-only, no name filter
    print(f"  Retrying with service-only filter...")
    for span_name in ["POST /v1/responses", "/v1/responses"]:
        q_fallback = f'{{name="{span_name}" && resource.service.name="{service}"}}'
        params_fallback = {"q": q_fallback, "start": int(start - 10), "end": int(end + buffer), "limit": limit}
        try:
            resp = requests.get(url, params=params_fallback, timeout=30)
            resp.raise_for_status()
            traces = resp.json().get("traces", [])
            if traces:
                print(f"  Found {len(traces)} traces (service-only)")
                return traces
        except Exception:
            pass

    print(f"  Found 0 traces")
    return []


def fetch_trace_detail(endpoint, trace_id):
    try:
        resp = requests.get(f"{endpoint}/api/traces/{trace_id}", timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  WARNING: Failed to fetch trace {trace_id}: {e}")
        return None


def extract_spans(trace_data):
    spans = []
    for batch in trace_data.get("batches", []):
        for scope_spans in batch.get("scopeSpans", []):
            spans.extend(scope_spans.get("spans", []))
    return spans


def _get_span_attr(span, key):
    for a in span.get("attributes", []):
        if a.get("key") == key:
            val = a.get("value", {})
            if "intValue" in val:
                return int(val["intValue"])
            if "stringValue" in val:
                return val["stringValue"]
            if "doubleValue" in val:
                return float(val["doubleValue"])
            if val:
                return list(val.values())[0]
    return None


def analyze_spans(spans):
    result = {
        "tool_call_count": 0,
        "request_duration_ms": 0,
        "inference_durations_ms": [],
        "list_mcp_tools_durations_ms": [],
        "invoke_mcp_tool_durations_ms": [],
        "db_durations_ms": [],
        "db_connect_durations_ms": [],
        "db_insert_durations_ms": [],
        "db_begin_durations_ms": [],
        "db_commit_durations_ms": [],
        "db_rollback_durations_ms": [],
        "db_other_durations_ms": [],
        "mcp_http_durations_ms": [],
        "input_tokens": [],
        "output_tokens": [],
    }

    db_names = {"INSERT", "connect", "BEGIN;", "COMMIT;", "ROLLBACK;", ";"}

    # Pass 1: identify DB span IDs to fix double-counting (children nested inside connect)
    db_span_ids = set()
    for span in spans:
        sid = span.get("spanId", "")
        name = span.get("name", "")
        first_word = name.split(" ")[0] if name else ""
        if first_word in db_names or name in db_names or _get_span_attr(span, "db.system") == "postgresql":
            db_span_ids.add(sid)

    # Pass 2: classify all spans
    for span in spans:
        name = span.get("name", "")
        sid = span.get("spanId", "")
        parent_id = span.get("parentSpanId", "")
        start_ns = int(span.get("startTimeUnixNano", "0"))
        end_ns = int(span.get("endTimeUnixNano", "0"))
        dur_ms = (end_ns - start_ns) / 1_000_000 if start_ns and end_ns else 0
        http_url = _get_span_attr(span, "http.url") or ""

        if name in ("/v1/responses", "POST /v1/responses", "create_response") and dur_ms > 0:
            result["request_duration_ms"] = dur_ms

        elif any(k in name for k in ("openai_chat_completion", "InferenceRouter", "chat_completion", "chat ")) and dur_ms > 0:
            result["inference_durations_ms"].append(dur_ms)
            inp = _get_span_attr(span, "gen_ai.usage.input_tokens")
            out = _get_span_attr(span, "gen_ai.usage.output_tokens")
            if inp is not None:
                result["input_tokens"].append(int(inp))
            if out is not None:
                result["output_tokens"].append(int(out))

        elif any(k in name for k in ("list_mcp_tools", "list_tools")) and dur_ms > 0:
            result["list_mcp_tools_durations_ms"].append(dur_ms)

        elif any(k in name for k in ("invoke_mcp_tool", "invoke_tool")):
            result["tool_call_count"] += 1
            if dur_ms > 0:
                result["invoke_mcp_tool_durations_ms"].append(dur_ms)

        elif sid in db_span_ids:
            if dur_ms > 0 and parent_id not in db_span_ids:
                result["db_durations_ms"].append(dur_ms)
            # Per-type DB metrics (all spans, including nested, for accurate counting)
            if dur_ms > 0:
                first_word = name.split(" ")[0] if name else ""
                if name == "connect":
                    result["db_connect_durations_ms"].append(dur_ms)
                elif first_word == "INSERT":
                    result["db_insert_durations_ms"].append(dur_ms)
                elif name == "BEGIN;":
                    result["db_begin_durations_ms"].append(dur_ms)
                elif name == "COMMIT;":
                    result["db_commit_durations_ms"].append(dur_ms)
                elif name == "ROLLBACK;":
                    result["db_rollback_durations_ms"].append(dur_ms)
                else:
                    result["db_other_durations_ms"].append(dur_ms)

        elif ("mcp" in http_url or "/sse" in http_url or "/messages/" in http_url) and dur_ms > 0:
            result["mcp_http_durations_ms"].append(dur_ms)

    total = result["request_duration_ms"]
    if total > 0:
        inference = sum(result["inference_durations_ms"])
        mcp = sum(result["list_mcp_tools_durations_ms"]) + sum(result["invoke_mcp_tool_durations_ms"])
        db = sum(result["db_durations_ms"])
        result["ls_overhead_ms"] = max(0, total - inference - mcp - db)
    else:
        result["ls_overhead_ms"] = 0

    return result


def safe_percentile(values, pct):
    if not values:
        return 0
    if len(values) < 2:
        return values[0]
    n = max(2, min(100, len(values)))
    q = statistics.quantiles(values, n=n)
    idx = min(int(pct / 100 * len(q)), len(q) - 1)
    return q[idx]


def _add_full_stats(metrics, prefix, values):
    if not values:
        return
    metrics[f"{prefix}/p50_ms"] = statistics.median(values)
    metrics[f"{prefix}/p95_ms"] = safe_percentile(values, 95)
    metrics[f"{prefix}/p99_ms"] = safe_percentile(values, 99)


def compute_aggregates(per_request):
    if not per_request:
        return {}
    metrics = {}

    durations = [r["request_duration_ms"] for r in per_request if r["request_duration_ms"] > 0]
    tools = [r["tool_call_count"] for r in per_request if r["tool_call_count"] > 0]
    inference = [sum(r["inference_durations_ms"]) for r in per_request if r["inference_durations_ms"]]
    list_tools = [sum(r["list_mcp_tools_durations_ms"]) for r in per_request if r["list_mcp_tools_durations_ms"]]
    invoke = [sum(r["invoke_mcp_tool_durations_ms"]) for r in per_request if r["invoke_mcp_tool_durations_ms"]]
    db = [sum(r["db_durations_ms"]) for r in per_request if r["db_durations_ms"]]
    inp_tokens = [sum(r["input_tokens"]) for r in per_request if r["input_tokens"]]
    out_tokens = [sum(r["output_tokens"]) for r in per_request if r["output_tokens"]]

    _add_full_stats(metrics, "trace/total_request", durations)
    _add_full_stats(metrics, "trace/inference", inference)
    _add_full_stats(metrics, "trace/list_mcp_tools", list_tools)
    _add_full_stats(metrics, "trace/invoke_mcp_tool", invoke)
    _add_full_stats(metrics, "trace/db", db)

    # Per-type DB metrics
    db_types = {
        "trace/db_connect": "db_connect_durations_ms",
        "trace/db_insert": "db_insert_durations_ms",
        "trace/db_begin": "db_begin_durations_ms",
        "trace/db_commit": "db_commit_durations_ms",
        "trace/db_rollback": "db_rollback_durations_ms",
        "trace/db_other": "db_other_durations_ms",
    }
    for prefix, key in db_types.items():
        vals = [sum(r[key]) for r in per_request if r[key]]
        if vals:
            _add_full_stats(metrics, prefix, vals)
            metrics[f"{prefix}/avg_count"] = statistics.mean([len(r[key]) for r in per_request])

    # LlamaStack overhead: total - inference - mcp - db
    ls_overhead = [r["ls_overhead_ms"] for r in per_request if r.get("ls_overhead_ms", 0) > 0]
    _add_full_stats(metrics, "trace/ls_overhead", ls_overhead)

    if tools:
        metrics["trace/avg_tool_calls_per_request"] = statistics.mean(tools)
        metrics["trace/total_tool_calls"] = sum(tools)

    if inp_tokens:
        metrics["trace/tokens/avg_input"] = statistics.mean(inp_tokens)
    if out_tokens:
        metrics["trace/tokens/avg_output"] = statistics.mean(out_tokens)
    if inp_tokens and out_tokens:
        metrics["trace/tokens/avg_total"] = statistics.mean([i + o for i, o in zip(inp_tokens, out_tokens)])

    return metrics


def main():
    args = parse_args()
    results_dir = Path(args.results_dir)
    empty_output = {"trace_count": 0, "aggregate_metrics": {}, "per_request_metrics": []}

    start, end = read_test_timestamps(results_dir)
    if start is None:
        print("Cannot determine test window, writing empty trace metrics")
        (results_dir / "trace_metrics.json").write_text(json.dumps(empty_output, indent=2))
        return

    expected_requests = _read_locust_request_count(results_dir)

    print("Waiting 15s for trace pipeline flush...")
    time.sleep(15)

    summaries = search_traces(args.tempo_endpoint, args.service_name, args.namespace,
                              start, end, args.search_window_buffer, args.max_traces)
    if not summaries:
        print("No traces found")
        (results_dir / "trace_metrics.json").write_text(json.dumps(empty_output, indent=2))
        return

    start_ns = int(start * 1_000_000_000)
    end_ns = int(end * 1_000_000_000)

    print(f"Fetching and analyzing {len(summaries)} traces...")
    per_request = []
    raw_traces = []
    skipped_time = 0
    skipped_root = 0
    for i, summary in enumerate(summaries):
        tid = summary.get("traceID", "")
        if not tid:
            continue
        detail = fetch_trace_detail(args.tempo_endpoint, tid)
        if not detail:
            continue
        spans = extract_spans(detail)
        if not spans:
            continue

        root_span = None
        for s in spans:
            if s.get("name", "") in ("POST /v1/responses", "/v1/responses", "create_response") and not s.get("parentSpanId"):
                root_span = s
                break
        if not root_span:
            skipped_root += 1
            continue

        root_start = int(root_span.get("startTimeUnixNano", "0"))
        root_end = int(root_span.get("endTimeUnixNano", "0"))
        if root_start < start_ns or root_end > end_ns:
            skipped_time += 1
            continue

        raw_traces.append({"traceID": tid, "summary": summary, "detail": detail})
        m = analyze_spans(spans)
        m["trace_id"] = tid
        m["step"] = len(per_request)
        m["_root_start_ns"] = root_start
        per_request.append(m)
        if (i + 1) % 25 == 0:
            print(f"  Processed {i + 1}/{len(summaries)} traces...")

    if skipped_root or skipped_time:
        print(f"  Skipped {skipped_root} non-responses traces, {skipped_time} outside test window")

    if expected_requests:
        diff = len(per_request) - expected_requests
        if diff != 0:
            print(f"  Note: {len(per_request)} traces vs {expected_requests} Locust requests (diff: {diff:+d})")

    print(f"Analyzed {len(per_request)} traces with span data")

    # Diagnostic: print all unique span names across traces to help identify missing/renamed spans
    all_span_names = set()
    for rt in raw_traces:
        detail = rt.get("detail", {})
        for batch in detail.get("batches", []):
            for scope_spans in batch.get("scopeSpans", []):
                for s in scope_spans.get("spans", []):
                    all_span_names.add(s.get("name", "<unnamed>"))
    if all_span_names:
        print(f"\nAll unique span names found across {len(raw_traces)} traces:")
        for sn in sorted(all_span_names):
            print(f"  - {sn}")
        print()

    if raw_traces:
        (results_dir / "traces_raw.json").write_text(json.dumps(raw_traces, indent=2))

    agg = compute_aggregates(per_request)
    for k, v in agg.items():
        print(f"  {k}: {v:.1f}")

    per_request_out = []
    for r in per_request:
        entry = {
            "step": r["step"],
            "trace_id": r["trace_id"],
            "request_duration_ms": r["request_duration_ms"],
            "tool_calls": r["tool_call_count"],
            "inference_duration_ms": sum(r["inference_durations_ms"]),
            "list_mcp_tools_ms": sum(r["list_mcp_tools_durations_ms"]),
            "invoke_mcp_tool_ms": sum(r["invoke_mcp_tool_durations_ms"]),
            "db_duration_ms": sum(r["db_durations_ms"]),
            "db_connect_ms": sum(r["db_connect_durations_ms"]),
            "db_connect_count": len(r["db_connect_durations_ms"]),
            "db_insert_ms": sum(r["db_insert_durations_ms"]),
            "db_insert_count": len(r["db_insert_durations_ms"]),
            "db_begin_count": len(r["db_begin_durations_ms"]),
            "db_commit_count": len(r["db_commit_durations_ms"]),
            "db_rollback_count": len(r["db_rollback_durations_ms"]),
            "mcp_http_duration_ms": sum(r["mcp_http_durations_ms"]),
            "ls_overhead_ms": r.get("ls_overhead_ms", 0),
            "input_tokens": sum(r["input_tokens"]),
            "output_tokens": sum(r["output_tokens"]),
        }
        per_request_out.append(entry)

    output = {"trace_count": len(per_request), "aggregate_metrics": agg,
              "per_request_metrics": per_request_out}
    (results_dir / "trace_metrics.json").write_text(json.dumps(output, indent=2))
    print(f"Wrote trace_metrics.json ({len(agg)} aggregate, {len(per_request_out)} per-request)")


if __name__ == "__main__":
    main()
