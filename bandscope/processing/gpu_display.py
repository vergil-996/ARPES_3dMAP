"""Optional CUDA acceleration for the per-voxel display mapping.

The CPU implementation in :mod:`bandscope.processing.display_transfer` stays the
reference: this module either returns bit-identical values or declines so the
caller keeps the CPU path.  It is opt-in and only the host enables it for the
NVIDIA backend, therefore the test suite and CPU builds never import CuPy.

Why the mapping is computed here instead of in the transfer function:
``docs/handoffs/display-transfer-weak-signals.md`` requires gamma to be applied
per voxel before the palette lookup.  Baking the curve on the GPU keeps that
semantic and only moves where the arithmetic runs.
"""
from __future__ import annotations

import os
import threading
import weakref

import numpy as np

MIN_ELEMENTS = 500_000
MAX_DEVICE_ARRAYS = 4

_lock = threading.RLock()
_enabled = False
_probed = False
_cp = None
_error = None
_devices = {}


def _probe():
    """Import CuPy and verify that the mapping kernels run on this device."""
    global _probed, _cp, _error
    if _probed:
        return _cp is not None
    _probed = True
    try:
        import cupy as cp

        if int(cp.cuda.runtime.getDeviceCount()) < 1:
            raise RuntimeError("No CUDA device was detected.")
        probe = cp.arange(16, dtype=cp.float32).reshape(4, 4)
        cp.asnumpy(cp.interp(probe, cp.asarray([0.0, 1.0]), cp.asarray([0.0, 1.0])))
        _cp = cp
        _error = None
    except Exception as exc:  # pragma: no cover - depends on the host GPU
        _cp = None
        _error = exc
    return _cp is not None


def available():
    with _lock:
        return _probe()


def set_enabled(flag):
    """Host switch: enable only when the selected compute backend is NVIDIA."""
    global _enabled
    with _lock:
        if os.environ.get("BANDSCOPE_DISABLE_GPU_DISPLAY"):
            _enabled = False
            return False
        _enabled = bool(flag) and _probe()
        return _enabled


def enabled():
    with _lock:
        return bool(_enabled)


def failure_reason():
    with _lock:
        return None if _error is None else f"{type(_error).__name__}: {_error}"


def _disable(exc):
    global _enabled, _error
    _enabled = False
    _error = exc
    print(f"GPU display mapping disabled: {type(exc).__name__}: {exc}")


def array_key(array):
    """Stable key for a view of cached data: owning buffer + byte offset + layout.

    The app hands out a fresh view of the same frame on every refresh
    (``raw[..., t]``), so ``id(view)`` changes while the memory does not.  The
    owning array is kept as a weak reference to detect a reused address.
    """
    base = array if array.base is None else array.base
    pointer = int(array.__array_interface__["data"][0])
    return (id(base), pointer, array.shape, array.strides, array.dtype.str), base


def _device_array(cp, values):
    """Keep the volume resident between ticks; the owning buffer guards the key."""
    key, base = array_key(values)
    entry = _devices.get(key)
    if entry is not None and entry[0]() is not None:
        return entry[1], True
    device = cp.asarray(values)
    if len(_devices) >= MAX_DEVICE_ARRAYS:
        for stale_key, stale in list(_devices.items()):
            if stale[0]() is None:
                _devices.pop(stale_key, None)
        if len(_devices) >= MAX_DEVICE_ARRAYS:
            _devices.pop(next(iter(_devices)))
    _devices[key] = (weakref.ref(base), device)
    return device, False


