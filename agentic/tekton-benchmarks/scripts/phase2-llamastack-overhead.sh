#!/bin/bash
# Phase 2: LlamaStack Overhead (PSAP-2057)
#
# Quantifies the performance overhead introduced by Llama Stack's routing
# and processing layer. Runs LlamaStack (no OTel) with ResponsesSimpleUser
# at the same concurrency levels as Phase 1 — 3 reps each — with 1, 2,
# and 4 LlamaStack replicas.
#
# Each test: 5 min warmup + 5 min actual test = 10 min per run
# Cooldown: 5 min between runs
# Total runs: 8 concurrency × 3 replica configs × 3 reps = 72 pipeline runs
#
# MLflow naming:  experiment = "Rhaiis_llamastack_overhead"
#   run names:    ls-overhead-1u-1r-10min-exp1, ls-overhead-1u-1r-10min-exp2, ...
#                 ls-overhead-128u-4r-10min-exp3
#
# vLLM model is deployed once and reused. LlamaStack + Postgres are
# redeployed fresh each run (clean DB state, correct replica count).
# Final cleanup at end.
#
# Usage:
#   ./scripts/phase2-llamastack-overhead.sh
#
# Resume from a specific point (skips completed runs):
#   RESUME_FROM="1-1-2" ./scripts/phase2-llamastack-overhead.sh
#   (format: USERS-REPLICAS-REP, e.g. 32-2-2 = 32 users, 2 replicas, rep 2)
#
# Prerequisites:
#   - Logged in to the OpenShift cluster (oc whoami)
#   - Tekton tasks/pipelines applied (oc apply -f tasks/ -f pipelines/)
#   - RBAC applied (oc apply -f rbac/)
#   - mlflow-credentials secret exists in tekton-llamastack namespace
#   - Smoke test passed: ./scripts/phase2-smoke-test.sh

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────
NAMESPACE="llamastack-bench"
TEKTON_NS="tekton-llamastack"
MODEL_NAME="qwen3-vl-30b-a3b-instruct"
PIPELINE="rhaiis-llamastack-simple-no-otel-benchmark"
PVC_NAME="qwen3-vl-model-pvc"
PVC_SIZE="120Gi"
DEPLOY_TIMEOUT="900"
DISTRIBUTION_NAME="llamastack-benchmark"

MLFLOW_EXPERIMENT="Rhaiis_llamastack_overhead_1000tok"

CONCURRENCY_LEVELS=(64 32 16 8 4 2 1)
REPLICA_COUNTS=(1)
REPETITIONS=1
WARMUP_SECONDS=300         # 5 minutes
RUN_TIME_SECONDS=300       # 5 minutes
COOLDOWN_SECONDS=300       # 5 minutes between runs
INPUT_TOKENS=1000
OUTPUT_TOKENS=1000

RESUME_FROM="${RESUME_FROM:-}"

# ── Helpers ──────────────────────────────────────────────────────
TOTAL_RUNS=$(( ${#CONCURRENCY_LEVELS[@]} * ${#REPLICA_COUNTS[@]} * REPETITIONS ))
RUN_COUNTER=0
PASS_COUNT=0
FAIL_COUNT=0
START_TIME=$(date +%s)

log() { echo "[$(date '+%H:%M:%S')] $*"; }

wait_for_pipelinerun() {
    local name="$1"
    log "Waiting for PipelineRun $name to complete..."
    while true; do
        STATUS=$(oc get pipelinerun "$name" -n "$TEKTON_NS" \
            -o jsonpath='{.status.conditions[0].reason}' 2>/dev/null || echo "Pending")
        case "$STATUS" in
            Succeeded)
                log "PipelineRun $name SUCCEEDED"
                return 0
                ;;
            Failed|PipelineRunCancelled|CouldntGetPipeline|CreateRunFailed)
                log "PipelineRun $name FAILED (reason: $STATUS)"
                oc get pipelinerun "$name" -n "$TEKTON_NS" \
                    -o jsonpath='{.status.conditions[0].message}' 2>/dev/null || true
                echo ""
                return 1
                ;;
            *)
                TASK=$(oc get pipelinerun "$name" -n "$TEKTON_NS" \
                    -o jsonpath='{.status.childReferences[-1:].pipelineTaskName}' 2>/dev/null || echo "?")
                printf "\r  Status: %-20s  Active task: %-30s" "$STATUS" "$TASK"
                sleep 15
                ;;
        esac
    done
}

elapsed_human() {
    local secs=$1
    printf "%dh %02dm %02ds" $((secs/3600)) $((secs%3600/60)) $((secs%60))
}

