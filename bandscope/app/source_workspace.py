"""多数据源工作区：会话状态换入换出与新增 / 切换 / 关闭事务。

主窗口只保留一套控件、渲染器和插件实例；"当前是哪份数据"由
:class:`~bandscope.core.source_session.SourceSessionManager` 决定。本模块负责：

- 把随数据源变化的窗口状态整体捕获 / 安装（数组按引用保存）；
- 新数据先独立准备，成功后才登记会话；失败不改变已有会话；
- 切源时先提交当前页、保存旧源状态、失效旧源显示任务与交互对象，再绑定目标
  会话，最后只触发一次页面激活与刷新；
- 根视图删除走源级关闭事务：先摘页面记录再清理，避免逐页删除时误激活。

不在这里做的事：页面内部参数（每个页面的 ``control_state``、裁剪、轴标题、
插件页参数）仍由页面自己保存；插件面板状态由宿主导出 / 恢复。
"""
from __future__ import annotations

import copy
from typing import Optional

from PyQt5.QtWidgets import QMessageBox

from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.source_session import (
    ENTRY_MAT,
    ENTRY_NPZ,
    SourceSessionManager,
    new_session_id,
)
from bandscope.ui.axis_title_controller import AxisTitleState
from bandscope.ui.result_workspace import AnalysisPageSpec, unique_title_among

#: 未加载任何数据时的缓存命名空间。
EMPTY_NAMESPACE = "__empty__"

#: 状态包里标记"这个属性当时不存在"。
_MISSING = object()


