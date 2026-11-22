"""Ordered opacity control points, PCHIP segments, and vertical-step evaluation."""
from __future__ import annotations

import math

import numpy as np

OPACITY_LINEAR = "linear"
OPACITY_SMOOTH = "smooth"
DEFAULT_OPACITY_INTERPOLATION = OPACITY_SMOOTH


def normalize_opacity_interpolation(value, default=OPACITY_LINEAR):
    if value is None:
        return default
    return OPACITY_SMOOTH if str(value) in {OPACITY_SMOOTH, "pchip", "平滑"} else OPACITY_LINEAR


def _point_array(points):
    values = np.asarray(points, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 2 or values.shape[0] < 2:
        raise ValueError("不透明度曲线至少需要两个控制点。")
    return values


def _hermite_segment(y0, y1, slope0, slope1):
    return [
        float(y0),
        float(slope0),
        float(3.0 * (y1 - y0) - 2.0 * slope0 - slope1),
        float(2.0 * (y0 - y1) + slope0 + slope1),
    ]


def linear_opacity_segments(points):
    values = _point_array(points)
    segments = []
    for (x0, y0), (x1, y1) in zip(values, values[1:]):
        segments.append(None if x1 <= x0 else [float(y0), float(y1 - y0), 0.0, 0.0])
    return segments


def _pchip_edge_slope(h0, h1, delta0, delta1):
    slope = ((2.0 * h0 + h1) * delta0 - h0 * delta1) / (h0 + h1)
    if math.copysign(1.0, slope) != math.copysign(1.0, delta0):
        return 0.0
    if (math.copysign(1.0, delta0) != math.copysign(1.0, delta1)
            and abs(slope) > abs(3.0 * delta0)):
        return 3.0 * delta0
    return slope


def _pchip_run_slopes(xs, ys):
    count = len(xs)
    if count == 2:
        slope = (ys[1] - ys[0]) / (xs[1] - xs[0])
        return [slope, slope]
    hs = np.diff(xs)
    deltas = np.diff(ys) / hs
    slopes = np.zeros(count, dtype=np.float64)
    for index in range(1, count - 1):
        before, after = float(deltas[index - 1]), float(deltas[index])
        if before == 0.0 or after == 0.0 or math.copysign(1.0, before) != math.copysign(1.0, after):
            continue
        h0, h1 = float(hs[index - 1]), float(hs[index])
        w1, w2 = 2.0 * h1 + h0, h1 + 2.0 * h0
        slopes[index] = (w1 + w2) / (w1 / before + w2 / after)
    slopes[0] = _pchip_edge_slope(float(hs[0]), float(hs[1]), float(deltas[0]), float(deltas[1]))
    slopes[-1] = _pchip_edge_slope(float(hs[-1]), float(hs[-2]), float(deltas[-1]), float(deltas[-2]))
    return slopes


def pchip_opacity_segments(points):
    """Return cubic coefficients in normalized segment coordinates, split at steps."""
    values = _point_array(points)
    segments = [None] * (len(values) - 1)
    run_start = 0
    while run_start < len(values) - 1:
        run_end = run_start + 1
        while run_end < len(values) and values[run_end, 0] > values[run_end - 1, 0]:
            run_end += 1
        if run_end - run_start >= 2:
            xs = values[run_start:run_end, 0]
            ys = values[run_start:run_end, 1]
            slopes = _pchip_run_slopes(xs, ys)
            for local in range(run_end - run_start - 1):
                index = run_start + local
                h = float(xs[local + 1] - xs[local])
                segments[index] = _hermite_segment(
                    ys[local], ys[local + 1],
                    h * slopes[local], h * slopes[local + 1],
                )
        run_start = run_end
    return segments


def sanitized_opacity_segments(points, segments):
    values = _point_array(points)
    if not isinstance(segments, (list, tuple)) or len(segments) != len(values) - 1:
        return None
    result = []
    for index, entry in enumerate(segments):
        x0, y0 = values[index]
        x1, y1 = values[index + 1]
        if x1 <= x0:
            if entry is not None:
                return None
            result.append(None)
            continue
        try:
            coefficients = [float(value) for value in entry]
        except (TypeError, ValueError):
            return None
        if len(coefficients) != 4 or not all(math.isfinite(value) for value in coefficients):
            return None
        tolerance = 1.0e-9
        if (abs(coefficients[0] - y0) > tolerance
                or abs(sum(coefficients) - y1) > tolerance):
            return None
        result.append(coefficients)
    return result


def resolved_opacity_segments(points, interpolation=OPACITY_LINEAR, segments=None):
    interpolation = normalize_opacity_interpolation(interpolation)
    if interpolation == OPACITY_LINEAR:
        return linear_opacity_segments(points)
    saved = sanitized_opacity_segments(points, segments)
    return pchip_opacity_segments(points) if saved is None else saved


def evaluate_opacity_curve(values, points, *, interpolation=OPACITY_LINEAR, segments=None, xp=np):
    """Evaluate at actual point positions; at a repeated intensity the last point wins."""
    points = _point_array(points)
    interpolation = normalize_opacity_interpolation(interpolation)
    coefficients = resolved_opacity_segments(points, interpolation, segments)
    xs = xp.asarray(points[:, 0], dtype=xp.float64)
    ys = xp.asarray(points[:, 1], dtype=xp.float64)
    packed = np.zeros((len(points) - 1, 4), dtype=np.float64)
    for index, entry in enumerate(coefficients):
        if entry is not None:
            packed[index] = entry
    packed = xp.asarray(packed, dtype=xp.float64)
    samples = xp.asarray(values, dtype=xp.float64)
    right = xp.searchsorted(xs, samples, side="right")
    index = xp.clip(right - 1, 0, len(points) - 2)
    x0, x1 = xs[index], xs[index + 1]
    h = x1 - x0
    safe_h = xp.where(h > 0.0, h, 1.0)
    u = (samples - x0) / safe_h
    a, b, c, d = (packed[:, column][index] for column in range(4))
    result = a + u * (b + u * (c + u * d))
    result = xp.where(h > 0.0, result, ys[index])
    result = xp.where(right <= 0, ys[0], result)
    return xp.where(right >= len(points), ys[-1], result)


def insert_opacity_point(points, intensity, *, interpolation=OPACITY_LINEAR, segments=None):
    """Insert on the current curve and exactly split a saved smooth cubic segment."""
    values = _point_array(points)
    intensity = float(intensity)
    if (not math.isfinite(intensity)
            or np.any(np.isclose(values[:, 0], intensity, rtol=0.0, atol=1.0e-12))):
        raise ValueError("新增点的强度必须位于两个不同控制点之间。")
    index = next((i for i, x in enumerate(values[:, 0]) if x > intensity), len(values))
    if index <= 0 or index >= len(values):
        raise ValueError("新增点的强度必须位于曲线内部。")
    interpolation = normalize_opacity_interpolation(interpolation)
    alpha = float(evaluate_opacity_curve(
        intensity, values, interpolation=interpolation, segments=segments,
    ))
    updated = np.insert(values, index, [intensity, alpha], axis=0)
    if interpolation == OPACITY_LINEAR:
        return [tuple(point) for point in updated], None, index
    coefficients = resolved_opacity_segments(values, interpolation, segments)
    original = coefficients[index - 1]
    x0, x1 = values[index - 1:index + 1, 0]
    position = (intensity - x0) / (x1 - x0)
    a, b, c, d = original
    complement = 1.0 - position
    left = [a, b * position, c * position ** 2, d * position ** 3]
    right = [
        alpha,
        (b + 2.0 * c * position + 3.0 * d * position ** 2) * complement,
        (c + 3.0 * d * position) * complement ** 2,
        d * complement ** 3,
    ]
    updated_segments = coefficients[:index - 1] + [left, right] + coefficients[index:]
    return [tuple(point) for point in updated], updated_segments, index
