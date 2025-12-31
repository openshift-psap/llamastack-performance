# Benchmark Templates

This folder contains YAML templates used by the automated scripts in [../scripts/](../scripts/).

Templates use variables like `$CONCURRENCY`, `$VLLM_SERVICE_NAME` that get substituted by `envsubst` when the scripts process them.

## InferenceService Template

### inferenceservice-template.yaml

Deploys vLLM model server using KServe InferenceService.

```yaml
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: llama-32-3b-instruct
  namespace: bench
spec:
  predictor:
    model:
      modelFormat:
        name: vLLM
      runtime: llama-32-3b-instruct
      storageUri: oci://quay.io/redhat-ai-services/modelcar-catalog:llama-3.2-3b-instruct
      image: quay.io/vllm/vllm-cuda:0.11.0.0
      resources:
        limits:
          nvidia.com/gpu: "1"
```

**Key configurations:**
- `--dtype=half` - Use half precision for faster inference
- `--max-model-len=20000` - Maximum context length
- `--gpu-memory-utilization=0.95` - Use 95% of GPU memory
- `--enable-chunked-prefill` - Enable chunked prefill for better throughput
- `--enable-auto-tool-choice` - Enable tool calling support

## LlamaStack Templates

### llamastack-distribution-template.yaml

Standard LlamaStack deployment using the default server configuration.

```yaml
apiVersion: llamastack.io/v1alpha1
kind: LlamaStackDistribution
metadata:
  name: llamastack-distribution-v2-23
  namespace: llamastack
spec:
  replicas: 1
  server:
    containerSpec:
      env:
        - name: VLLM_URL
          value: 'http://llama-32-3b-instruct-predictor.bench.svc.cluster.local:80/v1'
        - name: INFERENCE_MODEL
          value: llama-32-3b-instruct
    distribution:
      image: quay.io/aipcc/llama-stack/cpu-ubi9:rhoai-3.0-1761751927
```

### llamastack-distribution-template-uvicorn.yaml

LlamaStack deployment with custom uvicorn configuration for testing multiple workers.

```yaml
spec:
  server:
    containerSpec:
      command: ["uvicorn", "llama_stack.core.server.server:create_app", 
                "--host", "0.0.0.0", "--port", "8321", "--workers", "2", "--factory"]
```

**Difference from standard template:**
- Custom uvicorn command with 2 workers

## Benchmark Job Templates

### guidellm-job-template.yaml

Benchmark job that tests **LlamaStack + vLLM** (full stack).

**Target:** LlamaStack service
```yaml
--target http://$LLAMASTACK_SERVICE_NAME-service.$NAMESPACE_LLAMASTACK.svc.cluster.local:8321/v1/openai
```

**Containers:**
| Container | Purpose |
|-----------|---------|
| `benchmark` | Runs GuideLLM benchmark tool |
| `dcgm-metrics-scraper` | Collects GPU metrics from DCGM exporter |
| `vllm-metrics-scraper` | Collects vLLM internal metrics |
| `prometheus-metrics-scraper` | Collects CPU/memory metrics from Prometheus |
| `sidecar` | Keeps pod alive for log collection |

**GuideLLM parameters:**
```bash
guidellm benchmark \
  --target http://...:8321/v1/openai \
  --model vllm-inference/llama-32-3b-instruct \
  --processor meta-llama/Llama-3.2-3B-Instruct \
  --rate-type concurrent \
  --rate "$CONCURRENCY" \
  --max-seconds 300 \
  --data "prompt_tokens=256,output_tokens=128"
```

### guidellm-vllm-job-template.yaml

Benchmark job that tests **vLLM directly** (no LlamaStack).

**Target:** vLLM service directly
```yaml
--target http://$VLLM_SERVICE_NAME-predictor.$NAMESPACE_BENCH.svc.cluster.local/v1
```

Same containers as the LlamaStack job but targeting vLLM directly for baseline comparison.

## Template Variables

| Variable | Description | Example Value |
|----------|-------------|---------------|
| `$CONCURRENCY` | Number of concurrent requests | `32` |
| `$VLLM_SERVICE_NAME` | vLLM InferenceService name | `llama-32-3b-instruct` |
| `$LLAMASTACK_SERVICE_NAME` | LlamaStack distribution name | `llamastack-distribution-v2-23` |
| `$NAMESPACE_BENCH` | Namespace for vLLM and jobs | `bench` |
| `$NAMESPACE_LLAMASTACK` | Namespace for LlamaStack | `llamastack` |

## Notes

### Cluster-Specific Configuration

The `prometheus-metrics-scraper` container in job templates has a hardcoded Thanos URL that may need to be updated for your cluster.

To find your cluster's Thanos URL:
```bash
oc get route thanos-querier -n openshift-monitoring -o jsonpath='{.spec.host}'
```

