#!/bin/bash
# ============================================================================
# Profile all 36 combinations on g4dn.xlarge cluster at IMAGE_SIZE=64
#
# Grid: 3 models x 3 worker counts x 4 epoch counts = 36 runs
# Run from the Ray HEAD NODE with 4-node Ray cluster already active
#
# Usage:
#   cd /fsx/hyperparameter_test
#   bash profile_all_g4.sh
#
# Output: /fsx/hyperparameter_test/g4_img64.jsonl (consolidated)
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TRAIN_SCRIPT="$SCRIPT_DIR/train_cifar10_torch_oomsafe_instrumented.py"
OUTPUT_FILE="$SCRIPT_DIR/g4_img64.jsonl"
MEASUREMENT_DIR="$SCRIPT_DIR/measurements"
IMAGE_SIZE=64
INSTANCE_TYPE="g4dn.xlarge"

MODELS=("vgg19" "wide_resnet101_2" "convnext_large")
WORKERS=(1 2 4)
EPOCHS=(3 6 9 12)

# Clear previous output and measurement dir
> "$OUTPUT_FILE"
rm -rf "$MEASUREMENT_DIR"
mkdir -p "$MEASUREMENT_DIR"

TOTAL=$((${#MODELS[@]} * ${#WORKERS[@]} * ${#EPOCHS[@]}))
COUNT=0
FAILED=0

echo "=== Starting profiling: $TOTAL combinations ==="
echo "=== Image size: $IMAGE_SIZE, Instance: $INSTANCE_TYPE ==="
echo "=== Output: $OUTPUT_FILE ==="
echo "=== Measurements dir: $MEASUREMENT_DIR (shared via FSx) ==="
echo ""

for model in "${MODELS[@]}"; do
    for workers in "${WORKERS[@]}"; do
        for epochs in "${EPOCHS[@]}"; do
            COUNT=$((COUNT + 1))
            echo "--- [$COUNT/$TOTAL] model=$model workers=$workers epochs=$epochs ---"

            # Clear measurement dir before each run
            rm -f "$MEASUREMENT_DIR"/overhead_measurements_*.jsonl

            # Run profiling — OVERHEAD_LOG_DIR points to shared FSx path
            MEASURE=1 \
            MODEL_NAME="$model" \
            EPOCHS="$epochs" \
            HOSTS="$workers" \
            IMAGE_SIZE="$IMAGE_SIZE" \
            INSTANCE_TYPE="$INSTANCE_TYPE" \
            OVERHEAD_LOG_DIR="$MEASUREMENT_DIR" \
            BATCH_SIZE=64 \
            NUM_SAMPLES=1 \
                python3 "$TRAIN_SCRIPT" 2>&1 | tail -5

            # Collect result from shared FSx measurement dir
            if ls "$MEASUREMENT_DIR"/overhead_measurements_*.jsonl 1>/dev/null 2>&1; then
                # Take the last line (most recent entry)
                tail -1 "$MEASUREMENT_DIR"/overhead_measurements_*.jsonl >> "$OUTPUT_FILE"
                TRAINING_TIME=$(python3 -c "
import json
with open('$OUTPUT_FILE') as f:
    lines = f.readlines()
    last = json.loads(lines[-1])
    print(f\"{last['timing_breakdown']['total_training_time']:.1f}s\")
" 2>/dev/null || echo "?")
                echo "    => Training time: $TRAINING_TIME"
            else
                echo "    => FAILED: No measurement file found!"
                FAILED=$((FAILED + 1))
            fi

            echo ""
        done
    done
done

echo "============================================="
echo "Profiling complete!"
echo "  Total: $TOTAL | Succeeded: $((TOTAL - FAILED)) | Failed: $FAILED"
echo "  Output: $OUTPUT_FILE"
echo "  Entries: $(wc -l < "$OUTPUT_FILE") lines"
echo "============================================="

# Quick summary
python3 -c "
import json
data = []
with open('$OUTPUT_FILE') as f:
    for line in f:
        if line.strip():
            entry = json.loads(line)
            c = entry['configuration']
            t = entry['timing_breakdown']['total_training_time']
            data.append((c['model_name'], c['num_workers'], c['epochs'], t))

print('\nRuntime Summary (seconds):')
print(f'{\"Model\":<20} {\"Workers\":>7} {\"Epochs\":>6} {\"Time(s)\":>10}')
print('-' * 50)
for model, workers, epochs, time_s in sorted(data):
    print(f'{model:<20} {workers:>7} {epochs:>6} {time_s:>10.1f}')
" 2>/dev/null || true
