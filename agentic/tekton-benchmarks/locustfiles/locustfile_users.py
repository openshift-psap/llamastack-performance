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
"""
import os
import sys
import json
import random
import threading
from pathlib import Path
from locust import HttpUser, task, between


SYNTHETIC_PROMPTS_FILENAME = "synthetic_prompts.jsonl"
SYNTHETIC_PROMPT_FILENAME = "synthetic_prompt.txt"

_user_counter = 0
_user_counter_lock = threading.Lock()

print(f"[locustfile_users] Module loaded. INPUT_TOKENS={os.environ.get('INPUT_TOKENS', '0')}, LOCUST_OUTPUT_DIR={os.environ.get('LOCUST_OUTPUT_DIR', '')}", file=sys.stderr, flush=True)


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


class ResponsesMCPUser(HttpUser):
    """Responses API with MCP tool calling — full agentic flow."""
    wait_time = between(1, 3)
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


class ResponsesSimpleUser(HttpUser):
    """Responses API without tools — measures LlamaStack overhead.

    When INPUT_TOKENS > 0, each request picks a random prompt from
    synthetic_prompts.jsonl to avoid vLLM prefix cache hits.
    When OUTPUT_TOKENS > 0, sets max_output_tokens and passes ignore_eos + stop_token_ids
    via extra_body so LlamaStack forwards them to vLLM's chat completion call.
    """
    wait_time = between(1, 3)
    abstract = True
    _prompts = None

    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.input_tokens = int(os.environ.get("INPUT_TOKENS", "0"))
        self.output_tokens = int(os.environ.get("OUTPUT_TOKENS", "0"))
        self.default_prompt = os.environ.get("PROMPT", "What is the capital of France?")

        if self.input_tokens > 0 and ResponsesSimpleUser._prompts is None:
            ResponsesSimpleUser._prompts = _load_prompts()
        print(f"[ResponsesSimpleUser] on_start: input_tokens={self.input_tokens}, output_tokens={self.output_tokens}, prompts_available={len(ResponsesSimpleUser._prompts) if ResponsesSimpleUser._prompts else 0}", file=sys.stderr, flush=True)

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
            "input": prompt
        }
        if self.output_tokens > 0:
            payload["max_output_tokens"] = self.output_tokens
            payload["ignore_eos"] = True
            payload["stop_token_ids"] = []

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


class ChatCompletionsUser(HttpUser):
    """Chat Completions API — works against both vLLM direct and LlamaStack.

    When INPUT_TOKENS > 0, each request picks a random prompt from
    synthetic_prompts.jsonl to avoid vLLM prefix cache hits.
    When OUTPUT_TOKENS > 0, forces exact output length via ignore_eos and stop=null.
    """
    wait_time = between(1, 3)
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
            ]
        }
        if self.output_tokens > 0:
            payload["max_tokens"] = self.output_tokens
            payload["stop"] = None
            payload["ignore_eos"] = True

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
