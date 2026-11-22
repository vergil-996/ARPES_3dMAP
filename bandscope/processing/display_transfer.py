"""Shared scalar-to-color and opacity rules for crops and local display regions."""
import math

import numpy as np

from bandscope.core.display_style import (
    DEFAULT_COLORMAP_RANGE,
    DEFAULT_LEVELS,
    DEFAULT_OPACITY_MODE,
    DEFAULT_OPACITY_REFERENCE_DISTANCE,
    is_independent,
    resolved_display_style,
)
from bandscope.core.opacity_curve import evaluate_opacity_curve

# 折线逼近旧曲线时允许的 α 误差：半个量化步长，保证最终误差不超过 1/255。
OPACITY_POINT_TOLERANCE = 1.0 / 255.0 / 2.0
OPACITY_POINT_MAX_DEPTH = 14


OPACITY_MAPS = {
    "linear": [0., .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.],
    "log": [0., .157, .249, .320, .383, .441, .494, .544, .591, .636, 1.],
    "power": [0., .188, .266, .327, .378, .424, .467, .507, .545, .583, 1.],
    "sigmoid": [.006, .018, .049, .118, .268, .500, .732, .882, .951, .982, .994],
}
for _name, _key in (("线性", "linear"), ("对数", "log"), ("幂函数", "power")):
    OPACITY_MAPS[_name] = OPACITY_MAPS[_key]


def level_info(data_range, levels, *, include_zero=False, colormap_range=DEFAULT_COLORMAP_RANGE):
    low, high = data_range or (0., 1.)
    if include_zero:
        low, high = min(low, 0.), max(high, 0.)
    if high <= low:
        high = low + 1.
    black, gamma, white = levels
    b, w = np.clip([black / 100., white / 100.], 0., 1.)
    if w <= b:
        w = min(1., b + .01)
    if w <= b:
        b = max(0., w - .01)
    power = max(float(np.power(10., (50 - gamma) / 50.)), 1e-6)
    gray = b + (w - b) * np.power(.5, 1. / power)
    span = high - low
    cmap_low, cmap_high = colormap_range or DEFAULT_COLORMAP_RANGE
    return {"data_min": low, "data_max": high, "span": span,
            "black_pos": float(b), "white_pos": float(w), "gray_pos": float(gray),
            "black_value": low + b * span, "white_value": low + w * span,
            "gray_value": low + gray * span, "gamma_power": power,
            "colormap_start": float(cmap_low), "colormap_end": float(cmap_high)}


def gamma_for_gray_position(black, white, gray):
    black, white, gray = (float(value) for value in (black, white, gray))
    if white <= black:
        return 50.0
    position = float(np.clip((gray - black) / (white - black), 1.0e-9, 1.0 - 1.0e-9))
    power = np.log(0.5) / np.log(position)
    if not np.isfinite(power) or power <= 0.0:
        return 50.0
    gamma = 50.0 - 50.0 * np.log10(power)
    return float(np.clip(gamma, 0.0, 100.0))


def finite_samples(values):
    source = np.asarray(values)
    return source[np.isfinite(source)]


def finite_minmax(values):
    finite = finite_samples(values)
    if finite.size == 0:
        return None
    return float(np.min(finite)), float(np.max(finite))


def normalization_range(values, style=None, *, locked_range=None, include_zero=False):
    if locked_range is not None:
        low, high = (float(value) for value in locked_range)
        return low, high
    if not is_independent(style):
        measured = finite_minmax(values)
        if measured is None:
            measured = (0.0, 1.0)
        low, high = measured
        if include_zero:
            low, high = min(low, 0.0), max(high, 0.0)
        return low, high
    return finite_minmax(values) or (0.0, 1.0)


def display_level_info(values, style, *, locked_range=None, include_zero=False):
    resolved = resolved_display_style(style)
    data_range = normalization_range(
        values, resolved, locked_range=locked_range,
        include_zero=include_zero and not is_independent(resolved),
    )
    return level_info(
        data_range, resolved["levels"], colormap_range=resolved["colormap_range"],
    )


def mapped_positions(info, samples):
    samples = np.asarray(samples, dtype=np.float64)
    return np.power(np.clip(samples, 0., 1.), info["gamma_power"])


def colormap_positions(info, samples):
    low = float(info.get("colormap_start", 0.0))
    high = float(info.get("colormap_end", 1.0))
    return low + (high - low) * np.asarray(samples, dtype=np.float64)


def mapped_opacity(opacity, info):
    values = np.asarray(opacity, dtype=np.float64)
    if values.size <= 1:
        return opacity
    x = np.linspace(0., 1., values.size)
    return np.interp(np.power(x, info["gamma_power"]), x, values).tolist()


def opacity_ramp_points(mode, info):
    values = OPACITY_MAPS.get(str(mode), OPACITY_MAPS["linear"])
    positions = np.linspace(0., 1., len(values))
    if info["gamma_power"] != 1.:
        positions = np.unique(np.r_[positions, np.linspace(0., 1., 4097)])
    opacities = np.interp(positions, np.linspace(0., 1., len(values)), values)
    low, high = float(info["black_value"]), float(info["white_value"])
    if high <= low:
        high = low + 1.
    return low + (high - low) * np.power(positions, 1. / info["gamma_power"]), opacities


