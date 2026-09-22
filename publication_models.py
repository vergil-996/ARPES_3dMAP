# -*- coding: utf-8 -*-
"""图片导出：样式注册表、输出选项、坐标来源标签与偏好持久化。

设计依据 design/publication_export_research_plan_2026-09-21.md（v2）：

- 模板就是样式。同一视图族（3d/2d/1d）内提供三种可直观比较的样式；
  样式只改变边框、色条排布、刻度密度、字体、线条和留白，不改变科学内容。
- 样式及 overrides 按视图族和 style_id 记录；尺寸/DPI/格式属于输出偏好，
  恢复样式默认不重置它们。
- 坐标/单位标签必须诚实：有显式单位元数据才标单位；有坐标无数值单位时
  不猜单位；缺失坐标（索引回退）时使用中性 index 标签，不编造 eV/Å⁻¹。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Mapping, Optional, Tuple

STYLE_SCHEMA_VERSION = 1
SETTINGS_PREFIX = "publication_export/v1"

# ---------------------------------------------------------------------------
# 视图族
# ---------------------------------------------------------------------------

VIEW_TO_FAMILY = {
    "3d": "3d",
    "2d": "2d",
    "1d": "1d",
    "1d_comparison": "1d",
    "waterfall": "1d",
}

FAMILY_DEFAULT_SIZE_MM = {
    "3d": (89.0, 85.0),
    "2d": (89.0, 75.0),
    "1d": (89.0, 65.0),
}

FAMILY_LABELS = {"3d": "3D 视图", "2d": "2D 视图", "1d": "1D 视图"}

# 各视图族支持的输出格式（首版：全部 PNG；1D/2D 另支持 PDF；3D PDF 禁用）
FAMILY_FORMATS = {
    "3d": ("png",),
    "2d": ("png", "pdf"),
    "1d": ("png", "pdf"),
}


def view_family_for(view: str) -> Optional[str]:
    return VIEW_TO_FAMILY.get(str(view))


# ---------------------------------------------------------------------------
# 输出选项（与样式分开保存）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutputOptions:
    """输出物理参数。height_mm 为 None 时使用视图族默认高度。"""

    width_mm: float = 89.0
    height_mm: Optional[float] = None
    dpi: int = 600
    fmt: str = "png"

    def resolved_height_mm(self, family: str) -> float:
        if self.height_mm is not None:
            return float(self.height_mm)
        return float(FAMILY_DEFAULT_SIZE_MM.get(family, (89.0, 75.0))[1])

    def size_inches(self, family: str) -> Tuple[float, float]:
        return (
            float(self.width_mm) / 25.4,
            self.resolved_height_mm(family) / 25.4,
        )

    def target_pixels(self, family: str) -> Tuple[int, int]:
        w_in, h_in = self.size_inches(family)
        return round(w_in * int(self.dpi)), round(h_in * int(self.dpi))

    def signature(self, family: str) -> Tuple:
        return (
            round(float(self.width_mm), 3),
            round(self.resolved_height_mm(family), 3),
            int(self.dpi),
            str(self.fmt),
        )

    def with_updates(self, **kwargs) -> "OutputOptions":
        return replace(self, **kwargs)

    def to_json(self) -> str:
        return json.dumps(
            {
                "width_mm": float(self.width_mm),
                "height_mm": self.height_mm,
                "dpi": int(self.dpi),
                "fmt": str(self.fmt),
            }
        )

    @staticmethod
    def from_json(text: str) -> "OutputOptions":
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return OutputOptions()
        if not isinstance(payload, dict):
            return OutputOptions()
        width = payload.get("width_mm", 89.0)
        height = payload.get("height_mm")
        dpi = payload.get("dpi", 600)
        fmt = payload.get("fmt", "png")
        try:
            width = float(width)
            height = None if height is None else float(height)
            dpi = int(dpi)
        except (TypeError, ValueError):
            return OutputOptions()
        if not (20.0 <= width <= 300.0):
            width = 89.0
        if height is not None and not (20.0 <= height <= 300.0):
            height = None
        if not (72 <= dpi <= 2400):
            dpi = 600
        if fmt not in ("png", "pdf"):
            fmt = "png"
        return OutputOptions(width_mm=width, height_mm=height, dpi=dpi, fmt=fmt)


# ---------------------------------------------------------------------------
# 样式定义
# ---------------------------------------------------------------------------

# 共用排版默认值（计划 §3.3）：白色画布、7pt 轴名、6pt 刻度/图例、
# 0.6pt 坐标轴、0.8pt 曲线、2pt 主刻度、8pt 粗体面板编号。
_COMMON_PARAMS: Dict[str, Any] = {
    # 字体回退链：拉丁/符号优先 DejaVu Sans，CJK 回退到系统中文字体，
    # 保证中文图例/标题在 PNG 与 PDF（Type 42 嵌入）中不缺字形。
    "font_family": ["DejaVu Sans", "Microsoft YaHei", "SimHei", "sans-serif"],
    "axis_label_size": 7.0,
    "tick_label_size": 6.0,
    "legend_size": 6.0,
    "title_size": 7.0,
    "panel_label_size": 8.0,
    "axes_linewidth": 0.6,
    "curve_linewidth": 0.8,
    "tick_length": 2.0,       # pt
    "tick_width": 0.6,        # pt
    "colorbar_thickness_mm": 2.2,
    "colorbar_gap_mm": 2.5,
    "colorbar_title_size": 7.0,
    "ink_color": "#000000",
    "canvas_color": "#FFFFFF",
}

# 白底曲线身份色：主视图 theme.CURVE_PALETTE 的等序深色替代。
# 索引一一对应，保证同一曲线身份在所有 1D 样式中颜色一致，图例同步。
PUB_CURVE_PALETTE = [
    "#C2236B",  # 主强调粉（ACCENT #FF7EB6 的白底可读深色）
    "#1F77B4",  # 对应 #4CC9F0
    "#B07C00",  # 对应 #F9C74F
    "#4E8542",  # 对应 #90BE6D
    "#D0552B",  # 对应 #F3722C
    "#7A5CFF",  # 对应 #B388FF
    "#2E8C7A",  # 对应 #43AA8B
]
PUB_LINESTYLES = ["-", "--", "-.", ":"]


@dataclass(frozen=True)
class StyleSpec:
    style_id: str
    view_family: str
    name: str            # 卡片名，如 “极简”
    full_name: str       # 完整名，如 “3D · 极简”
    params: Mapping[str, Any] = field(default_factory=dict)

    def param(self, key, default=None):
        return self.params.get(key, _COMMON_PARAMS.get(key, default))


def _style(style_id, family, name, **params):
    merged = dict(_COMMON_PARAMS)
    merged.update(params)
    return StyleSpec(
        style_id=style_id,
        view_family=family,
        name=name,
        full_name=f"{family.upper()} · {name}",
        params=merged,
    )


STYLE_REGISTRY: Dict[str, Dict[str, StyleSpec]] = {
    "3d": {
        "3d_minimal": _style(
            "3d_minimal", "3d", "极简",
            axes_mode="tripod",      # 仅必要轴线（交于同一角的三条轴）
            show_box=False, show_grid=False,
            colorbar_position="right", colorbar_ticks=3, colorbar_outline=False,
            margin_left_mm=10.0, margin_right_mm=16.0,
            margin_top_mm=7.0, margin_bottom_mm=9.0,
        ),
        "3d_boxed": _style(
            "3d_boxed", "3d", "盒框",
            axes_mode="box",         # 细黑完整包围盒
            show_box=True, show_grid=False,
            colorbar_position="right", colorbar_ticks=5, colorbar_outline=True,
            margin_left_mm=10.0, margin_right_mm=16.0,
            margin_top_mm=7.0, margin_bottom_mm=9.0,
        ),
        "3d_horizontal": _style(
            "3d_horizontal", "3d", "横条紧凑",
            axes_mode="tripod",
            show_box=False, show_grid=False,
            colorbar_position="bottom", colorbar_ticks=3, colorbar_outline=False,
            margin_left_mm=10.0, margin_right_mm=8.0,
            margin_top_mm=7.0, margin_bottom_mm=16.0,
        ),
    },
    "2d": {
        "2d_boxed": _style(
            "2d_boxed", "2d", "标准框线",
            frame_mode="box", tick_direction="in",
            colorbar_position="right", colorbar_ticks=5, colorbar_outline=False,
            margin_left_mm=11.0, margin_right_mm=17.0,
            margin_top_mm=8.0, margin_bottom_mm=9.0,
            max_major_ticks=5,
        ),
        "2d_topbar": _style(
            "2d_topbar", "2d", "顶部横条",
            frame_mode="box", tick_direction="in",
            colorbar_position="top", colorbar_ticks=3, colorbar_outline=False,
            margin_left_mm=10.0, margin_right_mm=7.0,
            margin_top_mm=15.0, margin_bottom_mm=8.0,
            max_major_ticks=4,
        ),
        "2d_open": _style(
            "2d_open", "2d", "开放轴线",
            frame_mode="open", tick_direction="out",
            colorbar_position="right", colorbar_ticks=3, colorbar_outline=False,
            margin_left_mm=12.0, margin_right_mm=15.0,
            margin_top_mm=8.0, margin_bottom_mm=10.0,
            max_major_ticks=4,
        ),
    },
    "1d": {
        "1d_open": _style(
            "1d_open", "1d", "简洁开放",
            frame_mode="open", tick_direction="out",
            legend_mode="best",       # 无边框图例放空白区，无合适位置则放图外
            margin_left_mm=12.0, margin_right_mm=7.0,
            margin_top_mm=8.0, margin_bottom_mm=9.0,
            max_major_ticks=5,
        ),
        "1d_boxed": _style(
            "1d_boxed", "1d", "四边框线",
            frame_mode="box", tick_direction="in",
            legend_mode="best",
            margin_left_mm=12.0, margin_right_mm=7.0,
            margin_top_mm=8.0, margin_bottom_mm=9.0,
            max_major_ticks=5,
        ),
        "1d_compact": _style(
            "1d_compact", "1d", "紧凑排版",
            frame_mode="open", tick_direction="out",
            legend_mode="above",      # 图例放上方图外
            margin_left_mm=11.0, margin_right_mm=6.0,
            margin_top_mm=10.0, margin_bottom_mm=8.0,
            max_major_ticks=4,
        ),
    },
}

DEFAULT_STYLE_ID = {"3d": "3d_minimal", "2d": "2d_boxed", "1d": "1d_open"}


def styles_for_family(family: str) -> Tuple[StyleSpec, ...]:
    registry = STYLE_REGISTRY.get(family) or {}
    return tuple(registry.values())


def resolve_style(family: str, style_id: Optional[str]) -> StyleSpec:
    """按视图族解析样式；不存在或不属于该族时回退到该族默认样式。"""
    registry = STYLE_REGISTRY.get(family) or {}
    if style_id in registry:
        return registry[style_id]
    return registry[DEFAULT_STYLE_ID[family]]


# ---------------------------------------------------------------------------
# 样式微调（overrides）
# ---------------------------------------------------------------------------

# 各键的合法取值；未列出或取值非法的键在校验时被丢弃。
_OVERRIDE_SCHEMA = {
    "colorbar_visible": ("bool", None),
    "colorbar_position": ("choice", ("right", "left", "top", "bottom")),
    "colorbar_tick_mode": ("choice", ("values", "endpoints", "lowhigh", "none")),
    "colorbar_nticks": ("int", (2, 8)),
    "colorbar_outline": ("bool", None),
    # 色带沿长轴的长度占可用空间的百分比（100 = 占满）
    "colorbar_length": ("int", (30, 100)),
    # 色带厚度（毫米）
    "colorbar_thickness": ("float", (0.5, 10.0)),
    # 色带中心在画布上的位置（宽/高的百分比）；缺省时按布局自动居中
    "colorbar_cx": ("int", (0, 100)),
    "colorbar_cy": ("int", (0, 100)),
    "frame_mode": ("choice", ("box", "open")),
    "show_box": ("bool", None),
    "show_grid": ("bool", None),
    "panel_label": ("str", 8),
    "show_title": ("bool", None),
    # 数据体在画面中的大小百分比（100 = 快照取景原样）；仅 3D 族有意义
    "body_size": ("int", (0, 250)),
}

# 色条相关微调只对有色条的视图族有意义
_COLORBAR_OVERRIDE_KEYS = {
    "colorbar_visible", "colorbar_position", "colorbar_tick_mode",
    "colorbar_nticks", "colorbar_outline", "colorbar_length",
    "colorbar_thickness", "colorbar_cx", "colorbar_cy",
}
_FRAME_OVERRIDE_KEYS = {"frame_mode", "show_box", "show_grid"}


def validate_overrides(family: str, overrides: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """过滤非法/不适用键，返回干净的 overrides 字典。"""
    if not isinstance(overrides, Mapping):
        return {}
    clean: Dict[str, Any] = {}
    for key, value in overrides.items():
        schema = _OVERRIDE_SCHEMA.get(key)
        if schema is None:
            continue
        kind, constraint = schema
        if key in _COLORBAR_OVERRIDE_KEYS and family == "1d":
            continue  # 1D 通常没有色条
        if key == "frame_mode" and family == "3d":
            continue
        if key in ("show_box", "show_grid") and family != "3d":
            continue
        if key == "body_size" and family != "3d":
            continue
        if kind == "bool":
            clean[key] = bool(value)
        elif kind == "choice":
            if value in constraint:
                clean[key] = str(value)
        elif kind == "int":
            try:
                ivalue = int(value)
            except (TypeError, ValueError):
                continue
            low, high = constraint
            clean[key] = max(low, min(high, ivalue))
        elif kind == "float":
            try:
                fvalue = float(value)
            except (TypeError, ValueError):
                continue
            low, high = constraint
            clean[key] = round(max(low, min(high, fvalue)), 2)
        elif kind == "str":
            text = str(value).strip()[: int(constraint)]
            if text:
                clean[key] = text
    return clean


def overrides_signature(overrides: Mapping[str, Any]) -> Tuple:
    return tuple(sorted((str(k), repr(v)) for k, v in overrides.items()))


# ---------------------------------------------------------------------------
# 坐标来源与单位：诚实的轴标签
# ---------------------------------------------------------------------------

# axis_key → (基础名, index 回退名)
_AXIS_BASE_NAMES = {
    "X": ("kx", "kx (index)"),
    "Y": ("ky", "ky (index)"),
    "E": ("E", "E (index)"),
    "delay": ("Delay", "Delay (index)"),
}

_AXIS_DEFAULT_UNITS = {
    "X": None,
    "Y": None,
    "E": None,
    "delay": None,
}


def axis_label(axis_key: str, source: str, unit: Optional[str] = None) -> str:
    """按坐标来源生成轴标签。

    - source == "file" 且有显式单位元数据 → "kx (Å⁻¹)" 形式；
    - source == "file" 但无单位 → 只写 "kx"/"E"，不猜单位；
    - 其它（索引回退）→ 中性 index 标签，绝不编造 eV/Å⁻¹/fs。
    """
    base, index_name = _AXIS_BASE_NAMES.get(axis_key, (str(axis_key), f"{axis_key} (index)"))
    if source != "file":
        return index_name
    unit = (unit or "").strip() or _AXIS_DEFAULT_UNITS.get(axis_key)
    if unit:
        return f"{base} ({unit})"
    return base


def intensity_label_for(page_kind: str, normalized: bool = False) -> str:
    """色条/纵轴的强度标签：区分真实归一化、导数与未标定强度。"""
    if normalized:
        return "Normalized intensity"
    if page_kind == "second_derivative":
        return "-d²I/dE² (a.u.)"
    return "Intensity (a.u.)"


# ---------------------------------------------------------------------------
# 偏好持久化（QSettings）
# ---------------------------------------------------------------------------


def load_style_id(settings, family: str) -> Optional[str]:
    if family not in STYLE_REGISTRY:
        return None
    value = settings.value(f"{SETTINGS_PREFIX}/{family}/style_id", None, type=str)
    return value or None


def save_style_id(settings, family: str, style_id: str) -> None:
    settings.setValue(f"{SETTINGS_PREFIX}/{family}/style_id", str(style_id))


def load_overrides(settings, family: str, style_id: str) -> Dict[str, Any]:
    raw = settings.value(
        f"{SETTINGS_PREFIX}/{family}/overrides/{style_id}", "", type=str
    )
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return validate_overrides(family, payload)


def save_overrides(settings, family: str, style_id: str, overrides: Mapping[str, Any]) -> None:
    clean = validate_overrides(family, overrides)
    settings.setValue(
        f"{SETTINGS_PREFIX}/{family}/overrides/{style_id}", json.dumps(clean)
    )


def load_output_options(settings) -> OutputOptions:
    raw = settings.value(f"{SETTINGS_PREFIX}/output", "", type=str)
    return OutputOptions.from_json(raw) if raw else OutputOptions()


def save_output_options(settings, options: OutputOptions) -> None:
    settings.setValue(f"{SETTINGS_PREFIX}/output", options.to_json())


# ---------------------------------------------------------------------------
# 快照契约（plan v2 §6.1）
# ---------------------------------------------------------------------------


@dataclass
class PublicationSnapshot:
    """冻结的科学状态。只包含已解析数组与值类型，禁止持有活动
    Figure/Axes/Plotter/camera/actor/mapper 等可变场景对象。

    payload 按视图族组织：
    - 3d: volume, data_bounds, full_shape, spacing, clip_render_bounds,
          include_zero, opacity_mode, show_axes, viewport_aspect,
          level_info, intensity_label, axis_titles
    - 2d: image（已应用 E 翻转与降序归一化）, extent, title, xlabel,
          ylabel, level_info, intensity_label
    - 1d: curve{x,y,label}, title, xlabel, ylabel
    - 1d_comparison: curves[{x,y,label}], title, xlabel, ylabel
    - waterfall: energy_axis, curves, k_values, offset_step, title,
                 xlabel, ylabel
    """

    snapshot_id: str
    source_page_id: str
    source_page_title: str
    page_kind: str
    view: str
    view_family: str
    captured_at: str
    cmap_name: str
    levels_params: Tuple[float, float, float]
    locked_range: Optional[Tuple[float, float]]
    coords: Dict[str, Any] = field(default_factory=dict)          # np.ndarray
    coord_sources: Dict[str, str] = field(default_factory=dict)   # 'file'/'index'
    coord_units: Dict[str, Optional[str]] = field(default_factory=dict)
    camera: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)
    source_desc: str = ""
    home_frame_index: Optional[int] = None

    def axis_label(self, axis_key: str) -> str:
        return axis_label(
            axis_key,
            self.coord_sources.get(axis_key, "index"),
            self.coord_units.get(axis_key),
        )
