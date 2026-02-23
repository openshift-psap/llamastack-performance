"""
User classes for LlamaStack performance testing.
Each user class represents a different type of API consumer.
"""
import os
import json
from locust import HttpUser, task, between


class ResponsesMCPUser(HttpUser):
    """
    User that calls the Responses API with MCP tool calling.
    This simulates a client using LlamaStack's agentic capabilities.
    """
    wait_time = between(1, 3)
    
    def on_start(self):
        """Called when a user starts - setup configuration."""
        self.mcp_server = os.environ.get("MCP_SERVER", "http://sdg-docs-mcp-server.llamastack.svc.cluster.local:8000/sse")
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.prompt = os.environ.get("PROMPT", "What is Kubernetes?")
        
    @task
    def call_responses_with_mcp(self):
        """Call Responses API with MCP tool."""
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
                    # Check if we got a valid response
                    if "output" in data or "choices" in data:
                        response.success()
                    else:
                        response.failure(f"Unexpected response format: {list(data.keys())}")
                except json.JSONDecodeError:
                    response.failure("Invalid JSON response")
            else:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")


class ResponsesSimpleUser(HttpUser):
    """
    User that calls the Responses API without tools.
    Simpler workload for baseline testing.
    """
    wait_time = between(1, 3)
    abstract = True  # Don't use in tests by default
    
    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.prompt = os.environ.get("PROMPT", "What is the capital of France?")
        
    @task
    def call_responses_simple(self):
        """Call Responses API without tools."""
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
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")


class ChatCompletionsUser(HttpUser):
    """
    User that calls the Chat Completions API.
    For testing LlamaStack's OpenAI-compatible endpoint.
    """
    wait_time = between(1, 3)
    abstract = True  # Don't use in tests by default
    
    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.prompt = os.environ.get("PROMPT", "Hello, how are you?")
        
    @task
    def call_chat_completions(self):
        """Call Chat Completions API."""
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
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")
