"""
User classes for LlamaStack performance testing.
Each user class represents a different type of API consumer.

Selection is done via USER_CLASS env var in locustfile_main.py.
All classes are abstract by default — only the selected one is activated.
"""
import os
import json
from locust import HttpUser, task, between


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
    """Responses API without tools — measures LlamaStack overhead."""
    wait_time = between(1, 3)
    abstract = True
    
    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.prompt = os.environ.get("PROMPT", "What is the capital of France?")
        
    @task
    def call_responses_simple(self):
        payload = {
            "model": self.model,
            "input": self.prompt
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
    """Chat Completions API — works against both vLLM direct and LlamaStack."""
    wait_time = between(1, 3)
    abstract = True
    
    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.prompt = os.environ.get("PROMPT", "What is the capital of France?")
        
    @task
    def call_chat_completions(self):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": self.prompt}
            ]
        }
        
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
