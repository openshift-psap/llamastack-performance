# Tekton OGX Benchmarks

Automated performance testing for OGX on OpenShift using Tekton pipelines, Locust load generation, and comprehensive metrics collection.

## Overview

This framework deploys the full OGX stack (or just the inference backend), executes load tests with configurable concurrency and duration, collects metrics from every layer (Locust, vLLM, GPU, PostgreSQL, OTel traces, HPA), and logs all results to MLflow and Grafana for analysis.

## Pipelines

| Pipeline | Purpose |
|----------|---------|
| `rhaiis-ogx-simple` | Full OGX stack with RHAIIS inference, PostgreSQL, OTel tracing. Tests the `/v1/responses` API (no MCP tools). |
| `rhaiis-ogx-simple-no-otel` | Same as above but with OpenTelemetry **disabled** (`OTEL_SDK_DISABLED=true`). Used for A/B comparison to measure OTel overhead. |
| `rhaiis-ogx-mcp-benchmark` | OGX with RHAIIS, PostgreSQL, OTel, and a **deterministic MCP server** for tool-calling workloads. Prompts use synthetic filler + "use tool_0" suffix for zero variance. |
| `rhaiis-ogx-mcp-benchmark-no-otel` | Same as MCP benchmark but with OpenTelemetry **disabled**. For measuring MCP overhead without tracing noise. |
| `rhaiis-direct` | Direct RHAIIS (vLLM) benchmarking via `/v1/chat/completions` — no OGX, no Postgres, no OTel. Measures raw inference performance. |
| `vllm-direct` | Direct vLLM benchmarking via `/v1/chat/completions` — no OGX, no Postgres, no OTel. Supports OCI modelcar or RHAIIS deployment modes. |
| `responses-simple` | OGX Responses API with community vLLM backend. |
| `responses-mcp` | OGX Responses API with MCP tool calling and community vLLM backend. |

### Pipeline Selection Guide

- **Raw inference baseline** → `rhaiis-direct` or `vllm-direct`
- **OGX overhead (no tools)** → `rhaiis-ogx-simple-no-otel`
- **Full production stack with tracing** → `rhaiis-ogx-simple`
- **OGX + MCP tool-calling overhead** → `rhaiis-ogx-mcp-benchmark-no-otel`
- **OGX + MCP with full observability** → `rhaiis-ogx-mcp-benchmark`
- **OTel overhead measurement** → Compare any pipeline vs its `-no-otel` variant
- **Community vLLM (smaller models)** → `responses-simple` or `responses-mcp`

## Getting Started

```bash
# Apply RBAC, tasks, and pipelines
oc apply -f rbac/
oc apply -f tasks/
oc apply -f pipelines/

# Run a benchmark (edit PipelineRun params as needed)
oc create -f pipelineruns/benchmark-rhaiis-ogx-mcp.yaml

# Watch progress
tkn pipelinerun logs -f -n tekton-ogx
```

See [`docs/cluster-setup-guide.md`](docs/cluster-setup-guide.md) for full cluster preparation instructions.

## Pipeline Execution Flow

When a PipelineRun is created, the following happens in order:

```
1. generate-configmap     Clone git repo → create ConfigMaps (locustfiles, scripts)
2. deploy-rhaiis/vllm     Deploy model serving (first run downloads weights; subsequent runs reuse PVC cache)
3. deploy-postgres        Deploy PostgreSQL with fresh database
4. deploy-mcp-server      Deploy MCP server (only for MCP pipelines)
5. deploy-tracing         Flush Tempo + deploy OTel Collector (only for OTel pipelines)
6. deploy-ogx      Deploy OGXDistribution + optional HPA
7. generate-prompt        Generate synthetic prompts using the model's tokenizer
   ─── WARMUP (default 300s) ─── Prometheus discovers targets, pods stabilize
8. run-locust             Execute load test with sidecars:
                            • Locust main container: load generation + results
                            • HPA sidecar: scrapes pod count, CPU, memory, HPA state every 1s
                            • Prometheus sidecar: scrapes PostgreSQL + vLLM metrics every 5s
   ─── HPA_POST_TEST_SECONDS ─── Captures scale-down behavior after load stops
9. push-to-prometheus     Query Tempo traces + Prometheus → push to Pushgateway (for Grafana)
   — OR —
   query-prometheus       Query Prometheus only (for direct vLLM pipelines without OTel)
10. log-mlflow            Analyze traces → batch-log everything to SageMaker MLflow
11. cleanup               Delete all deployed resources (unless SKIP_CLEANUP=true)
```

The pipeline uses a shared `results` workspace (PVC) that all tasks write to and the final logging tasks read from.

## Tasks