def try_channels(values, info, mode, *, style=None, unit_distance=None, color=None, alpha=None):
    """Return display channels, computing only the missing independent channels."""
    with _lock:
        if not _enabled or _cp is None:
            return None
        source = np.asarray(values)
        if source.ndim != 3 or source.size < MIN_ELEMENTS or (color is not None and alpha is not None):
            return None
        cp = _cp
        try:
            from bandscope.core.display_style import is_independent, resolved_display_style
            from bandscope.processing.display_transfer import OPACITY_MAPS

            independent = is_independent(style)
            if not independent and (color is not None or alpha is not None):
                return None
            device, _cached = _device_array(cp, source)
            need_color = color is None
            need_alpha = alpha is None
            computed_color = color
            computed_alpha = alpha
            if need_color or not independent:
                low, high = float(info["black_value"]), float(info["white_value"])
                ratio = cp.clip((device.astype(cp.float64) - low) / (high - low), 0.0, 1.0)
                mapped = cp.power(ratio, float(info["gamma_power"]))
                if need_color:
                    cmap_low = float(info.get("colormap_start", 0.0))
                    cmap_high = float(info.get("colormap_end", 1.0))
                    computed_color = cmap_low + (cmap_high - cmap_low) * mapped
            if independent:
                if need_alpha:
                    from bandscope.core.display_style import DEFAULT_OPACITY_REFERENCE_DISTANCE

                    reference = (DEFAULT_OPACITY_REFERENCE_DISTANCE
                                 if unit_distance is None else float(unit_distance))
                    opacity = resolved_display_style(
                        style, reference_distance=reference,
                    )["opacity"]
                    from bandscope.core.opacity_curve import evaluate_opacity_curve

                    span = float(info.get("span", float(info["data_max"]) - float(info["data_min"])))
                    if not np.isfinite(span) or span <= 0.0:
                        span = 1.0
                    q = (device.astype(cp.float64) - float(info["data_min"])) / span
                    computed_alpha = evaluate_opacity_curve(
                        q, opacity["points"], interpolation=opacity["interpolation"],
                        segments=opacity.get("segments"), xp=cp,
                    )
            elif need_alpha:
                ramp = np.asarray(OPACITY_MAPS.get(str(mode), OPACITY_MAPS["linear"]), dtype=np.float64)
                computed_alpha = cp.interp(mapped, cp.linspace(0.0, 1.0, ramp.size, dtype=cp.float64),
                                           cp.asarray(ramp))
            finite = cp.isfinite(device)
            if need_color and need_alpha:
                packed = cp.empty((source.size, 2), dtype=cp.float32)
                packed[:, 0] = cp.where(finite, computed_color, 0).astype(cp.float32).ravel(order="F")
                packed[:, 1] = cp.where(finite, computed_alpha, 0).astype(cp.float32).ravel(order="F")
                owner = cp.cuda.alloc_pinned_memory(int(packed.nbytes))
                host = np.frombuffer(owner, dtype=np.float32, count=int(packed.size)).reshape(packed.shape)
                cp.asnumpy(packed, out=host)
                return (host[:, 0].reshape(source.shape, order="F"),
                        host[:, 1].reshape(source.shape, order="F"))
            device_channel = computed_color if need_color else computed_alpha
            packed = cp.where(finite, device_channel, 0).astype(cp.float32).ravel(order="F")
            owner = cp.cuda.alloc_pinned_memory(int(packed.nbytes))
            host = np.frombuffer(owner, dtype=np.float32, count=int(packed.size))
            cp.asnumpy(packed, out=host)
            result = host.reshape(source.shape, order="F")
            return (result, alpha) if need_color else (color, result)
        except Exception as exc:  # pragma: no cover - depends on the host GPU
            _disable(exc)
            return None


def try_finite_range(values):
    """Return ``(handled, (low, high) or None)``; ``handled=False`` means CPU."""
    with _lock:
        if not _enabled or _cp is None:
            return False, None
        source = np.asarray(values)
        if source.size < MIN_ELEMENTS:
            return False, None
        cp = _cp
        try:
            device, _cached = _device_array(cp, source)
            finite = cp.isfinite(device)
            if not bool(cp.any(finite)):
                return True, None
            low = float(cp.min(cp.where(finite, device, cp.inf)))
            high = float(cp.max(cp.where(finite, device, -cp.inf)))
            return True, (low, high)
        except Exception as exc:  # pragma: no cover - depends on the host GPU
            _disable(exc)
            return False, None


def reset():
    """Drop device buffers; used when the compute backend or data changes."""
    with _lock:
        _devices.clear()
