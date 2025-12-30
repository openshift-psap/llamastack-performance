#!/bin/bash
#===============================================================================
# LlamaStack Full Test Runner
# 
# This script automates the complete test cycle:
# 1. Cleans up existing resources (llamastack, otel, postgres)
# 2. Redeploys everything fresh
# 3. Applies tracing patches
# 4. Runs locust load test with trace collection
# 5. Saves results to timestamped folder
#
# Usage:
#   ./run_full_test.sh --namespace <ns> --users <n> --spawn-rate <n> --run-time <n>
#
# Example:
#   ./run_full_test.sh --namespace my-project --users 128 --spawn-rate 128 --run-time 60
#===============================================================================

set -e

# Default values
NAMESPACE="avis-project"
USERS=128
SPAWN_RATE=128
RUN_TIME=60
WAIT_AFTER_TEST=30

# Resource names
LLAMASTACK_NAME="llamastack-rhoai32-postgres-otel"
OTEL_DEPLOYMENT="otel-collector"
POSTGRES_DEPLOYMENT="postgres"

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

#===============================================================================
# Parse Arguments
#===============================================================================
while [[ $# -gt 0 ]]; do
    case $1 in
        --namespace|-n)
            NAMESPACE="$2"
            shift 2
            ;;
        --users|-u)
            USERS="$2"
            shift 2
            ;;
        --spawn-rate|-s)
            SPAWN_RATE="$2"
            shift 2
            ;;
        --run-time|-t)
            RUN_TIME="$2"
            shift 2
            ;;
        --wait-after|-w)
            WAIT_AFTER_TEST="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --namespace, -n    Kubernetes namespace (default: avis-project)"
            echo "  --users, -u        Number of concurrent users (default: 128)"
            echo "  --spawn-rate, -s   User spawn rate per second (default: 128)"
            echo "  --run-time, -t     Test duration in seconds (default: 60)"
            echo "  --wait-after, -w   Seconds to wait after test for traces (default: 30)"
            echo "  --help, -h         Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Generate results folder name
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_FOLDER="${SCRIPT_DIR}/locustTest_${NAMESPACE}_u${USERS}_t${RUN_TIME}_${TIMESTAMP}"

#===============================================================================
# Helper Functions
#===============================================================================
log_step() {
    echo -e "${BLUE}==>${NC} ${GREEN}$1${NC}"
}

log_info() {
    echo -e "    ${YELLOW}→${NC} $1"
}

log_error() {
    echo -e "    ${RED}✗${NC} $1"
}

log_success() {
    echo -e "    ${GREEN}✓${NC} $1"
}

# Apply YAML with namespace substitution
apply_yaml_with_namespace() {
    local file=$1
    # Replace hardcoded namespace, placeholder comments (various formats), and service URLs
    sed -e "s/namespace: avis-project/namespace: $NAMESPACE/g" \
        -e "s/# namespace: will be substituted by run_full_test.sh/namespace: $NAMESPACE/g" \
        -e "s/# namespace will be set by run_full_test.sh/namespace: $NAMESPACE/g" \
        -e "s/\.avis-project\./.$NAMESPACE./g" "$file" | oc apply -f -
}

wait_for_pods() {
    local label=$1
    local expected=$2
    local timeout=${3:-300}
    local interval=5
    local elapsed=0
    
    log_info "Waiting for pods with label '$label' to be ready..."
    
    while [ $elapsed -lt $timeout ]; do
        ready=$(oc get pods -n $NAMESPACE -l "$label" --no-headers 2>/dev/null | grep -c "Running" 2>/dev/null || echo "0")
        ready=$(echo "$ready" | head -1 | tr -d '[:space:]')
        if [ "$ready" -ge "$expected" ] 2>/dev/null; then
            log_success "Pods ready ($ready/$expected)"
            return 0
        fi
        sleep $interval
        elapsed=$((elapsed + interval))
        echo -ne "    Waiting... ($elapsed/${timeout}s)\r"
    done
    
    log_error "Timeout waiting for pods"
    return 1
}

