"""Versioned page-display defaults and validation, independent of Qt and VTK."""
from __future__ import annotations

import copy

LEGACY_MODE = "legacy"
INDEPENDENT_MODE = "independent"
NORMALIZATION_FULL = "full"
NORMALIZATION_PERCENTILE = "percentile"
NORMALIZATION_MANUAL = "manual"
OPACITY_PRESET_WEAK = "weak"
OPACITY_PRESET_STRONG = "strong"
OPACITY_PRESET_CUSTOM = "custom"
OPACITY_PRESETS = {
    OPACITY_PRESET_WEAK: {"threshold": 0.0, "transition": 0.0, "gamma": 1.4, "density": 12.0},
    OPACITY_PRESET_STRONG: {"threshold": 21.739, "transition": 0.0, "gamma": 1.3, "density": 40.0},
}
DEFAULT_LEVELS = (0.0, 50.0, 100.0)
DEFAULT_OPACITY_MODE = "线性"


def legacy_display_style(levels=DEFAULT_LEVELS, opacity_mode=DEFAULT_OPACITY_MODE):
    return {"levels": tuple(float(value) for value in levels), "opacity_mode": str(opacity_mode)}


def new_display_style(levels=DEFAULT_LEVELS, opacity_mode=DEFAULT_OPACITY_MODE):
    return {
        "display_mode": INDEPENDENT_MODE,
        "levels": tuple(float(value) for value in levels),
        "opacity_mode": str(opacity_mode),
        "normalization": {
            "mode": NORMALIZATION_PERCENTILE,
            "percentiles": (0.0, 99.5),
            "manual_range": (0.0, 1.0),
        },
        "opacity": dict(OPACITY_PRESETS[OPACITY_PRESET_WEAK], preset=OPACITY_PRESET_CUSTOM),
    }


def is_independent(style):
    return isinstance(style, dict) and style.get("display_mode") == INDEPENDENT_MODE


def _finite_float(value, default):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if result == result and abs(result) != float("inf") else float(default)


def resolved_display_style(style):
    if not is_independent(style):
        if isinstance(style, dict):
            return legacy_display_style(
                style.get("levels", DEFAULT_LEVELS), style.get("opacity_mode", DEFAULT_OPACITY_MODE)
            )
        return legacy_display_style()
    levels = tuple(style.get("levels", DEFAULT_LEVELS))
    if len(levels) != 3:
        levels = DEFAULT_LEVELS
    levels = tuple(min(100.0, max(0.0, _finite_float(value, default)))
                   for value, default in zip(levels, DEFAULT_LEVELS))
    normalization = style.get("normalization") or {}
    mode = normalization.get("mode", NORMALIZATION_PERCENTILE)
    if mode not in {NORMALIZATION_FULL, NORMALIZATION_PERCENTILE, NORMALIZATION_MANUAL}:
        mode = NORMALIZATION_PERCENTILE
    percentiles = tuple(normalization.get("percentiles", (0.0, 99.5)))
    if len(percentiles) != 2:
        percentiles = (0.0, 99.5)
    low = min(100.0, max(0.0, _finite_float(percentiles[0], 0.0)))
    high = min(100.0, max(0.0, _finite_float(percentiles[1], 99.5)))
    if high < low:
        low, high = high, low
    manual = tuple(normalization.get("manual_range", (0.0, 1.0)))
    if len(manual) != 2:
        manual = (0.0, 1.0)
    manual = tuple(_finite_float(value, default) for value, default in zip(manual, (0.0, 1.0)))
    if manual[1] <= manual[0]:
        manual = (manual[0], manual[0] + 1.0)
    opacity = style.get("opacity") or {}
    values = {
        "threshold": min(100.0, max(0.0, _finite_float(opacity.get("threshold"), 0.0))),
        "transition": min(100.0, max(0.0, _finite_float(opacity.get("transition"), 0.0))),
        "gamma": min(4.0, max(0.1, _finite_float(opacity.get("gamma"), 1.4))),
        "density": min(100.0, max(0.0, _finite_float(opacity.get("density"), 12.0))),
    }
    preset = opacity.get("preset", OPACITY_PRESET_CUSTOM)
    if preset not in {OPACITY_PRESET_WEAK, OPACITY_PRESET_STRONG, OPACITY_PRESET_CUSTOM}:
        preset = OPACITY_PRESET_CUSTOM
    if preset != OPACITY_PRESET_CUSTOM:
        for name, candidate in ((OPACITY_PRESET_WEAK, OPACITY_PRESETS[OPACITY_PRESET_WEAK]),
                                (OPACITY_PRESET_STRONG, OPACITY_PRESETS[OPACITY_PRESET_STRONG])):
            if all(abs(values[key] - candidate[key]) < 1e-9 for key in candidate):
                preset = name
                break
        else:
            preset = OPACITY_PRESET_CUSTOM
    return {
        "display_mode": INDEPENDENT_MODE,
        "levels": levels,
        "opacity_mode": str(style.get("opacity_mode", DEFAULT_OPACITY_MODE)),
        "normalization": {
            "mode": mode,
            "percentiles": (low, high),
            "manual_range": manual,
        },
        "opacity": dict(values, preset=preset),
    }


def with_display_mode(style, mode):
    current = copy.deepcopy(style) if isinstance(style, dict) else legacy_display_style()
    if mode == INDEPENDENT_MODE:
        converted = new_display_style(current.get("levels", DEFAULT_LEVELS),
                                      current.get("opacity_mode", DEFAULT_OPACITY_MODE))
        if is_independent(current):
            return resolved_display_style(current)
        return converted
    return legacy_display_style(current.get("levels", DEFAULT_LEVELS),
                                current.get("opacity_mode", DEFAULT_OPACITY_MODE))


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


def migrate_display_state(state, *, legacy=True):
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
            canonical = resolved_display_style(style)
            style.clear()
            style.update(canonical)
    return result


def normalization_policy(style):
    if not is_independent(style):
        return ("legacy",)
    normalization = resolved_display_style(style)["normalization"]
    if normalization["mode"] == NORMALIZATION_MANUAL:
        return (NORMALIZATION_MANUAL, tuple(normalization["manual_range"]))
    if normalization["mode"] == NORMALIZATION_FULL:
        return (NORMALIZATION_FULL,)
    return (NORMALIZATION_PERCENTILE, tuple(normalization["percentiles"]))
