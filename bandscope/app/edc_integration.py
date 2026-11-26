"""Create frozen EDC curve pages from all selections on the current page."""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np

from bandscope.core.crop_model import CropSelection, SPATIAL_KEYS, apply_selection
from bandscope.core.data_scope import (
    DataScopeDescriptor,
    ScopedSpatialArray,
    scoped_descriptor_from_array,
)
from bandscope.processing.edc import integrate_edc, selection_index_bounds
from bandscope.ui.result_workspace import AnalysisPageSpec


EDC_SOURCE_KEY = "edc_source"


@dataclass(frozen=True)
class _EdcSource:
    data: Any
    coords: dict
    full_shape: tuple[int, int, int]
    time_spec: Any
    view: str
    data_bounds: tuple[int, int, int, int, int, int]
    axis_index: int | None
    normal_index_bounds: tuple[int, int] | None


def _full_shape_from_coords(coords) -> tuple[int, int, int]:
    return tuple(int(np.asarray(coords[key]).size) for key in ("X", "Y", "E"))


def _spatial_source(context, coords, raw_data) -> tuple[Any, tuple[int, int, int], tuple]:
    source_data = context.get("data")
    descriptor = scoped_descriptor_from_array(source_data)
    data_bounds = context.get("data_bounds")
    if descriptor is None and data_bounds is not None:
        full_shape = context.get("full_shape")
        if full_shape is None:
            raw_shape = getattr(raw_data, "shape", ())
            full_shape = raw_shape[:3] if len(raw_shape) >= 3 else _full_shape_from_coords(coords)
        full_shape = tuple(int(size) for size in full_shape)
        delay = coords.get("delay")
        time_size = int(np.asarray(delay).size) if delay is not None else 1
        descriptor = DataScopeDescriptor(
            "edc-source",
            full_shape + (max(time_size, 1),),
            tuple(int(value) for value in data_bounds),
        )
        source_data = ScopedSpatialArray(np.asarray(source_data), descriptor)
    if descriptor is not None:
        return source_data, descriptor.full_shape[:3], descriptor.spatial_bounds

    data = np.asarray(source_data)
    if data.ndim != 3:
        raise ValueError("EDC source must be a three-dimensional spatial array.")
    full_shape = tuple(int(size) for size in data.shape)
    return data, full_shape, tuple(value for size in full_shape for value in (0, size - 1))


def _selection_snapshot(value) -> CropSelection:
    if isinstance(value, CropSelection):
        return copy.deepcopy(value)
    if isinstance(value, dict):
        return CropSelection.from_dict(copy.deepcopy(value))
    raise ValueError("Invalid EDC selection snapshot.")


def _nonzero_range(low, up) -> tuple[float, float]:
    low, up = float(low), float(up)
    if low != up:
        return low, up
    padding = max(abs(low) * 1e-9, 1e-9)
    return low - padding, up + padding


def _full_energy_range(coords) -> tuple[float, float]:
    energy = np.asarray(coords["E"], dtype=np.float64)
    if energy.ndim != 1 or energy.size == 0 or not np.all(np.isfinite(energy)):
        raise ValueError("EDC source energy coordinates are invalid.")
    return _nonzero_range(np.min(energy), np.max(energy))


def _normal_axis_key(axis_keys) -> str:
    keys = tuple(axis_keys)
    if (
        len(keys) != 2
        or len(set(keys)) != 2
        or not set(keys).issubset(SPATIAL_KEYS)
    ):
        raise ValueError("An EDC axis crop requires two distinct spatial axes.")
    return next(key for key in SPATIAL_KEYS if key not in keys)


def _axis_volume_selection(
    selection: CropSelection, coords, normal_index_bounds
) -> CropSelection:
    if selection.view != "2d":
        raise ValueError("An EDC axis crop requires a 2D selection.")
    normal_key = _normal_axis_key(selection.axes)
    ranges = {
        key: selection.bounds[2 * index:2 * index + 2]
        for index, key in enumerate(selection.axes)
    }
    if normal_key == "E":
        normal_range = _full_energy_range(coords)
    else:
        if normal_index_bounds is None:
            raise ValueError("An XZ/YZ EDC selection requires a normal-axis range.")
        normal_range = _index_coordinate_range(
            coords, normal_key, *normal_index_bounds
        )
    ranges[normal_key] = normal_range
    return CropSelection(
        "3d",
        SPATIAL_KEYS,
        tuple(value for key in SPATIAL_KEYS for value in ranges[key]),
        e_flip=selection.e_flip,
        operation=selection.operation,
    )