wait_for_llamastack() {
    local timeout=${1:-300}
    local interval=10
    local elapsed=0
    
    log_info "Waiting for LlamaStack pods to be ready..."
    
    while [ $elapsed -lt $timeout ]; do
        ready=$(oc get pods -n $NAMESPACE --no-headers 2>/dev/null | grep "^${LLAMASTACK_NAME}-" | grep -c "Running" 2>/dev/null || echo "0")
        ready=$(echo "$ready" | head -1 | tr -d '[:space:]')
        if [ "$ready" -ge 1 ] 2>/dev/null; then
            log_success "LlamaStack pods ready ($ready running)"
            return 0
        fi
        sleep $interval
        elapsed=$((elapsed + interval))
        echo -ne "    Waiting... ($elapsed/${timeout}s)\r"
    done
    
    log_error "Timeout waiting for LlamaStack"
    return 1
}

#===============================================================================
# Main Script
#===============================================================================
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           LlamaStack Full Test Runner                           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Namespace:    ${YELLOW}$NAMESPACE${NC}"
echo -e "  Users:        ${YELLOW}$USERS${NC}"
echo -e "  Spawn Rate:   ${YELLOW}$SPAWN_RATE${NC}"
echo -e "  Run Time:     ${YELLOW}${RUN_TIME}s${NC}"
echo -e "  Results:      ${YELLOW}$RESULTS_FOLDER${NC}"
echo ""

#===============================================================================
# Step 1: Cleanup existing resources
#===============================================================================
log_step "Step 1/8: Cleaning up existing resources..."

log_info "Deleting existing test job..."
oc delete job locust-complete-test -n $NAMESPACE --ignore-not-found 2>/dev/null || true

log_info "Deleting LlamaStack distribution..."
oc delete llamastackdistribution $LLAMASTACK_NAME -n $NAMESPACE --ignore-not-found 2>/dev/null || true

log_info "Waiting for LlamaStack pods to terminate..."
sleep 5
while oc get pods -n $NAMESPACE -l app.kubernetes.io/name=$LLAMASTACK_NAME --no-headers 2>/dev/null | grep -q .; do
    echo -ne "    Waiting for pods to terminate...\r"
    sleep 3
done
log_success "LlamaStack pods terminated"

log_info "Deleting OTEL collector..."
oc delete deployment $OTEL_DEPLOYMENT -n $NAMESPACE --ignore-not-found 2>/dev/null || true
oc delete service $OTEL_DEPLOYMENT -n $NAMESPACE --ignore-not-found 2>/dev/null || true
oc delete configmap otel-collector-config -n $NAMESPACE --ignore-not-found 2>/dev/null || true

log_info "Deleting Postgres..."
oc delete deployment $POSTGRES_DEPLOYMENT -n $NAMESPACE --ignore-not-found 2>/dev/null || true
oc delete service $POSTGRES_DEPLOYMENT -n $NAMESPACE --ignore-not-found 2>/dev/null || true
oc delete pvc postgres-pvc -n $NAMESPACE --ignore-not-found 2>/dev/null || true

log_info "Deleting ConfigMaps..."
oc delete configmap llamastack-tracing-patch -n $NAMESPACE --ignore-not-found 2>/dev/null || true
oc delete configmap llamastack-database-tracing-patch -n $NAMESPACE --ignore-not-found 2>/dev/null || true
oc delete configmap trace-collector-script -n $NAMESPACE --ignore-not-found 2>/dev/null || true
oc delete configmap locust-mcp-deepwiki-test-files -n $NAMESPACE --ignore-not-found 2>/dev/null || true

log_info "Waiting for all pods to terminate..."
sleep 5
log_success "Cleanup complete"

#===============================================================================
# Step 2: Deploy Postgres
#===============================================================================
log_step "Step 2/8: Deploying Postgres..."

cat <<EOF | oc apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: postgres-secret
  namespace: $NAMESPACE
type: Opaque
stringData:
  POSTGRES_DB: llamastack
  POSTGRES_USER: llamastack
  POSTGRES_PASSWORD: SecurePassword123
EOF

cat <<EOF | oc apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
  namespace: $NAMESPACE
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
      - name: postgres
        image: registry.redhat.io/rhel9/postgresql-15@sha256:90ec347a35ab8a5d530c8d09f5347b13cc71df04f3b994bfa8b1a409b1171d59
        ports:
        - containerPort: 5432
        env:
        - name: POSTGRESQL_DATABASE
          valueFrom:
            secretKeyRef:
              name: postgres-secret
              key: POSTGRES_DB
        - name: POSTGRESQL_USER
          valueFrom:
            secretKeyRef:
              name: postgres-secret
              key: POSTGRES_USER
        - name: POSTGRESQL_PASSWORD
          valueFrom:
            secretKeyRef:
              name: postgres-secret
              key: POSTGRES_PASSWORD
        volumeMounts:
        - name: postgres-storage
          mountPath: /var/lib/pgsql/data
      volumes:
      - name: postgres-storage
        emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: $NAMESPACE
