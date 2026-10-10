"""1D 出图外观、稳定身份与会话状态；不修改任何科学数组。"""
from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np

PREFIX = "publication_export/1d_presentation/v1"


def _number(value, low, high, integer=False):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or not low <= number <= high:
        return None
    return int(number) if integer else number


def _color(value):
    return str(value).upper() if re.fullmatch(r"#[0-9a-fA-F]{6}", str(value)) else None


def _patch(raw, schema):
    if not isinstance(raw, Mapping):
        return {}
    result = {}
    for key, value in raw.items():
        rule = schema.get(key)
        if rule is None:
            continue
        if rule == "color":
            clean = _color(value)
        elif rule == "bool":
            clean = value if isinstance(value, bool) else None
        elif rule == "text":
            clean = str(value).replace("\n", " ").replace("\r", " ")[:160]
        elif rule == "range":
            clean = None
            if isinstance(value, (tuple, list)) and len(value) == 2:
                a, b = (_number(v, -1e100, 1e100) for v in value)
                if a is not None and b is not None and a < b:
                    clean = [a, b]
        elif isinstance(rule, tuple) and rule[0] in ("float", "int"):
            clean = _number(value, rule[1], rule[2], rule[0] == "int")
        else:
            clean = value if value in rule else None
        if clean is not None:
            result[key] = clean
    return result


CURVE_SCHEMA = {
    "color": "color", "linestyle": ("-", "--", "-.", ":", "none"),
    "linewidth": ("float", 0.1, 10), "alpha": ("float", 0.05, 1),
    "marker": ("", "o", "s", "^", "v", "D", "+", "x", "."),
    "markersize": ("float", 1, 20), "marker_filled": "bool",
    "markevery": ("int", 1, 10000), "visible": "bool", "legend": "bool",
    "label": "text",
}
FIGURE_SCHEMA = {
    "palette": ("legacy", "uniform", "categorical", "gray", "gradient"),
    "uniform_color": "color", "base_emphasis": "bool",
    "legend_position": ("template", "auto", "upper right", "upper left", "lower right",
                        "lower left", "center", "outside_top", "outside_bottom",
                        "outside_left", "outside_right", "none"),
    "legend_columns": ("int", 1, 12), "legend_frame": "bool",
    "legend_background": "color", "legend_alpha": ("float", 0, 1),
    "legend_spacing": ("float", 0, 5), "legend_handlelength": ("float", 0.5, 8),
    "legend_wrap": ("int", 8, 100),
    "xlim": "range", "ylim": "range", "x_reverse": "bool", "y_reverse": "bool",
    "x_ticks": ("int", 2, 15), "y_ticks": ("int", 2, 15),
    "x_minor": "bool", "y_minor": "bool", "x_ticklabels": "bool", "y_ticklabels": "bool",
    "tick_direction": ("in", "out", "inout"), "tick_color": "color",
    "tick_length": ("float", 0, 15), "tick_width": ("float", 0.1, 5),
    "major_grid": "bool", "minor_grid": "bool",
    "axis_label_size": ("float", 4, 32), "tick_label_size": ("float", 4, 32),
    "legend_size": ("float", 4, 32), "title_size": ("float", 4, 40),
    "panel_label_size": ("float", 4, 40), "axes_linewidth": ("float", 0.1, 5),
    "labelpad": ("float", 0, 30), "ink_color": "color", "canvas_color": "color",
    "margin_left_mm": ("float", 1, 100), "margin_right_mm": ("float", 1, 100),
    "margin_top_mm": ("float", 1, 100), "margin_bottom_mm": ("float", 1, 100),
    "waterfall_labels": ("all", "none", "every", "auto"),
    "waterfall_every": ("int", 1, 1000), "waterfall_label_size": ("float", 4, 32),
    "waterfall_label_rotation": ("float", -90, 90),
    "waterfall_label_gap": ("float", 0, 30), "waterfall_label_color": "color",
}
ANNOTATION_SCHEMA = {
    "kind": ("text", "arrow", "hline", "vline", "hspan", "vspan"),
    "text": "text", "coordinates": ("data", "axes fraction"),
    "x": ("float", -1e100, 1e100), "y": ("float", -1e100, 1e100),
    "x2": ("float", -1e100, 1e100), "y2": ("float", -1e100, 1e100),
    "color": "color", "alpha": ("float", 0.05, 1),
    "size": ("float", 4, 32), "linewidth": ("float", 0.1, 5),
    "linestyle": ("-", "--", "-.", ":"),
}


