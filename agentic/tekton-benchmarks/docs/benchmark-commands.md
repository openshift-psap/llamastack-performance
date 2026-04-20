# Benchmark Run Commands

Quick-reference commands for running benchmark pipelines. Copy-paste directly into your terminal.

## Prerequisites

```bash
# Make sure you're logged in and in the correct namespace context
oc project tekton-llamastack

# Apply all tasks (only needed once or after task changes)
oc apply -f tasks/
```

---

## With OTel — 20 tokens, 128 users

Deploys RHAIIS, Postgres, OTel Collector, and LlamaStack with full tracing.

```bash
oc apply -f pipelines/rhaiis-llamastack-simple.yaml && \
oc create -f - <<'EOF'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: benchmark-rhaiis-llamastack-simple-otel-20t-
  namespace: tekton-llamastack
spec:
  pipelineRef:
    name: rhaiis-llamastack-simple-benchmark
  taskRunTemplate:
    serviceAccountName: tekton-deployer
  workspaces:
    - name: results
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
  params:
    - name: NAMESPACE
      value: "llamastack-bench"
    - name: MODEL_NAME
      value: "qwen3-vl-30b-a3b-instruct"
    - name: MODEL
      value: "vllm-inference/qwen3-vl-30b-a3b-instruct"
    - name: USERS
      value: "128"
    - name: SPAWN_RATE
      value: "128"
    - name: RUN_TIME_SECONDS
      value: "600"
    - name: INPUT_TOKENS
      value: "20"
    - name: OUTPUT_TOKENS
      value: "20"
    - name: WARMUP_SECONDS
      value: "300"
    - name: LOAD_SHAPE
      value: "steady"
    - name: ENABLE_MLFLOW
      value: "true"
    - name: MLFLOW_EXPERIMENT
      value: "rhaiis-benchmarks"
    - name: PVC_NAME
      value: "qwen3-vl-model-pvc"
    - name: PVC_SIZE
      value: "120Gi"
    - name: DEPLOY_TIMEOUT
      value: "900"
    - name: SKIP_DEPLOY_RHAIIS
      value: "false"
EOF
```

---

## Without OTel — 20 tokens, 128 users

Deploys RHAIIS, Postgres, and LlamaStack with `OTEL_SDK_DISABLED=true`. No OTel Collector.

```bash
oc apply -f pipelines/rhaiis-llamastack-simple-no-otel.yaml && \
oc create -f - <<'EOF'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: benchmark-rhaiis-llamastack-simple-no-otel-20t-
  namespace: tekton-llamastack
spec:
  pipelineRef:
    name: rhaiis-llamastack-simple-no-otel-benchmark
  taskRunTemplate:
    serviceAccountName: tekton-deployer
  workspaces:
    - name: results
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
  params:
    - name: NAMESPACE
      value: "llamastack-bench"
    - name: MODEL_NAME
      value: "qwen3-vl-30b-a3b-instruct"
    - name: MODEL
      value: "vllm-inference/qwen3-vl-30b-a3b-instruct"
    - name: USERS
      value: "128"
    - name: SPAWN_RATE
      value: "128"
    - name: RUN_TIME_SECONDS
      value: "600"
    - name: INPUT_TOKENS
      value: "20"
    - name: OUTPUT_TOKENS
      value: "20"
    - name: WARMUP_SECONDS
      value: "300"
    - name: LOAD_SHAPE
      value: "steady"
    - name: ENABLE_MLFLOW
      value: "true"
    - name: MLFLOW_EXPERIMENT
      value: "rhaiis-benchmarks"
    - name: PVC_NAME
      value: "qwen3-vl-model-pvc"
    - name: PVC_SIZE
      value: "120Gi"
    - name: DEPLOY_TIMEOUT
      value: "900"
    - name: SKIP_DEPLOY_RHAIIS
      value: "false"
EOF
```

---

## With OTel — 1000 tokens, 128 users

```bash
oc apply -f pipelines/rhaiis-llamastack-simple.yaml && \
oc create -f pipelineruns/benchmark-rhaiis-llamastack-simple.yaml
```

---

## Without OTel — 1000 tokens, 128 users

```bash
oc apply -f pipelines/rhaiis-llamastack-simple-no-otel.yaml && \
oc create -f pipelineruns/benchmark-rhaiis-llamastack-simple-no-otel.yaml
```

---

## Direct RHAIIS (no LlamaStack) — 1000 tokens, 128 users

```bash
oc apply -f pipelines/rhaiis-direct.yaml && \
oc create -f pipelineruns/benchmark-rhaiis-direct.yaml
```

---

## Useful Commands

```bash
# Watch pipeline logs
tkn pipelinerun logs -f -n tekton-llamastack

# Check running pods in benchmark namespace
oc get pods -n llamastack-bench

# Port-forward Grafana
oc port-forward svc/grafana -n llamastack-monitoring 3000:3000

# Clean up benchmark namespace
oc delete all --all -n llamastack-bench
```