spec:
  selector:
    app: postgres
  ports:
  - protocol: TCP
    port: 5432
    targetPort: 5432
  type: ClusterIP
EOF

wait_for_pods "app=postgres" 1 120
log_success "Postgres deployed"

#===============================================================================
# Step 3: Deploy OTEL Collector
#===============================================================================
log_step "Step 3/8: Deploying OTEL Collector..."

apply_yaml_with_namespace "$SCRIPT_DIR/otel-deployment/otel-collector-deployment.yaml"
wait_for_pods "app=otel-collector" 1 120
log_success "OTEL Collector deployed"

#===============================================================================
# Step 4: Apply Patch ConfigMaps
#===============================================================================
log_step "Step 4/8: Applying patch ConfigMaps..."

# Apply patches with namespace substitution (uses apply_yaml_with_namespace for consistency)
apply_yaml_with_namespace "$SCRIPT_DIR/patches/tracing-patch-configmap.yaml"
log_success "Tracing patch ConfigMap applied"

apply_yaml_with_namespace "$SCRIPT_DIR/patches/database-tracing-configmap.yaml"
log_success "Database tracing patch ConfigMap applied"

#===============================================================================
# Step 5: Deploy LlamaStack with patches
#===============================================================================
log_step "Step 5/8: Deploying LlamaStack..."

apply_yaml_with_namespace "$SCRIPT_DIR/otel-deployment/llamastack-distribution.yaml"

sleep 10

log_info "Waiting for LlamaStack deployment to be created..."
DEPLOY_TIMEOUT=120
DEPLOY_ELAPSED=0
while [ $DEPLOY_ELAPSED -lt $DEPLOY_TIMEOUT ]; do
    if oc get deployment $LLAMASTACK_NAME -n $NAMESPACE &>/dev/null; then
        log_success "LlamaStack deployment found"
        break
    fi
    sleep 5
    DEPLOY_ELAPSED=$((DEPLOY_ELAPSED + 5))
done

log_info "Applying volume patches to LlamaStack deployment..."

oc patch deployment/$LLAMASTACK_NAME -n $NAMESPACE --type='json' -p='[
  {"op": "add", "path": "/spec/template/spec/volumes/-", "value": {"name": "tracing-patch", "configMap": {"name": "llamastack-tracing-patch"}}},
  {"op": "add", "path": "/spec/template/spec/containers/0/volumeMounts/-", "value": {"name": "tracing-patch", "mountPath": "/opt/app-root/lib64/python3.12/site-packages/llama_stack/providers/utils/telemetry/tracing.py", "subPath": "tracing.py"}}
]'

oc patch deployment/$LLAMASTACK_NAME -n $NAMESPACE --type='json' -p='[
  {"op": "add", "path": "/spec/template/spec/volumes/-", "value": {"name": "database-tracing-patch", "configMap": {"name": "llamastack-database-tracing-patch"}}},
  {"op": "add", "path": "/spec/template/spec/containers/0/volumeMounts/-", "value": {"name": "database-tracing-patch", "mountPath": "/opt/app-root/lib64/python3.12/site-packages/llama_stack/providers/utils/sqlstore/sqlalchemy_sqlstore.py", "subPath": "sqlalchemy_sqlstore.py"}},
  {"op": "add", "path": "/spec/template/spec/containers/0/volumeMounts/-", "value": {"name": "database-tracing-patch", "mountPath": "/opt/app-root/lib64/python3.12/site-packages/llama_stack/providers/utils/kvstore/postgres/postgres.py", "subPath": "postgres_kvstore.py"}}
]'

log_success "LlamaStack patches applied"

log_info "Waiting for LlamaStack rollout to complete..."
oc rollout status deployment/$LLAMASTACK_NAME -n $NAMESPACE --timeout=300s

log_success "LlamaStack deployed and ready"

#===============================================================================
# Step 6: Apply Test ConfigMaps
#===============================================================================
log_step "Step 6/8: Applying test ConfigMaps..."

