"""Coordinate-aware, non-destructive cropping shared by views and exports."""
from dataclasses import dataclass, asdict, replace

import numpy as np

from bandscope.core.axis_mapping import interpolate_axis
from bandscope.core.rotation_geometry import creation_xy_coordinates, xy_canvas_mask


SPATIAL_KEYS = ("X", "Y", "E")
SPATIAL_LABELS = {"X": "kx", "Y": "ky", "E": "E"}
CROP_VIEWS = {"3d", "2d", "1d", "1d_comparison", "waterfall"}


@dataclass(frozen=True)
class CropSelection:
    view: str
    axes: tuple
    bounds: tuple
    e_flip: bool = False
    operation: str = "crop"
    rotation_angle: float | None = None
    logical_bounds: tuple | None = None

    def __post_init__(self):
        if self.operation not in {"crop", "erase"}:
            raise ValueError("未知的选区操作。")
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
        if self.rotation_angle is not None:
            angle = float(self.rotation_angle)
            if not np.isfinite(angle):
                raise ValueError("选区创建角度必须为有限数值。")
            object.__setattr__(self, "rotation_angle", angle)
        if self.logical_bounds is not None:
            if len(self.logical_bounds) != count * 2:
                raise ValueError("选区逻辑边界与当前视图不匹配。")
            logical = tuple(int(value) for value in self.logical_bounds)
            if any(value != original for value, original in zip(logical, self.logical_bounds)):
                raise ValueError("选区逻辑边界必须为整数索引。")
            if any(value < 0 for value in logical) or np.any(np.asarray(logical)[::2] > np.asarray(logical)[1::2]):
                raise ValueError("选区逻辑边界必须为有序非负索引。")
            object.__setattr__(self, "logical_bounds", logical)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(
            str(value["view"]), tuple(value["axes"]), tuple(value["bounds"]),
            bool(value.get("e_flip")), str(value.get("operation", "crop")),
            value.get("rotation_angle"), value.get("logical_bounds"),
        )


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


def reorient_volume_selection(context, selection, e_flip):
    """Keep absolute voxel positions when a saved 3D draft changes orientation.

    3D 的 E 翻转由相机镜像整个场景完成，坐标与体素都不动，因此草稿只需要
    更新记录下来的显示方向。
    """
    return replace(selection, e_flip=e_flip)


def waterfall_offsets(context):
    return np.asarray(context.get("curve_offsets", np.arange(len(context["curves"])) * float(context.get("offset_step", 1.2))), dtype=float)


def index_interval(values, low, high):
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


_index_interval = index_interval


def _full_spatial_shape(shape) -> tuple[int, int, int]:
    values = tuple(shape)
    if len(values) != 3:
        raise ValueError("空间选区需要完整的 X/Y/E 三维形状。")
    result = tuple(int(size) for size in values)
    if any(size <= 0 or size != original for size, original in zip(result, values)):
        raise ValueError("空间选区需要三个正整数维度。")
    return result


def _selection_axis_bounds(selection, coords, shape, normal_index_bounds=None):
    if selection.view == "3d":
        if tuple(selection.axes) != SPATIAL_KEYS:
            raise ValueError("三维空间选区必须按 X、Y、E 轴给出范围。")
        normal_key = None
    elif selection.view == "2d":
        axes = tuple(selection.axes)
        if len(set(axes)) != 2 or not set(axes).issubset(SPATIAL_KEYS):
            raise ValueError("二维空间选区必须包含两个不同的 X、Y、E 轴。")
        normal_key = next(key for key in SPATIAL_KEYS if key not in axes)
    else:
        raise ValueError("网格掩膜仅支持二维或三维空间选区。")

    ranges = {}
    for index, key in enumerate(selection.axes):
        if selection.logical_bounds is None:
            values = np.asarray(coords[key], dtype=float)
            if values.ndim != 1 or values.size != shape[SPATIAL_KEYS.index(key)]:
                raise ValueError(f"{key} 轴坐标与完整空间网格不匹配。")
            interval = index_interval(values, *selection.bounds[2 * index:2 * index + 2])
        else:
            interval = tuple(selection.logical_bounds[2 * index:2 * index + 2])
            if interval[1] >= shape[SPATIAL_KEYS.index(key)]:
                raise ValueError("选区逻辑边界超出完整空间网格。")
        if interval is None:
            return None, normal_key
        ranges[key] = tuple(int(value) for value in interval)
    if normal_key is not None:
        if normal_index_bounds is None:
            if normal_key != "E":
                raise ValueError("XZ/YZ 选区需要法向 inclusive 索引范围。")
            normal = (0, shape[2] - 1)
        else:
            values = tuple(normal_index_bounds)
            if len(values) != 2:
                raise ValueError("选区法向范围必须为两个 inclusive 索引。")
            normal = tuple(int(value) for value in values)
            if any(value != original for value, original in zip(normal, values)):
                raise ValueError("选区法向范围必须为整数索引。")
            normal = tuple(sorted(normal))
        if normal[0] < 0 or normal[1] >= shape[SPATIAL_KEYS.index(normal_key)]:
            raise ValueError("选区法向范围超出完整空间网格。")
        ranges[normal_key] = normal
    return ranges, normal_key


