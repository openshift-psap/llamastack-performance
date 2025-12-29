# OpenTelemetry Tracing for LlamaStack with Cluster Observability

This folder contains Kubernetes manifests for setting up distributed tracing for LlamaStack using OpenTelemetry, Grafana Tempo with multitenancy, and the OpenShift Distributed Tracing UI.

## Why Cluster Observability Instead of Jaeger UI?

The standalone Jaeger UI (used in [../otel-deployment-jaeger/](../otel-deployment-jaeger/)) is being deprecated in OpenShift. The **Cluster Observability Operator** provides a modern alternative:

- **Integrated UI**: Traces are viewable directly in the OpenShift Console (Observe → Traces)
- **Unified Experience**: Same interface for metrics, logs, and traces
- **Multi-tenancy**: Supports multiple teams/projects with isolated trace data
- **Better Security**: Uses OpenShift OAuth for authentication

However, Cluster Observability requires additional configuration compared to the simple Jaeger UI setup. This README explains each requirement and why it's needed.

## Architecture

```
LlamaStack  --->  OTel Collector  --->  Tempo Gateway  --->  Tempo Storage  --->  Cluster Observability (Observe → Traces)
```

### Data Flow Explained

1. **LlamaStack** sends traces via HTTP to the OTel Collector
2. **OTel Collector** does four things:
   - Enriches traces with Kubernetes metadata (namespace, pod name, deployment)
   - Batches traces for efficient transmission
   - Authenticates with Tempo using a service account token
   - Sends traces via gRPC to the Tempo Gateway
3. **Tempo Gateway** validates the OAuth token and routes traces to the correct tenant
4. **Tempo Storage** persists traces to disk (PersistentVolume)
5. **Cluster Observability** queries Tempo to display traces in the Distributed Tracing UI

## Components

| Component | Namespace | Purpose |
|-----------|-----------|---------|
| **LlamaStackDistribution** | `llamastack` | Application being traced. Sends OTLP traces via HTTP. |
| **OTel Collector** | `llamastack` | Receives traces, enriches with Kubernetes metadata, authenticates with Tempo, forwards traces. |
| **Tempo (with Gateway)** | `openshift-tempo-operator` | Stores traces on persistent storage. Gateway handles authentication and multitenancy. |
| **Cluster Observability Operator** | `openshift-cluster-observability-operator` | Provides the Distributed Tracing UI plugin for the OpenShift Console. |

## Files

| File | Description |
|------|-------------|
| `tempo-monolithic.yaml` | TempoMonolithic with multitenancy enabled and persistent storage |
| `otel-collector-deployment.yaml` | OTel Collector with OAuth authentication, k8sattributes processor, and RBAC |
| `llamastack-distribution-postgres-otel.yaml` | LlamaStack with OpenTelemetry environment variables and tracing patch |
| `tracing-patch-configmap.yaml` | **NEW** - Patched tracing module to fix trace context issues |

## Tracing Patch

The `tracing-patch-configmap.yaml` contains a patched version of LlamaStack's `tracing.py` module that fixes two critical issues:

### Issues Fixed

1. **Class-level spans list bug (trace mixing)**
   - **Problem**: The original implementation used a class-level `spans` list that was shared across all `TraceContext` instances. This caused trace data from concurrent requests to mix together.
   - **Fix**: Changed to instance-level `spans` list so each request has its own isolated span stack.

2. **start_trace overwriting existing trace context (trace naming issues)**
   - **Problem**: When `start_trace()` was called within an already-active trace (e.g., from a nested operation), it would create a new trace ID, fragmenting the trace into disconnected pieces.
   - **Fix**: Added logic to detect existing trace context and create a nested span instead of a new trace.

### How the Patch is Applied

The patch is applied via an init container in the LlamaStackDistribution:

1. The `tracing-patch-configmap.yaml` is mounted as a volume
2. An init container copies the patched `tracing.py` to the Python site-packages directory
3. The main container shares this directory via an `emptyDir` volume

## Prerequisites

1. **OpenShift cluster** with admin access
2. **Red Hat Tempo Operator** installed from OperatorHub
3. **Cluster Observability Operator** installed from OperatorHub
4. **LlamaStack Operator** installed
5. **PostgreSQL** deployed in `llamastack` namespace
   - See: [agentic/test-deployment/postgres-deployment/postgres-complete-deployment.yaml](../test-deployment/postgres-deployment/postgres-complete-deployment.yaml)
6. **vLLM InferenceService** deployed
   - See: [agentic/test-deployment/inferenceservice.yaml](../test-deployment/inferenceservice.yaml)

## Deployment Steps

### Step 1: Install Operators

Install from OperatorHub in the OpenShift Console:

1. **Red Hat Tempo Operator** - Creates `openshift-tempo-operator` namespace
2. **Cluster Observability Operator** - Creates `openshift-cluster-observability-operator` namespace

### Step 2: Create the Distributed Tracing UI Plugin

