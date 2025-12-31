# Automated Benchmark Scripts

This folder contains scripts for running automated concurrency tests across multiple levels.

## Available Scripts

| Script | Description |
|--------|-------------|
| `run-sequential-llamastack-with-templates.sh` | Tests LlamaStack + vLLM (full stack performance) |
| `run-sequential-vllm-with-templates.sh` | Tests vLLM directly (baseline, no LlamaStack) |

## Usage

```bash
./<script-name>.sh <output-directory>
```

**Examples:**
```bash
# Test LlamaStack + vLLM
./run-sequential-llamastack-with-templates.sh ./results/llamastack-rhoai32

# Test vLLM directly (baseline)
./run-sequential-vllm-with-templates.sh ./results/vllm-baseline
```

## How the Scripts Work

### 1. Configuration

The scripts define which concurrency levels to test and the service names:

```bash
CONCURRENCIES=("128" "64" "32" "16" "8" "4" "2" "1")
NAMESPACE_BENCH="bench"
NAMESPACE_LLAMASTACK="llamastack"
VLLM_SERVICE_NAME="llama-32-3b-instruct"
LLAMASTACK_SERVICE_NAME="llamastack-distribution-v2-23"
```

### 2. Template Processing

Templates from [../templates/](../templates/) are processed and saved to `<output-directory>/concurrency-N/configs/`:

**LlamaStack + vLLM script:**
- `inferenceservice-template.yaml` → vLLM deployment
- `llamastack-distribution-template.yaml` → LlamaStack deployment
- `guidellm-job-template.yaml` → Benchmark job

**vLLM direct script:**
- `inferenceservice-template.yaml` → vLLM deployment
- `guidellm-vllm-job-template.yaml` → Benchmark job

```bash
local test_dir="$BASE_DIR/concurrency-$concurrency"
mkdir -p "$test_dir/configs"

# Process templates and save to configs folder
substitute_template "$VLLM_TEMPLATE" "$test_dir/configs/inferenceservice.yaml"
substitute_template "$LLAMASTACK_TEMPLATE" "$test_dir/configs/llamastack-distribution.yaml"
substitute_template "$GUIDELLM_TEMPLATE" "$test_dir/configs/guidellm-job.yaml" "$concurrency"
```

The `substitute_template` function uses `envsubst` to replace variables like `$CONCURRENCY` with actual values.

### 3. Deploy vLLM

The script applies the processed config and waits for vLLM to be ready:

```bash
oc apply -f "$test_dir/configs/inferenceservice.yaml"
# wait for vLLM to be ready (timeout 300 seconds)
```

### 4. Deploy LlamaStack (full stack tests only)

For LlamaStack tests, the script also deploys LlamaStackDistribution:

```bash
oc apply -f "$test_dir/configs/llamastack-distribution.yaml"
# wait for LlamaStack to be ready (timeout 300 seconds)
```

### 5. Prometheus Warmup

Before running benchmarks, the script waits 5 minutes for Prometheus to accumulate data:

```bash
log "Waiting 5 minutes for Prometheus metrics to accumulate historical data..."
sleep 300
```

This is needed because Prometheus `rate()` queries require historical data points.

### 6. Run Benchmark Job

The GuideLLM benchmark job is deployed and the script waits for completion:

```bash
oc apply -f "$test_dir/configs/guidellm-job.yaml"
# wait for job to complete (timeout 900 seconds / 15 minutes)
```

### 7. Log Collection

After each benchmark, logs are collected from all containers and saved locally:

```bash
save_logs() {
    local log_dir="$BASE_DIR/concurrency-$concurrency/logs"
    mkdir -p "$log_dir"
    
    # GuideLLM benchmark output
    oc logs job/$job_name -c benchmark > "$log_dir/guidellm-benchmark.log"
    
    # GPU metrics from DCGM exporter
    oc logs job/$job_name -c dcgm-metrics-scraper > "$log_dir/dcgm-metrics.log"
    
    # vLLM metrics
    oc logs job/$job_name -c vllm-metrics-scraper > "$log_dir/vllm-metrics.log"
    
    # Prometheus CPU/memory metrics
    oc logs job/$job_name -c prometheus-metrics-scraper > "$log_dir/prometheus-metrics.log"
    
    # Copy JSON results from job pod to local
    local pod_name=$(oc get pods -n $NAMESPACE_BENCH -l job-name=$job_name -o jsonpath='{.items[0].metadata.name}')
    oc cp $NAMESPACE_BENCH/$pod_name:/output/ "$log_dir/" -c sidecar
    
    # vLLM server logs
    oc logs $vllm_pod -c kserve-container > "$log_dir/vllm-pod.log"
    
    # LlamaStack logs (all replicas)
    for pod in $llamastack_pods; do
        oc logs $pod -c llama-stack > "$log_dir/llamastack-pod-${index}.log"
    done
}
```

### 9. Cleanup Between Tests

Resources are deleted before each new test to ensure clean state:

```bash
cleanup() {
    oc delete job -n $NAMESPACE_BENCH --all --ignore-not-found=true
    oc delete llamastackdistribution -n $NAMESPACE_LLAMASTACK $LLAMASTACK_SERVICE_NAME --ignore-not-found=true
    oc delete inferenceservice -n $NAMESPACE_BENCH $VLLM_SERVICE_NAME --ignore-not-found=true
    sleep 10
}
```

### 10. Loop to Next Concurrency

After cleanup, the script waits 30 seconds then starts the next concurrency level:

```bash
for concurrency in "${CONCURRENCIES[@]}"; do
    # ... deploy, benchmark, collect logs, cleanup ...
    
    log "Cooldown period before next test..."
    sleep 30
done
```

## Estimated Runtime

Each concurrency level takes approximately:
- 5 min deployment + ready wait
- 5 min Prometheus warmup
- 5 min benchmark
- 1 min cleanup + cooldown

**Total for 8 concurrency levels: ~2 hours per script**

## Output

Results are saved to your specified output directory. See the main [README.md](../README.md) for output structure details.

