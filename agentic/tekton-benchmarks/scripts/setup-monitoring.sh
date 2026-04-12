#!/bin/bash
# One-time setup for the monitoring stack using OpenShift User Workload Monitoring.
# Deploys Grafana + Pushgateway, installs OTel Operator, connects to cluster Prometheus.
#
# Usage:
#   ./scripts/setup-monitoring.sh
#   ./scripts/setup-monitoring.sh --delete
#   BENCH_NAMESPACE=my-ns ./scripts/setup-monitoring.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFESTS_DIR="$SCRIPT_DIR/../manifests/monitoring"
NAMESPACE="llamastack-monitoring"
BENCH_NAMESPACE="${BENCH_NAMESPACE:-llamastack-bench}"
GRAFANA_SA="grafana"

if [ "$1" = "--delete" ]; then
  echo "=============================================="
  echo "Tearing down monitoring stack"
  echo "=============================================="
  oc delete -f "$MANIFESTS_DIR/grafana-dashboard.yaml" --ignore-not-found=true
  oc delete -f "$MANIFESTS_DIR/grafana-dashboard-ocp.yaml" --ignore-not-found=true
  oc delete -f "$MANIFESTS_DIR/grafana-dashboard-database.yaml" --ignore-not-found=true
  oc delete -f "$MANIFESTS_DIR/grafana-dashboard-inference.yaml" --ignore-not-found=true
  oc delete -f "$MANIFESTS_DIR/grafana-dashboard-gpu.yaml" --ignore-not-found=true
  oc delete -f "$MANIFESTS_DIR/grafana-dashboard-llamastack-deep.yaml" --ignore-not-found=true
  oc delete configmap grafana-datasources -n "$NAMESPACE" --ignore-not-found=true
  oc delete -f "$MANIFESTS_DIR/grafana.yaml" --ignore-not-found=true
  oc delete -f "$MANIFESTS_DIR/prometheus.yaml" --ignore-not-found=true
  oc delete sa "$GRAFANA_SA" -n "$NAMESPACE" --ignore-not-found=true
  oc delete clusterrolebinding grafana-cluster-monitoring-view --ignore-not-found=true
  oc delete -f "$MANIFESTS_DIR/servicemonitors.yaml" -n "$BENCH_NAMESPACE" --ignore-not-found=true
  oc delete namespace "$NAMESPACE" --ignore-not-found=true
  echo "Done."
  exit 0
fi

echo "=============================================="
echo "Setting up LlamaStack monitoring stack"
echo "=============================================="
echo "Monitoring namespace: $NAMESPACE"
echo "Benchmark namespace:  $BENCH_NAMESPACE"
echo ""

# --- 1. Enable User Workload Monitoring ---
echo "--- Enabling User Workload Monitoring ---"
UWM_ENABLED=$(oc get configmap cluster-monitoring-config -n openshift-monitoring \
  -o jsonpath='{.data.config\.yaml}' 2>/dev/null | grep -c "enableUserWorkload: true" || echo "0")

if [ "$UWM_ENABLED" -eq 0 ]; then
  echo "Enabling User Workload Monitoring..."
  oc apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-monitoring-config
  namespace: openshift-monitoring
data:
  config.yaml: |
    enableUserWorkload: true
EOF
  echo "Waiting 30s for monitoring stack to reconcile..."
  sleep 30
else
  echo "User Workload Monitoring already enabled"
fi

# --- 2. Install OTel Operator ---
echo ""
echo "--- Installing OpenTelemetry Operator ---"
if oc get subscription opentelemetry-product -n openshift-operators &>/dev/null; then
  echo "OTel Operator subscription already exists"
else
  echo "Creating OTel Operator subscription..."
  oc apply -f - <<'EOF'
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: opentelemetry-product
  namespace: openshift-operators
spec:
  channel: stable
  installPlanApproval: Automatic
  name: opentelemetry-product
  source: redhat-operators
  sourceNamespace: openshift-marketplace
