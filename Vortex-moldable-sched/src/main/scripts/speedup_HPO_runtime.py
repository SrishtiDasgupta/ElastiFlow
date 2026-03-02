import numpy as np


def getRuntime_g4(workers, model, epochs):
    model_factors = {'vgg19': 1.0, 'wide_resnet101_2': 1.34, 'convnext_large': 1.46}
    runtime_per_epoch = 23.039423 * (workers**-0.788501) * model_factors[model] + 0.000000
    return runtime_per_epoch * epochs

def getRuntime_g5(workers, model, epochs):
    model_factors = {'vgg19': 1.0, 'wide_resnet101_2': 1.34, 'convnext_large': 1.46}
    runtime_per_epoch = 16.444782 * (workers**-0.770580) * model_factors[model] + 0.000000
    return runtime_per_epoch * epochs
