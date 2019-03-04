import numpy as np
import tensorflow as tf

import neural_ode
from neural_ode import NeuralODE

tf.enable_eager_execution()
K = tf.keras.backend
keras = tf.keras


class SineDumpingModel(tf.keras.Model):
    def call(self, inputs, **kwargs):
        t, x = inputs
        return tf.exp(-x) * tf.sin(t)


class DoubleSineDumpingModel(tf.keras.Model):
    def call(self, inputs, **kwargs):
        t, xy = inputs
        x, y = xy[0], xy[1]
        h1 = tf.exp(-x) * tf.sin(t)
        h2 = 0.5 * tf.exp(-y) * tf.cos(t)
        return tf.stack([h1, h2])


class NNModule(tf.keras.Model):

    def __init__(self):
        super(NNModule, self).__init__(name='Module')
        self.num_filters = 3
        self.dense_1 = keras.layers.Dense(self.num_filters, activation='tanh')
        self.dense_2 = keras.layers.Dense(self.num_filters, activation='tanh')

    def call(self, inputs, **kwargs):
        t, x = inputs
        h = self.dense_1(x)
        return self.dense_2(h)

    def compute_output_shape(self, input_shape):
        shape = tf.TensorShape(input_shape).as_list()
        shape[-1] = self.num_filters
        return tf.TensorShape(shape)


class NNGradientModule(NNModule):

    def call(self, inputs, **kwargs):
        t, x = inputs
        x, y = tf.split(x, 2, axis=1)
        hx = self.dense_1(x)

        with tf.GradientTape() as tape:
            tape.watch(y)
            hy = self.dense_1(y)

        gradients = tape.gradient(
            target=hy,
            sources=y,
            output_gradients=tf.ones_like(y)
        )
        outputs = tf.concat([hx, gradients], axis=1)
        return outputs

    def compute_output_shape(self, input_shape):
        shape = tf.TensorShape(input_shape).as_list()
        shape[-1] = 2 * self.num_filters
        return tf.TensorShape(shape)