def selection_grid_mask(
    selection: CropSelection,
    coords,
    spatial_shape,
    *,
    rotation_angle=None,
    normal_index_bounds=None,
) -> np.ndarray:
    """Return a full [X, Y, E] mask on the current exact rotation grid.

    A missing selection angle keeps the legacy rectangular interpretation.
    Otherwise the current samples are mapped analytically to the creation grid;
    no intensity array is rotated or resampled by this function.
    """
    shape = _full_spatial_shape(spatial_shape)
    ranges, _normal_key = _selection_axis_bounds(
        selection, coords, shape, normal_index_bounds
    )
    if ranges is None:
        return np.zeros(shape, dtype=bool)
    current = (selection.rotation_angle if rotation_angle is None
               else float(rotation_angle or 0.0))
    creation = selection.rotation_angle
    rotated = (creation is not None and "X" in ranges and "Y" in ranges
               and abs(float(current or 0.0) - creation) >= 1e-12)
    mask = np.ones(shape, dtype=bool)
    for axis, key in enumerate(SPATIAL_KEYS):
        if rotated and key in {"X", "Y"}:
            continue
        low, up = ranges[key]
        axis_mask = np.zeros(shape[axis], dtype=bool)
        axis_mask[low:up + 1] = True
        reshape = [1, 1, 1]
        reshape[axis] = shape[axis]
        mask &= axis_mask.reshape(reshape)
    if rotated:
        x, y = creation_xy_coordinates(shape[:2], creation, current)
        tolerance = 1e-9
        xlow, xup = ranges["X"]
        ylow, yup = ranges["Y"]
        xy = ((x >= xlow - tolerance) & (x <= xup + tolerance)
              & (y >= ylow - tolerance) & (y <= yup + tolerance))
        mask &= xy[:, :, None]
    return mask


def _context_full_shape(context) -> tuple[int, int, int]:
    if context.get("full_shape") is not None:
        return _full_spatial_shape(tuple(context["full_shape"])[:3])
    coords = context["coords"]
    return _full_spatial_shape(tuple(np.asarray(coords[key]).size for key in SPATIAL_KEYS))


def _context_normal_bounds(context):
    info = context.get("slice_info") or {}
    axis = int(info["axis"])
    if axis not in (0, 1, 2):
        raise ValueError("二维切片的法向轴必须为 X、Y 或 E。")
    if "range" in info:
        low, up = info["range"]
    elif "index" in info:
        low = up = info["index"]
    else:
        low, up = 0, _context_full_shape(context)[axis] - 1
    low, up = sorted((int(low), int(up)))
    return axis, low, up


def _current_rotation_angle(context, rotation_angle=None, fallback=0.0) -> float:
    value = (context.get("rotation_angle", fallback) if rotation_angle is None
             else rotation_angle)
    result = float(value or 0.0)
    if not np.isfinite(result):
        raise ValueError("当前旋转角度必须为有限数值。")
    return result


