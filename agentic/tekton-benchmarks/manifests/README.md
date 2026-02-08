# Tekton Benchmark Manifests

Namespace-agnostic Kubernetes manifests for Tekton pipeline deployments.

## Usage

All manifests have **no hardcoded namespace**. Specify namespace at deploy time:

```bash
oc apply -f postgres.yaml -n my-namespace
oc apply -f llamastack.yaml -n my-namespace
```

## Files

| File | Description |
|------|-------------|
| `postgres.yaml` | PostgreSQL database (Secret, PVC, Deployment, Service) |
| `vllm-servingruntime.yaml` | KServe ServingRuntime for vLLM |
| `vllm-inferenceservice.yaml` | KServe InferenceService for Llama 3.2 3B |
| `mcp-server.yaml` | SDG Docs MCP server (Deployment, Service) |
| `llamastack.yaml` | LlamaStackDistribution with PostgreSQL and OTEL |

## Deployment Order

1. **PostgreSQL** - must be ready before LlamaStack
2. **vLLM** - ServingRuntime first, then InferenceService (can run in parallel with PostgreSQL)
3. **MCP Server** - can run in parallel with others
4. **LlamaStack** - after PostgreSQL and vLLM are ready

## Cross-Namespace References

The `llamastack.yaml` contains environment variables that reference other services:

- `VLLM_URL` - defaults to same namespace (`http://llama-32-3b-instruct-predictor:80/v1`)
- `POSTGRES_HOST` - defaults to same namespace (`postgres`)
- `OTEL_EXPORTER_OTLP_ENDPOINT` - defaults to same namespace (`http://otel-collector:4318`)

If vLLM is in a different namespace (e.g., `bench`), the Tekton task should patch this:
```yaml
VLLM_URL: "http://llama-32-3b-instruct-predictor.bench.svc.cluster.local:80/v1"
```
