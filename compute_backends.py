from __future__ import annotations

import os
import ctypes
import threading
from abc import ABC, abstractmethod
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np
from scipy.ndimage import gaussian_filter1d, rotate as ndimage_rotate

from refresh_pipeline import ComputeJob, ComputeResult


def physical_memory_bytes():
    """Best-effort physical-memory query without an additional dependency."""
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("avail_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total_phys)
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return 8 * 1024 ** 3


def recommended_host_cache_budget():
    return int(min(4 * 1024 ** 3, physical_memory_bytes() * 0.20))


class ArrayLRUCache:
    """Byte-budgeted cache for computed NumPy volumes."""

    def __init__(self, max_bytes=None):
        self.max_bytes = int(max_bytes or recommended_host_cache_budget())
        self.current_bytes = 0
        self._items = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key, default=None):
        with self._lock:
            item = self._items.pop(key, None)
            if item is None:
                return default
            self._items[key] = item
            return item

    def put(self, key, value):
        value = np.asarray(value)
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self.current_bytes -= int(old.nbytes)
            if int(value.nbytes) > self.max_bytes:
                return value
            self._items[key] = value
            self.current_bytes += int(value.nbytes)
            while self.current_bytes > self.max_bytes and self._items:
                _, removed = self._items.popitem(last=False)
                self.current_bytes -= int(removed.nbytes)
        return value

    def clear(self):
        with self._lock:
            self._items.clear()
            self.current_bytes = 0

    def __len__(self):
        with self._lock:
            return len(self._items)


def _normalized_range(low, up, size):
    low = int(np.clip(int(low), 0, max(int(size) - 1, 0)))
    up = int(np.clip(int(up), 0, max(int(size) - 1, 0)))
    return (low, up) if low <= up else (up, low)


def _preview_stride(shape, max_voxels=2_500_000):
    voxel_count = int(np.prod(shape, dtype=np.int64))
    if voxel_count <= int(max_voxels):
        return 1
    return max(1, int(np.ceil((voxel_count / float(max_voxels)) ** (1.0 / len(shape)))))


class ComputeBackend(ABC):
    name = "Unknown"

    @abstractmethod
    def execute(self, job: ComputeJob) -> ComputeResult:
        raise NotImplementedError


