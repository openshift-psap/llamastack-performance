#!/bin/bash

# Sequential LlamaStack + vLLM Concurrency Testing Script (Template-Based)
# This script uses separate YAML template files for job definitions
# Each test gets fresh vLLM + LlamaStack deployments
#
# Usage: ./run-sequential-llamastack-with-templates.sh <output-directory>
# Example: ./run-sequential-llamastack-with-templates.sh ./results/my-test-run

set -e  # Exit on any error

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Go one level up to get benchmarking directory
BENCHMARKING_DIR="$(dirname "$SCRIPT_DIR")"

# Check if output directory is provided as argument
if [ -z "$1" ]; then
    echo "Usage: $0 <output-directory>"
    echo "Example: $0 ./results/my-test-run"
    exit 1
fi

# Configuration
CONCURRENCIES=("128" "64" "32" "16" "8" "4" "2" "1")
BASE_DIR="$1"  # User provides output directory as first argument
NAMESPACE_BENCH="bench"
NAMESPACE_LLAMASTACK="llamastack"
VLLM_SERVICE_NAME="llama-32-3b-instruct"
LLAMASTACK_SERVICE_NAME="llamastack-distribution-v2-23"
GUIDELLM_IMAGE="ghcr.io/ccamacho/bench:main"

# Template file paths (relative to script location)
TEMPLATE_DIR="$BENCHMARKING_DIR/templates"
VLLM_TEMPLATE="$TEMPLATE_DIR/inferenceservice-template.yaml"
LLAMASTACK_TEMPLATE="$TEMPLATE_DIR/llamastack-distribution-template.yaml"
GUIDELLM_TEMPLATE="$TEMPLATE_DIR/guidellm-job-template.yaml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

success() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] SUCCESS:${NC} $1"
}

warning() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1"
}

# Function to substitute variables in template files
substitute_template() {
    local template_file="$1"
    local output_file="$2"
    local concurrency="$3"  # Optional - only used for job templates
    
    # Export variables for envsubst
    export VLLM_SERVICE_NAME="$VLLM_SERVICE_NAME"
    export LLAMASTACK_SERVICE_NAME="$LLAMASTACK_SERVICE_NAME"
    export NAMESPACE_BENCH="$NAMESPACE_BENCH"
    export NAMESPACE_LLAMASTACK="$NAMESPACE_LLAMASTACK"
    
    # Export concurrency if provided (for job templates)
    if [ -n "$concurrency" ]; then
        export CONCURRENCY="$concurrency"
    fi
    
    # Substitute variables in template (only specific variables)
    envsubst '$CONCURRENCY,$VLLM_SERVICE_NAME,$LLAMASTACK_SERVICE_NAME,$NAMESPACE_BENCH,$NAMESPACE_LLAMASTACK' < "$template_file" > "$output_file"
}

# Function to wait for vLLM to be ready
wait_for_vllm() {
    local timeout=300
    local counter=0
    
    log "Waiting for vLLM service to be ready..."
    while [ $counter -lt $timeout ]; do
        if oc get inferenceservice -n $NAMESPACE_BENCH $VLLM_SERVICE_NAME -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null | grep -q "True"; then
            success "vLLM service is ready"
            return 0
        fi
        sleep 5
        counter=$((counter + 5))
        log "Still waiting for vLLM... (${counter}s/${timeout}s)"
    done
    
    error "Timeout waiting for vLLM service to be ready"
    return 1
}

# Function to wait for LlamaStack to be ready
wait_for_llamastack() {
    local timeout=300
    local counter=0
    
    log "Waiting for LlamaStack distribution to be ready..."
    while [ $counter -lt $timeout ]; do
        if oc get llamastackdistribution -n $NAMESPACE_LLAMASTACK $LLAMASTACK_SERVICE_NAME -o jsonpath='{.status.phase}' 2>/dev/null | grep -q "Ready"; then
            success "LlamaStack distribution is ready"
            return 0
        fi
        sleep 5
        counter=$((counter + 5))
        log "Still waiting for LlamaStack... (${counter}s/${timeout}s)"
    done
    
    error "Timeout waiting for LlamaStack distribution to be ready"
    return 1
}

