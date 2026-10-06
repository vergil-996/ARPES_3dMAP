# -*- coding: utf-8 -*-
"""主程序升级前的插件影响评估（阶段 E）。

升级提示要能回答“升级之后我的插件会怎样”。评估**只读清单**、不导入插件代码，
并且用**目录声明的目标宿主版本与接口集合**判断，不能把当前接口当成未来宿主的
接口——那正是升级后插件集体加载失败的典型原因。

四类结论，与计划里的清单一一对应：

- :data:`IMPACT_OK`：升级后仍可继续使用；
- :data:`IMPACT_PAUSED`：升级后不再兼容，启动时会暂停加载并显示原因；
- :data:`IMPACT_UPDATE`：目录里存在适用于目标宿主的更新包；
- :data:`IMPACT_UNKNOWN`：元数据缺失或离线，兼容性无法确认。

存在暂停或无法确认时，更新提示默认选择“稍后”，由用户明确继续。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from bandscope.extensions.catalog import (
    STATUS_INCOMPATIBLE,
    STATUS_INSTALLABLE,
    STATUS_UPDATE_AVAILABLE,
    Catalog,
    match_catalog_entry,
)
from bandscope.extensions.compat import evaluate_compatibility

IMPACT_OK = "ok"
IMPACT_PAUSED = "paused"
IMPACT_UPDATE = "update"
IMPACT_UNKNOWN = "unknown"


@dataclass(frozen=True)
class PluginImpactItem:
    """单个插件在升级后的状态。"""

    plugin_id: str
    name: str
    installed_version: str
    category: str
    reason: str = ""
    target_version: str = ""


@dataclass(frozen=True)
class PluginImpactReport:
    """一次升级评估的完整结果。"""

    items: Tuple[PluginImpactItem, ...] = ()
    catalog_available: bool = False
    target_version: str = ""
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def by_category(self, category: str) -> List[PluginImpactItem]:
        return [item for item in self.items if item.category == category]

    @property
    def has_blocking(self) -> bool:
        """是否存在“升级后不能用”或“无法确认”的插件。"""
        return bool(self.by_category(IMPACT_PAUSED) or self.by_category(IMPACT_UNKNOWN))

    def summary_lines(self) -> List[str]:
        """给升级提示用的中文清单；没有插件时返回空列表。"""
        if not self.items:
            return []
        lines = []
        for category, label in (
            (IMPACT_PAUSED, "升级后暂停加载"),
            (IMPACT_UPDATE, "存在匹配更新"),
            (IMPACT_UNKNOWN, "兼容性无法确认"),
            (IMPACT_OK, "可继续使用"),
        ):
            entries = self.by_category(category)
            if not entries:
                continue
            names = "、".join(
                f"{item.name} {item.installed_version}".strip() for item in entries
            )
            lines.append(f"{label}（{len(entries)}）：{names}")
        return lines


def _manifest_fields(manifest) -> Optional[dict]:
    if manifest is None:
        return None
    return {
        "requires_app": manifest.requires_app,
        "api_version": manifest.api_version,
        "capabilities": tuple(manifest.capabilities),
    }


def evaluate_upgrade(
    manager,
    catalog: Optional[Catalog],
    *,
    target_version: str,
    target_apis: Optional[Sequence[int]] = None,
) -> PluginImpactReport:
    """评估已安装插件在目标宿主上的可用性。

    ``manager`` 需要提供 ``plugins()`` 与 ``installed_manifest(record)``；后者
    只读清单、不导入插件代码。``catalog`` 为 None（离线或目录验签失败）时所有
    插件都归入“兼容性无法确认”，而不是想当然地认为还能用。
    """
    notes: List[str] = []
    items: List[PluginImpactItem] = []
    apis = tuple(target_apis) if target_apis is not None else None

    for record in manager.plugins():
        if record.pending_removal or not record.enabled:
            # 用户已经决定不用它，不参与升级影响评估。
            continue
        manifest = manager.installed_manifest(record)
        fields = _manifest_fields(manifest)
        name = str(getattr(manifest, "name", "") or record.plugin_id)
        version = str(record.version or "")

        if not fields or catalog is None:
            items.append(
                PluginImpactItem(
                    record.plugin_id,
                    name,
                    version,
                    IMPACT_UNKNOWN,
                    reason=(
                        "找不到插件清单，无法评估兼容性。"
                        if not fields
                        else "离线或没有可用的签名目录，无法确认升级后是否兼容。"
                    ),
                )
            )
            continue

        catalog_apis = apis if apis is not None else catalog.host_api_versions
        verdict = evaluate_compatibility(
            name=name,
            requires_app=fields["requires_app"],
            api_version=fields["api_version"],
            capabilities=fields["capabilities"],
            app_version=target_version,
            supported_apis=catalog_apis,
        )
        if not verdict.ok:
            items.append(
                PluginImpactItem(
                    record.plugin_id, name, version, IMPACT_PAUSED, reason=verdict.reason
                )
            )
            continue

        match = match_catalog_entry(
            catalog,
            record.plugin_id,
            installed_version=version,
            app_version=target_version,
            supported_apis=catalog_apis,
        )
        if match.status == STATUS_UPDATE_AVAILABLE:
            items.append(
                PluginImpactItem(
                    record.plugin_id, name, version, IMPACT_UPDATE,
                    target_version=match.entry.version if match.entry else "",
                )
            )
        elif match.status == STATUS_INCOMPATIBLE:
            # 当前版本能在目标宿主上跑，但目录里没有匹配更新：仍然可以用，
            # 只是不能在升级后换成官方新包，如实说明。
            items.append(
                PluginImpactItem(
                    record.plugin_id, name, version, IMPACT_OK,
                    reason=f"目录里没有适用于目标宿主的更新：{match.reason}",
                )
            )
        elif match.status == STATUS_INSTALLABLE and match.reason:
            items.append(
                PluginImpactItem(
                    record.plugin_id, name, version, IMPACT_OK, reason=match.reason
                )
            )
        else:
            items.append(PluginImpactItem(record.plugin_id, name, version, IMPACT_OK))

    if not catalog:
        notes.append("没有可用的官方插件目录，插件兼容性无法确认。")
    return PluginImpactReport(
        items=tuple(items),
        catalog_available=catalog is not None,
        target_version=str(target_version),
        notes=tuple(notes),
    )


def format_impact(report: PluginImpactReport) -> str:
    """把影响清单拼成一段可直接放进提示框的文字。"""
    lines = report.summary_lines()
    if not lines:
        return "本机没有已启用的插件，升级不会影响插件。"
    prefix = ""
    if report.has_blocking:
        prefix = "升级后部分插件会被暂停加载。\n"
    return prefix + "\n".join(lines)
