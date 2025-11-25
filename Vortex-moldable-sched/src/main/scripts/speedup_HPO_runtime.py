import numpy as np


def getRuntime_g4(workers, model, epochs):
    model_factors = {'vgg19': 1.0, 'wide_resnet101_2': 1.37, 'convnext_large': 2.03}
    runtime_per_epoch = 84.468106 * (workers**-0.956345) * model_factors[model] + 0.000000
    return runtime_per_epoch * epochs

def getRuntime_g5(workers, model, epochs):
    model_factors = {'vgg19': 1.0, 'wide_resnet101_2': 1.37, 'convnext_large': 2.03}
    runtime_per_epoch = 38.206768 * (workers**-0.901272) * model_factors[model] + 0.000000
    return runtime_per_epoch * epochs
