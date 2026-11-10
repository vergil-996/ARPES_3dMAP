# -*- coding: utf-8 -*-
"""Blender 导出的页面枚举与 GUI 线程批量精确快照协调。"""
from __future__ import annotations

from dataclasses import dataclass
import time

from PyQt5.QtCore import QEventLoop, QTimer

from bandscope.exporting.publication_export import ExportError, capture_snapshot
from bandscope.rendering.refresh_pipeline import RenderQuality


class BlenderFreezeError(ExportError):
    pass


@dataclass(frozen=True)
class BlenderPageCandidate:
    session_id: str
    page_id: str
    source_title: str
    page_title: str
    current: bool = False
    grouped: bool = False

    @property
    def key(self):
        return self.session_id, self.page_id

    @property
    def display_title(self):
        return f"{self.source_title} → {self.page_title}"


def _candidate_kind(window, session, spec):
    kind = str(spec.page_kind)
    if kind in {"home", "time_integral"}:
        if kind == "home":
            active = window.source_manager.is_active(session.session_id)
            state = session.state or {}
            core = window.core if active else state.get("core")
            data = getattr(core, "raw_data", None)
            if data is None or getattr(data, "ndim", 0) not in {3, 4}:
                return False
            slice_info = window.home_slice_info if active else state.get("home_slice_info")
            if slice_info is not None:
                return False
        return True
    if kind == "second_derivative":
        return str(spec.params.get("source_view")) == "3d"
    if kind == getattr(window, "PLUGIN_DERIVED_PAGE_KIND", "plugin_derived"):
        saved = spec.params.get("_restored_result") or {}
        context = saved.get("context") or saved
        return str(context.get("view", spec.params.get("source_view", "3d"))) == "3d"
    return False


def list_blender_pages(window):
    """按数据源和页面树顺序列出候选；全局分组副本按身份去重。"""
    manager = getattr(window, "source_manager", None)
    if manager is None or getattr(window, "left_workspace", None) is None:
        return []
    workspace = window.left_workspace
    grouped_specs = list(workspace._group_page_specs().values())
    result = []
    seen = set()
    active_id = manager.active_id
    current_page = workspace.current_page_id
    for session in manager:
        active = session.session_id == active_id
        if active:
            specs = [spec for spec in workspace.page_specs.values() if spec.group_id is None]
            order = [workspace.home_page_id] + list(workspace._tab_order)
            by_id = {spec.page_id: spec for spec in specs}
            specs = [by_id[page_id] for page_id in order if page_id in by_id and page_id in by_id]
            specs += [spec for spec in by_id.values() if spec not in specs]
        else:
            specs = list((session.state or {}).get("workspace", {}).get("pages") or ())
        specs += [spec for spec in grouped_specs if manager.owner_of(spec.page_id) == session.session_id]
        for spec in specs:
            key = (session.session_id, spec.page_id)
            if key in seen or not _candidate_kind(window, session, spec):
                continue
            seen.add(key)
            result.append(BlenderPageCandidate(
                session.session_id, spec.page_id, session.alias, str(spec.title),
                current=bool(active and current_page == spec.page_id),
                grouped=spec.group_id is not None,
            ))
    return result


def _activate_candidate(window, candidate):
    manager = window.source_manager
    session = manager.get(candidate.session_id)
    if session is None or manager.is_closing(candidate.session_id):
        raise BlenderFreezeError(f"数据源已关闭：{candidate.source_title}")
    if not manager.is_active(candidate.session_id):
        workspace_state = session.state.setdefault("workspace", {})
        if candidate.page_id in window.left_workspace.page_specs or any(
                spec.page_id == candidate.page_id for spec in workspace_state.get("pages") or ()):
            workspace_state["current"] = candidate.page_id
            session.target_page_id = candidate.page_id
        if not window.activate_source(candidate.session_id):
            raise BlenderFreezeError(f"无法激活数据源：{candidate.source_title}")
    if window.left_workspace.current_page_id != candidate.page_id:
        if candidate.grouped:
            window._on_group_page_activation_requested(candidate.page_id)
        else:
            window.left_workspace.activate_page(candidate.page_id)
    spec = window.left_workspace.current_spec()
    if spec is None or spec.page_id != candidate.page_id:
        raise BlenderFreezeError(f"页面不存在或已关闭：{candidate.display_title}")


def _latest_generation(window, page_id):
    coordinator = getattr(window, "refresh_coordinator", None)
    latest = getattr(coordinator, "_latest_generation", {}) or {}
    return latest.get(page_id)


def _exact_ready(window, page_id):
    if not getattr(window, "_render_exact_ready", False):
        return False
    context = getattr(window, "current_render_context", None)
    if context is None:
        return False
    generation = _latest_generation(window, page_id)
    if generation is None:
        # 无后台计算的控制测试宿主直接同步填充上下文。
        return True
    request = getattr(window, "_last_completed_refresh", None)
    coordinator = getattr(window, "refresh_coordinator", None)
    if request is None and int(getattr(coordinator, "_generation", 0) or 0) == 0:
        # 控制测试宿主没有派发过后台任务；取消记录本身不代表存在回退结果。
        return True
    return bool(
        request is not None and request.page_id == page_id
        and request.generation == generation and request.quality == RenderQuality.EXACT
    )


