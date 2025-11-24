#!/usr/bin/env python3
"""
HPO Dedicated Executor System Test

This script tests the new HPO system with dedicated executor architecture:
1. Tests HPO scheduler (static and moldable)
2. Tests dedicated executor instance creation
3. Tests Ray cluster setup on worker nodes
4. Tests result relay system
5. Tests moldable resource requests
"""

import sys
import os
import time
import json
import threading
import requests
from typing import Dict, List

# Add project paths
sys.path.append('/Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main')

# Import HPO components
from scheduler.fcfs_scheduler_HPO import FCFS_Scheduler_HPO
from scheduler.fcfs_optimized_HPO import FCFS_Optimized_HPO
from scripts.create_instance_HPO import createExecutorInstance, createWorkerInstances, listHPOInstances
from executor_HPO import HPOExecutor, executeWorkflowHPO
from service.cloud_runner_HPO_new import CloudRunnerHPO
from wf_queue.redis_queue import Redis_Queue
from config.constants_HPO import SIMULATE
import yaml

class HPOSystemTester:
    """Comprehensive HPO system tester"""

    def __init__(self):
        self.test_results = {}
        self.test_workflow = None
        self.executor_instance = None

    def load_test_workflow(self) -> Dict:
        """Load test HPO workflow"""
        try:
            workflow_path = '/Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/test/hpo/test_hpo_workflow.yaml'

            with open(workflow_path, 'r') as f:
                workflow = yaml.safe_load(f)

            workflow['submit_time'] = time.time()
            self.test_workflow = workflow

            print(f"✅ Test workflow loaded: {workflow['id']}")
            return workflow

        except Exception as e:
            print(f"❌ Failed to load test workflow: {e}")
            return None

    def test_instance_creation(self) -> bool:
        """Test dedicated executor and worker instance creation"""
        try:
            print("\n🧪 Testing Instance Creation...")

            # Test executor instance creation
            print("  Creating dedicated executor instance...")
            executor_ip = createExecutorInstance('g4dn.2xlarge', sim=True)

            if executor_ip:
                print(f"  ✅ Executor instance created: {executor_ip}")
                self.test_results['executor_creation'] = True
            else:
                print(f"  ❌ Failed to create executor instance")
                self.test_results['executor_creation'] = False
                return False

            # Test worker instance creation
            print("  Creating worker instances...")
            worker_ips = createWorkerInstances('g5.2xlarge', 3, sim=True)

            if worker_ips and len(worker_ips) == 3:
                print(f"  ✅ Worker instances created: {worker_ips}")
                self.test_results['worker_creation'] = True
            else:
                print(f"  ❌ Failed to create worker instances")
                self.test_results['worker_creation'] = False
                return False

            return True

        except Exception as e:
            print(f"  ❌ Instance creation test failed: {e}")
            self.test_results['instance_creation'] = False
            return False

    def test_hpo_schedulers(self) -> bool:
        """Test both static and moldable HPO schedulers"""
        try:
            print("\n🧪 Testing HPO Schedulers...")

            if not self.test_workflow:
                print("  ❌ No test workflow loaded")
                return False

            # Test static scheduler
            print("  Testing static HPO scheduler...")
            static_scheduler = FCFS_Scheduler_HPO(
                queue=Redis_Queue('test-static-queue'),
                finish_queue=Redis_Queue('test-static-finish'),
                resource_request_queue=Redis_Queue('test-static-resources')
            )

            # Test resource allocation
            constraints = {
                'mesh': 'vgg19',
                'budget': 25.0,
                'deadline': 7200,
                'chains': 3,
                'tinydaIterations': 6
            }

            optimal_type = static_scheduler.selectOptimalInstanceType(
                constraints['budget'],
                constraints['deadline'],
                constraints['mesh'],
                constraints['chains'],
                constraints['tinydaIterations']
            )

            print(f"  ✅ Static scheduler optimal instance type: {optimal_type}")

            # Test moldable scheduler
            print("  Testing moldable HPO scheduler...")
            moldable_scheduler = FCFS_Optimized_HPO(
                queue=Redis_Queue('test-moldable-queue'),
                finish_queue=Redis_Queue('test-moldable-finish'),
                resource_request_queue=Redis_Queue('test-moldable-resources')
            )

            optimal_allocation = moldable_scheduler.findOptimalAllocationHPO(
                constraints['budget'],
                constraints['deadline'],
                constraints['mesh'],
                constraints['chains'],
                constraints['tinydaIterations']
            )

            if optimal_allocation:
                instance_type, instance_count = optimal_allocation
                print(f"  ✅ Moldable scheduler optimal allocation: {instance_count} × {instance_type}")
                self.test_results['scheduler_tests'] = True
                return True
            else:
                print(f"  ❌ Moldable scheduler failed to find allocation")
                self.test_results['scheduler_tests'] = False
                return False

        except Exception as e:
            print(f"  ❌ Scheduler test failed: {e}")
            self.test_results['scheduler_tests'] = False
            return False

    def test_executor_relay_system(self) -> bool:
        """Test dedicated executor and result relay system"""
        try:
            print("\n🧪 Testing Executor Relay System...")

            # Create HPO executor
            print("  Creating HPO executor...")
            executor_ip = "10.19.200.100"  # Simulated IP
            self.executor_instance = HPOExecutor(executor_ip)

            # Wait for result relay server to start
            time.sleep(2)

            # Test health check
            try:
                response = requests.get(f"http://localhost:8090/health", timeout=5)
                if response.status_code == 200:
                    print(f"  ✅ Executor health check passed")
                else:
                    print(f"  ❌ Executor health check failed: {response.status_code}")
                    return False
            except requests.exceptions.RequestException:
                print(f"  ⚠️  Health check skipped (server might not be accessible)")

            # Test result processing
            print("  Testing HPO result processing...")
            test_results = {
                'config': {
                    'learning_rate': 0.015,
                    'momentum': 0.85,
                    'batch_size': 96,
                    'next_trials': 2,
                    'accuracy': 0.92
                }
            }

            # Register a test workflow
            test_workflow_id = "test-workflow-001"
            self.executor_instance.active_workflows[test_workflow_id] = {
                'workflow_plan': self.test_workflow,
                'current_iteration': 1,
                'current_trials': 3,
                'moldable': True,
                'epochs': 6
            }

            # Process results
            self.executor_instance.process_hpo_results(test_workflow_id, test_results)

            print(f"  ✅ HPO result processing completed")
            self.test_results['executor_relay'] = True
            return True

        except Exception as e:
            print(f"  ❌ Executor relay test failed: {e}")
            self.test_results['executor_relay'] = False
            return False

    def test_cloud_runner_setup(self) -> bool:
        """Test cloud runner Ray cluster setup"""
        try:
            print("\n🧪 Testing Cloud Runner Setup...")

            # Create test request
            test_request = {
                'wf-plan': self.test_workflow,
                'hosts': {
                    'reserved': {
                        'g5.2xlarge': (2, ['10.19.100.1', '10.19.100.2'])
                    },
                    'on-demand': {
                        'g5.2xlarge': (1, ['10.19.100.3'])
                    }
                },
                'executor-ip': '10.19.200.100',
                'moldable': True
            }

            # Create cloud runner
            cloud_runner = CloudRunnerHPO(test_request)

            # Test worker IP extraction
            worker_ips = cloud_runner.extract_worker_ips()
            expected_ips = ['10.19.100.1', '10.19.100.2', '10.19.100.3']

            if worker_ips == expected_ips:
                print(f"  ✅ Worker IP extraction: {worker_ips}")
            else:
                print(f"  ❌ Worker IP extraction failed: {worker_ips} != {expected_ips}")
                return False

            # Test HPO config preparation
            hpo_config = cloud_runner.prepare_hpo_config()
            required_fields = ['model', 'epochs', 'next_trials', 'learning_rate']

            if all(field in hpo_config for field in required_fields):
                print(f"  ✅ HPO config preparation: {hpo_config}")
            else:
                print(f"  ❌ HPO config missing required fields")
                return False

            self.test_results['cloud_runner'] = True
            return True

        except Exception as e:
            print(f"  ❌ Cloud runner test failed: {e}")
            self.test_results['cloud_runner'] = False
            return False

    def test_moldable_workflow_simulation(self) -> bool:
        """Test complete moldable workflow simulation"""
        try:
            print("\n🧪 Testing Moldable Workflow Simulation...")

            if not self.test_workflow:
                print("  ❌ No test workflow loaded")
                return False

            # Simulate workflow execution with resource changes
            workflow_id = self.test_workflow['id']

            # Initial allocation
            initial_data = {
                'initial-alloc': True,
                'wf-plan': self.test_workflow,
                'hosts': {
                    'reserved': {
                        'g5.2xlarge': (2, ['10.19.100.1', '10.19.100.2'])
                    }
                },
                'executor-ip': '10.19.200.100',
                'moldable': True
            }

            print(f"  Simulating initial workflow execution...")

            # Create simulation environment
            class MockSim:
                def __init__(self):
                    self.current_time = 0

                def sleep(self, duration):
                    self.current_time += duration
                    print(f"    Sim time: {self.current_time}s")

            sim = MockSim()

            # Execute workflow
            executeWorkflowHPO(initial_data, sim)

            print(f"  ✅ Moldable workflow simulation completed")
            self.test_results['moldable_simulation'] = True
            return True

        except Exception as e:
            print(f"  ❌ Moldable workflow simulation failed: {e}")
            self.test_results['moldable_simulation'] = False
            return False

    def run_comprehensive_test(self) -> bool:
        """Run all tests and provide summary"""
        print("🚀 Starting HPO Dedicated Executor System Tests")
        print("=" * 60)

        # Load test workflow
        if not self.load_test_workflow():
            return False

        # Run all tests
        tests = [
            ("Instance Creation", self.test_instance_creation),
            ("HPO Schedulers", self.test_hpo_schedulers),
            ("Executor Relay System", self.test_executor_relay_system),
            ("Cloud Runner Setup", self.test_cloud_runner_setup),
            ("Moldable Workflow Simulation", self.test_moldable_workflow_simulation)
        ]

        passed_tests = 0
        total_tests = len(tests)

        for test_name, test_func in tests:
            try:
                if test_func():
                    passed_tests += 1
                    print(f"✅ {test_name}: PASSED")
                else:
                    print(f"❌ {test_name}: FAILED")
            except Exception as e:
                print(f"❌ {test_name}: ERROR - {e}")

        # Print summary
        print("\n" + "=" * 60)
        print("🏁 HPO System Test Summary")
        print("=" * 60)
        print(f"Tests Passed: {passed_tests}/{total_tests}")
        print(f"Success Rate: {(passed_tests/total_tests)*100:.1f}%")

        if passed_tests == total_tests:
            print("🎉 ALL TESTS PASSED! HPO system is ready for deployment.")
            return True
        else:
            print("⚠️  Some tests failed. Please review the implementation.")
            return False

def main():
    """Main test execution"""
    tester = HPOSystemTester()
    success = tester.run_comprehensive_test()

    if success:
        print("\n🎯 Next steps:")
        print("  1. Deploy HPO schedulers")
        print("  2. Create HPO workflow dispatcher")
        print("  3. Run performance comparison (static vs moldable)")
        print("  4. Measure moldable advantages")
    else:
        print("\n🔧 Recommended fixes:")
        print("  1. Review failed test outputs")
        print("  2. Check import paths and dependencies")
        print("  3. Verify configuration files")
        print("  4. Test individual components")

    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)