"""Versioned, declarative view recipes. No Qt, VTK or executable payloads."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FORMAT = "bandscope.view"
SCHEMA_VERSION = 1
PAGE_KINDS = frozenset({"home", "time_integral", "axis_integral", "axis_integral_crop",
                        "slice_dos", "energy_dos", "edc_curve", "second_derivative",
                        "curve_comparison_1d", "log_curve", "plugin_curve", "plugin_derived"})


class RecipeError(ValueError):
    """A recipe cannot be faithfully saved or restored."""


def plain(value):
    """Detach ordinary data; JSON null represents missing numeric samples."""
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, np.generic):
        return plain(value.item())
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isinf(value):
            raise RecipeError("复现记录中不能包含无穷值。")
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return {k: plain(v) for k, v in value.items()}
    raise RecipeError(f"不支持的复现参数类型：{type(value).__name__}")


def digest(value):
    encoded = json.dumps(plain(value), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def state_digest(document):
    """Paths may be relocated; scientific state must stay bound to its results."""
    keys = ("primary_source_id", "recipes", "pages", "scopes", "target_page_id",
            "home_page_id", "environment", "view_state", "results")
    state = {k: document.get(k) for k in keys}
    state["sources"] = {sid: {k: v for k, v in source.items() if k != "path"}
                        for sid, source in document.get("sources", {}).items()}
    return digest(state)


def file_stamp(path):
    stat = os.stat(path)
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)


def file_digest(path, expected_stamp=None):
    before = file_stamp(path)
    if expected_stamp is not None and before != expected_stamp:
        raise RecipeError("原始 NPZ 在加载后发生变化，请重新加载数据。")
    result = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    if file_stamp(path) != before:
        raise RecipeError("原始 NPZ 在读取过程中发生变化，请重新加载数据。")
    return result.hexdigest()


class SourceIdentity:
    """Hash starts at load time, bound to the file that produced the arrays."""
    def __init__(self, path):
        self.path = Path(path).resolve().as_posix()
        self.stamp = file_stamp(self.path)
        self.future = Future()

        def work():
            try:
                self.future.set_result(file_digest(self.path, self.stamp))
            except Exception as exc:
                self.future.set_exception(exc)
        threading.Thread(target=work, name="bandscope-source-hash", daemon=True).start()

    def descriptor(self, layout, *, verify_current=True):
        sha = self.future.result()
        if verify_current:
            try:
                current_stamp = file_stamp(self.path)
            except FileNotFoundError:
                # A completed load-time digest still identifies data after a move.
                current_stamp = self.stamp
            if current_stamp != self.stamp:
                raise RecipeError("原始 NPZ 在加载后发生变化，请重新加载数据。")
        return {"source_id": sha, "path": self.path, "sha256": sha,
                "size": self.stamp[0], "layout": plain(layout)}


class ProvenanceStore:
    """Immutable effective computations, retained across page/clipboard lifetimes."""
    def __init__(self):
        self.sources = {}
        self.nodes = {}

    def add(self, source, operation, params, inputs=(), **metadata):
        self.sources[source["source_id"]] = copy.deepcopy(source)
        node = plain({"operation": operation, "operation_version": 1,
                      "source_id": source["source_id"], "inputs": list(inputs),
                      "params": params, **metadata})
        key = digest(node)
        self.nodes.setdefault(key, node)
        return key

    def closure(self, roots):
        selected = {}
        def visit(key):
            if key in selected:
                return
            node = self.nodes[key]
            selected[key] = copy.deepcopy(node)
            for parent in node["inputs"]:
                visit(parent)
        for root in roots:
            visit(root)
        ids = {n["source_id"] for n in selected.values()}
        return ({key: copy.deepcopy(self.sources[key]) for key in ids}, selected)


def _object(value, name):
    if not isinstance(value, dict):
        raise RecipeError(f"{name} 必须是对象。")
    return value


@dataclass
class ViewRecipeDocument:
    data: dict

    def validate(self):
        d = _object(self.data, "复现文件")
        if d.get("format") != FORMAT or type(d.get("schema_version")) is not int or d["schema_version"] != 1:
            raise RecipeError("不是受支持的 BandScope 视图复现文件版本。")
        sources = _object(d.get("sources"), "sources")
        if d.get("primary_source_id") not in sources:
            raise RecipeError("缺少主原始数据来源。")
        for sid, source in sources.items():
            _object(source, "数据来源")
            if len(str(source.get("sha256", ""))) != 64 or source.get("source_id") != sid:
                raise RecipeError("数据来源摘要无效。")
            _object(source.get("layout"), "NPZ 解析信息")
        pages = d.get("pages")
        if not isinstance(pages, list) or not pages:
            raise RecipeError("页面树为空。")
        by_id = {}
        for page in pages:
            _object(page, "页面")
            pid = page.get("page_id")
            if not isinstance(pid, str) or not pid or pid in by_id:
                raise RecipeError("页面 ID 为空或重复。")
            if page.get("page_kind") not in PAGE_KINDS:
                raise RecipeError(f"不支持的页面类型：{page.get('page_kind')}")
            _object(page.get("params"), "页面参数")
            by_id[pid] = page
        if d.get("target_page_id") not in by_id or d.get("home_page_id") not in by_id:
            raise RecipeError("缺少目标页面或原始页面。")
        if by_id[d["home_page_id"]]["page_kind"] != "home":
            raise RecipeError("原始页面类型无效。")
        def check_graph(items, parents):
            visited, visiting = set(), set()
            def visit(key):
                if key in visiting:
                    raise RecipeError("来源依赖包含循环。")
                if key in visited:
                    return
                if key not in items:
                    raise RecipeError("来源依赖引用不存在的节点。")
                visiting.add(key)
                for p in parents(items[key]):
                    visit(p)
                visiting.remove(key)
                visited.add(key)
            for key in items:
                visit(key)
        check_graph(by_id, lambda p: [p["source_page_id"]] if p.get("source_page_id") else [])
        nodes = _object(d.get("recipes"), "recipes")
        for key, node in nodes.items():
            _object(node, "处理节点")
            if node.get("operation") not in PAGE_KINDS or node.get("operation_version") != 1:
                raise RecipeError("处理节点类型或版本不受支持。")
            if node.get("source_id") not in sources or not isinstance(node.get("inputs"), list):
                raise RecipeError("处理节点来源无效。")
            if digest(node) != key:
                raise RecipeError("处理配方已被编辑，不能继续使用原保存结果；请在软件内修改后重新导出。")
        check_graph(nodes, lambda n: n["inputs"])
        for page in pages:
            node = nodes.get(page.get("recipe_id"))
            if node is None or node["params"] != page["params"]:
                raise RecipeError("页面参数与计算配方不一致。")
            if node.get("result_digest") is not None:
                saved = d.get("results", {}).get(page["page_id"], {})
                if digest(saved.get("context")) != node["result_digest"]:
                    raise RecipeError("保存的结果快照与处理配方不一致。")
        for name in ("environment", "view_state", "scopes", "publication"):
            _object(d.get(name), name)
        if d.get("state_digest") != state_digest(d):
            raise RecipeError("科学参数或结果已被编辑；请在软件内修改后重新导出。")
        plain(d)
        return self

    def dumps(self):
        self.validate()
        document = copy.deepcopy(self.data)
        for source in document.get("sources", {}).values():
            source.pop("path", None)
        return json.dumps(plain(document), ensure_ascii=False, indent=2, allow_nan=False) + "\n"

    @classmethod
    def load(cls, path):
        def reject(value):
            raise RecipeError(f"JSON 不允许 {value}，缺测值请使用 null。")
        def pairs(values):
            obj = {}
            for key, value in values:
                if key in obj:
                    raise RecipeError(f"JSON 字段重复：{key}")
                obj[key] = value
            return obj
        try:
            with open(path, encoding="utf-8-sig") as stream:
                data = json.load(stream, parse_constant=reject, object_pairs_hook=pairs)
            return cls(data).validate()
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise RecipeError(f"无法读取复现文件：{exc}") from exc


def sidecar_path(image_path):
    return str(image_path) + ".bsview.json"