Reusable Tekton Tasks that pipelines compose:

| Task | Description |
|------|-------------|
| `generate-configmap` | Clones git repo, creates ConfigMaps for locustfiles and scripts |
| `deploy-rhaiis` | Deploys model via RHAIIS (HuggingFace download with PVC cache). First run downloads model (~62GB for Qwen3-VL-30B); subsequent runs reuse cached weights. |
| `deploy-vllm` | Deploys vLLM via KServe (ServingRuntime + InferenceService) using OCI modelcar |
| `deploy-postgres` | Deploys PostgreSQL (fresh DB each run) |
| `deploy-mcp-server` | Deploys the benchmark MCP server with configurable tool count and response token sizes |
| `deploy-tracing` | Flushes Tempo trace storage and deploys OpenTelemetry Collector |
| `deploy-ogx` | Deploys OGXDistribution via the RHOAI operator with optional HPA |
| `generate-prompt` | Generates synthetic prompts with exact token count using the deployed model's tokenizer |
| `run-locust` | Runs Locust load test with HPA metrics sidecar and Prometheus scraper sidecar |
| `push-to-prometheus` | Analyzes traces, queries Prometheus, pushes results to Pushgateway for Grafana |
| `query-prometheus` | Queries thanos-querier for vLLM/GPU/cluster metrics (for direct vLLM pipelines) |
| `log-mlflow` | Analyzes Tempo traces and batch-logs all results to SageMaker MLflow |
| `cleanup` | Removes all deployed resources (OGX, vLLM, MCP, PostgreSQL) |

## Pre-configured Scenarios

Available in `pipelineruns/`:

### RHAIIS + OGX (Production Model — Qwen3-VL-30B)

| File | Scenario | Key Settings |
|------|----------|-------------|
| `benchmark-rhaiis-ogx-simple.yaml` | Steady-state, full stack with OTel | 128 users, 600s |
| `benchmark-rhaiis-ogx-simple-no-otel.yaml` | Steady-state, no OTel | 128 users, 600s |
| `benchmark-rhaiis-ogx-mcp.yaml` | MCP tool calling with RHAIIS | 128 users, 600s, 1 tool, 50-token responses |
| `benchmark-rhaiis-direct.yaml` | Direct RHAIIS baseline | For overhead comparison |

### HPA Scaling

| File | Scenario | Key Settings |
|------|----------|-------------|
| `benchmark-ramp-hpa.yaml` | HPA reference — Poisson ramp | 256 users peak, memory target 75%, 50 min |
| `benchmark-poisson.yaml` | Poisson PMF with HPA | 128 users, λ=10, memory target 75% |

### Load Shapes

| File | Scenario | Key Settings |
|------|----------|-------------|
| `benchmark-steady.yaml` | MCP steady load | 100 users, 60s |
| `benchmark-spike.yaml` | MCP spike load | 3 baseline → 15 peak → cooldown |
| `benchmark-realistic.yaml` | Realistic traffic pattern | Warm-up → ramp → peak → taper → cool-down |
| `benchmark-custom.yaml` | Custom stages via JSON | Fully configurable stages |

### Community vLLM (Smaller Models — Llama 3.2 3B)

| File | Scenario | Key Settings |
|------|----------|-------------|
| `benchmark-vllm-direct.yaml` | Direct vLLM baseline | OCI modelcar deployment |
| `benchmark-vllm-direct-rhaiis.yaml` | Direct vLLM via RHAIIS | HuggingFace download |
| `benchmark-responses-simple.yaml` | OGX Responses (no tools) | Community vLLM backend |
| `benchmark-responses-simple-rhaiis.yaml` | OGX Responses via RHAIIS | RHAIIS backend |
| `benchmark-responses-mcp-rhaiis.yaml` | MCP with RHAIIS backend | Tool calling |
| `benchmark-stress-heavy.yaml` | Heavy stress test | 500 users, long prompt |

## Load Shapes

Locust load shapes control how user count varies over time:

| Shape | Description | Configuration |
|-------|-------------|---------------|
| `steady` | Constant user count | Default — uses `USERS` and `SPAWN_RATE` directly |
| `poisson` | Bell curve: rise → hold → fall (Poisson PMF) | `POISSON_LAMBDA`, `POISSON_HOLD_SECONDS`, `POISSON_MIN_USERS`, `POISSON_FALL_K_MULT` |
| `spike` | Sudden burst: baseline → spike → hold → drop | `SPIKE_BASELINE_USERS`, `SPIKE_PEAK_USERS`, `SPIKE_RAMP_DURATION`, `SPIKE_HOLD_DURATION` |
| `realistic` | Gradual traffic: warm-up → ramp → peak → taper → cool-down | Auto-distributes time: 10% warm-up, 15% ramp, 40% peak, 20% taper, 15% cool-down |
| `custom` | Fully configurable via JSON stages | `CUSTOM_STAGES` JSON array: `[{"duration":60,"users":10,"spawn_rate":2}, ...]` |