def capture_selection_geometry(context, selection: CropSelection, *, rotation_angle=None) -> CropSelection:
    """Freeze creation angle and absolute logical ranges for a spatial operation."""
    context = prepare_context(context)
    angle = _current_rotation_angle(context, rotation_angle, selection.rotation_angle)
    if selection.view not in {"3d", "2d"}:
        return replace(selection, rotation_angle=angle)
    info = context.get("slice_info") or {}
    if (selection.view == "2d" and selection.rotation_angle is None
            and abs(angle) < 1e-12 and "range" not in info and "index" not in info):
        return selection
    shape = _context_full_shape(context)
    normal = _context_normal_bounds(context)[1:] if selection.view == "2d" else None
    ranges, _normal_key = _selection_axis_bounds(selection, context["coords"], shape, normal)
    logical = None
    if ranges is not None:
        logical = tuple(value for key in selection.axes for value in ranges[key])
    return replace(selection, rotation_angle=angle, logical_bounds=logical)


def _localize_full_grid(context, values):
    view = context["view"]
    source = np.asarray(values)
    if view == "3d":
        bounds = spatial_bounds(context)
        slices = tuple(slice(bounds[2 * axis], bounds[2 * axis + 1] + 1) for axis in range(3))
        result = source[slices]
    else:
        normal_axis, normal_low, _normal_up = _context_normal_bounds(context)
        rect = context["plot_logical_bounds"]
        slices = [slice(None)] * 3
        for prefix in ("x", "y"):
            key = context["plot_axes"][prefix + "_key"]
            axis = SPATIAL_KEYS.index(key)
            slices[axis] = slice(int(rect[prefix + "_low"]), int(rect[prefix + "_up"]) + 1)
        slices[normal_axis] = slice(normal_low, normal_low + 1)
        result = np.take(source[tuple(slices)], 0, axis=normal_axis)
    if result.shape != np.asarray(context["data"]).shape:
        raise ValueError("紧凑空间数组与其完整网格逻辑边界不匹配。")
    return result


def _context_selection_mask(context, selection, rotation_angle):
    normal = _context_normal_bounds(context)[1:] if selection.view == "2d" else None
    full = selection_grid_mask(
        selection, context["coords"], _context_full_shape(context),
        rotation_angle=rotation_angle, normal_index_bounds=normal,
    )
    return _localize_full_grid(context, full)


def _context_canvas_mask(context, rotation_angle):
    existing = context.get("canvas_mask")
    if existing is not None:
        result = np.asarray(existing, dtype=bool)
        if result.shape != np.asarray(context["data"]).shape:
            raise ValueError("画布掩膜与空间数组形状不匹配。")
        return result
    shape = _context_full_shape(context)
    source_bounds = context.get("source_data_bounds")
    if source_bounds is None:
        xy_bounds = None
        energy_bounds = (0, shape[2] - 1)
    else:
        if len(source_bounds) != 6:
            raise ValueError("来源数据域必须包含六个 inclusive 索引。")
        xy_bounds = tuple(int(value) for value in source_bounds[:4])
        energy_bounds = tuple(int(value) for value in source_bounds[4:])
    full = np.broadcast_to(
        xy_canvas_mask(shape[:2], rotation_angle, source_bounds=xy_bounds)[:, :, None],
        shape,
    ).copy()
    if energy_bounds != (0, shape[2] - 1):
        energy = np.zeros(shape[2], dtype=bool)
        energy[energy_bounds[0]:energy_bounds[1] + 1] = True
        full &= energy[None, None, :]
    return _localize_full_grid(context, full)


def _context_valid_mask(context):
    existing = context.get("spatial_valid_mask")
    if existing is None:
        return np.ones(np.asarray(context["data"]).shape, dtype=bool)
    result = np.asarray(existing, dtype=bool)
    if result.shape != np.asarray(context["data"]).shape:
        raise ValueError("选区有效掩膜与空间数组形状不匹配。")
    return result


