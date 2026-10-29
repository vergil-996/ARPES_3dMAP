"""Capture and restore view recipes without turning the main window into a codec."""
from __future__ import annotations

import copy
import inspect
import json
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np

from bandscope.core.view_recipe import (
    FORMAT, SCHEMA_VERSION, ProvenanceStore, RecipeError, ViewRecipeDocument,
    digest, plain, state_digest,
)
from bandscope.core.data_scope import DataScopeDescriptor, ScopedDataVolume
from bandscope.processing.compute_backends import ArrayLRUCache, BackendManager
from bandscope.ui.result_workspace import AnalysisPageSpec


class _Value:
    def __init__(self, value):
        self.state = value if isinstance(value, dict) else {"value": value}
    def value(self):
        return self.state.get("value", 0)
    def isChecked(self):
        return bool(self.value())
    def currentIndex(self):
        return int(self.state.get("index", self.value()))
    def currentText(self):
        return str(self.state.get("text", self.value()))


class _Controls:
    """Only data reads are available to the preflight computation adapter."""
    def __init__(self, state):
        self.state = state or {}
    def __getattr__(self, name):
        if name not in self.state:
            raise AttributeError(name)
        return _Value(self.state[name])
    def get_rotation_angle(self):
        return float(self.state.get("rotation_angle", 0))


class RecipeEvaluator:
    """Run existing numerical helpers against explicit data/state, never live widgets.

    The adapter binds the host's numerical methods to an independent Python
    object. Its arrays, controls and caches are isolated from the active window.
    No QWidget lifecycle or page activation occurs during preflight.
    """
    def __init__(self, host_type, core, document):
        self.host_type = host_type
        self.core = core
        self.original_raw_data = core.raw_data
        self.original_coords = core.coords
        self.base_raw_data = core.raw_data
        self.base_coords = core.coords
        self.backend_manager = BackendManager("CPU")
        self._rotation_cache = {}
        self._computed_volume_cache = ArrayLRUCache()
        self._second_derivative_volume_cache = None
        self._axis_prefix_cache = None
        self._last_compute_backend = "CPU"
        self.shared_denoise_version = 0
        self.data_scopes = {sid: DataScopeDescriptor(**scope) for sid, scope in document["scopes"].items()}
        self.pages = {p["page_id"]: AnalysisPageSpec(**{k: v for k, v in p.items()
                      if k in AnalysisPageSpec.__dataclass_fields__}) for p in copy.deepcopy(document["pages"])}
        self.left_workspace = SimpleNamespace(current_spec=lambda: None, page_by_id=self.pages.get)
        self.scope_values = {}
        self.global_denoise_methods = document["environment"].get("denoise_methods", [])
        self.denoise_controls = document["environment"].get("denoise_controls", {})
        for sid, scope in self.data_scopes.items():
            values = scope.extract(core.raw_data)
            if self.global_denoise_methods:
                self._validate_denoise_for_descriptor(self.global_denoise_methods, scope)
                from bandscope.rendering.refresh_pipeline import ComputeJob
                values = self.backend_manager.cpu.execute(ComputeJob("denoise_pipeline", {
                    "data": values, "methods": self.global_denoise_methods})).value
            self.scope_values[sid] = values

    def __getattr__(self, name):
        attr = inspect.getattr_static(self.host_type, name)
        if isinstance(attr, staticmethod):
            return attr.__func__
        if isinstance(attr, classmethod):
            return attr.__get__(None, self.host_type)
        if inspect.isfunction(attr):
            return attr.__get__(self, type(self))
        if isinstance(attr, (str, int, float, set, frozenset, tuple)):
            return attr
        raise AttributeError(name)

    def _is_current_page(self, spec):
        return False

    def _get_display_state_for_spec(self, spec):
        scope = self.data_scopes[spec.data_scope_id]
        values = self.scope_values[scope.scope_id]
        return (values if scope.is_full else ScopedDataVolume(scope, values), self.original_coords)

    def evaluate(self, spec):
        controls = spec.params.get("control_state", {})
        self.timeline_bar = _Controls(controls.get("image"))
        self.page_data = _Controls(controls.get("data_process"))
        self.page_render = _Controls(controls.get("render"))
        self.rotation_angle = self.timeline_bar.get_rotation_angle()
        self.home_slice_info = spec.params.get("home_slice_info")
        self.clip_ranges = spec.params.get("clip_ranges")
        self.precise_logical_bounds = spec.params.get("precise_logical_bounds")
        self.axis_source_mode = controls.get("axis_source_mode", "frame")
        result = self._compute_render_context(spec)
        if result is None or result.get("crop_empty"):
            raise RecipeError(f"无法恢复页面：{spec.title}")
        return result


