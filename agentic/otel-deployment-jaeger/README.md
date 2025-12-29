# OpenTelemetry Tracing for LlamaStack

This folder contains Kubernetes manifests for setting up distributed tracing for LlamaStack using OpenTelemetry and Grafana Tempo with Jaeger UI.

## Architecture

```
LlamaStackDistribution  ------>  OpenTelemetry Collector  ------>  Tempo + Jaeger UI
```

### Components

| Component | Namespace | Purpose |
|-----------|-----------|---------|
| **LlamaStackDistribution** | `llamastack` | Application being traced. Sends OTLP (OpenTelemetry Protocol) traces via HTTP. |
| **OpenTelemetry Collector** | `llamastack` | Receives traces from LlamaStack, batches them, forwards to Tempo. Also logs to stdout for debugging. |
| **Tempo** | `openshift-tempo-operator` | Trace storage backend with built-in Jaeger UI for visualization. |

## Files

| File | Description |
|------|-------------|
| `tempo-monolithic.yaml` | TempoMonolithic CR - trace storage + Jaeger UI |
| `otel-collector-deployment.yaml` | OTel (OpenTelemetry) Collector ConfigMap, Deployment, and Service |
| `llamastack-distribution-postgres-otel.yaml` | LlamaStack with OpenTelemetry environment variables |

## Prerequisites

1. **OpenShift cluster** with admin access
2. **Red Hat Tempo Operator** installed from OperatorHub
3. **LlamaStack Operator** installed
4. **PostgreSQL** deployed in `llamastack` namespace with `postgres-secret` containing:
   - `POSTGRES_DB`
   - `POSTGRES_USER`
   - `POSTGRES_PASSWORD`
   - See: [agentic/test-deployment/postgres-deployment/postgres-complete-deployment.yaml](../test-deployment/postgres-deployment/postgres-complete-deployment.yaml)
5. **vLLM InferenceService** deployed (referenced in `VLLM_URL`)
   - See: [agentic/test-deployment/inferenceservice.yaml](../test-deployment/inferenceservice.yaml)

## Deployment Steps

### Step 1: Install Tempo Operator

Install the **Red Hat Tempo Operator** from OperatorHub in the OpenShift console. This creates the `openshift-tempo-operator` namespace.

### Step 2: Deploy Tempo

```bash
oc apply -f tempo-monolithic.yaml
```

Key configuration in `tempo-monolithic.yaml`:

```yaml
spec:
  ingestion:
    otlp:
      grpc:
        enabled: true   # Receives traces from OTel Collector on port 4317
  jaegerui:
    enabled: true       # Enables Jaeger UI for visualization
    route:
      enabled: true     # Creates OpenShift route for external access
  storage:
    traces:
      backend: memory   # For testing only. Use S3/GCS for production.
```

The Tempo service will be available at:
- `tempo-tracing.openshift-tempo-operator.svc.cluster.local:4317` (gRPC)

Wait for Tempo to be ready:
```bash
oc get tempomonolithic tracing -n openshift-tempo-operator -w
```

### Step 3: Deploy OpenTelemetry Collector

```bash
oc apply -f otel-collector-deployment.yaml
```

Key configuration in the collector ConfigMap:

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318   # LlamaStack sends traces here

exporters:
  otlp/tempo:
    # Sends traces to Tempo for storage and visualization in Jaeger UI
    # This URL comes from the Tempo service deployed in Step 2
    endpoint: tempo-tracing.openshift-tempo-operator.svc.cluster.local:4317
    tls:
      insecure: true
  debug:
    # Also logs traces to stdout for debugging (check with: oc logs deployment/otel-collector)
    verbosity: detailed

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlp/tempo, debug]   # Exports to both Tempo and stdout
```

The collector service will be available at:
- `otel-collector.llamastack.svc.cluster.local:4318` (HTTP)

Wait for collector to be ready:
```bash
oc rollout status deployment/otel-collector -n llamastack
```

### Step 4: Deploy LlamaStackDistribution with OpenTelemetry

```bash
oc apply -f llamastack-distribution-postgres-otel.yaml
```

Key environment variables in `llamastack-distribution-postgres-otel.yaml`:

```yaml
env:
  # OpenTelemetry configuration
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    # This URL comes from the OTel Collector service deployed in Step 3
    value: "http://otel-collector.llamastack.svc.cluster.local:4318"
  - name: OTEL_SERVICE_NAME
    value: "llamastack"   # Service name shown in Jaeger UI
  
  # vLLM configuration - update this to match your InferenceService
  - name: VLLM_URL
    value: "http://llama-32-3b-instruct-predictor.bench.svc.cluster.local:80/v1"
```

**Important**: Update `VLLM_URL` to match your vLLM InferenceService. The URL format is:
```
http://<inferenceservice-name>-predictor.<namespace>.svc.cluster.local:80/v1
```

Wait for LlamaStack to be ready:
```bash
oc rollout status deployment/llamastack-rhoai32-postgres-otel -n llamastack
```

## Accessing Jaeger UI

### Option 1: Port Forward (Recommended for testing)

```bash
oc port-forward pod/tempo-tracing-0 16686:16686 -n openshift-tempo-operator
```

Then open: http://localhost:16686

### Option 2: OpenShift Route

The Tempo deployment creates a route automatically. Get the URL:
```bash
oc get route -n openshift-tempo-operator
```

