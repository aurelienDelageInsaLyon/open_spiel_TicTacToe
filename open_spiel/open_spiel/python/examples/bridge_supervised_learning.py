# Copyright 2019 DeepMind Technologies Limited
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

# Lint as: python3
"""Train a policy net on bridge bidding based on a dataset of trajectories.

Suitable data for training, generated by WBridge5, may be downloaded from:
https://console.cloud.google.com/storage/browser/openspiel-data/bridge
"""

import os
import pickle
from typing import Any, Tuple

from absl import app
from absl import flags

import haiku as hk
import jax
from jax import numpy as jnp
import numpy as np
import optax

import pyspiel

OptState = Any
Params = Any

FLAGS = flags.FLAGS
GAME = pyspiel.load_game('bridge(use_double_dummy_result=false)')
NUM_ACTIONS = 38
MIN_ACTION = 52
NUM_CARDS = 52
NUM_PLAYERS = 4
TOP_K_ACTIONS = 5  # How many alternative actions to display

flags.DEFINE_integer('iterations', 100000, 'Number of iterations')
flags.DEFINE_string('data_path', None, 'Location for data')
flags.DEFINE_integer('eval_every', 10000, 'How often to evaluate the policy')
flags.DEFINE_integer('num_examples', 3,
                     'How many examples to print per evaluation')
flags.DEFINE_integer('train_batch', 128, 'Batch size for training step')
flags.DEFINE_integer('eval_batch', 10000, 'Batch size when evaluating')
flags.DEFINE_integer('rng_seed', 42, 'Seed for initial network weights')
flags.DEFINE_string('save_path', None, 'Location for saved networks')


def _no_play_trajectory(line: str):
  """Returns the deal and bidding actions only given a text trajectory."""
  actions = [int(x) for x in line.split(' ')]
  # Usually a trajectory is NUM_CARDS chance events for the deal, plus one
  # action for every bid of the auction, plus NUM_CARDS actions for the play
  # phase. Exceptionally, if all NUM_PLAYERS players Pass, there is no play
  # phase and the trajectory is just of length NUM_CARDS + NUM_PLAYERS.
  if len(actions) == NUM_CARDS + NUM_PLAYERS:
    return tuple(actions)
  else:
    return tuple(actions[:-NUM_CARDS])


def make_dataset(file: str):
  """Creates dataset as a generator of single examples."""
  all_trajectories = [_no_play_trajectory(line) for line in open(file)]
  while True:
    np.random.shuffle(all_trajectories)
    for trajectory in all_trajectories:
      action_index = np.random.randint(52, len(trajectory))
      state = GAME.new_initial_state()
      for action in trajectory[:action_index]:
        state.apply_action(action)
      yield (state.observation_tensor(), trajectory[action_index] - MIN_ACTION)


def batch(dataset, batch_size: int):
  """Creates a batched dataset from a one-at-a-time dataset."""
  observations = np.zeros([batch_size] + GAME.observation_tensor_shape(),
                          np.float32)
  labels = np.zeros(batch_size, dtype=np.int32)
  while True:
    for batch_index in range(batch_size):
      observations[batch_index], labels[batch_index] = next(dataset)
    yield observations, labels


def one_hot(x, k):
  """Returns a one-hot encoding of `x` of size `k`."""
  return jnp.array(x[..., jnp.newaxis] == jnp.arange(k), dtype=np.float32)


def net_fn(x):
  """Haiku module for our network."""
  net = hk.Sequential([
      hk.Linear(1024),
      jax.nn.relu,
      hk.Linear(1024),
      jax.nn.relu,
      hk.Linear(1024),
      jax.nn.relu,
      hk.Linear(1024),
      jax.nn.relu,
      hk.Linear(NUM_ACTIONS),
      jax.nn.log_softmax,
  ])
  return net(x)


