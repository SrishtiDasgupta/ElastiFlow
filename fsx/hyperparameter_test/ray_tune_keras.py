import os
os.environ["TF_USE_LEGACY_KERAS"] = "1"

import tempfile, json
from filelock import FileLock
# from tensorflow.keras.datasets import mnist
import tensorflow as tf
import tf_keras
from tf_keras.datasets import mnist
import ray
from ray import tune
from ray.train import Checkpoint
from ray.tune.integration.ray_train import TuneReportCallback
from ray.tune.schedulers import AsyncHyperBandScheduler
from ray.train.tensorflow import TensorflowTrainer
from ray.air.integrations.keras import ReportCheckpointCallback





def train_mnist(config):
    # https://github.com/tensorflow/tensorflow/issues/32159
    import tensorflow as tf

    batch_size = 256
    num_classes = 10
    epochs = config["epoch"]
    
    
    # strategy = tf.distribute.MultiWorkerMirroredStrategy()
    strategy = tf.distribute.get_strategy()
    
    # Add new layers on top of the model
    
    IMG_SHAPE = (32, 32, 3)

    
    with strategy.scope():
        
        with FileLock(os.path.expanduser("~/.data.lock")):
            (x_train, y_train), (x_test, y_test) = tf_keras.datasets.cifar10.load_data()
        # with FileLock(os.path.expanduser("~/.data.lock")):
        #         (x_train, y_train), (x_test, y_test) = mnist.load_data()
        # x_train, x_test = x_train / 255.0, x_test / 255.0
        
        train_dataset = tf.data.Dataset.from_tensor_slices(
        (x_train, y_train)).shuffle(60000).cache().repeat().batch(batch_size).prefetch(tf.data.AUTOTUNE)

        
            
        
        # model = tf_keras.models.Sequential(
        #     [
        #         tf_keras.layers.Flatten(input_shape=(28, 28)),
        #         tf_keras.layers.Dense(config["hidden"], activation="relu"),
        #         tf_keras.layers.Dropout(0.2),
        #         tf_keras.layers.Dense(num_classes, activation="softmax"),
        #     ]
        # )
        base_model = tf_keras.applications.DenseNet201(input_shape=IMG_SHAPE, input_tensor=None, include_top=False, weights='imagenet')
        # base_model = tf_keras.applications.ResNet152(input_shape=IMG_SHAPE, input_tensor=None, include_top=False, weights='imagenet')
        base_model.trainable = True

        # #define model
        model = tf_keras.Sequential()
        model.add(base_model)
        model.add(tf_keras.layers.GlobalAveragePooling2D())
        model.add(tf_keras.layers.Dense(config["hidden"], activation='softmax'))
    
    
        model.compile(
            loss="sparse_categorical_crossentropy",
            optimizer=tf_keras.optimizers.SGD(learning_rate=config["learning_rate"], momentum=config["momentum"]),
            metrics=["accuracy"],
        )

    history = model.fit(
        train_dataset,
        epochs = epochs,
        steps_per_epoch=len(x_train) // batch_size,
        verbose=1,
        # callbacks=[ReportCheckpointCallback(metrics={"accuracy": "accuracy"})],
    )
    print("Before Report")
    
    print("After Report")
    with tempfile.TemporaryDirectory() as temp_checkpoint_dir:
            # model.save(os.path.join("/fsx/", temp_checkpoint_dir, "model.keras"))
        checkpoint_dict = os.path.join("/fsx/ray_results", temp_checkpoint_dir, "checkpoint.json")
        ray.train.report(metrics={"accuracy":  history.history["accuracy"][-1]})
    return

        


    


# def tune_mnist():
#     sched = AsyncHyperBandScheduler(
#         time_attr="training_iteration", max_t=400, grace_period=20
#     )

#     tuner = tune.Tuner(
#         tune.with_resources(train_mnist, resources={"cpu": 1, "gpu": 0}),
#         tune_config=tune.TuneConfig(
#             metric="accuracy",
#             mode="max",
#             scheduler=sched,
#             num_samples=1

#         ),
#         run_config=tune.RunConfig(
#             name="exp",
#             stop={"accuracy": 0.99},
#             storage_path='/fsx/ray_results'
#         ),
#         param_space={
#             "threads": 2,
#             "learning_rate": tune.uniform(0.001, 0.1),
#             "momentum": tune.uniform(0.1, 0.9),
#             "hidden": tune.randint(32, 108),
            
#         },
        
        
#     )
#     results = tuner.fit()
#     return results

    

# results = tune_mnist()
# print(f"Best hyperparameters found were: {results.get_best_result().config} | Accuracy: {results.get_best_result().metrics['accuracy']}")