## User Classes

Locust user classes (selected via `USER_CLASS` env var):

| Class | API | Description |
|-------|-----|-------------|
| `ResponsesSimpleUser` | `/v1/responses` | OGX Responses API without tools |
| `ResponsesMCPUser` | `/v1/responses` | OGX Responses API with MCP tool calling (SDG docs MCP) |
| `ResponsesMCPBenchmarkUser` | `/v1/responses` | OGX Responses API with deterministic benchmark MCP server |
| `ChatCompletionsUser` | `/v1/chat/completions` | Direct vLLM Chat Completions API |

All user classes support:
- **Synthetic prompts**: `INPUT_TOKENS` > 0 → `generate-prompt` task creates tokenizer-accurate prompts per user
- **Controlled output**: `OUTPUT_TOKENS` > 0 → forces exact output token count via `ignore_eos`
- **Connection TTL**: `CONNECTION_TTL_SECONDS` > 0 → reconnects periodically to redistribute load across new HPA pods

## Configuration Parameters

Set these in the PipelineRun to configure your test:

| Parameter | Description | Example |
|-----------|-------------|---------|
| `NAMESPACE` | Namespace for deployed components | `ogx-bench` |
| `USERS` | Concurrent Locust users | `128` |
| `SPAWN_RATE` | Users spawned per second | `128` |
| `RUN_TIME_SECONDS` | Test duration | `600` |
| `INPUT_TOKENS` | Synthetic input prompt length (0 = use PROMPT as-is) | `50` |
| `OUTPUT_TOKENS` | Max output tokens per request (0 = model decides) | `50` |
| `WARMUP_SECONDS` | Wait before starting the test (Prometheus target discovery) | `300` |
| `LOAD_SHAPE` | Load pattern: `steady`, `spike`, `realistic`, `custom`, `poisson` | `poisson` |
| `ENABLE_MLFLOW` | Log results to SageMaker MLflow | `true` |
| `ENABLE_HPA` | Deploy HPA for OGX autoscaling | `true` |
| `HPA_MIN_REPLICAS` / `HPA_MAX_REPLICAS` | Autoscaler bounds | `1` / `4` |
| `HPA_MEMORY_TARGET` / `HPA_CPU_TARGET` | HPA target utilization (0 = disabled) | `75` / `60` |
| `HPA_POST_TEST_SECONDS` | Extra seconds to capture post-test scale-down | `120` |
| `REPLICAS` | Fixed replica count (when HPA is disabled) | `4` |
| `SKIP_DEPLOY_RHAIIS` | Skip model redeployment if already running | `true` |
| `SKIP_DEPLOY_POSTGRES` | Skip PostgreSQL redeployment | `true` |
| `SKIP_DEPLOY_OGX` | Skip OGX redeployment | `true` |
| `SKIP_CLEANUP` | Preserve resources after test (for investigation) | `true` |
| `CONNECTION_TTL_SECONDS` | Locust reconnect interval for load redistribution | `60` |
| `PROMPT_SUFFIX` | Appended to synthetic prompts (for MCP tool invocation) | `"use tool_0"` |
| `MCP_NUM_TOOLS` | Number of tools on benchmark MCP server | `1` |
| `MCP_TOOL_RESPONSE_TOKENS` | Token count in MCP tool responses | `50` |
| `MCP_TOOL_DESCRIPTION_TOKENS` | Token count in MCP tool descriptions | `50` |

## Metrics Collection

Every pipeline collects metrics from multiple layers. Even when OTel is disabled, all non-OTel metrics are still collected.

### Collected Metrics

