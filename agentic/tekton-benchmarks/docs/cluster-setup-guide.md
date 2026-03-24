# Cluster Setup Guide for Tekton Benchmark Pipelines

This guide walks you through preparing an OpenShift cluster (IBM Cloud IPI) to run the LlamaStack Tekton benchmark pipelines. By the end, you will have all operators, RBAC, namespaces, secrets, and Tekton definitions in place.

## Prerequisites

- `oc` CLI installed and authenticated as cluster-admin
- `tkn` CLI installed (Tekton CLI)
- Access to an OpenShift 4.16+ cluster on IBM Cloud
- At least one GPU worker node in the cluster (required for vLLM model serving)

## Architecture Overview

Three benchmark pipelines are available, each testing a different layer of the stack:

| Pipeline | What it tests | Components deployed |
|----------|--------------|---------------------|
| `vllm-direct-benchmark` | vLLM ChatCompletions API | vLLM |
| `responses-simple-benchmark` | LlamaStack Responses API | vLLM, LlamaStack, PostgreSQL, OTel |
| `responses-mcp-benchmark` | LlamaStack Responses API + MCP tools | vLLM, LlamaStack, MCP Server, PostgreSQL, OTel |

## Step 1: Verify Required Operators

The following operators must be installed via OperatorHub before proceeding:

| Operator | Purpose |
|----------|---------|
| OpenShift Pipelines | Tekton runtime |
| NVIDIA GPU Operator | GPU resource scheduling |
| Node Feature Discovery (NFD) | GPU Operator prerequisite |
| Red Hat OpenShift AI (RHOAI) | KServe (ServingRuntime, InferenceService CRDs) |
| Red Hat OpenShift Serverless | KServe dependency |
| Red Hat OpenShift Service Mesh | Serverless dependency |
| Tempo Operator | Distributed tracing backend |

> **Tested environment:** These pipelines were validated with the following operator versions on OpenShift 4.20. Other versions may work but have not been tested.
>
> | Operator | Tested Version |
> |----------|---------------|
> | OpenShift Pipelines | 1.21.0 |
> | NVIDIA GPU Operator | 25.10.1 |
> | Node Feature Discovery (NFD) | 4.20.0 |
> | Red Hat OpenShift AI (RHOAI) | 3.3.0 |
> | Red Hat OpenShift Serverless | 1.37.1 |
> | Red Hat OpenShift Service Mesh | 3.2.2 |
> | Tempo Operator | 0.20.0 |

Verify installed operators:

```bash
oc get csv --all-namespaces --no-headers | awk '{print $2, $NF}' | sort -u
```

You should see entries for: `openshift-pipelines-operator-rh`, `gpu-operator-certified`, `nfd`, `rhods-operator`, `serverless-operator`, `servicemeshoperator`, `tempo-operator`.

### Create operator instances

Installing operators only installs the controllers. You must also create the CR instances they manage.

**NodeFeatureDiscovery** (required for GPU detection):

```bash
oc apply -f - <<EOF
apiVersion: nfd.openshift.io/v1
kind: NodeFeatureDiscovery
metadata:
  name: nfd-instance
  namespace: openshift-nfd
spec: {}
EOF
```

Wait for NFD worker pods to be running on all nodes:

```bash
oc get pods -n openshift-nfd -o wide
```

**ClusterPolicy** (required for GPU driver and device plugin):

```bash
oc apply -f - <<EOF
apiVersion: nvidia.com/v1
kind: ClusterPolicy
metadata:
  name: gpu-cluster-policy
spec:
  daemonsets: {}
  dcgm:
    enabled: true
  dcgmExporter:
    enabled: true
  devicePlugin:
    enabled: true
  driver:
    enabled: true
  gfd:
    enabled: true
  nodeStatusExporter:
    enabled: true
  operator:
    defaultRuntime: crio
  toolkit:
    enabled: true
EOF
```

Wait for all GPU operator pods to be Running on the GPU node (5–10 minutes for driver compilation):

```bash
oc get pods -n nvidia-gpu-operator -o wide
```

Verify GPUs are visible:

```bash
oc get node <gpu-node-name> -o json | jq '.status.capacity' | grep nvidia
# Should show: "nvidia.com/gpu": "<number-of-gpus>"
```

## Step 2: Install the LlamaStack Operator

The LlamaStack operator is installed from the **upstream GitHub repository**, not through the OpenShift AI (RHOAI) operator. This is important because:

- The upstream operator supports features like HPA autoscaling that the RHOAI-bundled version may not
- The upstream operator gets updates independently of RHOAI release cycles

**Important: Disable the RHOAI-managed LlamaStack component first.** OpenShift AI includes its own LlamaStack operator component. If it is set to `Managed`, it will continuously override the CRD installed by the upstream operator, causing the upstream operator to malfunction. The RHOAI controller reconciles the CRD back to its own version, silently breaking fields that the upstream operator adds (like autoscaling).

Check the current state:

```bash
oc get datasciencecluster -o yaml | grep -A3 -i llamastack
```

If `managementState` is `Managed` (or anything other than `Removed`), disable it:

```bash
oc patch datasciencecluster default-dsc --type merge -p '{"spec":{"components":{"llamastackoperator":{"managementState":"Removed"}}}}'
```

Now install the upstream operator:

```bash
oc apply -f https://raw.githubusercontent.com/llamastack/llama-stack-k8s-operator/main/release/operator.yaml
```

Verify the operator is running and the CRD is created:

```bash
oc get pods -n llama-stack-k8s-operator-system
# Should show 1/1 Running

oc get crd llamastackdistributions.llamastack.io
# Should exist
```

## Step 3: Configure the DataScienceCluster

