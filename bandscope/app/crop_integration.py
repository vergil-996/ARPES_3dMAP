"""Crop interaction wiring for the analyzer's VTK and Matplotlib canvases.

主框选开关控制全部选区，裁剪 / 裁空开关只控制活动选区的数值范围窗口。
"""
import copy
from dataclasses import replace

import numpy as np
from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtGui import QContextMenuEvent

import bandscope.ui.theme as theme
from bandscope.core.axis_interval import nearest_index
from bandscope.core.crop_model import apply_selection, capture_selection_geometry
from bandscope.core.data_scope import DataScopeDescriptor
from bandscope.ui.result_workspace import AnalysisPageSpec
from bandscope.ui.interactive_crop_box import CropBoxGestureCoordinator, InteractiveCropBox
from bandscope.rendering.render_core import VisualEngine


def _crop_mode_button(host, operation):
    key = "btn_tb_erase" if operation == "erase" else "btn_tb_crop"
    if hasattr(host, "__dict__"):
        return host.__dict__.get(key)
    return getattr(host, key, None)


def _show_crop_popup_on_toggle(host, operation):
    """打开裁剪/裁空开关时直接给出范围窗口（原先只能右键呼出）。"""
    controller = getattr(host, "crop_controller", None)
    if controller is None or controller.selection is None:
        return
    controller.popup.show_near(_crop_mode_button(host, operation))


def _request_axis_title_menu(host, owner, global_pos):
    """裁剪模式下右键被范围窗口占用：轴标题菜单排到过滤器之外再弹。

    在事件过滤器里直接 ``exec_()`` 会开出嵌套事件循环，所以只排队一次调用；
    宿主没有该能力（只填了部分属性的测试替身）时静默跳过。
    """
    show_menu = getattr(host, "_show_axis_title_menu", None)
    if show_menu is None:
        return
    QTimer.singleShot(0, lambda: show_menu(owner, global_pos))


def _sync_crop_popup_visibility(host, context):
    controller = getattr(host, "crop_controller", None)
    if controller is None:
        return
    popup = controller.popup
    if not controller.enabled or controller.selection is None or context is None:
        popup.hide()
        return
    operation_button = _crop_mode_button(host, controller.operation)
    operation_checked = operation_button is None or operation_button.isChecked()
    if not operation_checked:
        popup.hide()
        return
    if not popup.isVisible():
        popup.show_near(operation_button)


