# LlamaStack Performance Testing Framework

A comprehensive performance benchmarking and testing framework for [LlamaStackDistribution](https://github.com/opendatahub-io/llama-stack-distribution), designed to measure overhead, identify bottlenecks, and validate scalability in production environments.

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
└── README.md           
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



## Technologies Used

- **Inference:** [vLLM](https://github.com/vllm-project/vllm) 0.11.x
- **Orchestration:** [LlamaStackDistribution](https://github.com/opendatahub-io/llama-stack-distribution)
- **Benchmarking:** [GuideLLM](https://github.com/neuralmagic/guidellm), [Locust](https://locust.io/)
- **Platform:** OpenShift 4.x with RHOAI 2.22+
- **Storage:** PostgreSQL, SQLite
- **Monitoring:** Prometheus, DCGM (GPU metrics)