class TestNeuralOde(tf.test.TestCase):

    def test_function_integration(self):
        t_max = 1
        ode = NeuralODE(
            SineDumpingModel(), t=np.linspace(0, t_max, 1000),
        )
        x0 = tf.to_float([0])
        xN_euler = ode.forward(x0)

        ode = NeuralODE(
            SineDumpingModel(), t=np.linspace(0, t_max, 100),
            solver=neural_ode.rk2_step
        )

        xN_rk2 = ode.forward(x0)

        ode = NeuralODE(
            SineDumpingModel(), t=np.linspace(0, t_max, 50),
            solver=neural_ode.rk4_step
        )

        xN_rk4 = ode.forward(x0)
        xN_exact = [np.log(2 - np.cos(t_max))]

        with self.assertRaises(Exception):
            self.assertAllClose(xN_euler.numpy(), xN_exact, atol=1e-4)

        self.assertAllClose(xN_rk2.numpy(), xN_exact, atol=1e-4)
        self.assertAllClose(xN_rk4.numpy(), xN_exact, atol=1e-4)

    def test_multiple_inputs(self):
        t_max = 1
        t_grid = np.linspace(0, t_max, 100)
        ode = NeuralODE(DoubleSineDumpingModel(), t=t_grid)

        xy0 = tf.to_float([0.0, 0.0])
        xyN_euler = ode.forward(xy0)

        ode = NeuralODE(
            DoubleSineDumpingModel(), t=t_grid,
            solver=neural_ode.rk2_step
        )

        xyN_rk2 = ode.forward(xy0)

        ode = NeuralODE(
            DoubleSineDumpingModel(), t=t_grid,
            solver=neural_ode.rk4_step
        )

        xyN_rk4 = ode.forward(xy0)

        xN_exact = np.log(2 - np.cos(t_max))
        yN_exact = np.log((np.sin(t_max) + 2)/2)
        xyN_exact = tf.to_float([xN_exact, yN_exact])

        self.assertAllClose(xyN_euler, xyN_exact, atol=1e-2)
        with self.assertRaises(Exception):
            self.assertAllClose(xyN_euler, xyN_exact, atol=1e-3)

        self.assertAllClose(xyN_rk2, xyN_exact, atol=1e-5)
        self.assertAllClose(xyN_rk4, xyN_exact, atol=1e-6)

    def test_backprop(self):
        t_max = 1
        t_grid = np.linspace(0, t_max, 40)

        ode = NeuralODE(SineDumpingModel(), t=t_grid, solver=neural_ode.rk4_step)
        x0 = tf.to_float([0])
        xN = ode.forward(x0)
        with tf.GradientTape() as g:
            g.watch(xN)
            loss = xN ** 2

        dLoss = g.gradient(loss, xN)
        x0_rec, dLdx0, dLdW = ode.backward(xN, dLoss)
        self.assertAllClose(x0_rec.numpy(), x0)
        self.assertEqual(dLdW, [])

        with tf.GradientTape() as g:
            g.watch(x0)
            xN = ode.forward(x0)
            loss = xN ** 2

        dLdx0_exact = g.gradient(loss, x0)
        self.assertAllClose(dLdx0_exact, dLdx0)

    def test_nn_forward_backward(self):
        tf.set_random_seed(1234)
        t_vec = np.linspace(0, 1.0, 20)
        model = NNModule()
        ode = NeuralODE(model, t=t_vec, solver=neural_ode.rk4_step)

        x0 = tf.random_normal(shape=[12, 3])
        xN = ode.forward(x0)
        dLoss = 2 * xN  # explicit gradient od x**2
        x0_rec, dLdx0, dLdW = ode.backward(xN, dLoss)
        self.assertAllClose(x0_rec.numpy(), x0.numpy())

        with tf.GradientTape() as g:
            g.watch(x0)
            xN = ode.forward(x0)
            loss = xN ** 2

        dLdx0_exact, *dLdW_exact = g.gradient(loss, [x0, *model.weights])

        self.assertAllClose(dLdx0_exact, dLdx0)
        self.assertAllClose(dLdW_exact, dLdW)

    def test_net_with_inner_gradient(self):
        tf.set_random_seed(1234)
        t_vec = np.linspace(0, 1.0, 20)
        model = NNGradientModule()
        ode = NeuralODE(model, t=t_vec, solver=neural_ode.rk4_step)

        xy0 = tf.random_normal(shape=[12, 2 * 3])
        xyN = ode.forward(xy0)

        with tf.GradientTape() as g:
            g.watch(xyN)
            loss = xyN ** 2
            dLoss = g.gradient(loss, xyN)

        xy0_rec, dLdxy0, dLdW = ode.backward(xyN, dLoss)
        self.assertAllClose(xy0_rec, xy0)

        with tf.GradientTape() as g:
            g.watch(xy0)
            xyN = ode.forward(xy0)
            loss = xyN ** 2

        dLdxy0_exact, *dLdW_exact = g.gradient(loss, [xy0, *model.weights])
        self.assertAllClose(dLdxy0_exact, dLdxy0)
        self.assertAllClose(dLdW_exact, dLdW)

    def test_grad_no_grad(self):

        class CosGradModel(tf.keras.Model):
            def call(self, inputs, **kwargs):
                t, x = inputs
                with tf.GradientTape() as g:
                    g.watch(x)
                    y = tf.sin(t * x)
                    dy = g.gradient(y, x)

                return dy

        class CosModel(tf.keras.Model):
            def call(self, inputs, **kwargs):
                t, x = inputs
                y = t * tf.cos(t * x)
                return y

        t_vec = np.linspace(0, 1.0, 40)
        ode = NeuralODE(CosModel(), t=t_vec, solver=neural_ode.rk4_step)
        x0 = tf.to_float([1.0])

        cos_xN = ode.forward(x0)
        x0_rec, dLdx0, _ = ode.backward(cos_xN, cos_xN)

        ode = NeuralODE(CosGradModel(), t=t_vec, solver=neural_ode.rk4_step)
        cos_grad_xN = ode.forward(x0)
        x0_grad_rec, dLdx0_grad, _ = ode.backward(cos_grad_xN, cos_grad_xN)

        self.assertAllClose(cos_grad_xN, cos_xN)
        self.assertAllClose(x0_grad_rec, x0_rec)
        self.assertAllClose(x0, x0_rec)
        self.assertAllClose(dLdx0, dLdx0_grad)

        ode = NeuralODE(CosModel(), t=t_vec, solver=neural_ode.rk4_step)
        with tf.GradientTape() as g:
            g.watch(x0)
            cos_xN = ode.forward(x0)
            loss = 0.5 * cos_xN ** 2

        dLdx0_exact = g.gradient(loss, [x0])

        self.assertAllClose(dLdx0_exact, dLdx0_grad)

        # build static graph
        ode = NeuralODE(CosGradModel(), t=t_vec, solver=neural_ode.rk4_step)
        ode = neural_ode.defun_neural_ode(ode)
        cos_grad_xN = ode.forward(x0)
        x0_grad_rec, dLdx0_grad, _ = ode.backward(cos_grad_xN, cos_grad_xN)

        self.assertAllClose(dLdx0_grad, dLdx0_exact)
        self.assertAllClose(x0_grad_rec, x0)
