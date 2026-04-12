"""
User classes for LlamaStack performance testing.
Each user class represents a different type of API consumer.

Selection is done via USER_CLASS env var in locustfile_main.py.
All classes are abstract by default — only the selected one is activated.

Token profile control (ChatCompletionsUser and ResponsesSimpleUser):
    INPUT_TOKENS:  Target input prompt length in tokens (0 = use PROMPT as-is).
                   When > 0, the generate-prompt pipeline task creates an exact
                   token-count prompt using the model's tokenizer and writes it
                   to synthetic_prompt.txt in the workspace.
    OUTPUT_TOKENS: Exact output tokens per request (0 = no limit, model decides).
                   When > 0, sends max_output_tokens to LlamaStack and passes
                   ignore_eos/stop_token_ids via extra_body to force vLLM to
                   generate exactly this many tokens without stopping at EOS.
"""
import os
import json
from pathlib import Path
from locust import HttpUser, task, between


SYNTHETIC_PROMPT_FILENAME = "synthetic_prompt.txt"

import sys
print(f"[locustfile_users] Module loaded. INPUT_TOKENS={os.environ.get('INPUT_TOKENS', '0')}, LOCUST_OUTPUT_DIR={os.environ.get('LOCUST_OUTPUT_DIR', '')}", file=sys.stderr, flush=True)


def _load_prompt():
    """Load the prompt, preferring a tokenizer-generated file from the workspace."""
    import sys
    output_dir = os.environ.get("LOCUST_OUTPUT_DIR", "")
    print(f"[prompt-loader] LOCUST_OUTPUT_DIR={output_dir!r}, INPUT_TOKENS={os.environ.get('INPUT_TOKENS', '0')}", file=sys.stderr, flush=True)
    if output_dir:
        prompt_file = Path(output_dir) / SYNTHETIC_PROMPT_FILENAME
        exists = prompt_file.exists()
        print(f"[prompt-loader] Checking {prompt_file} exists={exists}", file=sys.stderr, flush=True)
        if exists:
            prompt = prompt_file.read_text().strip()
            if prompt:
                print(f"[prompt-loader] Loaded synthetic prompt: {len(prompt)} chars", file=sys.stderr, flush=True)
                return prompt

    fallback = os.environ.get("PROMPT", "What is the capital of France?")
    print(f"[prompt-loader] Using PROMPT env var fallback: {len(fallback)} chars", file=sys.stderr, flush=True)
    return fallback


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

    When INPUT_TOKENS > 0, reads the tokenizer-generated prompt from the workspace.
    When OUTPUT_TOKENS > 0, sets max_output_tokens and passes ignore_eos + stop_token_ids
    via extra_body so LlamaStack forwards them to vLLM's chat completion call.
    """
    wait_time = between(1, 3)
    abstract = True

    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.input_tokens = int(os.environ.get("INPUT_TOKENS", "0"))
        self.output_tokens = int(os.environ.get("OUTPUT_TOKENS", "0"))
        print(f"[ResponsesSimpleUser] on_start: input_tokens={self.input_tokens}, output_tokens={self.output_tokens}", file=sys.stderr, flush=True)

        if self.input_tokens > 0:
            self.prompt = _load_prompt()
        else:
            self.prompt = os.environ.get("PROMPT", "What is the capital of France?")
        print(f"[ResponsesSimpleUser] prompt length: {len(self.prompt)} chars", file=sys.stderr, flush=True)

    @task
    def call_responses_simple(self):
        payload = {
            "model": self.model,
            "input": self.prompt
        }
        if self.output_tokens > 0:
            payload["max_output_tokens"] = self.output_tokens
            payload["extra_body"] = {
                "ignore_eos": True,
                "stop_token_ids": [],
            }

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

    When INPUT_TOKENS > 0, reads the tokenizer-generated prompt from the workspace.
    When OUTPUT_TOKENS > 0, forces exact output length via ignore_eos and stop=null.
    """
    wait_time = between(1, 3)
    abstract = True

    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.input_tokens = int(os.environ.get("INPUT_TOKENS", "0"))
        self.output_tokens = int(os.environ.get("OUTPUT_TOKENS", "0"))

        if self.input_tokens > 0:
            self.prompt = _load_prompt()
        else:
            self.prompt = os.environ.get("PROMPT", "What is the capital of France?")

    @task
    def call_chat_completions(self):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": self.prompt}
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
