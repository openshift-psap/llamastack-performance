# Tekton LlamaStack Benchmarks

Tekton-based performance testing for LlamaStack using Locust.

## Overview

This POC demonstrates running automated LlamaStack performance tests via Tekton pipelines with MLflow metrics logging.

## Structure

```
tekton-benchmarks/
├── tasks/                    # Tekton Tasks
│   ├── deploy-postgres.yaml  # Deploy PostgreSQL database
│   ├── deploy-llamastack.yaml# Deploy LlamaStackDistribution
│   ├── run-locust.yaml       # Run Locust performance test
│   └── cleanup.yaml          # Cleanup deployed resources
├── pipelines/
│   └── llamastack-benchmark.yaml  # Full end-to-end pipeline
├── pipelineruns/
│   ├── poc-taskrun.yaml      # Run just the Locust task
│   └── benchmark-example.yaml# Run full pipeline
├── rbac/
│   └── tekton-deployer-rbac.yaml  # ServiceAccount & permissions
└── configmap-locustfiles.yaml# Locust Python files (mounted to task)
```

## Tasks

| Task | Description |
|------|-------------|
| `deploy-postgres` | Deploys fresh PostgreSQL, waits for ready |
| `deploy-llamastack` | Deploys LlamaStackDistribution, waits for ready |
| `run-locust` | Runs Locust test, logs to MLflow |
| `cleanup` | Deletes PostgreSQL and LlamaStack |

## Pipeline

`llamastack-benchmark` runs tasks in order:

```
deploy-postgres → deploy-llamastack → run-locust → cleanup
```

## Quick Start

```bash
# 1. Apply resources
oc apply -f configmap-locustfiles.yaml
oc apply -f rbac/
oc apply -f tasks/
oc apply -f pipelines/

# 2. Run full pipeline
oc create -f pipelineruns/benchmark-example.yaml

# 3. Watch logs
tkn pipelinerun logs -f -n tekton-llamastack
```

## Configuration

Key parameters (set in PipelineRun):

| Parameter | Description |
|-----------|-------------|
| `NAMESPACE` | Target namespace for deployment |
| `HOST` | LlamaStack service URL |
| `USERS` | Concurrent users |
| `RUN_TIME_SECONDS` | Test duration |
| `MCP_SERVER` | MCP server URL |
| `MODEL` | Model name (provider/model format) |
| `ENABLE_MLFLOW` | Enable MLflow logging |

## MLflow

Results are logged to SageMaker MLflow using credentials from `mlflow-aws-credentials` secret.