# Function to wait for job completion
wait_for_job_completion() {
    local job_name=$1
    local timeout=900  # 15 minutes
    local counter=0
    
    log "Waiting for job $job_name to complete..."
    
    while [ $counter -lt $timeout ]; do
        # Check job status more thoroughly
        local succeeded=$(oc get job/$job_name -n $NAMESPACE_BENCH -o jsonpath='{.status.succeeded}' 2>/dev/null || echo "0")
        local failed=$(oc get job/$job_name -n $NAMESPACE_BENCH -o jsonpath='{.status.failed}' 2>/dev/null || echo "0")
        local active=$(oc get job/$job_name -n $NAMESPACE_BENCH -o jsonpath='{.status.active}' 2>/dev/null || echo "0")
        
        # Ensure we have numeric values
        succeeded=${succeeded:-0}
        failed=${failed:-0}
        active=${active:-0}
        
        # Check if all containers in the pod have completed
        local pod_name=$(oc get pods -n $NAMESPACE_BENCH -l job-name=$job_name -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
        if [ ! -z "$pod_name" ]; then
            local pod_phase=$(oc get pod/$pod_name -n $NAMESPACE_BENCH -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
            local containers_ready=$(oc get pod/$pod_name -n $NAMESPACE_BENCH -o jsonpath='{.status.containerStatuses[*].ready}' 2>/dev/null || echo "")
            
            # Check if benchmark container has completed (look for completion signal)
            local benchmark_logs=$(oc logs $pod_name -n $NAMESPACE_BENCH -c benchmark --tail=10 2>/dev/null || echo "")
            if echo "$benchmark_logs" | grep -q "Benchmark complete\|done"; then
                success "Job $job_name completed successfully (detected via logs)!"
                return 0
            fi
        fi
        
        if [ "$succeeded" -gt "0" ]; then
            success "Job $job_name completed successfully!"
            return 0
        elif [ "$failed" -gt "0" ]; then
            error "Job $job_name failed!"
            return 1
        fi
        
        sleep 10
        counter=$((counter + 10))
        log "Job still running... (${counter}s/${timeout}s)"
    done
    
    error "Timeout waiting for job $job_name to complete"
    return 1
}

# Function to cleanup resources
cleanup() {
    log "Cleaning up resources..."
    
    # Delete GuideLLM job
    oc delete job -n $NAMESPACE_BENCH --all --ignore-not-found=true
    
    # Delete LlamaStack distribution
    oc delete llamastackdistribution -n $NAMESPACE_LLAMASTACK $LLAMASTACK_SERVICE_NAME --ignore-not-found=true
    
    # Delete vLLM InferenceService
    oc delete inferenceservice -n $NAMESPACE_BENCH $VLLM_SERVICE_NAME --ignore-not-found=true
    
    # Wait for cleanup
    sleep 10
    
    success "Cleanup completed"
}

# Function to save logs
save_logs() {
    local concurrency="$1"
    local job_name="guidellm-llamastack-concurrency-$concurrency"
    local log_dir="$BASE_DIR/concurrency-$concurrency/logs"
    
    log "Saving logs for concurrency $concurrency to $log_dir"
    mkdir -p "$log_dir"
    
    # Save benchmark logs
    if oc get job/$job_name -n $NAMESPACE_BENCH >/dev/null 2>&1; then
        oc logs job/$job_name -n $NAMESPACE_BENCH -c benchmark > "$log_dir/guidellm-benchmark.log"
        success "Saved benchmark logs to $log_dir/guidellm-benchmark.log"
        
        # Save DCGM logs
        oc logs job/$job_name -n $NAMESPACE_BENCH -c dcgm-metrics-scraper > "$log_dir/dcgm-metrics.log"
        success "Saved DCGM logs to $log_dir/dcgm-metrics.log"
        
        # Save vLLM metrics logs
        oc logs job/$job_name -n $NAMESPACE_BENCH -c vllm-metrics-scraper > "$log_dir/vllm-metrics.log"
        success "Saved vLLM metrics logs to $log_dir/vllm-metrics.log"
        
        # Save Prometheus metrics logs
        oc logs job/$job_name -n $NAMESPACE_BENCH -c prometheus-metrics-scraper > "$log_dir/prometheus-metrics.log"
        success "Saved Prometheus metrics logs to $log_dir/prometheus-metrics.log"
        
        # Copy results from job pod to local
        local pod_name=$(oc get pods -n $NAMESPACE_BENCH -l job-name=$job_name -o jsonpath='{.items[0].metadata.name}')
        if [ ! -z "$pod_name" ]; then
            oc cp $NAMESPACE_BENCH/$pod_name:/output/ "$log_dir/" -c sidecar 2>/dev/null || warning "Could not copy result files from pod"
        fi
    else
        warning "Job $job_name not found, skipping log collection"
    fi
    
    # Save vLLM pod logs
    log "Saving vLLM pod logs..."
    local vllm_pod=$(oc get pods -n $NAMESPACE_BENCH -l serving.kserve.io/inferenceservice=$VLLM_SERVICE_NAME -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    if [ ! -z "$vllm_pod" ]; then
        # Save logs from kserve-container (main vLLM container)
        oc logs $vllm_pod -n $NAMESPACE_BENCH -c kserve-container > "$log_dir/vllm-pod.log" 2>/dev/null || warning "Could not get vLLM container logs"
        success "Saved vLLM pod logs to $log_dir/vllm-pod.log"
        
        # Save logs from queue-proxy container if it exists
        oc logs $vllm_pod -n $NAMESPACE_BENCH -c queue-proxy > "$log_dir/vllm-queue-proxy.log" 2>/dev/null || log "No queue-proxy container found"
        
        # Save logs from storage-initializer container if it exists
        oc logs $vllm_pod -n $NAMESPACE_BENCH -c storage-initializer > "$log_dir/vllm-storage-initializer.log" 2>/dev/null || log "No storage-initializer container found"
    else
        warning "vLLM pod not found, skipping vLLM log collection"
    fi
    
    # Save LlamaStack pod logs (all replicas)
    log "Saving LlamaStack pod logs..."
    local llamastack_pods=$(oc get pods -n $NAMESPACE_LLAMASTACK -l app=llama-stack,app.kubernetes.io/instance=$LLAMASTACK_SERVICE_NAME -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || echo "")
    if [ ! -z "$llamastack_pods" ]; then
        local pod_index=0
        for pod in $llamastack_pods; do
            # Save logs from llama-stack container
            oc logs $pod -n $NAMESPACE_LLAMASTACK -c llama-stack > "$log_dir/llamastack-pod-${pod_index}.log" 2>/dev/null || warning "Could not get LlamaStack container logs from $pod"
            success "Saved LlamaStack pod $pod logs to $log_dir/llamastack-pod-${pod_index}.log"
            pod_index=$((pod_index + 1))
        done
    else
        warning "LlamaStack pods not found, skipping LlamaStack log collection"
    fi
}

# Main execution
main() {
    log "Starting Sequential LlamaStack + vLLM Concurrency Testing"
    log "Results will be saved to: $BASE_DIR"
    
    # Check if template files exist
    if [ ! -f "$VLLM_TEMPLATE" ] || [ ! -f "$LLAMASTACK_TEMPLATE" ] || [ ! -f "$GUIDELLM_TEMPLATE" ]; then
        error "Template files not found. Please create template files in $TEMPLATE_DIR"
        exit 1
    fi
    
    # Create base directory
    mkdir -p "$BASE_DIR"
    
    # Initial cleanup
    cleanup
    
    # Run tests for each concurrency level
    for concurrency in "${CONCURRENCIES[@]}"; do
        log "=========================================="
        log "Testing concurrency: $concurrency"
        log "=========================================="
        
        # Create concurrency-specific directory
        local test_dir="$BASE_DIR/concurrency-$concurrency"
        mkdir -p "$test_dir/configs"
        
        # Generate YAML files from templates
        substitute_template "$VLLM_TEMPLATE" "$test_dir/configs/inferenceservice.yaml"
        substitute_template "$LLAMASTACK_TEMPLATE" "$test_dir/configs/llamastack-distribution.yaml"
        substitute_template "$GUIDELLM_TEMPLATE" "$test_dir/configs/guidellm-job.yaml" "$concurrency"
        
        # Deploy vLLM
        log "Deploying vLLM InferenceService..."
        oc apply -f "$test_dir/configs/inferenceservice.yaml"
        wait_for_vllm
        
        # Deploy LlamaStack
        log "Deploying LlamaStack distribution..."
        oc apply -f "$test_dir/configs/llamastack-distribution.yaml"
        wait_for_llamastack
        
        # Wait for Prometheus metrics to accumulate historical data
        log "Waiting 5 minutes for Prometheus metrics to accumulate historical data for rate() calculations..."
        sleep 300
        success "Wait complete. Prometheus should now have enough data for rate calculations."
        
        # Deploy GuideLLM job
        log "Starting GuideLLM benchmark for concurrency $concurrency..."
        oc apply -f "$test_dir/configs/guidellm-job.yaml"
        
        # Wait for job completion (but don't exit on failure)
        if wait_for_job_completion "guidellm-llamastack-concurrency-$concurrency"; then
            log "Job completed successfully"
        else
            warning "Job failed or timed out, but continuing to collect logs..."
        fi
        
        # Save logs BEFORE cleanup (always, even on failure)
        save_logs "$concurrency"
        
        # Cleanup for next iteration
        cleanup
        
        # Cooldown period
        log "Cooldown period before next test..."
        sleep 30
    done
    
    success "All concurrency tests completed!"
    log "Results saved in: $BASE_DIR"
}

# Run main function
main "$@" 