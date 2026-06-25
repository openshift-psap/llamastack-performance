#!/bin/bash
# Phase 1: Direct vLLM Baseline (PSAP-2056)
#
# Runs Locust directly against vLLM (via RHAIIS) at concurrency levels
# 1, 2, 4, 8, 16, 32, 64, 128 — 3 repetitions each — to establish a
# performance baseline before introducing Llama Stack overhead.
#
# Each test: 5 min warmup + 5 min actual test = 10 min per run
# Cooldown: 5 min between runs
# Total runs: 8 concurrency levels × 3 reps = 24 pipeline runs
#
# MLflow naming:  experiment = "direct-vllm-baseline"
#   run names:    direct-1u-10min-exp1, direct-1u-10min-exp2, ...
#                 direct-128u-10min-exp3
#
# The model is deployed once (first run), then SKIP_DEPLOY=true for subsequent
# runs. Cleanup runs after every test to reset workspace state (PVC/model persists).
#
# Usage:
#   ./scripts/phase1-direct-vllm-baseline.sh
#
# Resume from a specific point (skips completed runs):
#   RESUME_FROM="32-2" ./scripts/phase1-direct-vllm-baseline.sh
#
# Prerequisites:
#   - Logged in to the OpenShift cluster (oc whoami)
#   - Tekton tasks applied (oc apply -f tasks/)
#   - Tekton pipelines applied (oc apply -f pipelines/)
#   - RBAC applied (oc apply -f rbac/)
#   - mlflow-credentials secret exists in tekton-llamastack namespace
#   - Smoke test passed: ./scripts/phase1-smoke-test.sh

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────
NAMESPACE="vllm-direct-bench"
TEKTON_NS="tekton-llamastack"
MODEL_NAME="qwen3-vl-30b-a3b-instruct"
PIPELINE="rhaiis-direct-benchmark"
PVC_NAME="qwen3-vl-model-pvc"
PVC_SIZE="120Gi"
DEPLOY_TIMEOUT="900"

MLFLOW_EXPERIMENT="Rhaiis_direct_benchmark_1000tok"

CONCURRENCY_LEVELS=(128 64 32 16 8 4 2 1)
REPETITIONS=1
WARMUP_SECONDS=300         # 5 minutes
RUN_TIME_SECONDS=300       # 5 minutes
COOLDOWN_SECONDS=300       # 5 minutes between runs
INPUT_TOKENS=1000
OUTPUT_TOKENS=1000

RESUME_FROM="${RESUME_FROM:-}"

# ── Helpers ──────────────────────────────────────────────────────
TOTAL_RUNS=$(( ${#CONCURRENCY_LEVELS[@]} * REPETITIONS ))
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
log "Phase 1: Direct vLLM Baseline (PSAP-2056)"
log "============================================================"
log "Pipeline:      $PIPELINE"
log "Namespace:     $NAMESPACE"
log "Model:         $MODEL_NAME"
log "Concurrency:   ${CONCURRENCY_LEVELS[*]}"
log "Repetitions:   $REPETITIONS per level"
log "Warmup:        ${WARMUP_SECONDS}s (5 min)"
log "Test:          ${RUN_TIME_SECONDS}s (5 min)"
log "Cooldown:      ${COOLDOWN_SECONDS}s (5 min) between runs"
log "Total runs:    $TOTAL_RUNS"
log "MLflow Exp:    $MLFLOW_EXPERIMENT"
if [ -n "$RESUME_FROM" ]; then
    log "Resume from:   $RESUME_FROM (format: USERS-REP, e.g. 32-2)"
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
SINGLE_RUN_EST=$((WARMUP_SECONDS + RUN_TIME_SECONDS + 120))  # +2min overhead (prompt gen, mlflow, cleanup)
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
    for REP in $(seq 1 $REPETITIONS); do
        RUN_COUNTER=$((RUN_COUNTER + 1))
        RUN_TAG="${USERS}-${REP}"

        # Resume support: skip runs until we reach RESUME_FROM
        if [ "$SKIP_ACTIVE" = true ]; then
            if [ "$RUN_TAG" = "$RESUME_FROM" ]; then
                SKIP_ACTIVE=false
                log "Resuming from $RUN_TAG"
            else
                log "[$RUN_COUNTER/$TOTAL_RUNS] Skipping $RUN_TAG (resume mode)"
                continue
            fi
        fi

        RUN_NAME="direct-${USERS}u-10min-exp${REP}"

        # Cooldown between runs (skip before the very first run)
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
        log "  Users: $USERS | Rep: $REP/3 | Warmup: ${WARMUP_SECONDS}s | Test: ${RUN_TIME_SECONDS}s"
        log "============================================================"

        # Deploy only when model isn't already running
        ISVC_READY=$(oc get inferenceservice "$MODEL_NAME" -n "$NAMESPACE" \
            -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "False")
        if [ "$ISVC_READY" = "True" ]; then
            log "Model already running, skipping deploy"
            SKIP_DEPLOY="true"
        else
            log "Model not ready, will deploy"
            SKIP_DEPLOY="false"
        fi

        PR_NAME=$(oc create -f - -n "$TEKTON_NS" -o jsonpath='{.metadata.name}' <<EOF
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: p1-direct-${USERS}u-r${REP}-
  namespace: $TEKTON_NS
  labels:
    phase: "1"
    experiment: "direct-vllm-baseline"
    users: "${USERS}"
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
    - name: MODEL_NAME
      value: "$MODEL_NAME"
    - name: MODEL
      value: "$MODEL_NAME"
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
    - name: SKIP_DEPLOY
      value: "$SKIP_DEPLOY"
    - name: SKIP_CLEANUP
      value: "false"
    - name: CLEANUP_VLLM
      value: "false"
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

# ── Summary ──────────────────────────────────────────────────────
TOTAL_ELAPSED=$(($(date +%s) - START_TIME))
echo ""
log "============================================================"
log "Phase 1 Complete: Direct vLLM Baseline"
log "============================================================"
log "Total runs:    $TOTAL_RUNS"
log "Passed:        $PASS_COUNT"
log "Failed:        $FAIL_COUNT"
log "Total time:    $(elapsed_human $TOTAL_ELAPSED)"
log "MLflow:        $MLFLOW_EXPERIMENT"
log ""
log "Run names in MLflow:"
for U in "${CONCURRENCY_LEVELS[@]}"; do
    for R in $(seq 1 $REPETITIONS); do
        log "  direct-${U}u-10min-exp${R}"
    done
done
log ""
log "Next steps:"
log "  1. Review results in MLflow experiment '$MLFLOW_EXPERIMENT'"
log "  2. Identify vLLM saturation point (throughput plateau)"
log "  3. Proceed to Phase 2 (Llama Stack overhead measurement)"
log "============================================================"

if [ $FAIL_COUNT -gt 0 ]; then
    log "WARNING: $FAIL_COUNT runs failed. Investigate before drawing conclusions."
    exit 1
fi
