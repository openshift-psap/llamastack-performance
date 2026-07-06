# Daily Progress Log

## July 5, 2026

### Cluster Setup
- Upgraded RHOAI to 3.5-ea1
- Patched DSC: `llamastackoperator: Removed`, `ogx: Managed`
- Scaled down GPU worker node (`gpu-worker-1`)
- Scaled down old bx3d worker node (`worker-1`)
- Provisioned 3 new `cx4-24x48` worker nodes via MachineSet
- Labeled nodes: `node-role=ogx`, `node-role=postgres`, `node-role=loadgen`

### Manifests Created
- `manifests/inference-sim.yaml` — simulator deployment + service + ServiceMonitor
- `manifests/ogxserver-sim.yaml` — OGXServer CR (new `ogx.io/v1beta1` CRD) pointed at simulator
- `manifests/machineset-cx4.yaml` — MachineSet for 3x cx4-24x48 nodes
- Updated `manifests/postgres.yaml` — added nodeSelector, removed stale GPU affinity

### Tasks Created/Updated
- `tasks/deploy-inference-sim.yaml` — deploys simulator via manifest
- `tasks/deploy-ogxserver.yaml` — deploys OGXServer, checks operator readiness, patches nodeSelector
- Updated `tasks/cleanup.yaml` — added OGXServer and simulator cleanup
- Updated `tasks/deploy-postgres.yaml` — switched to `curl` for manifest fetch
- Fixed all tasks to use `curl` instead of `oc apply -f <URL>` (GitHub CDN caching issue)

### RBAC
- Added `ogx.io/ogxservers` permissions to `tekton-deployer` ClusterRole
- Added `datasciencecluster` read permissions for operator readiness check

### Validation
- Deployed simulator via Tekton task — verified it responds to `/v1/chat/completions`
- Tested simulator with `ignore_eos=true` — confirmed exact 1000 token output
- Verified simulator TTFT=1ms, ITL=1ms from Prometheus metrics endpoint
- Deployed PostgreSQL via Tekton task on dedicated node
- Deployed OGXServer via Tekton task on dedicated node
- Tested full flow: curl → OGX → simulator (`/v1/chat/completions`) — 50 tokens, 211ms
- Tested full flow: curl → OGX → simulator (`/v1/responses`) — streaming, 50 tokens
- Verified all pods running on correct dedicated nodes
- Verified Prometheus scraping simulator metrics (TTFT, ITL, E2E histograms visible in OpenShift Metrics UI)

### Next Steps
- Configure Locust load generator for the test matrix
- Implement 3 simulator delay profiles (Fast/Moderate/Realistic)
- Run Phase 1 — Direct Baseline

---

## July 6, 2026

### OTel Instrumentation
- Deployed metrics-only OTel collector (no traces, no Tempo) — new manifest (`otel-collector-metrics-only.yaml`), new task (`deploy-otel-metrics.yaml`)
- Enabled OTel on OGX with OTLP HTTP push to collector (metrics only, traces/logs disabled)
- Validated asyncio metrics flowing: `store_chat_completion`, `try_connect`, `connect`, `close`, `check_provider_health`
- Validated `http_server_duration_milliseconds` for `/v1/chat/completions` and `/v1/responses` endpoints
- Validated `http_server_active_requests` gauge
- Confirmed Prometheus scraping collector via ServiceMonitor (metrics visible in OpenShift Metrics UI)

### OTel Direct Prometheus Export (Not Supported)
- Tested `OTEL_METRICS_EXPORTER=prometheus` approach to expose `/metrics` directly from OGX (no collector)
- Failed: OGX image only has `opentelemetry-exporter-otlp-proto-http` installed, not `opentelemetry-exporter-prometheus`
- OTel auto-initialization crashes with `RuntimeError: Requested component 'otlp_proto_grpc' not found`
- Conclusion: OTel collector is required — cannot bypass it with this image

### Responses API Token Control
- Confirmed `ignore_eos=true` can be passed through Responses API to the simulator (OGX forwards unknown fields)
- Responses API with `ignore_eos=true` returns exactly `max_output_tokens` tokens
- No need for simulator fork (`--deterministic-tokens`) — original simulator works for both API paths

### Next Steps
- Redeploy OGX with corrected OTel config (revert to collector approach)
- Configure simulator with 3 delay profiles (Fast/Moderate/Realistic)
- Set up MLflow experiment tracking
- Validate Locust load generator against simulator
