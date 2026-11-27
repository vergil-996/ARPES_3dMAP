"""Page-owned physical scan intervals with time-compatible internal field names."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from bandscope.core.axis_interval import AxisInterval, AxisSpace
from bandscope.core.axis_units import resolve_axis_unit
from bandscope.core.data_scope import ScopedDataVolume, scoped_descriptor_from_array
from bandscope.rendering.refresh_pipeline import RefreshCause, RenderQuality
from bandscope.ui.axis_interval_controller import IntervalEditMode


class TimeIntervalMixin:
    TIME_INTERVAL_PAGE_KINDS = frozenset({
        "home",
        "axis_integral",
        "axis_integral_crop",
        "energy_dos",
        "edc_curve",
        "curve_comparison_1d",
        "log_curve",
        "second_derivative",
        "path_slice",
        "plugin_derived",
    })

    def _time_space_for_data(self):
        coords = (getattr(self.core, "coords", None) or {}).get("delay")
        if coords is None and getattr(self.core, "raw_data", None) is not None:
            coords = np.arange(int(self.core.raw_data.shape[3]), dtype=np.float64)
        assignment = getattr(self.core, "axis_assignment", None)
        is_time = assignment.is_time if assignment is not None else True
        scan_name = assignment.display_scan_name if assignment is not None else "时间"
        return AxisSpace(
            "delay",
            np.asarray(coords if coords is not None else [0.0], dtype=np.float64),
            label=scan_name,
            unit=resolve_axis_unit(
                "delay" if is_time else "scan",
                (getattr(self.core, "coord_units", None) or {}).get("delay"),
            ),
            source=(getattr(self.core, "coord_sources", None) or {}).get("delay", "index"),
        )

    def _time_page_editable(self, spec):
        if spec is None or not self._has_time_axis():
            return False
        if spec.page_kind not in self.TIME_INTERVAL_PAGE_KINDS:
            return False
        if spec.page_kind in {"curve_comparison_1d", "log_curve"}:
            return self._edc_time_recomputable(spec)
        if spec.page_kind == "edc_curve" and spec.params.get("base_curve") is not None:
            return self._edc_time_recomputable(spec)
        if spec.page_kind == self.PLUGIN_DERIVED_PAGE_KIND:
            session = self._plugin_session()
            record = None
            if session is not None:
                record = session.manager.record(str(spec.params.get("derived_plugin_id") or ""))
            if record is None or getattr(record, "instance", None) is None:
                return False
        return True

    def _legacy_time_interval(self, spec, space):
        params = (spec.params if spec is not None else None) or {}
        state = params.get("time_interval")
        if isinstance(state, dict) and state.get("axis_key") == "delay":
            interval = AxisInterval.from_dict(state)
            if interval is not None:
                interval.set_bounds(space.minimum, space.maximum)
                return interval

        control_state = params.get("control_state") or {}
        data_state = control_state.get("data_process") or {}
        image_state = control_state.get("image") or {}
        integrated = (
            spec.page_kind == "time_integral"
            or params.get("source_mode") == "time_integral"
            or params.get("source_page_kind") == "time_integral"
            or control_state.get("axis_source_mode") == "time_integral"
            or ("t_low" in params and "t_up" in params)
        )
        if integrated:
            low = params.get("t_low", params.get("source_t_low"))
            up = params.get("t_up", params.get("source_t_up"))
            low = (data_state.get("s_t_low") or {}).get("value", 0) if low is None else low
            up = (data_state.get("s_t_up") or {}).get("value", low) if up is None else up
        else:
            low = params.get("source_t_index", params.get("t_index"))
            if low is None:
                low = (image_state.get("slider_time") or {}).get("value", 0)
            up = low
        interval = AxisInterval.from_indices(
            space.coords, int(low or 0), int(up if up is not None else low or 0),
            locked=False, axis_key="delay",
        )
        if interval is not None:
            interval.set_bounds(space.minimum, space.maximum)
            if interval.length == 0.0:
                interval.set_locked(True)
            return interval

        source = None
        workspace = self.__dict__.get("left_workspace")
        source_id = getattr(spec, "source_page_id", None)
        if workspace is not None and source_id:
            source = workspace.page_by_id(source_id)
        source_state = (source.params if source is not None else {}).get("time_interval")
        if isinstance(source_state, dict):
            interval = AxisInterval.from_dict(source_state)
            if interval is not None:
                interval.set_bounds(space.minimum, space.maximum)
                return interval
        initial = float(space.coords[0])
        return space.as_interval(low=initial, up=initial, locked=True)

    def _time_interval_for_spec(self, spec):
        if spec is None or not self._has_time_axis():
            return None
        return self._legacy_time_interval(spec, self._time_space_for_data())

    def _time_indices_for_spec(self, spec):
        interval = self._time_interval_for_spec(spec)
        if interval is None:
            return 0, 0
        return interval.to_indices(self._time_space_for_data().coords)

    def _time_indices_for_params(self, params, *, page_kind=""):
        spec = SimpleNamespace(
            page_kind=page_kind,
            source_page_id=None,
            params=params or {},
        )
        return self._time_indices_for_spec(spec)

    def _time_interval_params_for_spec(self, spec):
        interval = self._time_interval_for_spec(spec)
        return {"time_interval": interval.as_dict()} if interval is not None else {}

    @staticmethod
    def _time_range_data(raw_data, low, up):
        if raw_data.ndim == 3:
            return raw_data
        if isinstance(raw_data, ScopedDataVolume):
            return raw_data.frame(low) if low == up else raw_data.time_integral(low, up)
        if low == up:
            return raw_data[..., int(low)]
        return np.sum(raw_data[..., int(low):int(up) + 1], axis=3, dtype=np.float32)

    def _get_rotated_time_range(self, raw_data, low, up, *, angle=None):
        shape = tuple(raw_data.shape)
        maximum = max(int(shape[3]) - 1, 0) if len(shape) > 3 else 0
        low = int(np.clip(int(low), 0, maximum))
        up = int(np.clip(int(up), 0, maximum))
        if low > up:
            low, up = up, low
        if angle is None:
            angle = self.rotation_angle
        angle = float(angle or 0.0)
        key = (
            "time_range",
            self._raw_data_identity(raw_data),
            low,
            up,
            round(angle, 2),
        )
        computed_cache = self.__dict__.get("_computed_volume_cache")
        cached = computed_cache.get(key) if computed_cache is not None else None
        if cached is not None:
            return self._restore_scoped_spatial(raw_data, cached, angle)

        data_3d = self._time_range_data(raw_data, low, up)
        if abs(angle) < 1e-6:
            return self._restore_scoped_spatial(raw_data, data_3d, angle)

        cached = self._rotation_cache_get(key)
        if cached is not None:
            return self._restore_scoped_spatial(raw_data, cached, angle)
        if isinstance(raw_data, ScopedDataVolume):
            data_3d = raw_data.materialize_3d(data_3d)
        rotated = self.backend_manager.cpu.rotate_volume(data_3d, angle)
        self._rotation_cache_store(key, rotated)
        return rotated

    def _rotation_angle_for_spec(self, spec):
        params = (getattr(spec, "params", None) or {})
        angle = params.get("source_rotation_angle")
        if angle is not None:
            return float(angle)
        if getattr(spec, "page_kind", None) in {"axis_integral", "axis_integral_crop"}:
            return float(self.rotation_angle)
        source_3d = params.get("source_3d")
        if isinstance(source_3d, dict):
            angle = source_3d.get("rotation_angle")
        return float(self.rotation_angle if angle is None else angle)

    def _get_rotated_time_range_for_spec(self, raw_data, spec):
        low, up = self._time_indices_for_spec(spec)
        return self._get_rotated_time_range(
            raw_data, low, up, angle=self._rotation_angle_for_spec(spec)
        )

    def _bind_time_interval(self, spec, *, animate=True):
        controller = self.__dict__.get("time_interval_controller")
        bar = self.__dict__.get("timeline_bar")
        if not self._has_time_axis():
            self.time_space = None
            self.time_interval = None
            if controller is not None:
                controller.clear()
            if bar is not None:
                bar.time_space = None
                bar.time_interval = None
                bar.slider_time.setEnabled(False)
                bar.set_timeline_visible(False, animate=animate)
            return

        space = self._time_space_for_data()
        interval = self._legacy_time_interval(spec, space)
        enabled = self._time_page_editable(spec)
        self.time_space = space
        self.time_interval = interval
        if spec is not None:
            if spec.page_kind == "time_integral":
                spec.page_kind = "home"
            params = spec.params
            params["time_interval"] = interval.as_dict()
            if params.get("source_page_kind") == "time_integral":
                params["source_page_kind"] = "home"
            for key in (
                "source_mode", "source_t_index", "source_t_low", "source_t_up",
                "t_index", "t_low", "t_up",
            ):
                params.pop(key, None)
            control_state = params.get("control_state") or {}
            control_state.pop("axis_source_mode", None)
            data_state = control_state.get("data_process") or {}
            for key in ("s_t_low", "s_t_up", "input_t_low", "input_t_up"):
                data_state.pop(key, None)
            image_state = control_state.get("image") or {}
            image_state.pop("slider_time", None)
        if controller is not None:
            manager = self.__dict__.get("source_manager")
            session = manager.active() if manager is not None else None
            controller.set_fourth_axis_drag(
                getattr(session, "fourth_axis_drag", None) if session is not None else None
            )
            controller.set_mode(IntervalEditMode.FULL if enabled else IntervalEditMode.DISABLED)
            controller.bind(space, interval)
        if bar is not None:
            bar.sync_time_interval(space, interval, enabled)
            bar.slider_time.setEnabled(enabled)
            bar.set_timeline_visible(enabled, animate=animate)

    def _persist_time_interval_state(self, spec=None):
        interval = self.__dict__.get("time_interval")
        current = self.left_workspace.current_spec()
        target = spec or current
        if interval is None or target is None or current is None:
            return
        if str(target.page_id) != str(current.page_id):
            return
        target.params["time_interval"] = interval.as_dict()

    def on_time_interval_changed(self):
        if self._syncing_controls or self.core.raw_data is None:
            return
        self._persist_time_interval_state()
        self.request_refresh(
            RefreshCause.DATA_SOURCE,
            interactive=True,
            preview_interval_ms=8,
        )

    def flush_time_interval_refresh(self):
        if self._syncing_controls or self.core.raw_data is None:
            return
        self._persist_time_interval_state()
        self.request_refresh(RefreshCause.DATA_SOURCE, RenderQuality.EXACT, immediate=True)

    def on_time_interval_lock_changed(self, _locked):
        if not self._syncing_controls:
            self._persist_time_interval_state()

    def _seed_time_interval_for_spec(self, spec):
        interval = self.__dict__.get("time_interval")
        source = None
        source_id = getattr(spec, "source_page_id", None)
        if source_id:
            source = self.left_workspace.page_by_id(source_id)
        source_state = (source.params if source is not None else {}).get("time_interval")
        if isinstance(source_state, dict):
            interval = AxisInterval.from_dict(source_state)
        if interval is not None:
            spec.params["time_interval"] = interval.as_dict()
