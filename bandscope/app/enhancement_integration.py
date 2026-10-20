"""Host-owned multi-region display controls and interaction lifecycle."""
import copy
import threading
import uuid

import numpy as np
from PyQt5.QtCore import QEvent, QRunnable, QSignalBlocker, QThreadPool, QTimer, Qt, pyqtSlot
from PyQt5.QtWidgets import QAction
from matplotlib.patches import Rectangle

from bandscope.core.local_enhancement import (
    DEFAULT_STYLE, STATE_KEY, EnhancementRegion, adapt_regions, defer_range_locks,
    geometry, logical_bounds, matching_regions, physical_bounds, region_slices, update_region_bounds,
)
from bandscope.core.data_scope import DataScopeDescriptor
from bandscope.processing.local_enhancement import resolve_display
from bandscope.rendering.local_enhancement import (
    add_region_scalar_bars_3d, draw_region_colorbars_2d, enhancement_legend,
)
from bandscope.rendering.refresh_pipeline import ComputeJob, RefreshCause, RenderQuality
from bandscope.rendering.render_core import VisualEngine
from bandscope.ui import theme
from bandscope.ui.control_layout_utils import combo_index_for_text, sync_slider_visual
from bandscope.ui.enhancement_controls import SHOW_ALL_REGIONS_ID
from bandscope.ui.interactive_crop_box import InteractiveCropBox


def _convex_hull(points):
    """2D monotonic-chain hull (counter-clockwise) for projected box corners."""
    points = sorted(set(points))
    if len(points) <= 1:
        return points

    def cross(origin, a, b):
        return ((a[0] - origin[0]) * (b[1] - origin[1])
                - (a[1] - origin[1]) * (b[0] - origin[0]))

    lower = []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _point_in_convex_polygon(x, y, polygon):
    if len(polygon) < 3:
        return False
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(index + 1) % len(polygon)]
        if (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1) < -1e-6:
            return False
    return True


class _EnhancementRoiTask(QRunnable):
    """Run the ROI denoise pipeline off the GUI thread and park the outcome.

    The poll timer in the host moves the result back to the GUI thread; no Qt
    object other than the thread pool is touched from here.
    """

    def __init__(self, host, token, source_id, version, requests, methods, original):
        super().__init__()
        self.host = host
        self.token = token
        self.source_id = source_id
        self.version = version
        self.requests = list(requests)
        self.methods = list(methods)
        self.original = original

    def _run_requests(self):
        backend = self.host.backend_manager
        validate = self.host._validate_denoise_for_descriptor
        results = {}
        for region_id, bounds, key in self.requests:
            descriptor = DataScopeDescriptor("enhancement:" + region_id, self.original.shape, bounds)
            try:
                validate(self.methods, descriptor)
                job = ComputeJob("denoise_pipeline", {
                    "data": descriptor.extract(self.original),
                    "methods": copy.deepcopy(self.methods),
                })
                results[region_id] = {"key": key, "values": backend.execute(job).value}
            except ValueError as exc:
                results[region_id] = {"key": key, "error": str(exc)}
        return results

    @pyqtSlot()
    def run(self):
        try:
            outcome = ("ok", self._run_requests())
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            outcome = ("error", f"{type(exc).__name__}: {exc}")
        with self.host._enhancement_roi_lock:
            self.host._enhancement_roi_result = (self.token, self.source_id, self.version, outcome)


