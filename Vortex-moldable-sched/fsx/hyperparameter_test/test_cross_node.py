# Save as: test_cross_node.py

import ray
ray.init(address="auto", ignore_reinit_error=True)

print("Testing cross-node task execution...")

# Get node IPs
nodes = ray.nodes()
for node in nodes:
    node_ip = [k for k in node['Resources'].keys() if k.startswith('node:') and k != 'node:__internal_head__'][0]
    print(f"Node: {node_ip}")

# Test running on each node specifically
@ray.remote(num_gpus=1)
def test_on_node():
    import torch
    import os
    import socket
    return {
        "hostname": socket.gethostname(),
        "ip": socket.gethostbyname(socket.gethostname()),
        "cuda": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "cuda_visible": os.environ.get("CUDA_VISIBLE_DEVICES"),
    }

# Run on node 1 (head)
print("\n--- Testing GPU on HEAD node ---")
try:
    result = ray.get(
        test_on_node.options(
            resources={"node:172.31.45.35": 0.01}
        ).remote(),
        timeout=60
    )
    print(f"SUCCESS: {result}")
except Exception as e:
    print(f"FAILED: {e}")

# Run on node 2 (worker)
print("\n--- Testing GPU on WORKER node ---")
try:
    result = ray.get(
        test_on_node.options(
            resources={"node:172.31.44.148": 0.01}
        ).remote(),
        timeout=60
    )
    print(f"SUCCESS: {result}")
except Exception as e:
    print(f"FAILED: {e}")

print("\nDone!")