| Source | Metrics | Always Available |
|--------|---------|:---:|
| **Locust** | RPS, latency (avg/min/max/p50/p95/p99/stddev/cv), failure rate, active users over time | Yes |
| **HPA sidecar** | Pod count, per-pod CPU/memory, HPA current/desired replicas, CPU/memory utilization % | Yes |
| **Prometheus sidecar** | PostgreSQL (connections, commits, rollbacks, cache hit, deadlocks, inserts, locks) + vLLM (running/waiting, KV cache, throughput) — every 5s | Yes |
| **vLLM (thanos-querier)** | Requests running/waiting, KV cache, TTFT (p50/p95), e2e latency (p50/p95), queue time, inter-token latency, prompt/generation throughput | Yes |
| **GPU (DCGM)** | Per-GPU utilization, memory used, temperature, power — separate series per GPU | Yes |
| **PostgreSQL (thanos-querier)** | Connections by state/app, commits/rollbacks/inserts/fetched/returned rates, cache hit ratio, deadlocks, locks by mode, seq/idx scans, DB size, temp bytes, per-table storage (live/dead rows, data/index/total bytes), autovacuum count, checkpointer stats, bgwriter stats | Yes |
| **Per-Pod I/O** | Network (rx/tx bytes/packets per pod), filesystem (read/write bytes per pod), CPU throttling (%) | Yes |
| **Per-Pod Compute** | CPU cores (total + OGX container), context switches, memory (GiB) | Yes |
| **Per-Node** | CPU (usage/user/system/iowait cores), memory (GiB), network (rx/tx bytes), disk (read/write bytes, I/O time) | Yes |
| **PVC Storage** | Used/capacity (GiB), inodes used — per PVC | Yes |
| **Endpoint / Pod Lifecycle** | Ready/not-ready endpoints, pod phase, readiness, restarts, terminating, terminated/waiting reasons | Yes |
| **OTel application** | GenAI request rate + latency, DB connection pool (used/idle), active requests, API request rate per endpoint, process CPU/memory/threads | Only with OTel |
| **Traces (Tempo)** | Per-request latency breakdown: inference, DB (connect/insert/begin/commit/rollback), MCP (list/invoke/HTTP transport), OGX overhead, input/output tokens, tool call counts | Only with OTel |

### Derived Trace Metrics

When OTel is enabled, `trace_analyzer.py` queries Tempo and computes per-request breakdowns and aggregate percentiles (p50/p95/p99) for:
- **Total request latency**
- **Inference time** (vLLM spans)
- **DB time** — total + per-operation type (connect, INSERT, BEGIN, COMMIT, ROLLBACK)
- **MCP time** — list_tools + invoke_tool + HTTP transport
- **OGX overhead** (total - inference - DB - MCP)
- **Token counts** (input/output per request, with avg/min/max aggregates)
- **Tool call counts** (avg/min/max/total)

### Metrics Destinations

- **Grafana dashboards** — via Prometheus Pushgateway and OpenShift monitoring
- **MLflow** — batch-logged with time-series data (when `ENABLE_MLFLOW=true`)

## Grafana

The monitoring stack includes Grafana with pre-configured dashboards. To access:

```bash
oc port-forward svc/grafana -n ogx-monitoring 3000:3000
```

Dashboards available (manifests in `manifests/monitoring/`):

| Dashboard | Content |
|-----------|---------|
| **OCP Overview** | High-level test summary: RPS, latency percentiles, failure rate, autoscaling behavior |
| **OGX Deep** | Application-level metrics from OTel (GenAI latency, DB pool, active requests, API latency by endpoint) |
| **Inference** | vLLM engine metrics (request queue, KV cache, TTFT, e2e latency, token throughput) |
| **GPU** | Per-GPU utilization, memory, temperature, power draw (DCGM) |
| **Database** | PostgreSQL performance (active connections, transactions, cache hit ratio, deadlocks) |

## MLflow — Results Structure and Viewing

When `ENABLE_MLFLOW=true`, the `log-mlflow` task reads all result files from the shared workspace and batch-logs them to SageMaker MLflow.

### Prerequisites

The `mlflow-aws-credentials` secret must exist in `tekton-ogx` namespace with:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `MLFLOW_TRACKING_ARN`

### Run Naming

Each MLflow run is named: `{prefix}-{model}-{users}u-{timestamp}` (e.g., `tekton-qwen3-vl-30b-a3b-instruct-128u-20260601-143022`). Custom prefixes can be set via `MLFLOW_RUN_NAME_PREFIX`.

### Logged Data

#### Parameters (run metadata)

| Parameter | Source | Example |
|-----------|--------|---------|
| `users` | PipelineRun param | `128` |
| `model` | PipelineRun param | `vllm-inference/qwen3-vl-30b-a3b-instruct` |
| `input_tokens` | PipelineRun param | `50` |
| `output_tokens` | PipelineRun param | `50` |
| `load_shape` | PipelineRun param | `poisson` |
| `replicas` | PipelineRun param | `4` |
| `test_type` | Always set | `locust_load_test` |
| `ocp_version` | Cluster query | `4.20.6` |
| `rhoai_version` | Cluster query | `3.3.0` |

#### Summary Metrics (single values per run)

From `metrics_collector.py` → `summary_metrics.json`, logged as `locust/*`:

