"""Main-window integration for built-in polyline path slices."""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, Optional

import numpy as np
from PyQt5.QtCore import QEvent, QSignalBlocker, QTimer, Qt
from PyQt5.QtWidgets import QApplication, QMenu, QMessageBox

import bandscope.ui.theme as theme
from bandscope.core.data_scope import scoped_descriptor_from_array
from bandscope.core.rotation_geometry import xy_canvas_mask
from bandscope.core.display_style import DEFAULT_OPACITY_MODE
from bandscope.core.path_slice import (
    PATH_SELECTED_KEY,
    PATH_SLICE_PAGE_KIND,
    PATH_SOURCE_KEY,
    PathNode,
    PathSegmentStyle,
    PathSliceModel,
    common_segment_values,
    draft_nodes,
    write_draft,
)
from bandscope.processing.local_enhancement import cached_cumulative_histogram
from bandscope.processing.path_slice import MomentumCalibration, PathSliceSampler
from bandscope.rendering.path_slice import (
    clamp_viewport,
    full_viewport,
    recommended_viewport,
    render_path_slice,
    viewport_for_segment,
)
from bandscope.ui.path_slice_controls import PathNodeRenameDialog, PathSegmentBar
from bandscope.ui.result_workspace import AnalysisPageSpec
from bandscope.ui.ui_controls import ActionButton