def _has_valid_samples(data, valid_mask=None, canvas_mask=None) -> bool:
    valid = np.isfinite(data)
    if valid_mask is not None:
        valid &= valid_mask
    if canvas_mask is not None:
        valid &= canvas_mask
    return bool(np.any(valid))


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
    metadata = {
        key: np.asarray(context[key], dtype=bool)[slices]
        for key in ("spatial_valid_mask", "canvas_mask") if key in context
    }
    if not data.size or not _has_valid_samples(
        data, metadata.get("spatial_valid_mask"), metadata.get("canvas_mask")
    ):
        return _empty(context)
    result.pop("compact_data", None)
    result.pop("compact_plot_logical_bounds", None)
    if dims == 3:
        old = spatial_bounds(context)
        bounds = tuple(v + int(old[2*i]) for i, pair in enumerate(intervals) for v in pair)
        result.update(data=data, data_bounds=bounds, full_shape=context.get("full_shape", context["data"].shape), clip_ranges=None)
        result.update(metadata)
        equal = [i for i in range(3) if selection.bounds[2*i] == selection.bounds[2*i+1]]
        if equal:
            axis = equal[0]
            keys = [key for i, key in enumerate(SPATIAL_KEYS) if i != axis]
            remaining = [i for i in range(3) if i != axis]
            rect = {p + suffix: bounds[2*i+j] for p, i in zip(("x", "y"), remaining) for j, suffix in enumerate(("_low", "_up"))}
            result.update(view="2d", data=np.take(data, 0, axis=axis),
                          **{key: np.take(value, 0, axis=axis) for key, value in metadata.items()},
                          slice_info={"axis": axis, "index": bounds[2*axis]},
                          plot_axes={"x_key": keys[0], "y_key": keys[1], "x_label": SPATIAL_LABELS[keys[0]], "y_label": SPATIAL_LABELS[keys[1]]},
                          plot_logical_bounds=rect)
            result.pop("data_bounds", None)
            result.pop("full_shape", None)
    else:
        old = context["plot_logical_bounds"]
        rect = {p + suffix: int(old[p + "_low"]) + intervals[i][j] for i, p in enumerate(("x", "y")) for j, suffix in enumerate(("_low", "_up"))}
        result.update(data=data, plot_logical_bounds=rect, crop_rect=rect, **metadata)
    if result["view"] == "2d":
        x, y = axis_values(result, 0), axis_values(result, 1)
        result["slice_info"] = dict(result["slice_info"], extent_override=[float(x[0]), float(x[-1]), float(y[0]), float(y[-1])])
    return result


def _mask_bounding_slices(mask):
    indices = np.where(np.asarray(mask, dtype=bool))
    if not indices[0].size:
        return None
    return tuple(slice(int(values.min()), int(values.max()) + 1) for values in indices)


def _apply_rotated_spatial_selection(context, selection, rotation_angle):
    selected = _context_selection_mask(context, selection, rotation_angle)
    result = dict(context)
    source = np.asarray(context["data"])
    valid = _context_valid_mask(context)
    canvas = _context_canvas_mask(context, rotation_angle)
    result.pop("compact_data", None)
    result.pop("compact_plot_logical_bounds", None)

    if selection.operation == "erase":
        if not np.any(selected):
            return result
        data = np.array(source, dtype=np.result_type(source.dtype, np.float32), copy=True)
        data[selected] = np.nan
        valid = valid & ~selected
        result.update(data=data, spatial_valid_mask=valid, canvas_mask=canvas)
        result["crop_empty"] = not _has_valid_samples(data, valid, canvas)
        return result

    slices = _mask_bounding_slices(selected)
    if slices is None:
        return _empty(context)
    intervals = tuple((part.start, part.stop - 1) for part in slices)
    selected = selected[slices]
    valid = valid[slices] & selected
    canvas = canvas[slices]
    data = np.where(selected, source[slices], 0)
    if not _has_valid_samples(data, valid, canvas):
        return _empty(context)

    if context["view"] == "3d":
        old = spatial_bounds(context)
        bounds = tuple(v + int(old[2 * i]) for i, pair in enumerate(intervals) for v in pair)
        result.update(
            data=data, data_bounds=bounds,
            full_shape=_context_full_shape(context), clip_ranges=None,
            spatial_valid_mask=valid, canvas_mask=canvas,
        )
        equal = [i for i in range(3) if selection.bounds[2 * i] == selection.bounds[2 * i + 1]]
        angle_changed = abs(rotation_angle - float(selection.rotation_angle)) >= 1e-12
        collapsible = [i for i in equal if i == 2 or not angle_changed]
        if collapsible:
            axis = collapsible[0]
            keys = [key for i, key in enumerate(SPATIAL_KEYS) if i != axis]
            remaining = [i for i in range(3) if i != axis]
            rect = {
                prefix + suffix: bounds[2 * i + j]
                for prefix, i in zip(("x", "y"), remaining)
                for j, suffix in enumerate(("_low", "_up"))
            }
            result.update(
                view="2d", data=np.take(data, 0, axis=axis),
                spatial_valid_mask=np.take(valid, 0, axis=axis),
                canvas_mask=np.take(canvas, 0, axis=axis),
                slice_info={"axis": axis, "index": bounds[2 * axis]},
                plot_axes={
                    "x_key": keys[0], "y_key": keys[1],
                    "x_label": SPATIAL_LABELS[keys[0]], "y_label": SPATIAL_LABELS[keys[1]],
                },
                plot_logical_bounds=rect,
            )
            result.pop("data_bounds", None)
            result.pop("full_shape", None)
    else:
        old = context["plot_logical_bounds"]
        rect = {
            prefix + suffix: int(old[prefix + "_low"]) + intervals[i][j]
            for i, prefix in enumerate(("x", "y"))
            for j, suffix in enumerate(("_low", "_up"))
        }
        result.update(
            data=data, plot_logical_bounds=rect, crop_rect=rect,
            spatial_valid_mask=valid, canvas_mask=canvas,
        )
    result["crop_empty"] = False
    if result["view"] == "2d":
        x, y = axis_values(result, 0), axis_values(result, 1)
        result["slice_info"] = dict(
            result["slice_info"],
            extent_override=[float(x[0]), float(x[-1]), float(y[0]), float(y[-1])],
        )
    return result