def opacity_values(values, info, mode):
    """Evaluate opacity after gamma, before any intensity lookup sampling."""
    ramp = OPACITY_MAPS.get(str(mode), OPACITY_MAPS["linear"])
    color = mapped_positions(info, normalized_color_samples(values, info))
    return np.interp(color, np.linspace(0., 1., len(ramp)), ramp)


def normalized_intensity_samples(values, info):
    source = np.asarray(values, dtype=np.float64)
    span = float(info.get("span", float(info["data_max"]) - float(info["data_min"])))
    if not np.isfinite(span) or span <= 0.0:
        span = 1.0
    return (source - float(info["data_min"])) / span


def independent_opacity_values(values, info, style, unit_distance=None):
    """可编辑曲线的最终逐体素 α；``unit_distance`` 是曲线的参考距离。"""
    reference = (DEFAULT_OPACITY_REFERENCE_DISTANCE if unit_distance is None
                 else float(unit_distance))
    opacity = resolved_display_style(style, reference_distance=reference)["opacity"]
    q = normalized_intensity_samples(values, info)
    with np.errstate(invalid="ignore"):
        return evaluate_opacity_curve(
            q, opacity["points"], interpolation=opacity["interpolation"],
            segments=opacity.get("segments"),
        )


def rebase_opacity(alpha, source_distance, target_distance):
    """把同一消光系数在不同参考距离下的 α 互换：1−α₂=(1−α₁)^(d₂/d₁)。"""
    source = float(source_distance)
    target = float(target_distance)
    if source <= 0.0 or target <= 0.0 or np.isclose(source, target):
        return alpha
    bounded = np.clip(np.asarray(alpha, dtype=np.float64), 0.0, 1.0 - 1.0e-12)
    return 1.0 - np.power(1.0 - bounded, target / source)


def _legacy_parameter_alpha(samples, params, reference_distance):
    q = np.asarray(samples, dtype=np.float64)
    threshold = float(params["threshold"]) / 100.0
    transition = float(params["transition"]) / 100.0
    signal = np.maximum(q - threshold, 0.0)
    if transition > 0.0:
        position = np.clip((q - threshold) / transition, 0.0, 1.0)
        gate = position * position * (3.0 - 2.0 * position)
    else:
        gate = 1.0
    with np.errstate(invalid="ignore", over="ignore"):
        sigma = (float(params["density"]) / 200.0) * np.power(
            signal, float(params["gamma"]),
        ) * gate
        return -np.expm1(-sigma * max(float(reference_distance), 0.0))


def _compatible_alpha(samples, levels, opacity_mode):
    """旧“兼容映射”的 α 抽样，按保存时的色阶、Gamma 与映射方式重建。"""
    q = np.asarray(samples, dtype=np.float64)
    black, gamma, white = (float(value) for value in levels)
    low = float(np.clip(black / 100.0, 0.0, 1.0))
    high = float(np.clip(white / 100.0, 0.0, 1.0))
    if high <= low:
        high = min(1.0, low + 0.01)
    if high <= low:
        low = max(0.0, high - 0.01)
    power = max(float(np.power(10.0, (50.0 - gamma) / 50.0)), 1.0e-6)
    position = np.clip((q - low) / max(high - low, 1.0e-9), 0.0, 1.0)
    mapped = np.power(position, power)
    ramp = OPACITY_MAPS.get(str(opacity_mode), OPACITY_MAPS["linear"])
    return np.interp(mapped, np.linspace(0.0, 1.0, len(ramp)), ramp)


def _refine_opacity_segment(alpha, x0, a0, x1, a1, tolerance, depth, out):
    if depth >= OPACITY_POINT_MAX_DEPTH or x1 - x0 <= 1.0e-7:
        return
    middle = 0.5 * (x0 + x1)
    middle_alpha = float(alpha(middle))
    error = abs(middle_alpha - 0.5 * (a0 + a1))
    for fraction in (0.25, 0.75):
        predicted = a0 + (a1 - a0) * fraction
        error = max(error, abs(float(alpha(x0 + fraction * (x1 - x0))) - predicted))
    if error <= tolerance:
        return
    _refine_opacity_segment(alpha, x0, a0, middle, middle_alpha, tolerance, depth + 1, out)
    out.append((middle, middle_alpha))
    _refine_opacity_segment(alpha, middle, middle_alpha, x1, a1, tolerance, depth + 1, out)


def _adaptive_opacity_points(alpha, breakpoints, tolerance=OPACITY_POINT_TOLERANCE):
    xs = sorted({min(1.0, max(0.0, float(x))) for x in (0.0, 1.0, *breakpoints)})
    nodes = [(x, min(1.0, max(0.0, float(alpha(x))))) for x in xs]
    points = [nodes[0]]
    for (x0, a0), (x1, a1) in zip(nodes, nodes[1:]):
        _refine_opacity_segment(alpha, x0, a0, x1, a1, tolerance, 0, points)
        points.append((x1, a1))
    return points


