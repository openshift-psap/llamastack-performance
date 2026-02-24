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
    parser.add_argument("--search-window-buffer", type=int, default=120)
    parser.add_argument("--max-traces", type=int, default=1000)
    return parser.parse_args()


def read_test_timestamps(results_dir):
    start_file = results_dir / "test_start_epoch"
    end_file = results_dir / "test_end_epoch"
    if not start_file.exists() or not end_file.exists():
        print("WARNING: Test timestamp files not found")
        return None, None
    start = int(start_file.read_text().strip())
    end = int(end_file.read_text().strip())
    print(f"Test window: {datetime.fromtimestamp(start)} -> {datetime.fromtimestamp(end)} ({end - start}s)")
    return start, end


def search_traces(endpoint, service, start, end, buffer, limit):
    url = f"{endpoint}/api/search"
    params = {"tags": f"service.name={service}", "start": start - buffer, "end": end + buffer, "limit": limit}
    print(f"Searching Tempo: {url} (service={service})")
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        traces = resp.json().get("traces", [])
        print(f"  Found {len(traces)} traces")
        return traces
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Cannot connect to Tempo at {endpoint}")
        return []
    except Exception as e:
        print(f"ERROR searching Tempo: {e}")
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


def analyze_spans(spans):
    result = {"tool_call_count": 0, "request_duration_ms": 0,
              "inference_durations_ms": [], "list_mcp_tools_durations_ms": [],
              "invoke_mcp_tool_durations_ms": []}
    for span in spans:
        name = span.get("name", "")
        start_ns = int(span.get("startTimeUnixNano", "0"))
        end_ns = int(span.get("endTimeUnixNano", "0"))
        dur_ms = (end_ns - start_ns) / 1_000_000 if start_ns and end_ns else 0

        if name in ("/v1/responses", "create_response") and dur_ms > 0:
            result["request_duration_ms"] = dur_ms
        if any(k in name for k in ("openai_chat_completion", "InferenceRouter", "chat_completion")) and dur_ms > 0:
            result["inference_durations_ms"].append(dur_ms)
        if any(k in name for k in ("list_mcp_tools", "list_tools")) and dur_ms > 0:
            result["list_mcp_tools_durations_ms"].append(dur_ms)
        if any(k in name for k in ("invoke_mcp_tool", "invoke_tool")):
            result["tool_call_count"] += 1
            if dur_ms > 0:
                result["invoke_mcp_tool_durations_ms"].append(dur_ms)
    return result


def safe_p95(values):
    if len(values) >= 20:
        return statistics.quantiles(values, n=20)[18]
    return max(values)


def compute_aggregates(per_request):
    if not per_request:
        return {}
    metrics = {}
    durations = [r["request_duration_ms"] for r in per_request if r["request_duration_ms"] > 0]
    tools = [r["tool_call_count"] for r in per_request if r["tool_call_count"] > 0]
    inference = [d for r in per_request for d in r["inference_durations_ms"]]
    list_tools = [d for r in per_request for d in r["list_mcp_tools_durations_ms"]]
    invoke = [d for r in per_request for d in r["invoke_mcp_tool_durations_ms"]]

    if durations:
        metrics["trace/avg_total_request_duration_ms"] = statistics.mean(durations)
        metrics["trace/p50_total_request_duration_ms"] = statistics.median(durations)
        metrics["trace/p95_total_request_duration_ms"] = safe_p95(durations)
        metrics["trace/min_total_request_duration_ms"] = min(durations)
        metrics["trace/max_total_request_duration_ms"] = max(durations)
    if tools:
        metrics["trace/avg_tool_calls_per_request"] = statistics.mean(tools)
        metrics["trace/total_tool_calls"] = sum(tools)
    if inference:
        metrics["trace/avg_inference_duration_ms"] = statistics.mean(inference)
        metrics["trace/p50_inference_duration_ms"] = statistics.median(inference)
        metrics["trace/p95_inference_duration_ms"] = safe_p95(inference)
    if list_tools:
        metrics["trace/avg_list_tools_duration_ms"] = statistics.mean(list_tools)
    if invoke:
        metrics["trace/avg_invoke_tool_duration_ms"] = statistics.mean(invoke)
        metrics["trace/p95_invoke_tool_duration_ms"] = safe_p95(invoke)
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

    print("Waiting 15s for trace pipeline flush...")
    time.sleep(15)

    summaries = search_traces(args.tempo_endpoint, args.service_name, start, end,
                              args.search_window_buffer, args.max_traces)
    if not summaries:
        print("No traces found")
        (results_dir / "trace_metrics.json").write_text(json.dumps(empty_output, indent=2))
        return

    print(f"Fetching and analyzing {len(summaries)} traces...")
    per_request = []
    raw_traces = []
    for i, summary in enumerate(summaries):
        tid = summary.get("traceID", "")
        if not tid:
            continue
        detail = fetch_trace_detail(args.tempo_endpoint, tid)
        if not detail:
            continue
        raw_traces.append({"traceID": tid, "summary": summary, "detail": detail})
        spans = extract_spans(detail)
        if spans:
            m = analyze_spans(spans)
            m["trace_id"] = tid
            m["step"] = i
            per_request.append(m)
        if (i + 1) % 25 == 0:
            print(f"  Processed {i + 1}/{len(summaries)} traces...")

    print(f"Analyzed {len(per_request)} traces with span data")

    if raw_traces:
        (results_dir / "traces_raw.json").write_text(json.dumps(raw_traces, indent=2))

    agg = compute_aggregates(per_request)
    for k, v in agg.items():
        print(f"  {k}: {v:.1f}")

    per_request_out = [{"step": r["step"], "trace_id": r["trace_id"],
                        "request_duration_ms": r["request_duration_ms"],
                        "tool_calls": r["tool_call_count"]} for r in per_request]

    output = {"trace_count": len(per_request), "aggregate_metrics": agg,
              "per_request_metrics": per_request_out}
    (results_dir / "trace_metrics.json").write_text(json.dumps(output, indent=2))
    print(f"Wrote trace_metrics.json ({len(agg)} aggregate, {len(per_request_out)} per-request)")


if __name__ == "__main__":
    main()