# ── Pre-flight checks ───────────────────────────────────────────
log "============================================================"
log "Phase 2: LlamaStack Overhead (PSAP-2057)"
log "============================================================"
log "Pipeline:      $PIPELINE"
log "Namespace:     $NAMESPACE"
log "Model:         $MODEL_NAME"
log "Distribution:  $DISTRIBUTION_NAME"
log "Concurrency:   ${CONCURRENCY_LEVELS[*]}"
log "Replicas:      ${REPLICA_COUNTS[*]}"
log "Repetitions:   $REPETITIONS per combo"
log "Warmup:        ${WARMUP_SECONDS}s (5 min)"
log "Test:          ${RUN_TIME_SECONDS}s (5 min)"
log "Cooldown:      ${COOLDOWN_SECONDS}s (5 min) between runs"
log "Total runs:    $TOTAL_RUNS"
log "MLflow Exp:    $MLFLOW_EXPERIMENT"
log "OTel:          DISABLED"
if [ -n "$RESUME_FROM" ]; then
    log "Resume from:   $RESUME_FROM (format: USERS-REPLICAS-REP, e.g. 32-2-2)"
fi
log "============================================================"
echo ""

if ! oc whoami &>/dev/null; then
    log "ERROR: Not logged in to OpenShift. Run 'oc login' first."
    exit 1
fi

log "Logged in as: $(oc whoami)"
log "Cluster: $(oc whoami --show-server)"
echo ""

# ── Estimated duration ───────────────────────────────────────────
SINGLE_RUN_EST=$((WARMUP_SECONDS + RUN_TIME_SECONDS + 180))  # +3min overhead (deploy LS/PG, prompt gen, mlflow, cleanup)
TOTAL_EST=$(( TOTAL_RUNS * SINGLE_RUN_EST + (TOTAL_RUNS - 1) * COOLDOWN_SECONDS + DEPLOY_TIMEOUT ))
log "Estimated total time: ~$(elapsed_human $TOTAL_EST)"
log "Press Ctrl+C within 10s to abort..."
sleep 10
echo ""

# ── Run matrix ───────────────────────────────────────────────────
SKIP_ACTIVE=false
if [ -n "$RESUME_FROM" ]; then
    SKIP_ACTIVE=true
fi