| Metric | Description |
|--------|-------------|
| `locust/total_requests` | Total requests completed |
| `locust/total_failures` | Total failed requests |
| `locust/failure_rate_pct` | Failure percentage |
| `locust/requests_per_second` | Overall throughput (req/s) |
| `locust/avg_response_time_ms` | Average request latency |
| `locust/min_response_time_ms` | Minimum latency |
| `locust/max_response_time_ms` | Maximum latency |
| `locust/response_time_p50_ms` | Median latency |
| `locust/response_time_p95_ms` | 95th percentile latency |
| `locust/response_time_p99_ms` | 99th percentile latency |
| `locust/response_time_stddev_ms` | Latency standard deviation |
| `locust/response_time_cv` | Coefficient of variation (stddev/mean) |

#### Trace Aggregate Metrics (single values per run, OTel only)

From `trace_analyzer.py` → `trace_metrics.json`, logged directly:

| Metric | Description |
|--------|-------------|
| `trace/total_request/{p50,p95,p99}_ms` | End-to-end request latency percentiles |
| `trace/inference/{p50,p95,p99}_ms` | Time spent in vLLM inference |
| `trace/ls_overhead/{p50,p95,p99}_ms` | OGX orchestration overhead (total - inference - DB - MCP) |
| `trace/db/{p50,p95,p99}_ms` | Total database time (top-level spans only) |
| `trace/db_connect/{p50,p95,p99}_ms` | DB connection acquisition time |
| `trace/db_connect/avg_count` | Average connect operations per request |
| `trace/db_insert/{p50,p95,p99}_ms` | DB insert time |
| `trace/db_insert/avg_count` | Average inserts per request |
| `trace/db_begin/{p50,p95,p99}_ms` | Transaction BEGIN time |
| `trace/db_begin/avg_count` | Average BEGINs per request |
| `trace/db_commit/{p50,p95,p99}_ms` | Transaction COMMIT time |
| `trace/db_commit/avg_count` | Average COMMITs per request |
| `trace/db_rollback/{p50,p95,p99}_ms` | Transaction ROLLBACK time |
| `trace/db_rollback/avg_count` | Average ROLLBACKs per request |
| `trace/db_other/{p50,p95,p99}_ms` | Other DB operations time |
| `trace/db_other/avg_count` | Average other DB ops per request |
| `trace/list_mcp_tools/{p50,p95,p99}_ms` | MCP tools/list latency |
| `trace/invoke_mcp_tool/{p50,p95,p99}_ms` | MCP tools/call latency |
| `trace/mcp_total/{p50,p95,p99}_ms` | Combined MCP time (list + invoke) |
| `trace/tool_calls/{avg,min,max,total}` | Tool call counts |
| `trace/tokens/{avg,min,max}_input` | Input tokens per request |
| `trace/tokens/{avg,min,max}_output` | Output tokens per request |
| `trace/tokens/avg_total` | Total tokens (input + output) per request |

#### Time-Series Metrics (step charts in MLflow UI)

All time-series use the actual test start epoch as the base timestamp, so charts align with Grafana. Each `step` = seconds from test start.

**Locust Scenario** (from `metrics_collector.py`, every 1s):

| Metric | Description |
|--------|-------------|
| `scenario/active_users` | Current active Locust users |
| `scenario/target_users` | Target user count (from load shape) |
| `scenario/rps_10s_window` | Requests per second (10s rolling window) |
| `scenario/failures_per_sec_10s_window` | Failures per second (10s rolling window) |
| `scenario/avg_response_time_cumulative_ms` | Cumulative average response time |
| `scenario/total_requests_cumulative` | Total requests so far |
| `scenario/total_failures_cumulative` | Total failures so far |
| `scenario/fail_ratio_cumulative_pct` | Cumulative failure ratio (%) |

**HPA / Pod Resources** (from HPA sidecar, every 1s):

| Metric | Description |
|--------|-------------|
| `hpa/pod_count` | Number of OGX pods running |
| `hpa/memory_avg_mib` | Average memory across pods (MiB) |
| `hpa/cpu_avg_millicores` | Average CPU across pods (millicores) |
| `hpa/current_replicas` | HPA current replicas |
| `hpa/desired_replicas` | HPA desired replicas |
| `hpa/cpu_percent` | HPA reported CPU utilization (%) |
| `hpa/memory_percent` | HPA reported memory utilization (%) |

**Prometheus Sidecar** (from scraper sidecar, every 5s):

| Metric | Description |
|--------|-------------|
| `pg/sidecar_active_connections` | PostgreSQL active connections |
| `pg/sidecar_xact_commits` | Transaction commits |
| `pg/sidecar_xact_rollbacks` | Transaction rollbacks |
| `pg/sidecar_cache_hit_ratio` | Buffer cache hit ratio |
| `pg/sidecar_deadlocks` | Deadlock count |
| `pg/sidecar_rows_inserted` | Rows inserted |
| `pg/sidecar_lock_count` | Active locks |
| `vllm/sidecar_requests_running` | vLLM requests currently running |
| `vllm/sidecar_requests_waiting` | vLLM requests in queue |
| `vllm/sidecar_gpu_cache_pct` | KV cache utilization (%) |
| `vllm/sidecar_throughput_tps` | Token throughput (tokens/s) |

