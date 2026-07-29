# Tekton OGX Benchmarks

Automated performance testing for OGX on OpenShift using Tekton pipelines, Locust load generation, and comprehensive metrics collection.

## Overview

This framework measures OGX overhead using a lightweight [inference simulator](https://github.com/llm-d/llm-d-inference-sim) with configurable TTFT/ITL delays. This isolates OGX overhead from model variability, enabling deterministic and reproducible testing. The framework deploys the full OGX stack, executes load tests with configurable concurrency and duration, collects metrics from every layer (Locust, PostgreSQL, OTel), and logs all results to MLflow for analysis.

## Pipelines

The [llm-d-inference-sim](https://github.com/llm-d/llm-d-inference-sim) implements the `/v1/chat/completions` API with configurable TTFT and ITL delays, enabling deterministic and reproducible overhead measurement.

| Pipeline | Purpose |
|----------|---------|
| `simulator-direct-benchmark` | **Phase 1 — Direct Baseline.** Locust → inference simulator (no OGX). Establishes the baseline throughput and latency for each delay profile. |
| `simulator-ogx-chat-benchmark` | **Phase 2 — OGX Chat API.** Locust → OGX → simulator. Routes `/v1/chat/completions` through OGX with PostgreSQL and OTel. Measures OGX overhead on the chat completions path. |
| `simulator-ogx-responses-benchmark` | **Phase 3 — OGX Responses API.** Locust → OGX → simulator. Routes `/v1/responses` through OGX with PostgreSQL and OTel. Measures overhead on the Responses API path (includes additional DB writes for response storage). |

#### Simulator Delay Profiles

The simulator is configured with TTFT (time to first token) and ITL (inter-token latency) values derived from real benchmark data. Three profiles cover the range of real-world inference behavior:

| Profile | TTFT | ITL | Derived from | Typical use |
|---------|------|-----|--------------|-------------|
| Fast | 2000 ms | 7 ms | Optimized small-model inference | Stress-testing OGX at high RPS |
| Moderate | 800 ms | 30 ms | Mid-range model inference | Default profile for overhead characterization |
| Realistic | 3700 ms | 85 ms | Large-model inference under load | Validates behavior with long request durations |

Each request takes approximately `TTFT + tokens × ITL` milliseconds. For example, a moderate-profile 500-token request takes 800 + 500 × 30 = 15,800 ms (~16 seconds).

### Experiment Jobs

The simulator pipelines are orchestrated by Kubernetes Jobs that run a concurrency ladder (1, 4, 16, 64, 128, 256, 384, 512, 768, 1024 concurrent users) × 3 payload sizes (50, 500, 2000 tokens) × 3 repetitions = 90 runs per job. Each job creates PipelineRuns sequentially from a template, waits for completion, and tracks pass/fail counts.

| Job | Pipeline | Profile | Target | Runs |
|-----|----------|---------|--------|------|
| `scripts/phase1-direct-all-job.yaml` | `simulator-direct-benchmark` | All (fast + moderate + realistic) | Simulator `/v1/chat/completions` (no OGX) | 270 |
| `scripts/phase2-chat-moderate-job.yaml` | `simulator-ogx-chat-benchmark` | Moderate (TTFT 800ms / ITL 30ms) | OGX `/v1/chat/completions` → simulator | 90 |
| `scripts/phase2-chat-realistic-job.yaml` | `simulator-ogx-chat-benchmark` | Realistic (TTFT 3700ms / ITL 85ms) | OGX `/v1/chat/completions` → simulator | 90 |
| `scripts/phase2-chat-all-job.yaml` | `simulator-ogx-chat-benchmark` | Moderate + Realistic sequentially | OGX `/v1/chat/completions` → simulator | 180 |
| `scripts/phase3-responses-fast-job.yaml` | `simulator-ogx-responses-benchmark` | Fast (TTFT 2000ms / ITL 7ms) | OGX `/v1/responses` → simulator | 90 |
| `scripts/phase3-responses-moderate-job.yaml` | `simulator-ogx-responses-benchmark` | Moderate (TTFT 800ms / ITL 30ms) | OGX `/v1/responses` → simulator | 90 |
| `scripts/phase3-responses-realistic-job.yaml` | `simulator-ogx-responses-benchmark` | Realistic (TTFT 3700ms / ITL 85ms) | OGX `/v1/responses` → simulator | 90 |
| `scripts/phase3-responses-all-job.yaml` | `simulator-ogx-responses-benchmark` | All 3 profiles sequentially | OGX `/v1/responses` → simulator | 270 |

All jobs support `RESUME_FROM` to continue from a specific point after interruption. Each job uses a PipelineRun template from `pipelineruns/templates/` with placeholder substitution.

## Getting Started

```bash
# Apply RBAC, tasks, and pipelines
oc apply -f rbac/
oc apply -f tasks/
oc apply -f pipelines/

# Run a simulator experiment (moderate profile, OGX Responses — ~10 hours)
oc create -f scripts/phase3-responses-moderate-job.yaml

# Watch progress
tkn pipelinerun logs -f -n tekton-llamastack
```

### Infrastructure

- **Inference simulator** (`manifests/inference-sim.yaml`) — a lightweight Go server ([llm-d-inference-sim](https://github.com/llm-d/llm-d-inference-sim)) that implements the `/v1/chat/completions` API with configurable delays. Runs on a loadgen node.
- **OGXServer** (`manifests/ogxserver-sim.yaml`) — standard OGXServer CR deployed by the RHOAI operator, configured to use the simulator as its inference backend.
- **PostgreSQL** (`manifests/postgres.yaml`) — PostgreSQL 18 with `max_connections=300` and a postgres-exporter sidecar for Prometheus metrics.
- **OTel Collector** (`manifests/otel-collector-metrics-only.yaml`) — receives OGX application metrics.

The simulator supports the `--time-to-first-token` and `--inter-token-latency` flags, which the `deploy-inference-sim` task configures based on pipeline parameters. The simulator handles up to 2048 concurrent sequences (`--max-num-seqs 2048`).

See [`docs/cluster-setup-guide.md`](docs/cluster-setup-guide.md) for full cluster preparation instructions.

## Pipeline Execution Flow

```
1. generate-configmap     Clone git repo → create ConfigMaps (locustfiles, scripts)
2. deploy-simulator       Deploy inference simulator with TTFT/ITL profile
3. deploy-postgres        Deploy PostgreSQL with fresh database (Phases 2/3 only)
4. deploy-otel            Deploy OTel Collector for metrics (Phases 2/3 only)
5. deploy-ogxserver       Deploy OGXServer via RHOAI operator (Phases 2/3 only)
6. generate-prompt        Generate synthetic prompts (random token sequences, no tokenizer needed)
   ─── WARMUP (default 30s) ───
7. run-locust             Execute load test with Prometheus scraper sidecar (PostgreSQL metrics every 5s)
8. query-prometheus       Query thanos-querier for all cluster-level metrics
9. log-mlflow             Batch-log everything to SageMaker MLflow
10. cleanup               Delete all deployed resources (unless SKIP_CLEANUP=true)
```

All pipelines use a shared `results` workspace (PVC) that all tasks write to and the final logging tasks read from.

## Tasks

Reusable Tekton Tasks that pipelines compose:

| Task | Description |
|------|-------------|
| `generate-configmap` | Clones git repo, creates ConfigMaps for locustfiles and scripts |
| `deploy-inference-sim` | Deploys the inference simulator with configurable TTFT/ITL delay profile |
| `deploy-postgres` | Deploys PostgreSQL (fresh DB each run) |
| `deploy-otel-metrics` | Deploys OpenTelemetry Collector for metrics collection |
| `deploy-ogxserver` | Deploys OGXServer via the RHOAI operator |
| `generate-prompt-sim` | Generates synthetic prompts with random token sequences (no tokenizer needed) |
| `run-locust` | Runs Locust load test with Prometheus scraper sidecar |
| `query-prometheus` | Queries thanos-querier for cluster-level metrics (PostgreSQL, OTel, pods, nodes) |
| `log-mlflow` | Batch-logs all results to SageMaker MLflow |
| `cleanup` | Removes all deployed resources (OGX, PostgreSQL, simulator) |

## PipelineRun Templates

PipelineRun templates in `pipelineruns/templates/` — designed for use with the [experiment runner Jobs](#experiment-runners):

| Template | Pipeline | Description |
|----------|----------|-------------|
| `simulator-direct-template.yaml` | `simulator-direct-benchmark` | Direct baseline — Locust → simulator |
| `simulator-ogx-chat-template.yaml` | `simulator-ogx-chat-benchmark` | OGX Chat API — Locust → OGX → simulator |
| `simulator-ogx-responses-template.yaml` | `simulator-ogx-responses-benchmark` | OGX Responses API — Locust → OGX → simulator |

Templates use `__PLACEHOLDER__` substitution (e.g., `__USERS__`, `__TOKENS__`, `__TTFT_MS__`, `__ITL_MS__`). The experiment runner Jobs handle this substitution automatically.

## User Classes

Locust user classes (selected via `USER_CLASS` env var):

| Class | API | Description |
|-------|-----|-------------|
| `ResponsesSimpleUser` | `/v1/responses` | OGX Responses API |
| `ChatCompletionsUser` | `/v1/chat/completions` | Direct simulator Chat Completions API |

All user classes support:
- **Synthetic prompts**: `INPUT_TOKENS` > 0 → `generate-prompt-sim` task creates random token sequences
- **Controlled output**: `OUTPUT_TOKENS` > 0 → forces exact output token count via `ignore_eos`

## Configuration Parameters

Set these in the PipelineRun to configure your test:

| Parameter | Description | Example |
|-----------|-------------|---------|
| `NAMESPACE` | Namespace for deployed components | `ogx-bench` |
| `USERS` | Concurrent Locust users | `128` |
| `SPAWN_RATE` | Users spawned per second | `128` |
| `RUN_TIME_SECONDS` | Test duration | `600` |
| `INPUT_TOKENS` | Synthetic input prompt length | `50` |
| `OUTPUT_TOKENS` | Max output tokens per request | `50` |
| `WARMUP_SECONDS` | Wait before starting the test | `30` |
| `TTFT_MS` | Simulator time to first token (ms) | `800` |
| `ITL_MS` | Simulator inter-token latency (ms) | `30` |
| `ENABLE_MLFLOW` | Log results to SageMaker MLflow | `true` |
| `SKIP_DEPLOY_POSTGRES` | Skip PostgreSQL redeployment | `true` |
| `SKIP_CLEANUP` | Preserve resources after test (for investigation) | `true` |

## Metrics Collection

Every pipeline collects metrics from multiple layers:

| Source | Metrics |
|--------|---------|
| **Locust** | RPS, latency (avg/min/max/p50/p95/p99/stddev/cv), failure rate, active users over time |
| **Prometheus sidecar** | PostgreSQL (connections, commits, rollbacks, cache hit, deadlocks, inserts, locks) — every 5s |
| **PostgreSQL (thanos-querier)** | Connections by state/app, commits/rollbacks/inserts/fetched/returned rates, cache hit ratio, deadlocks, locks by mode, seq/idx scans, DB size, temp bytes, per-table storage (live/dead rows, data/index/total bytes), autovacuum count, checkpointer stats, bgwriter stats |
| **OTel application** | GenAI request rate + latency, DB connection pool (used/idle), active requests, API request rate per endpoint, process CPU/memory/threads |
| **Per-Pod Compute** | CPU cores (total + OGX container), context switches, memory (GiB) |
| **Per-Pod I/O** | Network (rx/tx bytes/packets per pod), filesystem (read/write bytes per pod), CPU throttling (%) |
| **Per-Node** | CPU (usage/user/system/iowait cores), memory (GiB), network (rx/tx bytes), disk (read/write bytes, I/O time) |
| **PVC Storage** | Used/capacity (GiB), inodes used — per PVC |
| **Endpoint / Pod Lifecycle** | Ready/not-ready endpoints, pod phase, readiness, restarts, terminating, terminated/waiting reasons |

All results are batch-logged to **MLflow** (when `ENABLE_MLFLOW=true`).

## MLflow — Results Structure and Viewing

When `ENABLE_MLFLOW=true`, the `log-mlflow` task reads all result files from the shared workspace and batch-logs them to SageMaker MLflow.

### Prerequisites

The `mlflow-aws-credentials` secret must exist in `tekton-ogx` namespace with:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `MLFLOW_TRACKING_ARN`

### Run Naming

Each MLflow run is named: `{prefix}-{users}u-{tokens}tok-{timestamp}` (e.g., `resp-moderate-128u-500tok-20260717-143022`). Custom prefixes can be set via `MLFLOW_RUN_NAME_PREFIX`.

### Logged Data

#### Parameters (run metadata)

| Parameter | Source | Example |
|-----------|--------|---------|
| `users` | PipelineRun param | `128` |
| `input_tokens` | PipelineRun param | `50` |
| `output_tokens` | PipelineRun param | `50` |
| `ttft_ms` | PipelineRun param | `800` |
| `itl_ms` | PipelineRun param | `30` |
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

#### Time-Series Metrics (step charts in MLflow UI)

All time-series use the actual test start epoch as the base timestamp. Each `step` = seconds from test start.

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

> **Note:** For long tests (>12h), rate metrics are re-queried with a `[5m]` window and stored with a `_5m` suffix (e.g., `pg/commits_per_sec_5m`) to ensure full coverage when raw metrics are compacted.

#### Artifacts (raw files)

All files from the results workspace are uploaded as artifacts:

| File | Content |
|------|---------|
| `summary_metrics.json` | Locust aggregate stats |
| `timeseries_metrics.json` | Per-second Locust samples |
| `prometheus-metrics.jsonl` | Per-5s PostgreSQL metrics from sidecar scraper |
| `prometheus_query_results.json` | Thanos-querier results (aggregate + time-series) |
| `locust-results_stats.csv` | Locust standard stats output |
| `locust-results_stats_history.csv` | Locust per-second history |
| `locust-results_failures.csv` | Failed request details |
| `synthetic_prompts.jsonl` | Generated prompts (one per line) |
| `cluster_versions.json` | OCP, RHOAI, operator versions |