def _wait_for_exact(window, candidate, cancel_event, timeout):
    failure = getattr(window, "_last_refresh_failure", None)
    generation = _latest_generation(window, candidate.page_id)
    if failure is not None:
        request, message = failure
        if request.page_id == candidate.page_id and request.generation == generation:
            raise BlenderFreezeError(f"页面精确计算失败：{candidate.display_title}\n{message}")
    if _exact_ready(window, candidate.page_id):
        return
    loop = QEventLoop()
    deadline = time.monotonic() + float(timeout)
    state = {"done": False, "timed_out": False}

    def check():
        if state["done"]:
            return
        if time.monotonic() >= deadline:
            state.update(done=True, timed_out=True)
            loop.quit()
            return
        if cancel_event is not None and cancel_event.is_set():
            state["done"] = True
            loop.quit()
            return
        failure = getattr(window, "_last_refresh_failure", None)
        if failure is not None:
            request, message = failure
            if request.page_id == candidate.page_id and request.generation == _latest_generation(window, candidate.page_id):
                state["done"] = True
                loop.quit()
                return
        if _exact_ready(window, candidate.page_id):
            state["done"] = True
            loop.quit()

    for signal in (window.refreshFinished, window.refreshFailed):
        signal.connect(check)
    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(check)
    timer.start()
    try:
        check()
        while not state["done"] and time.monotonic() < deadline:
            loop.exec_()
            check()
        if state["timed_out"] or not state["done"] or cancel_event is not None and cancel_event.is_set():
            if cancel_event is not None and cancel_event.is_set():
                raise BlenderFreezeError("已取消批量冻结；未开始写出工程。")
            raise BlenderFreezeError(f"等待页面精确结果超时：{candidate.display_title}")
        failure = getattr(window, "_last_refresh_failure", None)
        if failure is not None:
            request, message = failure
            if request.page_id == candidate.page_id and request.generation == _latest_generation(window, candidate.page_id):
                raise BlenderFreezeError(f"页面精确计算失败：{candidate.display_title}\n{message}")
    finally:
        timer.stop()
        for signal in (window.refreshFinished, window.refreshFailed):
            try:
                signal.disconnect(check)
            except (TypeError, RuntimeError):
                pass


def _set_freeze_controls(window, frozen):
    stored = getattr(window, "_blender_freeze_widget_states", None)
    if frozen:
        if stored is not None:
            return
        states = {}
        for name in ("main_splitter", "page_container", "toolbar", "source_switcher_bar"):
            widget = getattr(window, name, None)
            if widget is not None:
                states[name] = widget.isEnabled()
                widget.setEnabled(False)
        window._blender_freeze_widget_states = states
        return
    if stored is None:
        return
    for name, enabled in stored.items():
        widget = getattr(window, name, None)
        if widget is not None:
            widget.setEnabled(enabled)
    window._blender_freeze_widget_states = None


def capture_blender_snapshots(window, selections, *, cancel_event=None, progress=None, timeout=180):
    """逐源逐页冻结精确 3D 结果；任何失败或取消都恢复进入前的界面状态。"""
    if not hasattr(window, "_blender_freeze_widget_states"):
        window._blender_freeze_widget_states = None
    candidates = {candidate.key: candidate for candidate in list_blender_pages(window)}
    ordered, seen = [], set()
    for session_id, page_id in selections:
        key = (str(session_id), str(page_id))
        if key in seen:
            continue
        seen.add(key)
        if key not in candidates:
            raise BlenderFreezeError(f"页面不可导出或已失效：{session_id}/{page_id}")
        ordered.append(candidates[key])
    if not ordered:
        raise BlenderFreezeError("请至少勾选一个 3D 页面。")
    manager = window.source_manager
    original_session = manager.active_id
    original_page = window.left_workspace.current_page_id
    original_navigation = window.left_workspace.page_tree.export_navigation_state()
    original_control_tab = window.page_container.currentIndex() if getattr(window, "page_container", None) else None
    snapshots = []
    _set_freeze_controls(window, True)
    try:
        if hasattr(window, "_persist_active_page_state"):
            window._persist_active_page_state()
        for index, candidate in enumerate(ordered):
            if cancel_event is not None and cancel_event.is_set():
                raise BlenderFreezeError("已取消批量冻结；未开始写出工程。")
            if progress is not None:
                progress(index, len(ordered), f"冻结 {candidate.display_title}")
            try:
                _activate_candidate(window, candidate)
                _wait_for_exact(window, candidate, cancel_event, timeout)
                snapshot = capture_snapshot(window)
                if snapshot.view != "3d" or snapshot.view_family != "3d":
                    raise BlenderFreezeError(f"页面当前结果不是 3D：{candidate.display_title}")
                snapshot.source_session_id = candidate.session_id
                snapshots.append(snapshot)
            except Exception as exc:
                if isinstance(exc, BlenderFreezeError):
                    raise
                raise BlenderFreezeError(f"冻结页面失败：{candidate.display_title}\n{exc}") from exc
    finally:
        restore_error = None
        try:
            if original_session is not None and manager.get(original_session) is not None:
                if not manager.is_active(original_session):
                    window.activate_source(original_session)
                if original_page in window.left_workspace.page_specs and window.left_workspace.current_page_id != original_page:
                    spec = window.left_workspace.page_specs[original_page]
                    if spec.group_id is not None:
                        window._on_group_page_activation_requested(original_page)
                    else:
                        window.left_workspace.activate_page(original_page)
                window.left_workspace.page_tree.restore_navigation_state(original_navigation)
                if original_control_tab is not None:
                    window.page_container.setCurrentIndex(original_control_tab)
        except Exception as exc:
            restore_error = exc
        finally:
            _set_freeze_controls(window, False)
        if restore_error is not None:
            raise BlenderFreezeError(f"批量冻结后恢复界面失败：{restore_error}") from restore_error
    if progress is not None:
        progress(len(ordered), len(ordered), "全部页面已冻结")
    return snapshots
