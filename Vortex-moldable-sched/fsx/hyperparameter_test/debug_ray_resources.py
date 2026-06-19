# Save as: debug_ray_resources.py

import ray
ray.init(address="auto", ignore_reinit_error=True)

print("=" * 60)
print("RAY CLUSTER DIAGNOSTIC")
print("=" * 60)

# Check cluster resources
print("\n1. CLUSTER RESOURCES:")
cluster_res = ray.cluster_resources()
for k, v in sorted(cluster_res.items()):
    if not k.startswith("bundle_group") and not k.startswith("CPU_group"):
        print(f"   {k}: {v}")

# Check available resources
print("\n2. AVAILABLE RESOURCES:")
avail_res = ray.available_resources()
for k, v in sorted(avail_res.items()):
    if not k.startswith("bundle_group") and not k.startswith("CPU_group"):
        print(f"   {k}: {v}")

# Check nodes
print("\n3. NODES:")
nodes = ray.nodes()
for node in nodes:
    print(f"   Node: {node['NodeID'][:8]}...")
    print(f"   Alive: {node['Alive']}")
    print(f"   Resources: {node['Resources']}")
    print()

# Check for placement groups that might be blocking resources
print("\n4. PLACEMENT GROUPS:")
try:
    import ray.util.placement_group as pg
    pgs = ray.util.placement_group_table()
    if pgs:
        for pg_id, pg_info in pgs.items():
            print(f"   PG: {pg_id[:8]}... State: {pg_info.get('state')}")
    else:
        print("   No placement groups found")
except Exception as e:
    print(f"   Error checking placement groups: {e}")

# Try to manually request GPU
print("\n5. TESTING GPU ACCESS:")
@ray.remote(num_gpus=1)
def test_gpu():
    import torch
    import os
    return {
        "cuda_visible": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "cuda_available": torch.cuda.is_available(),
        "device_count": torch.cuda.device_count(),
        "device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    }

try:
    result = ray.get(test_gpu.remote(), timeout=30)
    print(f"   GPU task succeeded: {result}")
except Exception as e:
    print(f"   GPU task FAILED: {e}")

print("\n" + "=" * 60)