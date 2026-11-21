"""Versioned page-display defaults and validation, independent of Qt and VTK."""
from __future__ import annotations

import copy
import math

LEGACY_MODE = "legacy"
INDEPENDENT_MODE = "independent"
NORMALIZATION_FULL = "full"
DEFAULT_LEVELS = (0.0, 50.0, 100.0)
DEFAULT_COLORMAP_RANGE = (0.0, 1.0)
DEFAULT_OPACITY_MODE = "线性"
# 默认三点曲线即旧“兼容映射 → 线性”在默认色阶下的外观。
DEFAULT_OPACITY_POINTS = ((0.0, 0.0), (0.5, 0.5), (1.0, 1.0))
MIN_OPACITY_POINT_GAP = 1.0e-4
DEFAULT_OPACITY_REFERENCE_DISTANCE = 1.0
# 旧独立模式保存过的四参数曲线。
LEGACY_OPACITY_KEYS = ("threshold", "transition", "gamma", "density")
LEGACY_OPACITY_DEFAULTS = {
    "threshold": 0.0, "transition": 0.0, "gamma": 1.4, "density": 12.0,
}
LEGACY_OPACITY_PRESETS = {
    "weak": {"threshold": 0.0, "transition": 0.0, "gamma": 1.4, "density": 12.0},
    "strong": {"threshold": 21.739, "transition": 0.0, "gamma": 1.3, "density": 40.0},
}


def default_opacity_points():
    return [list(point) for point in DEFAULT_OPACITY_POINTS]


def legacy_display_style(levels=DEFAULT_LEVELS, opacity_mode=DEFAULT_OPACITY_MODE,
                         colormap_range=DEFAULT_COLORMAP_RANGE):
    return {
        "levels": tuple(float(value) for value in levels),
        "opacity_mode": str(opacity_mode),
        "colormap_range": tuple(float(value) for value in colormap_range),
    }


