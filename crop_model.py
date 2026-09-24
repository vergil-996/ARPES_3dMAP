"""Coordinate-aware, non-destructive cropping shared by views and exports."""
from dataclasses import dataclass, asdict

import numpy as np


SPATIAL_KEYS = ("X", "Y", "E")
SPATIAL_LABELS = {"X": "kx", "Y": "ky", "E": "E"}
CROP_VIEWS = {"3d", "2d", "1d", "1d_comparison", "waterfall"}


@dataclass(frozen=True)
class CropSelection:
    view: str
    axes: tuple
    bounds: tuple
    e_flip: bool = False

    def __post_init__(self):
        count = 3 if self.view == "3d" else 2
        if self.view not in CROP_VIEWS or len(self.axes) != count or len(self.bounds) != count * 2:
            raise ValueError("裁剪范围与当前视图不匹配。")
        values = np.asarray(self.bounds, dtype=float)
        if not np.all(np.isfinite(values)):
            raise ValueError("裁剪上下限必须为有限数值。")
        if np.any(values[::2] > values[1::2]):
            raise ValueError("下限不能大于上限。")
        equal = values[::2] == values[1::2]
        if (count == 3 and np.count_nonzero(equal) > 1) or (count == 2 and np.any(equal)):
            raise ValueError("请选择有宽度的范围；3D 仅允许一个轴的上下限相等。")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(str(value["view"]), tuple(value["axes"]), tuple(value["bounds"]), bool(value.get("e_flip")))


def prepare_context(context):
    """Supply axis metadata for ordinary slices as well as integral results."""
    if context is None or context.get("view") not in CROP_VIEWS:
        return context
    result = dict(context)
    if result["view"] == "2d":
        axis = int(result["slice_info"]["axis"])
        keys = [key for i, key in enumerate(SPATIAL_KEYS) if i != axis]
        result.setdefault("plot_axes", {
            "x_key": keys[0], "y_key": keys[1],
            "x_label": SPATIAL_LABELS[keys[0]], "y_label": SPATIAL_LABELS[keys[1]],
        })
        shape = result["data"].shape
        result.setdefault("plot_logical_bounds", {"x_low": 0, "x_up": shape[0] - 1, "y_low": 0, "y_up": shape[1] - 1})
    return result


def spatial_bounds(context):
    shape = context.get("full_shape", context["data"].shape)
    return tuple(context.get("data_bounds") or [v for size in shape[:3] for v in (0, size - 1)])


def axis_values(context, dimension):
    if context["view"] == "3d":
        bounds = spatial_bounds(context)
        key = SPATIAL_KEYS[dimension]
        lo, hi = bounds[dimension * 2:dimension * 2 + 2]
    else:
        prefix = ("x", "y")[dimension]
        key = context["plot_axes"][prefix + "_key"]
        bounds = context["plot_logical_bounds"]
        lo, hi = bounds[prefix + "_low"], bounds[prefix + "_up"]
    return np.asarray(context["coords"][key], dtype=float)[int(lo):int(hi) + 1]


def _finite_range(values):
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if not finite.size:
        return None
    lo, hi = float(finite.min()), float(finite.max())
    if lo == hi:
        padding = max(abs(lo) * 0.01, 1e-6)
        return lo - padding, hi + padding
    return lo, hi


def selection_for_context(context, *, e_flip=False):
    context = prepare_context(context)
    if context is None or context.get("view") not in CROP_VIEWS or context.get("crop_empty"):
        return None
    view = context["view"]
    if view in {"3d", "2d"}:
        axes = SPATIAL_KEYS if view == "3d" else tuple(context["plot_axes"][p + "_key"] for p in ("x", "y"))
        ranges = []
        for i in range(len(axes)):
            values = axis_values(context, i)
            if not values.size:
                return None
            ranges.extend((float(np.min(values)), float(np.max(values))))
        # Single-pixel images still need a nonzero rectangle in plot coordinates.
        if view == "2d":
            for i in (0, 2):
                if ranges[i] == ranges[i + 1]:
                    ranges[i] -= 1e-6
                    ranges[i + 1] += 1e-6
    else:
        if view == "1d":
            xs, ys = context["x_data"], context["y_data"]
        elif view == "1d_comparison":
            curves = context.get("curves", [])
            if not curves:
                return None
            xs = np.concatenate([c["x_data"] for c in curves])
            ys = np.concatenate([c["y_data"] for c in curves])
        else:
            curves = np.asarray(context["curves"])
            xs = curves + waterfall_offsets(context)[:, None]
            ys = context["energy_axis"]
        xr, yr = _finite_range(xs), _finite_range(ys)
        if xr is None or yr is None:
            return None
        axes = (str(context.get("xlabel", "横轴")), str(context.get("ylabel", "Intensity (a.u.)")))
        ranges = [*xr, *yr]
    return CropSelection(view, tuple(axes), tuple(ranges), e_flip)


