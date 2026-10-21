"""Page-owned display regions in canonical, absolute sample coordinates."""
from dataclasses import asdict, dataclass

import numpy as np

from bandscope.core.axis_mapping import interpolate_axis
from bandscope.core.crop_model import prepare_context, spatial_bounds


STATE_KEY = "local_enhancement"
DEFAULT_STYLE = {"levels": (0.0, 50.0, 100.0), "opacity_mode": "线性"}


@dataclass(frozen=True)
class EnhancementRegion:
    region_id: str
    name: str
    view: str
    axes: tuple
    bounds: tuple
    enabled: bool = True

    def __post_init__(self):
        count = {"2d": 2, "3d": 3}.get(self.view)
        if count is None or len(self.axes) != count or len(self.bounds) != count * 2:
            raise ValueError("增强区域与视图不匹配。")
        values = np.asarray(self.bounds, dtype=float)
        if not np.all(np.isfinite(values)) or np.any(values[::2] > values[1::2]):
            raise ValueError("范围必须为有限数值，且下限不能大于上限。")

    def to_dict(self):
        return asdict(self)


def geometry(context):
    context = prepare_context(context)
    if context["view"] == "3d":
        return ("X", "Y", "E"), spatial_bounds(context)
    axes = tuple(context["plot_axes"][p + "_key"] for p in ("x", "y"))
    rect = context["plot_logical_bounds"]
    return axes, tuple(rect[p + suffix] for p in ("x", "y") for suffix in ("_low", "_up"))


def matching_regions(context, regions):
    if context is None or context.get("view") not in {"2d", "3d"}:
        return []
    axes, _ = geometry(context)
    return [r for r in regions if r["view"] == context["view"] and tuple(r["axes"]) == axes
            and r.get("projection_visible", True)]


def defer_range_locks(state):
    """Keep lock intent, but measure ranges in the new scientific result's units."""
    for target, key, pending in [(state, "common_locked_range", "common_lock_pending")] + [
            (r, "locked_range", "lock_pending") for r in state.get("regions", [])]:
        if target.pop(key, None) is not None:
            target[pending] = True


def adapt_regions(context, state):
    """Project page-owned volume boxes onto a slice/integral without losing depth.

    Full boxes remain in absolute sample coordinates. Only boxes whose selected
    samples intersect the collapsed axis appear in the current 2D result.
    Returns whether display geometry changed (for cache invalidation).
    """
    axes, _ = geometry(context)
    changed = False
    for region in state.get("regions", []):
        volume = tuple(region["bounds"]) if region["view"] == "3d" else region.get("volume_bounds")
        if volume is None:
            continue
        if context["view"] == "3d":
            bounds, visible = tuple(volume), True
        else:
            info = context.get("slice_info", {})
            axis = int(info["axis"])
            low, high = info["range"] if info.get("mode") == "integral" else (info["index"], info["index"])
            a, b = volume[2*axis:2*axis+2]
            visible = np.ceil(b - 1e-10) >= low and np.floor(a + 1e-10) <= high
            pairs = dict(zip(("X", "Y", "E"), zip(volume[::2], volume[1::2])))
            bounds = tuple(v for key in axes for v in pairs[key])
        if region["view"] != context["view"]:
            # A summed intensity cannot reuse a range measured in the volume.
            if region.pop("locked_range", None) is not None:
                region["lock_pending"] = True
            if state.pop("common_locked_range", None) is not None:
                state["common_lock_pending"] = True
        updates = dict(view=context["view"], axes=axes, bounds=bounds,
                       volume_bounds=tuple(volume), projection_visible=bool(visible))
        if any(region.get(key) != value for key, value in updates.items()):
            region.update(updates)
            changed = True
    if changed:
        state["revision"] = state.get("revision", 0) + 1
    return changed


def update_region_bounds(region, bounds):
    """Editing a projection changes its two axes while retaining box depth."""
    region["bounds"] = tuple(bounds)
    if "volume_bounds" in region:
        volume = list(region["volume_bounds"])
        for i, key in enumerate(region["axes"]):
            j = 2 * ("X", "Y", "E").index(key)
            volume[j:j+2] = bounds[2*i:2*i+2]
        region["volume_bounds"] = tuple(volume)


def region_slices(context, region):
    """Intersect a draft with the current (possibly cropped) sample domain."""
    _, domain = geometry(context)
    result = []
    for i, size in enumerate(np.shape(context["data"])):
        low, high = region["bounds"][2*i:2*i+2]
        origin, end = domain[2*i:2*i+2]
        if high < origin or low > end:
            return None
        a = max(0, int(np.floor(low - origin + 1e-10)))
        b = min(size - 1, int(np.ceil(high - origin - 1e-10)))
        if a > b:
            return None
        result.append(slice(a, b + 1))
    return tuple(result)


def _display_axis(context, key, dimension):
    """逻辑下标轴与物理坐标轴的对应；E 翻转只是显示镜像，不参与换算。"""
    values = np.asarray(context["coords"][key], dtype=float)
    indices = np.arange(len(values), dtype=float)
    return values, indices


def physical_bounds(context, logical):
    axes, _ = geometry(context)
    result = []
    for i, key in enumerate(axes):
        values, indices = _display_axis(context, key, i)
        if indices[0] > indices[-1]:
            indices, values = indices[::-1], values[::-1]
        result.extend(sorted(interpolate_axis(logical[2*i:2*i+2], indices, values, extrapolate=True)))
    return tuple(float(v) for v in result)


def logical_bounds(context, physical):
    axes, _ = geometry(context)
    result = []
    for i, key in enumerate(axes):
        values, indices = _display_axis(context, key, i)
        if values[0] > values[-1]:
            values, indices = values[::-1], indices[::-1]
        result.extend(sorted(interpolate_axis(physical[2*i:2*i+2], values, indices, extrapolate=True)))
    return tuple(float(v) for v in result)
