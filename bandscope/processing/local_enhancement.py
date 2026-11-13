"""Resolve independent color and opacity channels without modifying science data."""
import threading
import weakref

import numpy as np

from bandscope.core.display_style import is_independent, normalization_policy
from bandscope.core.local_enhancement import DEFAULT_STYLE, matching_regions, region_slices
from bandscope.processing.display_transfer import (
    channels,
    display_unit_distance,
    level_info,
    normalization_range,
    volume_display_unit_distance,
    volume_unit_distance,
)
from bandscope.processing.gpu_display import array_key

_RANGE_CACHE = {}
_RANGE_CACHE_LOCK = threading.RLock()
_RANGE_CACHE_LIMIT = 24


def _range_key(data, style, include_zero):
    values = np.asarray(data)
    key, base = array_key(values)
    return (key, normalization_policy(style), bool(include_zero)), base


def cached_finite_range(data, style=None, *, include_zero=False):
    """Return ``(cache_hit, range)`` without starting a synchronous statistic."""
    values = np.asarray(data)
    key, base = _range_key(values, style, include_zero)
    with _RANGE_CACHE_LOCK:
        entry = _RANGE_CACHE.get(key)
        if entry is not None and entry[0]() is not None:
            return True, entry[1]
    return False, None


def finite_range(data, style=None, *, include_zero=False, allow_compute=True):
    values = np.asarray(data)
    hit, result = cached_finite_range(values, style, include_zero=include_zero)
    if hit or not allow_compute:
        return result
    key, base = _range_key(values, style, include_zero)
    result = _measure_range(values, style, include_zero)
    with _RANGE_CACHE_LOCK:
        _RANGE_CACHE[key] = (weakref.ref(base), result)
        if len(_RANGE_CACHE) > _RANGE_CACHE_LIMIT:
            for stale_key, item in list(_RANGE_CACHE.items()):
                if item[0]() is None:
                    _RANGE_CACHE.pop(stale_key, None)
            while len(_RANGE_CACHE) > _RANGE_CACHE_LIMIT:
                _RANGE_CACHE.pop(next(iter(_RANGE_CACHE)))
    return result


def _measure_range(values, style=None, include_zero=False):
    if is_independent(style) and normalization_policy(style)[0] == "manual":
        return normalization_range(values, style)
    policy = normalization_policy(style)
    if policy[0] == "legacy" and not np.any(np.isfinite(values)):
        return None
    if policy[0] in {"legacy", "full"}:
        from bandscope.processing import gpu_display

        handled, result = gpu_display.try_finite_range(values)
        if handled:
            if result is not None and include_zero:
                result = (min(0.0, result[0]), max(0.0, result[1]))
            return result
    return normalization_range(values, style, include_zero=include_zero)


def clear_range_cache():
    with _RANGE_CACHE_LOCK:
        _RANGE_CACHE.clear()


def _rebased_alpha(alpha, source_distance, target_distance):
    if source_distance <= 0.0 or target_distance <= 0.0 or np.isclose(source_distance, target_distance):
        return alpha
    bounded = np.clip(np.asarray(alpha, dtype=np.float64), 0.0, 1.0 - 1.0e-12)
    return (1.0 - np.power(1.0 - bounded, target_distance / source_distance)).astype(np.float32)