def _crop_curve(curve, bounds):
    x, y = np.asarray(curve["x_data"], dtype=float), np.asarray(curve["y_data"], dtype=float)
    xmask = np.isfinite(x) & (x >= bounds[0]) & (x <= bounds[1])
    x, y = x[xmask], y[xmask]
    valid = np.isfinite(y) & (y >= bounds[2]) & (y <= bounds[3])
    return dict(curve, x_data=x, y_data=np.where(valid, y, np.nan)), bool(np.any(valid))


def _erase_selection(context, selection):
    """Mask selected samples without changing the domain or backing arrays."""
    result = dict(context)
    view = context["view"]
    if view in {"3d", "2d"}:
        dims = 3 if view == "3d" else 2
        intervals = [_index_interval(axis_values(context, i), *selection.bounds[2*i:2*i+2])
                     for i in range(dims)]
        if any(interval is None for interval in intervals):
            return result
        source = np.asarray(context["data"])
        data = np.array(source, dtype=np.result_type(source.dtype, np.float32), copy=True)
        slices = tuple(slice(a, b + 1) for a, b in intervals)
        data[slices] = np.nan
        valid = context.get("spatial_valid_mask")
        if valid is not None:
            valid = np.array(valid, dtype=bool, copy=True)
            valid[slices] = False
            result["spatial_valid_mask"] = valid
        canvas = context.get("canvas_mask")
        result.update(data=data, crop_empty=not _has_valid_samples(data, valid, canvas))
        result.pop("compact_data", None)
        result.pop("compact_plot_logical_bounds", None)
    elif view in {"1d", "1d_comparison"}:
        def erase_curve(curve):
            x, y = np.asarray(curve["x_data"]), np.asarray(curve["y_data"])
            xl, xh, yl, yh = selection.bounds
            selected = (x >= xl) & (x <= xh) & (y >= yl) & (y <= yh)
            masked = np.where(selected, np.nan, y)
            return dict(curve, y_data=masked), bool(np.any(np.isfinite(x) & np.isfinite(masked)))

        if view == "1d":
            result, valid = erase_curve(context)
        else:
            curves = [erase_curve(c) for c in context["curves"]]
            result["curves"] = [c for c, _ in curves]
            valid = any(valid for _, valid in curves)
        result["crop_empty"] = not valid
    else:
        energy = np.asarray(context["energy_axis"])
        curves = np.asarray(context["curves"])
        display_x = curves + waterfall_offsets(context)[:, None]
        xl, xh, yl, yh = selection.bounds
        selected = ((display_x >= xl) & (display_x <= xh)
                    & (energy[None, :] >= yl) & (energy[None, :] <= yh))
        masked = np.where(selected, np.nan, curves)
        result.update(curves=masked, crop_empty=not np.any(np.isfinite(masked)))
        if context.get("raw_curves") is not None:
            result["raw_curves"] = np.where(selected, np.nan, context["raw_curves"])
    return result


