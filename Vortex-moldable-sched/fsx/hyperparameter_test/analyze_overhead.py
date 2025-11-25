import json
import pandas as pd

# Read all overhead measurements
overhead_data = []
with open('/tmp/overhead_measurements_20250929.jsonl', 'r') as f:
    for line in f:
        overhead_data.append(json.loads(line))

# Convert to DataFrame for analysis
df = pd.json_normalize(overhead_data)

# Key overhead metrics to extract:
print("Model initialization time:", df['timing_breakdown.model_creation_time'].mean())
print("Data loading time:", df['timing_breakdown.dataloader_creation_time'].mean())
print("DDP initialization:", df['timing_breakdown.ddp_initialization_time'].mean())
print("GPU warmup:", df['timing_breakdown.gpu_warmup_time'].mean())
print("Total coordination overhead:", df['timing_breakdown.total_coordination_overhead'].mean())
print("Coordination overhead %:", df['timing_breakdown.coordination_overhead_percentage'].mean())