#!/bin/bash
# One-time setup for the monitoring stack using OpenShift User Workload Monitoring.
# Deploys Grafana + Pushgateway, connects to cluster Prometheus via SA token.
#
# Usage:
#   ./scripts/setup-monitoring.sh
#   ./scripts/setup-monitoring.sh --delete
#   BENCH_NAMESPACE=my-ns ./scripts/setup-monitoring.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFESTS_DIR="$SCRIPT_DIR/../manifests/monitoring"
NAMESPACE="llamastack-monitoring"
BENCH_NAMESPACE="${BENCH_NAMESPACE:-avis-project}"
GRAFANA_SA="grafana"

if [ "$1" = "--delete" ]; then
  echo "=============================================="
  echo "Tearing down monitoring stack"
  echo "=============================================="
  oc delete -f "$MANIFESTS_DIR/grafana-dashboard.yaml" --ignore-not-found=true
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

# --- 2. Create namespace ---
echo ""
echo "--- Creating namespace ---"
oc apply -f "$MANIFESTS_DIR/namespace.yaml"

# --- 3. Create Grafana SA with cluster-monitoring-view ---
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

# --- 4. Create datasource ConfigMap with token ---
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

# --- 5. Deploy Pushgateway ---
echo ""
echo "--- Deploying Pushgateway ---"
oc apply -f "$MANIFESTS_DIR/prometheus.yaml"

# --- 6. Deploy Grafana dashboard + Grafana ---
echo ""
echo "--- Deploying Grafana ---"
oc apply -f "$MANIFESTS_DIR/grafana-dashboard.yaml"
oc apply -f "$MANIFESTS_DIR/grafana.yaml"

# --- 7. Apply ServiceMonitors to benchmark namespace ---
echo ""
echo "--- Applying ServiceMonitors to $BENCH_NAMESPACE ---"
oc apply -f "$MANIFESTS_DIR/servicemonitors.yaml" -n "$BENCH_NAMESPACE"

# --- 8. Wait for pods ---
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
echo "Datasource: OpenShift Prometheus (thanos-querier)"
echo "Pushgateway: pushgateway.$NAMESPACE.svc:9091"
echo ""
echo "The dashboard 'LlamaStack Benchmark' is auto-provisioned."
echo "Select your benchmark namespace and run_id in the dropdowns."