def _index_coordinate_range(coords, key, low, up) -> tuple[float, float]:
    values = np.asarray(coords[key], dtype=np.float64)
    if values.ndim != 1 or values.size == 0:
        raise ValueError("EDC source coordinates are unavailable.")
    low, up = sorted((int(low), int(up)))
    if up < 0 or low >= values.size:
        raise ValueError("An EDC axis crop does not intersect the source coordinates.")
    low, up = max(low, 0), min(up, values.size - 1)
    physical_low, physical_up = sorted((float(values[low]), float(values[up])))
    return physical_low, physical_up


def _full_coordinate_range(coords, key) -> tuple[float, float]:
    values = np.asarray(coords[key], dtype=np.float64)
    if values.ndim != 1 or values.size == 0 or not np.all(np.isfinite(values)):
        raise ValueError("EDC source coordinates are invalid.")
    return _nonzero_range(np.min(values), np.max(values))


def _legacy_volume_selections(
    rect, coords, plot_axes, normal_index_bounds
) -> list[CropSelection]:
    x_key, y_key = str(plot_axes["x_key"]), str(plot_axes["y_key"])
    normal_key = _normal_axis_key((x_key, y_key))
    ranges = {
        x_key: _index_coordinate_range(
            coords, x_key, rect["x_low"], rect["x_up"]
        ),
        y_key: _index_coordinate_range(
            coords, y_key, rect["y_low"], rect["y_up"]
        ),
    }
    if normal_key == "E":
        normal_range = _full_energy_range(coords)
    else:
        if normal_index_bounds is None:
            raise ValueError("An XZ/YZ EDC selection requires a normal-axis range.")
        normal_range = _index_coordinate_range(
            coords, normal_key, *normal_index_bounds
        )

    def selection(plot_ranges):
        volume_ranges = dict(plot_ranges)
        volume_ranges[normal_key] = normal_range
        return CropSelection(
            "3d",
            SPATIAL_KEYS,
            tuple(value for key in SPATIAL_KEYS for value in volume_ranges[key]),
        )

    if all(low == up for low, up in ranges.values()):
        first = dict(ranges)
        first[y_key] = _full_coordinate_range(coords, y_key)
        second = dict(ranges)
        second[x_key] = _full_coordinate_range(coords, x_key)
        return [selection(first), selection(second)]
    return [selection(ranges)]


def _restore_volume_crop(context, full_shape):
    if context.get("view") != "2d":
        return context
    slice_info = context.get("slice_info") or {}
    collapsed_axis = int(slice_info["axis"])
    collapsed_index = int(slice_info["index"])
    bounds = [0] * 6
    bounds[2 * collapsed_axis:2 * collapsed_axis + 2] = [
        collapsed_index,
        collapsed_index,
    ]
    axis_by_key = {"X": 0, "Y": 1, "E": 2}
    rect = context["plot_logical_bounds"]
    for prefix in ("x", "y"):
        axis = axis_by_key[context["plot_axes"][prefix + "_key"]]
        bounds[2 * axis] = int(rect[prefix + "_low"])
        bounds[2 * axis + 1] = int(rect[prefix + "_up"])
    restored = dict(
        context,
        view="3d",
        data=np.expand_dims(np.asarray(context["data"]), axis=collapsed_axis),
        full_shape=full_shape,
        data_bounds=tuple(bounds),
    )
    for key in ("slice_info", "plot_axes", "plot_logical_bounds", "crop_rect"):
        restored.pop(key, None)
    return restored


def _legacy_crop_rectangle(params):
    required = ("crop_k_low", "crop_k_up", "crop_e_low", "crop_e_up")
    if any(key not in params for key in required):
        return None
    return {
        "x_low": params["crop_k_low"],
        "x_up": params["crop_k_up"],
        "y_low": params["crop_e_low"],
        "y_up": params["crop_e_up"],
    }


