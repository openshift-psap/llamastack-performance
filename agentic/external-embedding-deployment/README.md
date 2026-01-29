# External Embedding Deployment

This folder contains manifests to deploy LlamaStack with an **external vLLM embedding service** instead of the inline `sentence-transformers` provider.

## Why External Embedding?

- Inline `sentence-transformers` is **not supported in RHOAI production**
- External embedding reduces LlamaStack pod memory footprint

## Components

| File | Description |
|------|-------------|
| `servingruntime-embedding.yaml` | KServe ServingRuntime for vLLM CPU embedding |
| `inferenceservice-embedding.yaml` | KServe InferenceService deploying granite-embedding model |
| `llamastack-distribution-external-embedding.yaml` | LlamaStack configured to use external embedding |

## Deployment Order

```bash
# 1. Deploy embedding model
oc apply -f servingruntime-embedding.yaml 
oc apply -f inferenceservice-embedding.yaml

# 2. Deploy LlamaStack (in llamastack namespace)
oc apply -f llamastack-distribution-external-embedding.yaml
```

## Key Configuration

### LlamaStack Environment Variables

```yaml
# External embedding via vLLM
VLLM_EMBEDDING_URL: "http://granite-embedding-predictor.bench.svc.cluster.local:80/v1"
EMBEDDING_PROVIDER: "vllm-embedding"
EMBEDDING_MODEL: "granite-embedding-external"
EMBEDDING_PROVIDER_MODEL_ID: "granite-embedding"
```

### Why All 4 Variables Are Required

| Variable | Purpose |
|----------|---------|
| `VLLM_EMBEDDING_URL` | URL to external vLLM embedding service |
| `EMBEDDING_PROVIDER` | Enables the `vllm-embedding` provider |
| `EMBEDDING_MODEL` | LlamaStack's internal model identifier |
| `EMBEDDING_PROVIDER_MODEL_ID` | Actual model name on vLLM server |

**Note:** The older vLLM CPU image doesn't properly report model type. Without `EMBEDDING_MODEL` and `EMBEDDING_PROVIDER_MODEL_ID`, LlamaStack auto-discovers the model but registers it as type `llm` instead of `embedding`, causing embedding requests to fail.

## Notes

- Uses vLLM CPU image (`public.ecr.aws/q9t5s3a7/vllm-cpu-release-repo:latest`) - no GPU required
- Model: `ibm-granite/granite-embedding-125m-english`
