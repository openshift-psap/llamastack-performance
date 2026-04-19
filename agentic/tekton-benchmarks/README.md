# Tekton LlamaStack Benchmarks

Automated performance testing for LlamaStack on OpenShift using Tekton pipelines, Locust load generation, and comprehensive metrics collection.

## What This Does

Deploys the full LlamaStack stack (or just the inference backend), runs load tests with configurable concurrency and duration, collects metrics from every layer, and logs everything to MLflow and Grafana for analysis.

## Pipelines

| Pipeline | Purpose |
|----------|---------|
| `rhaiis-llamastack-simple` | Full LlamaStack stack with RHAIIS inference, PostgreSQL, OTel tracing. Tests the `/v1/responses` API. |
| `rhaiis-llamastack-simple-no-otel` | Same as above but with OpenTelemetry **disabled** (`OTEL_SDK_DISABLED=true`). Used for A/B comparison to measure OTel overhead. |
| `rhaiis-direct` | Direct RHAIIS (vLLM) benchmarking via `/v1/chat/completions` — no LlamaStack, no Postgres, no OTel. Measures raw inference performance. |
| `vllm-direct` | Direct vLLM benchmarking with optional RHAIIS or community vLLM deployment. |
| `responses-simple` | LlamaStack Responses API with community vLLM backend. |
| `responses-mcp` | LlamaStack Responses API with MCP tool calling (agentic workload). |

### Choosing a Pipeline

- **Baseline inference performance** → `rhaiis-direct`
- **LlamaStack overhead without OTel** → `rhaiis-llamastack-simple-no-otel`
- **Full production stack** → `rhaiis-llamastack-simple`
- **OTel overhead measurement** → Compare `rhaiis-llamastack-simple` vs `rhaiis-llamastack-simple-no-otel`

## Quick Start

```bash
# Apply RBAC, tasks, and pipeline
oc apply -f rbac/
oc apply -f tasks/
oc apply -f pipelines/rhaiis-llamastack-simple-no-otel.yaml

# Run a benchmark (edit the PipelineRun params as needed)
oc create -f pipelineruns/benchmark-rhaiis-llamastack-simple-no-otel.yaml

# Watch progress
tkn pipelinerun logs -f -n tekton-llamastack
```

## Key Parameters

Set these in the PipelineRun to configure your test:

| Parameter | Description | Example |
|-----------|-------------|---------|
| `NAMESPACE` | Namespace for deployed components | `llamastack-bench` |
| `USERS` | Concurrent Locust users | `128` |
| `SPAWN_RATE` | Users spawned per second | `128` |
| `RUN_TIME_SECONDS` | Test duration | `600` |
| `INPUT_TOKENS` | Synthetic input prompt length | `1000` |
| `OUTPUT_TOKENS` | Max output tokens per request | `1000` |
| `WARMUP_SECONDS` | Wait before starting the test (lets Prometheus discover targets) | `300` |
| `LOAD_SHAPE` | Load pattern: `steady`, `spike`, `realistic`, `custom` | `steady` |
| `ENABLE_MLFLOW` | Log results to SageMaker MLflow | `true` |
| `SKIP_DEPLOY_RHAIIS` | Skip RHAIIS redeployment if already running | `false` |

## Metrics Collection

Every pipeline collects metrics from multiple layers. Even when OTel is disabled, the non-OTel metrics are still collected.

### What Gets Collected

| Source | Metrics | Always Available |
|--------|---------|:---:|
| **Locust** | RPS, latency (avg/p50/p95/p99), failure rate, active users over time | Yes |
| **HPA sidecar** | Pod count, CPU/memory usage, autoscaler state | Yes |
| **vLLM** | Requests running/waiting, KV cache usage, TTFT, e2e latency, token throughput | Yes |
| **GPU (DCGM)** | Per-GPU utilization, memory, temperature, power — logged as separate series per GPU | Yes |
| **PostgreSQL** | Active connections, commits/rollbacks, cache hit ratio, deadlocks, inserts | Yes |
| **Cluster** | Per-pod CPU and memory | Yes |
| **OTel application** | GenAI request rate, DB connection pool, API latency by endpoint, LlamaStack process metrics | Only with OTel |
| **Traces (Tempo)** | Per-request latency breakdown: inference, DB, MCP tools, LlamaStack overhead | Only with OTel |

### Where Metrics Go

- **Grafana dashboards** — via Prometheus Pushgateway and OpenShift monitoring
- **MLflow** — batch-logged with time-series data (when `ENABLE_MLFLOW=true`)

## Grafana

The monitoring stack includes Grafana with pre-configured dashboards. To access it:

```bash
# Port-forward Grafana
oc port-forward svc/grafana -n llamastack-monitoring 3000:3000
```

Then open `http://localhost:3000`. You'll find dashboards for:

- **Overview** — High-level test summary: RPS, latency percentiles, failure rate, autoscaling behavior
- **LlamaStack** — Application-level metrics from OpenTelemetry (GenAI latency, DB connection pool, active requests, API latency by endpoint)
- **Inference** — vLLM engine metrics (request queue, KV cache usage, TTFT, e2e latency, token throughput)
- **GPU** — Per-GPU utilization, memory, temperature, power draw (DCGM)
- **Database** — PostgreSQL performance (active connections, transactions, cache hit ratio, deadlocks)
- **Cluster** — Per-pod CPU and memory usage across the benchmark namespace

The Pushgateway receives benchmark results at the end of each test run, so dashboards show both real-time and historical data.

## MLflow

When `ENABLE_MLFLOW=true`, all metrics are batch-logged to SageMaker MLflow. This requires the `mlflow-aws-credentials` secret in the `tekton-llamastack` namespace with:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `MLFLOW_TRACKING_ARN`

Each run includes summary metrics, time-series data (viewable as step charts), cluster version info, and all raw result files as artifacts.

## Structure

```
tekton-benchmarks/
├── pipelines/          # Pipeline definitions
├── pipelineruns/       # Pre-configured PipelineRun examples
├── tasks/              # Reusable Tekton Tasks
├── manifests/          # Kubernetes manifests (LlamaStack, Postgres, RHAIIS ServingRuntime/InferenceService)
├── scripts/            # Python scripts (MLflow logger, Prometheus queries, trace analysis)
├── rbac/               # ServiceAccount and RBAC for Tekton
├── locustfiles/        # Locust test files and load shapes
└── monitoring/         # Grafana dashboards and monitoring setup
```