class PathSliceController:
    def __init__(self, window, *, initialize_ui: bool = True):
        self.window = window
        self.sampler = PathSliceSampler()
        self.active_source_id = None
        self._draft_artists = []
        self._path_controls_active = False
        self._viewport_layouts = {}
        self._pending_single = None
        self._single_timer = None
        self.button = None
        self.bar = None
        if initialize_ui:
            self._single_timer = QTimer(window)
            self._single_timer.setSingleShot(True)
            self._single_timer.timeout.connect(self._commit_pending_single)
            self.button = ActionButton(window.canvas_tool_overlay)
            window.canvas_tool_overlay.install_path_button(self.button)
            self.button.toggled.connect(self.toggle_selection_mode)
            self.bar = PathSegmentBar(window.left_workspace)
            window.left_workspace.insert_footer_widget(self.bar, window.timeline_bar)
            self.bar.hide()
            self.bar.selection_changed.connect(self.on_selection_changed)
            self.bar.thickness_changed.connect(self.on_thickness_changed)
            self.bar.viewport_start_changed.connect(self.on_viewport_start_changed)
            self.bar.layoutChanged.connect(window._on_canvas_layout_changed)
            window.canvas_2d.mpl_connect("button_press_event", self.on_canvas_click)
            window.canvas_2d.mpl_connect("button_press_event", self.on_canvas_double_click)

    @classmethod
    def for_window(cls, window) -> "PathSliceController":
        controller = window.__dict__.get("path_slice_controller")
        if controller is None:
            controller = cls(window, initialize_ui=False)
            window.__dict__["path_slice_controller"] = controller
        return controller

    def current_spec(self):
        workspace = self.window.__dict__.get("left_workspace")
        return workspace.current_spec() if workspace is not None else None

    @staticmethod
    def is_path_page(spec) -> bool:
        return spec is not None and spec.page_kind == PATH_SLICE_PAGE_KIND

    def model_for(self, spec) -> PathSliceModel:
        return PathSliceModel.from_params(spec.params)

    def _coords(self):
        return getattr(self.window.core, "coords", {}) or {}

    def can_use_source(self, spec) -> bool:
        if spec is None or self.is_path_page(spec) or self.window.core.raw_data is None:
            return False
        energy = self._coords().get("E")
        if energy is None or len(energy) < 2:
            return False
        if spec.page_kind in {"axis_integral", "axis_integral_crop"}:
            return int(spec.params.get("axis_index", -1)) == 2
        if spec.page_kind == "home":
            info = spec.params.get("home_slice_info")
            if info is None and self._is_current(spec):
                info = self.window.__dict__.get("home_slice_info")
            return bool(info and int(info.get("axis", -1)) == 2)
        return False

    def _is_current(self, spec) -> bool:
        current = self.current_spec()
        return current is not None and spec is not None and current.page_id == spec.page_id

    def _set_available(self, available: bool) -> None:
        overlay = self.window.__dict__.get("canvas_tool_overlay")
        if overlay is not None:
            overlay.set_path_available(bool(available))

    def toggle_selection_mode(self, checked: bool) -> None:
        spec = self.current_spec()
        if checked and not self.can_use_source(spec):
            if self.button is not None:
                with QSignalBlocker(self.button):
                    self.button.setChecked(False)
            return
        if checked:
            for name in ("btn_tb_crop", "btn_tb_erase"):
                button = self.window.__dict__.get(name)
                if button is not None and button.isChecked():
                    button.setChecked(False)
            self.active_source_id = spec.page_id
            self.window.canvas_2d.setCursor(Qt.CrossCursor)
            manager = self.window.__dict__.get("toast_manager")
            if manager is not None:
                manager.show("左键落点，双击节点改名；右键生成、撤销或清除。", title="折线路径")
            self.redraw_draft()
        else:
            self.exit_selection_mode()

    def exit_selection_mode(self) -> None:
        self.deactivate()
        self.redraw_draft()

    def deactivate(self) -> None:
        self._cancel_pending_single()
        self.active_source_id = None
        if self.button is not None:
            with QSignalBlocker(self.button):
                self.button.setChecked(False)
        updater = getattr(self.window, "_update_crop_cursor", None)
        if updater is not None:
            updater()
        else:
            self.window.canvas_2d.unsetCursor()

    def on_crop_toggled(self, checked: bool) -> None:
        if checked and self.active_source_id is not None:
            self.exit_selection_mode()

    def handle_key_event(self, event) -> bool:
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape and self.active_source_id is not None:
            self.exit_selection_mode()
            return True
        return False

    def on_page_activated(self, spec) -> None:
        self._clear_draft_artists()
        self.deactivate()
        self._set_available(self.can_use_source(spec))
        if self.bar is not None:
            self.bar.setVisible(self.is_path_page(spec))

    def on_page_closed(self, page_id: str) -> None:
        self._viewport_layouts.pop(str(page_id), None)
        if self.active_source_id == str(page_id):
            self.deactivate()

    def on_theme_changed(self) -> None:
        if self.bar is not None:
            self.bar.update_theme()
        self.redraw_draft()
        if self.is_path_page(self.current_spec()):
            self.window.global_refresh()

    def _physical_node(self, node: PathNode):
        core = self.window.core
        return (
            float(core.logical_to_physical("X", node.x)),
            float(core.logical_to_physical("Y", node.y)),
        )

    def _logical_point(self, x: float, y: float):
        core = self.window.core
        return (
            float(core.physical_to_logical("X", float(x), clamp=False)),
            float(core.physical_to_logical("Y", float(y), clamp=False)),
        )

    def _hit_node(self, event, nodes):
        if event.inaxes is not self.window.ax_2d:
            return None
        transform = self.window.ax_2d.transData
        mouse = np.asarray([event.x, event.y], dtype=float)
        best = None
        for index, node in enumerate(nodes):
            point = np.asarray(transform.transform(self._physical_node(node)))
            distance = float(np.linalg.norm(point - mouse))
            if distance <= 12.0 and (best is None or distance < best[0]):
                best = (distance, index)
        return None if best is None else best[1]

    def _cancel_pending_single(self) -> None:
        self._pending_single = None
        if self._single_timer is not None:
            self._single_timer.stop()

    def _commit_pending_single(self) -> None:
        pending = self._pending_single
        self._pending_single = None
        if not pending:
            return
        spec = self.current_spec()
        if (
            pending["action"] == "close"
            and spec is not None
            and spec.page_id == pending["page_id"]
            and self.active_source_id == spec.page_id
        ):
            nodes = draft_nodes(spec.params)
            if len(nodes) >= 2 and self._valid_draft(nodes):
                first = nodes[0]
                nodes.append(PathNode(first.x, first.y, f"P{len(nodes) + 1}"))
                write_draft(spec.params, nodes)
                self.redraw_draft()

    def on_canvas_click(self, event) -> None:
        spec = self.current_spec()
        if self.active_source_id is None or spec is None or spec.page_id != self.active_source_id or not self.can_use_source(spec):
            return
        if event.button != 1 or getattr(event, "dblclick", False) or event.inaxes is not self.window.ax_2d:
            return
        if event.xdata is None or event.ydata is None:
            return
        nodes = draft_nodes(spec.params)
        hit = self._hit_node(event, nodes)
        if hit is not None:
            close_path = (
                hit == 0 and len(nodes) >= 2
                and np.hypot(nodes[-1].x - nodes[0].x, nodes[-1].y - nodes[0].y) > 1e-12
            )
            self._pending_single = {"page_id": spec.page_id, "action": "close" if close_path else "rename"}
            if self._single_timer is not None:
                self._single_timer.start(QApplication.doubleClickInterval())
            return
        self._cancel_pending_single()
        x, y = self._logical_point(event.xdata, event.ydata)
        nodes.append(PathNode(x, y, f"P{len(nodes) + 1}"))
        write_draft(spec.params, nodes)
        self.redraw_draft()

    def on_canvas_double_click(self, event) -> None:
        spec = self.current_spec()
        if self.active_source_id is None or spec is None or spec.page_id != self.active_source_id:
            return
        if event.button != 1 or not getattr(event, "dblclick", False) or event.inaxes is not self.window.ax_2d:
            return
        self._cancel_pending_single()
        nodes = draft_nodes(spec.params)
        index = self._hit_node(event, nodes)
        if index is None:
            return
        dialog = PathNodeRenameDialog(self.window, nodes[index].label)
        if dialog.exec_() == dialog.Accepted:
            nodes[index].label = dialog.node_name()
            write_draft(spec.params, nodes)
            self.redraw_draft()
        dialog.deleteLater()

    def undo_last(self) -> None:
        spec = self.current_spec()
        if spec is None:
            return
        nodes = draft_nodes(spec.params)
        if nodes:
            nodes.pop()
            write_draft(spec.params, nodes)
            self.redraw_draft()

    def clear_all(self) -> None:
        spec = self.current_spec()
        if spec is None or spec.page_id != self.active_source_id:
            return
        self._cancel_pending_single()
        write_draft(spec.params, [])
        self.redraw_draft()

    def _clear_draft_artists(self) -> None:
        for artist in self._draft_artists:
            if getattr(artist, "axes", None) is not self.window.ax_2d:
                continue
            try:
                artist.remove()
            except (ValueError, AttributeError):
                pass
        self._draft_artists = []

    def blit_artists(self):
        return tuple(self._draft_artists)

    def redraw_draft(self) -> None:
        self._clear_draft_artists()
        spec = self.current_spec()
        if not self.can_use_source(spec) or not self._is_current(spec):
            if spec is not None and self.active_source_id == spec.page_id:
                self.deactivate()
            self._set_available(False)
            return
        self._set_available(True)
        if self.active_source_id != spec.page_id:
            self.window.canvas_2d.draw_idle()
            return
        nodes = draft_nodes(spec.params)
        if not nodes:
            self.window.canvas_2d.draw_idle()
            return
        points = np.asarray([self._physical_node(node) for node in nodes])
        ax = self.window.ax_2d
        if len(nodes) > 1:
            self._draft_artists.append(ax.plot(
                points[:, 0], points[:, 1], color=theme.ACCENT,
                linewidth=1.7, alpha=0.92, zorder=20,
            )[0])
        self._draft_artists.append(ax.scatter(
            points[:, 0], points[:, 1], s=42, facecolor=theme.BG_2,
            edgecolor=theme.ACCENT, linewidth=1.6, zorder=21,
        ))
        for (x, y), node in zip(points, nodes):
            self._draft_artists.append(ax.text(
                x, y, f"  {node.label}", color=theme.ACCENT,
                fontsize=9, fontweight="bold", fontfamily=theme.MPL_FONT_FAMILIES,
                va="bottom", zorder=22,
            ))
        self.window.canvas_2d.draw_idle()

    def handle_context_menu(self, pos) -> bool:
        spec = self.current_spec()
        if self.is_path_page(spec):
            menu = QMenu(self.window.canvas_2d)
            menu.setStyleSheet(theme.context_menu_qss())
            model = self.model_for(spec)
            guides = menu.addAction("显示竖虚线")
            guides.setCheckable(True)
            guides.setChecked(model.show_guides)
            guides.triggered.connect(lambda checked: self.set_annotation(spec, guides=True, value=checked))
            labels = menu.addAction("显示点名")
            labels.setCheckable(True)
            labels.setChecked(model.show_labels)
            labels.triggered.connect(lambda checked: self.set_annotation(spec, labels=True, value=checked))
            menu.exec_(self.window.canvas_2d.mapToGlobal(pos))
            menu.deleteLater()
            return True
        if self.active_source_id is None or spec is None or spec.page_id != self.active_source_id:
            return False
        nodes = draft_nodes(spec.params)
        valid = self._valid_draft(nodes)
        menu = QMenu(self.window.canvas_2d)
        menu.setStyleSheet(theme.context_menu_qss())
        create = menu.addAction("生成路径切片")
        create.setEnabled(valid)
        create.triggered.connect(lambda: QTimer.singleShot(0, self.generate_page))
        undo = menu.addAction("撤销上一点")
        undo.setEnabled(bool(nodes))
        undo.triggered.connect(self.undo_last)
        clear = menu.addAction("清除所有选点")
        clear.setEnabled(bool(nodes))
        clear.triggered.connect(self.clear_all)
        menu.addSeparator()
        finish = menu.addAction("退出选点")
        finish.triggered.connect(self.exit_selection_mode)
        menu.exec_(self.window.canvas_2d.mapToGlobal(pos))
        menu.deleteLater()
        return True

    @staticmethod
    def _valid_draft(nodes) -> bool:
        return len(nodes) >= 2 and all(
            np.hypot(end.x - start.x, end.y - start.y) > 1e-12
            for start, end in zip(nodes, nodes[1:])
        )

    def set_annotation(self, spec, *, guides=False, labels=False, value=False) -> None:
        model = self.model_for(spec)
        if guides:
            model.show_guides = bool(value)
        if labels:
            model.show_labels = bool(value)
        model.write_params(spec.params)
        self.window.global_refresh()

    def _freeze_source(self, spec) -> Dict[str, Any]:
        if spec.page_kind in {"axis_integral", "axis_integral_crop"}:
            params = dict(self.window._resolved_axis_integral_params(spec))
            for key in ("source_mode", "source_t_index", "source_t_low", "source_t_up"):
                params.pop(key, None)
            params["crop_regions"] = copy.deepcopy(spec.params.get("crop_regions") or [])
            crop_rect = self.window._axis_crop_rect_from_params(spec.params)
            if crop_rect is not None:
                params.update({
                    "crop_k_low": int(crop_rect["x_low"]),
                    "crop_k_up": int(crop_rect["x_up"]),
                    "crop_e_low": int(crop_rect["y_low"]),
                    "crop_e_up": int(crop_rect["y_up"]),
                })
            params["source_page_kind"] = spec.page_kind
            params["clip_ranges"] = copy.deepcopy(spec.params.get("clip_ranges"))
            params["rotation_angle"] = float(self.window._rotation_angle_for_spec(spec))
            params["data_scope_id"] = str(getattr(spec, "data_scope_id", "full"))
            return params
        info = copy.deepcopy(spec.params.get("home_slice_info") or self.window.home_slice_info)
        return {
            "source_page_kind": "home",
            "home_slice_info": info,
            "clip_ranges": copy.deepcopy(spec.params.get("clip_ranges")),
            "crop_regions": copy.deepcopy(spec.params.get("crop_regions") or []),
            "rotation_angle": float(self.window._rotation_angle_for_spec(spec)),
            "data_scope_id": str(getattr(spec, "data_scope_id", "full")),
        }

    def generate_page(self) -> Optional[str]:
        source = self.current_spec()
        if not self.can_use_source(source):
            return None
        nodes = draft_nodes(source.params)
        if not self._valid_draft(nodes):
            self.window._show_message("无法生成路径切片", "至少需要两个位置不同的相邻节点。", QMessageBox.Information)
            return None
        levels = tuple(float(slider.value()) for slider in (
            self.window.page_render.s_low,
            self.window.page_render.s_gamma,
            self.window.page_render.s_up,
        ))
        intensity_map = DEFAULT_OPACITY_MODE
        colormap_range = self.window.page_render.get_display_style().get("colormap_range", (0.0, 1.0))
        styles = [
            PathSegmentStyle(0.0, *levels, intensity_map, *colormap_range)
            for _ in range(len(nodes) - 1)
        ]
        model = PathSliceModel(nodes=copy.deepcopy(nodes), segments=styles)
        title = self.window._make_unique_page_title(f"路径切片 {model.path_name()}")
        params = {
            PATH_SOURCE_KEY: self._freeze_source(source),
            **self.window._time_interval_params_for_spec(source),
        }
        model.write_params(params)
        spec = AnalysisPageSpec(
            page_id=self.window._make_page_id(),
            title=title,
            page_kind=PATH_SLICE_PAGE_KIND,
            source_module="path_slice",
            source_page_id=source.page_id,
            source_title=source.title,
            data_scope_id=getattr(source, "data_scope_id", "full"),
            params=params,
        )
        self.window._seed_control_state_for_spec(spec)
        self._clear_draft_artists()
        self.deactivate()
        self.window.left_workspace.add_page(spec)
        return spec.page_id

    def _source_spec(self, spec, source: Mapping[str, Any]):
        kind = str(source.get("source_page_kind") or "")
        if kind in {"axis_integral", "axis_integral_crop"}:
            source_spec = self.window._build_axis_source_spec_from_params(
                source, page_id="__path_slice_source__", title="路径来源"
            )
        else:
            source_spec = AnalysisPageSpec(
                page_id="__path_slice_source__",
                title="路径来源",
                page_kind="home",
                source_module="path_slice",
                params={
                    "home_slice_info": copy.deepcopy(source.get("home_slice_info")),
                    "clip_ranges": copy.deepcopy(source.get("clip_ranges")),
                },
            )
        source_spec.params.update(self.window._time_interval_params_for_spec(spec))
        source_spec.params["clip_ranges"] = copy.deepcopy(source.get("clip_ranges"))
        source_spec.params["source_rotation_angle"] = float(source.get(
            "rotation_angle", getattr(self.window, "rotation_angle", 0.0)
        ) or 0.0)
        source_spec.data_scope_id = str(source.get("data_scope_id", "full"))
        if source.get("crop_regions") is not None:
            source_spec.params["crop_regions"] = copy.deepcopy(source["crop_regions"])
        return source_spec

    def _valid_mask(self, source_context, display_context, raw_data, source: Mapping[str, Any], angle: float, coords):
        source_volume = source_context["data"]
        descriptor = scoped_descriptor_from_array(source_volume)
        full_shape = tuple(int(np.asarray(coords[key]).size) for key in ("X", "Y", "E"))
        if descriptor is not None:
            full = np.zeros(descriptor.full_shape[:3], dtype=np.float64)
            full[descriptor.spatial_slices] = np.asarray(source_volume, dtype=np.float64)
            mask = np.zeros(descriptor.full_shape[:3], dtype=bool)
            local_mask = np.ones(np.asarray(source_volume).shape, dtype=bool)
            for key in ("spatial_valid_mask", "canvas_mask"):
                valid = source_context.get(key)
                if valid is not None and np.asarray(valid).shape == local_mask.shape:
                    local_mask &= np.asarray(valid, dtype=bool)
            mask[descriptor.spatial_slices] = local_mask
        else:
            full = np.asarray(source_volume, dtype=np.float64)
            mask = np.ones(full.shape, dtype=bool)
            for key in ("spatial_valid_mask", "canvas_mask"):
                valid = source_context.get(key)
                if valid is not None and np.asarray(valid).shape == mask.shape:
                    mask &= np.asarray(valid, dtype=bool)

        source_bounds = source_context.get("source_data_bounds")
        raw_descriptor = getattr(raw_data, "descriptor", None)
        if source_bounds is None and raw_descriptor is not None and not raw_descriptor.is_full:
            source_bounds = raw_descriptor.spatial_bounds
        if source_bounds is not None and source_context.get("canvas_mask") is None:
            canvas = np.broadcast_to(
                xy_canvas_mask(
                    full_shape[:2], float(angle), source_bounds=tuple(source_bounds[:4]),
                )[:, :, None],
                full_shape,
            ).copy()
            energy_low, energy_up = (int(value) for value in source_bounds[4:6])
            energy = np.zeros(full_shape[2], dtype=bool)
            energy[energy_low:energy_up + 1] = True
            mask &= canvas & energy[None, None, :]

        plot_bounds = display_context["plot_logical_bounds"]
        x_low = max(0, int(plot_bounds["x_low"]))
        x_up = min(full_shape[0] - 1, int(plot_bounds["x_up"]))
        y_low = max(0, int(plot_bounds["y_low"]))
        y_up = min(full_shape[1] - 1, int(plot_bounds["y_up"]))
        local_display_mask = np.isfinite(display_context["data"])
        for key in ("spatial_valid_mask", "canvas_mask"):
            valid = display_context.get(key)
            if valid is not None and np.asarray(valid).shape == local_display_mask.shape:
                local_display_mask &= np.asarray(valid, dtype=bool)
        display_mask = np.zeros(full_shape[:2], dtype=bool)
        if x_low <= x_up and y_low <= y_up:
            expected_shape = (x_up - x_low + 1, y_up - y_low + 1)
            if local_display_mask.shape != expected_shape:
                raise ValueError("路径来源切片与其逻辑边界不匹配。")
            display_mask[x_low:x_up + 1, y_low:y_up + 1] = local_display_mask
        mask &= display_mask[:, :, None]

        clip_ranges = source_context.get("clip_ranges", source.get("clip_ranges"))
        if clip_ranges is not None and source_bounds is None:
            x0, x1, y0, y1, e0, e1 = (int(value) for value in clip_ranges)
            clipped = np.zeros(full_shape, dtype=bool)
            x0, x1 = max(0, x0), min(full_shape[0] - 1, x1)
            y0, y1 = max(0, y0), min(full_shape[1] - 1, y1)
            e0, e1 = max(0, e0), min(full_shape[2] - 1, e1)
            if x0 <= x1 and y0 <= y1 and e0 <= e1:
                clipped[x0:x1 + 1, y0:y1 + 1, e0:e1 + 1] = True
            mask &= clipped
        return full, mask

    def compute_context(self, spec):
        model = self.model_for(spec)
        model.validate()
        source = spec.params.get(PATH_SOURCE_KEY)
        if not isinstance(source, Mapping):
            raise ValueError("路径切片缺少来源参数。")
        raw_data, coords = self.window._get_display_state_for_spec(spec)
        if raw_data is None or coords is None:
            return None
        source_spec = self._source_spec(spec, source)
        source_context = self.window._get_3d_source_context_for_axis(source_spec, raw_data)
        display_context = self.window._compute_render_context(source_spec)
        if display_context is None or display_context.get("crop_empty"):
            raise ValueError("路径来源范围内没有有效数据。")
        angle = float(source.get(
            "rotation_angle", getattr(self.window, "rotation_angle", 0.0)
        ) or 0.0)
        volume, valid_mask = self._valid_mask(
            source_context, display_context, raw_data, source, angle, coords
        )
        energy_indices = np.flatnonzero(valid_mask.any(axis=(0, 1)))
        if energy_indices.size == 0:
            raise ValueError("来源范围内没有有效的能量数据。")
        e0, e1 = int(energy_indices[0]), int(energy_indices[-1])
        volume = volume[:, :, e0:e1 + 1]
        valid_mask = valid_mask[:, :, e0:e1 + 1]
        energy = np.asarray(coords["E"], dtype=np.float64)[e0:e1 + 1]
        core = self.window.core
        calibration = MomentumCalibration.create(
            coords["X"], coords["Y"],
            x_source=core.coord_sources.get("X", "index"),
            y_source=core.coord_sources.get("Y", "index"),
            x_unit=core.coord_units.get("X"), y_unit=core.coord_units.get("Y"),
        )
        t_low, t_up = self.window._time_indices_for_spec(spec)
        source_key = (
            repr(dict(source)),
            (t_low, t_up),
            self.window._raw_data_identity(raw_data),
            int(getattr(self.window, "shared_denoise_version", 0) or 0),
            round(angle, 2),
            e0, e1,
        )
        sample = self.sampler.sample(
            volume, energy, model, calibration,
            source_key=source_key, valid_mask=valid_mask,
        )
        title = f"路径切片 {model.path_name()}"
        return {
            "view": PATH_SLICE_PAGE_KIND,
            "title": title,
            "path_sample": sample,
            "path_model": model,
            "k": sample.k,
            "energy_axis": sample.energy,
            "raw_intensity": sample.intensity,
            "source_t_index": t_low,
            "source_t_indices": (t_low, t_up),
            "coords": coords,
        }

    def render(self, spec, context) -> None:
        sample = context["path_sample"]
        model = self.model_for(spec)
        width = max(100, self.window.canvas_2d.width() - 150)
        height = max(100, self.window.canvas_2d.height() - 150)
        layout = (int(width), int(height))
        recommended = recommended_viewport(sample, width, height)
        previous_layout = self._viewport_layouts.get(spec.page_id)
        if model.viewport is None:
            model.viewport = list(recommended)
        elif previous_layout != layout:
            current = clamp_viewport(model.viewport, sample)
            full = full_viewport(sample)
            was_full = current[1] - current[0] >= full[1] - full[0] - 1e-9
            center = full[0] if was_full else (current[0] + current[1]) / 2.0
            visible_width = recommended[1] - recommended[0]
            model.viewport = list(clamp_viewport(
                (center - visible_width / 2.0, center + visible_width / 2.0), sample
            ))
        else:
            model.viewport = list(clamp_viewport(model.viewport, sample))
        self._viewport_layouts[spec.page_id] = layout
        model.write_params(spec.params)
        context["path_model"] = model
        render_path_slice(
            self.window.ax_2d,
            self.window.canvas_2d,
            sample,
            model,
            cmap=self.window.page_render.get_selected_cmap(),
            title=context["title"],
            viewport=model.viewport,
            display_e_flip=bool(context.get("display_e_flip")),
            locked_range=self._locked_range(),
            energy_label=self.window._axis_label_text("E"),
        )
        self.sync_bar(spec, sample, model)

    @staticmethod
    def _locked_range():
        from bandscope.rendering.render_core import VisualEngine

        return VisualEngine.locked_data_range()

    def sync_bar(self, spec, sample=None, model=None) -> None:
        if self.bar is None:
            return
        sample = sample or self.window.current_render_context["path_sample"]
        model = model or self.model_for(spec)
        lengths = [
            sample.calibration.segment_length(model.nodes[index], model.nodes[index + 1])
            for index in range(model.segment_count)
        ]
        common = common_segment_values(model.segments, model.selected)
        self.bar.set_state(
            names=[node.label for node in model.nodes],
            lengths=lengths,
            selected=model.selected,
            thickness=common.get("thickness"),
            multi_thickness="thickness" in common and common["thickness"] is None,
            unit=sample.calibration.thickness_unit,
            step=sample.calibration.sample_spacing,
            viewport=model.viewport,
            full_viewport=full_viewport(sample),
        )

    def on_selection_changed(self, selected) -> None:
        spec = self.current_spec()
        if not self.is_path_page(spec):
            return
        previous = set(spec.params.get(PATH_SELECTED_KEY) or ())
        model = self.model_for(spec)
        model.selected = sorted({int(index) for index in selected if 0 <= index < model.segment_count})
        added = set(model.selected) - previous
        if self.window.current_render_context and added:
            sample = self.window.current_render_context["path_sample"]
            model.viewport = list(viewport_for_segment(sample, max(added), model.viewport))
        model.write_params(spec.params)
        self.sync_display_controls(spec)
        self.window.global_refresh()

    def on_thickness_changed(self, value: float) -> None:
        spec = self.current_spec()
        if not self.is_path_page(spec) or self.window._syncing_controls:
            return
        model = self.model_for(spec)
        model.update_selected(thickness=max(0.0, float(value)))
        model.write_params(spec.params)
        self.window.global_refresh()

    def on_viewport_start_changed(self, fraction: float) -> None:
        spec = self.current_spec()
        context = self.window.current_render_context
        if not self.is_path_page(spec) or not context or context.get("view") != PATH_SLICE_PAGE_KIND:
            return
        model = self.model_for(spec)
        sample = context["path_sample"]
        full = full_viewport(sample)
        current = clamp_viewport(model.viewport, sample)
        width = current[1] - current[0]
        start = full[0] + float(np.clip(fraction, 0.0, 1.0)) * (full[1] - full[0] - width)
        model.viewport = [start, start + width]
        model.write_params(spec.params)
        self.render(spec, context)

    def statistics_values(self, spec, context=None):
        context = context or self.window.current_render_context or {}
        sample = context.get("path_sample")
        model = self.model_for(spec)
        if sample is None or model is None:
            return self.__dict__.setdefault(
                "_empty_levels_statistics", np.asarray([], dtype=np.float64),
            )
        arrays = tuple(
            np.asarray(sample.segments[index].intensity)
            for index in model.selected if 0 <= index < len(sample.segments)
        )
        cached_arrays = self.__dict__.get("_levels_statistics_arrays")
        cache_hit = (
            cached_arrays is not None
            and len(cached_arrays) == len(arrays)
            and all(cached is current for cached, current in zip(cached_arrays, arrays))
        )
        if not cache_hit:
            self._levels_statistics_values = (
                np.concatenate([value.ravel() for value in arrays]) if arrays
                else np.asarray([], dtype=np.float64)
            )
            self._levels_statistics_arrays = arrays
        return self._levels_statistics_values

    def sync_display_controls(self, spec) -> None:
        page = self.window.page_render
        if not self.is_path_page(spec):
            if self._path_controls_active:
                for control in (page.s_low, page.s_gamma, page.s_up,
                                page.s_cmap_low, page.s_cmap_high):
                    control.setEnabled(True)
                page.region_combo.show()
                page._sync_display_visibility()
                self._path_controls_active = False
            return
        self._path_controls_active = True
        model = self.model_for(spec)
        common = common_segment_values(model.segments, model.selected)
        enabled = bool(model.selected)
        for key, slider in (("black", page.s_low), ("gray", page.s_gamma), ("white", page.s_up)):
            slider.setEnabled(enabled)
            with QSignalBlocker(slider):
                if common.get(key) is not None:
                    slider.setValue(round(common[key]))
            label = getattr(slider, "_value_label", None)
            if label is not None:
                label.setText("多值" if common.get(key) is None else str(int(common[key])))
            slider.setToolTip("多值：调整后写入全部选中段" if common.get(key) is None else "当前选中段的共同值")
        for key, slider in (("colormap_start", page.s_cmap_low),
                            ("colormap_end", page.s_cmap_high)):
            slider.setEnabled(enabled)
            with QSignalBlocker(slider):
                if common.get(key) is not None:
                    slider.setValue(round(common[key] * 1000.0))
            slider.setToolTip("多值：调整后写入全部选中段" if common.get(key) is None else "当前选中段的共同值")
        page.levels_editor.update()
        page.colormap_range_editor.sync_from_proxies()
        page.region_combo.hide()
        page._display_view = PATH_SLICE_PAGE_KIND
        page._sync_display_visibility()
        values = self.statistics_values(spec)
        hit, histogram = cached_cumulative_histogram(values)
        page.set_levels_statistics(histogram if hit else None, self._locked_range())

    def _display_change_field(self):
        try:
            sender = self.window.sender()
        except Exception:
            sender = None
        page = self.window.page_render
        for name, slider in (("black", page.s_low), ("gray", page.s_gamma), ("white", page.s_up),
                             ("colormap_start", getattr(page, "s_cmap_low", None)),
                             ("colormap_end", getattr(page, "s_cmap_high", None))):
            if slider is not None and sender is slider:
                return name
        return None

    def handle_levels_schedule(self) -> bool:
        spec = self.current_spec()
        if not self.is_path_page(spec):
            return False
        self._write_levels(spec, field=self._display_change_field())
        return True

    def handle_levels_commit(self) -> bool:
        spec = self.current_spec()
        if not self.is_path_page(spec):
            return False
        self._write_levels(spec, field=self._display_change_field())
        return True

    def _write_levels(self, spec, *, field=None) -> None:
        if self.window._syncing_controls:
            return
        model = self.model_for(spec)
        if not model.selected:
            return
        page = self.window.page_render
        if field in {"colormap_start", "colormap_end"}:
            slider = page.s_cmap_low if field == "colormap_start" else page.s_cmap_high
            model.update_selected(**{field: slider.value() / 1000.0})
            model.write_params(spec.params)
            from bandscope.rendering.refresh_pipeline import RenderQuality, RefreshCause

            self.window.request_refresh(RefreshCause.DISPLAY_MAPPING, RenderQuality.EXACT, immediate=True)
            return
        if field in {"black", "gray", "white"}:
            slider = {"black": page.s_low, "gray": page.s_gamma, "white": page.s_up}[field]
            model.update_selected(**{field: float(slider.value())})
        else:
            model.update_selected(levels=tuple(float(slider.value()) for slider in (
                page.s_low, page.s_gamma, page.s_up,
            )))
        model.write_params(spec.params)
        from bandscope.rendering.refresh_pipeline import RenderQuality, RefreshCause

        self.window.request_refresh(RefreshCause.DISPLAY_MAPPING, RenderQuality.EXACT, immediate=True)

    def export_payload(self, spec, context):
        sample = context["path_sample"]
        model = self.model_for(spec)
        arrays = {
            "k": np.asarray(sample.k, dtype=np.float32),
            "E": np.asarray(sample.energy, dtype=np.float32),
            "intensity": np.asarray(sample.intensity, dtype=np.float32),
            "path_nodes": np.asarray([[node.x, node.y] for node in model.nodes], dtype=np.float64),
            "path_node_names": np.asarray([node.label for node in model.nodes]),
            "path_node_k": np.asarray(sample.node_k, dtype=np.float64),
            "path_thickness": np.asarray([style.thickness for style in model.segments], dtype=np.float64),
            "path_segment_ranges": np.asarray(sample.segment_ranges, dtype=np.int32),
            "path_show_guides": np.asarray([model.show_guides]),
            "path_show_labels": np.asarray([model.show_labels]),
            "path_coordinate_mode": np.asarray(["physical" if sample.calibration.calibrated else "index"]),
            "path_k_unit": np.asarray([sample.calibration.unit]),
        }
        arrays.update(self.window._time_interval_export_metadata(spec, context.get("coords") or self.window.core.coords))
        name = self.window._sanitize_filename_component(spec.title) + ".mat"
        return "导出路径切片", name, arrays