@dataclass
class Figure1DPresentation:
    defaults: dict = field(default_factory=dict)
    curves: dict = field(default_factory=dict)
    order: list = field(default_factory=list)
    legend_order: list = field(default_factory=list)
    figure: dict = field(default_factory=dict)
    annotations: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, Mapping):
            return cls()
        curve_map = raw.get("curves", {})
        curve_map = curve_map if isinstance(curve_map, Mapping) else {}
        def ids(key):
            values = raw.get(key, [])
            return list(dict.fromkeys(str(v) for v in values)) if isinstance(values, list) else []
        annotations = raw.get("annotations", [])
        return cls(
            defaults=_patch(raw.get("defaults"), {k: v for k, v in CURVE_SCHEMA.items()
                                                  if k not in ("label", "visible", "legend")}),
            curves={str(k): _patch(v, CURVE_SCHEMA) for k, v in curve_map.items()},
            order=ids("order"), legend_order=ids("legend_order"),
            figure=_patch(raw.get("figure"), FIGURE_SCHEMA),
            annotations=[_patch(v, ANNOTATION_SCHEMA) for v in annotations
                         if isinstance(v, Mapping) and v.get("kind") in ANNOTATION_SCHEMA["kind"]]
                        if isinstance(annotations, list) else [],
        )

    def to_dict(self):
        return copy.deepcopy(vars(self))

    def clone(self):
        return self.from_dict(self.to_dict())

    def signature(self):
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)

    def preset(self):
        figure = {k: v for k, v in self.figure.items() if k not in ("xlim", "ylim")}
        return {"defaults": copy.deepcopy(self.defaults), "figure": copy.deepcopy(figure)}

    def apply_preset(self, raw):
        preset = self.from_dict(raw)
        self.defaults = preset.defaults
        ranges = {k: v for k, v in self.figure.items() if k in ("xlim", "ylim")}
        self.figure = {**preset.figure, **ranges}


@dataclass(frozen=True)
class CurveIdentity:
    curve_id: str
    label: str
    source: str
    palette_slot: int
    is_base: bool
    x: Any
    y: Any
    offset: float = 0.0
    k_value: float | None = None


def snapshot_curves(snapshot):
    """读取冻结数组；身份与原始顺序不随绘制顺序改变。"""
    payload = snapshot.payload
    if snapshot.view == "waterfall":
        curves = np.asarray(payload["curves"])
        offsets = payload.get("curve_offsets", np.arange(len(curves)) * payload["offset_step"])
        identities = payload.get("curve_ids", [f"waterfall:{i}" for i in range(len(curves))])
        slots = payload.get("palette_slots", np.arange(len(curves)))
        return [CurveIdentity(str(identities[i]), f"{payload['k_values'][i]:.4g}",
                              snapshot.source_page_title, int(slots[i]), False,
                              np.asarray(curve) + offsets[i], payload["energy_axis"],
                              float(offsets[i]), float(payload["k_values"][i]))
                for i, curve in enumerate(curves)]
    curves = [payload["curve"]] if snapshot.view == "1d" else payload["curves"]
    return [CurveIdentity(str(c.get("curve_id") or ("main" if snapshot.view == "1d" else f"legacy:{i}")),
                          str(c.get("label") or (snapshot.source_page_title if snapshot.view == "1d"
                                                else f"Curve {i + 1}")),
                          str(c.get("source_title") or snapshot.source_page_title),
                          int(c.get("palette_slot", i)), bool(c.get("is_base", i == 0)),
                          c["x"], c["y"]) for i, c in enumerate(curves)]


def ordered_curves(snapshot, presentation):
    curves = snapshot_curves(snapshot)
    ranking = {key: i for i, key in enumerate(presentation.order)}
    return sorted(curves, key=lambda c: ranking.get(c.curve_id, len(ranking) + c.palette_slot))


def load_presentation(settings, style_id):
    try:
        raw = json.loads(settings.value(f"{PREFIX}/styles/{style_id}", "{}", type=str))
    except (TypeError, ValueError):
        raw = {}
    return Figure1DPresentation.from_dict(raw)


def save_presentation(settings, style_id, presentation):
    settings.setValue(f"{PREFIX}/styles/{style_id}", json.dumps(presentation.preset()))


def load_presets(settings):
    if settings is None:
        return {}
    try:
        raw = json.loads(settings.value(f"{PREFIX}/presets", "{}", type=str))
    except (TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_presets(settings, presets):
    settings.setValue(f"{PREFIX}/presets", json.dumps(presets, ensure_ascii=False))


def source_revision(window, spec):
    """只取值类型签名，不计算数据、不读取活动绘图对象。"""
    # QWidget-based test doubles may intentionally omit native initialization;
    # reading absent attributes through Qt's __getattr__ would raise RuntimeError.
    state = window.__dict__
    timeline = state.get("timeline_bar")
    flip = getattr(timeline, "switch_flip", None)
    time = getattr(timeline, "slider_time", None)
    return (state.get("shared_denoise_version", 0),
            bool(flip.isChecked()) if flip is not None else False,
            int(time.value()) if time is not None else None,
            repr(getattr(spec, "params", {})))


class PublicationSession:
    """仅本窗口会话内记忆；数据修订与页面归属分开。"""
    def __init__(self):
        self.pages = {}

    def forget_page(self, page_id):
        self.pages.pop(page_id, None)

    def clear(self):
        self.pages.clear()


def publication_session(window):
    session = window.__dict__.get("_publication_session")
    if session is None:
        session = PublicationSession()
        window.__dict__["_publication_session"] = session
    return session
