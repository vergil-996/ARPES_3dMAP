import unittest

import numpy as np
from scipy.ndimage import rotate as scipy_rotate

from compute_backends import ArrayLRUCache, BackendManager, CpuComputeBackend, CupyComputeBackend
from refresh_pipeline import ComputeJob


class ComputeBackendTests(unittest.TestCase):
    def test_parallel_rotation_is_identical_to_existing_scipy_algorithm(self):
        data = np.random.default_rng(8).normal(size=(31, 27, 18)).astype(np.float32)
        expected = scipy_rotate(
            data,
            17.5,
            axes=(0, 1),
            reshape=False,
            order=1,
            mode="constant",
            cval=0.0,
            prefilter=True,
        )
        actual = CpuComputeBackend(max_workers=8).rotate_volume(data, 17.5)
        np.testing.assert_array_equal(actual, expected)
        self.assertTrue(actual.flags.f_contiguous)

    def test_combined_prefix_job_reuses_one_prepared_volume(self):
        raw = np.arange(9 * 7 * 6 * 3, dtype=np.float32).reshape(9, 7, 6, 3)
        result = CpuComputeBackend(max_workers=4).execute(
            ComputeJob(
                "prepare_axis_prefix",
                {
                    "raw_data": raw,
                    "source_mode": "frame",
                    "frame_index": 1,
                    "angle": 0.0,
                    "axis": 1,
                },
            )
        )
        np.testing.assert_array_equal(result.value["volume"], raw[..., 1])
        np.testing.assert_array_equal(
            result.value["prefix"],
            np.cumsum(raw[..., 1], axis=1, dtype=np.float32),
        )

    def test_unavailable_nvidia_mode_falls_back_without_error(self):
        class UnavailableGpu:
            available = False
            error = RuntimeError("no cuda")

            def free_unused_memory(self):
                pass

        manager = BackendManager(mode="NVIDIA GPU", gpu_backend=UnavailableGpu())
        self.assertEqual(manager.mode, "CPU")
        result = manager.execute(
            ComputeJob("preview_downsample", {"data": np.ones((4, 4, 4), np.float32)})
        )
        self.assertEqual(result.backend, "CPU")

    def test_cuda_runtime_failure_retries_the_job_on_cpu(self):
        class FailingGpu:
            available = True

            def execute(self, _job):
                raise MemoryError("simulated cuda oom")

            def free_unused_memory(self):
                pass

        manager = BackendManager(mode="Auto", gpu_backend=FailingGpu())
        result = manager.execute(
            ComputeJob("preview_downsample", {"data": np.ones((4, 4, 4), np.float32)})
        )
        self.assertEqual(result.backend, "CPU")
        self.assertIn("simulated cuda oom", result.metadata["gpu_fallback_error"])

    def test_lru_never_exceeds_byte_budget(self):
        cache = ArrayLRUCache(max_bytes=128)
        cache.put("a", np.zeros(24, dtype=np.float32))
        cache.put("b", np.zeros(24, dtype=np.float32))
        self.assertLessEqual(cache.current_bytes, 128)
        self.assertIsNone(cache.get("a"))
        self.assertIsNotNone(cache.get("b"))

    def test_cuda_rotation_matches_cpu_with_documented_tolerance(self):
        gpu = CupyComputeBackend()
        if not gpu.available:
            self.skipTest("CuPy/CUDA is not installed in the standard CPU environment")
        data = np.random.default_rng(42).normal(size=(17, 15, 13)).astype(np.float32)
        cpu = CpuComputeBackend(max_workers=4).rotate_volume(data, 13.0)
        actual = gpu.execute(ComputeJob("rotate_volume", {"data": data, "angle": 13.0})).value
        scale = max(float(np.max(np.abs(cpu))), 1.0)
        np.testing.assert_allclose(actual, cpu, rtol=5e-4, atol=5e-5 * scale)


if __name__ == "__main__":
    unittest.main()
