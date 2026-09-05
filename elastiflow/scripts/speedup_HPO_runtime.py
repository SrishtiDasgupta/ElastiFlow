import numpy as np


# Per-(family, size) runtime curves keyed by full instance name.
#
# Each entry maps a full inventory key (matching `Instance.name`) to a tuple
# (a, b, model_factors), parameterising
#     runtime_per_epoch = a * workers**(-b) * model_factors[model]
#
# Adding a new size is additive: drop a new entry here and the scheduler picks
# it up via getRuntime(name, ...). Family classification is in
# scheduler/scheduler_HPO.py::getFamily(); see docs/history/POLICY_B_TODO.md for the
# deferred structural changes that exercise heterogeneity within a family.
RUNTIME_MODELS = {
    'g4dn.xlarge': (
        23.039423, 0.788501,
        {'vgg19': 1.0, 'wide_resnet101_2': 1.34, 'convnext_large': 1.46},
    ),
    'g5.xlarge': (
        16.444782, 0.770580,
        {'vgg19': 1.0, 'wide_resnet101_2': 1.34, 'convnext_large': 1.46},
    ),
    # 2xlarge curves were measured in earlier campaigns; coefficients TBC from
    # archived jsonl when (and if) the inventory is ever widened. Kept commented
    # so the registry shape is documented without claiming unverified numbers.
    # 'g4dn.2xlarge': (..., ..., {...}),
    # 'g5.2xlarge':   (..., ..., {...}),
}


def _family_of(name):
    if 'g4dn' in name or 'on-prem' in name:
        return 'g4'
    if 'g5' in name:
        return 'g5'
    return None


def getRuntime(instance_name, workers, model, epochs):
    """Name-keyed runtime predictor.

    Falls back to the family default when an exact-name entry is missing — this
    preserves single-size-inventory behaviour for any future size that has no
    profiling curve yet.
    """
    if instance_name in RUNTIME_MODELS:
        a, b, factors = RUNTIME_MODELS[instance_name]
    else:
        family = _family_of(instance_name)
        default_key = 'g4dn.xlarge' if family == 'g4' else 'g5.xlarge'
        a, b, factors = RUNTIME_MODELS[default_key]
    runtime_per_epoch = a * (workers ** -b) * factors[model]
    return runtime_per_epoch * epochs


def getRuntime_g4(workers, model, epochs):
    return getRuntime('g4dn.xlarge', workers, model, epochs)


def getRuntime_g5(workers, model, epochs):
    return getRuntime('g5.xlarge', workers, model, epochs)
