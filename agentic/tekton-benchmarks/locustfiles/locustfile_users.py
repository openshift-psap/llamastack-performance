"""
User classes for LlamaStack performance testing.
Each user class represents a different type of API consumer.

Selection is done via USER_CLASS env var in locustfile_main.py.
All classes are abstract by default — only the selected one is activated.

Token profile control (ChatCompletionsUser and ResponsesSimpleUser):
    INPUT_TOKENS:  Target input prompt length in tokens (0 = use PROMPT as-is).
                   When > 0, the generate-prompt pipeline task creates unique
                   prompts per user in synthetic_prompts.jsonl (one per line).
    OUTPUT_TOKENS: Exact output tokens per request (0 = no limit, model decides).
                   When > 0, sends max_output_tokens to LlamaStack and passes
                   ignore_eos/stop_token_ids via extra_body to force vLLM to
                   generate exactly this many tokens without stopping at EOS.
    STREAM:        "true" or "false" (default: "true"). Controls whether requests
                   use SSE streaming. When streaming, E2E latency is measured as
                   time from request sent to stream completion.
"""
import os
import sys
import json
import time
import random
import threading
from pathlib import Path
from locust import HttpUser, task, between, constant


SYNTHETIC_PROMPTS_FILENAME = "synthetic_prompts.jsonl"
SYNTHETIC_PROMPT_FILENAME = "synthetic_prompt.txt"

_user_counter = 0
_user_counter_lock = threading.Lock()

CONNECTION_TTL = int(os.environ.get("CONNECTION_TTL_SECONDS", "0"))
STREAM = os.environ.get("STREAM", "true").lower() == "true"

print(f"[locustfile_users] Module loaded. INPUT_TOKENS={os.environ.get('INPUT_TOKENS', '0')}, CONNECTION_TTL={CONNECTION_TTL}s, STREAM={STREAM}, LOCUST_OUTPUT_DIR={os.environ.get('LOCUST_OUTPUT_DIR', '')}", file=sys.stderr, flush=True)


def _load_prompts():
    """Load all prompts from JSONL, falling back to single prompt file, then env var."""
    output_dir = os.environ.get("LOCUST_OUTPUT_DIR", "")

    if output_dir:
        jsonl_file = Path(output_dir) / SYNTHETIC_PROMPTS_FILENAME
        if jsonl_file.exists():
            prompts = []
            for line in jsonl_file.read_text().strip().split("\n"):
                if line.strip():
                    prompts.append(json.loads(line)["prompt"])
            if prompts:
                print(f"[prompt-loader] Loaded {len(prompts)} unique prompts from {jsonl_file}", file=sys.stderr, flush=True)
                return prompts

        txt_file = Path(output_dir) / SYNTHETIC_PROMPT_FILENAME
        if txt_file.exists():
            prompt = txt_file.read_text().strip()
            if prompt:
                print(f"[prompt-loader] Loaded single prompt from {txt_file}: {len(prompt)} chars", file=sys.stderr, flush=True)
                return [prompt]

    fallback = os.environ.get("PROMPT", "What is the capital of France?")
    print(f"[prompt-loader] Using PROMPT env var fallback: {len(fallback)} chars", file=sys.stderr, flush=True)
    return [fallback]


def _get_user_prompt(prompts):
    """Assign a unique prompt to each user via round-robin."""
    global _user_counter
    with _user_counter_lock:
        idx = _user_counter
        _user_counter += 1
    prompt = prompts[idx % len(prompts)]
    print(f"[prompt-loader] User {idx} assigned prompt {idx % len(prompts)}/{len(prompts)} ({len(prompt)} chars)", file=sys.stderr, flush=True)
    return prompt


def _maybe_recycle_connection(user):
    """Close and reopen the HTTP session if CONNECTION_TTL_SECONDS has elapsed.

    K8s Services balance at connection establishment. Without recycling,
    users that connected to pod-1 will send ALL requests there even after
    HPA adds pod-2/3/4. Recycling forces a new TCP connection which the
    Service can route to any ready pod."""
    if CONNECTION_TTL <= 0:
        return
    now = time.monotonic()
    if not hasattr(user, '_conn_created_at'):
        user._conn_created_at = now
        return
    if now - user._conn_created_at >= CONNECTION_TTL:
        user.client.close()
        user._conn_created_at = now