**Trace Per-Request** (from `trace_analyzer.py`, per trace):

| Metric | Description |
|--------|-------------|
| `trace/request_duration_ms` | Total request time |
| `trace/inference_duration_ms` | vLLM inference time |
| `trace/list_mcp_tools_ms` | MCP tools/list time |
| `trace/invoke_mcp_tool_ms` | MCP tools/call time |
| `trace/db_duration_ms` | Total DB time |
| `trace/db_connect_ms` | DB connection time |
| `trace/db_connect_count` | Number of connect operations |
| `trace/db_insert_ms` | DB insert time |
| `trace/db_insert_count` | Number of inserts |
| `trace/db_begin_count` | Number of BEGINs |
| `trace/db_commit_count` | Number of COMMITs |
| `trace/db_rollback_count` | Number of ROLLBACKs |
| `trace/mcp_http_duration_ms` | MCP HTTP transport time |
| `trace/ls_overhead_ms` | OGX overhead (total - inference - DB - MCP) |
| `trace/input_tokens` | Input tokens for this request |
| `trace/output_tokens` | Output tokens for this request |
| `trace/tool_calls` | Number of tool calls |

#### Prometheus Query Results (from thanos-querier, time-series + aggregates)

The `query_prometheus.py` script queries thanos-querier for the test window and logs both time-series data (for step charts) and aggregate values (avg/max) for each metric. Every metric below is stored as both `{name}` (time-series) and `{name}_avg` / `{name}_max` (aggregate).

**OTel Application Metrics** (only available with OTel enabled):

| Metric | Description |
|--------|-------------|
| `otel/genai_request_rate` | GenAI request rate (req/s) |
| `otel/genai_avg_latency_s` | GenAI average latency (seconds) |
| `otel/db_pool_used` | DB connection pool — used connections |
| `otel/db_pool_idle` | DB connection pool — idle connections |
| `otel/active_requests` | Active HTTP requests in OGX |
| `otel/api_request_rate_{endpoint}` | Request rate per API endpoint (labeled) |
| `otel/cpu_utilization` | OGX process CPU utilization |
| `otel/memory_rss_bytes` | OGX process memory (RSS) |
| `otel/thread_count` | OGX process thread count |

**vLLM Inference Metrics:**

| Metric | Description |
|--------|-------------|
| `vllm/requests_running` | Requests currently being processed |
| `vllm/requests_waiting` | Requests queued |
| `vllm/kv_cache_usage` | KV cache utilization (0-1) |
| `vllm/prompt_throughput_tps` | Input token throughput (tokens/s) |
| `vllm/generation_throughput_tps` | Output token throughput (tokens/s) |
| `vllm/ttft_p50_s` | Time to first token — p50 (seconds) |
| `vllm/ttft_p95_s` | Time to first token — p95 (seconds) |
| `vllm/e2e_latency_p50_s` | End-to-end request latency — p50 (seconds) |
| `vllm/e2e_latency_p95_s` | End-to-end request latency — p95 (seconds) |
| `vllm/queue_time_p50_s` | Queue wait time — p50 (seconds) |
| `vllm/inter_token_latency_p50_s` | Inter-token latency — p50 (seconds) |

**GPU / DCGM Metrics** (per-GPU, labeled by GPU index):

| Metric | Description |
|--------|-------------|
| `gpu/utilization_pct_{gpu}` | GPU compute utilization (%) |
| `gpu/memory_used_mib_{gpu}` | GPU memory used (MiB) |
| `gpu/temperature_c_{gpu}` | GPU temperature (°C) |
| `gpu/power_w_{gpu}` | GPU power draw (W) |

**PostgreSQL Metrics:**

