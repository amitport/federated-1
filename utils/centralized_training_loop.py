# Copyright 2020, Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Dispatcher for centralized training loops."""

import os
from typing import Any, Dict, Optional

from absl import logging
import pandas as pd
import tensorflow as tf

from federated_learning_research import pseudo_round
from federated_learning_research.aggregators import Aggregation
from federated_learning_research.pseudo_round import noop_mean
from optimization.shared import keras_callbacks
from utils import utils_impl


def run(
        keras_model: tf.keras.Model,
        train_dataset: tf.data.Dataset,
        experiment_name: str,
        root_output_dir: str,
        num_epochs: int,
        pseudo_round_size: int = None,
        pseudo_round_aggregation: Aggregation = noop_mean,
        hparams_dict: Optional[Dict[str, Any]] = None,
        decay_epochs: Optional[int] = None,
        lr_decay: Optional[float] = None,
        decay_type: str = 'linear',
        validation_dataset: Optional[tf.data.Dataset] = None,
        test_dataset: Optional[tf.data.Dataset] = None
) -> tf.keras.callbacks.History:
    """Run centralized training for a given compiled `tf.keras.Model`.

  Args:
    keras_model: A compiled `tf.keras.Model`.
    train_dataset: The `tf.data.Dataset` to be used for training.
    experiment_name: Name of the experiment, used as part of the name of the
      output directory.
    root_output_dir: The top-level output directory. The directory
      `root_output_dir/experiment_name` will contain TensorBoard logs, metrics
      CSVs and other outputs.
    num_epochs: How many training epochs to perform.
    pseudo_round_size: How many batches to merge before applying gradients.
    pseudo_round_aggregation: TODO.
    hparams_dict: An optional dict specifying hyperparameters. If provided, the
      hyperparameters will be written to CSV.
    decay_epochs: Number of training epochs before decaying the learning rate.
    lr_decay: How much to decay the learning rate by every `decay_epochs`.
    decay_type: TODO.
    validation_dataset: An optional `tf.data.Dataset` used for validation during
      training.
    test_dataset: An optional `tf.data.Dataset` used for testing after all
      training has completed.

  Returns:
    A `tf.keras.callbacks.History` object.
  """

    if pseudo_round_size:
        train_dataset = train_dataset.batch(pseudo_round_size, drop_remainder=True)
        pseudo_round.augment_keras_model(keras_model, pseudo_round_aggregation)

    tensorboard_dir = os.path.join(root_output_dir, 'logdir', experiment_name)
    results_dir = os.path.join(root_output_dir, 'results', experiment_name)

    for path in [root_output_dir, tensorboard_dir, results_dir]:
        tf.io.gfile.makedirs(path)

    if hparams_dict:
        hparams_file = os.path.join(results_dir, 'hparams.csv')
        logging.info('Saving hyper parameters to: [%s]', hparams_file)
        hparams_df = pd.DataFrame(hparams_dict, index=[0])
        utils_impl.atomic_write_to_csv(hparams_df, hparams_file)

    csv_logger_callback = keras_callbacks.AtomicCSVLogger(results_dir)
    tensorboard_callback = tf.keras.callbacks.TensorBoard(log_dir=tensorboard_dir)
    training_callbacks = [tensorboard_callback, csv_logger_callback]

    if decay_epochs is not None and decay_epochs > 0:
        # Reduce the learning rate after a fixed number of epochs.
        def decay_lr(epoch, learning_rate):
            if epoch > 0 and epoch % decay_epochs == 0:
                if decay_type == 'inverse_sqrt':
                    return learning_rate * tf.math.rsqrt(tf.cast(epoch, tf.float32))
                else:
                    return learning_rate * lr_decay
            else:
                return learning_rate

        lr_callback = tf.keras.callbacks.LearningRateScheduler(decay_lr, verbose=1)
        training_callbacks.append(lr_callback)

    logging.info('Training model:')
    logging.info(keras_model.summary())

    history = keras_model.fit(
        train_dataset,
        validation_data=validation_dataset,
        epochs=num_epochs,
        callbacks=training_callbacks)

    logging.info('Final training metrics:')
    for metric in keras_model.metrics:
        name = metric.name
        metric = history.history[name][-1]
        logging.info('\t%s: %.4f', name, metric)

    if validation_dataset:
        logging.info('Final validation metrics:')
        for metric in keras_model.metrics:
            name = metric.name
            metric = history.history['val_{}'.format(name)][-1]
            logging.info('\t%s: %.4f', name, metric)

    if test_dataset:
        test_metrics = keras_model.evaluate(test_dataset, return_dict=True)
        logging.info('Test metrics:')
        for metric in keras_model.metrics:
            name = metric.name
            metric = test_metrics[name]
            logging.info('\t%s: %.4f', name, metric)

    return history
