import unittest

import numpy as np

from bandscope.processing import gpu_display
from bandscope.processing.display_transfer import channels, level_info
from bandscope.processing.local_enhancement import clear_range_cache, finite_range


def _gpu_ready(test):
    try:
        ready = gpu_display.set_enabled(True)
    except Exception:  # pragma: no cover - depends on the host GPU
        ready = False
    if not ready:
        test.skipTest(f"CUDA display mapping unavailable: {gpu_display.failure_reason()}")
    return True


class GpuDisplayParityTests(unittest.TestCase):
    """The CUDA path must stay bit-comparable with the CPU reference."""

    def tearDown(self):
        gpu_display.set_enabled(False)
        gpu_display.reset()
        clear_range_cache()

    def _volume(self):
        rng = np.random.default_rng(5)
        data = rng.random((80, 80, 80), dtype=np.float32)
        data[0, 0, 0] = np.nan
        data[1, 0, 0] = np.inf
        data[2, 0, 0] = 0.0
        return data

    def test_channels_and_range_match_cpu_reference(self):
        _gpu_ready(self)
        data = self._volume()
        info = level_info((0.0, 1.0), (10, 35, 90))
        cpu_color, cpu_alpha = channels(data, info, "sigmoid")
        self.assertTrue(gpu_display.enabled())
        gpu_color, gpu_alpha = channels(data, info, "sigmoid")
        np.testing.assert_allclose(gpu_color, cpu_color, rtol=0.0, atol=1e-7)
        np.testing.assert_allclose(gpu_alpha, cpu_alpha, rtol=0.0, atol=1e-7)
        self.assertTrue(np.all(gpu_color[np.isfinite(data)] >= 0.0))
        self.assertEqual(float(gpu_alpha[0, 0, 0]), 0.0)

        clear_range_cache()
        gpu_range = finite_range(data)
        gpu_display.set_enabled(False)
        clear_range_cache()
        cpu_range = finite_range(data)
        self.assertEqual(gpu_range, cpu_range)

    def test_small_arrays_stay_on_the_cpu(self):
        _gpu_ready(self)
        data = np.random.default_rng(6).random((16, 16, 16), dtype=np.float32)
        info = level_info((0.0, 1.0), (0, 50, 100))
        gpu_color, _ = channels(data, info, "linear")
        gpu_display.set_enabled(False)
        cpu_color, _ = channels(data, info, "linear")
        np.testing.assert_array_equal(gpu_color, cpu_color)


if __name__ == "__main__":
    unittest.main()