class ViewRecipeMixin:
    def _recipe_store(self):
        return self.__dict__.setdefault("_view_recipe_store", ProvenanceStore())

    def _prepare_recipe_capture(self):
        if getattr(self.core, "source_identity", None) is not None:
            self._persist_active_page_state()

    def _recipe_environment(self):
        return plain({"denoise_methods": self.global_denoise_methods,
                      "denoise_controls": self._capture_global_denoise_control_state(),
                      "rotation_angle": self.rotation_angle})

    def _freeze_recipe_page(self, spec, *, params=None, inputs=None, environment=None, result=None,
                            verify_source=True):
        identity = getattr(self.core, "source_identity", None)
        if identity is None:
            raise RecipeError("原始数据缺少文件来源，请重新加载 NPZ 后更新预览。")
        params = copy.deepcopy(spec.params if params is None else params)
        params = {k: v for k, v in params.items() if not k.startswith("_restored_")}
        store = self._recipe_store()
        refs = list(inputs or self.__dict__.get("_recipe_origins", {}).get(spec.page_id, []))
        for curve in [params.get("base_curve", {})] + list(params.get("overlay_curves", [])):
            key = curve.get("recipe_id")
            if key and key in store.nodes and key not in refs:
                refs.append(key)
        source = identity.descriptor(self.core.source_layout, verify_current=verify_source)
        return store.add(source, spec.page_kind, params, refs,
                         environment=environment or self._recipe_environment(),
                         result_digest=digest(result) if result is not None else None)

    def _on_recipe_page_added(self, page_id):
        try:
            self._capture_recipe_page_origin(page_id)
        except Exception as exc:
            # Provenance failure must never escape a Qt signal and terminate
            # an otherwise usable analysis. Export reports missing evidence.
            self.__dict__.setdefault("_recipe_origin_errors", {})[page_id] = str(exc)

    def _capture_recipe_page_origin(self, page_id):
        if self.__dict__.get("_restoring_recipe") or getattr(self.core, "source_identity", None) is None:
            return
        spec = self.left_workspace.page_by_id(page_id)
        submitted_source = spec.params.get("plugin_analysis", {}).get("source_recipe_id")
        if submitted_source and submitted_source in self._recipe_store().nodes:
            self.__dict__.setdefault("_recipe_origins", {})[page_id] = [submitted_source]
            return
        parent = self.left_workspace.page_by_id(spec.source_page_id)
        if parent is None or parent is spec or parent.page_kind == "control_panel":
            return
        self._persist_active_page_state()
        key = self._freeze_recipe_page(parent, verify_source=False)
        self.__dict__.setdefault("_recipe_origins", {})[page_id] = [key]

    def _capture_view_recipe(self, snapshot, context):
        from bandscope.app_metadata import APP_VERSION
        workspace = self.left_workspace
        spec = workspace.page_by_id(snapshot.source_page_id)
        ancestors = set()
        current = spec
        while current is not None and current.page_id not in ancestors:
            ancestors.add(current.page_id)
            current = workspace.page_by_id(current.source_page_id)
        ancestors.add(workspace.home_page_id)
        pages, roots, scopes, results = [], [], {}, {}
        for pid in workspace._ordered_page_ids():
            if pid not in ancestors:
                continue
            item = workspace.page_by_id(pid)
            record = asdict(item)
            record["params"] = {k: v for k, v in record["params"].items() if not k.startswith("_restored_")}
            params = record["params"]
            if item.page_kind == "plugin_derived":
                saved = self.__dict__.get("_recipe_derived_results", {}).get(pid)
                if saved is None:
                    raise RecipeError(f"派生结果 {item.title} 缺少成功计算快照，请先打开该页面。")
                params = copy.deepcopy(saved["params"])
                # Control/display changes do not alter the successful scientific result.
                params["control_state"] = record["params"].get("control_state", {})
                record["params"] = params
                results[pid] = {**copy.deepcopy(saved), "params": params}
            saved = results.get(pid)
            key = self._freeze_recipe_page(item, params=params,
                    environment=saved.get("environment") if saved else None,
                    result=saved["context"] if saved else None)
            record["recipe_id"] = key
            roots.append(key)
            pages.append(record)
            scope = self._scope_for_spec(item)
            if scope is None:
                raise RecipeError(f"页面 {item.title} 的 ROI 来源丢失。")
            scopes[scope.scope_id] = asdict(scope)
        sources, nodes = self._recipe_store().closure(roots)
        for source in sources.values():
            source.pop("path", None)
        primary = self.core.source_identity.descriptor(self.core.source_layout)["source_id"]
        camera_pose = self._current_camera_view_pose() if snapshot.camera else None
        view = plain({"camera": snapshot.camera,
                      "camera_pose": asdict(camera_pose) if camera_pose else None,
                      "levels": snapshot.levels_params, "locked_range": snapshot.locked_range,
                      "levels_controls": dict(zip(("black", "gray", "white"), snapshot.levels_params)),
                      "cmap": snapshot.cmap_name, "level_info": snapshot.payload.get("level_info"),
                      "viewport_aspect": snapshot.payload.get("viewport_aspect"),
                      "opacity_multiplier": snapshot.payload.get("opacity_multiplier"),
                      "axis_titles": self._axis_titles().export_records(ancestors),
                      "limits": self._recipe_plot_limits(snapshot.view)})
        document = {"format": FORMAT, "schema_version": SCHEMA_VERSION,
                    "app_version": APP_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                    "primary_source_id": primary, "sources": sources, "recipes": nodes,
                    "pages": pages, "scopes": scopes, "target_page_id": spec.page_id,
                    "home_page_id": workspace.home_page_id, "environment": self._recipe_environment(),
                    "view_state": view, "results": results, "publication": {}}
        document["plugins"] = self._recipe_plugins()
        document["provenance_notes"] = {pid: error for pid, error in
            self.__dict__.get("_recipe_origin_errors", {}).items() if pid in ancestors}
        document["state_digest"] = state_digest(document)
        return plain(ViewRecipeDocument(plain(document)).validate().data)

    def _recipe_plot_limits(self, view):
        if view == "3d":
            return None
        return {"xlim": list(self.ax_2d.get_xlim()), "ylim": list(self.ax_2d.get_ylim())}

    def _remember_derived_recipe(self, spec, context):
        if getattr(self.core, "source_identity", None) is None:
            return
        self.__dict__.setdefault("_recipe_derived_results", {})[spec.page_id] = {
            "params": plain(spec.params), "context": plain(context),
            "plugins": self._recipe_plugins(), "environment": self._recipe_environment()}

    def _recipe_plugins(self):
        session = self.__dict__.get("plugin_session")
        if session is None:
            return []
        return [{"plugin_id": r.plugin_id, "version": str(r.manifest.version),
                 "digest": str(getattr(r, "started_digest", ""))}
                for r in session.manager.ready_plugins()]

    def _load_view_recipe(self, path, *, confirm=True, relocate=None):
        """把一份视图复现文件作为**新数据源**打开，不替换已打开的分析。"""
        from bandscope.core.analyzer_core import AnalyzerCore
        from bandscope.ui.file_dialogs import RememberingFileDialog as QFileDialog
        from bandscope.core.source_session import ENTRY_VIEW_RECIPE
        document = ViewRecipeDocument.load(path).data
        source = document["sources"][document["primary_source_id"]]
        if relocate is None:
            source_path, _ = QFileDialog.getOpenFileName(
                self, "选择原始 NPZ", "", "NumPy Files (*.npz)")
        else:
            source_path = relocate(source)
        if not source_path:
            return False
        core = AnalyzerCore()
        success, info = core.load_npz(str(source_path), layout=source["layout"])
        if not success:
            raise RecipeError(f"原始数据无法加载：{info}")
        actual = core.source_identity.descriptor(core.source_layout)
        if actual["sha256"] != source["sha256"]:
            raise RecipeError("所选 NPZ 的 SHA-256 与原始数据不一致，未新增数据源。")
        source["path"] = actual["path"]
        self._validate_recipe_publication(document)
        evaluator = RecipeEvaluator(type(self), core, document)
        results = document.get("results", {})
        for pid, page in evaluator.pages.items():
            if page.page_kind == "plugin_derived":
                saved = results.get(pid)
                if not isinstance(saved, dict) or not isinstance(saved.get("context"), dict):
                    raise RecipeError(f"派生结果 {page.title} 缺少结果快照。")
                if not self._recipe_recompute_derived(evaluator, page, saved):
                    evaluator.pages[pid].params["_restored_result"] = copy.deepcopy(saved)
            else:
                evaluator.evaluate(page)
        # 预检全部成功后才开始新源事务；失败或取消不留下半成品选项。
        previous_id = self._begin_source_transaction()
        try:
            target_page_id = self._apply_recipe_workspace(core, document, evaluator)
            self._commit_source_transaction(
                previous_id,
                entry_kind=ENTRY_VIEW_RECIPE,
                entry_path=str(path),
                primary_path=str(source_path),
                target_page_id=target_page_id,
            )
        except Exception:
            self._abort_source_transaction(previous_id)
            raise
        return True

    def _remap_recipe_pages(self, pages, document):
        """为运行时页面建立新的全局唯一 ID，并返回 ``(id 映射, 目标页)``。

        只在内存里的副本上映射：已校验文档的配方 ID、参数与摘要不动，后续导出
        由现有捕获流程重新生成文档。
        """
        id_map = {str(page_id): self._make_page_id() for page_id in pages}
        for page_id, spec in pages.items():
            spec.page_id = id_map[str(page_id)]
            source_id = getattr(spec, "source_page_id", None)
            if source_id in id_map:
                spec.source_page_id = id_map[source_id]
        target_id = id_map.get(str(document.get("target_page_id"))) or id_map.get(
            str(document.get("home_page_id"))
        )
        return id_map, target_id

    def _apply_recipe_workspace(self, core, document, evaluator):
        """把复现文件恢复进当前（新源）工作区；不发激活回调，由提交事务统一刷新。"""
        id_map, target_id = self._remap_recipe_pages(evaluator.pages, document)
        pages = list(evaluator.pages.values())
        home_id = id_map.get(str(document["home_page_id"]), pages[0].page_id if pages else None)
        results = {
            id_map[str(pid)]: copy.deepcopy(saved)
            for pid, saved in (document.get("results") or {}).items()
            if str(pid) in id_map
        }
        view_state = copy.deepcopy(document.get("view_state") or {})
        view_state["axis_titles"] = [
            {**record, "page_id": id_map.get(str(record.get("page_id")), record.get("page_id"))}
            for record in view_state.get("axis_titles") or []
        ]

        self._restoring_recipe = True
        self._syncing_controls = True
        try:
            session = self.__dict__.get("plugin_session")
            if session is not None:
                session.reset_panel_state()
            controller = self.__dict__.get("crop_controller")
            if controller is not None:
                # 只收起当前裁剪交互，保留其它数据源的选区草稿。
                controller.popup.hide()
                controller.selection = None
                controller.page_id = None
            self._clear_axis_crop_interaction(redraw=False)
            self._reset_axis_title_interaction()
            self._clear_interactive_box()
            self.axis_interval = self.axis_space = None

            self.core = core
            self._prime_loaded_data_state(core.source_identity.path)
            self._configure_loaded_time_controls(core.raw_data.shape)
            # _prime_loaded_data_state 已经重置数据域与去噪状态，这里换成复现记录。
            self.data_scopes = evaluator.data_scopes
            self._scope_generation = max(
                (scope.generation for scope in self.data_scopes.values()), default=0
            )
            self._scope_committed_signatures = {
                scope_id: self._denoise_signature(evaluator.global_denoise_methods)
                for scope_id in self.data_scopes
            }
            self.global_denoise_methods = copy.deepcopy(evaluator.global_denoise_methods)
            self.shared_denoise_signature = self._denoise_signature(self.global_denoise_methods)
            self.shared_denoised_raw_data = (
                evaluator.scope_values.get("full") if self.global_denoise_methods else None
            )
            for sid, values in evaluator.scope_values.items():
                if self.global_denoise_methods:
                    self._store_scope_denoise_result(sid, values, self.shared_denoise_signature)
            denoise = document["environment"].get("denoise_controls", {})
            self.page_render.restore_state(
                self._render_state_with_global_denoise({}, denoise), block_signals=True
            )
            self.page_control_blank.restore_state(
                self._detail_state_with_global_denoise({}, denoise), block_signals=True
            )

            self.active_page_spec = None
            self.left_workspace.replace_pages(pages, home_id, target_id)
            self.active_page_spec = self.left_workspace.current_spec()
            self.last_visual_page_id = (
                self.active_page_spec.page_id
                if self.active_page_spec is not None
                else home_id
            )
            self._axis_titles().restore_records(view_state.get("axis_titles", []))
            self._recipe_derived_results = results
            self._restored_publication = {
                target_id: copy.deepcopy(document.get("publication") or {})
            }
            self._restored_recipe_views = {target_id: view_state}
            self._recipe_scene_pending = {target_id}
            store = self._recipe_store()
            store.sources.update(copy.deepcopy(document["sources"]))
            store.nodes.update(copy.deepcopy(document["recipes"]))
            self._recipe_origins = {
                id_map[str(page["page_id"])]: list(
                    document["recipes"][page["recipe_id"]]["inputs"]
                )
                for page in document["pages"]
                if str(page["page_id"]) in id_map
            }
            for page in pages:
                if page.page_kind == "plugin_curve":
                    page.params["_restored_snapshot"] = True
            if view_state.get("opacity_multiplier") is not None and self.active_page_spec is not None:
                self.active_page_spec.params["_restored_effect"] = True
            self.left_workspace._refresh_header()
            self._bind_axis_interval(self.active_page_spec, animate=False)
            self._configure_time_controls()
            self._refresh_core_display_state()
            # 先把相机落到当前窗口：切源会保存这份状态，切回来时不能退回旧视角。
            self._apply_recipe_camera(view_state.get("camera"))
            # 复现文件明确决定视角（含 2D 页没有相机的情况），不再套用新数据默认取景。
            self.__dict__["_pending_default_camera"] = False
            from bandscope.rendering.render_core import VisualEngine
            VisualEngine._locked_data_range = view_state.get("locked_range")
        finally:
            self._syncing_controls = False
            self._restoring_recipe = False
        return target_id

    def _apply_recipe_camera(self, camera):
        """把复现文件里的相机姿态写到当前 3D 场景；不触发渲染。"""
        if not camera:
            return False
        try:
            cam = self.plotter.camera
            cam.position = camera["position"]
            cam.focal_point = camera["focal_point"]
            cam.up = camera["view_up"]
            cam.SetParallelProjection(bool(camera.get("parallel_projection")))
            for name, method in (
                ("parallel_scale", cam.SetParallelScale),
                ("view_angle", cam.SetViewAngle),
            ):
                if camera.get(name) is not None:
                    method(camera[name])
            if camera.get("clipping_range"):
                cam.clipping_range = camera["clipping_range"]
        except Exception:
            return False
        # 复现文件带来了明确视角：不要再被"新数据默认取景"覆盖。
        self.__dict__["_pending_default_camera"] = False
        return True

    def _restore_recipe_scene(self, spec):
        pending = self.__dict__.get("_recipe_scene_pending", set())
        if spec is None or spec.page_id not in pending:
            return
        pending.discard(spec.page_id)
        state = self.__dict__.get("_restored_recipe_views", {}).get(spec.page_id)
        if not state:
            return
        camera = state.get("camera")
        if camera:
            if self._apply_recipe_camera(camera):
                self._capture_3d_camera_position()
                self.plotter.render()
        elif state.get("limits"):
            self.ax_2d.set_xlim(state["limits"]["xlim"])
            self.ax_2d.set_ylim(state["limits"]["ylim"])
            self.canvas_2d.draw_idle()

    def _recipe_frozen_multiplier(self, context, *, stride=1):
        spec = self.left_workspace.current_spec()
        if spec is None:
            return None
        state = self.__dict__.get("_restored_recipe_views", {}).get(spec.page_id, {})
        values = state.get("opacity_multiplier")
        if values is None:
            return None
        # Stored values are already aligned to this page's ROI and raw E order.
        return np.asarray(values, dtype=float)[::stride]

    def _recipe_derived_context(self, spec, saved):
        context = copy.deepcopy(saved["context"])
        for key in ("energy_axis", "curves", "raw_curves", "k_values", "sample_indices"):
            if key in context:
                context[key] = np.asarray(context[key], dtype=float)
        context["restored_snapshot"] = True
        return context

    def _append_recipe_actions(self, menu):
        page = self.left_workspace.current_spec()
        if page is None or not page.params.get("_restored_effect"):
            return
        action = menu.addAction("使用当前插件重新计算效果")
        action.setEnabled(bool(self._recipe_plugins()))
        def release():
            self.__dict__.get("_restored_recipe_views", {}).get(page.page_id, {}).pop("opacity_multiplier", None)
            page.params.pop("_restored_effect", None)
            self.left_workspace._refresh_header()
            self.global_refresh()
        action.triggered.connect(release)

    @staticmethod
    def _validate_recipe_publication(document):
        pub = document["publication"]
        if not pub:
            return
        from bandscope.exporting.publication_models import resolve_style, OutputOptions
        style_id = pub.get("style_id", "")
        family = style_id.split("_")[0]
        if family not in ("1d", "2d", "3d") or resolve_style(family, style_id).style_id != style_id:
            raise RecipeError("复现文件引用的出版样式不受支持。")
        if not isinstance(pub.get("style_params"), dict) or not isinstance(pub.get("overrides"), dict):
            raise RecipeError("出版样式参数无效。")
        output = pub.get("output", {})
        options = OutputOptions.from_json(json.dumps(output))
        if json.loads(options.to_json()) != output:
            raise RecipeError("出版尺寸、分辨率或格式无效。")

    def _recipe_recompute_derived(self, evaluator, page, saved):
        session = self.__dict__.get("plugin_session")
        if session is None or saved.get("environment") != self._recipe_environment_from_evaluator(evaluator, page):
            return False
        plugin_id = page.params.get("derived_plugin_id")
        recorded = next((p for p in saved.get("plugins", []) if p["plugin_id"] == plugin_id), None)
        current = next((p for p in self._recipe_plugins() if p["plugin_id"] == plugin_id), None)
        if not recorded or not recorded.get("digest") or recorded != current:
            return False
        try:
            from bandscope.extensions.api import DerivedSource2D, validate_derived_waterfall
            evaluator.timeline_bar = _Controls(page.params.get("control_state", {}).get("image"))
            evaluator.rotation_angle = evaluator.timeline_bar.get_rotation_angle()
            controller = copy.copy(session._derived)
            controller.window = evaluator
            source = DerivedSource2D.from_mapping(page.params["derived_source"])
            input2d = controller.build_input(source, page.params["derived_params"], plugin_id)
            result = validate_derived_waterfall(controller._instance(plugin_id).compute_derived_result(input2d), input2d)
            context = controller._context_from_result(source, input2d, result)
            for key in ("energy_axis", "k_values", "raw_curves", "curves"):
                if not np.allclose(context[key], np.asarray(saved["context"][key], dtype=float),
                                   rtol=1e-5, atol=1e-7, equal_nan=True):
                    return False
            return True
        except Exception:
            return False

    @staticmethod
    def _recipe_environment_from_evaluator(evaluator, page):
        controls = page.params.get("control_state", {})
        return {"denoise_methods": evaluator.global_denoise_methods,
                "denoise_controls": getattr(evaluator, "denoise_controls", {}),
                "rotation_angle": controls.get("image", {}).get("rotation_angle", 0)}