FIRST_RUN=true
for USERS in "${CONCURRENCY_LEVELS[@]}"; do
    for REPLICAS in "${REPLICA_COUNTS[@]}"; do
        for REP in $(seq 1 $REPETITIONS); do
            RUN_COUNTER=$((RUN_COUNTER + 1))
            RUN_TAG="${USERS}-${REPLICAS}-${REP}"

            if [ "$SKIP_ACTIVE" = true ]; then
                if [ "$RUN_TAG" = "$RESUME_FROM" ]; then
                    SKIP_ACTIVE=false
                    log "Resuming from $RUN_TAG"
                else
                    log "[$RUN_COUNTER/$TOTAL_RUNS] Skipping $RUN_TAG (resume mode)"
                    continue
                fi
            fi

            RUN_NAME="ls-overhead-${USERS}u-${REPLICAS}r-10min-exp${REP}"

            if [ "$FIRST_RUN" = false ]; then
                log "Cooldown: ${COOLDOWN_SECONDS}s before next run..."
                COOL_ELAPSED=0
                while [ $COOL_ELAPSED -lt $COOLDOWN_SECONDS ]; do
                    sleep 30
                    COOL_ELAPSED=$((COOL_ELAPSED + 30))
                    printf "\r  Cooldown: %ds / %ds" "$COOL_ELAPSED" "$COOLDOWN_SECONDS"
                done
                echo ""
            fi
            FIRST_RUN=false

            log "============================================================"
            log "[$RUN_COUNTER/$TOTAL_RUNS] $RUN_NAME"
            log "  Users: $USERS | Replicas: $REPLICAS | Rep: $REP/3 | Warmup: ${WARMUP_SECONDS}s | Test: ${RUN_TIME_SECONDS}s"
            log "============================================================"

            ISVC_READY=$(oc get inferenceservice "$MODEL_NAME" -n "$NAMESPACE" \
                -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "False")
            if [ "$ISVC_READY" = "True" ]; then
                log "RHAIIS model already running, skipping deploy"
                SKIP_RHAIIS="true"
            else
                log "RHAIIS model not ready, will deploy"
                SKIP_RHAIIS="false"
            fi

            PR_NAME=$(oc create -f - -n "$TEKTON_NS" -o jsonpath='{.metadata.name}' <<EOF
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: p2-ls-${USERS}u-${REPLICAS}r-r${REP}-
  namespace: $TEKTON_NS
  labels:
    phase: "2"
    experiment: "llamastack-overhead"
    users: "${USERS}"
    replicas: "${REPLICAS}"
    repetition: "${REP}"
spec:
  pipelineRef:
    name: $PIPELINE
  taskRunTemplate:
    serviceAccountName: tekton-deployer
  workspaces:
    - name: results
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 5Gi
  params:
    - name: NAMESPACE
      value: "$NAMESPACE"
    - name: DISTRIBUTION_NAME
      value: "$DISTRIBUTION_NAME"
    - name: MODEL_NAME
      value: "$MODEL_NAME"
    - name: MODEL
      value: "vllm-inference/$MODEL_NAME"
    - name: USERS
      value: "$USERS"
    - name: SPAWN_RATE
      value: "$USERS"
    - name: RUN_TIME_SECONDS
      value: "$RUN_TIME_SECONDS"
    - name: INPUT_TOKENS
      value: "$INPUT_TOKENS"
    - name: OUTPUT_TOKENS
      value: "$OUTPUT_TOKENS"
    - name: WARMUP_SECONDS
      value: "$WARMUP_SECONDS"
    - name: LOAD_SHAPE
      value: "steady"
    - name: ENABLE_MLFLOW
      value: "true"
    - name: MLFLOW_EXPERIMENT
      value: "$MLFLOW_EXPERIMENT"
    - name: MLFLOW_RUN_NAME_PREFIX
      value: "$RUN_NAME"
    - name: PVC_NAME
      value: "$PVC_NAME"
    - name: PVC_SIZE
      value: "$PVC_SIZE"
    - name: DEPLOY_TIMEOUT
      value: "$DEPLOY_TIMEOUT"
    - name: SKIP_DEPLOY_RHAIIS
      value: "$SKIP_RHAIIS"
    - name: SKIP_DEPLOY_POSTGRES
      value: "false"
    - name: SKIP_DEPLOY_LLAMASTACK
      value: "false"
    - name: SKIP_CLEANUP
      value: "false"
    - name: CLEANUP_VLLM
      value: "true"
    - name: REPLICAS
      value: "$REPLICAS"
EOF
            )

            log "Created PipelineRun: $PR_NAME"

            if wait_for_pipelinerun "$PR_NAME"; then
                PASS_COUNT=$((PASS_COUNT + 1))
                log "PASS [$RUN_COUNTER/$TOTAL_RUNS] $RUN_NAME"
            else
                FAIL_COUNT=$((FAIL_COUNT + 1))
                log "FAIL [$RUN_COUNTER/$TOTAL_RUNS] $RUN_NAME"
                log "Continuing to next run despite failure..."
            fi

            ELAPSED=$(($(date +%s) - START_TIME))
            REMAINING_RUNS=$((TOTAL_RUNS - RUN_COUNTER))
            if [ $RUN_COUNTER -gt 0 ]; then
                AVG_PER_RUN=$((ELAPSED / RUN_COUNTER))
                ETA=$((REMAINING_RUNS * AVG_PER_RUN))
                log "Progress: $RUN_COUNTER/$TOTAL_RUNS | Elapsed: $(elapsed_human $ELAPSED) | ETA: ~$(elapsed_human $ETA)"
            fi
            echo ""
        done
    done
done

# ── Summary ──────────────────────────────────────────────────────
TOTAL_ELAPSED=$(($(date +%s) - START_TIME))
echo ""
log "============================================================"
log "Phase 2 Complete: LlamaStack Overhead"
log "============================================================"
log "Total runs:    $TOTAL_RUNS"
log "Passed:        $PASS_COUNT"
log "Failed:        $FAIL_COUNT"
log "Total time:    $(elapsed_human $TOTAL_ELAPSED)"
log "MLflow:        $MLFLOW_EXPERIMENT"
log ""
log "Run names in MLflow:"
for U in "${CONCURRENCY_LEVELS[@]}"; do
    for RP in "${REPLICA_COUNTS[@]}"; do
        for R in $(seq 1 $REPETITIONS); do
            log "  ls-overhead-${U}u-${RP}r-10min-exp${R}"
        done
    done
done
log ""
log "Next steps:"
log "  1. Compare Phase 2 results against Phase 1 ('Rhaiis_direct_benchmark')"
log "  2. Quantify LlamaStack overhead per concurrency level"
log "  3. Determine if replica scaling is needed to match Phase 1 throughput"
log "============================================================"

if [ $FAIL_COUNT -gt 0 ]; then
    log "WARNING: $FAIL_COUNT runs failed. Investigate before drawing conclusions."
    exit 1
fi