| Metric | Description |
|--------|-------------|
| `pg/active_connections_{state}` | Connections by state (active, idle, etc.) |
| `pg/commits_per_sec` | Transaction commit rate |
| `pg/rollbacks_per_sec` | Transaction rollback rate |
| `pg/inserts_per_sec` | Row insert rate |
| `pg/cache_hit_ratio` | Buffer cache hit ratio |
| `pg/deadlocks_per_sec` | Deadlock rate |
| `pg/database_size_bytes` | Total database size |
| `pg/rows_fetched_per_sec` | Rows fetched rate |
| `pg/rows_returned_per_sec` | Rows returned rate |
| `pg/blk_read_time_ms_per_sec` | Block read I/O time |
| `pg/blk_write_time_ms_per_sec` | Block write I/O time |
| `pg/temp_bytes` | Temporary file space used |
| `pg/locks_{mode}` | Locks by mode (AccessShareLock, RowExclusiveLock, etc.) |
| `pg/seq_scan_per_sec` | Sequential scan rate (all tables) |
| `pg/idx_scan_per_sec` | Index scan rate (all tables) |
| `pg/inserts_by_table_{table}` | Cumulative inserts per table |
| `pg/idx_scan_by_table_{table}` | Cumulative index scans per table |
| `pg/seq_scan_by_table_{table}` | Cumulative seq scans per table |
| `pg/connections_by_app_{app}` | Connections by application name |
| `pg/max_connections` | PostgreSQL max_connections setting |

**PostgreSQL Storage & Vacuum** (per-table):

| Metric | Description |
|--------|-------------|
| `pg/live_rows_by_table_{table}` | Live rows per table |
| `pg/dead_rows_by_table_{table}` | Dead (unvacuumed) rows per table |
| `pg/autovacuum_count_by_table_{table}` | Autovacuum runs per table |
| `pg/table_size_bytes_{table}` | Table data size |
| `pg/table_total_bytes_{table}` | Total table size (data + indexes + toast) |
| `pg/table_data_bytes_{table}` | Table data bytes |
| `pg/index_bytes_{table}` | Index size per table |

**PostgreSQL Checkpointer & Background Writer:**

| Metric | Description |
|--------|-------------|
| `pg/checkpoints_timed` | Scheduled checkpoint count |
| `pg/checkpoints_requested` | Requested checkpoint count |
| `pg/checkpoint_write_time_ms` | Checkpoint write time |
| `pg/checkpoint_sync_time_ms` | Checkpoint sync time |
| `pg/checkpoint_buffers_written` | Buffers written by checkpointer |
| `pg/bgwriter_buffers_clean` | Buffers written by background writer |
| `pg/bgwriter_buffers_alloc` | Buffers allocated |
| `pg/bgwriter_maxwritten_clean` | Times bgwriter stopped due to write limit |

**Per-Pod Network I/O** (labeled by pod):

| Metric | Description |
|--------|-------------|
| `pod_net/rx_bytes_per_sec_{pod}` | Network receive bytes/s |
| `pod_net/tx_bytes_per_sec_{pod}` | Network transmit bytes/s |
| `pod_net/rx_packets_per_sec_{pod}` | Network receive packets/s |
| `pod_net/tx_packets_per_sec_{pod}` | Network transmit packets/s |

**Per-Pod Filesystem I/O** (labeled by pod):

| Metric | Description |
|--------|-------------|
| `pod_fs/write_bytes_per_sec_{pod}` | Filesystem write bytes/s |
| `pod_fs/read_bytes_per_sec_{pod}` | Filesystem read bytes/s |

**Per-Pod CPU** (labeled by pod):

| Metric | Description |
|--------|-------------|
| `pod_cpu/throttled_pct_{pod}` | CPU throttling percentage |
| `pod_cpu/cpu_cores_{pod}` | CPU usage (cores) — all containers |
| `pod_cpu/cpu_cores_ogx_{pod}` | CPU usage (cores) — OGX container only |
| `pod_cpu/context_switches_voluntary_{pod}` | Voluntary context switches/s |

**Per-Pod Memory** (labeled by pod):

| Metric | Description |
|--------|-------------|
| `pod_memory/memory_gib_{pod}` | Working set memory (GiB) |

**Per-Node CPU & Memory** (labeled by instance/node):

| Metric | Description |
|--------|-------------|
| `node_cpu/usage_cores_{node}` | Total CPU usage (non-idle cores) |
| `node_cpu/user_cores_{node}` | User-space CPU (cores) |
| `node_cpu/system_cores_{node}` | Kernel-space CPU (cores) |
| `node_cpu/iowait_cores_{node}` | I/O wait CPU (cores) |
| `node_memory/usage_gib_{node}` | Memory used (GiB) |

**Per-Node Network I/O** (labeled by instance):

| Metric | Description |
|--------|-------------|
| `node_net/rx_bytes_per_sec_{instance}` | Node network receive bytes/s |
| `node_net/tx_bytes_per_sec_{instance}` | Node network transmit bytes/s |

**Per-Node Disk I/O** (labeled by instance):

| Metric | Description |
|--------|-------------|
| `node_disk/read_bytes_per_sec_{instance}` | Disk read bytes/s |
| `node_disk/write_bytes_per_sec_{instance}` | Disk write bytes/s |
| `node_disk/io_time_seconds_per_sec_{instance}` | Disk I/O time (fraction of second) |