def axis_labels(selection):
    return tuple(SPATIAL_LABELS.get(key, key) for key in selection.axes)


def waterfall_offsets(context):
    return np.asarray(context.get("curve_offsets", np.arange(len(context["curves"])) * float(context.get("offset_step", 1.2))), dtype=float)


def _orient(context, flip):
    """Involution matching the main view's E orientation (without Qt state)."""
    if not flip:
        return context
    result = dict(context)
    view = context["view"]
    if view == "2d" and int(context["slice_info"]["axis"]) in (0, 1):
        result["data"] = np.flip(context["data"], axis=1)
    elif view == "1d" and "energy" in str(context.get("xlabel", "")).lower():
        result["y_data"] = np.flip(context["y_data"])
    elif view == "1d_comparison" and context.get("comparison_kind") in {"energy_dos", "edc_curve"}:
        result["curves"] = [dict(c, y_data=np.flip(c["y_data"])) for c in context["curves"]]
    elif view == "waterfall":
        for key in ("curves", "raw_curves"):
            if context.get(key) is not None:
                result[key] = np.flip(context[key], axis=1)
    return result


def _index_interval(values, low, high):
    """Inclusive, outward-rounded sample range, also for descending axes."""
    values = np.asarray(values, dtype=float)
    if not values.size or high < np.min(values) or low > np.max(values):
        return None
    indices = np.arange(values.size, dtype=float)
    if values[0] > values[-1]:
        values, indices = values[::-1], indices[::-1]
    a, b = np.interp([low, high], values, indices)
    if low == high:
        index = int(np.clip(round(a), 0, len(values) - 1))
        return index, index
    return max(0, int(np.floor(min(a, b) + 1e-10))), min(len(values) - 1, int(np.ceil(max(a, b) - 1e-10)))


def _empty(context):
    return dict(context, crop_empty=True)


def _crop_grid(context, selection):
    dims = 3 if context["view"] == "3d" else 2
    intervals = [_index_interval(axis_values(context, i), *selection.bounds[2*i:2*i+2]) for i in range(dims)]
    if any(value is None for value in intervals):
        return _empty(context)
    slices = tuple(slice(a, b + 1) for a, b in intervals)
    result = dict(context)
    data = np.asarray(context["data"])[slices]
    if not data.size or not np.any(np.isfinite(data)):
        return _empty(context)
    result.pop("compact_data", None)
    result.pop("compact_plot_logical_bounds", None)
    if dims == 3:
        old = spatial_bounds(context)
        bounds = tuple(v + int(old[2*i]) for i, pair in enumerate(intervals) for v in pair)
        result.update(data=data, data_bounds=bounds, full_shape=context.get("full_shape", context["data"].shape), clip_ranges=None)
        equal = [i for i in range(3) if selection.bounds[2*i] == selection.bounds[2*i+1]]
        if equal:
            axis = equal[0]
            keys = [key for i, key in enumerate(SPATIAL_KEYS) if i != axis]
            remaining = [i for i in range(3) if i != axis]
            rect = {p + suffix: bounds[2*i+j] for p, i in zip(("x", "y"), remaining) for j, suffix in enumerate(("_low", "_up"))}
            result.update(view="2d", data=np.take(data, 0, axis=axis),
                          slice_info={"axis": axis, "index": bounds[2*axis]},
                          plot_axes={"x_key": keys[0], "y_key": keys[1], "x_label": SPATIAL_LABELS[keys[0]], "y_label": SPATIAL_LABELS[keys[1]]},
                          plot_logical_bounds=rect)
            result.pop("data_bounds", None)
            result.pop("full_shape", None)
    else:
        old = context["plot_logical_bounds"]
        rect = {p + suffix: int(old[p + "_low"]) + intervals[i][j] for i, p in enumerate(("x", "y")) for j, suffix in enumerate(("_low", "_up"))}
        result.update(data=data, plot_logical_bounds=rect, crop_rect=rect)
    if result["view"] == "2d":
        x, y = axis_values(result, 0), axis_values(result, 1)
        result["slice_info"] = dict(result["slice_info"], extent_override=[float(x[0]), float(x[-1]), float(y[0]), float(y[-1])])
    return result


