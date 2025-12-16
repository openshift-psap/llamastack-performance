# LlamaStack Performance Testing Framework

A comprehensive performance benchmarking and testing framework for [LlamaStack](https://github.com/meta-llama/llama-stack), designed to measure overhead, identify bottlenecks, and validate scalability in production environments.

## Overview

This repository contains tools and configurations for performance testing LlamaStack deployments on Kubernetes/OpenShift, with a focus on:

1. **Quantifying LlamaStack overhead** compared to direct vLLM inference
2. **Testing agentic workflows** with PostgreSQL state management and MCP tool integration
3. **Validating horizontal pod autoscaling** under realistic workloads
4. **Identifying bottlenecks** in stateful operations and concurrent request handling

## Repository Structure

```
├── benchmarking/          # Chat Completions endpoint testing (LlamaStack vs vLLM)
├── agentic/              # Responses API testing (PostgreSQL + MCP + Autoscaling)
├── docs/                 # Detailed documentation and guides
└── README.md            # This file
```

## Test Types

### 1. Chat Completions Benchmarking (`benchmarking/`)

**Purpose:** Measure the performance overhead introduced by LlamaStack when wrapping a vLLM inference backend.

**Tool:** [GuideLLM](https://github.com/neuralmagic/guidellm)

**Methodology:**
- Baseline: Direct vLLM inference via OpenAI-compatible API
- Comparison: Same workload through LlamaStack → vLLM
- Metrics: Throughput (requests/sec), latency (TTFT, TPOT), token rates

**Key Variables Tested:**
- Concurrency levels (1, 2, 4, 8, 16, 32, 64, 128)
- Uvicorn worker counts (1, 2, 4)
- RHOAI versions (2.25, 3.0, 3.2)
- Pod replica counts (1, 2, 4)

📖 **[See benchmarking/README.md](benchmarking/README.md) for detailed setup and usage**

### 2. Responses API (Agentic) Testing (`agentic/`)

**Purpose:** Test the LlamaStack Responses API with stateful operations, tool calling, and database persistence.

**Tool:** [Locust](https://locust.io/) with OpenAI extensions

**Focus Areas:**
- PostgreSQL backend performance (vs SQLite baseline)
- MCP (Model Context Protocol) tool integration
- Horizontal Pod Autoscaling (HPA) behavior under load
- Multi-turn conversations with state persistence

**Test Scenarios:**
- Simple Responses API (no tools)
- Responses API with MCP tool calling (National Parks Service example)
- Direct vLLM comparison (baseline)

📖 **[See agentic/README.md](agentic/README.md) for detailed setup and usage**

## Quick Start

### Prerequisites

- Kubernetes/OpenShift cluster with:
  - NVIDIA GPU nodes (for vLLM inference)
  - Red Hat OpenShift AI (RHOAI) or KServe installed
  - Persistent storage provisioner
- `kubectl` or `oc` CLI configured
- Python 3.9+ with pip (for local test execution)

### Running Your First Test

**Chat Completions Benchmark:**
```bash
cd benchmarking
# Deploy vLLM and LlamaStack, then run GuideLLM benchmark
# See benchmarking/README.md for detailed instructions
```

**Responses API Test:**
```bash
cd agentic
# Deploy PostgreSQL, LlamaStack, and run Locust test
# See agentic/README.md for detailed instructions
```

## Key Findings & Use Cases

### When to Use This Framework

- **Before production deployment:** Establish performance baselines and capacity planning
- **Version upgrades:** Compare performance across LlamaStack/vLLM versions
- **Configuration tuning:** Test impact of worker counts, keepalive settings, resource limits
- **Scaling validation:** Verify autoscaling works correctly with your workload
- **Backend comparison:** SQLite vs PostgreSQL for stateful operations

### Typical Performance Characteristics

*(Based on testing with Llama-3.2-3B-Instruct on A10G GPU)*

- **LlamaStack overhead:** ~5-15% latency increase vs direct vLLM (depends on configuration)
- **PostgreSQL impact:** Minimal for read-heavy workloads; connection pooling critical for high concurrency
- **Autoscaling:** HPA responds to CPU load within 30-60 seconds; requires proper resource requests

## Documentation

- [Quick Reference for Operators](docs/quick-reference-operators.md) - Common commands and troubleshooting
- [Complete Performance Guide](docs/llama-stack-performance-complete-guide.md) - Comprehensive methodology

## Technologies Used

- **Inference:** [vLLM](https://github.com/vllm-project/vllm) 0.11.x
- **Orchestration:** [LlamaStack](https://github.com/meta-llama/llama-stack) 0.3.4+
- **Benchmarking:** [GuideLLM](https://github.com/neuralmagic/guidellm), [Locust](https://locust.io/)
- **Platform:** OpenShift 4.x with RHOAI 2.22+
- **Storage:** PostgreSQL 13, SQLite
- **Monitoring:** Prometheus, DCGM (GPU metrics)

## Contributing

Contributions are welcome! Key areas for improvement:
- Additional test scenarios (RAG, multi-modal, batch processing)
- Support for more inference backends (Ollama, TGI, etc.)
- Automated report generation and comparison visualizations
- CI/CD integration for continuous performance regression testing

## License

[Add your license here]

## Authors

- PSAP Team (Performance and Scale for AI Platforms)
- Red Hat OpenShift AI Engineering

## Support

For questions or issues:
- File an issue in this repository
- Contact: [Your team contact info]

---

**Note:** This framework is designed for performance testing and benchmarking. Results will vary based on hardware, models, and workload patterns. Always validate performance in your specific environment.
