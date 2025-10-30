cd /fsx/hyperparameter_test
echo $1> ~/client.out

echo $1 | /home/ubuntu/rayenv/bin/python3 hpo_pipeline_verbose.py --hosts $2 >> ~/client.out 2>&1 # should give model, num samples and epoch