def resolve_display(context, state, *, background_locked_range=None, region_data=None):
    """Statistics use original samples; later enabled regions own overlaps."""
    regions = matching_regions(context, state.get("regions", []))
    background_style = state.get("background", DEFAULT_STYLE)
    if not regions and not is_independent(background_style):
        return None
    source = np.asarray(context["data"])
    view = context["view"]
    include_zero = view == "3d" and bool(context.get("enhancement_include_zero", context.get("include_zero")))
    background_include_zero = view == "3d" and bool(context.get("include_zero"))
    region_data = region_data or {}
    full_shape = context.get("full_shape", source.shape)
    legacy_distance = volume_unit_distance(source.shape, full_shape) if view == "3d" else 0.0
    unified_distance = volume_display_unit_distance(full_shape) if view == "3d" else 0.0
    candidates = []
    for region in regions:
        slices = region_slices(context, region)
        values = region_data.get(region["region_id"], source[slices] if slices is not None else None)
        if slices is not None and values is not None and np.shape(values) != source[slices].shape:
            raise ValueError("增强区域的数据形状与裁剪范围不匹配。")
        style = state.get("common_style", DEFAULT_STYLE) if state.get("common") else region.get("style", DEFAULT_STYLE)
        actual = finite_range(values) if slices is not None and values is not None else None
        measured = finite_range(values, style) if slices is not None and values is not None else None
        if measured is not None and include_zero and not is_independent(style):
            measured = (min(0.0, measured[0]), max(0.0, measured[1]))
        candidates.append((region, slices, actual, measured, values, style))
    shared_range = None
    if state.get("common"):
        ranges = [measured for region, _, actual, measured, _, _ in candidates
                  if region.get("enabled", True) and measured is not None]
        if ranges:
            shared_range = (min(r[0] for r in ranges), max(r[1] for r in ranges))
        shared_range = state.get("common_locked_range") or shared_range
    owners = np.zeros(source.shape, dtype=np.int32)
    resolved = []
    any_independent = is_independent(background_style)
    for region, slices, actual, measured, values, style in candidates:
        any_independent = any_independent or is_independent(style)
        data_range = shared_range if state.get("common") else region.get("locked_range") or measured
        status = ("disabled" if not region.get("enabled", True) else "empty" if actual is None
                  else "constant" if data_range is None or data_range[1] <= data_range[0] else "ready")
        record = dict(
            region,
            data_range=data_range,
            status=status,
            level_info=level_info(data_range, style["levels"]),
            style=dict(style),
            slices=slices,
            normalization_range=data_range,
        )
        if context.get("enhancement_errors", {}).get(region["region_id"]):
            record["error"] = context["enhancement_errors"][region["region_id"]]
        if view == "3d" and values is not None:
            record["opacity_unit_distance"] = volume_unit_distance(np.shape(values), full_shape)
        resolved.append(record)
        if status in {"ready", "constant"}:
            owners[slices] = len(resolved)
    target_distance = unified_distance if any_independent else legacy_distance
    bg_range = background_locked_range or finite_range(
        source, background_style,
        include_zero=background_include_zero and not is_independent(background_style),
    )
    bg_info = level_info(bg_range, background_style["levels"])
    color, alpha = channels(
        source, bg_info, background_style["opacity_mode"], view=view,
        style=background_style, unit_distance=target_distance,
    )
    if view == "3d" and any_independent and not is_independent(background_style):
        alpha = _rebased_alpha(alpha, legacy_distance, target_distance)
    for index, region in enumerate(resolved, 1):
        if region["status"] not in {"ready", "constant"}:
            continue
        slices = region["slices"]
        values = candidates[index - 1][4]
        style = region["style"]
        c, a = channels(
            values, region["level_info"], style["opacity_mode"], view=view,
            style=style, unit_distance=target_distance,
        )
        if view == "3d" and not is_independent(style):
            source_distance = region.get("opacity_unit_distance", legacy_distance)
            a = _rebased_alpha(a, source_distance, target_distance)
        mask = owners[slices] == index
        color[slices][mask] = c[mask]
        alpha[slices][mask] = a[mask]
    return {
        "color": color,
        "alpha": alpha,
        "background_level_info": bg_info,
        "background_style": dict(background_style),
        "background_range": (bg_info["data_min"], bg_info["data_max"]),
        "regions": resolved,
        "common": bool(state.get("common")),
        "active": any(r["status"] in {"ready", "constant"} for r in resolved),
        "independent": any_independent,
        "opacity_unit_distance": target_distance,
        "revision": int(state.get("revision", 0)),
        "owners": owners,
        "color_rules": [],
        "region_data": {r["region_id"]: values for r, _, _, _, values, _ in candidates if values is not None},
        "geometry_context": {k: v for k, v in context.items() if k != "data"},
    }


def orient_2d(values, slice_info, extent):
    image = np.asarray(values).T
    if slice_info.get("display_e_flip") and int(slice_info["axis"]) in (0, 1):
        image = image[::-1]
    if extent[0] > extent[1]:
        image = image[:, ::-1]
    if extent[2] > extent[3]:
        image = image[::-1]
    return image