In the OpenShift Console:

1. Navigate to **Operators → Installed Operators**
2. Select **Cluster Observability Operator**
3. Go to the **UI Plugin** tab
4. Click **Create UIPlugin**
5. Switch to **YAML view** and apply this configuration:

```yaml
apiVersion: observability.openshift.io/v1alpha1
kind: UIPlugin
metadata:
  name: distributed-tracing
spec:
  type: DistributedTracing
```

6. Click **Create**

**Why:** This creates the "Traces" menu item under Observe in the OpenShift Console. Without this, there's no UI to view traces.

Verify the plugin is ready by checking in the OpenShift Console that **Observe → Traces** menu item appears. You may need to refresh the browser.

### Step 3: Deploy Tempo with Multitenancy

```bash
oc apply -f tempo-monolithic.yaml
```

**Why multitenancy is required:** The Distributed Tracing UI only displays Tempo instances that have multitenancy enabled. This is a design decision by the UI plugin to support enterprise environments where different teams need isolated trace data.

Key configuration in `tempo-monolithic.yaml`:

```yaml
spec:
  # Multitenancy - REQUIRED for Distributed Tracing UI
  multitenancy:
    enabled: true
    mode: openshift              # Uses OpenShift OAuth for authentication
    authentication:
    - tenantName: dev            # Tenant name - used in X-Scope-OrgID header
      tenantId: "1610b0c3-c509-4592-a256-a1871353dbfa"  # Unique ID (can be any UUID)

  # Storage - REQUIRED for multitenancy
  storage:
    traces:
      backend: pv                # PersistentVolume storage (uses default StorageClass)
      size: 10Gi                 # Adjust based on trace volume

  # Ingestion - how traces are received
  ingestion:
    otlp:
      grpc:
        enabled: true            # OTel Collector sends traces via gRPC (port 4317)
      http:
        enabled: true            # Also accepts HTTP (port 4318)
```

**Important:** With multitenancy enabled, Tempo creates a **gateway** service (`tempo-tracing-gateway`) instead of a direct service (`tempo-tracing`). The gateway handles authentication and tenant routing.

Wait for Tempo to be ready:
```bash
oc get tempomonolithic tracing -n openshift-tempo-operator
```

Expected output:
```
NAME      AGE     TEMPO VERSION
tracing   3d19h   2.9.0
```

Verify the gateway service was created:
```bash
oc get svc -n openshift-tempo-operator | grep tempo-tracing-gateway
```

Expected output:
```
tempo-tracing-gateway   ClusterIP   172.30.215.146   <none>   8080/TCP,8081/TCP,3200/TCP,4317/TCP   3d19h
```

### Step 4: Deploy OpenTelemetry Collector

```bash
oc apply -f otel-collector-deployment.yaml
```

The Cluster Observability setup requires a more complex collector configuration than the Jaeger UI setup. Here's what each part does and why:

#### 4.1 Bearer Token Authentication Extension

```yaml
extensions:
  bearertokenauth:
    filename: /var/run/secrets/kubernetes.io/serviceaccount/token
```

**Why:** The Tempo gateway requires OAuth authentication. This extension reads the Kubernetes service account token (automatically mounted in every pod) and uses it as a bearer token when connecting to Tempo.

#### 4.2 Kubernetes Attributes Processor

```yaml
processors:
  k8sattributes:
    extract:
      metadata:
        - k8s.namespace.name
        - k8s.pod.name
        - k8s.deployment.name
    pod_association:
      - sources:
          - from: resource_attribute
            name: k8s.pod.ip
      - sources:
          - from: connection
```

**Why:** The Distributed Tracing UI filters traces by Kubernetes namespace. Without this processor, traces don't have namespace information and the UI shows "No results found". The processor contacts the Kubernetes API, looks up which pod sent each trace (by IP address), and adds the namespace/pod/deployment metadata to every span.

#### 4.3 Exporter Configuration

```yaml
exporters:
  otlp/tempo:
    endpoint: tempo-tracing-gateway.openshift-tempo-operator.svc.cluster.local:4317
    auth:
      authenticator: bearertokenauth
    headers:
      X-Scope-OrgID: dev
    tls:
      ca_file: /var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt
```

**Why each setting:**

- **endpoint**: Points to the Tempo **gateway** (not the direct service). The gateway handles authentication.
- **auth.authenticator**: Uses the bearer token extension we defined above.
- **headers.X-Scope-OrgID**: Identifies which tenant this trace belongs to. Must match `tenantName` in Tempo config.
- **tls.ca_file**: The gateway requires TLS (encrypted connection). This uses OpenShift's internal certificate authority to verify the connection is legitimate.

#### 4.4 Container Image

```yaml
image: otel/opentelemetry-collector-contrib:latest
```

**Why:** The base `otel/opentelemetry-collector` image doesn't include the `k8sattributes` processor. The `contrib` image includes additional processors and extensions.

