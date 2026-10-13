"""Resolve independent color and opacity channels without modifying science data."""
import numpy as np

from bandscope.core.local_enhancement import DEFAULT_STYLE, matching_regions, region_slices
from bandscope.processing.display_transfer import channels, level_info, normalized_color_samples, volume_unit_distance


def finite_range(data):
    values = np.asarray(data)
    values = values[np.isfinite(values)]
    return (float(values.min()), float(values.max())) if values.size else None


def resolve_display(context, state, *, background_locked_range=None, region_data=None):
    """Statistics always use original samples, including when ROIs overlap.

    Later enabled regions own overlapping samples. Background always retains
    the full source mapping; deleting a region restores that original mapping.
    Optional ROI data uses the same processing as a committed crop.
    """
    regions = matching_regions(context, state.get("regions", []))
    if not regions:
        return None
    source = np.asarray(context["data"])
    view = context["view"]
    include_zero = view == "3d" and bool(context.get("enhancement_include_zero", context.get("include_zero")))
    region_data = region_data or {}
    full_shape = context.get("full_shape", source.shape)
    base_distance = volume_unit_distance(source.shape, full_shape) if view == "3d" else 0.
    candidates = []
    for region in regions:
        slices = region_slices(context, region)
        values = region_data.get(region["region_id"], source[slices] if slices is not None else None)
        if slices is not None and values is not None and np.shape(values) != source[slices].shape:
            raise ValueError("增强区域的数据形状与裁剪范围不匹配。")
        measured = finite_range(values) if slices is not None and values is not None else None
        if measured is not None and include_zero:
            measured = (min(0., measured[0]), max(0., measured[1]))
        candidates.append((region, slices, measured, values))
    shared_range = None
    if state.get("common"):
        ranges = [r for region, _, r, _ in candidates if region.get("enabled", True) and r is not None]
        if ranges:
            shared_range = (min(r[0] for r in ranges), max(r[1] for r in ranges))
        shared_range = state.get("common_locked_range") or shared_range
    owners = np.zeros(source.shape, dtype=np.int32)
    resolved = []
    for region, slices, measured, values in candidates:
        data_range = shared_range if state.get("common") else region.get("locked_range") or measured
        status = "disabled" if not region.get("enabled", True) else "empty" if measured is None else "constant" if data_range is None or data_range[1] <= data_range[0] else "ready"
        style = state.get("common_style", DEFAULT_STYLE) if state.get("common") else region.get("style", DEFAULT_STYLE)
        record = dict(region, data_range=data_range, status=status,
                      level_info=level_info(data_range, style["levels"]), style=dict(style), slices=slices)
        if context.get("enhancement_errors", {}).get(region["region_id"]):
            record["error"] = context["enhancement_errors"][region["region_id"]]
        if view == "3d" and values is not None:
            record["opacity_unit_distance"] = volume_unit_distance(np.shape(values), full_shape)
        resolved.append(record)
        if status in {"ready", "constant"}:
            owners[slices] = len(resolved)
    bg_range = background_locked_range or finite_range(source)
    bg_style = state.get("background", DEFAULT_STYLE)
    bg_info = level_info(bg_range, bg_style["levels"], include_zero=background_locked_range is None and view == "3d" and bool(context.get("include_zero")))
    color, alpha = channels(source, bg_info, bg_style["opacity_mode"], view=view)
    color_rules = []
    if view == "3d":
        color = np.where(np.isfinite(source), normalized_color_samples(source, bg_info), 0)
        color_rules.append(dict(offset=0., level_info=bg_info))
    for index, region in enumerate(resolved, 1):
        if region["status"] not in {"ready", "constant"}:
            continue
        slices = region["slices"]
        values = candidates[index-1][3]
        c, a = channels(values, region["level_info"], region["style"]["opacity_mode"], view=view)
        if view == "3d" and base_distance > 0 and region.get("opacity_unit_distance", 0) > 0:
            # A single volume retains the original grid's opacity distance.
            # Rebase the ROI alpha so attenuation matches a separately cropped grid.
            a = (1. - np.power(1. - a.astype(np.float64), base_distance / region["opacity_unit_distance"])).astype(np.float32)
        if view == "3d":
            offset = float(2*index)
            c = np.where(np.isfinite(values), offset + normalized_color_samples(values, region["level_info"]), 0)
            color_rules.append(dict(offset=offset, level_info=region["level_info"]))
        mask = owners[slices] == index
        color[slices][mask] = c[mask]
        alpha[slices][mask] = a[mask]
    return {"color": color, "alpha": alpha, "background_level_info": bg_info,
            "regions": resolved, "common": bool(state.get("common")),
            "active": any(r["status"] in {"ready", "constant"} for r in resolved),
            "revision": int(state.get("revision", 0)),
            "owners": owners,
            "color_rules": color_rules,
            "region_data": {r["region_id"]: values for r, _, _, values in candidates if values is not None},
            "geometry_context": {k: v for k, v in context.items() if k != "data"}}


def orient_2d(values, slice_info, extent):
    image = np.asarray(values).T
    if slice_info.get("display_e_flip") and int(slice_info["axis"]) in (0, 1):
        image = image[::-1]
    if extent[0] > extent[1]:
        image = image[:, ::-1]
    if extent[2] > extent[3]:
        image = image[::-1]
    return image