def legacy_opacity_points(params, reference_distance=DEFAULT_OPACITY_REFERENCE_DISTANCE):
    """旧四参数曲线按其原公式转换为点列（端点与转折处保留）。"""
    values = {
        "threshold": min(100.0, max(0.0, float(params.get("threshold", 0.0)))),
        "transition": min(100.0, max(0.0, float(params.get("transition", 0.0)))),
        "gamma": min(4.0, max(0.1, float(params.get("gamma", 1.4)))),
        "density": min(100.0, max(0.0, float(params.get("density", 12.0)))),
    }
    distance = max(float(reference_distance), 0.0)

    def alpha(samples):
        return _legacy_parameter_alpha(samples, values, distance)

    threshold = values["threshold"] / 100.0
    transition = values["transition"] / 100.0
    return _adaptive_opacity_points(
        alpha,
        (0.0, threshold, min(1.0, threshold + transition), 1.0),
    )


def compatible_opacity_points(levels=DEFAULT_LEVELS, opacity_mode=DEFAULT_OPACITY_MODE,
                              reference_distance=DEFAULT_OPACITY_REFERENCE_DISTANCE,
                              source_distance=None):
    """旧兼容曲线按其保存的色阶、Gamma、映射方式及参考距离转换为点列。"""
    black, _gamma, white = (float(value) for value in levels)
    low = float(np.clip(black / 100.0, 0.0, 1.0))
    high = float(np.clip(white / 100.0, 0.0, 1.0))
    if high <= low:
        high = min(1.0, low + 0.01)
    source = float(reference_distance if source_distance is None else source_distance)
    target = float(reference_distance)

    def alpha(samples):
        values = _compatible_alpha(samples, levels, opacity_mode)
        return rebase_opacity(values, source, target)

    return _adaptive_opacity_points(alpha, (0.0, low, high, 1.0))



def channels(values, info, mode, *, view="3d", style=None, unit_distance=None,
             color=None, alpha=None):
    """Resolve display channels, reusing any channel supplied by the caller."""
    source = np.asarray(values)
    if color is not None and alpha is not None:
        return color, alpha
    independent = is_independent(style)
    if view == "3d" and source.size >= 500_000:
        from bandscope.processing import gpu_display

        accelerated = gpu_display.try_channels(
            source, info, mode, style=style, unit_distance=unit_distance,
            color=color, alpha=alpha,
        )
        if accelerated is not None:
            return accelerated
    computed_color = color
    mapped = None
    if color is None or (alpha is None and not independent and view != "2d"):
        t = normalized_color_samples(source, info)
        mapped = mapped_positions(info, t)
        if color is None:
            computed_color = colormap_positions(info, mapped)
    computed_alpha = alpha
    if alpha is None:
        if view == "2d":
            computed_alpha = np.ones(source.shape, dtype=np.float32)
        elif independent:
            computed_alpha = independent_opacity_values(
                source, info, style, 1.0 if unit_distance is None else unit_distance,
            )
        else:
            ramp = OPACITY_MAPS.get(str(mode), OPACITY_MAPS["linear"])
            computed_alpha = np.interp(mapped, np.linspace(0., 1., len(ramp)), ramp)
    finite = np.isfinite(source)
    if color is None:
        computed_color = np.where(finite, computed_color, 0).astype(
            np.float64 if view == "2d" else np.float32
        )
    if alpha is None:
        computed_alpha = np.where(finite, computed_alpha, 0).astype(np.float32)
    return computed_color, computed_alpha


def normalized_color_samples(values, info):
    return np.clip((np.asarray(values, dtype=np.float64) - info["black_value"]) /
                   (info["white_value"] - info["black_value"]), 0., 1.)


def volume_unit_distance(shape, full_shape):
    """PyVista's default opacity distance with the host's physical grid spacing."""
    shape, full_shape = np.asarray(shape, dtype=float), np.asarray(full_shape, dtype=float)
    spacing = np.divide(200., full_shape-1., out=np.ones(3), where=full_shape > 1.)
    denominator = np.mean(shape) - 1.
    return float(np.linalg.norm((shape-1.) * spacing) / denominator) if denominator > 0 else 0.


def display_unit_distance(spacing):
    values = np.asarray(spacing, dtype=np.float64).reshape(-1)
    positive = values[np.isfinite(values) & (values > 0.0)]
    return float(np.min(positive)) if positive.size else 1.0


def opacity_reference_distance(full_shape):
    """可编辑曲线的参考距离：旧“兼容映射 → 线性”在完整显示网格上的单位距离。

    完整网格只影响采样步长、不受裁剪影响，因此同一份数据裁剪前后参考距离一致；
    未裁剪时它就等于旧兼容渲染实际使用的 ``volume_unit_distance``。
    """
    shape = np.asarray(full_shape, dtype=np.float64).reshape(-1)[:3]
    if shape.size != 3 or not np.all(np.isfinite(shape)) or np.any(shape < 2.0):
        shape = np.array([2.0, 2.0, 2.0])
    return volume_unit_distance(shape, shape)
