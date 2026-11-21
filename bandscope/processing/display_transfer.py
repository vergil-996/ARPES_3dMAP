"""Shared scalar-to-color and opacity rules for crops and local display regions."""
import numpy as np

from bandscope.core.display_style import (
    DEFAULT_COLORMAP_RANGE,
    is_independent,
    resolved_display_style,
)


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


def independent_opacity_values(values, info, style, unit_distance=1.0):
    opacity = resolved_display_style(style)["opacity"]
    q = normalized_intensity_samples(values, info)
    threshold = float(opacity["threshold"]) / 100.0
    transition = float(opacity["transition"]) / 100.0
    signal = np.maximum(q - threshold, 0.0)
    if transition > 0.0:
        position = np.clip((q - threshold) / transition, 0.0, 1.0)
        gate = position * position * (3.0 - 2.0 * position)
    else:
        gate = 1.0
    distance = max(float(unit_distance), 0.0)
    with np.errstate(invalid="ignore", over="ignore"):
        sigma = (float(opacity["density"]) / 200.0) * np.power(signal, float(opacity["gamma"])) * gate
        return -np.expm1(-sigma * distance)


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


def volume_display_unit_distance(full_shape):
    shape = np.asarray(full_shape, dtype=np.float64)
    spacing = np.divide(200.0, shape - 1.0, out=np.ones(3), where=shape > 1.0)
    return display_unit_distance(spacing)
