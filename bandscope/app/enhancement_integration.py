"""Host-owned multi-region display controls and interaction lifecycle."""
import copy
import uuid

import numpy as np
from PyQt5.QtCore import QEvent, QSignalBlocker, QTimer, Qt
from PyQt5.QtWidgets import QInputDialog
from matplotlib.patches import Rectangle

from bandscope.core.local_enhancement import (
    DEFAULT_STYLE, STATE_KEY, EnhancementRegion, adapt_regions, defer_range_locks,
    geometry, logical_bounds, matching_regions, physical_bounds, region_slices, update_region_bounds,
)
from bandscope.core.data_scope import DataScopeDescriptor
from bandscope.processing.local_enhancement import resolve_display
from bandscope.rendering.local_enhancement import enhancement_legend
from bandscope.rendering.refresh_pipeline import RefreshCause, RenderQuality
from bandscope.rendering.render_core import VisualEngine
from bandscope.ui import theme
from bandscope.ui.control_layout_utils import combo_index_for_text, sync_slider_visual
from bandscope.ui.enhancement_controls import EnhancementBoundsDialog
from bandscope.ui.interactive_crop_box import InteractiveCropBox


class EnhancementInteractionMixin:
    def _init_enhancement(self):
        self._enhancement_selector = None
        self._enhancement_box_widget = None
        self._enhancement_artists = []
        self._enhancement_actors = []
        self._enhancement_cache = None
        self._enhancement_draw_key = None
        self._enhancement_dragging = False
        self._enhancement_actor_regions = {}
        self.plotter.iren.add_observer("LeftButtonPressEvent", self._pick_enhancement_box)
        controls = self.page_render.enhancement_controls
        controls.target_changed.connect(self._select_enhancement_target)
        controls.delete_requested.connect(self._delete_enhancement_region)
        controls.rename_requested.connect(self._rename_enhancement_region)
        controls.bounds_requested.connect(self._edit_enhancement_bounds)
        controls.common_changed.connect(self._set_enhancement_common)
        controls.locked_changed.connect(self._set_enhancement_locked)
        self.canvas_2d.mpl_connect("button_press_event", self._pick_enhancement_region)

    def _enhancement_spec(self):
        workspace = self.__dict__.get("left_workspace")
        return self.__dict__.get("active_page_spec") or (workspace.current_spec() if workspace else None)

    def _enhancement_state(self, spec=None):
        spec = spec or self._enhancement_spec()
        state = spec.params.get(STATE_KEY) if spec is not None else None
        if state:
            changed = False
            for region in state.get("regions", []):
                if not region.get("enabled", True):
                    region["enabled"] = True
                    changed = True
            if changed:
                state["revision"] = state.get("revision", 0) + 1
        return state

    def _inherit_enhancement_state(self, spec):
        if spec.page_kind not in {"home", "time_integral", "axis_integral", "axis_integral_crop", "second_derivative"} or STATE_KEY in spec.params:
            return
        workspace = self.__dict__.get("left_workspace")
        source = self._enhancement_spec()
        state = self._enhancement_state(source) if source is not None else None
        if not state and workspace is not None and hasattr(workspace, "page_by_id"):
            source = workspace.page_by_id(spec.source_page_id)
            state = self._enhancement_state(source) if source is not None else None
        if not state or source is spec:
            return
        if source is self._enhancement_spec() and "_enhancement_artists" in self.__dict__:
            self._commit_enhancement_controls()
        inherited = copy.deepcopy(state)
        defer_range_locks(inherited)
        spec.params[STATE_KEY] = inherited

    def _enhancement_ui_style(self):
        return {"levels": tuple(float(s.value()) for s in (self.page_render.s_low, self.page_render.s_gamma, self.page_render.s_up)),
                "opacity_mode": str(self.page_render.combo_map.currentText()) if hasattr(self.page_render, "combo_map") else "线性"}

    def _enhancement_background_style(self):
        state = self._enhancement_state()
        return state["background"] if state else self._enhancement_ui_style()

    def _enhancement_selected(self):
        state = self._enhancement_state()
        return next((r for r in state["regions"] if r["region_id"] == state.get("selected_id")), None) if state else None

    def _commit_enhancement_controls(self):
        state = self._enhancement_state()
        if state is None or getattr(self, "_syncing_controls", False):
            return False
        region = self._enhancement_selected()
        target = state if region and state.get("common") else region or state
        key = "common_style" if region and state.get("common") else "style" if region else "background"
        style = self._enhancement_ui_style()
        if target.get(key) != style:
            target[key] = style
            state["revision"] += 1
        return True

    def _enhancement_export_render_state(self, render_state):
        state = self._enhancement_state()
        if state:
            self._commit_enhancement_controls()
            style = state["background"]
            for key, value in zip(("s_low", "s_gamma", "s_up"), style["levels"]):
                render_state[key] = dict(render_state[key], value=int(value))
            render_state["combo_map"] = style["opacity_mode"]
        return render_state

    def _restore_enhancement_target_style(self):
        state = self._enhancement_state()
        if not state:
            return
        region = self._enhancement_selected()
        style = state.get("common_style", DEFAULT_STYLE) if region and state.get("common") else region.get("style", DEFAULT_STYLE) if region else state["background"]
        widgets = (self.page_render.s_low, self.page_render.s_gamma, self.page_render.s_up, self.page_render.combo_map)
        blockers = [QSignalBlocker(w) for w in widgets]
        for slider, value in zip(widgets[:3], style["levels"]):
            slider.setValue(int(value))
            sync_slider_visual(slider)
        index = combo_index_for_text(widgets[3], style["opacity_mode"])
        if index >= 0:
            widgets[3].setCurrentIndex(index)
        del blockers

    def _enhancement_refresh(self, *, interactive=False):
        self._enhancement_cache = None
        self.request_refresh(RefreshCause.DISPLAY_MAPPING, RenderQuality.EXACT,
                             interactive=interactive, immediate=not interactive)

    def _release_background_lock(self):
        VisualEngine.unlock_data_range()
        button = getattr(self, "btn_tb_lock", None)
        if button is not None:
            blocker = QSignalBlocker(button)
            button.setChecked(False)
            del blocker
            self._update_color_lock_button_style()

    def _select_enhancement_target(self, region_id):
        state = self._enhancement_state()
        if state is None:
            return
        self._commit_enhancement_controls()
        state["selected_id"] = region_id or None
        self._restore_enhancement_target_style()
        self._clear_enhancement_interaction()
        self._sync_enhancement_controls(self._enhancement_spec(), self.current_render_context)
        self._draw_enhancement_regions(self.current_render_context, self._enhancement_display(self.current_render_context))

    def _add_enhancement_region(self):
        context = self.current_render_context
        spec = self.left_workspace.current_spec()
        if spec is None or context is None or context.get("view") not in {"2d", "3d"} or context.get("crop_empty"):
            return
        if not self._render_exact_ready:
            self._show_message("局部增强", "请等待当前结果计算完成。")
            return
        self._commit_enhancement_controls()
        for button in (self.btn_tb_crop, self.btn_tb_erase):
            button.setChecked(False)
        self._clear_enhancement_interaction()
        state = spec.params.get(STATE_KEY)
        if state is None:
            state = {"regions": [], "background": self._enhancement_ui_style(), "revision": 0, "next_label": 1, "common": False}
            spec.params[STATE_KEY] = state
        axes, domain = geometry(context)
        bounds = []
        for low, high in zip(domain[::2], domain[1::2]):
            center, half = (low + high) / 2, max((high - low) / 6, 1e-6)
            bounds.extend((center - half, center + half))
        number = state["next_label"]
        state["next_label"] += 1
        region = EnhancementRegion(uuid.uuid4().hex, f"区域 {number}", context["view"], axes, tuple(bounds)).to_dict()
        region.update(style=copy.deepcopy(self._enhancement_ui_style()), label=f"R{number}")
        state["regions"].append(region)
        state["selected_id"] = region["region_id"]
        state["revision"] += 1
        self._restore_enhancement_target_style()
        self._enhancement_refresh()
        self._select_control_page(0)

    def _delete_enhancement_region(self, region_id):
        state = self._enhancement_state()
        if not state:
            return
        index = next((i for i, r in enumerate(state["regions"]) if r["region_id"] == region_id), None)
        if index is None:
            return
        self._commit_enhancement_controls()
        state["regions"].pop(index)
        if state.get("selected_id") == region_id:
            state["selected_id"] = state["regions"][min(index, len(state["regions"]) - 1)]["region_id"] if state["regions"] else None
        state["revision"] += 1
        self._release_background_lock()
        self._clear_enhancement_interaction()
        self._restore_enhancement_target_style()
        self._enhancement_refresh()

    def _rename_enhancement_region(self):
        region = self._enhancement_selected()
        if region:
            name, accepted = QInputDialog.getText(self, "重命名增强区域", "名称", text=region["name"])
            if accepted and name.strip():
                region["name"] = name.strip()
                self._enhancement_state()["revision"] += 1
                self._enhancement_refresh()

    def _edit_enhancement_bounds(self):
        region = self._enhancement_selected()
        context = self.current_render_context
        if not region or not context:
            return
        flip = self.timeline_bar.switch_flip.isChecked()
        dialog = EnhancementBoundsDialog(region["axes"], physical_bounds(context, region["bounds"], e_flip=flip), self)
        if dialog.exec_():
            update_region_bounds(region, logical_bounds(context, dialog.bounds, e_flip=flip))
            self._enhancement_state()["revision"] += 1
            self._clear_enhancement_interaction()
            self._enhancement_refresh()

    def _set_enhancement_common(self, enabled):
        state = self._enhancement_state()
        if not state:
            return
        self._commit_enhancement_controls()
        state["common"] = bool(enabled)
        state.setdefault("common_style", copy.deepcopy(self._enhancement_selected().get("style", DEFAULT_STYLE) if self._enhancement_selected() else DEFAULT_STYLE))
        state["revision"] += 1
        self._restore_enhancement_target_style()
        self._enhancement_refresh()

    def _set_enhancement_locked(self, locked):
        state, region = self._enhancement_state(), self._enhancement_selected()
        if not state or not region:
            return
        fields = self._enhancement_display(self.current_render_context)
        resolved = next((r for r in fields["regions"] if r["region_id"] == region["region_id"]), None) if fields else None
        target, key = (state, "common_locked_range") if state.get("common") else (region, "locked_range")
        pending = "common_lock_pending" if state.get("common") else "lock_pending"
        data_range = resolved["data_range"] if resolved is not None else None
        if locked and (data_range is None or data_range[1] <= data_range[0]):
            self._show_message("局部增强", "当前区域没有可锁定的有效强度范围。")
        else:
            target.pop(pending, None)
            target[key] = tuple(data_range) if locked else None
            state["revision"] += 1
        self._enhancement_refresh()

    def _enhancement_crop_data(self, context, spec, state, *, allow_compute):
        """Use the same raw ROI and denoise job as a first home-volume crop.

        Other crop paths slice the already computed scientific result. Display
        ROI processing is cached separately and never changes page/science data.
        """
        can_scope = (context["view"] == "3d" and getattr(spec, "page_kind", None) == "home"
                     and not spec.params.get("crop_regions")
                     and abs(float(self.__dict__.get("rotation_angle", 0))) < 1e-6)
        context = dict(context, enhancement_include_zero=bool(context.get("include_zero") or can_scope))
        methods = self.__dict__.get("global_denoise_methods", [])
        original = self.__dict__.get("original_raw_data")
        if not can_scope or not methods or original is None:
            return context, {}
        cache = self.__dict__.setdefault("_enhancement_roi_data_cache", {})
        current_ids = {r["region_id"] for r in state["regions"]}
        for rid in list(cache):
            if rid not in current_ids:
                cache.pop(rid)
        _, domain = geometry(context)
        data, errors = {}, {}
        for region in matching_regions(context, state["regions"]):
            if not region.get("enabled", True):
                continue
            slices = region_slices(context, region)
            if slices is None:
                continue
            bounds = tuple(v + int(domain[2*i]) for i, s in enumerate(slices) for v in (s.start, s.stop-1))
            key = (id(original), tuple(bounds), repr(methods), self.__dict__.get("shared_denoise_version", 0))
            entry = cache.get(region["region_id"])
            if entry is None or entry["key"] != key:
                if not allow_compute:
                    continue
                descriptor = DataScopeDescriptor("enhancement:" + region["region_id"], original.shape, bounds)
                try:
                    self._validate_denoise_for_descriptor(methods, descriptor)
                    result = self.backend_manager.execute(self._build_denoise_job(descriptor, methods))
                    entry = dict(key=key, values=result.value)
                except ValueError as exc:
                    entry = dict(key=key, error=str(exc))
                cache[region["region_id"]] = entry
            if entry.get("error"):
                errors[region["region_id"]] = entry["error"]
                entry.setdefault("invalid", np.full(context["data"][slices].shape, np.nan, dtype=np.float32))
                data[region["region_id"]] = entry["invalid"]
            else:
                index = int(self.timeline_bar.slider_time.value())
                if entry.get("frame_index") != index:
                    entry.update(frame_index=index, frame=entry["values"][..., index])
                data[region["region_id"]] = entry["frame"]
        context["enhancement_errors"] = errors
        return context, data

    def _enhancement_display(self, context, spec=None, *, freeze_pending=True):
        state = self._enhancement_state(spec)
        if not state or context is None or context.get("view") not in {"2d", "3d"} or context.get("crop_empty"):
            return None
        adapt_regions(context, state)
        context, region_data = self._enhancement_crop_data(context, spec or self._enhancement_spec(), state,
                                                         allow_compute=freeze_pending)
        if freeze_pending:
            pending_fields = None
            pending = (state.get("common_lock_pending") if state.get("common") else
                       any(r.get("lock_pending") for r in state["regions"]))
            if pending:
                pending_fields = resolve_display(context, state, region_data=region_data)
            if pending_fields:
                for record in pending_fields["regions"]:
                    if record["status"] != "ready":
                        continue
                    region = next(r for r in state["regions"] if r["region_id"] == record["region_id"])
                    if not state.get("common") and region.pop("lock_pending", False):
                        region["locked_range"] = tuple(record["data_range"])
                        state["revision"] += 1
                    if state.get("common") and state.pop("common_lock_pending", False):
                        state["common_locked_range"] = tuple(record["data_range"])
                        state["revision"] += 1
        data = np.asarray(context["data"])
        key = (id(data), int(data.__array_interface__["data"][0]), data.shape,
               self.__dict__.get("shared_denoise_version", 0), self.__dict__.get("rotation_angle", 0),
               repr(context.get("slice_info")), repr(context.get("plot_logical_bounds")),
               repr(context.get("data_bounds")),
               context.get("enhancement_include_zero"), tuple((k, id(v)) for k, v in region_data.items()),
               repr({k: v for k, v in state.items() if k != "show_all_regions"}),
               VisualEngine.locked_data_range())
        cached = self.__dict__.get("_enhancement_cache")
        if cached is None or cached[0] != key:
            cached = (key, resolve_display(context, state, background_locked_range=VisualEngine.locked_data_range(),
                                          region_data=region_data))
            self._enhancement_cache = cached
        if cached[1] is not None and VisualEngine.locked_data_range() is None:
            info = cached[1]["background_level_info"]
            VisualEngine._last_data_range = (info["data_min"], info["data_max"])
        return cached[1]

    def _sync_enhancement_controls(self, spec, context=None):
        if "_enhancement_artists" not in self.__dict__:
            return
        state = self._enhancement_state(spec)
        fields = self._enhancement_display(context, spec, freeze_pending=False)
        regions = matching_regions(context, state["regions"]) if state and context else []
        if state and context and state.get("selected_id") not in {r["region_id"] for r in regions}:
            state["selected_id"] = None
            self._restore_enhancement_target_style()
        region = self._enhancement_selected() if state else None
        resolved = next((r for r in fields["regions"] if region and r["region_id"] == region["region_id"]), None) if fields else None
        summary = "后添加区域优先；背景保持原图映射，删除后恢复背景。"
        if resolved:
            status = {"empty": "没有有效样本", "constant": "无动态范围", "disabled": "已停用"}.get(resolved["status"])
            data_range = resolved["data_range"]
            summary = resolved.get("error") or status or f"强度范围：{data_range[0]:.4g} ～ {data_range[1]:.4g}"
            if state.get("common"):
                summary += "；当前色阶和透明度设置作用于全部增强区域。"
        self.page_render.enhancement_controls.sync(regions, state.get("selected_id") if state else None,
            common=bool(state and state.get("common")),
            locked=bool(state and ((state.get("common_locked_range") or state.get("common_lock_pending")) if state.get("common") else region and (region.get("locked_range") or region.get("lock_pending")))), summary=summary)
        self.page_render.set_enhancement_controls_visible(bool(regions))

    def _append_enhancement_actions(self, menu):
        context = self.current_render_context
        if context and context.get("view") in {"2d", "3d"} and not context.get("crop_empty"):
            menu.addSeparator()
            action = menu.addAction("添加局部增强区域")
            action.triggered.connect(self._add_enhancement_region)
            state = self._enhancement_state()
            show_all = menu.addAction("显示全部增强区域")
            show_all.setCheckable(True)
            show_all.setChecked(bool(state and state.get("show_all_regions")))
            show_all.setEnabled(bool(state and matching_regions(context, state["regions"])))
            show_all.toggled.connect(self._set_enhancement_show_all)
            return True
        return False

    def _set_enhancement_show_all(self, enabled):
        state = self._enhancement_state()
        if state is None:
            return
        state["show_all_regions"] = bool(enabled)
        context = self.current_render_context
        self._draw_enhancement_regions(context, self._enhancement_display(context))

    def _enhancement_outline_regions(self, regions):
        """Outline visibility is independent of all active enhancement channels."""
        state = self._enhancement_state()
        if state and state.get("show_all_regions"):
            return regions
        selected_id = state.get("selected_id") if state else None
        return [r for r in regions if r["region_id"] == selected_id]

    def _reset_enhancement_for_new_data(self):
        self._clear_enhancement_interaction()
        self._enhancement_cache = None
        self._enhancement_roi_data_cache = {}
        workspace = self.__dict__.get("left_workspace")
        pages = getattr(workspace, "page_specs", {}) if workspace is not None else {}
        if isinstance(pages, dict):
            for spec in pages.values():
                spec.params.pop(STATE_KEY, None)
        spec = self._enhancement_spec()
        if spec is not None:
            spec.params.pop(STATE_KEY, None)
        page = self.__dict__.get("page_render")
        if page is not None and hasattr(page, "set_enhancement_controls_visible"):
            page.set_enhancement_controls_visible(False, animate=False)

    def _clear_enhancement_interaction(self):
        selector = self.__dict__.get("_enhancement_selector")
        if selector is not None:
            selector.dispose()
            self._enhancement_selector = None
        widget = self.__dict__.get("_enhancement_box_widget")
        if widget is not None:
            widget.Off()
            if widget in self.plotter.box_widgets:
                self.plotter.box_widgets.remove(widget)
            self._enhancement_box_widget = None
        for artist in self.__dict__.get("_enhancement_artists", []):
            if artist.axes is not None:
                artist.remove()
        self._enhancement_artists = []
        for name in self.__dict__.get("_enhancement_actors", []):
            self.plotter.remove_actor(name, render=False)
        self._enhancement_actors = []
        self._enhancement_draw_key = None

    def _enhancement_blit_artists(self):
        artists = list(self.__dict__.get("_enhancement_artists", []))
        selector = self.__dict__.get("_enhancement_selector")
        if selector is not None:
            artists.extend(selector.artists)
        return artists

    def _draw_enhancement_regions(self, context, fields):
        if "_enhancement_artists" not in self.__dict__:
            return
        if fields is None or self._crop_enabled():
            self._clear_enhancement_interaction()
            return
        state = self._enhancement_state()
        active = self._enhancement_selected()
        fields = dict(fields, regions=self._enhancement_outline_regions(fields["regions"]))
        if context["view"] == "3d":
            self._draw_enhancement_boxes(context, fields, active)
            return
        key = (state.get("selected_id"), id(self.ax_2d._arpes_image), self.timeline_bar.switch_flip.isChecked())
        if key != self._enhancement_draw_key:
            self._clear_enhancement_interaction()
            self._enhancement_draw_key = key
            if active:
                bounds = physical_bounds(context, active["bounds"], e_flip=self.timeline_bar.switch_flip.isChecked())
                self._enhancement_selector = InteractiveCropBox(self.ax_2d, self._on_enhancement_rectangle, bounds)
        for artist in self._enhancement_artists:
            if artist.axes is not None:
                artist.remove()
        self._enhancement_artists = []
        for region in fields["regions"]:
            bounds = physical_bounds(context, region["bounds"], e_flip=self.timeline_bar.switch_flip.isChecked())
            x0, x1, y0, y1 = bounds
            patch = Rectangle((x0, y0), x1-x0, y1-y0, fill=False, edgecolor=theme.ACCENT,
                              linewidth=1.2, linestyle="--" if not region["enabled"] else "-", zorder=80)
            self.ax_2d.add_patch(patch)
            label = self.ax_2d.text(x0, y1, region.get("label", region["name"]), color=theme.ACCENT, zorder=90, clip_on=True)
            self._enhancement_artists.extend((patch, label))
        if fields["regions"]:
            note = self.ax_2d.text(.01, .01, enhancement_legend(fields), transform=self.ax_2d.transAxes,
                                  color=theme.TEXT_2, fontsize=8, zorder=90, va="bottom")
            self._enhancement_artists.append(note)
        if self._enhancement_selector is not None and active:
            self._enhancement_selector.sync_bounds(physical_bounds(context, active["bounds"], e_flip=self.timeline_bar.switch_flip.isChecked()))
        self.canvas_2d.draw_idle()

    def _on_enhancement_rectangle(self, press, release):
        region = self._enhancement_selected()
        if region and all(v is not None for v in (press.xdata, release.xdata, press.ydata, release.ydata)):
            physical = (*sorted((press.xdata, release.xdata)), *sorted((press.ydata, release.ydata)))
            update_region_bounds(region, logical_bounds(self.current_render_context, physical, e_flip=self.timeline_bar.switch_flip.isChecked()))
            self._enhancement_state()["revision"] += 1
            QTimer.singleShot(0, self._enhancement_refresh)

    def _pick_enhancement_region(self, event):
        if self._crop_enabled() or event.inaxes is not getattr(self, "ax_2d", None) or event.button != 1 or event.xdata is None:
            return
        state = self._enhancement_state()
        context = self.current_render_context
        if not state or not context or context.get("view") != "2d":
            return
        for region in reversed(self._enhancement_outline_regions(matching_regions(context, state["regions"]))):
            x0, x1, y0, y1 = physical_bounds(context, region["bounds"], e_flip=self.timeline_bar.switch_flip.isChecked())
            if x0 <= event.xdata <= x1 and y0 <= event.ydata <= y1:
                if region["region_id"] != state.get("selected_id"):
                    QTimer.singleShot(0, lambda rid=region["region_id"]: self._select_enhancement_target(rid))
                break

    def _draw_enhancement_boxes(self, context, fields, active):
        self._enhancement_actor_regions = {}
        for name in self._enhancement_actors:
            self.plotter.remove_actor(name, render=False)
        self._enhancement_actors = []
        shape = context.get("full_shape", context["data"].shape)
        for region in fields["regions"]:
            bounds = self.core.logical_to_render_bounds(region["bounds"], shape, clamp=False)
            name = "enhancement:" + region["region_id"]
            import pyvista as pv
            actor = self.plotter.add_mesh(pv.Box(bounds=bounds).outline(), color=theme.ACCENT, name=name, render=False)
            self._enhancement_actor_regions[actor] = region["region_id"]
            self.plotter.add_point_labels([[(bounds[0]+bounds[1])/2, (bounds[2]+bounds[3])/2, bounds[5]]],
                                         [region.get("label", "ROI")], name=name+":label", show_points=False,
                                         font_size=12, always_visible=True, render=False)
            self._enhancement_actors.extend((name, name+":label"))
        if fields["regions"]:
            self.plotter.add_text(enhancement_legend(fields), name="enhancement:legend", position="lower_left", font_size=9, render=False)
            self._enhancement_actors.append("enhancement:legend")
        key = (active and active["region_id"], active and tuple(active["bounds"]))
        if key != self._enhancement_draw_key and not self._enhancement_dragging:
            widget = self._enhancement_box_widget
            if widget is not None:
                widget.Off()
                if widget in self.plotter.box_widgets:
                    self.plotter.box_widgets.remove(widget)
            self._enhancement_box_widget = None
            self._enhancement_draw_key = key
            if active:
                self._enhancement_building = True
                try:
                    self._enhancement_box_widget = self.plotter.add_box_widget(
                        self._on_enhancement_box, bounds=self.core.logical_to_render_bounds(active["bounds"], shape, clamp=False),
                        factor=1, rotation_enabled=False, color=theme.ACCENT, interaction_event="always")
                    self._enhancement_box_widget.AddObserver("StartInteractionEvent", self._start_enhancement_box)
                    self._enhancement_box_widget.AddObserver("EndInteractionEvent", self._finish_enhancement_box)
                finally:
                    self._enhancement_building = False
        self.plotter.render()

    def _pick_enhancement_box(self, _obj, _event):
        if self._crop_enabled() or self._enhancement_dragging or not self.current_render_context or self.current_render_context.get("view") != "3d":
            return
        import vtk
        x, y = self.plotter.iren.interactor.GetEventPosition()
        picker = vtk.vtkPropPicker()
        picker.Pick(x, y, 0, self.plotter.renderer)
        region_id = self._enhancement_actor_regions.get(picker.GetActor())
        if region_id and region_id != self._enhancement_state().get("selected_id"):
            QTimer.singleShot(0, lambda: self._select_enhancement_target(region_id))

    def _start_enhancement_box(self, _obj, _event):
        self._enhancement_dragging = True

    def _finish_enhancement_box(self, _obj, _event):
        self._enhancement_dragging = False
        QTimer.singleShot(0, self._enhancement_refresh)

    def _on_enhancement_box(self, poly):
        if getattr(self, "_enhancement_building", False):
            return
        region = self._enhancement_selected()
        if region:
            context = self.current_render_context
            shape = context.get("full_shape", context["data"].shape)
            update_region_bounds(region, tuple(self.core.render_to_logical_bounds(poly.bounds, shape, clamp=False)))
            self._enhancement_state()["revision"] += 1
            self._enhancement_refresh(interactive=True)

    def _enhancement_event_filter(self, watched, event):
        if "_enhancement_artists" not in self.__dict__:
            return False
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape and self._enhancement_selected() is not None:
            if watched in (self.canvas_2d, self.plotter, self.plotter.interactor):
                self._select_enhancement_target("")
                return True
        return False