def _replay_axis_source_crops(
    spec, source_context, coords, raw_data, plot_axes, normal_index_bounds
):
    data, full_shape, data_bounds = _spatial_source(source_context, coords, raw_data)
    replayed = dict(
        source_context,
        view="3d",
        data=data,
        coords=coords,
        full_shape=full_shape,
        data_bounds=data_bounds,
    )
    regions = list(spec.params.get("crop_regions") or ())
    base_rect = None
    if spec.page_kind == "axis_integral_crop":
        base_rect = (
            spec.params.get("crop_base_rect")
            if regions
            else _legacy_crop_rectangle(spec.params)
        )
    selections = []
    if base_rect is not None:
        selections.extend(
            _legacy_volume_selections(
                base_rect, coords, plot_axes, normal_index_bounds
            )
        )
    selections.extend(
        _axis_volume_selection(
            _selection_snapshot(region), coords, normal_index_bounds
        )
        for region in regions
    )
    for selection in selections:
        replayed = apply_selection(replayed, selection)
        if replayed.get("crop_empty"):
            raise ValueError("An EDC axis crop leaves no three-dimensional source data.")
        replayed = _restore_volume_crop(replayed, full_shape)
    return _spatial_source(replayed, coords, raw_data)


def _time_metadata(host, spec, coords) -> dict:
    delay_values = coords.get("delay")
    if delay_values is None:
        return {}
    delay = np.asarray(delay_values, dtype=np.float64)
    if delay.ndim != 1 or delay.size == 0:
        return {}
    low, up = (int(value) for value in host._time_indices_for_spec(spec))
    if not (0 <= low < delay.size and 0 <= up < delay.size):
        raise ValueError("EDC time interval is outside the source coordinates.")
    metadata = {
        "source_t_index": low,
        "source_t_value": float(delay[low]),
        "source_t_indices": (low, up),
        "source_t_range": (float(delay[low]), float(delay[up])),
    }
    interval = (spec.params or {}).get("time_interval")
    if interval is not None:
        metadata["time_interval"] = copy.deepcopy(interval)
    return metadata


