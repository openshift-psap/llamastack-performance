# LlamaStack Full Test Suite

This folder contains everything needed to run automated load tests against LlamaStack with OpenTelemetry trace collection and analysis.

## Overview

The test suite:
1. **Cleans up** existing resources (LlamaStack, OTEL collector, Postgres)
2. **Deploys** fresh infrastructure (Postgres, OTEL collector, LlamaStack with patches)
3. **Applies tracing patches** for comprehensive telemetry
4. **Runs Locust load test** against the `/v1/responses` API
5. **Collects and analyzes** OpenTelemetry traces
6. **Saves results** to a timestamped folder

## Prerequisites

- OpenShift cluster access with `oc` CLI configured
- vLLM inference server running (e.g., `llama-32-3b-instruct-predictor`)
- Namespace with appropriate permissions

## Quick Start

```bash
# Run with defaults (128 users, 60s)
./run_full_test.sh --namespace <your-namespace>

# Run with custom parameters
./run_full_test.sh \
  --namespace my-project \
  --users 64 \
  --spawn-rate 64 \
  --run-time 120
```

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--namespace`, `-n` | `avis-project` | Target OpenShift namespace |
| `--users`, `-u` | `128` | Number of concurrent Locust users |
| `--spawn-rate`, `-s` | `128` | User spawn rate per second |
| `--run-time`, `-t` | `60` | Test duration in seconds |
| `--wait-after`, `-w` | `30` | Wait time after test for trace collection |

## Output

Results are saved to: `./locustTest_<namespace>_u<users>_t<runtime>_<timestamp>/`

### Files Generated

| File | Description |
|------|-------------|
| `traces_*.json` | Full Jaeger-compatible trace data |
| `traces_*_analysis.json` | Aggregated statistics and metrics |
| `locust_logs.txt` | Locust test execution logs |
| `trace_collector_logs.txt` | Trace collection process logs |
| `vllm_logs.txt` | vLLM inference server logs (test window only) |
| `locust-results_*.csv` | Locust CSV exports |
| `mcp_metrics.csv` | MCP tool call metrics |
| `test_parameters.json` | Test configuration used |

### Analysis Report Structure

```json
{
  "summary": {
    "total_traces": 190,
    "total_spans": 3572,
    "avg_spans_per_trace": 18.8
  },
  "operation_stats": {
    "/v1/responses": { "count": 190, "avg_ms": 16159, "p95_ms": 34473 },
    "invoke_mcp_tool": { ... },
    "SqlStore.insert": { ... }
  },
  "trace_stats": { ... },
  "llm_analysis": { ... }
}
```

## Folder Structure

```
Full-test-avis/
├── run_full_test.sh           # Main test runner script
├── README.md                  # This file
├── patches/
│   ├── tracing-patch-configmap.yaml        # LlamaStack tracing fixes
│   └── database-tracing-configmap.yaml     # Database operation tracing
├── otel-deployment/
│   ├── otel-collector-deployment.yaml      # OTEL collector config
│   └── llamastack-distribution.yaml        # LlamaStack CRD template
├── test-job/
│   └── configmap-locust-test.yaml          # Locust test configuration
└── trace-collector/
    └── configmap-trace-collector.yaml      # Trace analysis script
```

## Patches Included

### 1. Tracing Patch (`tracing-patch-configmap.yaml`)
Fixes:
- Class-level spans list bug (trace mixing between concurrent requests)
- `start_trace` overwriting existing trace context (trace fragmentation)

### 2. Database Tracing Patch (`database-tracing-configmap.yaml`)
- Adds tracing for `SqlStore` operations (insert, fetch, update, delete)
- Adds tracing for `KVStore` operations
- Uses LlamaStack's native tracing context (not OpenTelemetry directly)
- Only creates spans when parent span exists (no orphaned traces)

## Customization

### Changing the vLLM Model

Edit `otel-deployment/llamastack-distribution.yaml`:
```yaml
env:
  - name: VLLM_URL
    value: "http://<your-model>-predictor.<namespace>.svc.cluster.local:80/v1"
  - name: INFERENCE_MODEL
    value: "<your-model-name>"
```

### Changing the Locust Test

Edit `test-job/configmap-locust-test.yaml` to modify:
- The prompt/input text
- MCP tools configuration
- Model to use

## Troubleshooting

### No traces collected
- Check OTEL collector logs: `oc logs -l app=otel-collector`
- Verify LlamaStack has OTEL endpoint configured
- Ensure patches are mounted correctly: `oc describe pod -l app.kubernetes.io/name=llamastack-*`

### Trace count doesn't match Locust requests
- Time filtering uses Locust's exact shutdown time
- Traces completing after shutdown are excluded
- Check `trace_collector_logs.txt` for timing details

### Patches not applied
- Verify ConfigMaps exist: `oc get cm | grep llamastack`
- Check volume mounts on LlamaStack deployment
- Look for "PATCHED" in the mounted files


