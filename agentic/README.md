# Agentic API (Responses API) Performance Testing

Performance testing framework for LlamaStack's **Responses API** - the highest-level abstraction for agentic workflows with tool calling, multi-turn conversations, and persistent state management.

## Overview

The Responses API is LlamaStack's most complex endpoint, handling:
- Multi-turn conversations with persistent state (PostgreSQL/SQLite)
- Tool discovery and execution via MCP (Model Context Protocol)
- Response streaming and event handling
- Database-backed session management across multiple pods

This framework tests the performance impact of these stateful operations compared to simple chat completions.

## What Gets Deployed

```
┌─────────────┐      ┌──────────────┐      ┌─────────┐
│   Locust    │─────▶│  LlamaStack  │─────▶│  vLLM   │
│ (Load Gen)  │      │  (Responses) │      │ (Llama  │
└─────────────┘      └──────┬───────┘      │  3.2)   │
                            │              └─────────┘
                            ▼
                     ┌─────────────┐
                     │  PostgreSQL │
                     │  (State)    │
                     └─────────────┘
                            ▲
                            │ (optional)
                     ┌──────┴──────┐
                     │  MCP Server │
                     │  (Tools)    │
                     └─────────────┘
```

## Getting Started

### Prerequisites

- OpenShift cluster with GPU nodes
- `kubectl` or `oc` CLI configured
- Python 3.9+ with `locust` and `openai` packages (for local testing)

### Step-by-Step Deployment

#### 1. Deploy vLLM (Inference Backend)

```bash
oc apply -f test-deployment/inferenceservice.yaml -n bench
```

**Creates:**
- InferenceService: `llama-32-3b-instruct`
- Namespace: `bench`
- Model: Llama-3.2-3B-Instruct
- Features: Tool calling enabled (`--enable-auto-tool-choice`)

**Verify:**
```bash
oc get pods -n bench
# Should show vLLM pod in Running state
```

**Note:** This deployment uses `quay.io/vllm/vllm-cuda:0.11.0.0` (public image). Adjust GPU memory settings if needed.

---

#### 2. Deploy PostgreSQL (State Storage)

```bash
oc apply -f test-deployment/postgres-deployment/postgres-complete-deployment.yaml -n llamastack
```

**Creates:**
- Secret: `postgres-secret` (credentials)
- PVC: `postgres-pvc` (20Gi)
- Deployment: `postgres` (PostgreSQL 13)
- Service: `postgres.llamastack.svc.cluster.local:5432`

**Note:** This deployment uses `registry.redhat.io/rhel9/postgresql-13:latest`.

**Credentials (from secret):**
- Database: `llamastack`
- User: `llamastack`
- Password: `SecurePassword123`

**Verify:**
```bash
oc exec -n llamastack deployment/postgres -- psql -U llamastack -d llamastack -c "SELECT version();"
```

---

#### 3. Create LlamaStack Configuration (run.yaml)

The ConfigMap tells LlamaStack to use PostgreSQL instead of SQLite.

```bash
oc apply -f test-deployment/custom-run-yaml-config/configmap-rhoai32-new-postgres-minimal.yaml -n llamastack
```

**Creates:**
- ConfigMap: `llamastack-rhoai32-new-postgres-minimal`
- Contains: Custom LlamaStack configuration

**Storage backends configured for PostgreSQL:**

Each backend changed from SQLite (file-based) to PostgreSQL (shared database).

Example for `sql_inference`:
```yaml
sql_inference:
  type: sql_postgres
  host: ${env.POSTGRES_HOST}
  port: ${env.POSTGRES_PORT}
  database: ${env.POSTGRES_DB}
  user: ${env.POSTGRES_USER}
  password: ${env.POSTGRES_PASSWORD}
```

Same configuration applied to: `sql_agents`, `sql_files`, `sql_default`

---

#### 4. Deploy LlamaStack

**Option A: With Autoscaling (Recommended)**

```bash
oc apply -f test-deployment/llamastack-distribution-postgres-autoscaling.yaml -n llamastack
```

**Requires:**
- LlamaStack operator installed (see [Operator Setup](#operator-setup) below)
- ConfigMap from Step 3

**Creates:**
- LlamaStackDistribution CR: `llamastack-rhoai32-new-postgres-minimal`
- Which automatically generates:
  - Deployment: `llamastack-rhoai32-new-postgres-minimal` (1 replica initially)
  - Service: `llamastack-rhoai32-new-postgres-minimal-service:8321`
  - HPA: `llamastack-rhoai32-new-postgres-minimal-hpa` (scales 1-5 replicas based on CPU/Memory)
  - PVC: `llamastack-rhoai32-new-postgres-minimal-pvc` (20Gi)


**Verify autoscaling:**
```bash
oc get hpa -n llamastack
# Should show: llamastack-rhoai32-new-postgres-minimal-hpa
```

---

#### 5. Verify PostgreSQL Connection

Check that LlamaStack created tables in Postgres:

```bash
oc exec -n llamastack deployment/postgres -- psql -U llamastack -d llamastack -c "\dt"
```

**Expected tables:**
- `openai_conversations`
- `openai_responses`
- `chat_completions`
- `conversation_items`, `conversation_messages`
- `openai_files`

If no tables appear, check LlamaStack logs for connection errors.

---

#### 6. Deploy MCP Server (Optional)

Only required for testing Responses API with tool calling.

```bash
oc apply -f test-deployment/mcp-deployment/nps-mcp-server-deployment.yaml -n bench
```

📖 **[See mcp-deployment/README.md](test-deployment/mcp-deployment/README.md) for details**

---

### You're Ready!

All components are deployed. 

📖 **Next:** See [TESTING.md](TESTING.md) for instructions on running performance tests and monitoring autoscaling

---

## Operator Setup

The LlamaStack operator is required for autoscaling support.

### Install Operator

```bash
oc apply -f https://raw.githubusercontent.com/llamastack/llama-stack-k8s-operator/main/release/operator.yaml
```

**Verifies installation:**
```bash
oc get pods -n llama-stack-k8s-operator-system
```

### Disable RHOAI LlamaStack Component (Critical!)

If you have Red Hat OpenShift AI (RHOAI) installed, you **must** disable its bundled llama-stack operator to avoid CRD version conflicts:

```bash
oc patch datasciencecluster default-dsc -n redhat-ods-operator --type='merge' \
  -p '{"spec":{"components":{"llamastack":{"managementState":"Removed"}}}}'
```

**Why?** 
- RHOAI 2.22.1 bundles an older llama-stack-operator without `autoscaling` and `userConfig` support
- The two operators conflict over the CRD definition
- Disabling RHOAI's version allows the upstream operator to take over

**Verify CRD supports autoscaling:**
```bash
oc get crd llamastackdistributions.llamastack.io -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.server.properties}' | jq 'has("autoscaling")'
# Should return: true
```

---
