# Locust Test Files

Performance test scripts using a custom Locust fork with enhanced metrics tracking for LlamaStack Responses API and MCP tool calling.

## Custom Locust Fork

These tests require a modified version of Locust that captures MCP and Responses API-specific metrics.

**Fork:** https://github.com/tosokin/locust  
**Branch:** `feature/openai-tool-and-streaming-metrics`

### What's Enhanced

The custom fork adds metric extraction for:

**MCP Tool Metrics:**
- `mcp_call_count` - Number of MCP tool calls per request
- `mcp_tool_names` - Names of tools invoked
- `tools_discovered` - Count of tools discovered via `mcp_list_tools`

**Responses API Metrics:**
- `total_output_items` - Total output array length
- Counts by type: `mcp_list_tools`, `mcp_call`, `file_search_call`, `message`

**Token Usage:**
- `input_tokens` - Prompt tokens consumed
- `output_tokens` - Completion tokens generated
- `total_tokens` - Sum of input + output

**Test Summary:**
- Displays tool call statistics at end of test run
- Shows: Total requests with tools, total tool calls, average tools per request

### Installation

**Option 1: Install from fork (local testing)**
```bash
pip install git+https://github.com/tosokin/locust.git@feature/openai-tool-and-streaming-metrics
pip install openai
```

**Option 2: Build Docker image (cluster testing)**
```bash
git clone https://github.com/tosokin/locust.git
cd locust
git checkout feature/openai-tool-and-streaming-metrics
docker build -f Dockerfile.custom -t locust:mcp-metrics .
# Push to your registry
docker tag locust:mcp-metrics quay.io/YOUR_ORG/locust:mcp-metrics
docker push quay.io/YOUR_ORG/locust:mcp-metrics
```

**Note:** We built a Docker image from this fork and published it to `quay.io/rh-ee-tosokin/locust-openai:v1-mcp-metrics` (public). This image is used for cluster-based testing:
- Simple job examples: [`test-job/`](../test-job/) - Basic Locust jobs without sidecars
- Advanced jobs with metrics sidecars: [`templates/`](../templates/) - Jobs with DCGM, vLLM, and Prometheus scrapers

## Running Tests Locally

**Important:** For local testing, you must port-forward the target service to localhost.

**For LlamaStack tests** (responses_simple, responses_mcp, chat_completions):
```bash
oc port-forward -n llamastack svc/llamastack-rhoai32-new-postgres-minimal-service 8321:8321 &
```

**For vLLM direct test** (vllm_with_wait):
```bash
oc port-forward -n bench svc/llama-32-3b-instruct-predictor 8080:80 &
```

Then use `--host http://localhost:8321` (LlamaStack) or `--host http://localhost:8080` (vLLM) in your locust commands.

---

## Test Files

### locustfile_responses_simple.py

**Purpose:** Test Responses API without tools (baseline overhead measurement).

**What it does:**
- Sends random questions from a predefined pool
- Calls `client.responses.create()` with no tools
- Measures pure Responses API + PostgreSQL write overhead

**Usage:**
```bash
locust -f locustfiles/locustfile_responses_simple.py \
  --headless \
  --users 128 \
  --spawn-rate 128 \
  --run-time 300s \
  --host http://localhost:8321 \
  --only-summary
```

---

### locustfile_responses_mcp.py

**Purpose:** Test Responses API with MCP tool calling (full agentic workflow).

**What it does:**
- Asks: "Use search_parks to find parks in Rhode Island, then get_park_events for upcoming events"
- LlamaStack discovers tools via MCP server
- LlamaStack calls `search_parks(state_code="RI")`
- LlamaStack generates final answer

**Usage:**
```bash
locust -f locustfiles/locustfile_responses_mcp.py \
  --headless \
  --users 128 \
  --spawn-rate 128 \
  --run-time 300s \
  --host http://localhost:8321 \
  --csv results/responses-mcp \
  --only-summary
```

**Prerequisites:**
- MCP server deployed (`nps-mcp-server`)

**Custom outputs:**
- `mcp_metrics.csv` - Per-request MCP metrics

The CSV includes these columns (extracted from response context):
- `timestamp` - Request start time
- `response_time` - Total response time (ms)
- `mcp_call_count` - Number of MCP tool calls
- `mcp_tool_names` - Comma-separated tool names invoked
- `tools_discovered` - Number of tools discovered from MCP server
- `total_output_items` - Total items in Responses API output array
- `input_tokens` - Prompt tokens consumed
- `output_tokens` - Completion tokens generated
- `total_tokens` - Sum of input + output

**Note:** The custom Locust fork provides additional metrics in the `context` dict (like `file_search_count`, `mcp_list_tools_count`, `message_count`) that you can optionally add to your CSV export.

---

### locustfile_chat_completions.py

**Purpose:** Baseline test for Chat Completions endpoint (comparison).

**What it does:**
- Calls standard `/v1/openai/v1/chat/completions` endpoint
- No tools, no state persistence (lightweight)

**Usage:**
```bash
locust -f locustfiles/locustfile_chat_completions.py \
  --headless \
  --users 128 \
  --spawn-rate 128 \
  --run-time 300s \
  --host http://localhost:8321 \
  --csv results/chat-completions \
  --only-summary
```

**Purpose:** Compare Responses API overhead vs Chat Completions.

---

### locustfile_vllm_with_wait.py

**Purpose:** Direct vLLM baseline (bypasses LlamaStack entirely).

**What it does:**
- Calls vLLM's `/v1/chat/completions` directly
- No LlamaStack layer involved

**Usage:**
```bash
locust -f locustfiles/locustfile_vllm_with_wait.py \
  --headless \
  --users 128 \
  --spawn-rate 128 \
  --run-time 300s \
  --host http://localhost:8080 \
  --csv results/vllm-direct \
  --only-summary
```

**Note:** Requires vLLM port-forward (see [Running Tests Locally](#running-tests-locally))

**Purpose:** Measure pure vLLM performance to quantify LlamaStack overhead.

---

## Modifying Tests

### Common Parameters

```bash
--headless              # Run without web UI
--users 128             # Total concurrent users
--spawn-rate 128        # How fast to spawn users (users per second)
--run-time 300s         # Test duration
--host http://...       # Target URL
--csv results/name      # Output file prefix
--only-summary          # Don't print continuous stats (cleaner output)
```

### Customizing Locustfiles

**Change concurrency:**
- Edit `--users` and `--spawn-rate` parameters

**Change test duration:**
- Edit `--run-time` (e.g., `60s`, `5m`, `1h`)

**Change wait time between requests:**
```python
class ResponsesAPISimpleUser(OpenAIUser):
    wait_time = between(1, 2)  # Wait 1-2 seconds between requests
```

**Change questions/prompts:**
- Edit the `questions` list in the locustfile

---

## Related Documentation

- [Main Testing Guide](../TESTING.md) - Complete testing instructions
- [Deployment Guide](../README.md) - Setup instructions
- [Locust Fork](https://github.com/tosokin/locust/tree/feature/openai-tool-and-streaming-metrics) - Source code