def apply_selection(context, selection, *, rotation_angle=None):
    """Apply one selection without resampling the current intensity grid.

    E 翻转只是显示镜像，坐标与数据始终是原始排列，选区因此直接落在原始体素上。
    NaNs represent removed curve segments, including exports. New spatial
    selections replay their creation geometry; legacy selections keep the old path.
    """
    context = prepare_context(context)
    if context.get("crop_empty"):
        return context
    if context["view"] != selection.view:
        return _empty(context)
    if selection.view == "2d" and tuple(context["plot_axes"][p + "_key"] for p in ("x", "y")) != selection.axes:
        return _empty(context)
    view = selection.view
    current_angle = _current_rotation_angle(context, rotation_angle, selection.rotation_angle)
    if selection.rotation_angle is not None and view in {"3d", "2d"}:
        result = _apply_rotated_spatial_selection(context, selection, current_angle)
    elif selection.operation == "erase":
        result = _erase_selection(context, selection)
    elif view in {"3d", "2d"}:
        result = _crop_grid(context, selection)
    elif view == "1d":
        result, valid = _crop_curve(context, selection.bounds)
        result["crop_empty"] = not valid
    elif view == "1d_comparison":
        cropped = [_crop_curve(c, selection.bounds) for c in context["curves"]]
        result = dict(context, curves=[c for c, _ in cropped], crop_empty=not any(valid for _, valid in cropped))
    else:
        result = dict(context)
        energy = np.asarray(context["energy_axis"], dtype=float)
        curves = np.asarray(context["curves"], dtype=float)
        offsets = waterfall_offsets(context)
        xl, xh, yl, yh = selection.bounds
        emask = np.isfinite(energy) & (energy >= yl) & (energy <= yh)
        curves = curves[:, emask]
        display_x = curves + offsets[:, None]
        valid = np.isfinite(display_x) & (display_x >= xl) & (display_x <= xh)
        result.update(energy_axis=energy[emask], curves=np.where(valid, curves, np.nan), curve_offsets=offsets, crop_empty=not np.any(valid))
        if context.get("raw_curves") is not None:
            result["raw_curves"] = np.where(valid, np.asarray(context["raw_curves"])[:, emask], np.nan)
    if view == "3d":
        # Also restore canonical coordinates when a volume becomes a slice.
        result["coords"] = context["coords"]
        if result["view"] == "2d":
            x, y = axis_values(result, 0), axis_values(result, 1)
            result["slice_info"] = dict(result["slice_info"], extent_override=[float(x[0]), float(x[-1]), float(y[0]), float(y[-1])])
    result["erase_bounds" if selection.operation == "erase" else "crop_bounds"] = selection.bounds
    return result


def apply_crop_regions(context, regions, *, rotation_angle=None):
    """Replay operations in order on one exact grid at ``rotation_angle``."""
    result = prepare_context(context)
    selections = [region if isinstance(region, CropSelection) else CropSelection.from_dict(region)
                  for region in regions]
    fallback = next((selection.rotation_angle for selection in selections
                     if selection.rotation_angle is not None), 0.0)
    current_angle = _current_rotation_angle(result, rotation_angle, fallback)
    for selection in selections:
        result = apply_selection(result, selection, rotation_angle=current_angle)
    return result


def export_cropped_context(context):
    """Numeric arrays only: never leak uncropped backing arrays into exports."""
    if context.get("crop_empty"):
        raise ValueError("当前裁剪范围内没有有效数据。")
    view = context["view"]
    if view in {"3d", "2d"}:
        result = {"sample": np.asarray(context["data"], dtype=np.float32)}
        keys = SPATIAL_KEYS if view == "3d" else [context["plot_axes"][p + "_key"] for p in ("x", "y")]
        for i, key in enumerate(keys):
            result[{"X": "kx", "Y": "ky", "E": "E"}[key]] = axis_values(context, i).astype(np.float32)
        for key in ("spatial_valid_mask", "canvas_mask"):
            if key in context:
                result[key] = np.asarray(context[key], dtype=bool)
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
    if "erase_bounds" in context:
        result["erase_range"] = np.asarray(context["erase_bounds"], dtype=float)
    return result
