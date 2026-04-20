"""
Benchmark MCP Server — configurable tool count with tokenizer-accurate random text responses.

Designed for deterministic LlamaStack performance benchmarking.
All configuration via environment variables:
  NUM_TOOLS              Number of tools to register (default: 1)
  TOOL_RESPONSE_TOKENS   Exact token count per tool response (default: 100)
  TOKENIZER_MODEL        HuggingFace model name for tokenizer (default: Qwen/Qwen3-VL-30B-A3B-Instruct)
  TOOL_DESCRIPTION_TOKENS  Exact token count for each tool's description (default: 0 = use short default)
  POOL_SIZE              Number of unique pre-generated responses (default: 50)
  PORT                   Server port (default: 8000)
"""

import os
import random
import logging
from functools import partial

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from transformers import AutoTokenizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("benchmark-mcp")

NUM_TOOLS = int(os.environ.get("NUM_TOOLS", "1"))
TOOL_RESPONSE_TOKENS = int(os.environ.get("TOOL_RESPONSE_TOKENS", "100"))
TOOL_DESCRIPTION_TOKENS = int(os.environ.get("TOOL_DESCRIPTION_TOKENS", "0"))
TOKENIZER_MODEL = os.environ.get("TOKENIZER_MODEL", "Qwen/Qwen3-VL-30B-A3B-Instruct")
POOL_SIZE = int(os.environ.get("POOL_SIZE", "50"))
PORT = int(os.environ.get("PORT", "8000"))


def build_response_pool(tokenizer, valid_ids: list[int], num_tokens: int, pool_size: int) -> list[str]:
    """Pre-generate a pool of random text responses, each exactly `num_tokens` tokens."""
    pool = [_build_exact_text(tokenizer, valid_ids, num_tokens) for _ in range(pool_size)]
    log.info("Generated %d responses, each exactly %d tokens", len(pool), num_tokens)
    return pool


def _build_exact_text(tokenizer, valid_ids: list[int], num_tokens: int, prefix: str = "") -> str:
    """Build text of exactly `num_tokens` tokens, optionally starting with a prefix.
    Uses an encode-trim-decode loop to guarantee the final text is exactly num_tokens.
    Retries with a fresh random sample if convergence fails."""
    for attempt in range(10):
        filler_ids = random.choices(valid_ids, k=num_tokens * 2)
        text = prefix + tokenizer.decode(filler_ids, skip_special_tokens=True)

        for _ in range(10):
            ids = tokenizer.encode(text, add_special_tokens=False)
            if len(ids) == num_tokens:
                return text
            text = tokenizer.decode(ids[:num_tokens], skip_special_tokens=True)

    return text


def build_description(tokenizer, valid_ids: list[int], tool_index: int, num_tokens: int) -> str:
    """Build a tool description of exactly `num_tokens` tokens."""
    if num_tokens <= 0:
        return (f"Retrieve document #{tool_index}. "
                f"Returns a text document of approximately {TOOL_RESPONSE_TOKENS} tokens for analysis.")
    return _build_exact_text(tokenizer, valid_ids, num_tokens, prefix=f"Retrieve document #{tool_index}. ")


def make_tool_handler(pool: list[str]):
    """Return a handler that picks a random response from the pool."""
    def handler() -> str:
        return random.choice(pool)
    return handler


def _get_valid_ids(tokenizer):
    """Get non-special token IDs from the tokenizer vocabulary."""
    vocab = tokenizer.get_vocab()
    special_ids = set()
    for attr in ("bos_token_id", "eos_token_id", "pad_token_id", "unk_token_id"):
        tid = getattr(tokenizer, attr, None)
        if tid is not None:
            special_ids.add(tid)
    if hasattr(tokenizer, "all_special_ids"):
        special_ids.update(tokenizer.all_special_ids)
    return [tid for tid in vocab.values() if tid not in special_ids]


def create_server() -> FastMCP:
    log.info("Configuration: NUM_TOOLS=%d, TOOL_RESPONSE_TOKENS=%d, TOOL_DESCRIPTION_TOKENS=%d, TOKENIZER_MODEL=%s, POOL_SIZE=%d",
             NUM_TOOLS, TOOL_RESPONSE_TOKENS, TOOL_DESCRIPTION_TOKENS, TOKENIZER_MODEL, POOL_SIZE)

    log.info("Loading tokenizer: %s", TOKENIZER_MODEL)
    tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_MODEL, trust_remote_code=True)
    log.info("Tokenizer loaded (vocab size: %d)", tokenizer.vocab_size)

    valid_ids = _get_valid_ids(tokenizer)
    log.info("Vocabulary: %d usable token IDs", len(valid_ids))

    log.info("Building response pool...")
    pool = build_response_pool(tokenizer, valid_ids, TOOL_RESPONSE_TOKENS, POOL_SIZE)

    mcp = FastMCP(
        name="Benchmark MCP",
        instructions=(
            f"Benchmark MCP server with {NUM_TOOLS} tools. "
            f"Each tool returns a text document of ~{TOOL_RESPONSE_TOKENS} tokens. "
            "Call any tool to retrieve a document for analysis."
        ),
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> Response:
        return JSONResponse({
            "status": "healthy",
            "service": "benchmark-mcp",
            "num_tools": NUM_TOOLS,
            "tool_response_tokens": TOOL_RESPONSE_TOKENS,
            "tool_description_tokens": TOOL_DESCRIPTION_TOKENS,
            "tokenizer_model": TOKENIZER_MODEL,
            "pool_size": POOL_SIZE,
        })

    for i in range(NUM_TOOLS):
        tool_name = f"tool_{i}"
        handler = make_tool_handler(pool)
        handler.__name__ = tool_name
        handler.__qualname__ = tool_name
        handler.__doc__ = build_description(tokenizer, valid_ids, i, TOOL_DESCRIPTION_TOKENS)
        mcp.tool(handler)

    if TOOL_DESCRIPTION_TOKENS > 0:
        log.info("Registered %d tools, each with ~%d-token description", NUM_TOOLS, TOOL_DESCRIPTION_TOKENS)
    else:
        log.info("Registered %d tools with default descriptions", NUM_TOOLS)
    return mcp


server = create_server()


def main():
    log.info("Starting benchmark MCP server on port %d", PORT)
    server.run(transport="sse", host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