class EnhancementInteractionMixin:
    def _init_enhancement(self):
        self._enhancement_selector = None
        self._enhancement_box_widget = None
        self._enhancement_artists = []
        self._enhancement_actors = []
        self._enhancement_colorbars = []
        self._enhancement_scalar_bars = []
        self._enhancement_scalar_bar_records = []
        self._enhancement_cache = None
        self._enhancement_draw_key = None
        self._enhancement_box_signature = None
        self._enhancement_bar_signature = None
        self._enhancement_dragging = False
        self._enhancement_actor_regions = {}
        self._enhancement_roi_pool = None
        self._enhancement_roi_timer = None
        self._enhancement_roi_lock = threading.Lock()
        self._enhancement_roi_token = 0
        self._enhancement_roi_job = None
        self._enhancement_roi_task = None
        self._enhancement_roi_result = None
        self._enhancement_roi_disabled = False
        # 左侧视图右键命中的区域：菜单据此提供「删除当前区域」。
        self._enhancement_context_target = None
        # 色带复制/粘贴：剪贴板只保存色阶映射（不含范围、锁定与透明度映射），
        # 会话内有效；右键命中的色带决定粘贴目标。
        self._color_band_clipboard = None
        self._band_context_target = None
        self.plotter.iren.add_observer("LeftButtonPressEvent", self._pick_enhancement_box)
        self.plotter.iren.add_observer("RightButtonPressEvent", self._remember_enhancement_box_context)
        combo = self.page_render.region_combo
        combo.target_changed.connect(self._select_enhancement_target)
        combo.rename_committed.connect(self._rename_enhancement_region)
        self.page_render.btn_lock.toggled.connect(self.on_toggle_color_lock)
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

    def _commit_enhancement_controls(self, *, apply_all=False):
        """写入当前对象；「全部显示」下实际调节时统一写入背景和全部区域。"""
        state = self._enhancement_state()
        if state is None or getattr(self, "_syncing_controls", False):
            return False
        style = self._enhancement_ui_style()
        if state.get("show_all_regions"):
            # 查看全部色带、切页和保存本身不能抹平各对象原有的独立设置。
            if not apply_all and style == self.__dict__.get("_enhancement_restored_style", style):
                return True
            targets = [(state, "background")] + [(r, "style") for r in state["regions"]]
            if state.get("common"):
                targets.append((state, "common_style"))
        else:
            region = self._enhancement_selected()
            target = state if region and state.get("common") else region or state
            key = "common_style" if region and state.get("common") else "style" if region else "background"
            targets = [(target, key)]
        changed = False
        for target, key in targets:
            if target.get(key) != style:
                target[key] = copy.deepcopy(style)
                changed = True
        if changed:
            state["revision"] += 1
        self._enhancement_restored_style = copy.deepcopy(style)
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

    def _restore_enhancement_style(self):
        """回显当前调整对象的色阶与强度映射，不触发参数提交。"""
        state = self._enhancement_state()
        if not state:
            return
        region = None if state.get("show_all_regions") else self._enhancement_selected()
        style = (state.get("common_style", DEFAULT_STYLE) if region and state.get("common")
                 else region.get("style", DEFAULT_STYLE) if region else state["background"])
        widgets = (self.page_render.s_low, self.page_render.s_gamma, self.page_render.s_up, self.page_render.combo_map)
        blockers = [QSignalBlocker(w) for w in widgets]
        for slider, value in zip(widgets[:3], style["levels"]):
            slider.setValue(int(value))
            sync_slider_visual(slider)
        index = combo_index_for_text(widgets[3], style["opacity_mode"])
        if index >= 0:
            widgets[3].setCurrentIndex(index)
        del blockers
        self._enhancement_restored_style = copy.deepcopy(self._enhancement_ui_style())

    def _enhancement_refresh(self, *, interactive=False):
        self._enhancement_cache = None
        self.request_refresh(RefreshCause.DISPLAY_MAPPING, RenderQuality.EXACT,
                             interactive=interactive, immediate=not interactive)

    def _release_global_lock(self):
        """删除区域会改变背景映射：解除全局锁定（背景与全部区域）；按钮状态随控件同步。"""
        VisualEngine.unlock_data_range()
        state = self._enhancement_state()
        if state:
            for region in state["regions"]:
                region.pop("locked_range", None)
                region.pop("lock_pending", None)
        self._restore_color_lock_button(False)
        self._update_color_lock_button_style()

    def _restore_color_lock_button(self, checked):
        """不触发信号地把锁定按钮复位到给定状态。"""
        page = getattr(self, "page_render", None)
        btn = getattr(page, "btn_lock", None) if page is not None else None
        if btn is None:
            return
        blocker = QSignalBlocker(btn)
        btn.setChecked(bool(checked))
        del blocker

    def _sync_color_lock_button(self):
        """锁定按钮反映全局锁定状态：背景或任一区域有锁定即视为锁定。"""
        page = getattr(self, "page_render", None)
        btn = getattr(page, "btn_lock", None) if page is not None else None
        if btn is None:
            return
        state = self._enhancement_state()
        locked = VisualEngine.locked_data_range() is not None
        if state is not None:
            locked = locked or any(
                region.get("locked_range") or region.get("lock_pending")
                for region in state["regions"]
            )
        self._restore_color_lock_button(locked)
        self._update_color_lock_button_style()

    def _select_enhancement_target(self, region_id):
        """切换调整对象并回显其设置；色带与轮廓随下拉框显示。"""
        state = self._enhancement_state()
        if state is None:
            return
        self._commit_enhancement_controls()
        if region_id == SHOW_ALL_REGIONS_ID:
            state["show_all_regions"] = True
        else:
            state["show_all_regions"] = False
            state["selected_id"] = region_id or None
        self._restore_enhancement_style()
        self._clear_enhancement_interaction()
        self._sync_enhancement_controls(self._enhancement_spec(), self.current_render_context)
        self._draw_enhancement_regions(self.current_render_context,
                                       self._enhancement_display(self.current_render_context, allow_compute=False))

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
        region = EnhancementRegion(uuid.uuid4().hex, f"R{number}", context["view"], axes, tuple(bounds)).to_dict()
        region.update(style=copy.deepcopy(self._enhancement_ui_style()), label=f"R{number}")
        state["regions"].append(region)
        state["selected_id"] = region["region_id"]
        state["show_all_regions"] = False
        state["revision"] += 1
        self._restore_enhancement_style()
        self._enhancement_refresh()
        # 全局锁定开启时，新增区域立即锁定自己当前的强度范围。
        if VisualEngine.locked_data_range() is not None:
            self._set_all_regions_locked(True)
        self._select_control_page(0)

    def _delete_enhancement_region(self, region_id):
        state = self._enhancement_state()
        if not state:
            return
        index = next((i for i, r in enumerate(state["regions"]) if r["region_id"] == region_id), None)
        if index is None:
            return
        if self.__dict__.get("_enhancement_context_target") == region_id:
            self._enhancement_context_target = None
        self._commit_enhancement_controls()
        state["regions"].pop(index)
        if state.get("selected_id") == region_id:
            state["selected_id"] = state["regions"][min(index, len(state["regions"]) - 1)]["region_id"] if state["regions"] else None
        state["revision"] += 1
        self._restore_enhancement_style()
        self._release_global_lock()
        self._clear_enhancement_interaction()
        self._enhancement_refresh()

    def _rename_enhancement_region(self, name):
        """下拉框内编辑提交即重命名：视图标注（label）与名称保持一致。"""
        state = self._enhancement_state()
        region = self._enhancement_selected()
        name = (name or "").strip()
        if state is None:
            return
        if region is not None and name and name != region["name"]:
            region["name"] = name
            region["label"] = name
            state["revision"] += 1
            self._enhancement_refresh()
        else:
            # 未生效的编辑（空名、未改名或选中了背景）：把下拉框文字拉回当前状态。
            self._sync_enhancement_controls(self._enhancement_spec(), self.current_render_context)

    def _set_all_regions_locked(self, locked):
        """全局锁定/解锁：每个区域锁定自己实测的强度范围（不共用同一归一化）。"""
        state = self._enhancement_state()
        if not state:
            return
        if locked:
            fields = self._enhancement_display(self.current_render_context, allow_compute=False)
            resolved = {r["region_id"]: r for r in fields["regions"]} if fields else {}
            for region in state["regions"]:
                record = resolved.get(region["region_id"])
                data_range = record["data_range"] if record is not None else None
                region.pop("lock_pending", None)
                region["locked_range"] = (
                    tuple(data_range) if data_range is not None and data_range[1] > data_range[0] else None
                )
        else:
            for region in state["regions"]:
                region.pop("locked_range", None)
                region.pop("lock_pending", None)
        state["revision"] += 1

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

    def _enhancement_roi_requests(self, context, spec, state):
        """Regions whose ROI denoise data is missing or stale, plus their frozen keys."""
        if context.get("view") != "3d" or getattr(spec, "page_kind", None) != "home":
            return []
        if spec.params.get("crop_regions") or abs(float(self.__dict__.get("rotation_angle", 0))) >= 1e-6:
            return []
        methods = self.__dict__.get("global_denoise_methods", [])
        original = self.__dict__.get("original_raw_data")
        if not methods or original is None:
            return []
        cache = self.__dict__.get("_enhancement_roi_data_cache", {})
        _, domain = geometry(context)
        requests = []
        for region in matching_regions(context, state["regions"]):
            if not region.get("enabled", True):
                continue
            slices = region_slices(context, region)
            if slices is None:
                continue
            bounds = tuple(v + int(domain[2 * i]) for i, s in enumerate(slices) for v in (s.start, s.stop - 1))
            key = (id(original), tuple(bounds), repr(methods),
                   self.__dict__.get("shared_denoise_version", 0))
            entry = cache.get(region["region_id"])
            if entry is None or entry.get("key") != key:
                requests.append((region["region_id"], bounds, key))
        return requests

    def _ensure_enhancement_roi_transport(self):
        if self.__dict__.get("_enhancement_roi_pool") is None:
            pool = QThreadPool()
            pool.setMaxThreadCount(1)
            self._enhancement_roi_pool = pool
        if self.__dict__.get("_enhancement_roi_lock") is None:
            self._enhancement_roi_lock = threading.Lock()
        if self.__dict__.get("_enhancement_roi_timer") is None:
            timer = QTimer()
            timer.setInterval(30)
            timer.timeout.connect(self._poll_enhancement_roi)
            self._enhancement_roi_timer = timer

    def _schedule_enhancement_roi(self, context, spec, state):
        """Submit missing ROI denoise work once; the GUI keeps rendering cached data."""
        if self.__dict__.get("_enhancement_roi_disabled") or state is None or spec is None:
            return
        if self.__dict__.get("_enhancement_roi_job") is not None:
            return
        try:
            requests = self._enhancement_roi_requests(context, spec, state)
            if not requests:
                return
            original = self.__dict__.get("original_raw_data")
            methods = list(self.__dict__.get("global_denoise_methods", []))
            self._ensure_enhancement_roi_transport()
            self._enhancement_roi_token = int(self.__dict__.get("_enhancement_roi_token", 0)) + 1
            token = self._enhancement_roi_token
            task = _EnhancementRoiTask(
                self, token, id(original), int(self.__dict__.get("shared_denoise_version", 0)),
                requests, methods, original,
            )
            self._enhancement_roi_job = token
            self._enhancement_roi_task = task
            self._enhancement_roi_timer.start()
            self._enhancement_roi_pool.start(task)
            self._render_exact_ready = False
            update = getattr(self, "_update_export_button_states", None)
            if callable(update):
                update()
        except Exception as exc:  # pragma: no cover - defensive submission boundary
            self._enhancement_roi_disabled = True
            print(f"Enhancement ROI scheduling disabled: {type(exc).__name__}: {exc}")

    def _poll_enhancement_roi(self):
        """Move a finished worker result onto the GUI thread exactly once."""
        lock = self.__dict__.get("_enhancement_roi_lock")
        if lock is None:
            return
        with lock:
            pending = self.__dict__.get("_enhancement_roi_result")
            self._enhancement_roi_result = None
        if pending is None:
            return
        timer = self.__dict__.get("_enhancement_roi_timer")
        if timer is not None:
            timer.stop()
        token, source_id, version, outcome = pending
        current = (self.__dict__.get("_enhancement_roi_job"),
                   id(self.__dict__.get("original_raw_data")),
                   int(self.__dict__.get("shared_denoise_version", 0)))
        if current != (token, source_id, version):
            # 数据源、去噪版本或 token 已变化：丢弃结果，等下一次刷新重新排。
            self._enhancement_roi_job = None
            self._enhancement_roi_task = None
            return
        self._enhancement_roi_job = None
        self._enhancement_roi_task = None
        if outcome[0] == "error":
            self._enhancement_roi_disabled = True
            print(f"Enhancement ROI job failed: {outcome[1]}")
        else:
            cache = self.__dict__.setdefault("_enhancement_roi_data_cache", {})
            cache.update(outcome[1])
        self._enhancement_cache = None
        request = getattr(self, "request_refresh", None)
        if callable(request):
            request(RefreshCause.DENOISE, RenderQuality.EXACT, immediate=True)

    def _invalidate_enhancement_roi(self):
        self._enhancement_roi_token = int(self.__dict__.get("_enhancement_roi_token", 0)) + 1
        self._enhancement_roi_job = None
        self._enhancement_roi_task = None
        lock = self.__dict__.get("_enhancement_roi_lock")
        if lock is not None:
            with lock:
                self._enhancement_roi_result = None
        timer = self.__dict__.get("_enhancement_roi_timer")
        if timer is not None:
            timer.stop()

    def _enhancement_display(self, context, spec=None, *, freeze_pending=True, allow_compute=None):
        state = self._enhancement_state(spec)
        if not state or context is None or context.get("view") not in {"2d", "3d"} or context.get("crop_empty"):
            return None
        adapt_regions(context, state)
        allow = freeze_pending if allow_compute is None else bool(allow_compute)
        context, region_data = self._enhancement_crop_data(context, spec or self._enhancement_spec(), state,
                                                          allow_compute=allow)
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
        # 每次刷新拿到的都是同一帧的新视图（id 变化、内存不变），键必须落在底层缓冲上。
        from bandscope.processing.gpu_display import array_key

        data_key, _base = array_key(data)
        key = (data_key,
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
            # 递增 token 让渲染层区分「同一份通道」与「重新算过」：只有前者可以跳过重传。
            self._enhancement_display_token = int(self.__dict__.get("_enhancement_display_token", 0)) + 1
        if cached[1] is not None and VisualEngine.locked_data_range() is None:
            info = cached[1]["background_level_info"]
            VisualEngine._last_data_range = (info["data_min"], info["data_max"])
        return cached[1]

    def _sync_enhancement_controls(self, spec, context=None):
        if "_enhancement_artists" not in self.__dict__:
            return
        state = self._enhancement_state(spec)
        regions = matching_regions(context, state["regions"]) if state and context else []
        if state and context:
            previous = (state.get("selected_id"), bool(state.get("show_all_regions")))
            if state.get("selected_id") not in {r["region_id"] for r in regions}:
                state["selected_id"] = None
            if not regions:
                state["show_all_regions"] = False
            if previous != (state.get("selected_id"), bool(state.get("show_all_regions"))):
                self._restore_enhancement_style()
        self.page_render.region_combo.sync(
            regions,
            state.get("selected_id") if state else None,
            show_all=bool(state and state.get("show_all_regions")),
        )
        self._sync_color_lock_button()

    def _append_enhancement_actions(self, menu, target_region_id=None):
        context = self.current_render_context
        if context and context.get("view") in {"2d", "3d"} and not context.get("crop_empty"):
            menu.addSeparator()
            state = self._enhancement_state()
            target = None
            if state and target_region_id:
                target = next((r for r in matching_regions(context, state["regions"])
                               if r["region_id"] == target_region_id), None)
            if target is not None:
                label = target.get("label") or target.get("name") or "区域"
                region_id = target["region_id"]
                delete_action = menu.addAction(f"删除增强区域「{label}」")
                delete_action.triggered.connect(
                    lambda _checked=False, rid=region_id: self._delete_enhancement_region(rid))
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

    def _band_region(self, target, state=None):
        """Resolve a right-clicked band target to its region record."""
        kind, region_id = target
        if kind != "region":
            return None
        state = state or self._enhancement_state()
        if not state:
            return None
        return next((r for r in state.get("regions", []) if r.get("region_id") == region_id), None)

    def _band_target_label(self, target, state=None):
        kind, _region_id = target
        if kind == "background":
            return "背景"
        region = self._band_region(target, state)
        return str((region or {}).get("label") or (region or {}).get("name") or "区域")

    def _band_target_style(self, target):
        """Current colour-scale mapping of the object behind a visible band.

        Only levels are copied. Absolute range, lock state and opacity mapping
        stay with the target so a paste never changes frame-to-frame locking.
        """
        kind, _region_id = target
        state = self._enhancement_state()
        if kind == "background":
            style = state.get("background", DEFAULT_STYLE) if state is not None else self._enhancement_ui_style()
        else:
            region = self._band_region(target, state)
            if region is None:
                return None
            style = (state.get("common_style", DEFAULT_STYLE) if state is not None and state.get("common")
                     else region.get("style", DEFAULT_STYLE))
        try:
            levels = tuple(float(v) for v in style.get("levels", DEFAULT_STYLE["levels"]))
        except (AttributeError, TypeError, ValueError):
            return None
        return levels if len(levels) == 3 else None

    def _copy_color_band(self, target):
        levels = self._band_target_style(target)
        if levels is None:
            return False
        self._color_band_clipboard = {"levels": levels, "source": self._band_target_label(target)}
        toast = getattr(self, "_toast_success", None)
        if callable(toast):
            toast("已复制色带", f"来源：{self._color_band_clipboard['source']}")
        return True

    def _current_color_band_target(self, state=None):
        state = state or self._enhancement_state()
        if state is None or state.get("show_all_regions"):
            return ("background", None)
        region_id = state.get("selected_id")
        if region_id and any(r.get("region_id") == region_id for r in state.get("regions", [])):
            return ("region", region_id)
        return ("background", None)

    @staticmethod
    def _writable_band_style(container, key):
        """Return a mutable style dict, copying the shared default if needed."""
        style = container.get(key)
        if style is DEFAULT_STYLE:
            style = copy.deepcopy(style)
        elif not isinstance(style, dict):
            style = copy.deepcopy(DEFAULT_STYLE)
        container[key] = style
        return style

    def _set_background_levels_ui(self, levels):
        widgets = (self.page_render.s_low, self.page_render.s_gamma, self.page_render.s_up)
        blockers = [QSignalBlocker(widget) for widget in widgets]
        for widget, value in zip(widgets, levels):
            widget.setValue(int(round(float(value))))
            sync_slider_visual(widget)
        del blockers

    def _paste_color_band(self, target):
        clipboard = self.__dict__.get("_color_band_clipboard")
        if not clipboard:
            return False
        levels = tuple(float(v) for v in clipboard.get("levels", ()))
        if len(levels) != 3:
            return False
        kind, _region_id = target
        state = self._enhancement_state()
        changed = False
        if kind == "background":
            if state is not None:
                style = self._writable_band_style(state, "background")
                if tuple(float(v) for v in style.get("levels", ())) != levels:
                    style["levels"] = levels
                    changed = True
            else:
                current = tuple(float(w.value()) for w in
                                (self.page_render.s_low, self.page_render.s_gamma, self.page_render.s_up))
                if current != levels:
                    self._set_background_levels_ui(levels)
                    changed = True
        else:
            region = self._band_region(target, state)
            if state is None or region is None:
                return False
            if state.get("common"):
                style = self._writable_band_style(state, "common_style")
            else:
                style = self._writable_band_style(region, "style")
            if tuple(float(v) for v in style.get("levels", ())) != levels:
                style["levels"] = levels
                changed = True
        toast = getattr(self, "_toast_success", None)
        if not changed:
            if callable(toast):
                toast("色带相同", f"{self._band_target_label(target, state)} 已使用该色带")
            return False
        if state is not None:
            state["revision"] = int(state.get("revision", 0)) + 1
            if self._current_color_band_target(state) == target:
                self._restore_enhancement_style()
            self._enhancement_refresh()
        else:
            self.request_refresh(RefreshCause.TRANSFER_FUNCTION, RenderQuality.EXACT, immediate=True)
        if callable(toast):
            toast("已粘贴色带", f"目标：{self._band_target_label(target, state)}")
        return True

    def _append_band_actions(self, menu, target):
        """Put copy/paste at the top of the menu opened on a colour band."""
        if target is None:
            return False
        first = menu.actions()[0] if menu.actions() else None
        copy_action = QAction("复制色带", menu)
        copy_action.triggered.connect(
            lambda _checked=False, band_target=target: self._copy_color_band(band_target))
        paste_action = QAction("粘贴色带", menu)
        paste_action.setEnabled(bool(self.__dict__.get("_color_band_clipboard")))
        paste_action.triggered.connect(
            lambda _checked=False, band_target=target: self._paste_color_band(band_target))
        if first is not None:
            menu.insertAction(first, copy_action)
            menu.insertAction(first, paste_action)
            menu.insertSeparator(first)
        else:
            menu.addAction(copy_action)
            menu.addAction(paste_action)
            menu.addSeparator()
        return True

    def _set_enhancement_show_all(self, enabled):
        state = self._enhancement_state()
        if state is None:
            return
        self._commit_enhancement_controls()
        state["show_all_regions"] = bool(enabled)
        self._restore_enhancement_style()
        context = self.current_render_context
        self._sync_enhancement_controls(self._enhancement_spec(), context)
        self._draw_enhancement_regions(context, self._enhancement_display(context, allow_compute=False))

    def _enhancement_outline_regions(self, regions):
        """Outline visibility is independent of all active enhancement channels."""
        state = self._enhancement_state()
        if state and state.get("show_all_regions"):
            return regions
        selected_id = state.get("selected_id") if state else None
        return [r for r in regions if r["region_id"] == selected_id]

    def _reset_enhancement_for_new_data(self):
        self._enhancement_context_target = None
        self._clear_enhancement_interaction()
        self._invalidate_enhancement_roi()
        self._enhancement_cache = None
        self._enhancement_roi_data_cache = {}
        # 换数据时释放驻留显存与按数组身份缓存的统计，避免旧体数据继续占用。
        from bandscope.processing import gpu_display
        from bandscope.processing.local_enhancement import clear_range_cache

        gpu_display.reset()
        clear_range_cache()
        workspace = self.__dict__.get("left_workspace")
        pages = getattr(workspace, "page_specs", {}) if workspace is not None else {}
        if isinstance(pages, dict):
            for spec in pages.values():
                spec.params.pop(STATE_KEY, None)
        spec = self._enhancement_spec()
        if spec is not None:
            spec.params.pop(STATE_KEY, None)
        page = self.__dict__.get("page_render")
        if page is not None and hasattr(page, "region_combo"):
            page.region_combo.sync([], None, show_all=False)

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
        for colorbar in self.__dict__.get("_enhancement_colorbars", []):
            try:
                colorbar.remove()
            except Exception:
                pass
        self._enhancement_colorbars = []
        for title in self.__dict__.get("_enhancement_scalar_bars", []):
            try:
                self.plotter.remove_scalar_bar(title, render=False)
            except Exception:
                pass
        self._enhancement_scalar_bars = []
        self._enhancement_scalar_bar_records = []
        for name in self.__dict__.get("_enhancement_actors", []):
            self.plotter.remove_actor(name, render=False)
        self._enhancement_actors = []
        self._enhancement_draw_key = None
        self._enhancement_box_signature = None
        self._enhancement_bar_signature = None
        self._band_context_target = None

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
            # 左下角留给区域色带，摘要文字放在左上角。
            note = self.ax_2d.text(.01, .99, enhancement_legend(fields), transform=self.ax_2d.transAxes,
                                  color=theme.TEXT_2, fontsize=8, zorder=90, va="top")
            self._enhancement_artists.append(note)
        # 数量不变时复用色带，只更新 mappable 与刻度；数量变化由绘制函数重建。
        self._enhancement_colorbars = draw_region_colorbars_2d(
            self.ax_2d, fields["regions"], self.page_render.get_selected_cmap(),
            existing=self.__dict__.get("_enhancement_colorbars", ()))
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

    def _enhancement_region_at_2d_point(self, xdata, ydata):
        """Return the outlined region under a 2D data point, topmost first."""
        state = self._enhancement_state()
        context = self.current_render_context
        if (not state or not context or context.get("view") != "2d"
                or xdata is None or ydata is None):
            return None
        e_flip = self.timeline_bar.switch_flip.isChecked()
        for region in reversed(self._enhancement_outline_regions(
                matching_regions(context, state["regions"]))):
            x0, x1, y0, y1 = physical_bounds(context, region["bounds"], e_flip=e_flip)
            if x0 <= xdata <= x1 and y0 <= ydata <= y1:
                return region
        return None

    def _two_d_band_target_at(self, canvas, x, y, axes=None):
        """Return the colour band under display-space canvas coordinates.

        Colourbars created by ``Axes.inset_axes`` live in ``child_axes`` rather
        than ``figure.axes``, and matplotlib reports the parent axes for a click
        inside a child inset.  Hit-test the actual child axes boxes instead of
        trusting ``event.inaxes``; the tight box also covers tick labels.
        """
        if canvas is None or x is None or y is None:
            return None
        target = getattr(axes, "_arpes_band_target", None) if axes is not None else None
        if target is not None:
            return target
        figure = getattr(canvas, "figure", None)
        if figure is None:
            return None
        try:
            renderer = canvas.get_renderer()
        except Exception:
            renderer = None
        candidates = []
        seen = set()
        for ax in getattr(figure, "axes", ()):
            for candidate in (ax, *getattr(ax, "child_axes", ())):
                if id(candidate) in seen:
                    continue
                seen.add(id(candidate))
                target = getattr(candidate, "_arpes_band_target", None)
                if target is not None:
                    candidates.append((candidate, target))
        for ax, target in candidates:
            try:
                if ax.get_visible() and ax.bbox.contains(x, y):
                    return target
            except Exception:
                pass
        if renderer is None:
            return None
        for ax, target in candidates:
            try:
                bbox = ax.get_tightbbox(renderer)
            except Exception:
                bbox = None
            if bbox is not None and bbox.contains(x, y):
                return target
        return None

    def _two_d_band_target_at_event(self, event):
        if event is None:
            return None
        return self._two_d_band_target_at(
            getattr(event, "canvas", None),
            getattr(event, "x", None),
            getattr(event, "y", None),
            getattr(event, "inaxes", None),
        )

    def _two_d_band_target_at_canvas_pos(self, pos):
        """Fallback for Qt menu events that arrive without a matplotlib press."""
        canvas = getattr(self, "canvas_2d", None)
        if canvas is None or pos is None:
            return None
        ratio_value = getattr(canvas, "device_pixel_ratio", 1.0)
        try:
            ratio = float(ratio_value() if callable(ratio_value) else (ratio_value or 1.0))
            x = float(pos.x()) * ratio
            y = (float(canvas.height()) - float(pos.y())) * ratio
        except (AttributeError, TypeError, ValueError):
            return None
        return self._two_d_band_target_at(canvas, x, y)

    def _pick_enhancement_region(self, event):
        if event.button == 3:
            # 右键只记录该点所属区域/色带：菜单据此提供入口，不改变当前选择。
            self._enhancement_context_target = None
            self._band_context_target = None
            if not self._crop_enabled():
                self._band_context_target = self._two_d_band_target_at_event(event)
                if (self._band_context_target is None
                        and event.inaxes is getattr(self, "ax_2d", None)):
                    region = self._enhancement_region_at_2d_point(event.xdata, event.ydata)
                    if region is not None:
                        self._enhancement_context_target = region["region_id"]
            return
        self._band_context_target = None
        if self._crop_enabled() or event.inaxes is not getattr(self, "ax_2d", None) or event.button != 1:
            return
        self._enhancement_context_target = None
        state = self._enhancement_state()
        region = self._enhancement_region_at_2d_point(event.xdata, event.ydata)
        if state is None or region is None:
            return
        if state.get("show_all_regions") or region["region_id"] != state.get("selected_id"):
            QTimer.singleShot(
                0, lambda rid=region["region_id"]: self._select_enhancement_target(rid))

    def _draw_enhancement_boxes(self, context, fields, active):
        """Update 3D outlines, labels and scalar bars; the caller renders once.

        Geometry only changes when regions move or the selection changes, while
        scalar bars change whenever the levels change.  Keeping both signatures
        apart lets a colour-slider tick skip the box/label teardown entirely.
        """
        box_signature = tuple(
            (region["region_id"], tuple(region["bounds"]), str(region.get("label")), bool(region["enabled"]))
            for region in fields["regions"]
        )
        bar_signature = tuple(
            (region["region_id"], tuple(region["data_range"] or ()),
             region["level_info"]["black_value"], region["level_info"]["white_value"],
             region["level_info"]["gamma_power"], bool(region["enabled"]))
            for region in fields["regions"]
        )
        if box_signature != self.__dict__.get("_enhancement_box_signature"):
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
            self._enhancement_box_signature = box_signature
            self._enhancement_bar_signature = None
        if bar_signature != self.__dict__.get("_enhancement_bar_signature"):
            for title in self.__dict__.get("_enhancement_scalar_bars", []):
                try:
                    self.plotter.remove_scalar_bar(title, render=False)
                except Exception:
                    pass
            self._enhancement_scalar_bars = []
            self._enhancement_scalar_bar_records = []
            try:
                self.plotter.remove_actor("enhancement:legend", render=False)
            except Exception:
                pass
            if "enhancement:legend" in self._enhancement_actors:
                self._enhancement_actors.remove("enhancement:legend")
            if fields["regions"]:
                self.plotter.add_text(enhancement_legend(fields), name="enhancement:legend", position="upper_left", font_size=9, render=False)
                self._enhancement_actors.append("enhancement:legend")
                # 每个可见区域一条自己的 scalar bar，范围用区域（可能已锁定）的归一化区间。
                records = add_region_scalar_bars_3d(
                    self.plotter, fields["regions"], self.page_render.get_selected_cmap())
                self._enhancement_scalar_bars = [record["title"] for record in records]
                self._enhancement_scalar_bar_records = records
            self._enhancement_bar_signature = bar_signature
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
                    shape = context.get("full_shape", context["data"].shape)
                    self._enhancement_box_widget = self.plotter.add_box_widget(
                        self._on_enhancement_box, bounds=self.core.logical_to_render_bounds(active["bounds"], shape, clamp=False),
                        factor=1, rotation_enabled=False, color=theme.ACCENT, interaction_event="always")
                    self._enhancement_box_widget.AddObserver("StartInteractionEvent", self._start_enhancement_box)
                    self._enhancement_box_widget.AddObserver("EndInteractionEvent", self._finish_enhancement_box)
                finally:
                    self._enhancement_building = False

    def _enhancement_box_region_at_cursor(self):
        """Pick an outlined 3D box; empty interiors fall back to projection hit-testing."""
        import vtk
        x, y = self.plotter.iren.interactor.GetEventPosition()
        picker = vtk.vtkPropPicker()
        picker.Pick(x, y, 0, self.plotter.renderer)
        region_id = self._enhancement_actor_regions.get(picker.GetActor())
        if region_id is not None:
            return region_id
        return self._enhancement_box_region_at_display_point(x, y)

    def _enhancement_box_region_at_display_point(self, x, y):
        """Hit-test projected box volumes so right-clicking inside a box still works.

        ``vtkPropPicker`` only sees the wireframe outline.  Projecting the eight
        box corners and testing the cursor against their convex hull covers the
        empty interior while staying in the same display coordinate system as
        the VTK picker.
        """
        state = self._enhancement_state()
        context = self.current_render_context
        if not state or not context or context.get("view") != "3d":
            return None
        try:
            shape = context.get("full_shape", context["data"].shape)
            renderer = self.plotter.renderer
            candidates = []
            for region in reversed(self._enhancement_outline_regions(
                    matching_regions(context, state["regions"]))):
                bounds = self.core.logical_to_render_bounds(
                    region["bounds"], shape, clamp=False)
                if bounds is None:
                    continue
                points = []
                depth = 0.0
                for corner_x in (bounds[0], bounds[1]):
                    for corner_y in (bounds[2], bounds[3]):
                        for corner_z in (bounds[4], bounds[5]):
                            renderer.SetWorldPoint(corner_x, corner_y, corner_z, 1.0)
                            renderer.WorldToDisplay()
                            display = renderer.GetDisplayPoint()
                            points.append((display[0], display[1]))
                            depth += display[2]
                if _point_in_convex_polygon(x, y, _convex_hull(points)):
                    candidates.append((depth / len(points), region["region_id"]))
            return min(candidates)[1] if candidates else None
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _scalar_bar_contains(actor, nx, ny):
        """Whether a normalized viewport point lies inside a scalar bar."""
        try:
            position = actor.GetPosition()
            px, py = float(position[0]), float(position[1])
            width = float(actor.GetWidth())
            height = float(actor.GetHeight())
            return px <= nx <= px + width and py <= ny <= py + height
        except (AttributeError, IndexError, TypeError, ValueError):
            return False

    def _three_d_band_target_at_display(self, x, y):
        """Resolve the 3D scalar bar under a display-space point."""
        try:
            renderer = self.plotter.renderer
            size = renderer.GetSize()
            width, height = float(size[0]), float(size[1])
            if width <= 0 or height <= 0:
                return None
            origin = renderer.GetOrigin()
            ox, oy = float(origin[0]), float(origin[1])
            nx = (float(x) - ox) / width
            ny = (float(y) - oy) / height
        except (AttributeError, IndexError, TypeError, ValueError):
            return None
        if not (0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0):
            return None
        # The background bar sits on the right, region bars on the left.  Check
        # the background first so a region accidentally named "Intensity"
        # cannot shadow it when PyVista reuses the scalar-bar actor by title.
        try:
            actor = self.plotter.scalar_bars[VisualEngine.COLORBAR_TITLE]
        except (KeyError, AttributeError, TypeError):
            actor = None
        if actor is not None and self._scalar_bar_contains(actor, nx, ny):
            return ("background", None)
        # Later region bars are drawn on top; check them first so overlapping
        # bars (more than three visible regions use the same slot) stay usable.
        # A title shared by two regions reuses one PyVista actor; keep the first
        # record for that actor instead of letting the later region shadow it.
        resolved = []
        seen_actors = set()
        for record in self.__dict__.get("_enhancement_scalar_bar_records", ()):
            if not isinstance(record, dict):
                continue
            actor = record.get("actor")
            if actor is None:
                try:
                    actor = self.plotter.scalar_bars[record["title"]]
                except (KeyError, AttributeError, TypeError):
                    actor = None
            if actor is None or id(actor) in seen_actors:
                continue
            seen_actors.add(id(actor))
            resolved.append((actor, record["region_id"]))
        for actor, region_id in reversed(resolved):
            if self._scalar_bar_contains(actor, nx, ny):
                return ("region", region_id)
        return None

    def _three_d_band_target_at_cursor(self):
        if self._crop_enabled():
            return None
        try:
            x, y = self.plotter.iren.interactor.GetEventPosition()
        except (AttributeError, TypeError, ValueError):
            return None
        return self._three_d_band_target_at_display(x, y)

    def _pick_enhancement_box(self, _obj, _event):
        self._band_context_target = None
        if self._crop_enabled() or self._enhancement_dragging or not self.current_render_context or self.current_render_context.get("view") != "3d":
            return
        self._enhancement_context_target = None
        region_id = self._enhancement_box_region_at_cursor()
        state = self._enhancement_state()
        if region_id and state and (state.get("show_all_regions") or region_id != state.get("selected_id")):
            QTimer.singleShot(0, lambda: self._select_enhancement_target(region_id))

    def _remember_enhancement_box_context(self, _obj, _event):
        """Pick the outlined 3D box or scalar bar under the cursor for the menu."""
        self._enhancement_context_target = None
        self._band_context_target = None
        context = self.current_render_context
        if self._crop_enabled() or not context or context.get("view") != "3d":
            return
        self._band_context_target = self._three_d_band_target_at_cursor()
        if self._band_context_target is not None:
            # The scalar bar overlays the scene; a hit on it must not also offer
            # the box-delete entry for a region behind the bar.
            return
        self._enhancement_context_target = self._enhancement_box_region_at_cursor()

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