def _crop_curve(curve, bounds):
    x, y = np.asarray(curve["x_data"], dtype=float), np.asarray(curve["y_data"], dtype=float)
    xmask = np.isfinite(x) & (x >= bounds[0]) & (x <= bounds[1])
    x, y = x[xmask], y[xmask]
    valid = np.isfinite(y) & (y >= bounds[2]) & (y <= bounds[3])
    return dict(curve, x_data=x, y_data=np.where(valid, y, np.nan)), bool(np.any(valid))


def apply_selection(context, selection):
    """Apply in the coordinate orientation captured at selection time.

    Return canonical data so flipping the resulting page never applies the
    selection twice. NaNs represent removed curve segments, including exports.
    """
    context = prepare_context(context)
    if context.get("crop_empty"):
        return context
    if context["view"] != selection.view:
        return _empty(context)
    if selection.view == "2d" and tuple(context["plot_axes"][p + "_key"] for p in ("x", "y")) != selection.axes:
        return _empty(context)
    oriented = _orient(context, selection.e_flip)
    view = selection.view
    if view in {"3d", "2d"}:
        result = _crop_grid(oriented, selection)
    elif view == "1d":
        result, valid = _crop_curve(oriented, selection.bounds)
        result["crop_empty"] = not valid
    elif view == "1d_comparison":
        cropped = [_crop_curve(c, selection.bounds) for c in oriented["curves"]]
        result = dict(oriented, curves=[c for c, _ in cropped], crop_empty=not any(valid for _, valid in cropped))
    else:
        result = dict(oriented)
        energy = np.asarray(oriented["energy_axis"], dtype=float)
        curves = np.asarray(oriented["curves"], dtype=float)
        offsets = waterfall_offsets(oriented)
        xl, xh, yl, yh = selection.bounds
        emask = np.isfinite(energy) & (energy >= yl) & (energy <= yh)
        curves = curves[:, emask]
        display_x = curves + offsets[:, None]
        valid = np.isfinite(display_x) & (display_x >= xl) & (display_x <= xh)
        result.update(energy_axis=energy[emask], curves=np.where(valid, curves, np.nan), curve_offsets=offsets, crop_empty=not np.any(valid))
        if oriented.get("raw_curves") is not None:
            result["raw_curves"] = np.where(valid, np.asarray(oriented["raw_curves"])[:, emask], np.nan)
    if result["view"] == context["view"]:
        result = _orient(result, selection.e_flip)
    result["crop_bounds"] = selection.bounds
    return result


def apply_crop_regions(context, regions):
    result = prepare_context(context)
    for region in regions:
        result = apply_selection(result, CropSelection.from_dict(region))
    return result


def export_cropped_context(context, *, e_flip=False):
    """Numeric arrays only: never leak uncropped backing arrays into exports."""
    if context.get("crop_empty"):
        raise ValueError("当前裁剪范围内没有有效数据。")
    context = _orient(context, e_flip)
    view = context["view"]
    if view in {"3d", "2d"}:
        result = {"sample": np.asarray(context["data"], dtype=np.float32)}
        keys = SPATIAL_KEYS if view == "3d" else [context["plot_axes"][p + "_key"] for p in ("x", "y")]
        for i, key in enumerate(keys):
            result[{"X": "kx", "Y": "ky", "E": "E"}[key]] = axis_values(context, i).astype(np.float32)
    elif view == "waterfall":
        result = {"E": np.asarray(context["energy_axis"]), "k": np.asarray(context["k_values"]),
                  "intensity": np.asarray(context.get("raw_curves", context["curves"])),
                  "normalized_intensity": np.asarray(context["curves"]), "curve_offsets": waterfall_offsets(context)}
    elif view == "1d":
        key = "E" if "energy" in str(context.get("xlabel", "")).lower() else "time"
        result = {key: np.asarray(context["x_data"]), "intensity": np.asarray(context["y_data"])}
    else:
        raise ValueError("比较页暂不支持数据导出。")
    result["crop_range"] = np.asarray(context.get("crop_bounds", ()), dtype=float)
    return result
