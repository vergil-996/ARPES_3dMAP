"""Transactional restoration of multi-source Blender recipe companions."""
from __future__ import annotations

import copy
import json

from bandscope.app.view_recipe_integration import RecipeEvaluator
from bandscope.core.blender_recipe import BLENDER_RECIPE_FORMAT, BlenderRecipeDocument
from bandscope.core.display_style import create_display_state
from bandscope.core.local_enhancement import RENDER_QUALITY_KEY, STATE_KEY
from bandscope.core.source_session import ENTRY_VIEW_RECIPE, new_session_id
from bandscope.core.view_recipe import (
    FORMAT as VIEW_RECIPE_FORMAT,
    RecipeError,
    ViewRecipeDocument,
    digest,
    state_digest,
)


class BlenderRecipeMixin:
    def _load_recipe_by_format(self, path, *, confirm=True, relocate=None):
        try:
            with open(path, encoding="utf-8-sig") as stream:
                document_format = (json.load(stream) or {}).get("format")
        except (OSError, ValueError, TypeError) as exc:
            raise RecipeError(f"无法读取复现文件：{exc}") from exc
        if document_format == VIEW_RECIPE_FORMAT:
            return self._load_view_recipe(path, confirm=confirm, relocate=relocate)
        if document_format == BLENDER_RECIPE_FORMAT:
            return self._load_blender_recipe(path, confirm=confirm, relocate=relocate)
        raise RecipeError("不是受支持的 BandScope 复现文件格式。")

    @staticmethod
    def _blender_science_signature(document, page):
        node = document["recipes"][page["recipe_id"]]
        params = copy.deepcopy(node["params"])
        params.pop(STATE_KEY, None)
        params.pop(RENDER_QUALITY_KEY, None)
        params.pop("_legacy_display", None)
        controls = params.get("control_state") or {}
        controls.pop("render", None)
        (controls.get("image") or {}).pop("rotation_angle", None)
        environment = copy.deepcopy(node.get("environment") or {})
        environment.pop("rotation_angle", None)
        return digest({
            "operation": node["operation"],
            "source_id": node["source_id"],
            "source_page_id": page.get("source_page_id"),
            "data_scope_id": page.get("data_scope_id"),
            "params": params,
            "environment": environment,
            "result_digest": node.get("result_digest"),
        })

    def _merge_blender_recipe_entries(self, entries):
        documents = [entry["recipe"] for entry in entries]
        merged = copy.deepcopy(documents[0])
        merged["pages"] = []
        merged["recipes"] = {}
        merged["results"] = {}
        merged["sources"] = {}
        merged["scopes"] = {}
        merged["plugins"] = []
        pages_by_id = {}
        target_seen = {}
        states = []
        source_environment = None
        for entry, document in zip(entries, documents):
            entry_id = entry["entry_id"]
            self._validate_recipe_publication(document)
            environment = document.get("environment") or {}
            denoise = {
                "denoise_methods": environment.get("denoise_methods"),
                "denoise_controls": environment.get("denoise_controls"),
            }
            if source_environment is None:
                source_environment = denoise
            elif denoise != source_environment:
                raise RecipeError(f"条目 {entry_id} 的源级去噪或数据域不一致，请重新冻结导出。")
            if document["home_page_id"] != merged["home_page_id"]:
                raise RecipeError(f"条目 {entry_id} 的共同祖先不一致，请重新冻结导出。")
            for source_id, source in document["sources"].items():
                previous = merged["sources"].get(source_id)
                comparable = {key: value for key, value in source.items() if key != "path"}
                if previous is not None and {
                        key: value for key, value in previous.items() if key != "path"} != comparable:
                    raise RecipeError(f"条目 {entry_id} 的原始数据布局发生冲突。")
                merged["sources"][source_id] = copy.deepcopy(source)
            for scope_id, scope in document["scopes"].items():
                previous = merged["scopes"].get(scope_id)
                if previous is not None and previous != scope:
                    raise RecipeError(f"条目 {entry_id} 的数据域 {scope_id} 发生真实冲突。")
                merged["scopes"][scope_id] = copy.deepcopy(scope)
            for key, node in document["recipes"].items():
                previous = merged["recipes"].get(key)
                if previous is not None and previous != node:
                    raise RecipeError("处理配方摘要冲突，请重新冻结导出。")
                merged["recipes"][key] = copy.deepcopy(node)
            for plugin in document.get("plugins") or []:
                if plugin not in merged["plugins"]:
                    merged["plugins"].append(copy.deepcopy(plugin))
            for page in document["pages"]:
                page_id = page["page_id"]
                signature = self._blender_science_signature(document, page)
                previous = pages_by_id.get(page_id)
                if previous is not None and previous[1] != signature:
                    raise RecipeError(
                        f"页面“{page.get('title', page_id)}”的重复记录存在真实科学参数冲突，请重新冻结导出。"
                    )
                is_target = page_id == document["target_page_id"]
                restored_id = page_id
                if is_target and page_id in target_seen:
                    restored_id = f"{page_id}@@{entry_id}"
                    if restored_id in pages_by_id:
                        raise RecipeError(f"条目 {entry_id} 的重复页面身份无效。")
                    restored = copy.deepcopy(page)
                    restored["page_id"] = restored_id
                    merged["pages"].append(restored)
                    pages_by_id[restored_id] = (restored, signature)
                    if page_id in document.get("results", {}):
                        merged["results"][restored_id] = copy.deepcopy(document["results"][page_id])
                elif previous is None:
                    restored = copy.deepcopy(page)
                    merged["pages"].append(restored)
                    pages_by_id[page_id] = (restored, signature)
                    if page_id in document.get("results", {}):
                        merged["results"][page_id] = copy.deepcopy(document["results"][page_id])
                if is_target:
                    target_seen[page_id] = entry_id
                    states.append({
                        "entry_id": entry_id,
                        "target_page_id": restored_id,
                        "view_state": copy.deepcopy(document.get("view_state") or {}),
                        "publication": copy.deepcopy(document.get("publication") or {}),
                        "display_override": copy.deepcopy(entry.get("display_override")),
                    })
        merged["target_page_id"] = states[0]["target_page_id"]
        merged["state_digest"] = state_digest(merged)
        return ViewRecipeDocument(merged).validate().data, states

    def _load_blender_recipe(self, path, *, confirm=True, relocate=None):
        from bandscope.core.analyzer_core import AnalyzerCore
        from bandscope.exporting.blender_project import request_from_recipe_settings
        from bandscope.ui.file_dialogs import RememberingFileDialog as QFileDialog

        document = BlenderRecipeDocument.load(path).data
        request_from_recipe_settings(document["export_settings"], [object()])
        entries = {entry["entry_id"]: entry for entry in document["entries"]}
        prepared = []
        for index, group in enumerate(document["groups"], 1):
            group_entries = [entries[entry_id] for entry_id in group["entry_ids"]]
            merged, states = self._merge_blender_recipe_entries(group_entries)
            source = merged["sources"][merged["primary_source_id"]]
            if relocate is None:
                source_path, _ = QFileDialog.getOpenFileName(
                    self,
                    f"选择原始 NPZ（来源 {index}/{len(document['groups'])}，{len(group_entries)} 个页面）",
                    "",
                    "NumPy Files (*.npz)",
                )
            else:
                source_path = relocate(source)
            if not source_path:
                return False
            core = AnalyzerCore()
            success, info = core.load_npz(str(source_path), layout=source["layout"])
            if not success:
                raise RecipeError(f"来源 {index} 的原始数据无法加载：{info}")
            actual = core.source_identity.descriptor(core.source_layout)
            if actual["sha256"] != source["sha256"]:
                raise RecipeError(f"来源 {index} 所选 NPZ 的 SHA-256 与原始数据不一致，未新增任何数据源。")
            source["path"] = actual["path"]
            evaluator = RecipeEvaluator(type(self), core, merged)
            results = merged.get("results", {})
            for page_id, page in evaluator.pages.items():
                if page.page_kind == "plugin_derived":
                    saved = results.get(page_id)
                    if not isinstance(saved, dict) or not isinstance(saved.get("context"), dict):
                        raise RecipeError(f"派生结果 {page.title} 缺少结果快照。")
                    if not self._recipe_recompute_derived(evaluator, page, saved):
                        evaluator.pages[page_id].params["_restored_result"] = copy.deepcopy(saved)
                else:
                    evaluator.evaluate(page)
            prepared.append({
                "group_id": group["group_id"],
                "core": core,
                "document": merged,
                "evaluator": evaluator,
                "states": states,
                "source_path": str(source_path),
            })
        return self._commit_blender_recipe_workspaces(path, document, prepared)

    @staticmethod
    def _apply_blender_display_override(spec, view_state, override):
        if not override:
            return
        for key in ("levels", "levels_controls", "locked_range", "cmap", "level_info",
                    "display", "render_quality"):
            if key in override:
                view_state[key] = copy.deepcopy(override[key])
        display = copy.deepcopy(override.get("display") or {})
        style = display.get("background_style")
        controls = spec.params.setdefault("control_state", {})
        render = controls.setdefault("render", {})
        levels = override.get("levels", (0.0, 50.0, 100.0))
        for key, value in zip(("s_low", "s_gamma", "s_up"), levels):
            render[key] = {**(render.get(key) or {}), "value": value}
        if style is not None:
            render["display_style"] = copy.deepcopy(style)
            render["combo_map"] = style.get("opacity_mode", render.get("combo_map"))
        render["render_quality"] = copy.deepcopy(override.get("render_quality"))
        render["display_range"] = display.get("resolved_range")
        spec.params[RENDER_QUALITY_KEY] = copy.deepcopy(override.get("render_quality"))
        removed = set(override.get("removed_effects") or ())
        if "local_enhancement" in removed:
            spec.params[STATE_KEY] = create_display_state(style)
        elif style is not None and spec.params.get(STATE_KEY):
            spec.params[STATE_KEY]["background"] = copy.deepcopy(style)
        if "opacity_multiplier" in removed:
            view_state["opacity_multiplier"] = None
            spec.params.pop("_restored_effect", None)
        elif "opacity_multiplier" in override:
            view_state["opacity_multiplier"] = copy.deepcopy(override["opacity_multiplier"])
        if display:
            view_state["display"] = display
        view_state["render_quality"] = copy.deepcopy(override.get("render_quality"))

    def _apply_blender_group_workspace(self, prepared):
        target_id = self._apply_recipe_workspace(
            prepared["core"], prepared["document"], prepared["evaluator"],
        )
        id_map = self.__dict__.pop("_last_recipe_id_map")
        restored_views = {}
        restored_publication = {}
        pending = set()
        title_records = []
        selections = []
        for state in prepared["states"]:
            runtime_id = id_map[state["target_page_id"]]
            spec = self.left_workspace.page_by_id(runtime_id)
            view_state = copy.deepcopy(state["view_state"])
            self._apply_blender_display_override(spec, view_state, state["display_override"])
            view_state["axis_titles"] = [
                {**record, "page_id": id_map.get(str(record.get("page_id")), record.get("page_id"))}
                for record in view_state.get("axis_titles") or []
            ]
            title_records.extend(view_state["axis_titles"])
            restored_views[runtime_id] = view_state
            restored_publication[runtime_id] = copy.deepcopy(state["publication"])
            pending.add(runtime_id)
            if view_state.get("opacity_multiplier") is not None:
                spec.params["_restored_effect"] = True
            else:
                spec.params.pop("_restored_effect", None)
            selections.append((state["entry_id"], runtime_id))
        self._axis_titles().restore_records(title_records)
        self._restored_recipe_views = restored_views
        self._restored_publication = restored_publication
        self._recipe_scene_pending = pending
        first_view = restored_views[target_id]
        self._apply_recipe_camera(first_view.get("camera"))
        from bandscope.rendering.render_core import VisualEngine

        VisualEngine._locked_data_range = first_view.get("locked_range")
        return target_id, selections

    def _commit_blender_recipe_workspaces(self, path, document, prepared):
        store = self._recipe_store()
        old_sources = copy.deepcopy(store.sources)
        old_nodes = copy.deepcopy(store.nodes)
        previous_id = self._begin_source_transaction()
        added = []
        states = []
        try:
            for group in prepared:
                self.left_workspace.detach_source_state()
                session_id = new_session_id()
                self._source_pending_id = session_id
                target_id, selections = self._apply_blender_group_workspace(group)
                state = self._capture_source_state()
                states.append({
                    "session_id": session_id,
                    "state": state,
                    "source_path": group["source_path"],
                    "target_id": target_id,
                    "selections": selections,
                })
            self.left_workspace.detach_source_state()
            self._source_pending_id = None
            seen_pages = set()
            for item in states:
                page_ids = [spec.page_id for spec in item["state"]["workspace"]["pages"]]
                if seen_pages.intersection(page_ids) or any(
                        self.source_manager.owner_of(page_id) is not None for page_id in page_ids):
                    raise RecipeError("恢复后的页面 ID 发生冲突，未新增任何数据源。")
                seen_pages.update(page_ids)
            for item in states:
                session = self.source_manager.add(
                    entry_kind=ENTRY_VIEW_RECIPE,
                    entry_path=str(path),
                    primary_path=item["source_path"],
                    session_id=item["session_id"],
                    state=item["state"],
                    activate=False,
                )
                added.append(session.session_id)
                session.target_page_id = item["target_id"]
                self.source_manager.register_pages(
                    session.session_id,
                    [spec.page_id for spec in item["state"]["workspace"]["pages"]],
                    home_page_id=item["state"]["workspace"]["home"],
                )
                self._sync_source_home_title(session)
            first = states[0]
            self.source_manager.set_active(first["session_id"])
            self._install_source_state(first["state"])
            self._sync_source_switcher()
            self._activate_current_session()
            selections = []
            for item in states:
                selections.extend(
                    (item["session_id"], runtime_id) for _entry_id, runtime_id in item["selections"]
                )
            if not self.open_blender_project_dialog():
                raise RuntimeError("Blender 导出面板无法打开。")
            dialog = self.__dict__.get("_blender_project_dialog")
            dialog.export_panel.apply_recipe_settings(document["export_settings"], selections)
            return True
        except Exception:
            for session_id in reversed(added):
                self.source_manager.close(session_id)
            self._abort_source_transaction(previous_id)
            store.sources = old_sources
            store.nodes = old_nodes
            self.__dict__.pop("_last_recipe_id_map", None)
            raise
