# Benchmark MCP Server

A configurable MCP server for deterministic LlamaStack performance benchmarking. Generates N tools that return tokenizer-accurate random text, giving you full control over every variable in MCP tool-calling benchmarks.

## Why

Real MCP servers introduce noise: cause retries, tool complexity varies, and response sizes are unpredictable. This server eliminates all that -- every tool call succeeds on the first try, returns exactly the token count you specify, and the number of tools is configurable.

## Configuration

All settings via environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `NUM_TOOLS` | Number of tools to register (`tool_0`, `tool_1`, ...) | `1` |
| `TOOL_RESPONSE_TOKENS` | Exact token count returned by each tool call | `100` |
| `TOOL_DESCRIPTION_TOKENS` | Exact token count for each tool's description (0 = short default) | `0` |
| `TOKENIZER_MODEL` | HuggingFace model name for accurate token counting | `Qwen/Qwen3-VL-30B-A3B-Instruct` |
| `POOL_SIZE` | Number of unique pre-generated responses | `50` |
| `PORT` | Server port | `8000` |

## How It Works

1. On startup, downloads the tokenizer (~10MB) from HuggingFace
2. Samples random token IDs from the vocabulary, decodes to text, and trims to exactly N tokens
3. Pre-generates a pool of responses (and descriptions if configured) at startup
4. Registers N tools via FastMCP, each returning a random response from the pool
5. Tool calls are just `random.choice()` -- sub-millisecond, no processing overhead

## Run Locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

NUM_TOOLS=5 TOOL_RESPONSE_TOKENS=50 TOKENIZER_MODEL=gpt2 python3 server.py
```

## Deploy on OpenShift

```bash
oc apply -f k8s-deployment.yaml -n llamastack-bench
```

Change settings without rebuilding:

```bash
oc set env deployment/benchmark-mcp-server \
  NUM_TOOLS=128 \
  TOOL_RESPONSE_TOKENS=500 \
  TOOL_DESCRIPTION_TOKENS=100 \
  -n llamastack-bench
```

## Use with LlamaStack

The MCP server URL is passed per-request, not configured in LlamaStack:

```bash
curl -X POST http://localhost:8321/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "vllm-inference/qwen3-vl-30b-a3b-instruct",
    "input": "Use tool_0 to retrieve a document and summarize it.",
    "tools": [{
      "type": "mcp",
      "server_label": "benchmark",
      "server_url": "http://benchmark-mcp-server.llamastack-bench.svc.cluster.local:8000/sse"
    }]
  }'
```

## What You Control in Benchmarks

| Knob | Controls |
|------|----------|
| Filler words in prompt | Input token count |
| `NUM_TOOLS` | Tool discovery overhead (context size) |
| `TOOL_DESCRIPTION_TOKENS` | Per-tool context cost in list_tools |
| `TOOL_RESPONSE_TOKENS` | MCP response size per tool call |
| Tool names in prompt | Number of tool calls per request |
| `OUTPUT_TOKENS` (Locust param) | Model output length |
