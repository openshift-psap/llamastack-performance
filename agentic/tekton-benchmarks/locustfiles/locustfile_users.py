"""
User classes for LlamaStack performance testing.
Each user class represents a different type of API consumer.

Selection is done via USER_CLASS env var in locustfile_main.py.
All classes are abstract by default — only the selected one is activated.

Token profile control (ChatCompletionsUser and ResponsesSimpleUser):
    INPUT_TOKENS:  Target input prompt length in tokens (0 = use PROMPT as-is)
    OUTPUT_TOKENS: Max output tokens per request (0 = no limit, model decides)
"""
import os
import json
from locust import HttpUser, task, between


def generate_synthetic_prompt(target_tokens):
    """Generate a prompt of approximately target_tokens length.
    Uses ~4 chars per token as a rough estimate for Llama tokenizers."""
    chars_per_token = 4
    base = "Repeat and elaborate on the following text with detailed analysis. "
    filler = "The quick brown fox jumps over the lazy dog near the river bank. "
    needed_chars = target_tokens * chars_per_token
    prompt = base + (filler * ((needed_chars // len(filler)) + 1))
    return prompt[:needed_chars]


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

    Supports INPUT_TOKENS and OUTPUT_TOKENS env vars for token profile control.
    """
    wait_time = between(1, 3)
    abstract = True

    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.prompt = os.environ.get("PROMPT", "What is the capital of France?")
        self.input_tokens = int(os.environ.get("INPUT_TOKENS", "0"))
        self.output_tokens = int(os.environ.get("OUTPUT_TOKENS", "0"))

        if self.input_tokens > 0:
            self.prompt = generate_synthetic_prompt(self.input_tokens)
            print(f"Generated synthetic prompt: ~{self.input_tokens} tokens ({len(self.prompt)} chars)")

    @task
    def call_responses_simple(self):
        payload = {
            "model": self.model,
            "input": self.prompt
        }
        if self.output_tokens > 0:
            payload["max_output_tokens"] = self.output_tokens
        
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

    Token profile control via environment variables:
        INPUT_TOKENS:  Target input prompt length in tokens (0 = use PROMPT as-is)
        OUTPUT_TOKENS: Max output tokens per request (0 = no limit, model decides)

    For fixed profiles, set both. For variable profiles, the values are used as-is
    (no distribution sampling — each request gets the same token count).
    """
    wait_time = between(1, 3)
    abstract = True

    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.prompt = os.environ.get("PROMPT", "What is the capital of France?")
        self.input_tokens = int(os.environ.get("INPUT_TOKENS", "0"))
        self.output_tokens = int(os.environ.get("OUTPUT_TOKENS", "0"))

        if self.input_tokens > 0:
            self.prompt = generate_synthetic_prompt(self.input_tokens)
            print(f"Generated synthetic prompt: ~{self.input_tokens} tokens ({len(self.prompt)} chars)")

        if self.output_tokens > 0:
            print(f"Output tokens capped at: {self.output_tokens}")

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