class SourceWorkspaceMixin:
    """会话级工作区事务；与主窗口的其它 mixin 共用同一份窗口状态。"""

    #: 随数据源整体换入换出的窗口属性。数组、页面和缓存都按引用保存，
    #: 不做深拷贝（内存保留策略）；窗口级共享对象（计算后端、剪贴板、
    #: 派生卷缓存、旋转缓存）不在此列。
    SOURCE_STATE_ATTRS = (
        "core",
        "base_raw_data",
        "base_coords",
        "original_raw_data",
        "original_coords",
        "clip_ranges",
        "home_slice_info",
        "precise_logical_bounds",
        "last_synced_slice_texts",
        "active_page_spec",
        "last_visual_page_id",
        "global_denoise_methods",
        "shared_denoised_raw_data",
        "shared_denoise_signature",
        "shared_denoise_version",
        "data_scopes",
        "_scope_generation",
        "_scope_data_cache",
        "_scope_committed_signatures",
        "_pending_denoise_methods",
        "_pending_denoise_signature",
        "_pending_denoise_scope_id",
        "_pending_scope_denoise_keys",
        "_pending_roi_scope_id",
        "axis_space",
        "axis_interval",
        "axis_source_mode",
        "loaded_npz_stem",
        "rotation_angle",
        "_rendered_rotation_angle",
        "_axis_prefix_cache",
        "_second_derivative_volume_cache",
        "_restored_publication",
        "_restored_recipe_views",
        "_recipe_scene_pending",
        "_recipe_derived_results",
        "_recipe_origins",
        "_recipe_origin_errors",
        "axis_title_state",
        "_saved_3d_camera_position",
        "_camera_e_flip_applied",
        "_last_compute_backend",
        "_last_ui_transfer_signature",
        "_render_exact_ready",
        "_enhancement_cache",
        "_enhancement_roi_data_cache",
        "_enhancement_roi_result",
    )

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def _init_source_workspaces(self):
        self.source_manager = SourceSessionManager()
        #: 新源准备期间暂存的会话标识；提交成功前不进入管理器。
        self._source_pending_id: Optional[str] = None
        self._source_switching = False
        #: 新数据首次 3D 渲染后再重置视角：加载途中还没有体积，reset_camera 会取错景。
        self._pending_default_camera = False
        #: 记录切源触发了几次页面激活 / 刷新，供验收脚本核对"只一次"。
        self._source_activation_count = 0

    # ------------------------------------------------------------------
    # 缓存命名空间与预算
    # ------------------------------------------------------------------
    def _source_cache_namespace(self) -> str:
        pending = self.__dict__.get("_source_pending_id")
        if pending:
            return str(pending)
        manager = self.__dict__.get("source_manager")
        active = manager.active_id if manager is not None else None
        return str(active) if active else EMPTY_NAMESPACE

    def _global_denoise_page_id(self) -> str:
        namespace = self._source_cache_namespace()
        if namespace == EMPTY_NAMESPACE:
            # 未加载任何数据源（含测试替身）时沿用原标识。
            return self.GLOBAL_DENOISE_PAGE_ID
        return f"{self.GLOBAL_DENOISE_PAGE_ID}:{namespace}"

    def _scope_denoise_page_id(self, scope_id) -> str:
        namespace = self._source_cache_namespace()
        if namespace == EMPTY_NAMESPACE:
            return f"{self.SCOPE_DENOISE_PAGE_PREFIX}{scope_id}"
        return f"{self.SCOPE_DENOISE_PAGE_PREFIX}{namespace}:{scope_id}"

    def _rebalance_scope_cache_budgets(self):
        """可重建 CPU 缓存共用一份全应用预算，不随源数倍增。"""
        from bandscope.processing.compute_backends import recommended_host_cache_budget

        manager = self.__dict__.get("source_manager")
        sessions = list(manager) if manager is not None else []
        share = max(1, int(recommended_host_cache_budget()) // max(1, len(sessions) or 1))
        for session in sessions:
            cache = (session.state or {}).get("_scope_data_cache")
            setter = getattr(cache, "set_budget", None)
            if callable(setter):
                setter(share)
        active_cache = self.__dict__.get("_scope_data_cache")
        setter = getattr(active_cache, "set_budget", None)
        if callable(setter) and not sessions:
            setter(share)

    def _drop_session_cache_entries(self, session_id):
        """关闭源时驱逐只属于它的可重建缓存。"""
        manager = self.__dict__.get("source_manager")
        session = manager.get(session_id) if manager is not None else None
        state = session.state if session is not None else None
        cache = (state or {}).get("_scope_data_cache")
        if cache is not None:
            cache.clear()
        # 派生卷 / 旋转缓存以数组身份为键：源关闭后整表重算最省心。
        self._computed_volume_cache.clear()
        self._rotation_cache.clear()

    # ------------------------------------------------------------------
    # 状态捕获 / 安装
    # ------------------------------------------------------------------
    def _plugin_panel_state(self):
        session = self._plugin_session()
        if session is None:
            return {}
        return session.export_panel_state()

    def _capture_source_state(self) -> dict:
        """捕获当前窗口状态；不复制数组，只保存引用。"""
        state = {}
        for name in self.SOURCE_STATE_ATTRS:
            state[name] = self.__dict__.get(name, _MISSING)
        workspace = self.left_workspace
        state["workspace"] = workspace.export_source_state()
        state["camera"] = self._current_3d_camera_position()
        state["plugin_panel"] = self._plugin_panel_state()
        from bandscope.rendering.render_core import VisualEngine

        state["locked_range"] = VisualEngine.locked_data_range()
        return state

    def _install_source_state(self, state) -> None:
        """把状态包换回窗口；阻断控件信号，激活与刷新由调用方统一触发一次。"""
        state = state or {}
        for name in self.SOURCE_STATE_ATTRS:
            value = state.get(name, _MISSING)
            if value is not _MISSING:
                setattr(self, name, value)
        self.left_workspace.install_source_state(state.get("workspace") or {})
        spec = self.left_workspace.current_spec()
        self.active_page_spec = spec
        if spec is not None and spec.page_kind != "control_panel":
            self.last_visual_page_id = spec.page_id
        elif self.left_workspace.home_page_id is not None:
            self.last_visual_page_id = self.left_workspace.home_page_id
        camera = state.get("camera")
        self._saved_3d_camera_position = camera
        if camera is not None:
            try:
                self.plotter.camera_position = camera
            except Exception:
                pass
        session = self._plugin_session()
        if session is not None:
            session.restore_panel_state(state.get("plugin_panel") or {})
        from bandscope.rendering.render_core import VisualEngine

        VisualEngine._locked_data_range = state.get("locked_range")
        # 切到已有数据源只恢复它自己的相机；不要让上一次新源准备的默认取景
        # 标记残留到这里。
        self._pending_default_camera = False

    def _store_active_session(self):
        """提交当前编辑并保存活动会话；不改变活动源。"""
        manager = self.__dict__.get("source_manager")
        session = manager.active() if manager is not None else None
        if session is None:
            return None
        self._persist_active_page_state()
        state = self._capture_source_state()
        session.state = state
        workspace = self.left_workspace
        session.home_page_id = workspace.home_page_id
        session.target_page_id = workspace.current_page_id
        manager.register_pages(
            session.session_id,
            [page_id for page_id in workspace.page_specs],
            home_page_id=workspace.home_page_id,
        )
        return session

    # ------------------------------------------------------------------
    # 切换
    # ------------------------------------------------------------------
    def _invalidate_pending_tasks(self):
        """切源使显示预览与未完成去噪失效；配置保留，切回按需重算。"""
        spec = self.__dict__.get("active_page_spec")
        page_id = getattr(spec, "page_id", None)
        if page_id:
            self.refresh_coordinator.cancel_page(page_id)
        self.refresh_coordinator.cancel_page(self._global_denoise_page_id())
        for scope_id in list(self.__dict__.get("data_scopes") or {}):
            self.refresh_coordinator.cancel_page(self._scope_denoise_page_id(scope_id))
        self._pending_denoise_methods = None
        self._pending_denoise_signature = None
        self._pending_denoise_scope_id = None
        self._pending_scope_denoise_keys = set()
        self._pending_roi_scope_id = None

    def _clear_interaction_objects(self):
        """换源前清掉只对旧场景有效的交互对象（不改页面参数）。"""
        self._clear_enhancement_interaction()
        controller = self.__dict__.get("crop_controller")
        if controller is not None:
            controller.popup.hide()
        self._clear_axis_crop_interaction(redraw=False)
        self._reset_axis_title_interaction()
        self._clear_interactive_box()
        self.current_render_context = None
        self._enhancement_cache = None

    def activate_source(self, session_id) -> bool:
        """切到指定数据源；返回是否真的发生切换。"""
        manager = self.__dict__.get("source_manager")
        if manager is None or self.__dict__.get("_source_switching"):
            return False
        session = manager.get(session_id)
        if session is None or manager.is_closing(session_id):
            return False
        if manager.is_active(session_id):
            return False

        self._source_switching = True
        try:
            # 1. 失效旧源的显示任务，清理只对旧场景有效的交互对象。
            self._invalidate_pending_tasks()
            self._clear_interaction_objects()
            # 2. 提交并保存旧源（页面、插件、相机、导航）。
            self._store_active_session()
            # 3. 绑定目标会话：阻断信号安装控件。
            if not manager.set_active(session_id):
                return False
            self._install_source_state(session.state)
            # 4. 只触发一次页面绑定与刷新。
            self._activate_current_session()
            self._sync_source_switcher()
        finally:
            self._source_switching = False
        return True

    def _activate_current_session(self):
        """一次切源只走这里一次：恢复控件 → 页面激活 → 单次刷新。"""
        self._source_activation_count += 1
        workspace = self.left_workspace
        spec = workspace.current_spec()
        self.active_page_spec = spec
        if spec is not None and spec.page_kind != "control_panel":
            self.last_visual_page_id = spec.page_id

        self._syncing_controls = True
        try:
            if spec is not None:
                # 不同数据源的时间轴长度可以不同：先按本源的形状落定滑条范围，
                # 再用页面保存的控件状态恢复帧号与积分区间。
                if self.core.raw_data is not None:
                    self._configure_loaded_time_controls(self.core.raw_data.shape)
                self._sync_controls_from_page(spec)
                # _restore_page_ui_state 会自己开关该标志，这里重新压住后续绑定。
                self._syncing_controls = True
                if self.core.raw_data is not None:
                    self.update_ax_slider_range()
                    self._sync_slice_edits_from_logical_bounds(
                        self.precise_logical_bounds or self.clip_ranges
                    )
                self._bind_axis_interval(spec, animate=False)
                self._update_time_slider_state()
                self._activate_crop_context(spec, None)
            self._update_time_slider_state()
            self._configure_time_controls()
        finally:
            self._syncing_controls = False

        if spec is not None:
            self._request_scope_denoise_if_needed(spec)
        workspace._refresh_header()
        self._update_statusbar()
        self._update_export_button_states()
        self._update_screenshot_tooltip()
        self.global_refresh()

    # ------------------------------------------------------------------
    # 新增
    # ------------------------------------------------------------------
    def _begin_source_transaction(self) -> Optional[str]:
        """保存旧源并把工作区摘成空白，返回旧活动源标识。

        旧源的页面对象原样留在它的状态包里，这里不发 ``page_closed``：它们没有
        被删除，只是暂时不显示。
        """
        previous_id = None
        manager = self.__dict__.get("source_manager")
        if manager is not None:
            previous_id = manager.active_id
            if previous_id is not None:
                self._invalidate_pending_tasks()
                self._clear_interaction_objects()
                self._store_active_session()
        self.left_workspace.detach_source_state()
        self._source_pending_id = new_session_id()
        return previous_id

    def _commit_source_transaction(
        self,
        previous_id,
        *,
        entry_kind,
        entry_path,
        primary_path,
        alias=None,
        reset_camera=False,
        target_page_id=None,
    ):
        """登记新源并切换过去；失败由调用方回滚到 ``previous_id``。"""
        manager = self.__dict__.get("source_manager")
        session_id = self._source_pending_id
        if manager is None or session_id is None:
            raise RuntimeError("数据源事务尚未开始。")

        workspace = self.left_workspace
        if target_page_id in workspace.page_specs and workspace.current_page_id != target_page_id:
            # 只落定当前页，不发出激活回调：提交事务统一触发一次绑定与刷新。
            workspace.current_page_id = target_page_id
            workspace._refresh_navigation_layout()
            workspace._refresh_header()
        state = self._capture_source_state()
        session = None
        try:
            session = manager.add(
                entry_kind=entry_kind,
                entry_path=entry_path,
                primary_path=primary_path,
                alias=alias,
                session_id=session_id,
                state=state,
                activate=True,
            )
            session.target_page_id = workspace.current_page_id
            manager.register_pages(
                session_id,
                [page_id for page_id in workspace.page_specs],
                home_page_id=workspace.home_page_id,
            )
        except Exception:
            if session is not None:
                manager.close(session.session_id)
            raise
        self._source_pending_id = None
        self._rebalance_scope_cache_budgets()
        if reset_camera:
            # 不在提交时立刻 reset_camera：此时场景里还没有体积，取景会落在空包围盒。
            self._pending_default_camera = True
        self._sync_source_switcher()
        self._activate_current_session()
        return session

    def _abort_source_transaction(self, previous_id):
        """回滚半成品新源：丢弃暂存标识，回到旧源状态。"""
        pending = self._source_pending_id
        self._source_pending_id = None
        manager = self.__dict__.get("source_manager")
        if manager is not None and pending is not None and manager.has_session(pending):
            manager.close(pending)
        self._reset_working_state()
        if manager is not None and previous_id is not None and manager.get(previous_id) is not None:
            manager.activate_fallback(previous_id)
            self._install_source_state(manager.get(previous_id).state)
            self._activate_current_session()
        else:
            self._reset_empty_workspace()
        self._sync_source_switcher()

    def open_npz_source(self, entry_path, target_path) -> bool:
        """准备并把一份 NPZ（或已转换的 MAT）登记为新数据源。"""
        core = AnalyzerCore()
        success, info = core.load_npz(str(target_path))
        if not success:
            self._show_message("数据加载失败", f"无法加载文件：\n{info}", QMessageBox.Critical)
            return False
        entry_kind = ENTRY_MAT if str(entry_path).lower().endswith(".mat") else ENTRY_NPZ
        previous_id = self._begin_source_transaction()
        try:
            self._prepare_new_source_state(core, info, target_path)
            self._commit_source_transaction(
                previous_id,
                entry_kind=entry_kind,
                entry_path=str(entry_path),
                primary_path=str(target_path),
                reset_camera=True,
            )
        except Exception:
            self._abort_source_transaction(previous_id)
            raise
        return True

    def _prepare_new_source_state(self, core, info, target_path):
        """把一个新源初始化成"刚加载数据"的独立状态（旧源已摘出）。"""
        self.core = core
        home = self._build_home_page()
        self.left_workspace.replace_pages([home], home.page_id, home.page_id)
        self.active_page_spec = home
        self.last_visual_page_id = home.page_id
        self._prime_loaded_data_state(target_path)
        self._restore_initial_controls_after_load()
        self._refresh_core_display_state()
        # 新源没有历史面板记录：回到插件默认值，不继承上一份数据的峰位。
        session = self._plugin_session()
        if session is not None:
            session.reset_panel_state()
        self._configure_loaded_time_controls(info)
        self._reset_workspace_after_load()
        home = self.left_workspace.home_spec()
        if home is not None:
            home.params["control_state"] = self._capture_control_state()
            self._persist_home_page_state(home)
        self._rebalance_scope_cache_budgets()

    def _build_home_page(
        self, *, title="原始视图", params=None, closeable=True
    ) -> AnalysisPageSpec:
        home = AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=title,
            page_kind="home",
            source_module="system",
            closeable=closeable,
            data_scope_id=self.FULL_SCOPE_ID,
        )
        if params:
            home.params.update(params)
        return home

    def _prime_loaded_data_state(self, target_path):
        """把当前（新源）窗口状态切到"刚加载一份数据"的初始值。"""
        self._restored_publication = {}
        self._restored_recipe_views = {}
        self._recipe_scene_pending = set()
        self._recipe_derived_results = {}
        self._recipe_origins = {}
        self._recipe_origin_errors = {}
        self._reset_enhancement_for_new_data()
        self.loaded_npz_stem = self._path_stem(target_path)
        canonical, original, base = self._canonical_data_aliases(self.core.raw_data)
        self.core.raw_data = canonical
        self.original_raw_data = original
        self.original_coords = self._clone_coords()
        self.base_raw_data = base
        self.base_coords = self._clone_coords(self.original_coords)
        self.refresh_coordinator.cancel_page(self._global_denoise_page_id())
        self.global_denoise_methods = []
        self.shared_denoised_raw_data = None
        self.shared_denoise_signature = self._denoise_signature([])
        self.shared_denoise_version += 1
        self._initialize_data_scopes()
        self._pending_denoise_methods = None
        self._pending_denoise_signature = None
        self._pending_denoise_scope_id = None
        self._pending_scope_denoise_keys = set()
        self._pending_roi_scope_id = None
        self._rotation_cache.clear()
        self._computed_volume_cache.clear()
        self.backend_manager.clear_caches()
        if hasattr(self, "volume_session"):
            self.volume_session.clear(render=False)
        self._second_derivative_volume_cache = None
        self._axis_prefix_cache = None
        self.rotation_angle = 0.0
        self._rendered_rotation_angle = 0.0
        self._saved_3d_camera_position = None
        self._camera_e_flip_applied = False
        self.clip_ranges = None
        self.home_slice_info = None
        self.axis_source_mode = "frame"
        self._last_compute_backend = self.backend_manager.selected_backend_name()
        self._render_exact_ready = True
        self.axis_title_state = AxisTitleState()
        controller = self.__dict__.get("axis_title_controller")
        if controller is not None:
            controller.state = self.axis_title_state
        self._enhancement_cache = None
        self._enhancement_roi_data_cache = {}
        self._enhancement_roi_result = None
        from bandscope.rendering.render_core import VisualEngine

        VisualEngine._locked_data_range = None

    @staticmethod
    def _path_stem(path):
        from pathlib import Path

        return Path(str(path)).stem

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------
    def close_source(self, session_id=None) -> bool:
        """根视图删除入口：关掉整份数据（含全部派生页与关联任务）。"""
        manager = self.__dict__.get("source_manager")
        if manager is None:
            return False
        session_id = session_id or manager.active_id
        session = manager.get(session_id)
        if session is None or manager.is_closing(session_id):
            return False

        is_active = manager.is_active(session_id)
        manager.mark_closing(session_id)
        try:
            # 先驱逐只属于它的缓存，此时会话对象还在管理器里。
            self._drop_session_cache_entries(session_id)
            if is_active:
                self._invalidate_pending_tasks()
                self._clear_interaction_objects()
                self._store_active_session()
                self.left_workspace.detach_all_pages()
                self._reset_working_state()
            else:
                self._purge_session_pages(session)
            manager.close(session_id)
            self._rebalance_scope_cache_budgets()
            self._sync_source_switcher()
            if is_active:
                if manager.active_id is not None:
                    self._install_source_state(manager.active().state)
                    self._activate_current_session()
                else:
                    self._reset_empty_workspace()
            elif manager.active_id is not None:
                # 关闭非活动源不该打断当前查看的数据：只刷新状态栏与切换器。
                self._update_statusbar()
                self._update_export_button_states()
        finally:
            manager.unmark_closing(session_id)
        return True

    def _purge_session_pages(self, session):
        """非活动源关闭：页面不在工作区里，逐页直接清理关联资源。"""
        for page_id in list(session.page_ids):
            self._forget_page_resources(page_id)

    def _forget_page_resources(self, page_id):
        """清理单页的全局引用（与 ``on_result_page_closed`` 共用）。"""
        from bandscope.exporting.curve_presentation import publication_session

        publication_session(self).forget_page(page_id)
        dialog = self.__dict__.get("_publication_dialog")
        if dialog is not None:
            dialog.forget_page(page_id)
        controller = self.__dict__.get("crop_controller")
        if controller is not None:
            controller.remove_page(page_id)
        self.refresh_coordinator.cancel_page(page_id)
        session = self._plugin_session()
        if session is not None:
            session.cancel_page_analysis(page_id)
            session.invalidate_derived_page(page_id)
        self._clear_axis_prefix_cache(page_id)
        self._axis_titles().forget_page(page_id)
        self._computed_volume_cache.clear()
        self._rotation_cache.clear()
        self._second_derivative_volume_cache = None

    def _reset_working_state(self):
        """清掉"上一份数据"遗留的窗口级临时状态（不动任何会话的内容）。"""
        self.active_page_spec = None
        self.last_visual_page_id = None
        self.current_render_context = None
        self._enhancement_cache = None
        self._enhancement_roi_data_cache = {}
        self._enhancement_roi_result = None
        from bandscope.rendering.render_core import VisualEngine

        VisualEngine._locked_data_range = None

    def _reset_empty_workspace(self):
        """最后一份数据关闭后回到未加载界面。"""
        self._source_pending_id = None
        self._pending_default_camera = False
        self._reset_working_state()
        workspace = self.left_workspace
        if workspace.page_specs:
            workspace.detach_all_pages()

        self._syncing_controls = True
        try:
            self.core = AnalyzerCore()
            self.base_raw_data = None
            self.base_coords = None
            self.original_raw_data = None
            self.original_coords = None
            self.clip_ranges = None
            self.home_slice_info = None
            self.precise_logical_bounds = None
            self.last_synced_slice_texts = None
            self.active_page_spec = None
            self.last_visual_page_id = None
            self.global_denoise_methods = []
            self.shared_denoised_raw_data = None
            self.shared_denoise_signature = self._denoise_signature([])
            self.shared_denoise_version += 1
            self.data_scopes = {}
            self._scope_generation = 0
            self._scope_committed_signatures = {}
            self._pending_denoise_methods = None
            self._pending_denoise_signature = None
            self._pending_denoise_scope_id = None
            self._pending_scope_denoise_keys = set()
            self._pending_roi_scope_id = None
            self._rotation_cache.clear()
            self._computed_volume_cache.clear()
            self.backend_manager.clear_caches()
            self._axis_prefix_cache = None
            self._second_derivative_volume_cache = None
            self.axis_space = None
            self.axis_interval = None
            self.axis_source_mode = "frame"
            self.loaded_npz_stem = "data"
            self._restored_publication = {}
            self._restored_recipe_views = {}
            self._recipe_scene_pending = set()
            self._recipe_derived_results = {}
            self._recipe_origins = {}
            self._recipe_origin_errors = {}
            self.axis_title_state = AxisTitleState()
            controller = self.__dict__.get("axis_title_controller")
            if controller is not None:
                controller.state = self.axis_title_state
            self._saved_3d_camera_position = None
            self._camera_e_flip_applied = False
            self._render_exact_ready = True
            self._restore_initial_controls_after_load()
        finally:
            self._syncing_controls = False

        # 未加载界面仍保留一个不可删除的占位根页面（与启动时一致）。
        home = self._build_home_page(closeable=False)
        workspace.replace_pages([home], home.page_id, home.page_id)
        self.active_page_spec = home
        self.last_visual_page_id = home.page_id
        home.params["control_state"] = self._capture_control_state()
        self._bind_axis_interval(home, animate=False)
        self._configure_time_controls()
        stack = self.__dict__.get("left_display_stack")
        if stack is not None:
            stack.setCurrentIndex(0)
        session = self.__dict__.get("volume_session")
        if session is not None:
            session.clear(render=False)
        self._clear_interactive_box()
        from bandscope.rendering.render_core import VisualEngine

        VisualEngine.clear_2d_colorbar(self.ax_2d)
        self.ax_2d.clear()
        self.canvas_2d.draw()
        self._set_camera_view_visible(False)
        self._update_statusbar()
        self._update_export_button_states()
        self.global_refresh()

    # ------------------------------------------------------------------
    # 切换器与别名
    # ------------------------------------------------------------------
    def _sync_source_switcher(self):
        bar = self.__dict__.get("source_switcher_bar")
        if bar is None:
            return
        manager = self.__dict__.get("source_manager")
        if manager is None:
            bar.apply_sources([], None)
            return
        bar.apply_sources(manager.source_items(), manager.active_id)
        session = manager.active()
        if session is not None:
            # 提示区分入口文件（用户点开的那个）和实际主 NPZ。
            lines = [session.alias, f"入口文件：{session.entry_path}"]
            if session.primary_path and session.primary_path != session.entry_path:
                lines.append(f"主数据：{session.primary_path}")
            lines.append(f"共 {len(manager)} 份数据，可在下拉框改名（Enter 提交，Esc 撤销）")
            bar.switcher.setToolTip("\n".join(lines))
        else:
            bar.switcher.setToolTip("已加载的数据源")

    def _commit_source_alias(self, session_id, alias):
        manager = self.__dict__.get("source_manager")
        if manager is None:
            return
        final_alias = manager.set_alias(session_id, alias)
        session = manager.get(session_id)
        if session is not None and final_alias:
            session.state.setdefault("alias", final_alias)
        self._sync_source_switcher()
        self._update_statusbar()

    # ------------------------------------------------------------------
    # 页面归属
    # ------------------------------------------------------------------
    def _on_source_close_requested(self, page_id):
        """删除左侧根页面：按页面归属找到会话并关闭整份数据。"""
        manager = self.__dict__.get("source_manager")
        if manager is None:
            return False
        owner = manager.owner_of(page_id) or manager.active_id
        return self.close_source(owner)

    def _register_page_with_session(self, page_id):
        manager = self.__dict__.get("source_manager")
        if manager is None or manager.active_id is None:
            return
        manager.register_page(manager.active_id, page_id)
        session = manager.active()
        if session is not None:
            session.target_page_id = page_id

    # ------------------------------------------------------------------
    # 非活动会话的页面登记（后台分析完成时用）
    # ------------------------------------------------------------------
    def _session_workspace_state(self, session):
        return (session.state or {}).get("workspace") or {}

    def _session_page_specs(self, session):
        return list(self._session_workspace_state(session).get("pages") or ())

    def _session_page_by_id(self, session, page_id):
        for spec in self._session_page_specs(session):
            if spec.page_id == page_id:
                return spec
        return None

    def _session_denoise_generation(self, session):
        """某个会话当前的处理代次；活动源读窗口，非活动源读它保存的状态。"""
        manager = self.__dict__.get("source_manager")
        if session is None:
            return None
        if manager is not None and manager.is_active(session.session_id):
            return int(self.__dict__.get("shared_denoise_version", 0) or 0)
        state = session.state or {}
        if not state:
            return 0
        return int(state.get("shared_denoise_version") or 0)

    def _add_page_to_session(
        self,
        session,
        *,
        title,
        page_kind,
        source_module,
        source_page_id,
        source_title=None,
        data_scope_id=None,
        params=None,
    ):
        """把后台结果登记回它所属的（可能非活动的）数据源，不抢焦点。"""
        manager = self.__dict__.get("source_manager")
        workspace = self._session_workspace_state(session)
        if manager is None or not workspace:
            return None
        pages = workspace.setdefault("pages", [])
        final_title = unique_title_among({str(spec.title) for spec in pages}, title)
        spec = AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=final_title,
            page_kind=str(page_kind),
            source_module=str(source_module),
            source_page_id=source_page_id,
            source_title=source_title,
            data_scope_id=str(data_scope_id or self.FULL_SCOPE_ID),
            params=dict(params or {}),
        )
        spec.auto_title = final_title
        source = next((item for item in pages if item.page_id == source_page_id), None)
        control_state = copy.deepcopy(
            (source.params if source is not None else {}).get("control_state")
        )
        if control_state:
            # 结果页沿用来源页当时的控件状态，而不是当前活动源的控件状态。
            spec.params.setdefault("control_state", control_state)
        pages.append(spec)
        order = workspace.setdefault("order", [])
        if spec.page_id != workspace.get("home"):
            order.append(spec.page_id)
        manager.register_page(session.session_id, spec.page_id)
        return spec

    def _session_for_page(self, page_id):
        manager = self.__dict__.get("source_manager")
        if manager is None:
            return None
        return manager.session_for_page(page_id)
