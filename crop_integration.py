"""Crop interaction wiring for the analyzer's VTK and Matplotlib canvases."""
import copy
from dataclasses import replace

import numpy as np
from PyQt5.QtCore import QEvent, Qt
from matplotlib.patches import Rectangle
from matplotlib.widgets import RectangleSelector

import theme
from crop_model import apply_selection, spatial_bounds
from data_scope import DataScopeDescriptor
from result_workspace import AnalysisPageSpec
from render_core import VisualEngine


class CropInteractionMixin:
    def _render_empty_crop(self):
        self._clear_interactive_box()
        self._clear_axis_crop_interaction(redraw=False)
        self.left_display_stack.setCurrentIndex(1)
        VisualEngine.clear_2d_colorbar(self.ax_2d)
        self.ax_2d.clear()
        self.ax_2d.text(0.5, 0.5, "当前裁剪范围内没有有效数据", transform=self.ax_2d.transAxes,
                        ha="center", va="center", color=theme.TEXT_2,
                        fontfamily=["DejaVu Sans", "Microsoft YaHei"])
        self.ax_2d.set_axis_off()
        self.canvas_2d.draw_idle()

    def _crop_enabled(self):
        controller = self.__dict__.get("crop_controller")
        return bool(controller is not None and controller.enabled)

    def _crop_event_filter(self, watched, event):
        controller = self.__dict__.get("crop_controller")
        if controller is None or not controller.enabled:
            return False
        popup = controller.popup
        in_canvas = watched in (self.canvas_2d, self.plotter, self.plotter.interactor)
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            if popup.isVisible():
                popup.hide()
                return True
            if in_canvas:
                self.btn_tb_crop.setChecked(False)
                return True
        if not in_canvas or controller.selection is None:
            return False
        if event.type() in (QEvent.Enter, QEvent.MouseMove):
            watched.setCursor(controller.cursor)
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.RightButton:
            self._show_crop_popup(event.globalPos())
            return True
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.RightButton:
            return True
        if event.type() == QEvent.ContextMenu:
            if not popup.isVisible():
                self._show_crop_popup(event.globalPos())
            return True
        return False

    def _show_crop_popup(self, global_pos):
        controller = self.crop_controller
        if controller.selection is not None:
            controller.popup.show_at(global_pos)

    def _activate_crop_context(self, spec, context):
        controller = self.__dict__.get("crop_controller")
        if controller is None:
            return
        controller.activate(spec.page_id if spec else None, context, e_flip=self.timeline_bar.switch_flip.isChecked())
        self._update_crop_cursor()

    def _update_crop_cursor(self):
        controller = self.crop_controller
        active = controller.enabled and controller.selection is not None
        for widget in (self.canvas_2d, self.plotter, self.plotter.interactor):
            if active:
                widget.setCursor(controller.cursor)
            else:
                widget.unsetCursor()

    def on_toggle_interactive_box(self, checked):
        self.crop_controller.enabled = bool(checked)
        if not checked:
            self.crop_controller.popup.hide()
        if checked and self._can_show_interactive_box():
            self._rebuild_interactive_box()
        else:
            self._restore_volume_opacity_if_dimmed()
            self._clear_interactive_box()
        self._refresh_axis_crop_interaction(self.left_workspace.current_spec(), self.current_render_context)
        self._update_crop_cursor()
        self.plotter.render()

    def _can_show_interactive_box(self):
        context = self.current_render_context
        return bool(self._crop_enabled() and context and context.get("view") == "3d"
                    and not context.get("crop_empty") and self.left_display_stack.currentIndex() == 0)

    def _crop_logical_bounds(self):
        selection = self.crop_controller.selection
        if selection is None or selection.view != "3d":
            return None
        result = []
        for i, key in enumerate(selection.axes):
            result.extend(sorted(self.core.physical_to_logical(key, v) for v in selection.bounds[2*i:2*i+2]))
        return result

    def _get_render_bounds_for_box(self, logical_bounds=None):
        shape = self._full_domain_spatial_shape()
        if shape is None:
            return None
        bounds = logical_bounds if logical_bounds is not None else self._crop_logical_bounds()
        if bounds is None:
            return None
        domain = spatial_bounds(self.current_render_context)
        bounded = []
        for i in range(3):
            a, b = np.clip(bounds[2*i:2*i+2], domain[2*i], domain[2*i+1])
            bounded.extend((a, b))
        return self.core.logical_to_render_bounds(bounded, shape)

    def _sync_slice_edits_from_logical_bounds(self, logical_bounds=None):
        if self.core.raw_data is None:
            return
        bounds = logical_bounds if logical_bounds is not None else self._get_full_logical_bounds()
        if bounds is None:
            return
        self.precise_logical_bounds = list(bounds)
        physical = self.core.logical_bounds_to_physical_bounds(bounds)
        physical = tuple(v for i in range(3) for v in sorted(physical[2*i:2*i+2]))
        self.last_synced_slice_texts = self._logical_bounds_to_texts(bounds)
        controller = self.__dict__.get("crop_controller")
        if controller is not None and controller.selection is not None and controller.selection.view == "3d":
            try:
                controller.set_selection(replace(controller.selection, bounds=physical), notify=False)
            except ValueError:
                # A zero-thickness VTK handle gesture on multiple axes is not a
                # valid slice. Keep the last valid draft instead.
                return

    def _sync_slice_edits_from_render_bounds(self, render_bounds):
        shape = self._full_domain_spatial_shape()
        if shape is None:
            return
        bounds = self.core.render_to_logical_bounds(render_bounds, shape)
        domain = spatial_bounds(self.current_render_context)
        bounds = [float(np.clip(v, domain[2*(i//2)], domain[2*(i//2)+1])) for i, v in enumerate(bounds)]
        self._sync_slice_edits_from_logical_bounds(bounds)

    def _on_crop_selection_changed(self):
        selection = self.crop_controller.selection
        if selection is None:
            return
        if selection.view == "3d":
            self.precise_logical_bounds = self._crop_logical_bounds()
            self._rebuild_interactive_box()
            self.plotter.render()
        else:
            self._refresh_axis_crop_interaction(self.left_workspace.current_spec(), self.current_render_context)

    def _on_axis_crop_canvas_click(self, event):
        # Right click now opens the numeric popup; it must never erase a draft.
        return

    def _supports_axis_crop_context(self, spec, context=None):
        context = context if context is not None else self.current_render_context
        return bool(spec and context and context.get("view") in {"2d", "1d", "1d_comparison", "waterfall"}
                    and not context.get("crop_empty"))

    def _draw_axis_crop_overlay(self, spec, context):
        self._clear_axis_crop_overlay(redraw=False)
        selection = self.crop_controller.selection
        if selection is None or selection.view == "3d":
            return
        x0, x1, y0, y1 = selection.bounds
        self.axis_crop_overlay = Rectangle((x0, y0), x1-x0, y1-y0, fill=False,
                                          edgecolor=theme.ACCENT, linewidth=1.4, linestyle="--", alpha=0.9)
        self.ax_2d.add_patch(self.axis_crop_overlay)

    def _refresh_axis_crop_interaction(self, spec, context):
        self._clear_axis_crop_selector()
        self._clear_axis_crop_overlay(redraw=False)
        if self._crop_enabled() and self._supports_axis_crop_context(spec, context):
            self._draw_axis_crop_overlay(spec, context)
            self.axis_crop_selector = RectangleSelector(
                self.ax_2d, self._on_axis_crop_selected, useblit=True, button=[1],
                minspanx=1e-12, minspany=1e-12, spancoords="data", interactive=False,
                props={"facecolor": theme.ACCENT, "edgecolor": theme.ACCENT, "alpha": 0.12, "fill": True},
            )
        if self.left_display_stack.currentIndex() == 1:
            self.canvas_2d.draw_idle()

    def _on_axis_crop_selected(self, eclick, erelease):
        selection = self.crop_controller.selection
        if selection is None or selection.view == "3d":
            return
        values = (eclick.xdata, erelease.xdata, eclick.ydata, erelease.ydata)
        if any(value is None for value in values):
            return
        bounds = (*sorted(values[:2]), *sorted(values[2:]))
        try:
            self.crop_controller.set_selection(replace(selection, bounds=bounds))
        except ValueError:
            return

    def on_cut(self):
        controller = self.crop_controller
        source = self.left_workspace.current_spec()
        selection = controller.selection
        if source is None or selection is None or source.page_kind == "control_panel":
            return
        if not self._render_exact_ready:
            controller.popup.set_error("请等待当前结果计算完成后裁剪。")
            return
        context = self._compute_render_context(source)
        if context is None:
            return
        cropped = apply_selection(context, selection)
        if cropped.get("crop_empty"):
            controller.popup.set_error("裁剪范围内没有有效数据。")
            return
        volume_roi = source.page_kind == "home" and selection.view == "3d" and cropped["view"] == "3d"
        if volume_roi and abs(float(self.rotation_angle)) >= 1e-6:
            controller.popup.set_error("请先将 Z 轴旋转恢复为 0°，再裁剪全帧 ROI。")
            return
        self._persist_active_page_state()
        params = copy.deepcopy(source.params)
        kind = source.page_kind
        scope_id = source.data_scope_id
        if volume_roi:
            bounds = tuple(int(v) for v in cropped["data_bounds"])
            candidate = DataScopeDescriptor("candidate", tuple(self.original_raw_data.shape), bounds)
            try:
                self._validate_denoise_for_descriptor(self.global_denoise_methods, candidate)
            except ValueError as exc:
                controller.popup.set_error(str(exc))
                return
            descriptor = self._register_roi_scope(bounds)
            scope_id = descriptor.scope_id
            params.update(clip_ranges=list(bounds), home_slice_info=None,
                          precise_logical_bounds=list(bounds), data_scope_label=descriptor.label)
            params.pop("crop_regions", None)
        else:
            regions = list(params.get("crop_regions", []))
            if kind in {"axis_integral", "axis_integral_crop"}:
                if not regions:
                    params["crop_base_rect"] = self._axis_crop_rect_from_params(source.params) if kind == "axis_integral_crop" else None
                kind = "axis_integral_crop"
                rect = cropped["plot_logical_bounds"]
                params.update(crop_k_low=rect["x_low"], crop_k_up=rect["x_up"], crop_e_low=rect["y_low"], crop_e_up=rect["y_up"])
            regions.append(selection.to_dict())
            params["crop_regions"] = regions
        title = self._make_unique_page_title(f"裁剪 - {source.title}")
        spec = AnalysisPageSpec(self._make_page_id(), title, kind, source.source_module,
                                params=params, source_page_id=source.page_id, source_title=source.title,
                                data_scope_id=scope_id)
        controller.popup.hide()
        self.left_workspace.add_page(spec)
        if volume_roi:
            self._invalidate_scope_render_state()
            self._request_scope_denoise_if_needed(spec)
            self.global_refresh()
