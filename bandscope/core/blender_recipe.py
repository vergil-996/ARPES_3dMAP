"""Declarative Blender companions built from ordinary view recipes."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re

from bandscope.core.view_recipe import RecipeError, ViewRecipeDocument, digest, plain

BLENDER_RECIPE_FORMAT = "bandscope.blender-recipe"
BLENDER_RECIPE_SCHEMA_VERSION = 1
OVERRIDE_KEYS = frozenset({
    "levels", "levels_controls", "locked_range", "cmap", "level_info", "display",
    "render_quality", "include_zero", "opacity_mode", "opacity_multiplier",
    "removed_effects",
})
REMOVED_EFFECTS = frozenset({"local_enhancement", "opacity_multiplier"})


def blender_sidecar_path(blend_path):
    return str(blend_path) + ".bsview.json"


def blender_recipe_digest(document):
    return digest({
        "groups": document.get("groups"),
        "entries": document.get("entries"),
        "export_settings": document.get("export_settings"),
    })


def _object(value, label):
    if not isinstance(value, dict):
        raise RecipeError(f"{label} 必须是对象。")
    return value


def _override_from_snapshot(snapshot):
    payload = snapshot.payload or {}
    display = copy.deepcopy(payload.get("display") or {})
    display.pop("background_level_info", None)
    display["state"] = None
    return plain({
        "levels": list(snapshot.levels_params),
        "levels_controls": dict(zip(("black", "gray", "white"), snapshot.levels_params)),
        "locked_range": snapshot.locked_range,
        "cmap": snapshot.cmap_name,
        "level_info": payload.get("level_info"),
        "display": display,
        "render_quality": payload.get("render_quality"),
        "include_zero": bool(payload.get("include_zero", False)),
        "opacity_mode": payload.get("opacity_mode", "linear"),
        "opacity_multiplier": payload.get("opacity_multiplier"),
        "removed_effects": [
            name for name, present in (
                ("local_enhancement", payload.get("enhancement") is None),
                ("opacity_multiplier", payload.get("opacity_multiplier") is None),
            ) if present
        ],
    })


def build_blender_recipe(request, *, display_overrides=None):
    from bandscope.app_metadata import APP_VERSION

    groups = []
    entries = []
    group_ids = {}
    group_records = {}
    overrides = display_overrides or {}
    for entry in request.entries:
        snapshot = entry.snapshot
        title = str(snapshot.source_page_title or entry.entry_id)
        if not isinstance(snapshot.view_recipe, dict):
            detail = str(snapshot.recipe_error or "缺少有效页面配方").strip()
            raise RecipeError(f"页面“{title}”无法保存 Blender 复现配置：{detail}")
        recipe = ViewRecipeDocument(copy.deepcopy(snapshot.view_recipe)).validate().data
        for source in recipe.get("sources", {}).values():
            source.pop("path", None)
        session_key = str(snapshot.source_session_id or "legacy-frozen-project")
        if session_key not in group_ids:
            group_ids[session_key] = f"source-{len(group_ids) + 1:03d}"
            group_records[group_ids[session_key]] = {
                "group_id": group_ids[session_key], "entry_ids": [],
            }
            groups.append(group_records[group_ids[session_key]])
        group_id = group_ids[session_key]
        group_records[group_id]["entry_ids"].append(entry.entry_id)
        override = overrides.get(entry.entry_id)
        if override is None and entry.entry_id in overrides:
            override = _override_from_snapshot(snapshot)
        entries.append({
            "entry_id": entry.entry_id,
            "group_id": group_id,
            "recipe": recipe,
            "display_override": copy.deepcopy(override),
        })
    document = plain({
        "format": BLENDER_RECIPE_FORMAT,
        "schema_version": BLENDER_RECIPE_SCHEMA_VERSION,
        "app_version": APP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "groups": groups,
        "entries": entries,
        "export_settings": request.recipe_settings(),
    })
    document["state_digest"] = blender_recipe_digest(document)
    return BlenderRecipeDocument(document).validate().data


def updated_blender_recipe(original, snapshots):
    document = copy.deepcopy(original)
    by_id = {entry["entry_id"]: entry for entry in document["entries"]}
    for entry_id, snapshot in snapshots.items():
        if entry_id not in by_id:
            raise RecipeError(f"显示覆写包含不属于原配置的条目：{entry_id}")
        by_id[entry_id]["display_override"] = _override_from_snapshot(snapshot)
    document["state_digest"] = blender_recipe_digest(document)
    return BlenderRecipeDocument(document).validate().data


@dataclass
class BlenderRecipeDocument:
    data: dict

    def validate(self):
        document = _object(self.data, "Blender 复现配置")
        if (document.get("format") != BLENDER_RECIPE_FORMAT
                or type(document.get("schema_version")) is not int
                or document["schema_version"] != BLENDER_RECIPE_SCHEMA_VERSION):
            raise RecipeError("不是受支持的 BandScope Blender 复现配置版本。")
        groups = document.get("groups")
        entries = document.get("entries")
        if not isinstance(groups, list) or not groups or not isinstance(entries, list) or not entries:
            raise RecipeError("Blender 复现配置的来源分组或条目为空。")
        by_group = {}
        for group in groups:
            _object(group, "来源分组")
            group_id = group.get("group_id")
            if not isinstance(group_id, str) or not group_id or group_id in by_group:
                raise RecipeError("Blender 来源分组 ID 为空或重复。")
            if not isinstance(group.get("entry_ids"), list) or not group["entry_ids"]:
                raise RecipeError("Blender 来源分组没有条目顺序。")
            by_group[group_id] = group
        by_entry = {}
        primary_by_group = {}
        for entry in entries:
            _object(entry, "Blender 条目")
            entry_id = entry.get("entry_id")
            group_id = entry.get("group_id")
            if (not isinstance(entry_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", entry_id)
                    or entry_id in {".", ".."} or entry_id in by_entry):
                raise RecipeError("Blender 条目 ID 无效或重复。")
            if group_id not in by_group:
                raise RecipeError(f"条目 {entry_id} 的来源分组不存在。")
            recipe = ViewRecipeDocument(copy.deepcopy(entry.get("recipe"))).validate().data
            primary = recipe["primary_source_id"]
            if group_id in primary_by_group and primary_by_group[group_id] != primary:
                raise RecipeError(f"来源分组 {group_id} 混用了不同原始数据。")
            primary_by_group[group_id] = primary
            for source in recipe.get("sources", {}).values():
                if "path" in source:
                    raise RecipeError("Blender 复现配置不得保存原始数据路径。")
            override = entry.get("display_override")
            if override is not None:
                _object(override, "显示覆写")
                unknown = set(override).difference(OVERRIDE_KEYS)
                if unknown:
                    raise RecipeError(f"条目 {entry_id} 的显示覆写含未知字段：{sorted(unknown)}")
                removed = override.get("removed_effects", [])
                if (not isinstance(removed, list) or len(set(removed)) != len(removed)
                        or not set(removed).issubset(REMOVED_EFFECTS)):
                    raise RecipeError(f"条目 {entry_id} 的已移除效果记录无效。")
            entry["recipe"] = recipe
            by_entry[entry_id] = entry
        if set(by_entry) != {entry_id for group in groups for entry_id in group["entry_ids"]}:
            raise RecipeError("Blender 来源分组与条目集合不一致。")
        for group in groups:
            expected = [entry["entry_id"] for entry in entries if entry["group_id"] == group["group_id"]]
            if group["entry_ids"] != expected:
                raise RecipeError("Blender 条目顺序与来源分组记录不一致。")
        _object(document.get("export_settings"), "Blender 导出设置")
        if document.get("state_digest") != blender_recipe_digest(document):
            raise RecipeError("Blender 复现配置已被编辑；请重新冻结导出。")
        plain(document)
        return self

    def dumps(self):
        self.validate()
        return json.dumps(plain(self.data), ensure_ascii=False, indent=2, allow_nan=False) + "\n"

    @classmethod
    def load(cls, path):
        def reject(value):
            raise RecipeError(f"JSON 不允许 {value}，缺测值请使用 null。")

        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    raise RecipeError(f"JSON 字段重复：{key}")
                result[key] = value
            return result

        try:
            with open(path, encoding="utf-8-sig") as stream:
                data = json.load(stream, parse_constant=reject, object_pairs_hook=pairs)
            return cls(data).validate()
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise RecipeError(f"无法读取 Blender 复现配置：{exc}") from exc