#### 4.5 Service Account

```yaml
serviceAccountName: otel-collector-sa
```

**Why:** The collector needs a dedicated service account for two reasons:
1. To get an OAuth token for Tempo authentication
2. To grant RBAC permissions for the k8sattributes processor

#### 4.6 RBAC for Kubernetes Attributes

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: otel-collector-k8s-reader
rules:
- apiGroups: [""]
  resources: ["pods", "namespaces"]
  verbs: ["get", "watch", "list"]
- apiGroups: ["apps"]
  resources: ["deployments", "replicasets"]
  verbs: ["get", "watch", "list"]
```

**Why:** The k8sattributes processor needs to query the Kubernetes API to look up pod information. This ClusterRole grants read-only access to pods, namespaces, deployments, and replicasets.

#### 4.7 RBAC for Tempo Trace Writing

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: traces-writer-dev
rules:
- apiGroups: [tempo.grafana.com]
  resources: [dev]              # Tenant name
  resourceNames: [traces]
  verbs: [create]
```

**Why:** Tempo's OpenShift multitenancy mode uses Kubernetes RBAC to control who can write traces to each tenant. This grants the collector's service account permission to write traces to the `dev` tenant. Without this, Tempo rejects the traces with "Unauthenticated" errors.

Wait for the collector to be ready:
```bash
oc rollout status deployment/otel-collector -n llamastack
```

### Step 5: Deploy Tracing Patch ConfigMap

```bash
oc apply -f tracing-patch-configmap.yaml
```

This creates the ConfigMap containing the patched `tracing.py` module that fixes trace context issues.

### Step 6: Deploy LlamaStackDistribution with OpenTelemetry

```bash
oc apply -f llamastack-distribution-postgres-otel.yaml
```

Key environment variables:

```yaml
env:
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: "http://otel-collector.llamastack.svc.cluster.local:4318"
  - name: OTEL_SERVICE_NAME
    value: "llamastack"
```

**Why:**
- `OTEL_EXPORTER_OTLP_ENDPOINT`: Tells LlamaStack where to send traces (the collector we deployed in Step 4)
- `OTEL_SERVICE_NAME`: The service name shown in the tracing UI

The LlamaStackDistribution now includes:
- An init container that applies the tracing patch
- Volume mounts for the patch ConfigMap and shared site-packages directory

**Important:** Update `VLLM_URL` to match your vLLM InferenceService:
```
http://<inferenceservice-name>-predictor.<namespace>.svc.cluster.local:80/v1
```

Wait for LlamaStack to be ready:
```bash
oc rollout status deployment/llamastack-rhoai32-postgres-otel -n llamastack
```

### Verify Patch Application

Check that the init container applied the patch successfully:

```bash
oc logs deployment/llamastack-rhoai32-postgres-otel -c apply-tracing-patch -n llamastack
```

Expected output:
```
Applying tracing patch...
Tracing patch applied successfully
```

## Viewing Traces

1. Open the OpenShift Console
2. Navigate to **Observe → Traces**
3. Select:
   - **Tempo instance**: `tracing` (or your Tempo name)
   - **Tenant**: `dev`
4. Use filters:
   - **Namespace**: `llamastack`
   - **Service name**: `llamastack`
5. Click **Run query**

## Troubleshooting

### Traces not appearing in the UI

1. Check OTel Collector logs:
   ```bash
   oc logs deployment/otel-collector -n llamastack
   ```

2. Verify LlamaStack is sending traces:
   ```bash
   oc logs deployment/llamastack-rhoai32-postgres-otel -n llamastack | grep -i trace
   ```

3. Check Tempo gateway logs:
   ```bash
   oc logs deployment/tempo-tracing-gateway -n openshift-tempo-operator
   ```

### Trace context issues (mixed or fragmented traces)

If you see traces being split into multiple disconnected pieces, verify the patch is applied:

```bash
oc exec deployment/llamastack-rhoai32-postgres-otel -n llamastack -- \
  grep -A5 "PATCHED VERSION" /opt/app-root/lib/python3.11/site-packages/llama_stack/providers/utils/telemetry/tracing.py
```

You should see the patch header comment in the output.

## Comparison with Jaeger UI Setup

| Aspect | Jaeger UI ([../otel-deployment-jaeger/](../otel-deployment-jaeger/)) | Cluster Observability (this folder) |
|--------|-----------------------------------|-------------------------------------|
| UI Location | Standalone Jaeger UI (port-forward or route) | OpenShift Console (Observe → Traces) |
| Authentication | None (insecure TLS) | OAuth via service account token |
| Multitenancy | No | Yes (required by UI) |
| Storage | Memory (ephemeral) | PersistentVolume |
| k8sattributes | No | Yes (required by UI) |
| RBAC | None | ClusterRoles for k8s-reader and traces-writer |
| Complexity | Simple | More complex but production-ready |
| Tracing Patch | Yes | Yes |