apply_yaml_with_namespace "$SCRIPT_DIR/test-job/configmap-locust-test.yaml"
log_success "Locust test ConfigMap applied"

apply_yaml_with_namespace "$SCRIPT_DIR/trace-collector/configmap-trace-collector.yaml"
log_success "Trace collector ConfigMap applied"

#===============================================================================
# Step 7: Create and run the test job
#===============================================================================
log_step "Step 7/8: Running load test..."

cat <<EOF | oc apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: locust-complete-test
  namespace: $NAMESPACE
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 7200
  template:
    metadata:
      labels:
        job-name: locust-complete-test
    spec:
      restartPolicy: Never
      serviceAccountName: trace-collector-sa
      initContainers:
        - name: locust-test
          image: quay.io/opendatahub/llama-stack:latest
          command: ["/bin/sh", "-c"]
          args:
            - |
              locust -f /tests/locustfile.py \
                --host http://${LLAMASTACK_NAME}-service.${NAMESPACE}.svc.cluster.local:8321 \
                --headless \
                --users $USERS \
                --spawn-rate $SPAWN_RATE \
                --run-time ${RUN_TIME}s \
                --csv /output/locust-results \
                --only-summary || true
              echo "Locust test completed"
          env:
            - name: OPENAI_API_KEY
              value: "dummy-key"
            - name: LOCUST_OUTPUT_DIR
              value: "/output"
          volumeMounts:
            - name: test-files
              mountPath: /tests
            - name: results
              mountPath: /output
          resources:
            requests:
              memory: "512Mi"
              cpu: "500m"
            limits:
              memory: "1Gi"
              cpu: "1"
      containers:
        - name: trace-collector
          image: registry.access.redhat.com/ubi9/python-311:latest
          command: ["/bin/bash", "-c"]
          args:
            - |
              set -e
              echo "Installing oc CLI..."
              cd /tmp
              curl -sLO "https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/openshift-client-linux.tar.gz"
              tar xzf openshift-client-linux.tar.gz oc
              export PATH="/tmp:\$PATH"
              
              echo "Running trace collector..."
              python3 /scripts/trace_collector.py
              
              echo "Results:"
              ls -la /output/
              
              echo "Container will exit in 120 seconds..."
              sleep 120
          env:
            - name: TEST_USERS
              value: "$USERS"
            - name: TEST_SPAWN_RATE
              value: "$SPAWN_RATE"
            - name: TEST_RUN_TIME
              value: "$RUN_TIME"
            - name: WAIT_AFTER_TEST
              value: "$WAIT_AFTER_TEST"
            - name: OTEL_NAMESPACE
              value: "$NAMESPACE"
            - name: OTEL_COLLECTOR_LABEL
              value: "app=otel-collector"
            - name: OUTPUT_DIR
              value: "/output"
          volumeMounts:
            - name: scripts
              mountPath: /scripts
            - name: results
              mountPath: /output
          resources:
            requests:
              memory: "256Mi"
              cpu: "100m"
            limits:
              memory: "512Mi"
              cpu: "500m"
      volumes:
        - name: test-files
          configMap:
            name: locust-mcp-deepwiki-test-files
        - name: scripts
          configMap:
            name: trace-collector-script
        - name: results
          emptyDir: {}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: trace-collector-sa
  namespace: $NAMESPACE
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: trace-collector-role
  namespace: $NAMESPACE
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: trace-collector-rolebinding
  namespace: $NAMESPACE
subjects:
  - kind: ServiceAccount
    name: trace-collector-sa
    namespace: $NAMESPACE
roleRef:
  kind: Role
  name: trace-collector-role
  apiGroup: rbac.authorization.k8s.io
EOF

log_success "Test job created"

log_info "Waiting for Locust test to start..."
sleep 10

