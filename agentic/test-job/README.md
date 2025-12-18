# Kubernetes Job Examples

Example Kubernetes Jobs for cluster-based Locust performance testing.

## Files

### Chat Completions Test

**configmap-chat-completions.yaml**  
ConfigMap embedding the Chat Completions locustfile (based on [`locustfile_chat_completions.py`](../locustfiles/locustfile_chat_completions.py)). Contains Python code that tests LlamaStack's `/v1/openai/v1/chat/completions` endpoint with a synthetic 256-token prompt. Creates ConfigMap named `locust-test-files` in `bench` namespace.

**job-chat-completions.yaml**  
Kubernetes Job that runs 128 concurrent Locust users for 300 seconds. References the ConfigMap above, mounts it at `/tests`, and exports results to `/output`. Job name: `locust-chat-completions-c128`.

### Responses API Simple Test

**configmap-responses-simple.yaml**  
ConfigMap embedding the Responses API locustfile (based on [`locustfile_responses_simple.py`](../locustfiles/locustfile_responses_simple.py)) without tools. Tests pure Responses API with random questions from a pool. Creates ConfigMap named `locust-responses-simple-test-files` in `bench` namespace.

**job-responses-simple.yaml**  
Kubernetes Job that runs 128 concurrent Locust users testing Responses API without MCP tools. Tests stateful API operations (conversation persistence) without the complexity of tool calling. Job name: `locust-responses-simple-c128`.

### Responses API with MCP Test

**configmap-responses-mcp.yaml**  
ConfigMap embedding the Responses API locustfile (based on [`locustfile_responses_mcp.py`](../locustfiles/locustfile_responses_mcp.py)) with MCP tool calling logic. Includes event listener that captures MCP metrics (tool calls, tokens) and exports to `mcp_metrics.csv`. Creates ConfigMap named `locust-mcp-test-files` in `bench` namespace.

**job-responses-mcp.yaml**  
Kubernetes Job that runs 128 concurrent Locust users testing the Responses API with National Parks MCP tools. Sets `LOCUST_OUTPUT_DIR=/output` for metrics export. Job name: `locust-mcp-c128`.

### vLLM Direct Test

**configmap-vllm-direct.yaml**  
ConfigMap embedding the vLLM direct locustfile (based on [`locustfile_vllm_with_wait.py`](../locustfiles/locustfile_vllm_with_wait.py)). Bypasses LlamaStack entirely and calls vLLM's OpenAI-compatible endpoint directly for baseline performance measurement. Creates ConfigMap named `locust-vllm-test-files` in `bench` namespace.

**job-vllm-direct.yaml**  
Kubernetes Job that runs 128 concurrent Locust users directly against vLLM predictor service. Used to calculate LlamaStack overhead by comparing with Chat Completions test results. Job name: `locust-vllm-direct-c128`.

## Test Descriptions

### Chat Completions Test

**What it does:**
- Sends 128 concurrent requests to LlamaStack for 5 minutes
- Uses a synthetic 256-token prompt (repeated text from Pride & Prejudice)
- Requests 128 output tokens per completion
- Measures throughput, latency, and token generation performance

**Purpose:** Baseline performance test for LlamaStack's Chat Completions endpoint (no tools, no state)

### Responses API Simple Test

**What it does:**
- Sends 128 concurrent requests to LlamaStack Responses API for 5 minutes
- Uses random questions from a pool (science/technology topics)
- No tools, just Q&A through Responses API

**Purpose:** Measure Responses API overhead vs Chat Completions (both without tools)

### Responses API with MCP Test

**What it does:**
- Sends requests asking to search Rhode Island parks and get events
- LlamaStack discovers 5 tools from the MCP server
- LlamaStack autonomously calls `search_parks`
- Generates final natural language response

**Outputs:**
- Standard Locust CSVs (stats, history)
- `mcp_metrics.csv` - Tool call counts, names, tokens per request

**Prerequisites:** MCP server deployed (`nps-mcp-server`)

**Purpose:** Stress test stateful operations, tool calling, and database writes

### vLLM Direct Test

**What it does:**
- Sends 128 concurrent requests directly to vLLM for 5 minutes
- Uses the same synthetic 256-token prompt as Chat Completions test
- Adds 1-2 second wait time between requests to smooth load
- No state persistence, no LlamaStack overhead

**Purpose:** Establish baseline performance to calculate LlamaStack overhead

## Usage

### Deploy Tests

Deploy ConfigMap first, then the Job:

**Chat Completions:**
```bash
oc apply -f configmap-chat-completions.yaml
oc apply -f job-chat-completions.yaml
```

**Responses API Simple:**
```bash
oc apply -f configmap-responses-simple.yaml
oc apply -f job-responses-simple.yaml
```

**Responses API with MCP:**
```bash
oc apply -f configmap-responses-mcp.yaml
oc apply -f job-responses-mcp.yaml
```

**vLLM direct:**
```bash
oc apply -f configmap-vllm-direct.yaml
oc apply -f job-vllm-direct.yaml
```

### Before Running

**Update service names to match your deployment:**

Edit the `--host` argument in each `job-*.yaml` file:
```yaml
args:
  - "--host"
  - "http://YOUR-SERVICE-NAME.namespace.svc.cluster.local:PORT"
```

### Monitor

```bash
# Watch job progress
oc get jobs -n bench -w

# View logs
oc logs -f job/locust-chat-completions-c128 -n bench
```

### Collect Results

```bash
# Find the pod
POD=$(oc get pods -n bench -l job-name=locust-chat-completions-c128 -o jsonpath='{.items[0].metadata.name}')

# Copy results
oc cp bench/$POD:/output/ ./results/

# Or view directly
oc exec -n bench $POD -- ls -la /output/
```

**Files created:**
- `locust-results_stats.csv` - Summary statistics
- `locust-results_stats_history.csv` - Timeline
- `mcp_metrics.csv` - MCP metrics (only for MCP test)

## Custom Locust Image

These jobs use a custom Locust image built from https://github.com/tosokin/locust/tree/feature/openai-tool-and-streaming-metrics

**Image:** `quay.io/rh-ee-tosokin/locust-openai:v1-mcp-metrics` (public)

**What's in the image:**
- Locust with enhancements to extract MCP metrics (tool calls, tokens, output structure)
- OpenAI Python client for making Responses API calls

**To build your own or understand the modifications:**
See [locustfiles/README.md](../locustfiles/README.md) for fork details and build instructions

## Customizing

### Change Concurrency

Edit the Job args in `job-*.yaml`:
```yaml
- "--users"
- "128"  # Change this
- "--spawn-rate"  
- "128"  # And this
```

### Change Duration

```yaml
- "--run-time"
- "300s"  # 5 minutes - adjust as needed
```

### Change Test Logic

Edit the `locustfile.py` content in the corresponding `configmap-*.yaml` file.

## Related

- [Locust Test Files](../locustfiles/) - Python scripts and fork documentation
- [Deployment Guide](../README.md) - Setup instructions