class EdcIntegrationMixin:
    def _create_edc_result_from_selections(self):
        current_spec = self.left_workspace.current_spec()
        controller = self.__dict__.get("crop_controller")
        if current_spec is None or controller is None:
            return None

        controller.commit_edits()
        snapshots = tuple(controller.selections_for_page(current_spec.page_id))
        context = self.__dict__.get("current_render_context")
        if not snapshots or context is None or context.get("crop_empty"):
            return self._edc_selection_creation_failed()

        try:
            source = self._prepare_edc_selection_source(current_spec, context)
            source_scope_metadata = self._edc_source_scope_metadata(current_spec, source)
            source_rotation_angle = float(self.__dict__.get("rotation_angle", 0.0) or 0.0)
            selections = [
                (str(selection_id), _selection_snapshot(selection))
                for selection_id, selection in snapshots
            ]
            if source.view == "3d":
                if current_spec.page_kind != "home" or any(
                    selection.view != "3d" for _selection_id, selection in selections
                ):
                    raise ValueError("A 3D EDC source requires 3D selections on a home page.")
            else:
                expected_axes = set(SPATIAL_KEYS) - {SPATIAL_KEYS[source.axis_index]}
                if current_spec.page_kind not in {"axis_integral", "axis_integral_crop"} or any(
                    selection.view != "2d" or set(selection.axes) != expected_axes
                    for _selection_id, selection in selections
                ):
                    raise ValueError("A 2D EDC source requires selections matching its integral page axes.")

            time_metadata = _time_metadata(self, source.time_spec, source.coords)
            integrated = []
            for selection_id, selection in selections:
                index_bounds = selection_index_bounds(
                    selection,
                    source.coords,
                    source.full_shape,
                    normal_index_bounds=source.normal_index_bounds,
                )
                if index_bounds is None:
                    raise ValueError("An EDC selection does not intersect the source coordinates.")
                curve = integrate_edc(source.data, source.coords["E"], index_bounds)
                if curve is None or curve.energy.size == 0:
                    raise ValueError("An EDC selection does not intersect the prepared source ROI.")
                if np.all(np.isnan(curve.intensity)):
                    raise ValueError("An EDC selection contains no valid intensity samples.")
                integrated.append((selection_id, selection, curve))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return self._edc_selection_creation_failed()

        page_id = str(self._make_page_id())
        curves = []
        curve_metadata = []
        for index, (selection_id, selection, curve) in enumerate(integrated):
            curve_id = uuid.uuid4().hex
            metadata = self._edc_curve_metadata(
                curve_id=curve_id,
                selection_id=selection_id,
                selection=selection,
                curve=curve,
                coords=source.coords,
                time_metadata=time_metadata,
                selection_index=index + 1,
            )
            title = (
                f"EDC #{index + 1}"
                if len(integrated) > 1
                else self._edc_selection_title(
                    index + 1,
                    metadata,
                    include_energy=source.view == "3d" or source.axis_index in (0, 1),
                )
            )
            curves.append(
                {
                    "curve_kind": "edc_curve",
                    "curve_id": curve_id,
                    "title": title,
                    "source_title": str(current_spec.title),
                    "label": title,
                    "xlabel": "Energy (eV)",
                    "x_data": np.asarray(curve.energy, dtype=np.float64).tolist(),
                    "y_data": np.asarray(curve.intensity, dtype=np.float64).tolist(),
                    "palette_slot": index,
                    "is_base": index == 0,
                }
            )
            curve_metadata.append(metadata)

        page_kind = "edc_curve" if len(curves) == 1 else "curve_comparison_1d"
        title = curves[0]["title"] if len(curves) == 1 else f"EDC Curves ({len(curves)} selections)"
        params = {
            "comparison_kind": "edc_curve",
            "x_match_policy": "per_curve",
            "base_curve": curves[0],
            "overlay_curves": curves[1:],
            "curve_metadata": curve_metadata,
            EDC_SOURCE_KEY: self._freeze_edc_source(
                current_spec, context, source_rotation_angle
            ),
            "source_page_id": str(current_spec.page_id),
            "source_page_kind": str(current_spec.page_kind),
            "source_view": source.view,
            "source_full_shape": tuple(source.full_shape),
            "source_data_bounds": tuple(source.data_bounds),
            "source_scope_metadata": source_scope_metadata,
            "source_rotation_angle": source_rotation_angle,
        }
        if time_metadata.get("time_interval") is not None:
            params["time_interval"] = copy.deepcopy(time_metadata["time_interval"])
        spec = AnalysisPageSpec(
            page_id=page_id,
            title=self._make_unique_page_title(title),
            page_kind=page_kind,
            source_module="data_process",
            source_page_id=current_spec.page_id,
            source_title=str(current_spec.title),
            data_scope_id=str(getattr(current_spec, "data_scope_id", "full")),
            params=params,
        )
        self._seed_control_state_for_spec(spec)
        self.left_workspace.add_page(spec)
        return page_id

    def _freeze_edc_source(self, spec, context, rotation_angle):
        source_params = copy.deepcopy(spec.params or {})
        if spec.page_kind == "home":
            source_params["home_slice_info"] = copy.deepcopy(
                (spec.params or {}).get("home_slice_info")
            )
            if "clip_ranges" not in source_params:
                source_params["clip_ranges"] = copy.deepcopy(context.get("clip_ranges"))
        elif spec.page_kind in {"axis_integral", "axis_integral_crop"}:
            resolved = self._resolved_axis_integral_params(spec)
            if resolved is not None:
                source_params.update(copy.deepcopy(resolved))
        return {
            "page_kind": str(spec.page_kind),
            "params": source_params,
            "data_scope_id": str(getattr(spec, "data_scope_id", "full")),
            "rotation_angle": float(rotation_angle),
        }

    def _edc_time_recomputable(self, spec):
        if spec is None:
            return False
        params = getattr(spec, "params", None) or {}
        page_kind = getattr(spec, "page_kind", None)
        if page_kind == "edc_curve":
            pass
        elif page_kind == "curve_comparison_1d":
            if params.get("comparison_kind") != "edc_curve":
                return False
        elif page_kind == "log_curve":
            if (
                params.get("comparison_kind") != "edc_curve"
                or params.get("log_base") != "log10"
            ):
                return False
        else:
            return False

        source = params.get(EDC_SOURCE_KEY)
        if not isinstance(source, dict) or source.get("page_kind") not in {
            "home", "axis_integral", "axis_integral_crop"
        }:
            return False
        if not isinstance(source.get("params"), dict):
            return False
        try:
            if not np.isfinite(float(source.get("rotation_angle"))):
                return False
        except (TypeError, ValueError, OverflowError):
            return False

        curves = [params.get("base_curve"), *(params.get("overlay_curves") or ())]
        metadata = params.get("curve_metadata")
        if (
            not curves
            or any(not isinstance(curve, dict) for curve in curves)
            or not isinstance(metadata, (list, tuple))
            or len(metadata) != len(curves)
        ):
            return False
        metadata_by_id = {
            str(item.get("curve_id")): item
            for item in metadata
            if isinstance(item, dict)
        }
        curve_ids = set()
        for curve in curves:
            curve_id = str(curve.get("curve_id") or "")
            item = metadata_by_id.get(curve_id)
            if (
                curve.get("recipe_id")
                or not curve_id
                or curve_id in curve_ids
                or item is None
                or not isinstance(item.get("selection"), dict)
            ):
                return False
            try:
                if len(tuple(item.get("index_bounds"))) != 6:
                    return False
            except TypeError:
                return False
            curve_ids.add(curve_id)
        return True

    def _edc_source_spec_for_time(self, spec):
        source = spec.params[EDC_SOURCE_KEY]
        params = copy.deepcopy(source["params"])
        time_interval = (spec.params or {}).get("time_interval")
        if time_interval is not None:
            params["time_interval"] = copy.deepcopy(time_interval)
        return AnalysisPageSpec(
            page_id="__edc_time_source__",
            title="EDC source",
            page_kind=str(source["page_kind"]),
            source_module="edc",
            params=params,
            data_scope_id=str(source.get("data_scope_id") or "full"),
        )

    def _recompute_edc_time_curves(self, spec):
        if not self._edc_time_recomputable(spec):
            return None
        previous_rotation = float(self.__dict__.get("rotation_angle", 0.0) or 0.0)
        try:
            source_state = spec.params[EDC_SOURCE_KEY]
            self.rotation_angle = float(source_state["rotation_angle"])
            source_spec = self._edc_source_spec_for_time(spec)
            if source_spec.page_kind == "home":
                source_context = self._compute_render_context(source_spec)
            else:
                resolved = self._resolved_axis_integral_params(source_spec)
                axis_info = self._axis_plot_info(int(resolved["axis_index"]))
                source_context = {
                    "view": "2d",
                    "plot_axes": {
                        "x_key": axis_info["x_key"],
                        "y_key": axis_info["y_key"],
                    },
                }
            if source_context is None or source_context.get("crop_empty"):
                return None
            source = self._prepare_edc_selection_source(source_spec, source_context)
            time_metadata = _time_metadata(self, spec, source.coords)
            metadata_by_id = {
                str(item.get("curve_id")): item
                for item in spec.params.get("curve_metadata", ())
            }
            saved_curves = [
                spec.params["base_curve"],
                *(spec.params.get("overlay_curves") or ()),
            ]
            curves = []
            curve_metadata = []
            for saved in saved_curves:
                metadata = metadata_by_id[str(saved.get("curve_id"))]
                curve = integrate_edc(
                    source.data,
                    source.coords["E"],
                    tuple(int(value) for value in metadata["index_bounds"]),
                )
                if curve is None or curve.energy.size == 0:
                    return None
                if not np.array_equal(
                    curve.energy,
                    np.asarray(saved.get("x_data"), dtype=np.float64),
                ):
                    return None
                updated = copy.deepcopy(saved)
                updated["x_data"] = np.asarray(
                    curve.energy, dtype=np.float64
                ).tolist()
                updated["y_data"] = np.asarray(
                    curve.intensity, dtype=np.float64
                ).tolist()
                updated_metadata = copy.deepcopy(metadata)
                updated_metadata["index_bounds"] = tuple(
                    int(value) for value in curve.index_bounds
                )
                updated_metadata.update(copy.deepcopy(time_metadata))
                curves.append(updated)
                curve_metadata.append(updated_metadata)
            return {"curves": curves, "curve_metadata": curve_metadata}
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return None
        finally:
            self.rotation_angle = previous_rotation

    def _prepare_edc_selection_source(self, spec, context) -> _EdcSource:
        if spec.page_kind == "home" and context.get("view") == "3d":
            raw_data, display_coords = self._get_display_state_for_spec(spec)
            coords = context.get("coords") or display_coords
            if coords is None:
                raise ValueError("EDC source coordinates are unavailable.")
            data, full_shape, data_bounds = _spatial_source(context, coords, raw_data)
            return _EdcSource(
                data, coords, full_shape, spec, "3d", data_bounds, None, None
            )

        if spec.page_kind not in {"axis_integral", "axis_integral_crop"} or context.get("view") != "2d":
            raise ValueError("EDC requires a 3D home page or a two-axis integral page.")
        resolved = self._resolved_axis_integral_params(spec)
        axis_index = -1 if resolved is None else int(resolved.get("axis_index", -1))
        if axis_index not in (0, 1, 2):
            raise ValueError("EDC requires an X, Y, or E axis integral page.")
        plot_axes = context.get("plot_axes") or {}
        expected_axes = set(SPATIAL_KEYS) - {SPATIAL_KEYS[axis_index]}
        if {plot_axes.get("x_key"), plot_axes.get("y_key")} != expected_axes:
            raise ValueError("EDC plot axes do not match the integral page axis.")

        axis_params = copy.deepcopy(spec.params or {})
        axis_params.update(copy.deepcopy(resolved))
        source_spec = self._build_axis_source_spec_from_params(axis_params)
        if source_spec is None:
            raise ValueError("EDC axis source could not be restored.")
        raw_data, coords = self._get_display_state_for_spec(spec)
        if raw_data is None or coords is None:
            raise ValueError("EDC source data is unavailable.")
        source_context = self._get_3d_source_context_for_axis(source_spec, raw_data)
        if source_context is None or source_context.get("view") != "3d":
            raise ValueError("EDC axis source is not three-dimensional.")
        full_shape = _full_shape_from_coords(coords)
        normal_index_bounds = (
            (0, full_shape[2] - 1)
            if axis_index == 2
            else tuple(sorted((int(resolved["low"]), int(resolved["up"]))))
        )
        data, full_shape, data_bounds = _replay_axis_source_crops(
            spec,
            source_context,
            coords,
            raw_data,
            plot_axes,
            normal_index_bounds,
        )
        return _EdcSource(
            data,
            coords,
            full_shape,
            source_spec,
            "2d",
            data_bounds,
            axis_index,
            normal_index_bounds,
        )

    def _edc_source_scope_metadata(self, spec, source) -> dict:
        descriptor = scoped_descriptor_from_array(source.data)
        scope_for_spec = getattr(self, "_scope_for_spec", None)
        if callable(scope_for_spec):
            scoped = scope_for_spec(spec)
            if scoped is not None:
                descriptor = scoped
        export_metadata = getattr(self, "_scope_export_metadata", None)
        if callable(export_metadata):
            return copy.deepcopy(export_metadata(descriptor, source.coords))
        if descriptor is None or descriptor.is_full:
            return {
                "data_scope_id": np.asarray(["full"]),
                "data_scope_label": np.asarray(["完整数据"]),
            }
        return {
            "data_scope_id": np.asarray([descriptor.scope_id]),
            "data_scope_label": np.asarray([descriptor.label]),
            "roi_index_bounds": np.asarray(descriptor.spatial_bounds, dtype=np.int32),
            "roi_physical_bounds": descriptor.physical_bounds(source.coords),
        }

    @staticmethod
    def _edc_curve_metadata(
        *, curve_id, selection_id, selection, curve, coords, time_metadata, selection_index
    ) -> dict:
        x_low, x_up, y_low, y_up, e_low, e_up = curve.index_bounds
        metadata = {
            "curve_id": curve_id,
            "selection_id": selection_id,
            "selection_index": int(selection_index),
            "selection": selection.to_dict(),
            "operation": selection.operation,
            "index_bounds": tuple(int(value) for value in curve.index_bounds),
            "crop_rect": {
                "x_low": int(x_low),
                "x_up": int(x_up),
                "y_low": int(y_low),
                "y_up": int(y_up),
            },
            "kx_range": (float(coords["X"][x_low]), float(coords["X"][x_up])),
            "ky_range": (float(coords["Y"][y_low]), float(coords["Y"][y_up])),
            "energy_range": (float(coords["E"][e_low]), float(coords["E"][e_up])),
        }
        metadata.update(copy.deepcopy(time_metadata))
        return metadata

    def _edc_selection_title(self, index, metadata, *, include_energy) -> str:
        kx_low, kx_up = (self._edc_number_text(value) for value in metadata["kx_range"])
        ky_low, ky_up = (self._edc_number_text(value) for value in metadata["ky_range"])
        title = f"EDC #{index} [kx {kx_low}~{kx_up}, ky {ky_low}~{ky_up}"
        if include_energy:
            e_low, e_up = (self._edc_number_text(value) for value in metadata["energy_range"])
            title += f", E {e_low}~{e_up}"
        return title + "]"

    def _edc_number_text(self, value) -> str:
        formatter = getattr(self, "_format_filename_number", None)
        return str(formatter(value)) if formatter is not None else f"{float(value):g}"

    def _edc_selection_creation_failed(self):
        show_message = getattr(self, "_show_message", None)
        if show_message is not None:
            show_message(
                "无法创建 EDC",
                "请在三维主页或任意轴积分页面建立有效选区，并确保所有选区与数据相交。",
            )
        return None
