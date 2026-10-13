"""Shared scalar-to-color and opacity rules for crops and local display regions."""
import numpy as np


OPACITY_MAPS = {
    "linear": [0., .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.],
    "log": [0., .157, .249, .320, .383, .441, .494, .544, .591, .636, 1.],
    "power": [0., .188, .266, .327, .378, .424, .467, .507, .545, .583, 1.],
    "sigmoid": [.006, .018, .049, .118, .268, .500, .732, .882, .951, .982, .994],
}
for _name, _key in (("线性", "linear"), ("对数", "log"), ("幂函数", "power")):
    OPACITY_MAPS[_name] = OPACITY_MAPS[_key]


def level_info(data_range, levels, *, include_zero=False):
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
    return {"data_min": low, "data_max": high, "span": span,
            "black_pos": float(b), "white_pos": float(w), "gray_pos": float(gray),
            "black_value": low + b * span, "white_value": low + w * span,
            "gray_value": low + gray * span, "gamma_power": power}


def mapped_positions(info, samples):
    samples = np.asarray(samples, dtype=np.float64)
    return info["black_pos"] + (info["white_pos"] - info["black_pos"]) * np.power(
        np.clip(samples, 0., 1.), info["gamma_power"])


def mapped_opacity(opacity, info):
    values = np.asarray(opacity, dtype=np.float64)
    if values.size <= 1:
        return opacity
    x = np.linspace(0., 1., values.size)
    return np.interp(np.power(x, info["gamma_power"]), x, values).tolist()


def opacity_ramp_points(mode, info):
    values = OPACITY_MAPS.get(str(mode), OPACITY_MAPS["linear"])
    opacities = np.asarray(mapped_opacity(values, info), dtype=np.float64)
    low, high = float(info["black_value"]), float(info["white_value"])
    if high <= low:
        high = low + 1.
    return low + (high - low) * np.arange(len(opacities)) / max(len(opacities)-1, 1), opacities


def channels(values, info, mode, *, view="3d"):
    """2D uses the crop's opaque ListedColormap; 3D uses its opacity ramp."""
    source = np.asarray(values)
    t = np.clip((source.astype(np.float64) - info["black_value"]) /
                (info["white_value"] - info["black_value"]), 0., 1.)
    if view == "2d":
        # ListedColormap indexes the 256 samples before applying the base palette.
        t = np.minimum(np.floor(t * 256), 255) / 255
    color = mapped_positions(info, t)
    if view == "2d":
        alpha = np.ones(source.shape, dtype=np.float32)
    else:
        x, a = opacity_ramp_points(mode, info)
        alpha = np.interp(source, x, a)
    finite = np.isfinite(source)
    return np.where(finite, color, 0).astype(np.float64 if view == "2d" else np.float32), np.where(finite, alpha, 0).astype(np.float32)


def normalized_color_samples(values, info):
    return np.clip((np.asarray(values, dtype=np.float64) - info["black_value"]) /
                   (info["white_value"] - info["black_value"]), 0., 1.).astype(np.float32)


def volume_unit_distance(shape, full_shape):
    """PyVista's default opacity distance with the host's physical grid spacing."""
    shape, full_shape = np.asarray(shape, dtype=float), np.asarray(full_shape, dtype=float)
    spacing = np.divide(200., full_shape-1., out=np.ones(3), where=full_shape > 1.)
    denominator = np.mean(shape) - 1.
    return float(np.linalg.norm((shape-1.) * spacing) / denominator) if denominator > 0 else 0.