The OpenShift AI DataScienceCluster (DSC) controls several settings that affect the benchmarks.

**Set KServe service type to Headed** (default is `Headless`, which breaks in-cluster service discovery):

```bash
oc patch datasciencecluster default-dsc --type merge -p '{"spec":{"components":{"kserve":{"rawDeploymentServiceConfig":"Headed"}}}}'
```

Without this, KServe creates headless services (ClusterIP: None) for InferenceServices in RawDeployment mode. The pipelines access vLLM via `http://<model>-predictor.<namespace>.svc.cluster.local:80`, which requires a real ClusterIP.

Verify:

```bash
oc get datasciencecluster default-dsc -o jsonpath='{.spec.components.kserve.rawDeploymentServiceConfig}'
# Should show: Headed
```

## Step 4: Create the Tempo Instance

The Tempo instance stores distributed traces from LlamaStack. This is required for the `responses-simple` and `responses-mcp` pipelines, and for MLflow trace analysis.

```bash
oc apply -f agentic/otel-deployment-jaeger/tempo-monolithic.yaml
```

Verify:

```bash
oc get tempomonolithic tracing -n openshift-tempo-operator
# Should show the instance with a TEMPO VERSION
```

Wait for the Tempo pod to be running:

```bash
oc get pods -n openshift-tempo-operator -l app.kubernetes.io/name=tempo
```

## Step 5: Create Namespaces

Two namespaces are required:

- `tekton-llamastack` — where pipeline/task definitions and ConfigMaps live
- `llamastack-bench` — where benchmark workloads (vLLM, LlamaStack, Postgres, etc.) are deployed

```bash
oc new-project tekton-llamastack
oc new-project llamastack-bench
```

## Step 6: Configure RBAC

### Tekton deployer ServiceAccount

The pipelines use a dedicated ServiceAccount (`tekton-deployer`) with cluster-wide permissions to create and manage benchmark resources across namespaces.

```bash
oc apply -f agentic/tekton-benchmarks/rbac/tekton-deployer-rbac.yaml
```

This creates:
- ServiceAccount `tekton-deployer` in `tekton-llamastack`
- ClusterRole `tekton-deployer-role` with permissions for: deployments, services, secrets, configmaps, PVCs, llamastackdistributions, inferenceservices, servingruntimes, pods, HPA
- ClusterRoleBinding `tekton-deployer-binding`

### SCC anyuid for benchmark namespace

Some workloads (PostgreSQL, OTel Collector) require running as a specific UID. Grant the `anyuid` SCC to the default ServiceAccount in the benchmark namespace:

```bash
oc adm policy add-scc-to-user anyuid system:serviceaccount:llamastack-bench:default
```

## Step 7: Create Secrets

### MLflow credentials (required only if `ENABLE_MLFLOW=true`)

If you want to log benchmark results to AWS SageMaker MLflow, create the credentials secret:

```bash
oc create secret generic mlflow-aws-credentials \
  -n tekton-llamastack \
  --from-literal=AWS_ACCESS_KEY_ID=<your-access-key> \
  --from-literal=AWS_SECRET_ACCESS_KEY=<your-secret-key> \
  --from-literal=MLFLOW_TRACKING_ARN=<your-mlflow-tracking-arn>
```

If you don't need MLflow, skip this step and set `ENABLE_MLFLOW: "false"` in your PipelineRun parameters.

### Image pull secret for LlamaStack (required for responses-simple and responses-mcp pipelines)

The LlamaStack distribution image is hosted on a private Quay.io registry and requires an image pull secret. Create a docker-registry secret and link it to the default ServiceAccount in the benchmark namespace:

```bash
oc create secret docker-registry llamastack-quay-secret \
  -n llamastack-bench \
  --docker-server=quay.io \
  --docker-username=<your-quay-username> \
  --docker-password=<your-quay-password>

oc secrets link default llamastack-quay-secret --for=pull -n llamastack-bench
```

Verify:

```bash
oc get serviceaccount default -n llamastack-bench -o yaml | grep -A5 imagePullSecrets
# Should list llamastack-quay-secret
```

## Step 8: Apply Tekton Pipeline and Task Definitions

Apply all tasks and pipelines to the `tekton-llamastack` namespace:

```bash
oc apply -f agentic/tekton-benchmarks/tasks/ -n tekton-llamastack
oc apply -f agentic/tekton-benchmarks/pipelines/ -n tekton-llamastack
```

Verify:

```bash
tkn pipeline list -n tekton-llamastack
tkn task list -n tekton-llamastack
```

You should see 3 pipelines and 9 tasks.

## Step 9: Verify Setup

Run a quick verification of all components:

```bash
echo "=== Operators ===" && \
oc get csv --all-namespaces --no-headers | awk '{print $2, $NF}' | sort -u && \
echo "=== GPU Node ===" && \
oc get nodes -l node-role.kubernetes.io/worker --no-headers && \
echo "=== LlamaStack Operator ===" && \
oc get pods -n llama-stack-k8s-operator-system --no-headers && \
echo "=== Tempo ===" && \
oc get tempomonolithic -n openshift-tempo-operator && \
echo "=== CRDs ===" && \
oc get crd | grep -E "inferenceservice|servingruntime|llamastack" && \
echo "=== Namespaces ===" && \
oc get ns tekton-llamastack llamastack-bench --no-headers && \
echo "=== RBAC ===" && \
oc get sa tekton-deployer -n tekton-llamastack --no-headers && \
oc get clusterrolebinding tekton-deployer-binding --no-headers && \
echo "=== Pipelines ===" && \
tkn pipeline list -n tekton-llamastack && \
echo "=== Tasks ===" && \
tkn task list -n tekton-llamastack
```