def main(argv):
  if len(argv) > 1:
    raise app.UsageError('Too many command-line arguments.')

  # Make the network.
  net = hk.without_apply_rng(hk.transform(net_fn))

  # Make the optimiser.
  opt = optax.adam(1e-4)

  @jax.jit
  def loss(
      params: Params,
      inputs: np.ndarray,
      targets: np.ndarray,
  ) -> jnp.DeviceArray:
    """Cross-entropy loss."""
    assert targets.dtype == np.int32
    log_probs = net.apply(params, inputs)
    return -jnp.mean(one_hot(targets, NUM_ACTIONS) * log_probs)

  @jax.jit
  def accuracy(
      params: Params,
      inputs: np.ndarray,
      targets: np.ndarray,
  ) -> jnp.DeviceArray:
    """Classification accuracy."""
    predictions = net.apply(params, inputs)
    return jnp.mean(jnp.argmax(predictions, axis=-1) == targets)

  @jax.jit
  def update(
      params: Params,
      opt_state: OptState,
      inputs: np.ndarray,
      targets: np.ndarray,
  ) -> Tuple[Params, OptState]:
    """Learning rule (stochastic gradient descent)."""
    _, gradient = jax.value_and_grad(loss)(params, inputs, targets)
    updates, opt_state = opt.update(gradient, opt_state)
    new_params = optax.apply_updates(params, updates)
    return new_params, opt_state

  def output_samples(params: Params, max_samples: int):
    """Output some cases where the policy disagrees with the dataset action."""
    if max_samples == 0:
      return
    count = 0
    with open(os.path.join(FLAGS.data_path, 'test.txt')) as f:
      lines = list(f)
    np.random.shuffle(lines)
    for line in lines:
      state = GAME.new_initial_state()
      actions = _no_play_trajectory(line)
      for action in actions:
        if not state.is_chance_node():
          observation = np.array(state.observation_tensor(), np.float32)
          policy = np.exp(net.apply(params, observation))
          probs_actions = [(p, a + MIN_ACTION) for a, p in enumerate(policy)]
          pred = max(probs_actions)[1]
          if pred != action:
            print(state)
            for p, a in reversed(sorted(probs_actions)[-TOP_K_ACTIONS:]):
              print('{:7} {:.2f}'.format(state.action_to_string(a), p))
            print('Ground truth {}\n'.format(state.action_to_string(action)))
            count += 1
            break
        state.apply_action(action)
      if count >= max_samples:
        return

  # Make datasets.
  if FLAGS.data_path is None:
    raise app.UsageError(
        'Please generate your own supervised training data or download from '
        'https://console.cloud.google.com/storage/browser/openspiel-data/bridge'
        ' and supply the local location as --data_path')
  train = batch(
      make_dataset(os.path.join(FLAGS.data_path, 'train.txt')),
      FLAGS.train_batch)
  test = batch(
      make_dataset(os.path.join(FLAGS.data_path, 'test.txt')), FLAGS.eval_batch)

  # Initialize network and optimiser.
  rng = jax.random.PRNGKey(FLAGS.rng_seed)  # seed used for network weights
  inputs, unused_targets = next(train)
  params = net.init(rng, inputs)
  opt_state = opt.init(params)

  # Train/eval loop.
  for step in range(FLAGS.iterations):
    # Do SGD on a batch of training examples.
    inputs, targets = next(train)
    params, opt_state = update(params, opt_state, inputs, targets)

    # Periodically evaluate classification accuracy on the test set.
    if (1 + step) % FLAGS.eval_every == 0:
      inputs, targets = next(test)
      test_accuracy = accuracy(params, inputs, targets)
      print(f'After {1+step} steps, test accuracy: {test_accuracy}.')
      if FLAGS.save_path:
        filename = os.path.join(FLAGS.save_path, f'params-{1 + step}.pkl')
        with open(filename, 'wb') as pkl_file:
          pickle.dump(params, pkl_file)
      output_samples(params, FLAGS.num_examples)


if __name__ == '__main__':
  app.run(main)