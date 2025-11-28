# Save as: full_cleanup.py

import ray
ray.init(address="auto", ignore_reinit_error=True)

print("=" * 60)
print("FULL RAY CLEANUP")
print("=" * 60)

print("\n1. Current available resources:")
print(f"   {ray.available_resources()}")

print("\n2. Killing all actors...")
try:
    actors = ray.util.list_named_actors(all_namespaces=True)
    print(f"   Found {len(actors)} named actors")
    for actor_name in actors:
        try:
            actor = ray.get_actor(actor_name)
            ray.kill(actor)
            print(f"   Killed: {actor_name}")
        except:
            pass
except Exception as e:
    print(f"   Error listing actors: {e}")

print("\n3. Removing placement groups...")
try:
    pgs = ray.util.placement_group_table()
    for pg_id, pg_info in pgs.items():
        if pg_info.get('state') == 'CREATED':
            try:
                ray.util.remove_placement_group(ray.util.get_placement_group(pg_id))
                print(f"   Removed PG: {pg_id[:8]}...")
            except:
                pass
except Exception as e:
    print(f"   Error: {e}")

print("\n4. Cancelling pending tasks...")
# Force garbage collection
import gc
gc.collect()

print("\n5. Resources after cleanup:")
import time
time.sleep(2)
print(f"   Cluster: {ray.cluster_resources()}")
print(f"   Available: {ray.available_resources()}")

# Check if CPUs are available
avail = ray.available_resources()
if avail.get('CPU', 0) < 8 or avail.get('GPU', 0) < 2:
    print("\n⚠️  RESOURCES STILL BLOCKED!")
    print("   Run: ray stop && ray start --head ...")
else:
    print("\n✅ Resources are free!")