class CpuComputeBackend(ComputeBackend):
    name = "CPU"

    def __init__(self, max_workers=None):
        detected = max(int(os.cpu_count() or 1), 1)
        self.max_workers = max(1, min(int(max_workers or detected), 16))

    def execute(self, job):
        payload = dict(job.payload)
        operation = str(job.operation)
        if operation == "rotate_volume":
            value = self.rotate_volume(payload["data"], payload.get("angle", 0.0))
        elif operation == "prepare_volume":
            value = self.prepare_volume(**payload)
        elif operation == "prepare_axis_prefix":
            axis = int(payload.pop("axis"))
            volume = self.prepare_volume(**payload)
            value = {
                "volume": volume,
                "prefix": np.cumsum(volume, axis=axis, dtype=np.float32),
            }
        elif operation == "prepare_second_derivative":
            axis = int(payload.pop("axis"))
            coordinates = payload.pop("coordinates", None)
            sigma = payload.pop("sigma", 1.5)
            threshold = payload.pop("threshold", 0.0)
            volume = self.prepare_volume(**payload)
            value = {
                "volume": volume,
                "derivative": self.second_derivative(
                    volume,
                    axis=axis,
                    coordinates=coordinates,
                    sigma=sigma,
                    threshold=threshold,
                ),
            }
        elif operation == "axis_prefix":
            value = np.cumsum(
                np.asarray(payload["data"], dtype=np.float32),
                axis=int(payload["axis"]),
                dtype=np.float32,
            )
        elif operation == "axis_range_sum":
            value = self.axis_range_sum(**payload)
        elif operation == "second_derivative":
            value = self.second_derivative(**payload)
        elif operation == "preview_downsample":
            value = self.preview_downsample(**payload)
        else:
            raise ValueError(f"Unsupported CPU compute operation: {operation}")
        return ComputeResult(value=value, backend=self.name)

    def prepare_volume(
        self,
        raw_data,
        source_mode="frame",
        frame_index=0,
        t_low=0,
        t_up=0,
        angle=0.0,
        preview=False,
        max_voxels=2_500_000,
    ):
        raw_data = np.asarray(raw_data, dtype=np.float32)
        if source_mode == "time_integral":
            low, up = _normalized_range(t_low, t_up, raw_data.shape[3])
            volume = np.sum(raw_data[..., low:up + 1], axis=3, dtype=np.float32)
        else:
            frame_index = int(np.clip(int(frame_index), 0, raw_data.shape[3] - 1))
            volume = raw_data[..., frame_index]

        if preview:
            volume = self.preview_downsample(volume, max_voxels=max_voxels)
        return self.rotate_volume(volume, angle)

    def rotate_volume(self, data, angle):
        source = np.asarray(data, dtype=np.float32)
        if source.ndim != 3:
            raise ValueError("Volume rotation requires a 3D array.")
        if abs(float(angle)) < 1e-6:
            return np.asfortranarray(source)

        workers = max(1, min(self.max_workers, int(source.shape[2])))
        if workers == 1 or source.shape[2] < 8:
            return np.asfortranarray(
                ndimage_rotate(
                    source,
                    float(angle),
                    axes=(0, 1),
                    reshape=False,
                    order=1,
                    mode="constant",
                    cval=0.0,
                    prefilter=True,
                )
            )

        output = np.empty(source.shape, dtype=np.float32, order="F")
        edges = np.linspace(0, source.shape[2], workers + 1, dtype=int)
        ranges = [(int(start), int(stop)) for start, stop in zip(edges[:-1], edges[1:]) if stop > start]

        def rotate_chunk(bounds):
            start, stop = bounds
            ndimage_rotate(
                source[:, :, start:stop],
                float(angle),
                axes=(0, 1),
                reshape=False,
                output=output[:, :, start:stop],
                order=1,
                mode="constant",
                cval=0.0,
                prefilter=True,
            )

        with ThreadPoolExecutor(max_workers=len(ranges)) as executor:
            list(executor.map(rotate_chunk, ranges))
        return output

    @staticmethod
    def preview_downsample(data, max_voxels=2_500_000):
        source = np.asarray(data, dtype=np.float32)
        stride = _preview_stride(source.shape, max_voxels=max_voxels)
        if stride == 1:
            return np.asfortranarray(source)
        slices = tuple(slice(None, None, stride) for _ in range(source.ndim))
        return np.asfortranarray(source[slices])

    @staticmethod
    def axis_range_sum(data, axis, low, up):
        source = np.asarray(data, dtype=np.float32)
        axis = int(axis)
        low, up = _normalized_range(low, up, source.shape[axis])
        slices = [slice(None)] * source.ndim
        slices[axis] = slice(low, up + 1)
        return np.sum(source[tuple(slices)], axis=axis, dtype=np.float32)

    @staticmethod
    def second_derivative(data, axis, coordinates=None, sigma=1.5, threshold=0.0):
        source = np.asarray(data, dtype=np.float32)
        axis = int(axis)
        smoothed = gaussian_filter1d(source, sigma=max(float(sigma), 0.0), axis=axis)
        if coordinates is None:
            coordinates = np.arange(source.shape[axis], dtype=np.float64)
        else:
            coordinates = np.asarray(coordinates, dtype=np.float64)
        first = np.gradient(smoothed, coordinates, axis=axis, edge_order=2)
        result = -np.gradient(first, coordinates, axis=axis, edge_order=2)
        result[result < float(threshold)] = 0
        return np.asarray(result, dtype=np.float32, order="F")