class CropInteractionMixin:
    """裁剪/裁空接线。

    模块级的窗口辅助函数按宿主属性取名，不要求宿主实现额外方法——
    测试替身只提供被调用到的属性。
    """

    def _render_empty_crop(self):
        self._clear_interactive_box()
        self._clear_axis_crop_interaction(redraw=False)
        self.left_display_stack.setCurrentIndex(1)
        VisualEngine.clear_2d_colorbar(self.ax_2d)
        self.ax_2d.clear()
        self.ax_2d.text(0.5, 0.5, "当前结果没有有效数据", transform=self.ax_2d.transAxes,
                        ha="center", va="center", color=theme.TEXT_2,
                        fontfamily=theme.MPL_FONT_FAMILIES)
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
                button = self.btn_tb_erase if controller.operation == "erase" else self.btn_tb_crop
                button.setChecked(False)
                return True
        if not in_canvas or controller.selection is None:
            return False
        if event.type() in (QEvent.Enter, QEvent.MouseMove):
            watched.setCursor(controller.cursor)
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.RightButton:
            selection_id = CropInteractionMixin._crop_selection_at_pointer(self, watched, event)
            if selection_id is not None and CropInteractionMixin._show_crop_selection_popup(
                    self, selection_id, event.globalPos()):
                return True
            self._show_crop_popup(event.globalPos())
            _request_axis_title_menu(self, watched, event.globalPos())
            return True
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.RightButton:
            return True
        if event.type() == QEvent.ContextMenu:
            selection_id = CropInteractionMixin._crop_selection_at_pointer(self, watched, event)
            if selection_id is not None and CropInteractionMixin._show_crop_selection_popup(
                    self, selection_id, event.globalPos()):
                return True
            if not popup.isVisible():
                self._show_crop_popup(event.globalPos())
            _request_axis_title_menu(self, watched, event.globalPos())
            return True
        return False

    def _show_crop_popup(self, global_pos):
        controller = self.crop_controller
        if controller.selection is not None:
            controller.popup.show_at(global_pos)

    def _crop_selection_at_pointer(self, watched, event):
        if watched is not self.canvas_2d:
            hit_test = getattr(self, "_crop_box_selection_at_event", None)
            return hit_test(watched, event) if hit_test is not None else None
        if (event.type() == QEvent.ContextMenu
                and event.reason() != QContextMenuEvent.Mouse):
            return None
        return CropInteractionMixin._axis_crop_selection_at_event(self, event)

    def _axis_crop_selection_at_event(self, event):
        selectors = self.__dict__.get("axis_crop_selectors", {})
        if not selectors:
            return None
        pos = event.pos()
        ratio_getter = getattr(self.canvas_2d, "devicePixelRatioF", None)
        ratio = float(ratio_getter()) if ratio_getter is not None else 1.0
        x = pos.x() * ratio
        y = (self.canvas_2d.figure.bbox.height / ratio - pos.y()) * ratio
        for selection_id, selector in reversed(list(selectors.items())):
            if selector.handle_at_display(x, y) is not None:
                return selection_id
        return None

    def _show_crop_selection_popup(self, selection_id, global_pos):
        if not CropInteractionMixin._activate_crop_selection(self, selection_id):
            return False
        self.crop_controller.popup.show_at(global_pos)
        return True

    def _activate_crop_context(self, spec, context):
        controller = self.__dict__.get("crop_controller")
        if controller is None:
            return
        controller.activate(spec.page_id if spec else None, context, e_flip=self.timeline_bar.switch_flip.isChecked())
        self._update_crop_cursor()
        _sync_crop_popup_visibility(self, context)

    def _update_crop_cursor(self):
        controller = self.crop_controller
        active = controller.enabled and controller.selection is not None
        for widget in (self.canvas_2d, self.plotter, self.plotter.interactor):
            if active:
                widget.setCursor(controller.cursor)
            else:
                widget.unsetCursor()

    def on_toggle_interactive_box(self, checked):
        self._toggle_region_mode("crop", checked)

    def on_toggle_erase(self, checked):
        self._toggle_region_mode("erase", checked)

    def _set_selection_mode(self, checked):
        controller = self.crop_controller
        checked = bool(checked)
        if checked:
            path_controller = self.__dict__.get("path_slice_controller")
            if path_controller is not None:
                path_controller.on_crop_toggled(True)
            clear_enhancement = getattr(self, "_clear_enhancement_interaction", None)
            if clear_enhancement is not None:
                clear_enhancement()
        else:
            for button in (self.btn_tb_crop, self.btn_tb_erase):
                button.blockSignals(True)
                button.setChecked(False)
                button.blockSignals(False)
            controller.popup.hide()
        controller.enabled = checked
        if checked and self.left_workspace.current_spec() is not None and self.current_render_context is not None:
            self._activate_crop_context(
                self.left_workspace.current_spec(), self.current_render_context
            )
        if checked and self._can_show_interactive_box():
            self._rebuild_interactive_box()
        else:
            self._restore_volume_opacity_if_dimmed()
            self._clear_interactive_box()
        self._refresh_axis_crop_interaction(
            self.left_workspace.current_spec(), self.current_render_context
        )
        self._update_crop_cursor()
        self.plotter.render()

    def on_selection_toggled(self, checked):
        self._set_selection_mode(checked)

    def _on_new_region_requested(self):
        selection_id = self.crop_controller.add_selection()
        if selection_id is None:
            return None
        if self._can_show_interactive_box():
            self._rebuild_interactive_box()
        self._refresh_axis_crop_interaction(
            self.left_workspace.current_spec(), self.current_render_context
        )
        self.plotter.render()
        return selection_id

    def _toggle_region_mode(self, operation, checked):
        overlay = self.__dict__.get("canvas_tool_overlay")
        selection_button = getattr(overlay, "selection_button", None)
        if checked:
            if selection_button is not None and not selection_button.isChecked():
                selection_button.setChecked(True)
            else:
                CropInteractionMixin._set_selection_mode(self, True)
            other = self.btn_tb_crop if operation == "erase" else self.btn_tb_erase
            other.blockSignals(True)
            other.setChecked(False)
            other.blockSignals(False)
            self.crop_controller.set_operation(operation)
        elif selection_button is None:
            CropInteractionMixin._set_selection_mode(self, False)
        self.crop_controller.popup.hide()
        if checked:
            _show_crop_popup_on_toggle(self, operation)

    def _activate_crop_selection(self, selection_id):
        controller = self.crop_controller
        if not hasattr(controller, "activate_selection"):
            return selection_id in (None, 0)
        if controller.active_selection_id != selection_id:
            if not controller.activate_selection(selection_id):
                return False
        elif selection_id not in dict(controller.selections_for_page()):
            return False
        if controller.selection.operation != controller.operation:
            controller.set_selection(
                replace(controller.selection, operation=controller.operation), notify=False
            )
        return True

    def _can_show_interactive_box(self):
        context = self.current_render_context
        return bool(self._crop_enabled() and context and context.get("view") == "3d"
                    and not context.get("crop_empty") and self.left_display_stack.currentIndex() == 0)

    def _crop_logical_bounds(self, selection=None):
        selection = self.crop_controller.selection if selection is None else selection
        if selection is None or selection.view != "3d":
            return None
        result = []
        for i, key in enumerate(selection.axes):
            # 3D 的 E 翻转只镜像相机与坐标轴标注，世界坐标与体素都不动，
            # 因此物理坐标到逻辑下标的换算不需要再镜像一次。
            indices = [self.core.physical_to_logical(key, v, clamp=False) for v in selection.bounds[2*i:2*i+2]]
            result.extend(sorted(indices))
        return result

    def _get_render_bounds_for_box(self, logical_bounds=None):
        shape = self._full_domain_spatial_shape()
        if shape is None:
            return None
        bounds = logical_bounds if logical_bounds is not None else self._crop_logical_bounds()
        if bounds is None:
            return None
        # Keep draft geometry outside the data. apply_selection intersects the
        # range with available samples only when the user applies the action.
        return self.core.logical_to_render_bounds(bounds, shape, clamp=False)

    def _sync_slice_edits_from_logical_bounds(self, logical_bounds=None, selection_id=None):
        if self.core.raw_data is None:
            return
        bounds = logical_bounds if logical_bounds is not None else self._get_full_logical_bounds()
        if bounds is None:
            return
        controller = self.__dict__.get("crop_controller")
        active_id = getattr(controller, "active_selection_id", None)
        if selection_id is None and controller is not None:
            selection_id = active_id
        selections = (
            dict(controller.selections_for_page())
            if controller is not None and hasattr(controller, "selections_for_page")
            else {}
        )
        selection = selections.get(selection_id)
        if selection is None and controller is not None and selection_id == active_id:
            selection = controller.selection
        is_active = controller is None or selection_id == active_id
        if is_active:
            self.precise_logical_bounds = list(bounds)
            self.last_synced_slice_texts = self._logical_bounds_to_texts(bounds)
        flipped = self.timeline_bar.switch_flip.isChecked()
        # E 翻转由相机镜像整个场景，逻辑下标与物理坐标一一对应，不再镜像。
        physical = self.core.logical_bounds_to_physical_bounds(bounds, clamp=False)
        physical = tuple(v for i in range(3) for v in sorted(physical[2*i:2*i+2]))
        if selection is not None and selection.view == "3d":
            try:
                updated = replace(selection, bounds=physical, e_flip=flipped)
            except ValueError:
                # A zero-thickness VTK handle gesture on multiple axes is not a
                # valid slice. Keep the last valid draft instead.
                return
            if selection_id is not None and hasattr(controller, "update_selection"):
                controller.update_selection(selection_id, updated, notify=False)
            elif is_active:
                controller.set_selection(updated, notify=False)

    def _sync_slice_edits_from_render_bounds(self, render_bounds, selection_id=None):
        shape = self._full_domain_spatial_shape()
        if shape is None:
            return
        bounds = self.core.render_to_logical_bounds(render_bounds, shape, clamp=False)
        self._sync_slice_edits_from_logical_bounds(bounds, selection_id=selection_id)

    def _update_slice_source_range(self, spec, *, axis_index=None):
        """Update only the normal range of a cropped slice, preserving its 2D tail."""
        interval = getattr(self, "axis_interval", None)
        if spec is None or interval is None:
            return False
        if axis_index is None:
            axis_index = self._spec_slice_axis(spec)
        if axis_index is None:
            return False
        axis_index = int(axis_index)
        coords = self._coords_for_axis(axis_index)
        low, up = interval.to_indices(coords)
        mid = int(np.clip(nearest_index(coords, interval.center), low, up))
        source_low = float(self.core.logical_to_physical(axis_index, low))
        source_up = float(self.core.logical_to_physical(axis_index, up))
        params = spec.params
        before = repr((params.get("home_slice_info"), params.get("crop_regions"),
                       params.get("precise_logical_bounds"), params.get("axis_interval")))
        params["home_slice_info"] = {
            "axis": axis_index, "index": mid, "mode": "integral", "range": (low, up),
        }
        params["axis_interval"] = interval.as_dict()

        regions = list(params.get("crop_regions") or [])
        first = regions[0] if regions else None
        if isinstance(first, dict) and first.get("view") == "3d":
            bounds = list(first.get("bounds") or [])
            if len(bounds) == 6:
                bounds[2 * axis_index:2 * axis_index + 2] = [source_low, source_up]
                params["crop_regions"] = [dict(first, bounds=tuple(bounds))] + regions[1:]

        precise = params.get("precise_logical_bounds")
        if precise is not None and len(precise) == 6:
            precise = list(precise)
            precise[2 * axis_index:2 * axis_index + 2] = [float(low), float(up)]
            params["precise_logical_bounds"] = precise
        after = repr((params.get("home_slice_info"), params.get("crop_regions"),
                      params.get("precise_logical_bounds"), params.get("axis_interval")))
        changed = before != after
        if changed and self._is_current_page(spec):
            self.home_slice_info = dict(params["home_slice_info"])
            if params.get("precise_logical_bounds") is not None:
                self.precise_logical_bounds = list(params["precise_logical_bounds"])
                self.last_synced_slice_texts = self._logical_bounds_to_texts(
                    self.precise_logical_bounds
                )
        return changed

    def _on_crop_selection_changed(self):
        selection = self.crop_controller.selection
        if selection is None:
            context = self.current_render_context
            if context is not None and context.get("view") == "3d":
                self._rebuild_interactive_box()
            else:
                self._refresh_axis_crop_interaction(
                    self.left_workspace.current_spec(), context
                )
            self.plotter.render()
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

    def _refresh_axis_crop_interaction(self, spec, context):
        self._clear_axis_crop_overlay(redraw=False)
        controller = self.crop_controller
        if hasattr(controller, "selections_for_page"):
            selections = list(controller.selections_for_page())
        else:
            selections = [(0, controller.selection)] if controller.selection is not None else []
        selectors = self.__dict__.setdefault("axis_crop_selectors", {})
        coordinator = self.__dict__.setdefault(
            "axis_crop_gesture_coordinator", CropBoxGestureCoordinator()
        )
        visible = self._crop_enabled() and self._supports_axis_crop_context(spec, context)
        wanted = {selection_id for selection_id, selection in selections
                  if visible and selection is not None and selection.view != "3d"}
        for selection_id in list(selectors):
            selector = selectors[selection_id]
            detached = (not selector.artists
                        or selector.artists[0] not in self.ax_2d.patches)
            if selection_id not in wanted or detached:
                selector.dispose()
                selectors.pop(selection_id, None)
        for selection_id, selection in selections:
            if selection_id not in wanted:
                continue
            label = (controller.selection_label(selection_id)
                     if hasattr(controller, "selection_label") else f"Z{selection_id}")
            selector = selectors.get(selection_id)
            if selector is None:
                selector = InteractiveCropBox(
                    self.ax_2d,
                    lambda eclick, erelease, sid=selection_id: self._on_axis_crop_selected(
                        eclick, erelease, selection_id=sid
                    ),
                    selection.bounds,
                    on_activate=lambda sid=selection_id: self._activate_crop_selection(sid),
                    coordinator=coordinator,
                    label=label,
                )
                selectors[selection_id] = selector
            else:
                selector.set_label(label)
                selector.sync_bounds(selection.bounds)
        coordinator.set_selectors(selectors.values())
        active_id = getattr(controller, "active_selection_id", None)
        self.axis_crop_selector = selectors.get(active_id)
        if self.axis_crop_selector is None and len(selectors) == 1:
            self.axis_crop_selector = next(iter(selectors.values()))
        if self.left_display_stack.currentIndex() == 1:
            self.canvas_2d.draw_idle()

    def _on_axis_crop_selected(self, eclick, erelease, selection_id=None):
        controller = self.crop_controller
        if selection_id is None:
            selection_id = getattr(controller, "active_selection_id", None)
        if hasattr(controller, "selections_for_page"):
            selection = dict(controller.selections_for_page()).get(selection_id)
        else:
            selection = controller.selection
        if selection is None or selection.view == "3d":
            return
        values = (eclick.xdata, erelease.xdata, eclick.ydata, erelease.ydata)
        if any(value is None for value in values):
            return
        bounds = (*sorted(values[:2]), *sorted(values[2:]))
        try:
            updated = replace(selection, bounds=bounds)
        except ValueError:
            return
        if selection_id is not None and hasattr(controller, "update_selection"):
            controller.update_selection(selection_id, updated)
        else:
            controller.set_selection(updated)

    def on_cut(self):
        controller = self.crop_controller
        source = self.left_workspace.current_spec()
        selection = controller.selection
        if source is None or selection is None or source.page_kind == "control_panel":
            return
        erasing = selection.operation == "erase"
        action = "裁空" if erasing else "裁剪"
        if not self._render_exact_ready:
            controller.popup.set_error(f"请等待当前结果计算完成后{action}。")
            return
        context = self._compute_render_context(source)
        if context is None:
            return
        rotation_angle = float(context.get(
            "rotation_angle", self.__dict__.get("rotation_angle", 0.0)
        ) or 0.0)
        if selection.view in {"3d", "2d"}:
            selection = capture_selection_geometry(
                context, selection, rotation_angle=rotation_angle
            )
            cropped = apply_selection(context, selection, rotation_angle=rotation_angle)
        else:
            cropped = apply_selection(context, selection)
        if cropped.get("crop_empty") and not erasing:
            controller.popup.set_error("裁剪范围内没有有效数据。")
            return
        home_volume_crop = (not erasing and not source.params.get("crop_regions")
                            and source.page_kind == "home" and selection.view == "3d"
                            and cropped["view"] == "3d")
        volume_roi = home_volume_crop and abs(rotation_angle) < 1e-6
        self._persist_active_page_state()
        params = copy.deepcopy(source.params)
        kind = source.page_kind
        scope_id = source.data_scope_id
        if home_volume_crop:
            bounds = tuple(int(v) for v in cropped["data_bounds"])
            candidate = DataScopeDescriptor("candidate", tuple(self.original_raw_data.shape), bounds)
            try:
                self._validate_denoise_for_descriptor(self.global_denoise_methods, candidate)
            except ValueError as exc:
                controller.popup.set_error(str(exc))
                return
        if volume_roi:
            descriptor = self._register_roi_scope(bounds)
            scope_id = descriptor.scope_id
            params.update(clip_ranges=list(bounds), home_slice_info=None,
                          precise_logical_bounds=list(bounds), data_scope_label=descriptor.label)
            params.pop("crop_regions", None)
            params.pop("local_crop_denoise", None)
        else:
            if home_volume_crop and not volume_roi:
                params["local_crop_denoise"] = True
            regions = list(params.get("crop_regions", []))
            if kind in {"axis_integral", "axis_integral_crop"}:
                if not regions:
                    params["crop_base_rect"] = self._axis_crop_rect_from_params(source.params) if kind == "axis_integral_crop" else None
                if not erasing:
                    kind = "axis_integral_crop"
                    rect = cropped["plot_logical_bounds"]
                    params.update(crop_k_low=rect["x_low"], crop_k_up=rect["x_up"], crop_e_low=rect["y_low"], crop_e_up=rect["y_up"])
            if selection.view == "3d" and selection.e_flip:
                # Persist canonical physical coordinates so slice-position
                # controls and subsequent pages use the original axis domain.
                logical = selection.logical_bounds or self._crop_logical_bounds(selection)
                physical = self.core.logical_bounds_to_physical_bounds(logical, clamp=False)
                physical = tuple(v for i in range(3) for v in sorted(physical[2*i:2*i+2]))
                selection = replace(selection, bounds=physical, e_flip=False)
            regions.append(selection.to_dict())
            params["crop_regions"] = regions
        title = self._make_unique_page_title(f"{action} - {source.title}")
        spec = AnalysisPageSpec(self._make_page_id(), title, kind, source.source_module,
                                params=params, source_page_id=source.page_id, source_title=source.title,
                                data_scope_id=scope_id)
        if not erasing and selection.view == "3d" and cropped.get("view") == "2d":
            info = cropped.get("slice_info") or {}
            axis_index = int(info.get("axis", self._spec_slice_axis(spec)))
            index = int(info.get("index", 0))
            self._seed_axis_interval_params(
                spec, axis_index=axis_index, low=index, up=index, mid=index, locked=True,
            )
            params["home_slice_info"] = {
                "axis": axis_index, "index": index, "mode": "integral", "range": (index, index),
            }
        controller.popup.hide()
        self.left_workspace.add_page(spec)
        if volume_roi:
            self._invalidate_scope_render_state()
            self._request_scope_denoise_if_needed(spec)
            self.global_refresh()