EOF
  echo "Waiting for OTel Operator to install..."
  sleep 15

  # Auto-approve InstallPlan if stuck on Manual (known OTel Operator quirk)
  for i in $(seq 1 6); do
    PENDING_IP=$(oc get installplan -n openshift-operators -o json 2>/dev/null | \
      python3 -c "
import sys,json
data=json.load(sys.stdin)
for item in data.get('items',[]):
  if not item.get('spec',{}).get('approved',True):
    csvs = item.get('spec',{}).get('clusterServiceVersionNames',[])
    if any('opentelemetry' in c for c in csvs):
      print(item['metadata']['name'])
      break
" 2>/dev/null || echo "")
    if [ -n "$PENDING_IP" ]; then
      echo "Auto-approving OTel InstallPlan: $PENDING_IP"
      oc patch installplan "$PENDING_IP" -n openshift-operators \
        --type=merge -p '{"spec":{"approved":true}}'
      break
    fi
    sleep 10
  done

  # Wait for CRD
  for i in $(seq 1 30); do
    if oc get crd opentelemetrycollectors.opentelemetry.io &>/dev/null; then
      echo "OTel Operator CRD available"
      break
    fi
    sleep 10
  done
fi

# --- 3. Create namespace ---
echo ""
echo "--- Creating namespace ---"
oc apply -f "$MANIFESTS_DIR/namespace.yaml"

# --- 4. Create Grafana SA with cluster-monitoring-view ---
echo ""
echo "--- Creating Grafana ServiceAccount ---"
oc apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: $GRAFANA_SA
  namespace: $NAMESPACE
EOF

oc adm policy add-cluster-role-to-user cluster-monitoring-view \
  -z "$GRAFANA_SA" -n "$NAMESPACE" 2>/dev/null || true

GRAFANA_TOKEN=$(oc create token "$GRAFANA_SA" -n "$NAMESPACE" --duration=8760h 2>/dev/null || echo "")
if [ -z "$GRAFANA_TOKEN" ]; then
  echo "ERROR: Could not obtain SA token"
  exit 1
fi
echo "SA token obtained"

# --- 5. Create datasource ConfigMap with token ---
echo ""
echo "--- Creating Grafana datasource ---"
oc apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-datasources
  namespace: $NAMESPACE
data:
  datasources.yaml: |
    apiVersion: 1
    datasources:
    - name: OpenShift Prometheus
      type: prometheus
      access: proxy
      url: https://thanos-querier.openshift-monitoring.svc:9091
      isDefault: true
      uid: prometheus
      jsonData:
        tlsSkipVerify: true
        httpHeaderName1: Authorization
      secureJsonData:
        httpHeaderValue1: "Bearer ${GRAFANA_TOKEN}"
    - name: Tempo
      type: tempo
      access: proxy
      url: http://tempo-tracing.openshift-tempo-operator.svc.cluster.local:3200
      uid: tempo
      editable: true
EOF

# --- 6. Deploy Pushgateway ---
echo ""
echo "--- Deploying Pushgateway ---"
oc apply -f "$MANIFESTS_DIR/prometheus.yaml"

# --- 7. Deploy all dashboards + Grafana ---
echo ""
echo "--- Deploying Grafana + Dashboards ---"
oc apply -f "$MANIFESTS_DIR/grafana-dashboard.yaml"
oc apply -f "$MANIFESTS_DIR/grafana-dashboard-ocp.yaml"
oc apply -f "$MANIFESTS_DIR/grafana-dashboard-database.yaml"
oc apply -f "$MANIFESTS_DIR/grafana-dashboard-inference.yaml"
oc apply -f "$MANIFESTS_DIR/grafana-dashboard-gpu.yaml"
oc apply -f "$MANIFESTS_DIR/grafana-dashboard-llamastack-deep.yaml"
oc apply -f "$MANIFESTS_DIR/grafana.yaml"

# --- 8. Apply ServiceMonitors to benchmark namespace (if it exists) ---
echo ""
echo "--- Applying ServiceMonitors ---"
if oc get namespace "$BENCH_NAMESPACE" &>/dev/null; then
  echo "Applying ServiceMonitors to $BENCH_NAMESPACE..."
  oc apply -f "$MANIFESTS_DIR/servicemonitors.yaml" -n "$BENCH_NAMESPACE"
else
  echo "Namespace $BENCH_NAMESPACE does not exist yet — ServiceMonitors will be"
  echo "created automatically by the pipeline tasks (deploy-tracing, deploy-postgres)."
fi

# DCGM ServiceMonitor (in nvidia-gpu-operator namespace, if it exists)
if oc get namespace nvidia-gpu-operator &>/dev/null; then
  echo "Applying DCGM ServiceMonitor to nvidia-gpu-operator..."
  oc apply -f - <<'EOF'
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: nvidia-dcgm-exporter
  namespace: nvidia-gpu-operator
  labels:
    app: nvidia-dcgm-exporter
spec:
  endpoints:
    - path: /metrics
      port: gpu-metrics
      interval: 5s
  namespaceSelector:
    matchNames:
      - nvidia-gpu-operator
  selector:
    matchLabels:
      app: nvidia-dcgm-exporter
EOF
else
  echo "nvidia-gpu-operator namespace not found — GPU dashboard will be empty"
fi

# --- 9. Wait for pods ---
echo ""
echo "--- Waiting for Pushgateway ---"
oc wait --for=condition=available deployment/pushgateway -n "$NAMESPACE" --timeout=120s

echo ""
echo "--- Waiting for Grafana ---"
oc wait --for=condition=available deployment/grafana -n "$NAMESPACE" --timeout=120s

echo ""
echo "=============================================="
echo "Monitoring stack ready!"
echo "=============================================="
echo ""
echo "To access Grafana:"
echo "  oc port-forward svc/grafana 3000:3000 -n $NAMESPACE"
echo "  Then open: http://localhost:3000"
echo "  Login: admin / llamastack"
echo ""
echo "Dashboards:"
echo "  - Overview (Locust + Trace analysis + HPA)"
echo "  - Cluster (OCP nodes + pods)"
echo "  - Database (PostgreSQL + connection pool)"
echo "  - Inference (vLLM serving metrics)"
echo "  - GPU (NVIDIA DCGM hardware)"
echo "  - LlamaStack (OTel application deep dive)"
echo ""
echo "Datasource: OpenShift Prometheus (thanos-querier)"
echo "Pushgateway: pushgateway.$NAMESPACE.svc:9091"