def normalize_opacity_points(points):
    """按强度排序、去重并固定端点的有效点列；端点始终位于 0 与 1。"""
    cleaned = []
    for entry in points or ():
        try:
            intensity, alpha = float(entry[0]), float(entry[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if not (math.isfinite(intensity) and math.isfinite(alpha)):
            continue
        cleaned.append((min(1.0, max(0.0, intensity)), min(1.0, max(0.0, alpha))))
    if not cleaned:
        return [tuple(point) for point in DEFAULT_OPACITY_POINTS]
    cleaned.sort(key=lambda point: point[0])
    result = []
    for intensity, alpha in cleaned:
        if result and intensity - result[-1][0] < MIN_OPACITY_POINT_GAP:
            # 同一强度的重复点合并：后写入的值获胜，横坐标保持前一个。
            result[-1] = (result[-1][0], alpha)
            continue
        result.append((intensity, alpha))
    if result[0][0] < MIN_OPACITY_POINT_GAP:
        result[0] = (0.0, result[0][1])
    else:
        result.insert(0, (0.0, result[0][1]))
    if result[-1][0] > 1.0 - MIN_OPACITY_POINT_GAP:
        result[-1] = (1.0, result[-1][1])
    else:
        result.append((1.0, result[-1][1]))
    return result


def _opacity_parameter_values(opacity):
    preset = opacity.get("preset")
    if preset in LEGACY_OPACITY_PRESETS:
        candidates = LEGACY_OPACITY_PRESETS[preset]
    else:
        candidates = opacity
    return {
        key: _finite_float(candidates.get(key), LEGACY_OPACITY_DEFAULTS[key])
        for key in LEGACY_OPACITY_KEYS
    }


def has_opacity_points(opacity):
    return isinstance(opacity, dict) and isinstance(opacity.get("points"), (list, tuple))


def _legacy_opacity_conversion(opacity, reference_distance):
    from bandscope.processing.display_transfer import legacy_opacity_points

    return [list(point) for point in legacy_opacity_points(
        _opacity_parameter_values(opacity),
        DEFAULT_OPACITY_REFERENCE_DISTANCE if reference_distance is None else reference_distance,
    )]


def new_display_style(levels=DEFAULT_LEVELS, opacity_mode=DEFAULT_OPACITY_MODE,
                      colormap_range=DEFAULT_COLORMAP_RANGE, opacity_points=None):
    return {
        "display_mode": INDEPENDENT_MODE,
        "levels": tuple(float(value) for value in levels),
        "opacity_mode": str(opacity_mode),
        "colormap_range": tuple(float(value) for value in colormap_range),
        "normalization": {"mode": NORMALIZATION_FULL},
        "opacity": {"points": [list(point) for point in normalize_opacity_points(
            default_opacity_points() if opacity_points is None else opacity_points
        )]},
    }


def is_independent(style):
    return isinstance(style, dict) and style.get("display_mode") == INDEPENDENT_MODE


_DISPLAY_STYLE_KEYS = frozenset({
    "levels", "opacity_mode", "colormap_range", "display_mode", "normalization", "opacity",
})


def looks_like_display_style(value):
    """区分显示样式与同样带 levels/opacity_mode 的容器（例如 Blender 显示覆写）。"""
    return (
        isinstance(value, dict)
        and "levels" in value
        and "opacity_mode" in value
        and set(value).issubset(_DISPLAY_STYLE_KEYS)
    )


def _finite_float(value, default):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if result == result and abs(result) != float("inf") else float(default)


def _colormap_range(style):
    values = tuple(style.get("colormap_range", DEFAULT_COLORMAP_RANGE))
    if len(values) != 2:
        values = DEFAULT_COLORMAP_RANGE
    low, high = (
        min(1.0, max(0.0, _finite_float(value, default)))
        for value, default in zip(values, DEFAULT_COLORMAP_RANGE)
    )
    return (low, high) if low <= high else (high, low)


def sanitized_legacy_opacity(opacity):
    opacity = opacity if isinstance(opacity, dict) else {}
    return {
        "threshold": min(100.0, max(0.0, _finite_float(opacity.get("threshold"), 0.0))),
        "transition": min(100.0, max(0.0, _finite_float(opacity.get("transition"), 0.0))),
        "gamma": min(4.0, max(0.1, _finite_float(opacity.get("gamma"), 1.4))),
        "density": min(100.0, max(0.0, _finite_float(opacity.get("density"), 12.0))),
    }


def resolved_display_style(style, *, reference_distance=None, convert_opacity=True):
    """Canonical style; ``reference_distance`` converts pre-point opacity curves.

    ``convert_opacity=False`` keeps the legacy four-parameter opacity intact so an
    old Blender project can be rewritten in its original format.
    """
    if not is_independent(style):
        if isinstance(style, dict):
            return legacy_display_style(
                style.get("levels", DEFAULT_LEVELS), style.get("opacity_mode", DEFAULT_OPACITY_MODE),
                _colormap_range(style),
            )
        return legacy_display_style()
    levels = tuple(style.get("levels", DEFAULT_LEVELS))
    if len(levels) != 3:
        levels = DEFAULT_LEVELS
    levels = tuple(min(100.0, max(0.0, _finite_float(value, default)))
                   for value, default in zip(levels, DEFAULT_LEVELS))
    opacity = style.get("opacity") or {}
    if has_opacity_points(opacity):
        points = [list(point) for point in normalize_opacity_points(opacity["points"])]
        opacity_value = {"points": points}
    elif convert_opacity:
        opacity_value = {"points": _legacy_opacity_conversion(opacity, reference_distance)}
    else:
        opacity_value = sanitized_legacy_opacity(opacity)
    return {
        "display_mode": INDEPENDENT_MODE,
        "levels": levels,
        "opacity_mode": str(style.get("opacity_mode", DEFAULT_OPACITY_MODE)),
        "colormap_range": _colormap_range(style),
        "normalization": {"mode": NORMALIZATION_FULL},
        "opacity": opacity_value,
    }


def with_display_mode(style, mode, *, reference_distance=None, source_distance=None):
    current = copy.deepcopy(style) if isinstance(style, dict) else legacy_display_style()
    colormap_range = _colormap_range(current)
    levels = current.get("levels", DEFAULT_LEVELS)
    opacity_mode = current.get("opacity_mode", DEFAULT_OPACITY_MODE)
    if mode == INDEPENDENT_MODE:
        if is_independent(current):
            return resolved_display_style(current, reference_distance=reference_distance)
        from bandscope.processing.display_transfer import compatible_opacity_points

        points = compatible_opacity_points(
            levels, opacity_mode,
            DEFAULT_OPACITY_REFERENCE_DISTANCE if reference_distance is None else reference_distance,
            source_distance,
        )
        return new_display_style(levels, opacity_mode, colormap_range, points)
    return legacy_display_style(levels, opacity_mode, colormap_range)


def same_display_style(left, right, *, reference_distance=None):
    """两种样式在可编辑模型下是否等价（兼容旧样式的语义比较）。"""
    if left == right:
        return True
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if set(left) == set(right):
        return False
    converted = [
        resolved_display_style(
            with_display_mode(style, INDEPENDENT_MODE, reference_distance=reference_distance),
            reference_distance=reference_distance,
        )
        for style in (left, right)
    ]
    return converted[0] == converted[1]


def new_render_quality():
    return {"mode": "explicit", "interpolation": "linear", "resting": "standard"}


def legacy_render_quality():
    return {"mode": LEGACY_MODE, "interpolation": "nearest", "resting": "standard"}


def resolved_render_quality(quality):
    if not isinstance(quality, dict) or quality.get("mode") != "explicit":
        return legacy_render_quality()
    interpolation = "nearest" if quality.get("interpolation") == "nearest" else "linear"
    resting = "fine" if quality.get("resting") == "fine" else "standard"
    return {"mode": "explicit", "interpolation": interpolation, "resting": resting}


def create_display_state(style=None, *, legacy=False):
    background = (legacy_display_style() if legacy else new_display_style()) if style is None else copy.deepcopy(style)
    return {
        "state_version": 2,
        "regions": [],
        "background": background,
        "revision": 0,
        "next_label": 1,
        "common": False,
    }


def migrate_display_state(state, *, legacy=True, reference_distance=None):
    result = copy.deepcopy(state) if isinstance(state, dict) else create_display_state(legacy=legacy)
    result.setdefault("regions", [])
    result.setdefault("revision", 0)
    result.setdefault("next_label", len(result["regions"]) + 1)
    result.setdefault("common", False)
    result["state_version"] = 2
    styles = [result.setdefault("background", legacy_display_style())]
    if "common_style" in result:
        styles.append(result["common_style"])
    styles.extend(region.setdefault("style", legacy_display_style()) for region in result["regions"])
    for style in styles:
        if legacy:
            if is_independent(style):
                converted = with_display_mode(style, LEGACY_MODE)
                style.clear()
                style.update(converted)
            style["display_mode"] = LEGACY_MODE
        elif is_independent(style):
            canonical = resolved_display_style(style, reference_distance=reference_distance)
            style.clear()
            style.update(canonical)
    return result


def editable_display_state(state, *, reference_distance=None):
    """把整张显示状态（背景、共同样式与全部区域）转换为可编辑点列。"""
    result = copy.deepcopy(state) if isinstance(state, dict) else create_display_state()
    result.setdefault("regions", [])
    result.setdefault("revision", 0)
    result.setdefault("next_label", len(result["regions"]) + 1)
    result.setdefault("common", False)
    result["state_version"] = 2
    targets = [result.setdefault("background", new_display_style())]
    if "common_style" in result:
        targets.append(result["common_style"])
    targets.extend(region.setdefault("style", new_display_style()) for region in result["regions"])
    for style in targets:
        converted = with_display_mode(style, INDEPENDENT_MODE, reference_distance=reference_distance)
        style.clear()
        style.update(converted)
    return result


def normalization_policy(style):
    return (NORMALIZATION_FULL,) if is_independent(style) else ("legacy",)


def has_restricted_normalization(style):
    if not is_independent(style):
        return False
    normalization = style.get("normalization")
    return not isinstance(normalization, dict) or normalization.get("mode") != NORMALIZATION_FULL