def _consume_sse_stream(response):
    """Consume an SSE stream, return (success, token_count, error_msg).

    Reads all lines from the stream until completion. Counts content delta
    events as tokens. Adds stream consumption time to response.request_meta
    so Locust reports full E2E latency (not just time-to-headers).
    Returns success=True if stream completed normally."""
    token_count = 0
    start_perf_counter = time.perf_counter()
    try:
        for line in response.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8") if isinstance(line, bytes) else line
            if decoded.startswith("data: "):
                data_str = decoded[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    event_type = data.get("type", "")
                    if event_type == "response.output_text.delta":
                        token_count += 1
                    elif "choices" in data:
                        delta = data["choices"][0].get("delta", {}) if data["choices"] else {}
                        if delta.get("content"):
                            token_count += 1
                    elif event_type in ("response.completed", "response.incomplete"):
                        break
                except json.JSONDecodeError:
                    pass
        stream_time_ms = (time.perf_counter() - start_perf_counter) * 1000
        response.request_meta['response_time'] += stream_time_ms
        return True, token_count, None
    except Exception as e:
        stream_time_ms = (time.perf_counter() - start_perf_counter) * 1000
        response.request_meta['response_time'] += stream_time_ms
        return False, token_count, str(e)


class ResponsesMCPUser(HttpUser):
    """Responses API with MCP tool calling — full agentic flow."""
    wait_time = constant(0)
    abstract = True

    def on_start(self):
        self.mcp_server = os.environ.get("MCP_SERVER", "http://sdg-docs-mcp-server.llamastack.svc.cluster.local:8000/sse")
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.prompt = os.environ.get("PROMPT", "What is Kubernetes?")

    @task
    def call_responses_with_mcp(self):
        payload = {
            "model": self.model,
            "input": self.prompt,
            "stream": False,
            "tools": [{
                "type": "mcp",
                "server_label": "deepwiki",
                "server_url": self.mcp_server,
                "require_approval": "never"
            }]
        }

        with self.client.post(
            "/v1/responses",
            json=payload,
            name="responses-mcp",
            catch_response=True
        ) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    if "output" in data or "choices" in data:
                        response.success()
                    else:
                        response.failure(f"Unexpected response format: {list(data.keys())}")
                except json.JSONDecodeError:
                    response.failure("Invalid JSON response")
            else:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")
        _maybe_recycle_connection(self)


class ResponsesSimpleUser(HttpUser):
    """Responses API without tools — measures LlamaStack overhead.

    When INPUT_TOKENS > 0, each request picks a random prompt from
    synthetic_prompts.jsonl to avoid vLLM prefix cache hits.
    When OUTPUT_TOKENS > 0, sets max_output_tokens and passes ignore_eos + stop_token_ids
    via extra_body so LlamaStack forwards them to vLLM's chat completion call.
    Supports streaming (STREAM=true) and non-streaming (STREAM=false) modes.
    """
    wait_time = constant(0)
    abstract = True
    _prompts = None

    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.input_tokens = int(os.environ.get("INPUT_TOKENS", "0"))
        self.output_tokens = int(os.environ.get("OUTPUT_TOKENS", "0"))
        self.default_prompt = os.environ.get("PROMPT", "What is the capital of France?")

        if self.input_tokens > 0 and ResponsesSimpleUser._prompts is None:
            ResponsesSimpleUser._prompts = _load_prompts()
        print(f"[ResponsesSimpleUser] on_start: input_tokens={self.input_tokens}, output_tokens={self.output_tokens}, stream={STREAM}, prompts_available={len(ResponsesSimpleUser._prompts) if ResponsesSimpleUser._prompts else 0}", file=sys.stderr, flush=True)

    @task
    def call_responses_simple(self):
        if ResponsesSimpleUser._prompts:
            with _user_counter_lock:
                global _user_counter
                idx = _user_counter
                _user_counter += 1
            prompt = ResponsesSimpleUser._prompts[idx % len(ResponsesSimpleUser._prompts)]
        else:
            prompt = self.default_prompt
        payload = {
            "model": self.model,
            "input": prompt,
            "stream": STREAM,
        }
        if self.output_tokens > 0:
            payload["max_output_tokens"] = self.output_tokens
            payload["ignore_eos"] = True
            payload["stop_token_ids"] = []

        if STREAM:
            with self.client.post(
                "/v1/responses",
                json=payload,
                name="responses-simple",
                catch_response=True,
                stream=True
            ) as response:
                if response.status_code == 200:
                    success, tokens, err = _consume_sse_stream(response)
                    if success:
                        response.success()
                    else:
                        response.failure(f"Stream error: {err}")
                else:
                    response.failure(f"HTTP {response.status_code}")
        else:
            with self.client.post(
                "/v1/responses",
                json=payload,
                name="responses-simple",
                catch_response=True
            ) as response:
                if response.status_code == 200:
                    try:
                        data = response.json()
                        if "output" in data or "choices" in data:
                            response.success()
                        else:
                            response.failure(f"Unexpected response format: {list(data.keys())}")
                    except json.JSONDecodeError:
                        response.failure("Invalid JSON response")
                else:
                    response.failure(f"HTTP {response.status_code}: {response.text[:200]}")
        _maybe_recycle_connection(self)


class ResponsesMCPBenchmarkUser(HttpUser):
    """Responses API with benchmark MCP server — deterministic tool calling.

    Uses synthetic prompts (filler + tool instruction suffix) from
    generate-mcp-prompt task. Every user calls the same tool for zero variance.
    MCP_SERVER env var points to the benchmark MCP server URL.
    """
    wait_time = constant(0)
    abstract = True
    _prompts = None

    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/qwen3-vl-30b-a3b-instruct")
        self.input_tokens = int(os.environ.get("INPUT_TOKENS", "0"))
        self.output_tokens = int(os.environ.get("OUTPUT_TOKENS", "0"))
        self.mcp_server = os.environ.get("MCP_SERVER", "http://benchmark-mcp-server.llamastack-bench.svc.cluster.local:8000/sse")
        self.default_prompt = os.environ.get("PROMPT", "Use tool_0 to retrieve a document and summarize it.")

        if self.input_tokens > 0 and ResponsesMCPBenchmarkUser._prompts is None:
            ResponsesMCPBenchmarkUser._prompts = _load_prompts()

    @task
    def call_responses_mcp_benchmark(self):
        if ResponsesMCPBenchmarkUser._prompts:
            prompt = random.choice(ResponsesMCPBenchmarkUser._prompts)
        else:
            prompt = self.default_prompt

        payload = {
            "model": self.model,
            "input": prompt,
            "stream": False,
            "tools": [{
                "type": "mcp",
                "server_label": "benchmark",
                "server_url": self.mcp_server,
            }],
        }
        if self.output_tokens > 0:
            payload["max_output_tokens"] = self.output_tokens

        with self.client.post(
            "/v1/responses",
            json=payload,
            name="responses-mcp-benchmark",
            catch_response=True
        ) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    if "output" in data or "choices" in data:
                        response.success()
                    else:
                        response.failure(f"Unexpected response format: {list(data.keys())}")
                except json.JSONDecodeError:
                    response.failure("Invalid JSON response")
            else:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")
        _maybe_recycle_connection(self)


class ChatCompletionsUser(HttpUser):
    """Chat Completions API — works against both vLLM direct and LlamaStack.

    When INPUT_TOKENS > 0, each request picks a random prompt from
    synthetic_prompts.jsonl to avoid vLLM prefix cache hits.
    When OUTPUT_TOKENS > 0, forces exact output length via ignore_eos and stop=null.
    Supports streaming (STREAM=true) and non-streaming (STREAM=false) modes.
    """
    wait_time = constant(0)
    abstract = True
    _prompts = None

    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.input_tokens = int(os.environ.get("INPUT_TOKENS", "0"))
        self.output_tokens = int(os.environ.get("OUTPUT_TOKENS", "0"))
        self.default_prompt = os.environ.get("PROMPT", "What is the capital of France?")

        if self.input_tokens > 0 and ChatCompletionsUser._prompts is None:
            ChatCompletionsUser._prompts = _load_prompts()

    @task
    def call_chat_completions(self):
        if ChatCompletionsUser._prompts:
            with _user_counter_lock:
                global _user_counter
                idx = _user_counter
                _user_counter += 1
            prompt = ChatCompletionsUser._prompts[idx % len(ChatCompletionsUser._prompts)]
        else:
            prompt = self.default_prompt
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "stream": STREAM,
        }
        if self.output_tokens > 0:
            payload["max_tokens"] = self.output_tokens
            payload["stop"] = None
            payload["ignore_eos"] = True

        if STREAM:
            with self.client.post(
                "/v1/chat/completions",
                json=payload,
                name="chat-completions",
                catch_response=True,
                stream=True
            ) as response:
                if response.status_code == 200:
                    success, tokens, err = _consume_sse_stream(response)
                    if success:
                        response.success()
                    else:
                        response.failure(f"Stream error: {err}")
                else:
                    response.failure(f"HTTP {response.status_code}")
        else:
            with self.client.post(
                "/v1/chat/completions",
                json=payload,
                name="chat-completions",
                catch_response=True
            ) as response:
                if response.status_code == 200:
                    try:
                        data = response.json()
                        if "choices" in data:
                            response.success()
                        else:
                            response.failure(f"Unexpected response format: {list(data.keys())}")
                    except json.JSONDecodeError:
                        response.failure("Invalid JSON response")
                else:
                    response.failure(f"HTTP {response.status_code}: {response.text[:200]}")
        _maybe_recycle_connection(self)
