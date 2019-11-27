# Copyright 2018 The TensorFlow Probability Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================
"""The Autoregressive distribution."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow.compat.v2 as tf

from tensorflow_probability.python.distributions import distribution
from tensorflow_probability.python.internal import assert_util
from tensorflow_probability.python.internal import tensor_util
from tensorflow_probability.python.internal import tensorshape_util
from tensorflow_probability.python.util.seed_stream import SeedStream
from tensorflow.python.util import deprecation  # pylint: disable=g-direct-tensorflow-import


class Autoregressive(distribution.Distribution):
  """Autoregressive distributions.

  The Autoregressive distribution enables learning (often) richer multivariate
  distributions by repeatedly applying a [diffeomorphic](
  https://en.wikipedia.org/wiki/Diffeomorphism) transformation (such as
  implemented by `Bijector`s). Regarding terminology,

    'Autoregressive models decompose the joint density as a product of
    conditionals, and model each conditional in turn. Normalizing flows
    transform a base density (e.g. a standard Gaussian) into the target density
    by an invertible transformation with tractable Jacobian.' [(Papamakarios et
    al., 2016)][1]

  In other words, the 'autoregressive property' is equivalent to the
  decomposition, `p(x) = prod{ p(x[i] | x[0:i]) : i=0, ..., d }`. The provided
  `shift_and_log_scale_fn`, `masked_autoregressive_default_template`, achieves
  this property by zeroing out weights in its `masked_dense` layers.

  Practically speaking the autoregressive property means that there exists a
  permutation of the event coordinates such that each coordinate is a
  diffeomorphic function of only preceding coordinates
  [(van den Oord et al., 2016)][2].

  #### Mathematical Details

  The probability function is

  ```none
  prob(x; fn, n) = fn(x).prob(x)
  ```

  And a sample is generated by

  ```none
  x = fn(...fn(fn(x0).sample()).sample()).sample()
  ```

  where the ellipses (`...`) represent `n-2` composed calls to `fn`, `fn`
  constructs a `tfd.Distribution`-like instance, and `x0` is a
  fixed initializing `Tensor`.

  #### Examples

  ```python
  tfd = tfp.distributions
  tfb = tfp.bijectors

  def _normal_fn(event_size):
    n = event_size * (event_size + 1) // 2
    p = tf.Variable(tfd.Normal(loc=0., scale=1.).sample(n))
    affine = tfb.Affine(
        scale_tril=tfp.math.fill_triangular(0.25 * p))
    def _fn(samples):
      scale = tf.exp(affine.forward(samples))
      return tfd.Independent(
          tfd.Normal(loc=0., scale=scale, validate_args=True),
          reinterpreted_batch_ndims=1)
    return _fn

  batch_and_event_shape = [3, 2, 4]
  sample0 = tf.zeros(batch_and_event_shape)
  ar = tfd.Autoregressive(
      _normal_fn(batch_and_event_shape[-1]), sample0)
  x = ar.sample([6, 5])
  # ==> x.shape = [6, 5, 3, 2, 4]
  prob_x = ar.prob(x)
  # ==> x.shape = [6, 5, 3, 2]

  ```

  #### References

  [1]: George Papamakarios, Theo Pavlakou, and Iain Murray. Masked
       Autoregressive Flow for Density Estimation. In _Neural Information
       Processing Systems_, 2017. https://arxiv.org/abs/1705.07057

  [2]: Aaron van den Oord, Nal Kalchbrenner, Oriol Vinyals, Lasse Espeholt,
       Alex Graves, and Koray Kavukcuoglu. Conditional Image Generation with
       PixelCNN Decoders. In _Neural Information Processing Systems_, 2016.
       https://arxiv.org/abs/1606.05328
  """

  def __init__(self,
               distribution_fn,
               sample0=None,
               num_steps=None,
               validate_args=False,
               allow_nan_stats=True,
               name='Autoregressive'):
    """Construct an `Autoregressive` distribution.

    Args:
      distribution_fn: Python `callable` which constructs a
        `tfd.Distribution`-like instance from a `Tensor` (e.g.,
        `sample0`). The function must respect the 'autoregressive property',
        i.e., there exists a permutation of event such that each coordinate is a
        diffeomorphic function of on preceding coordinates.
      sample0: Initial input to `distribution_fn`; used to
        build the distribution in `__init__` which in turn specifies this
        distribution's properties, e.g., `event_shape`, `batch_shape`, `dtype`.
        If unspecified, then `distribution_fn` should be default constructable.
      num_steps: Number of times `distribution_fn` is composed from samples,
        e.g., `num_steps=2` implies
        `distribution_fn(distribution_fn(sample0).sample(n)).sample()`.
      validate_args: Python `bool`.  Whether to validate input with asserts.
        If `validate_args` is `False`, and the inputs are invalid,
        correct behavior is not guaranteed.
      allow_nan_stats: Python `bool`, default `True`. When `True`, statistics
        (e.g., mean, mode, variance) use the value '`NaN`' to indicate the
        result is undefined. When `False`, an exception is raised if one or
        more of the statistic's batch members are undefined.
      name: Python `str` name prefixed to Ops created by this class.
        Default value: 'Autoregressive'.

    Raises:
      ValueError: if `num_steps < 1`.
    """
    parameters = dict(locals())
    with tf.name_scope(name) as name:
      self._distribution_fn = distribution_fn
      self._sample0 = tensor_util.convert_nonref_to_tensor(sample0)
      self._num_steps = tensor_util.convert_nonref_to_tensor(num_steps)

      # We need to call `distribution_fn` once here to determine the `dtype`
      # and `reparameterization_type` of this distribution.  We don't otherwise
      # use the resulting `distribution0`, so this is '`tf.Variable` safe'
      # as long as `distribution_fn` returns `tfd.Distribution` instances with
      # consistent `dtype` and `reparameterization_type`.
      if self._sample0 is not None:
        distribution0 = self._distribution_fn(self._sample0)
      else:
        distribution0 = self._distribution_fn()

      super(Autoregressive, self).__init__(
          dtype=distribution0.dtype,
          reparameterization_type=distribution0.reparameterization_type,
          validate_args=validate_args,
          allow_nan_stats=allow_nan_stats,
          parameters=parameters,
          name=name)

  @property
  def distribution_fn(self):
    return self._distribution_fn

  @property
  def sample0(self):
    return self._sample0

  @property
  def num_steps(self):
    if self._num_steps is None:
      return self._num_steps_deprecated_behavior()
    return self._num_steps

  @deprecation.deprecated(
      '2019-02-15',
      'The `num_setps` property will return `None` when the distribution is '
      'constructed with with `num_steps=None`.  Use '
      '`tf.reduce_prod(event_shape_tensor())` instead.',
      warn_once=True)
  def _num_steps_deprecated_behavior(self):
    distribution0 = self._get_distribution0()
    num_steps_static = tensorshape_util.num_elements(distribution0.event_shape)
    if num_steps_static is not None:
      return num_steps_static
    return tf.reduce_prod(distribution0.event_shape_tensor())

  @property
  @deprecation.deprecated(
      '2020-02-15',
      'The `distribution0` property is deprecated.  '
      'Use `distribution_fn()` or `distribution_fn(sample0)` instead.',
      warn_once=True)
  def distribution0(self):
    return self._get_distribution0()

  def _get_distribution0(self):
    if self._sample0 is not None:
      ret = self._distribution_fn(self._sample0)
    else:
      ret = self._distribution_fn()
    if ret.dtype != self.dtype:
      raise ValueError(
          '`distribution_fn` returned distributions with different dtype -- '
          'previously {} and now {}'.format(self.dtype, ret.dtype))
    if ret.reparameterization_type != self.reparameterization_type:
      raise ValueError(
          '`distribution_fn` returned distributions with different '
          'reparameterize_type -- previously {} and now {}'.format(
              self.reparameterization_type, ret.reparameterization_type))
    return ret

  def _batch_shape(self):
    # NOTE: The batch shape of the output of `self._distribution_fn(...)` could
    # depend on values (or the shape of such values) read from variables during
    # the execution of `distribution_fn`.  Thus, in general, we cannot
    # statically determine the batch shape here.
    #
    # Also, `self._distribution_fn(...)` could have graph side effects.
    return tf.TensorShape(None)

  def _batch_shape_tensor(self):
    return self._get_distribution0().batch_shape_tensor()

  def _event_shape(self):
    # NOTE: The event shape of the output of `self._distribution_fn(...)` could
    # depend on values (or the shape of such values) read from variables during
    # the execution of `distribution_fn`.  Thus, in general, we cannot
    # statically determine the event shape here.
    #
    # Also, `self._distribution_fn(...)` could have graph side effects.
    return tf.TensorShape(None)

  def _event_shape_tensor(self):
    return self._get_distribution0().event_shape_tensor()

  def _sample_n(self, n, seed=None):
    distribution0 = self._get_distribution0()

    if self._num_steps is not None:
      num_steps = tf.convert_to_tensor(self._num_steps)
      num_steps_static = tf.get_static_value(num_steps)
    else:
      num_steps_static = tensorshape_util.num_elements(
          distribution0.event_shape)
      if num_steps_static is None:
        num_steps = tf.reduce_prod(distribution0.event_shape_tensor())

    seed = SeedStream(seed, salt='Autoregressive')()
    samples = distribution0.sample(n, seed=seed)
    if num_steps_static is not None:
      for _ in range(num_steps_static):
        # pylint: disable=not-callable
        samples = self.distribution_fn(samples).sample(seed=seed)
    else:
      samples = tf.foldl(
          # pylint: disable=not-callable
          lambda s, _: self.distribution_fn(s).sample(seed=seed),
          elems=tf.range(0, num_steps),
          initializer=samples)
    return samples

  def _log_prob(self, value):
    # pylint: disable=not-callable
    return self.distribution_fn(value).log_prob(value)

  def _prob(self, value):
    # pylint: disable=not-callable
    return self.distribution_fn(value).prob(value)

  def _parameter_control_dependencies(self, is_init):
    if not self.validate_args:
      return []
    assertions = []

    if self._num_steps is not None:
      if is_init != tensor_util.is_ref(self._num_steps):
        assertions.append(assert_util.assert_rank(
            self._num_steps, 0,
            message='Argument `num_steps` must be a scalar'))
        assertions.append(assert_util.assert_positive(
            self._num_steps, message='Argument `num_steps` must be positive'))

    return assertions
