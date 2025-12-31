# Chat Completions API Benchmarking

This folder contains scripts, templates, and deployment manifests for benchmarking the **Chat Completions API** (`/v1/chat/completions` endpoint) using [GuideLLM](https://github.com/neuralmagic/guidellm).

## Overview

These benchmarks measure LlamaStack and vLLM performance under various concurrency levels. The tests are designed to:

1. **Compare LlamaStack vs Direct vLLM** - Measure the overhead introduced by LlamaStack
2. **Test different concurrency levels** - From 1 to 128 concurrent requests
3. **Collect comprehensive metrics** - GPU, CPU, memory, and vLLM-specific metrics

## Test Modes

- **LlamaStack Test**: GuideLLM → LlamaStack → vLLM (measures full stack performance)
- **vLLM Direct Test**: GuideLLM → vLLM (baseline, no LlamaStack overhead)

## Prerequisites

1. **OpenShift Cluster** with:
   - GPU nodes (NVIDIA)
   - LlamaStack Operator installed

2. **Namespaces**:
   - `bench` - For vLLM InferenceService and benchmark jobs
   - `llamastack` - For LlamaStack Distribution

3. **ServingRuntime** for vLLM:
   ```bash
   oc apply -f test-deployment/servingruntime-vllm.yaml -n bench
   ```

4. **Secrets**:
   - `hf-token-secret` in `bench` namespace (HuggingFace token for model access)

## Files Structure

```
benchmarking/
├── README.md           # This file
├── scripts/            # Test automation scripts
├── templates/          # YAML templates with variables for customization
└── test-deployment/    # Ready-to-use deployment manifests
```

## Running the Tests

### Option 1: Automated Sequential Tests (Recommended)

The scripts automatically run tests for all concurrency levels (128, 64, 32, 16, 8, 4, 2, 1).

**Test LlamaStack + vLLM:**
```bash
./scripts/run-sequential-llamastack-with-templates.sh <output-directory>
```

**Test vLLM directly (baseline):**
```bash
./scripts/run-sequential-vllm-with-templates.sh <output-directory>
```

See [scripts/README.md](scripts/README.md) for more details.

### Option 2: Manual Single Test

1. **Deploy vLLM InferenceService** and wait for it to be ready:
   ```bash
   oc apply -f test-deployment/inferenceservice.yaml
   ```

2. **(For LlamaStack tests) Deploy LlamaStack** and wait for it to be ready:
   ```bash
   oc apply -f test-deployment/llamastack-distribution.yaml
   ```

3. **Run the benchmark job:**
   ```bash
   oc apply -f test-deployment/guidellm-job.yaml  # or guidellm-job-vllm.yaml
   ```

See [test-deployment/README.md](test-deployment/README.md) for more details.

## Configuration

### Script Configuration

Edit the variables at the top of the scripts:

| Variable | Description | Default |
|----------|-------------|---------|
| `CONCURRENCIES` | Array of concurrency levels to test | `("128" "64" "32" "16" "8" "4" "2" "1")` |
| `NAMESPACE_BENCH` | Namespace for vLLM and jobs | `bench` |
| `NAMESPACE_LLAMASTACK` | Namespace for LlamaStack | `llamastack` |
| `VLLM_SERVICE_NAME` | Name of the InferenceService | `llama-32-3b-instruct` |
| `LLAMASTACK_SERVICE_NAME` | Name of the LlamaStackDistribution | `llamastack-distribution-v2-23` |

## Metrics Collected (Automated Tests)

Each benchmark job collects multiple metrics in parallel:

| Container | Metrics | Description |
|-----------|---------|-------------|
| `benchmark` | GuideLLM results | Throughput, latency, tokens/sec |
| `dcgm-metrics-scraper` | GPU metrics | GPU utilization, memory, power |
| `vllm-metrics-scraper` | vLLM metrics | Queue size, cache hit rate, batch size |
| `prometheus-metrics-scraper` | CPU/Memory | Container resource usage from Prometheus |

## Output Structure (Automated Tests)

After running automated tests, results are saved to:

```
<output-directory>/
├── concurrency-128/
│   ├── configs/                    # Generated YAML files used
│   │   ├── inferenceservice.yaml
│   │   ├── llamastack-distribution.yaml
│   │   └── guidellm-job.yaml
│   └── logs/
│       ├── results-guidellm-*.json      # GuideLLM benchmark results
│       ├── results-dcgm-*.txt           # GPU metrics
│       ├── results-vllm-*.txt           # vLLM metrics
│       ├── llamastack-cpu-metrics-*.jsonl
│       ├── llamastack-memory-metrics-*.jsonl
│       ├── vllm-cpu-metrics-*.jsonl
│       ├── vllm-memory-metrics-*.jsonl
│       ├── guidellm-benchmark.log       # Benchmark container logs
│       ├── vllm-pod.log                 # vLLM server logs
│       └── llamastack-pod-*.log         # LlamaStack logs (per replica)
├── concurrency-64/
│   └── ...
└── ...
```

## Test Flow

The automated scripts follow this flow for each concurrency level:

1. **Cleanup** - Delete any existing resources
2. **Deploy vLLM** - Apply InferenceService and wait for ready
3. **Deploy LlamaStack** - (LlamaStack tests only) Apply distribution and wait for ready
4. **Wait for Prometheus** - 5 minute wait for metrics to accumulate (needed for `rate()` calculations)
5. **Run Benchmark** - Apply GuideLLM job and wait for completion
6. **Save Logs** - Collect all logs and metrics from pods
7. **Cleanup** - Delete resources before next iteration
8. **Cooldown** - 30 second wait before next test

## Customization

### Testing Different LlamaStack Images

Edit `templates/llamastack-distribution-template.yaml`:

```yaml
distribution:
  image: quay.io/aipcc/llama-stack/cpu-ubi9:your-tag-here
```

### Testing Different vLLM Versions

Edit `templates/inferenceservice-template.yaml`:

```yaml
image: quay.io/vllm/vllm-cuda:0.11.0.0  # Change version here
```

### Testing Different Models

Update both templates with the new model name and adjust:
- `storageUri` in InferenceService
- `--model` and `--processor` flags in GuideLLM job

### Testing Multiple LlamaStack Replicas

Edit `templates/llamastack-distribution-template.yaml`:

```yaml
spec:
  replicas: 4  # Change number of replicas
```
