# Manual Test Deployment

Ready-to-use manifests for manual benchmarking. Unlike the automated scripts that use templates, these files can be applied directly with `oc apply`.

## Files

| File | Description |
|------|-------------|
| `servingruntime-vllm.yaml` | ServingRuntime definition for vLLM |
| `inferenceservice.yaml` | Deploys vLLM model server |
| `llamastack-distribution.yaml` | Deploys LlamaStack |
| `guidellm-job.yaml` | Benchmark job targeting LlamaStack |
| `guidellm-job-vllm.yaml` | Benchmark job targeting vLLM directly |

## Prerequisites

**HuggingFace Token Secret** - GuideLLM uses the tokenizer to generate prompts with specific token lengths:
```bash
oc create secret generic hf-token-secret --from-literal=token=<your-hf-token> -n bench
```

## Deployment Order

### Option 1: Test LlamaStack

```bash
# 1. Deploy vLLM model server
oc apply -f servingruntime-vllm.yaml
oc apply -f inferenceservice.yaml
# Wait for vLLM pod to be ready

# 2. Deploy LlamaStack
oc apply -f llamastack-distribution.yaml
# Wait for LlamaStack pod to be ready

# 3. Run benchmark
oc apply -f guidellm-job.yaml
```

### Option 2: Test vLLM Directly

```bash
# 1. Deploy vLLM model server
oc apply -f servingruntime-vllm.yaml
oc apply -f inferenceservice.yaml
# Wait for vLLM pod to be ready

# 2. Run benchmark
oc apply -f guidellm-job-vllm.yaml
```

## Configuration

Before applying, review and modify these values in the manifests:

### InferenceService (`inferenceservice.yaml`)

| Parameter | Current Value | Description |
|-----------|---------------|-------------|
| `namespace` | `bench` | Target namespace for vLLM |
| `image` | `quay.io/vllm/vllm-cuda:0.11.0.0` | vLLM container image |
| `storageUri` | `oci://quay.io/redhat-ai-services/modelcar-catalog:llama-3.2-3b-instruct` | Model location |
| `gpu` | `1` | Number of GPUs |

### LlamaStack (`llamastack-distribution.yaml`)

| Parameter | Current Value | Description |
|-----------|---------------|-------------|
| `namespace` | `llamastack` | Target namespace |
| `replicas` | `4` | Number of LlamaStack replicas |
| `VLLM_URL` | `http://llama-32-3b-instruct-predictor.bench.svc.cluster.local:80/v1` | vLLM endpoint |
| `image` | `quay.io/aipcc/llama-stack/cpu-ubi9:rhoai-3.0-...` | LlamaStack image |

### LlamaStack with Uvicorn (`llamastack-distribution-template-uvicorn.yaml`)

| Parameter | Current Value | Description |
|-----------|---------------|-------------|
| `--workers` | `2` | Number of uvicorn worker processes |

### Benchmark Jobs

| Parameter | Current Value | Description |
|-----------|---------------|-------------|
| `rate` | `128` | Concurrent requests |
| `max-seconds` | `300` | Test duration (5 minutes) |
| `prompt_tokens` | `256` | Input token count |
| `output_tokens` | `128` | Output token count |

## Job Structure

The benchmark jobs include multiple containers:

1. **benchmark** - Runs GuideLLM, saves results to `/output/`
2. **dcgm-metrics-scraper** - Collects GPU metrics from DCGM exporter
3. **vllm-metrics-scraper** - Collects vLLM internal metrics
4. **prometheus-metrics-scraper** - Collects CPU/memory metrics (LlamaStack job only)
5. **sidecar** - Keeps pod alive for result retrieval

## Retrieving Results

After the job completes, copy results from the sidecar container:

```bash
# Get the pod name
POD=$(oc get pods -n bench -l job-name=guidellm-llamastack-4replicas-concurrency-128 -o jsonpath='{.items[0].metadata.name}')

# Copy all results
oc cp bench/$POD:/output/ ./results/ -c sidecar

# View what was collected
ls -la ./results/
```

Expected output files:
- `results-guidellm-*.json` - GuideLLM benchmark results
- `results-dcgm-*.txt` - GPU metrics
- `results-vllm-*.txt` - vLLM metrics
- `llamastack-cpu-metrics-*.jsonl` - LlamaStack CPU usage
- `llamastack-memory-metrics-*.jsonl` - LlamaStack memory usage

## Cleanup

```bash
# Delete job (automatically deletes after 24h via ttlSecondsAfterFinished)
oc delete job guidellm-llamastack-4replicas-concurrency-128 -n bench

# Delete LlamaStack
oc delete llamastackdistribution llamastack-distribution-v2-23 -n llamastack

# Delete vLLM
oc delete inferenceservice llama-32-3b-instruct -n bench
oc delete servingruntime llama-32-3b-instruct -n bench
```

## Notes

- **Thanos URL**: The Prometheus metrics scraper in `guidellm-job.yaml` has a hardcoded Thanos Querier URL. Update it to match your cluster:
  ```
  https://thanos-querier-openshift-monitoring.apps.<your-cluster>/api/v1/query
  ```