POD_NAME=""
while [ -z "$POD_NAME" ]; do
    POD_NAME=$(oc get pods -n $NAMESPACE -l job-name=locust-complete-test -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    sleep 2
done
log_info "Test pod: $POD_NAME"

TEST_START_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
log_info "Test start time: $TEST_START_TIME"

log_info "Running Locust test (${RUN_TIME}s + warmup)..."
while true; do
    STATUS=$(oc get pod $POD_NAME -n $NAMESPACE -o jsonpath='{.status.initContainerStatuses[0].state}' 2>/dev/null || echo "")
    if echo "$STATUS" | grep -q "terminated"; then
        break
    fi
    sleep 5
done
log_success "Locust test completed"

log_info "Waiting for trace collection to complete..."
RESULTS_READY=false
MAX_WAIT=300
ELAPSED=0

while [ $ELAPSED -lt $MAX_WAIT ]; do
    PHASE=$(oc get pod $POD_NAME -n $NAMESPACE -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
    
    if oc exec $POD_NAME -n $NAMESPACE -c trace-collector -- sh -c 'ls /output/traces_*_analysis.json' 2>/dev/null; then
        log_success "Analysis files ready"
        RESULTS_READY=true
        break
    fi
    
    if [ "$PHASE" = "Succeeded" ] || [ "$PHASE" = "Failed" ]; then
        log_error "Pod completed before we could copy results"
        break
    fi
    
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

#===============================================================================
# Step 8: Save results
#===============================================================================
log_step "Step 8/8: Saving results..."

mkdir -p "$RESULTS_FOLDER"

log_info "Copying results from pod: $POD_NAME"

if oc cp "$NAMESPACE/$POD_NAME:/output" "$RESULTS_FOLDER"; then
    log_success "Files copied successfully"
else
    log_error "oc cp failed with exit code: $?"
fi

log_info "Saving container logs..."
oc logs $POD_NAME -n $NAMESPACE -c trace-collector > "$RESULTS_FOLDER/trace_collector_logs.txt" 2>/dev/null || true
oc logs $POD_NAME -n $NAMESPACE -c locust-test > "$RESULTS_FOLDER/locust_logs.txt" 2>/dev/null || true

log_info "Saving vLLM logs (from test window: $TEST_START_TIME)..."
VLLM_POD=$(oc get pods -n $NAMESPACE -l serving.kserve.io/inferenceservice=llama-32-3b-instruct -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
if [ -n "$VLLM_POD" ]; then
    oc logs $VLLM_POD -n $NAMESPACE -c kserve-container --since-time="$TEST_START_TIME" > "$RESULTS_FOLDER/vllm_logs.txt" 2>/dev/null || true
    log_success "vLLM logs saved (filtered from $TEST_START_TIME)"
else
    VLLM_POD=$(oc get pods -n $NAMESPACE --no-headers 2>/dev/null | grep "llama-.*-predictor" | awk '{print $1}' | head -1)
    if [ -n "$VLLM_POD" ]; then
        oc logs $VLLM_POD -n $NAMESPACE -c kserve-container --since-time="$TEST_START_TIME" > "$RESULTS_FOLDER/vllm_logs.txt" 2>/dev/null || true
        log_success "vLLM logs saved from $VLLM_POD (filtered from $TEST_START_TIME)"
    else
        log_error "Could not find vLLM pod"
    fi
fi

cat > "$RESULTS_FOLDER/test_parameters.json" <<EOF
{
  "namespace": "$NAMESPACE",
  "users": $USERS,
  "spawn_rate": $SPAWN_RATE,
  "run_time": $RUN_TIME,
  "wait_after_test": $WAIT_AFTER_TEST,
  "timestamp": "$TIMESTAMP",
  "llamastack_distribution": "$LLAMASTACK_NAME"
}
EOF

log_success "Results saved to: $RESULTS_FOLDER"

#===============================================================================
# Summary
#===============================================================================
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    Test Complete!                                ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Results folder: ${YELLOW}$RESULTS_FOLDER${NC}"
echo ""
echo "  Files:"
ls -la "$RESULTS_FOLDER" 2>/dev/null | head -15
echo ""

ANALYSIS_FILE=$(ls "$RESULTS_FOLDER"/traces_*_analysis.json 2>/dev/null | head -1)
if [ -n "$ANALYSIS_FILE" ] && [ -f "$ANALYSIS_FILE" ]; then
    echo -e "  ${GREEN}Analysis Summary:${NC}"
    python3 -c "
import json
with open('$ANALYSIS_FILE') as f:
    data = json.load(f)
    summary = data.get('summary', {})
    print(f\"    Total traces: {summary.get('total_traces', 'N/A')}\")
    print(f\"    Total spans:  {summary.get('total_spans', 'N/A')}\")
    print(f\"    Avg spans/trace: {summary.get('avg_spans_per_trace', 'N/A')}\")
" 2>/dev/null || echo "    (Unable to parse analysis file)"
fi

echo ""
echo -e "${GREEN}Done!${NC}"

