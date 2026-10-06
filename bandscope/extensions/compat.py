# -*- coding: utf-8 -*-
"""统一的版本与能力兼容判定。

对照「插件管理入口与系统完善计划」阶段 C。安装事务、启动加载、发布构建脚本、
官方目录筛选与主程序升级评估都调用这里的同一组纯函数：同一份声明在哪儿都得
到同一个结论和同一句可读原因，不允许各写一套近似判断。

规则（依据 https://packaging.pypa.io/en/stable/specifiers.html）：

- ``requires_app`` 是**裸版本**（不含任何运算符）时按精确版本匹配，旧清单原样
  解释，不自动放宽也不重写；含运算符时按 ``SpecifierSet`` 解析成版本范围，
  例如 ``>=1.10.0,<1.11.0``。
- ``api_version`` 是整数。宿主声明一个**支持集合**，插件声明的版本必须落在
  集合内；不能因为主程序小版本接近就认定接口兼容。
- 插件声明的每一项能力都必须由宿主支持，缺一不可。

版本比较一律走 ``packaging.version.Version``，不再抽数字比元组：后者会把
``1.10`` 判成小于 ``1.9``，也无法表达预发布版本。

本模块只依赖 ``packaging``，不导入 Qt / VTK，也不导入插件代码。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Tuple

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

#: 能力标识：插件可以提交沿能量轴的不透明度倍率。
CAPABILITY_OPACITY_MULTIPLIER = "opacity_multiplier"
#: 能力标识：插件可以抓取已完成二维结果的只读快照。
CAPABILITY_DATA_SNAPSHOT_2D = "data_snapshot_2d"
#: 能力标识：插件可以提交后台分析任务。
CAPABILITY_ANALYSIS_TASK = "analysis_task"
#: 能力标识：插件可以交回一维曲线结果，由宿主建结果页。
CAPABILITY_RESULT_CURVE_1D = "result_curve_1d"

#: 分析插件（API 2）必须同时声明这三项能力，缺一不可。
ANALYSIS_CAPABILITIES = (
    CAPABILITY_DATA_SNAPSHOT_2D,
    CAPABILITY_ANALYSIS_TASK,
    CAPABILITY_RESULT_CURVE_1D,
)

#: 宿主声明支持的插件接口版本集合。插件清单里的 ``api_version`` 必须是其中
#: 之一才会被加载；新增接口版本时在这里追加，而不是让旧判断“近似通过”。
SUPPORTED_API_VERSIONS = frozenset({1, 2})

#: 宿主声明支持的能力集合。插件声明的能力必须全部在这里。
SUPPORTED_CAPABILITIES = frozenset(
    {CAPABILITY_OPACITY_MULTIPLIER, *ANALYSIS_CAPABILITIES}
)

#: 当前宿主接口版本（支持集合里最高的一个），供插件与构建脚本引用。
API_VERSION = max(SUPPORTED_API_VERSIONS)

#: 版本声明里出现这些字符就按范围解析；PEP 440 的版本号本身不含它们。
_RANGE_CHARS = set("<>=!~,")


@dataclass(frozen=True)
class AppRequirement:
    """``requires_app`` 的解析结果。

    ``exact`` 为真表示清单写的是裸版本，按精确匹配；否则按 ``specifier``
    判断是否落在范围内。``error`` 非空表示声明本身无法解析——调用方应当把
    它当成“不兼容”，并原样把原因展示给用户，而不是猜一个宽范围。
    """

    raw: str
    exact: bool = True
    version: Optional[Version] = None
    specifier: Optional[SpecifierSet] = None
    error: str = ""

    @property
    def valid(self) -> bool:
        return not self.error

    def accepts(self, app_version: str) -> bool:
        """当前主程序版本是否落在声明范围内。"""
        if not self.valid:
            return False
        try:
            candidate = Version(str(app_version).strip())
        except InvalidVersion:
            return False
        if self.exact:
            # 裸版本走 ``==`` 说明符而不是 ``Version.__eq__``：两处用同一套
            # PEP 440 语义，零填充（1.11.2 与 1.11.2.0）不会被判成不兼容。
            return SpecifierSet(f"=={self.version}").contains(candidate)
        assert self.specifier is not None
        return self.specifier.contains(candidate)

    def describe(self) -> str:
        if not self.valid:
            return f"{self.raw}（无效声明：{self.error}）"
        return self.raw


def parse_requires_app(value) -> AppRequirement:
    """解析 ``requires_app``；非法声明返回带 ``error`` 的结果，不抛异常。"""
    text = str(value or "").strip()
    if not text:
        return AppRequirement("", error="未声明所需的主程序版本")
    if any(char in _RANGE_CHARS for char in text):
        try:
            specifier = SpecifierSet(text)
        except InvalidSpecifier:
            return AppRequirement(text, exact=False, error=f"不是有效的版本范围：{text!r}")
        if not list(specifier):
            return AppRequirement(text, exact=False, error=f"版本范围为空：{text!r}")
        return AppRequirement(text, exact=False, specifier=specifier)
    try:
        version = Version(text)
    except InvalidVersion:
        return AppRequirement(text, error=f"不是有效的版本号：{text!r}")
    return AppRequirement(text, exact=True, version=version)


@dataclass(frozen=True)
class CompatibilityVerdict:
    """一次兼容判定的完整结论。

    ``reason`` 是第一条失败原因，直接用于界面与日志；三项分开保留，方便官方
    目录和升级评估向用户解释“为什么不兼容”。
    """

    ok: bool = True
    reason: str = ""
    api_ok: bool = True
    app_ok: bool = True
    capabilities_ok: bool = True
    missing_capabilities: Tuple[str, ...] = field(default_factory=tuple)


def evaluate_compatibility(
    *,
    name: str,
    requires_app,
    api_version,
    capabilities: Iterable[str] = (),
    app_version: str,
    supported_apis: Iterable[int] = SUPPORTED_API_VERSIONS,
    supported_capabilities: Iterable[str] = SUPPORTED_CAPABILITIES,
) -> CompatibilityVerdict:
    """判定一份插件声明能否与当前宿主配合工作。

    判定顺序固定为「接口版本 → 主程序版本 → 能力」，与安装、加载、目录筛选
    完全一致；第一项失败即给出原因，``reason`` 就是用户看到的那句话。
    """
    label = str(name or "插件")
    supported = frozenset(int(item) for item in supported_apis)
    try:
        declared_api = int(api_version)
    except (TypeError, ValueError):
        declared_api = None

    if declared_api is None or declared_api not in supported:
        rendered = "、".join(str(item) for item in sorted(supported)) or "（无）"
        reason = (
            f"{label} 需要宿主接口版本 {api_version}，当前主程序提供 {rendered}。"
        )
        return CompatibilityVerdict(
            ok=False, reason=reason, api_ok=False
        )

    requirement = parse_requires_app(requires_app)
    if not requirement.accepts(app_version):
        if requirement.valid:
            reason = (
                f"{label} 需要主程序 {requirement.describe()}，当前为 {app_version}。"
            )
        else:
            reason = f"{label} 的主程序版本声明无法解析：{requirement.error}。"
        return CompatibilityVerdict(
            ok=False, reason=reason, app_ok=False
        )

    supported_caps = frozenset(str(item) for item in supported_capabilities)
    declared_caps = tuple(str(item) for item in (capabilities or ()))
    missing = tuple(sorted(item for item in declared_caps if item not in supported_caps))
    if missing:
        reason = f"{label} 需要宿主尚未支持的能力：{'、'.join(missing)}。"
        return CompatibilityVerdict(
            ok=False, reason=reason, capabilities_ok=False,
            missing_capabilities=missing,
        )

    return CompatibilityVerdict(ok=True)


# ---------------------------------------------------------------------------
# 版本排序（官方目录与升级评估共用）
# ---------------------------------------------------------------------------


def sortable_version(value) -> Optional[Version]:
    """把插件自报版本转成可排序版本；非标准版本返回 None。

    历史插件可能写着 ``1.0.0-beta1`` 之外的自定义串。这类版本仍可在本地登记表
    与界面上如实展示，但不参加在线“最新版本”比较——拿它排序会把不可比的字符串
    排成看似权威的结论。
    """
    try:
        return Version(str(value or "").strip())
    except InvalidVersion:
        return None


def is_prerelease(value) -> bool:
    """是否为预发布版本（稳定目录默认不推荐、不自动选中）。"""
    parsed = sortable_version(value)
    return bool(parsed is not None and parsed.is_prerelease)


def compare_versions(left, right) -> Optional[int]:
    """比较两个版本；任一不可排序时返回 None（表示不可比，而不是相等）。"""
    left_version = sortable_version(left)
    right_version = sortable_version(right)
    if left_version is None or right_version is None:
        return None
    if left_version == right_version:
        return 0
    return -1 if left_version < right_version else 1