**PVC Storage** (labeled by PVC name):

| Metric | Description |
|--------|-------------|
| `node_storage/pvc_used_gib_{pvc}` | PVC space used (GiB) |
| `node_storage/pvc_capacity_gib_{pvc}` | PVC total capacity (GiB) |
| `node_storage/pvc_inodes_used_{pvc}` | PVC inodes used |

**Endpoint Readiness** (labeled by pod):

| Metric | Description |
|--------|-------------|
| `endpoint/ready_{pod}` | Pod is in ready endpoints |
| `endpoint/not_ready_{pod}` | Pod is in not-ready endpoints |

**Pod Lifecycle** (labeled by pod or reason):

| Metric | Description |
|--------|-------------|
| `pod_lifecycle/phase_{pod}` | Pod phase (Pending/Running/Succeeded/Failed) |
| `pod_lifecycle/ready_{pod}` | Pod readiness condition |
| `pod_lifecycle/restarts_{pod}` | Container restart count |
| `pod_lifecycle/terminating_{pod}` | Pod has deletion timestamp (being terminated) |
| `pod_lifecycle/container_terminated_{reason}` | Container terminated (OOMKilled, Error, etc.) |
| `pod_lifecycle/container_waiting_{reason}` | Container waiting (CrashLoopBackOff, etc.) |

**Locust Pod / Tekton Namespace** (ensures load generator is not resource-starved):

| Metric | Description |
|--------|-------------|
| `tekton_cpu/cpu_cores_{pod}` | CPU usage (cores) — Locust pod + sidecars |
| `tekton_memory/memory_gib_{pod}` | Memory usage (GiB) — Locust pod + sidecars |
| `tekton_cpu/throttled_pct_{pod}` | CPU throttling percentage (indicates resource starvation) |
| `tekton_net/rx_bytes_per_sec_{pod}` | Network receive bytes/s |
| `tekton_net/tx_bytes_per_sec_{pod}` | Network transmit bytes/s |

> **Note:** For long tests (>12h), rate metrics are re-queried with a `[5m]` window and stored with a `_5m` suffix (e.g., `pg/commits_per_sec_5m`, `vllm/prompt_throughput_tps_5m`) to ensure full coverage when raw metrics are compacted.

#### Artifacts (raw files)

All files from the results workspace are uploaded as artifacts:

| File | Content |
|------|---------|
| `summary_metrics.json` | Locust aggregate stats |
| `timeseries_metrics.json` | Per-second Locust samples |
| `hpa-metrics.jsonl` | Per-second HPA/pod metrics |
| `prometheus-metrics.jsonl` | Per-5s PostgreSQL + vLLM metrics |
| `prometheus_query_results.json` | Thanos-querier results (aggregate + time-series) |
| `trace_metrics.json` | Tempo trace analysis (aggregate + per-request) |
| `locust-results_stats.csv` | Locust standard stats output |
| `locust-results_stats_history.csv` | Locust per-second history |
| `locust-results_failures.csv` | Failed request details |
| `synthetic_prompts.jsonl` | Generated prompts (one per line) |
| `cluster_versions.json` | OCP, RHOAI, operator versions |

### Viewing Results in MLflow

1. **Compare runs**: Select multiple runs in the experiment view → click "Compare" → choose metrics to overlay (e.g., `locust/p99_response_time_ms` across concurrency levels)
2. **Step charts**: Click any run → "Metrics" tab → select a time-series metric (e.g., `scenario/active_users` or `hpa/pod_count`) to see the full test timeline
3. **Artifacts**: Click any run → "Artifacts" tab → download raw CSV/JSON files for custom analysis
4. **Filter by params**: Use the search bar with `params.users = "128"` or `params.load_shape = "poisson"` to filter runs

## Experiment Runners

Scripts for running systematic experiment matrices (concurrency ladders with multiple repetitions):

| Script | Description |
|--------|-------------|
| `scripts/phase1-direct-vllm-baseline.sh` | Concurrency ladder (1–128) × 3 reps against direct vLLM. 24 pipeline runs. |
| `scripts/phase2-ogx-overhead.sh` | Concurrency ladder × replica counts (1, 2, 4) × 3 reps. 72 pipeline runs. |
| `scripts/phase1-runner-job.yaml` | Kubernetes Job wrapper for Phase 1 script |
| `scripts/phase2-runner-job.yaml` | Kubernetes Job wrapper for Phase 2 script |

Both scripts support `RESUME_FROM` to continue from a specific point after interruption.

## Container Images

| File | Description |
|------|-------------|
| `docker/Dockerfile.locust-mlflow` | Extends the Locust image with MLflow + boto3 for SageMaker logging |