class CupyComputeBackend(ComputeBackend):
    name = "NVIDIA GPU"

    def __init__(self):
        self._cp = None
        self._ndimage = None
        self._available = False
        self._error = None
        self._memory_budget = 0
        self._probe()

    @property
    def available(self):
        return bool(self._available)

    @property
    def error(self):
        return self._error

    @property
    def memory_budget(self):
        return int(self._memory_budget)

    def _probe(self):
        try:
            import cupy as cp
            from cupyx.scipy import ndimage as cupy_ndimage

            if int(cp.cuda.runtime.getDeviceCount()) < 1:
                raise RuntimeError("No CUDA device was detected.")
            with cp.cuda.Device(0):
                probe = cp.arange(16, dtype=cp.float32).reshape(4, 4)
                rotated_probe = cupy_ndimage.rotate(
                    probe,
                    7.0,
                    reshape=False,
                    order=1,
                    mode="constant",
                    cval=0.0,
                    prefilter=True,
                )
                float(cp.asnumpy(rotated_probe.sum()))
                free_bytes, total_bytes = cp.cuda.runtime.memGetInfo()
                reserve = 3 * 1024 ** 3
                budget = max(0, min(8 * 1024 ** 3, total_bytes // 2, free_bytes - reserve))
                if budget <= 0:
                    raise RuntimeError("CUDA has less than the required 3 GiB free-memory reserve.")
                cp.get_default_memory_pool().set_limit(size=int(budget))
            self._cp = cp
            self._ndimage = cupy_ndimage
            self._memory_budget = int(budget)
            self._available = True
        except Exception as exc:
            self._error = exc
            self._available = False

    def execute(self, job):
        if not self.available:
            raise RuntimeError(f"CUDA backend unavailable: {self.error}")
        cp = self._cp
        payload = dict(job.payload)
        operation = str(job.operation)
        if operation == "prepare_volume":
            value = self.prepare_volume(**payload)
        elif operation == "prepare_axis_prefix":
            axis = int(payload.pop("axis"))
            volume = self.prepare_volume(**payload)
            prefix = cp.cumsum(cp.asarray(volume, dtype=cp.float32), axis=axis, dtype=cp.float32)
            value = {"volume": volume, "prefix": self._to_host_fortran(prefix)}
        elif operation == "prepare_second_derivative":
            axis = int(payload.pop("axis"))
            coordinates = payload.pop("coordinates", None)
            sigma = payload.pop("sigma", 1.5)
            threshold = payload.pop("threshold", 0.0)
            volume = self.prepare_volume(**payload)
            value = {
                "volume": volume,
                "derivative": self.second_derivative(
                    volume,
                    axis=axis,
                    coordinates=coordinates,
                    sigma=sigma,
                    threshold=threshold,
                ),
            }
        elif operation == "rotate_volume":
            value = self._to_host_fortran(
                self._ndimage.rotate(
                    cp.asarray(payload["data"], dtype=cp.float32),
                    float(payload.get("angle", 0.0)),
                    axes=(0, 1),
                    reshape=False,
                    order=1,
                    mode="constant",
                    cval=0.0,
                    prefilter=True,
                )
            )
        elif operation == "axis_prefix":
            value = self._to_host_fortran(
                cp.cumsum(cp.asarray(payload["data"], dtype=cp.float32), axis=int(payload["axis"]), dtype=cp.float32)
            )
        elif operation == "axis_range_sum":
            value = self.axis_range_sum(**payload)
        elif operation == "second_derivative":
            value = self.second_derivative(**payload)
        elif operation == "preview_downsample":
            source = cp.asarray(payload["data"], dtype=cp.float32)
            stride = _preview_stride(source.shape, payload.get("max_voxels", 2_500_000))
            value = self._to_host_fortran(source[tuple(slice(None, None, stride) for _ in source.shape)])
        else:
            raise ValueError(f"Unsupported CUDA compute operation: {operation}")
        return ComputeResult(value=value, backend=self.name, metadata={"memory_budget": self.memory_budget})

    def prepare_volume(
        self,
        raw_data,
        source_mode="frame",
        frame_index=0,
        t_low=0,
        t_up=0,
        angle=0.0,
        preview=False,
        max_voxels=2_500_000,
    ):
        cp = self._cp
        raw_data = np.asarray(raw_data, dtype=np.float32)
        volume_bytes = int(np.prod(raw_data.shape[:3], dtype=np.int64) * np.dtype(np.float32).itemsize)
        working_multiplier = 3 if abs(float(angle)) >= 1e-6 else 2
        if self.memory_budget and volume_bytes * working_multiplier > self.memory_budget:
            raise MemoryError(
                f"A {volume_bytes / 1024 ** 3:.2f} GiB volume exceeds the CUDA working-memory budget."
            )
        if source_mode == "time_integral":
            low, up = _normalized_range(t_low, t_up, raw_data.shape[3])
            interval = raw_data[..., low:up + 1]
            if self.memory_budget and int(interval.nbytes + volume_bytes) > self.memory_budget:
                # Stream one frame at a time so a large 4D interval is never copied to VRAM in full.
                volume = cp.zeros(raw_data.shape[:3], dtype=cp.float32, order="F")
                for frame_index in range(low, up + 1):
                    volume += cp.asarray(raw_data[..., frame_index], dtype=cp.float32)
            else:
                volume = cp.sum(cp.asarray(interval), axis=3, dtype=cp.float32)
        else:
            frame_index = int(np.clip(int(frame_index), 0, raw_data.shape[3] - 1))
            volume = cp.asarray(raw_data[..., frame_index], dtype=cp.float32)
        if preview:
            stride = _preview_stride(volume.shape, max_voxels)
            volume = volume[::stride, ::stride, ::stride]
        if abs(float(angle)) >= 1e-6:
            volume = self._ndimage.rotate(
                volume,
                float(angle),
                axes=(0, 1),
                reshape=False,
                order=1,
                mode="constant",
                cval=0.0,
                prefilter=True,
            )
        return self._to_host_fortran(volume)

    def axis_range_sum(self, data, axis, low, up):
        cp = self._cp
        source = cp.asarray(data, dtype=cp.float32)
        axis = int(axis)
        low, up = _normalized_range(low, up, source.shape[axis])
        slices = [slice(None)] * source.ndim
        slices[axis] = slice(low, up + 1)
        return self._to_host_fortran(cp.sum(source[tuple(slices)], axis=axis, dtype=cp.float32))

    def second_derivative(self, data, axis, coordinates=None, sigma=1.5, threshold=0.0):
        cp = self._cp
        source = cp.asarray(data, dtype=cp.float32)
        axis = int(axis)
        smoothed = self._ndimage.gaussian_filter1d(source, sigma=max(float(sigma), 0.0), axis=axis)
        coords = cp.arange(source.shape[axis], dtype=cp.float64) if coordinates is None else cp.asarray(coordinates, dtype=cp.float64)
        first = cp.gradient(smoothed, coords, axis=axis, edge_order=2)
        result = -cp.gradient(first, coords, axis=axis, edge_order=2)
        result = cp.where(result < float(threshold), cp.float32(0), result)
        return self._to_host_fortran(result.astype(cp.float32, copy=False))

    def free_unused_memory(self):
        if not self.available:
            return
        self._cp.get_default_memory_pool().free_all_blocks()
        self._cp.get_default_pinned_memory_pool().free_all_blocks()

    def _to_host_fortran(self, array):
        cp = self._cp
        try:
            owner = cp.cuda.alloc_pinned_memory(int(array.nbytes))
            pinned = np.frombuffer(owner, dtype=array.dtype, count=array.size).reshape(array.shape, order="C")
            cp.asnumpy(array, out=pinned)
            # A strided device-to-host copy into an F-order destination makes
            # CuPy load cuBLAS.  Keep the transfer pinned and perform the cheap
            # layout conversion on the host so ndimage-only installations need
            # only cudart + NVRTC.
            return np.asfortranarray(pinned)
        except Exception:
            return np.asfortranarray(cp.asnumpy(array))


class BackendManager:
    MODES = ("Auto", "CPU", "NVIDIA GPU")

    def __init__(self, mode="Auto", *, cpu_backend=None, gpu_backend=None):
        self.cpu = cpu_backend or CpuComputeBackend()
        self.gpu = gpu_backend if gpu_backend is not None else CupyComputeBackend()
        self.mode = "Auto"
        self.set_mode(mode)
        self.last_fallback_error: Optional[str] = None

    @property
    def gpu_available(self):
        return bool(self.gpu is not None and self.gpu.available)

    def set_mode(self, mode):
        mode = str(mode)
        if mode not in self.MODES:
            mode = "Auto"
        if mode == "NVIDIA GPU" and not self.gpu_available:
            mode = "CPU"
        self.mode = mode
        return self.mode

    def selected_backend_name(self):
        if self.mode == "NVIDIA GPU" or (self.mode == "Auto" and self.gpu_available):
            return "NVIDIA GPU"
        return "CPU"

    def execute(self, job):
        use_gpu = self.mode == "NVIDIA GPU" or (self.mode == "Auto" and self.gpu_available)
        if use_gpu:
            try:
                self.last_fallback_error = None
                return self.gpu.execute(job)
            except Exception as exc:
                self.last_fallback_error = str(exc)
                try:
                    self.gpu.free_unused_memory()
                except Exception:
                    pass
        result = self.cpu.execute(job)
        if self.last_fallback_error:
            result.metadata["gpu_fallback_error"] = self.last_fallback_error
        return result

    def clear_caches(self):
        if self.gpu is not None:
            try:
                self.gpu.free_unused_memory()
            except Exception:
                pass
