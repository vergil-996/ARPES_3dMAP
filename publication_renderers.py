# -*- coding: utf-8 -*-
"""图片导出：独立渲染器与共用布局引擎。

原则（plan v2 §5/§6）：

- 不复用活动 Figure/Axes/Plotter，不修改全局 rcParams/theme；每个产物由
  独立 Figure(Agg) 或独立 off-screen Plotter 生成。
- 色阶映射与主视图完全一致：复用 render_core 的 leveled-cmap / opacity
  纯函数，但色阶范围用 ``compute_level_info`` 纯函数计算，绝不触碰
  ``VisualEngine._last_data_range / _locked_data_range`` 类级共享状态。
- 固定物理画布（mm → inch → dpi 像素），两遍布局：先按样式边距排版，
  再实测文字外接框，必要时只增大边距防止裁切，不使用 tight bbox。
- 3D 体渲染为栅格，文字/坐标/色条由 matplotlib 矢量排版；VTK 世界坐标
  经 renderer.WorldToDisplay 投影后叠加轴线/刻度，不拉伸体渲染。
"""
from __future__ import annotations

import math
import os
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import matplotlib

# 不调用 matplotlib.use()；按需使用 Figure/Agg，不经过 pyplot 全局状态。
from matplotlib import colormaps
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import ListedColormap, Normalize
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator, ScalarFormatter
import numpy as np

from render_core import VisualEngine, VolumeRenderSession
from publication_models import PUB_CURVE_PALETTE, PUB_LINESTYLES, TITLE_GAP_DEFAULT_MM

RENDER_LOCK = threading.RLock()

MM_PER_INCH = 25.4


class RenderError(Exception):
    """导出渲染失败（调用方负责给出用户可读提示，不静默回退抓屏）。"""


# ---------------------------------------------------------------------------
# 色阶（纯函数版本，不写 VisualEngine 类状态）
# ---------------------------------------------------------------------------


def compute_level_info(
    data,
    levels_params: Sequence[float],
    include_zero: bool = False,
    locked_range: Optional[Tuple[float, float]] = None,
) -> Dict[str, float]:
    """与 VisualEngine._level_info 数值一致，但不读写任何类级共享状态。"""
    black, gamma, white = (float(v) for v in levels_params)
    if locked_range is not None:
        d_min, d_max = float(locked_range[0]), float(locked_range[1])
    else:
        source = np.asarray(data)
        if source.size == 0:
            d_min, d_max = 0.0, 1.0
        else:
            try:
                d_min, d_max = float(np.nanmin(source)), float(np.nanmax(source))
            except (TypeError, ValueError):
                d_min, d_max = 0.0, 1.0
            if not np.isfinite(d_min) or not np.isfinite(d_max):
                finite = source[np.isfinite(source)]
                if finite.size == 0:
                    d_min, d_max = 0.0, 1.0
                else:
                    d_min, d_max = float(np.min(finite)), float(np.max(finite))
        if include_zero:
            d_min = min(d_min, 0.0)
            d_max = max(d_max, 0.0)

    span = d_max - d_min
    if span <= 0:
        span = 1.0
        d_max = d_min + span

    black_pos = float(np.clip(black / 100.0, 0.0, 1.0))
    white_pos = float(np.clip(white / 100.0, 0.0, 1.0))
    if white_pos <= black_pos:
        white_pos = min(1.0, black_pos + 0.01)
    if white_pos <= black_pos:
        black_pos = max(0.0, white_pos - 0.01)

    gamma_power = float(np.power(10, (50 - gamma) / 50.0))
    gamma_power = max(gamma_power, 1e-6)
    gray_pos = black_pos + (white_pos - black_pos) * np.power(0.5, 1.0 / gamma_power)

    return {
        "data_min": d_min,
        "data_max": d_max,
        "span": span,
        "black_pos": black_pos,
        "gray_pos": float(gray_pos),
        "white_pos": white_pos,
        "black_value": d_min + black_pos * span,
        "gray_value": d_min + float(gray_pos) * span,
        "white_value": d_min + white_pos * span,
        "gamma_power": gamma_power,
    }


def actual_data_range(data) -> Tuple[float, float]:
    """冻结数据的实际范围（用于色条截断端点判断）。"""
    source = np.asarray(data)
    if source.size == 0:
        return 0.0, 1.0
    finite = source[np.isfinite(source)]
    if finite.size == 0:
        return 0.0, 1.0
    return float(np.min(finite)), float(np.max(finite))


def build_display_cmap(cmap, level_info: Mapping[str, float]) -> ListedColormap:
    return VisualEngine._leveled_cmap(cmap, level_info)


def mapped_opacity_values(opac_mode: str, level_info: Mapping[str, float]) -> List[float]:
    values = VolumeRenderSession.OPACITY_MAPS.get(
        str(opac_mode), VolumeRenderSession.OPACITY_MAPS["linear"]
    )
    return VisualEngine._mapped_opacity(values, level_info)


# ---------------------------------------------------------------------------
# 刻度与数值格式化
# ---------------------------------------------------------------------------


def nice_ticks(vmin: float, vmax: float, target: int) -> np.ndarray:
    """[vmin, vmax] 内约 target 个好读刻度；常量数据返回单一有效值。"""
    vmin, vmax = float(vmin), float(vmax)
    if not np.isfinite(vmin) or not np.isfinite(vmax):
        return np.asarray([])
    if vmax < vmin:
        vmin, vmax = vmax, vmin
    if vmax - vmin <= 1e-12 * max(1.0, abs(vmin), abs(vmax)):
        return np.asarray([vmin])
    locator = MaxNLocator(
        nbins=max(1, int(target) - 1),
        steps=[1, 2, 2.5, 5, 10],
        min_n_ticks=2,
    )
    ticks = np.asarray(locator.tick_values(vmin, vmax), dtype=np.float64)
    eps = (vmax - vmin) * 1e-9
    return ticks[(ticks >= vmin - eps) & (ticks <= vmax + eps)]


def _strip_negative_zero(text: str) -> str:
    return "0" if text in ("-0", "−0") else text


def format_tick_labels(ticks: Sequence[float], vmin: float, vmax: float):
    """返回 (labels, offset_text)。公共科学计数指数只出现一次（offset text）。"""
    ticks = [float(t) for t in ticks]
    if not ticks:
        return [], ""
    formatter = ScalarFormatter(useMathText=False)
    formatter.set_powerlimits((-3, 4))
    formatter.create_dummy_axis()
    formatter.axis.set_data_interval(float(vmin), float(vmax))
    formatter.axis.set_view_interval(float(vmin), float(vmax))
    formatter.set_locs(ticks)
    labels = [_strip_negative_zero(formatter(t)) for t in ticks]
    try:
        offset = formatter.get_offset() or ""
    except Exception:
        offset = ""
    return labels, offset


def _apply_formatter(axis, ticks: np.ndarray, lo: float, hi: float) -> None:
    labels, offset = format_tick_labels(ticks, lo, hi)
    axis.set_major_locator(matplotlib.ticker.FixedLocator(ticks))
    axis.set_major_formatter(matplotlib.ticker.FixedFormatter(labels))
    offset_text = axis.get_offset_text()
    if offset:
        offset_text.set_text(offset)
        offset_text.set_visible(True)
    else:
        offset_text.set_visible(False)


# ---------------------------------------------------------------------------
# 布局引擎
# ---------------------------------------------------------------------------


def _mm(value: float) -> float:
    return float(value) / MM_PER_INCH


def _new_figure(width_mm: float, height_mm: float, dpi: int) -> Figure:
    fig = Figure(
        figsize=(_mm(width_mm), _mm(height_mm)),
        dpi=int(dpi),
        facecolor="white",
    )
    FigureCanvasAgg(fig)
    return fig


def _fraction_rect(rect_mm, width_mm, height_mm):
    x0, y0, x1, y1 = rect_mm
    return [x0 / width_mm, y0 / height_mm, (x1 - x0) / width_mm, (y1 - y0) / height_mm]


def _compute_rects(
    width_mm: float,
    height_mm: float,
    margins_mm: Mapping[str, float],
    colorbar: Optional[Mapping[str, Any]],
) -> Tuple[List[float], Optional[List[float]]]:
    """按边距与色条位置计算主图与色条矩形（mm 坐标，左下角原点）。"""
    ml, mr = margins_mm["left"], margins_mm["right"]
    mb, mt = margins_mm["bottom"], margins_mm["top"]
    main = [ml, mb, width_mm - mr, height_mm - mt]
    if colorbar is None:
        return main, None
    thickness = float(colorbar["thickness_mm"])
    gap = float(colorbar["gap_mm"])
    position = colorbar["position"]
    # 色带沿长轴的长度比例（1.0 = 占满可用跨度），缩短时在主图跨度内居中
    frac = max(0.0, min(1.0, float(colorbar.get("length_frac", 1.0))))
    # 用户指定的色带中心（画布 % → mm）；缺省时按边距自动定位
    cx_pct = colorbar.get("center_x_pct")
    cy_pct = colorbar.get("center_y_pct")
    cx_mm = None if cx_pct is None else float(cx_pct) / 100.0 * width_mm
    cy_mm = None if cy_pct is None else float(cy_pct) / 100.0 * height_mm
    if position in ("right", "left"):
        full_h = height_mm - mt - mb
        bar_h = full_h * frac
        cy = mb + full_h / 2.0 if cy_mm is None else cy_mm
        # 色带始终完整落在画布内
        cy = min(max(cy, bar_h / 2.0), height_mm - bar_h / 2.0)
        if cx_mm is None:
            cx = (width_mm - mr - thickness / 2.0) if position == "right" else (ml + thickness / 2.0)
        else:
            cx = min(max(cx_mm, thickness / 2.0), width_mm - thickness / 2.0)
        cbar = [cx - thickness / 2.0, cy - bar_h / 2.0,
                cx + thickness / 2.0, cy + bar_h / 2.0]
        if position == "right":
            main[2] = cbar[0] - gap
        else:
            main[0] = cbar[2] + gap
    elif position in ("top", "bottom"):
        if cy_mm is None:
            cy = (height_mm - mt - thickness / 2.0) if position == "top" else (mb + thickness / 2.0)
        else:
            cy = min(max(cy_mm, thickness / 2.0), height_mm - thickness / 2.0)
        cbar = [0.0, cy - thickness / 2.0, 0.0, cy + thickness / 2.0]
        if position == "top":
            main[3] = cbar[1] - gap
        else:
            main[1] = cbar[3] + gap
        bar_width = (main[2] - main[0]) * 0.62 * frac
        cx = (main[0] + main[2]) / 2.0 if cx_mm is None else cx_mm
        cx = min(max(cx, bar_width / 2.0), width_mm - bar_width / 2.0)
        cbar[0], cbar[2] = cx - bar_width / 2.0, cx + bar_width / 2.0
    else:
        return main, None
    if main[2] - main[0] < 10.0 or main[3] - main[1] < 10.0:
        raise RenderError("画布尺寸过小，无法容纳所选样式的边距与色条。")
    return main, cbar


def _annotation_artists(ax):
    artists = []
    artists.extend(ax.get_xticklabels())
    artists.extend(ax.get_yticklabels())
    artists.append(ax.xaxis.label)
    artists.append(ax.yaxis.label)
    artists.append(ax.title)
    artists.append(ax.xaxis.get_offset_text())
    artists.append(ax.yaxis.get_offset_text())
    legend = ax.get_legend()
    if legend is not None:
        artists.append(legend)
    artists.extend(ax.texts)
    return artists


def _measure_overflow(fig, axes_list) -> Dict[str, float]:
    """测量注释艺术家超出当前坐标框的部分（figure 分数坐标）。"""
    canvas = fig.canvas
    canvas.draw()
    renderer = canvas.get_renderer()
    overflow = {"left": 0.0, "right": 0.0, "bottom": 0.0, "top": 0.0}
    for ax in axes_list:
        bbox = ax.get_position()
        for artist in _annotation_artists(ax):
            if artist is None or not artist.get_visible():
                continue
            try:
                extent = artist.get_window_extent(renderer)
            except (RuntimeError, ValueError):
                continue
            if extent.width <= 0 or extent.height <= 0:
                continue
            corners = fig.transFigure.inverted().transform(
                [(extent.x0, extent.y0), (extent.x1, extent.y1)]
            )
            x0, y0 = corners[0]
            x1, y1 = corners[1]
            overflow["left"] = max(overflow["left"], bbox.x0 - x0)
            overflow["right"] = max(overflow["right"], x1 - bbox.x1)
            overflow["bottom"] = max(overflow["bottom"], bbox.y0 - y0)
            overflow["top"] = max(overflow["top"], y1 - bbox.y1)
    return overflow


def _fit_layout(
    fig,
    ax,
    cbar_ax,
    width_mm,
    height_mm,
    margins_mm: Dict[str, float],
    colorbar_layout: Optional[Mapping[str, Any]],
    pad_mm: float = 1.2,
    iterations: int = 2,
    title_plan: Optional[Mapping[str, Any]] = None,
):
    """两遍布局：先按样式边距排版，再按实测文字外接框只增不减地调整。

    返回 (margins, overflow)：overflow 为最终布局下各侧注释超出内容区的
    实测比例（figure 分数），供 place_title 把标题排在注释之外。
    """
    axes_list = [ax] + ([cbar_ax] if cbar_ax is not None else [])
    # 边距增长上限：极端长文本不能无限侵占数据区（双侧各 40%，保证主区 ≥10mm）
    max_lr = width_mm * 0.40
    max_tb = height_mm * 0.40
    for _ in range(max(1, int(iterations))):
        main_rect, cbar_rect = _compute_rects(width_mm, height_mm, margins_mm, colorbar_layout)
        ax.set_position(_fraction_rect(main_rect, width_mm, height_mm))
        if cbar_ax is not None and cbar_rect is not None:
            cbar_ax.set_position(_fraction_rect(cbar_rect, width_mm, height_mm))
        overflow = _measure_overflow(fig, axes_list)
        changed = False
        for side, key in (("left", "left"), ("right", "right"), ("bottom", "bottom"), ("top", "top")):
            # overflow 是 figure 分数；换算为 mm
            over_mm = overflow[side] * (width_mm if side in ("left", "right") else height_mm)
            need = over_mm + pad_mm
            if title_plan is not None and side == title_plan["side"]:
                # 标题排在注释之外，并在边上留出距离与标题带
                need = max(need, title_margin_need_mm(title_plan, over_mm))
            # 夹在上限内：极端长文本宁可略微出血也不触发画布过小错误
            cap = max_lr if side in ("left", "right") else max_tb
            need = min(need, cap)
            if need > margins_mm[key] + 1e-6:
                margins_mm[key] = need
                changed = True
        if not changed:
            break
    main_rect, cbar_rect = _compute_rects(width_mm, height_mm, margins_mm, colorbar_layout)
    ax.set_position(_fraction_rect(main_rect, width_mm, height_mm))
    if cbar_ax is not None and cbar_rect is not None:
        cbar_ax.set_position(_fraction_rect(cbar_rect, width_mm, height_mm))
    # 实测即绘制：_measure_overflow 内部会 draw 一次
    overflow = _measure_overflow(fig, axes_list)
    return margins_mm, overflow


def _style_axes_frame(ax, style_params: Mapping[str, Any], overrides: Mapping[str, Any], family: str):
    ink = style_params.get("ink_color", "#000000")
    lw = float(style_params.get("axes_linewidth", 0.6))
    frame_mode = overrides.get("frame_mode", style_params.get("frame_mode", "open"))
    if family == "2d":
        frame_mode = overrides.get("frame_mode", style_params.get("frame_mode", "box"))
    for name, spine in ax.spines.items():
        visible = frame_mode == "box" or name in ("left", "bottom")
        spine.set_visible(visible)
        spine.set_color(ink)
        spine.set_linewidth(lw)
    direction = style_params.get("tick_direction", "out")
    ax.tick_params(
        axis="both",
        which="major",
        direction=direction,
        length=float(style_params.get("tick_length", 2.0)),
        width=float(style_params.get("tick_width", 0.6)),
        colors=ink,
        labelsize=float(style_params.get("tick_label_size", 6.0)),
        labelfontfamily=style_params.get("font_family", "DejaVu Sans"),
    )
    ax.grid(False)


def _style_axis_labels(ax, style_params, xlabel, ylabel):
    ink = style_params.get("ink_color", "#000000")
    family = style_params.get("font_family", "DejaVu Sans")
    ax.set_xlabel(
        xlabel, fontsize=float(style_params.get("axis_label_size", 7.0)),
        color=ink, fontfamily=family, labelpad=2.0,
    )
    ax.set_ylabel(
        ylabel, fontsize=float(style_params.get("axis_label_size", 7.0)),
        color=ink, fontfamily=family, labelpad=2.0,
    )


def _add_panel_label(ax, overrides, style_params):
    text = str(overrides.get("panel_label") or "").strip()
    if not text:
        return
    ax.text(
        -0.16, 1.03, text,
        transform=ax.transAxes,
        fontsize=float(style_params.get("panel_label_size", 8.0)),
        fontweight="bold",
        color=style_params.get("ink_color", "#000000"),
        ha="left", va="bottom",
        clip_on=False,
    )


# ---------------------------------------------------------------------------
# 标题（画布级文字：自动命名 / 位置 / 对齐 / 距离）
# ---------------------------------------------------------------------------

# 标题带与画布边缘之间保留的最小空白（毫米）
_TITLE_EDGE_INSET_MM = 0.6


def _resolve_title(snapshot, overrides) -> Optional[str]:
    """解析图片上要显示的标题。

    - 关闭显示（show_title=False）→ 无标题；
    - 显式给出 title_text（含空串）→ 用用户命名的文字，空串即不显示标题；
    - 缺省 → 回退到快照自动标题（切片/积分/结果类型），没有则无标题。
    """
    if not overrides.get("show_title", True):
        return None
    if "title_text" in overrides:
        return str(overrides.get("title_text") or "").strip() or None
    return str(snapshot.payload.get("title") or "").strip() or None


def _wrap_title(text: str, width_mm: float) -> str:
    """长标题换行而不是裁掉或无限撑大边距；无空格的极端串也强制断行。"""
    import textwrap

    wrapped = "\n".join(
        textwrap.wrap(
            str(text), width=max(20, int(width_mm * 0.45)),
            break_long_words=True, break_on_hyphens=False,
        )
    )
    return wrapped or str(text)


def _title_band_mm(wrapped: str, style_params) -> float:
    """标题文字带高度估算（毫米）：行数 × 字号 × 行距。"""
    lines = str(wrapped).count("\n") + 1
    return lines * float(style_params.get("title_size", 7.0)) * 1.3 / 72.0 * MM_PER_INCH


def _title_side(overrides) -> str:
    return "bottom" if overrides.get("title_position", "top") == "bottom" else "top"


def _title_gap_mm(overrides) -> float:
    try:
        return max(0.0, float(overrides.get("title_gap_mm", TITLE_GAP_DEFAULT_MM)))
    except (TypeError, ValueError):
        return TITLE_GAP_DEFAULT_MM


def plan_title(snapshot, overrides, style_params, width_mm) -> Optional[Dict[str, Any]]:
    """标题排版计划：字形与位置参数，供布局引擎预留边距、供绘制时定位。

    标题是画布级文字，_measure_overflow 测不到它，所以边距必须由
    _fit_layout / _fit_layout_3d 按本计划显式叠加在注释之外。
    """
    title = _resolve_title(snapshot, overrides)
    if not title:
        return None
    wrapped = _wrap_title(title, width_mm)
    return {
        "text": wrapped,
        "side": _title_side(overrides),
        "gap_mm": _title_gap_mm(overrides),
        "band_mm": _title_band_mm(wrapped, style_params),
        "align": overrides.get("title_align", "center"),
        "fontsize": float(style_params.get("title_size", 7.0)),
        "color": style_params.get("ink_color", "#000000"),
        "fontfamily": style_params.get("font_family", "DejaVu Sans"),
    }


def title_margin_need_mm(plan: Optional[Mapping[str, Any]], over_mm: float) -> float:
    """标题所在边需要的边距：注释外接框 + 距离 + 标题带 + 贴边留白。"""
    if plan is None:
        return 0.0
    return float(over_mm) + plan["gap_mm"] + plan["band_mm"] + _TITLE_EDGE_INSET_MM


def place_title(fig, plan, width_mm, height_mm, margins, colorbar_layout,
                overflow) -> None:
    """排版后把标题画到画布上（位置/对齐/距离来自 plan）。

    与坐标框在水平方向对齐（左/中/右）；垂直方向排在注释（刻度、轴名、
    图例、顶/底色条）之外 gap 处——注释外接框由 _measure_overflow 实测，
    所以标题不会压住轴名或色条。位置最后夹在画布内：即使标题带被上限
    截断或色条被手动拖到画布边缘，标题也不会出血。
    """
    if plan is None:
        return
    main, _cbar = _compute_rects(width_mm, height_mm, margins, colorbar_layout)
    side = plan["side"]
    over_mm = float(overflow.get(side, 0.0)) * height_mm
    band = float(plan["band_mm"])
    gap = float(plan["gap_mm"])
    if side == "top":
        y_mm = (height_mm - margins["top"]) + over_mm + gap
        y_mm = min(y_mm, height_mm - _TITLE_EDGE_INSET_MM - band)
        va = "bottom"
    else:
        y_mm = margins["bottom"] - over_mm - gap
        y_mm = max(y_mm, _TITLE_EDGE_INSET_MM + band)
        va = "top"
    align = plan["align"]
    if align == "left":
        x_mm, ha = main[0], "left"
    elif align == "right":
        x_mm, ha = main[2], "right"
    else:
        x_mm, ha = (main[0] + main[2]) / 2.0, "center"
    fig.text(
        x_mm / width_mm, y_mm / height_mm, plan["text"],
        ha=ha, va=va,
        fontsize=float(plan["fontsize"]),
        color=plan["color"],
        fontfamily=plan["fontfamily"],
    )


def _draw_colorbar(
    fig,
    cbar_ax,
    cmap,
    level_info,
    data_range,
    position: str,
    tick_mode: str,
    target_ticks: int,
    outline: bool,
    title: str,
    style_params: Mapping[str, Any],
):
    """在指定 cbar_ax 绘制色条；刻度与冻结的 LUT/norm 严格一致。"""
    ink = style_params.get("ink_color", "#000000")
    tick_size = float(style_params.get("tick_label_size", 6.0))
    family = style_params.get("font_family", "DejaVu Sans")
    low = float(level_info["black_value"])
    high = float(level_info["white_value"])
    if high <= low:
        high = low + 1.0
    norm = Normalize(vmin=low, vmax=high)
    mappable = matplotlib.cm.ScalarMappable(norm=norm, cmap=cmap)

    d_lo, d_hi = data_range
    eps = (high - low) * 1e-9
    extend = "neither"
    if d_lo < low - eps and d_hi > high + eps:
        extend = "both"
    elif d_lo < low - eps:
        extend = "min"
    elif d_hi > high + eps:
        extend = "max"

    orientation = "vertical" if position in ("left", "right") else "horizontal"
    colorbar = fig.colorbar(mappable, cax=cbar_ax, orientation=orientation, extend=extend)

    if tick_mode == "values":
        ticks = nice_ticks(low, high, target_ticks)
    elif tick_mode == "endpoints":
        ticks = np.asarray([low, high]) if high > low else np.asarray([low])
    else:
        ticks = np.asarray([])

    if tick_mode == "lowhigh":
        ends = [low, high] if high > low else [low]
        labels = ["Low", "High"] if high > low else [format_tick_labels([low], low, high)[0][0]]
        colorbar.set_ticks(ends)
        colorbar.set_ticklabels(labels)
    elif tick_mode == "none":
        colorbar.set_ticks([])
    else:
        colorbar.set_ticks(list(ticks))
        labels, offset = format_tick_labels(ticks, low, high)
        colorbar.set_ticklabels(labels)
        if offset:
            # 公共科学计数指数只显示一次
            offset_text = cbar_ax.yaxis.get_offset_text() if orientation == "vertical" else cbar_ax.xaxis.get_offset_text()
            offset_text.set_text(offset)
            offset_text.set_visible(True)

    if title:
        colorbar.set_label(
            title,
            fontsize=float(style_params.get("colorbar_title_size", 7.0)),
            color=ink,
            fontfamily=family,
        )
        if orientation == "horizontal":
            cbar_ax.xaxis.set_label_position("top" if position == "top" else "bottom")

    cbar_ax.tick_params(
        axis="both", which="major", direction="out",
        length=float(style_params.get("tick_length", 2.0)),
        width=float(style_params.get("tick_width", 0.6)),
        colors=ink, labelsize=tick_size, labelfontfamily=family,
    )
    if orientation == "vertical" and position == "left":
        cbar_ax.yaxis.set_ticks_position("left")
        cbar_ax.yaxis.set_label_position("left")
    for spine in cbar_ax.spines.values():
        spine.set_visible(bool(outline))
        spine.set_edgecolor(ink)
        spine.set_linewidth(0.4)
    try:
        colorbar.outline.set_visible(bool(outline))
        if outline:
            colorbar.outline.set_edgecolor(ink)
            colorbar.outline.set_linewidth(0.4)
    except Exception:
        pass
    return colorbar


def _colorbar_layout_for(style_params, overrides, family):
    visible = overrides.get("colorbar_visible", True)
    if not visible or family == "1d":
        return None
    try:
        length_pct = int(overrides.get("colorbar_length", 100))
    except (TypeError, ValueError):
        length_pct = 100
    try:
        thickness = float(overrides.get(
            "colorbar_thickness", style_params.get("colorbar_thickness_mm", 2.2)
        ))
    except (TypeError, ValueError):
        thickness = 2.2
    layout = {
        "position": overrides.get(
            "colorbar_position", style_params.get("colorbar_position", "right")
        ),
        "thickness_mm": max(0.5, min(10.0, thickness)),
        "gap_mm": float(style_params.get("colorbar_gap_mm", 2.5)),
        "length_frac": max(0.3, min(1.0, length_pct / 100.0)),
    }
    # 用户指定的色带中心（画布宽/高百分比），由 _compute_rects 换算成 mm
    for key, name in (("colorbar_cx", "center_x_pct"), ("colorbar_cy", "center_y_pct")):
        if key in overrides:
            try:
                layout[name] = max(0.0, min(100.0, float(overrides[key])))
            except (TypeError, ValueError):
                pass
    return layout


def default_colorbar_center(style_params, overrides, family, width_mm, height_mm):
    """无中心覆盖时按当前布局求色带中心（画布宽/高 %）。无色带返回 None。"""
    clean = {
        k: v for k, v in (overrides or {}).items()
        if k not in ("colorbar_cx", "colorbar_cy")
    }
    cbar_layout = _colorbar_layout_for(style_params, clean, family)
    if cbar_layout is None:
        return None
    margins = _initial_margins(style_params)
    try:
        _, cbar = _compute_rects(
            float(width_mm), float(height_mm), margins, cbar_layout
        )
    except RenderError:
        return None
    if cbar is None:
        return None
    return (
        (cbar[0] + cbar[2]) / 2.0 / float(width_mm) * 100.0,
        (cbar[1] + cbar[3]) / 2.0 / float(height_mm) * 100.0,
    )


def _initial_margins(style_params) -> Dict[str, float]:
    return {
        "left": float(style_params.get("margin_left_mm", 11.0)),
        "right": float(style_params.get("margin_right_mm", 15.0)),
        "bottom": float(style_params.get("margin_bottom_mm", 9.0)),
        "top": float(style_params.get("margin_top_mm", 8.0)),
    }


# ---------------------------------------------------------------------------
# 2D 强度图
# ---------------------------------------------------------------------------


def render_2d(snapshot, style, overrides, options, dpi: int) -> Figure:
    params = style.params
    family = "2d"
    width_mm = float(options.width_mm)
    height_mm = options.resolved_height_mm(family)

    img = np.asarray(snapshot.payload["image"], dtype=np.float64)
    if img.size == 0 or not np.any(np.isfinite(img)):
        raise RenderError("数据为空或全部无效，不能生成科学图片。")
    ext = [float(v) for v in snapshot.payload["extent"]]
    level_info = snapshot.payload["level_info"]
    cmap = build_display_cmap(snapshot.cmap_name, level_info)
    data_range = actual_data_range(img)

    fig = _new_figure(width_mm, height_mm, dpi)
    ax = fig.add_axes([0.15, 0.15, 0.7, 0.7])

    image = ax.imshow(
        img,
        cmap=cmap,
        aspect="auto",
        origin="lower",
        extent=ext,
        interpolation="spline16",   # 继承当前正式显示的插值选择
        vmin=float(level_info["black_value"]),
        vmax=float(level_info["white_value"]),
    )
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])

    max_ticks = int(params.get("max_major_ticks", 5))
    _apply_formatter(ax.xaxis, nice_ticks(ext[0], ext[1], max_ticks), ext[0], ext[1])
    _apply_formatter(ax.yaxis, nice_ticks(ext[2], ext[3], max_ticks), ext[2], ext[3])

    _style_axes_frame(ax, params, overrides, family)
    cbar_layout = _colorbar_layout_for(params, overrides, family)
    _style_axis_labels(
        ax, params,
        snapshot.payload["xlabel"], snapshot.payload["ylabel"],
    )
    _add_panel_label(ax, overrides, params)

    cbar_ax = None
    if cbar_layout is not None:
        cbar_ax = fig.add_axes([0.85, 0.15, 0.05, 0.7])
        _draw_colorbar(
            fig, cbar_ax, cmap, level_info, data_range,
            position=cbar_layout["position"],
            tick_mode=overrides.get("colorbar_tick_mode", "values"),
            target_ticks=int(overrides.get(
                "colorbar_nticks", params.get("colorbar_ticks", 3)
            )),
            outline=bool(overrides.get(
                "colorbar_outline", params.get("colorbar_outline", False)
            )),
            title=snapshot.payload.get("intensity_label", "Intensity (a.u.)"),
            style_params=params,
        )

    margins = _initial_margins(params)
    title_plan = plan_title(snapshot, overrides, params, width_mm)
    margins, overflow = _fit_layout(
        fig, ax, cbar_ax, width_mm, height_mm, margins, cbar_layout, title_plan=title_plan
    )
    place_title(fig, title_plan, width_mm, height_mm, margins, cbar_layout, overflow)
    return fig


# ---------------------------------------------------------------------------
# 1D 曲线（单曲线 / 比较 / 瀑布图共用样式库）
# ---------------------------------------------------------------------------


def _pub_curve_style(index: int, is_base: bool, params):
    color = PUB_CURVE_PALETTE[index % len(PUB_CURVE_PALETTE)]
    linestyle = PUB_LINESTYLES[(index // len(PUB_CURVE_PALETTE)) % len(PUB_LINESTYLES)]
    linewidth = float(params.get("curve_linewidth", 0.8))
    if is_base:
        linewidth *= 1.5  # 保留基准强调，不被统一线宽抹掉
    return color, linestyle, linewidth


def _curve_label_is_meaningful(label: Optional[str]) -> bool:
    return bool(label and str(label).strip())


def _render_1d_axes_content(ax, snapshot, params):
    """返回是否有图例内容。曲线身份颜色按索引确定映射，样式间不重新分配。"""
    view = snapshot.view
    has_legend = False
    if view == "1d":
        curve = snapshot.payload["curve"]
        x, y = np.asarray(curve["x"], dtype=np.float64), np.asarray(curve["y"], dtype=np.float64)
        color, linestyle, linewidth = _pub_curve_style(0, True, params)
        label = curve.get("label")
        ax.plot(
            x, y, color=color, linestyle=linestyle, linewidth=linewidth,
            label=label if _curve_label_is_meaningful(label) else None,
        )
        has_legend = _curve_label_is_meaningful(label)
    elif view == "1d_comparison":
        for idx, curve in enumerate(snapshot.payload["curves"]):
            x = np.asarray(curve["x"], dtype=np.float64)
            y = np.asarray(curve["y"], dtype=np.float64)
            label = str(curve.get("label") or f"Curve {idx + 1}")
            if idx == 0:
                label = f"基准 - {label}"
            color, linestyle, linewidth = _pub_curve_style(idx, idx == 0, params)
            ax.plot(x, y, color=color, linestyle=linestyle, linewidth=linewidth, label=label)
        has_legend = len(snapshot.payload["curves"]) > 1
    elif view == "waterfall":
        energy = np.asarray(snapshot.payload["energy_axis"], dtype=np.float64)
        curves = np.asarray(snapshot.payload["curves"], dtype=np.float64)
        k_values = np.asarray(snapshot.payload["k_values"], dtype=np.float64)
        offset_step = float(snapshot.payload["offset_step"])
        ink = params.get("ink_color", "#000000")
        offsets = np.asarray(snapshot.payload.get("curve_offsets", np.arange(len(curves)) * offset_step))
        for idx, curve in enumerate(curves):
            offset = float(offsets[idx])
            if not np.any(np.isfinite(curve)):
                continue
            ax.plot(curve + offset, energy, color=ink, linewidth=float(params.get("curve_linewidth", 0.8)))
            # 保留原有动量序列标签（横轴数据位置 + 轴上方）
            ax.annotate(
                f"{k_values[idx]:.4g}",
                xy=(offset + 0.5, 1.01),
                xycoords=("data", "axes fraction"),
                fontsize=float(params.get("tick_label_size", 6.0)),
                color=ink, ha="center", va="bottom",
                annotation_clip=False,
            )
        ax.set_xlim(-0.1, max(1.25, (len(curves) - 1) * offset_step + 1.1))
        has_legend = False  # 瀑布图不为每条线生成冗长图例
    return has_legend


def render_1d(snapshot, style, overrides, options, dpi: int) -> Figure:
    params = style.params
    family = "1d"
    width_mm = float(options.width_mm)
    height_mm = options.resolved_height_mm(family)

    fig = _new_figure(width_mm, height_mm, dpi)
    ax = fig.add_axes([0.18, 0.18, 0.68, 0.68])

    has_legend = _render_1d_axes_content(ax, snapshot, params)
    ax.margins(x=0.02, y=0.08)

    max_ticks = int(params.get("max_major_ticks", 5))
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    _apply_formatter(ax.xaxis, nice_ticks(x0, x1, max_ticks), x0, x1)
    _apply_formatter(ax.yaxis, nice_ticks(y0, y1, max_ticks), y0, y1)

    _style_axes_frame(ax, params, overrides, family)
    _style_axis_labels(
        ax, params,
        snapshot.payload.get("xlabel", ""), snapshot.payload.get("ylabel", ""),
    )
    _add_panel_label(ax, overrides, params)

    if has_legend:
        ink = params.get("ink_color", "#000000")
        legend_mode = params.get("legend_mode", "best")
        legend_kwargs = dict(
            frameon=False,
            fontsize=float(params.get("legend_size", 6.0)),
            labelcolor=ink,
        )
        if legend_mode == "above":
            legend = ax.legend(
                loc="lower left", bbox_to_anchor=(0.0, 1.01, 1.0, 0.05),
                mode=None, borderaxespad=0.0, **legend_kwargs,
            )
        else:
            legend = ax.legend(loc="best", **legend_kwargs)
        if legend is not None:
            for text in legend.get_texts():
                text.set_color(ink)
                text.set_fontfamily(params.get("font_family", "DejaVu Sans"))

    margins = _initial_margins(params)
    title_plan = plan_title(snapshot, overrides, params, width_mm)
    margins, overflow = _fit_layout(
        fig, ax, None, width_mm, height_mm, margins, None, title_plan=title_plan
    )
    place_title(fig, title_plan, width_mm, height_mm, margins, None, overflow)
    return fig


# ---------------------------------------------------------------------------
# 3D 体渲染（独立 off-screen 场景 + matplotlib 排版合成）
# ---------------------------------------------------------------------------


def _apply_snapshot_camera(plotter, camera: Mapping[str, Any]) -> None:
    # camera_position 三元组一次性设置 position/focal/view-up
    plotter.camera_position = (
        tuple(float(v) for v in camera["position"]),
        tuple(float(v) for v in camera["focal_point"]),
        tuple(float(v) for v in camera["view_up"]),
    )
    cam = plotter.camera
    try:
        cam.parallel_projection = bool(camera.get("parallel_projection", False))
        if cam.parallel_projection and camera.get("parallel_scale"):
            cam.parallel_scale = float(camera["parallel_scale"])
        elif camera.get("view_angle"):
            cam.SetViewAngle(float(camera["view_angle"]))
        if camera.get("clipping_range"):
            cam.clipping_range = tuple(float(v) for v in camera["clipping_range"])
    except Exception:
        pass


def _apply_body_zoom(plotter, factor: float) -> None:
    """数据体大小微调：按 factor 缩放画面中的数据体（1.0 = 快照取景原样）。

    只改视角（透视）或平行缩放（正交），不移动相机位置：数据体始终以
    焦点（数据中心）为缩放中心，裁剪范围不受影响；坐标轴叠加使用的
    WorldToDisplay 投影与最终相机自动一致。
    """
    if abs(factor - 1.0) < 1e-9:
        return
    cam = plotter.camera
    try:
        if cam.GetParallelProjection():
            cam.SetParallelScale(cam.GetParallelScale() / factor)
        else:
            # 视角必须在 (0, 180) 内：极端缩小夹到 175°，极端放大夹到 1°
            angle = cam.GetViewAngle() / factor
            cam.SetViewAngle(min(max(angle, 1.0), 175.0))
    except Exception:
        pass


def _world_to_display(renderer, point) -> Tuple[float, float]:
    renderer.SetWorldPoint(float(point[0]), float(point[1]), float(point[2]), 1.0)
    renderer.WorldToDisplay()
    display = renderer.GetDisplayPoint()
    return float(display[0]), float(display[1])


def _box_geometry(snapshot) -> Tuple[np.ndarray, Tuple[float, float, float]]:
    """返回体数据包围盒的 8 个世界坐标角点与 spacing。"""
    bounds = snapshot.payload["data_bounds"]  # 6 个 inclusive 体素索引
    spacing = snapshot.payload["spacing"]
    xs = (bounds[0] * spacing[0], bounds[1] * spacing[0])
    ys = (bounds[2] * spacing[1], bounds[3] * spacing[1])
    zs = (bounds[4] * spacing[2], bounds[5] * spacing[2])
    corners = np.asarray(
        [[x, y, z] for x in xs for y in ys for z in zs], dtype=np.float64
    )
    return corners, spacing


# 12 条边：两端点仅一个坐标不同
_BOX_EDGES = [(0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3),
              (2, 6), (4, 5), (4, 6), (3, 7), (5, 7), (6, 7)]


def _corner_bits(index: int) -> Tuple[int, int, int]:
    """corners 按 ``for x in xs for y in ys for z in zs`` 生成：
    index = xi*4 + yi*2 + zi，即 bit2=x、bit1=y、bit0=z。"""
    return ((index >> 2) & 1, (index >> 1) & 1, index & 1)


def _axis_edge_candidates(axis: int) -> List[Tuple[int, int]]:
    """指定轴向（0/1/2）的 4 条平行边。"""
    edges = []
    for a, b in _BOX_EDGES:
        bits_a, bits_b = _corner_bits(a), _corner_bits(b)
        diff = [i for i in range(3) if bits_a[i] != bits_b[i]]
        if diff == [axis]:
            edges.append((a, b))
    return edges


def _project_points(renderer, points: np.ndarray) -> np.ndarray:
    return np.asarray([_world_to_display(renderer, p) for p in points], dtype=np.float64)


def _axis_value_to_world(axis: int, value: float, snapshot) -> float:
    """物理坐标 → 该轴世界坐标（均匀网格，快照已校验）。"""
    coords = np.asarray(snapshot.coords[("X", "Y", "E")[axis]], dtype=np.float64)
    spacing = snapshot.payload["spacing"][axis]
    n = coords.size
    if n <= 1:
        return 0.0
    c0, c1 = float(coords[0]), float(coords[-1])
    if c1 == c0:
        return 0.0
    index = (float(value) - c0) / (c1 - c0) * (n - 1)
    return float(index) * spacing


def _draw_3d_grid(ax_img, renderer, snapshot, style_params):
    """在三个背向相机的包围盒面上叠加网格线（图像像素坐标系）。

    网格线取各轴物理坐标的整读刻度位置，与轴线刻度同源；线宽细于轴线、
    半透明，避免与数据体争夺视觉重心。
    """
    ink = style_params.get("ink_color", "#000000")
    lw = max(0.3, float(style_params.get("axes_linewidth", 0.6)) * 0.6)
    cam_pos = np.asarray(snapshot.camera["position"], dtype=np.float64)
    bounds = snapshot.payload["data_bounds"]
    spacing = snapshot.payload["spacing"]
    ranges = [
        (bounds[0] * spacing[0], bounds[1] * spacing[0]),
        (bounds[2] * spacing[1], bounds[3] * spacing[1]),
        (bounds[4] * spacing[2], bounds[5] * spacing[2]),
    ]
    axis_keys = ("X", "Y", "E")
    for axis in range(3):
        lo, hi = ranges[axis]
        # 背面：与相机所在侧相对的面（相机在中点哪侧，背面就取另一侧）
        face_value = lo if cam_pos[axis] > (lo + hi) / 2.0 else hi
        others = [a for a in range(3) if a != axis]
        for line_axis in others:
            tick_axis = next(a for a in others if a != line_axis)
            coords = np.asarray(snapshot.coords[axis_keys[tick_axis]], dtype=np.float64)
            # 与轴线刻度同源（同为 3 档整读刻度），网格线与刻度标签对齐
            ticks = nice_ticks(float(np.min(coords)), float(np.max(coords)), 3)
            for value in ticks:
                world_v = _axis_value_to_world(tick_axis, value, snapshot)
                p0 = [0.0, 0.0, 0.0]
                p1 = [0.0, 0.0, 0.0]
                p0[axis] = p1[axis] = face_value
                p0[tick_axis] = p1[tick_axis] = world_v
                p0[line_axis] = ranges[line_axis][0]
                p1[line_axis] = ranges[line_axis][1]
                d0 = _world_to_display(renderer, p0)
                d1 = _world_to_display(renderer, p1)
                ax_img.plot(
                    [d0[0], d1[0]], [d0[1], d1[1]],
                    color=ink, linewidth=lw, alpha=0.35,
                    solid_capstyle="butt", zorder=2,
                )


def _draw_3d_axes(ax_img, renderer, snapshot, style_params, overrides):
    """按样式在图像坐标系叠加轴线/包围盒/刻度/轴名。"""
    ink = style_params.get("ink_color", "#000000")
    lw = float(style_params.get("axes_linewidth", 0.6))
    tick_len_pt = float(style_params.get("tick_length", 2.0))
    tick_size = float(style_params.get("tick_label_size", 6.0))
    label_size = float(style_params.get("axis_label_size", 7.0))
    family_font = style_params.get("font_family", "DejaVu Sans")
    dpi = ax_img.figure.get_dpi()
    tick_len_px = tick_len_pt / 72.0 * dpi

    corners, spacing = _box_geometry(snapshot)
    projected = _project_points(renderer, corners)
    center = projected.mean(axis=0)

    show_box = bool(overrides.get("show_box", style_params.get("show_box", False)))
    axes_mode = style_params.get("axes_mode", "tripod")

    if bool(overrides.get("show_grid", style_params.get("show_grid", False))):
        _draw_3d_grid(ax_img, renderer, snapshot, style_params)

    if show_box or axes_mode == "box":
        # 隐藏远离相机的角点相连的三条边（盒框剪影规则）
        view_dir = np.asarray(snapshot.camera["focal_point"]) - np.asarray(snapshot.camera["position"])
        box_center_world = corners.mean(axis=0)
        far_corner = int(np.argmax([(c - box_center_world) @ view_dir for c in corners]))
        hidden_edges = {tuple(sorted(e)) for e in _BOX_EDGES if far_corner in e}
        for a, b in _BOX_EDGES:
            if tuple(sorted((a, b))) in hidden_edges:
                continue
            xs = [projected[a][0], projected[b][0]]
            ys = [projected[a][1], projected[b][1]]
            ax_img.plot(xs, ys, color=ink, linewidth=lw, solid_capstyle="round", zorder=3)

    # 每个轴向选一条最外侧边绘制刻度与轴名
    for axis in range(3):
        edges = _axis_edge_candidates(axis)
        best, best_dist = None, -1.0
        for a, b in edges:
            mid = (projected[a] + projected[b]) / 2.0
            dist = float(np.hypot(*(mid - center)))
            if dist > best_dist:
                best, best_dist = (a, b), dist
        if best is None:
            continue
        a, b = best
        p0, p1 = projected[a], projected[b]
        edge_vec = p1 - p0
        edge_len = float(np.hypot(*edge_vec))
        if edge_len < 1e-6:
            continue
        direction = edge_vec / edge_len
        outward = np.asarray([-direction[1], direction[0]])
        mid = (p0 + p1) / 2.0
        if outward @ (mid - center) < 0:
            outward = -outward

        ax_img.plot([p0[0], p1[0]], [p0[1], p1[1]], color=ink, linewidth=lw,
                    solid_capstyle="round", zorder=4)

        coords = np.asarray(snapshot.coords[("X", "Y", "E")[axis]], dtype=np.float64)
        lo, hi = float(np.min(coords)), float(np.max(coords))
        ticks = nice_ticks(lo, hi, 3)
        labels, offset = format_tick_labels(ticks, lo, hi)
        label_gap_px = (tick_len_pt + 2.0) / 72.0 * dpi
        name_gap_px = (tick_len_pt + 15.0) / 72.0 * dpi
        # 轴名沿边方向旋转，避免与刻度标签碰撞
        angle = math.degrees(math.atan2(direction[1], direction[0]))
        if angle > 90.0:
            angle -= 180.0
        elif angle < -90.0:
            angle += 180.0
        for value, label in zip(ticks, labels):
            world = _axis_value_to_world(axis, value, snapshot)
            world_point = corners[a].copy()
            world_point[axis] = world
            # 注意：corners[a] 在 axis 位可能不是最小端，直接用绝对世界坐标
            px = _world_to_display(renderer, world_point)
            tick_end = px + outward * tick_len_px
            ax_img.plot([px[0], tick_end[0]], [px[1], tick_end[1]],
                        color=ink, linewidth=float(style_params.get("tick_width", 0.6)), zorder=4)
            label_pos = px + outward * label_gap_px
            ha = "left" if outward[0] > 0.3 else ("right" if outward[0] < -0.3 else "center")
            va = "bottom" if outward[1] > 0.3 else ("top" if outward[1] < -0.3 else "center")
            ax_img.text(label_pos[0], label_pos[1], label, fontsize=tick_size,
                        color=ink, fontfamily=family_font, ha=ha, va=va, zorder=5)
        name_pos = mid + outward * name_gap_px
        ax_img.text(
            name_pos[0], name_pos[1], snapshot.payload["axis_titles"][axis],
            fontsize=label_size, color=ink, fontfamily=family_font,
            ha="center", va="center", rotation=angle, rotation_mode="anchor", zorder=5,
        )


def _content_bbox(image, pad_px: int):
    """截图中非白内容的外接框（向外扩 pad_px）。

    返回数组行列索引 (r0, r1, c0, c1)（行自上而下）；全白时返回 None。
    白边来自主视图取景留白，裁掉后数据体才能真正充满导出图内容区。
    """
    arr = np.asarray(image)
    if arr.ndim < 2:
        return None
    if arr.ndim == 2:
        mask = arr < 252
    else:
        mask = np.any(arr[..., :3] < 252, axis=-1)
    rows = np.nonzero(mask.any(axis=1))[0]
    cols = np.nonzero(mask.any(axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        return None
    h, w = mask.shape
    r0 = max(0, int(rows[0]) - pad_px)
    r1 = min(h, int(rows[-1]) + pad_px + 1)
    c0 = max(0, int(cols[0]) - pad_px)
    c1 = min(w, int(cols[-1]) + pad_px + 1)
    return r0, r1, c0, c1


def render_3d(snapshot, style, overrides, options, dpi: int) -> Figure:
    import pyvista as pv  # 延迟导入：模块本身保持轻量可测

    params = style.params
    family = "3d"
    width_mm = float(options.width_mm)
    height_mm = options.resolved_height_mm(family)

    volume = np.asarray(snapshot.payload["volume"], dtype=np.float32)
    if volume.size == 0 or not np.any(np.isfinite(volume)):
        raise RenderError("数据为空或全部无效，不能生成科学图片。")
    level_info = snapshot.payload["level_info"]
    cmap = build_display_cmap(snapshot.cmap_name, level_info)
    data_range = actual_data_range(volume)

    fig = _new_figure(width_mm, height_mm, dpi)
    ax_img = fig.add_axes([0.12, 0.12, 0.7, 0.7])
    ax_img.set_axis_off()

    cbar_layout = _colorbar_layout_for(params, overrides, family)
    margins = _initial_margins(params)
    title_plan = plan_title(snapshot, overrides, params, width_mm)
    # 先按初始边距得到内容区，决定离屏视口像素（保持主视图纵横比）
    main_rect, _ = _compute_rects(width_mm, height_mm, margins, cbar_layout)
    content_w_mm = main_rect[2] - main_rect[0]
    content_h_mm = main_rect[3] - main_rect[1]
    viewport_aspect = float(snapshot.payload["viewport_aspect"])
    if viewport_aspect <= 0:
        viewport_aspect = 1.0
    content_aspect = content_w_mm / content_h_mm
    if content_aspect >= viewport_aspect:
        view_h_mm = content_h_mm
        view_w_mm = view_h_mm * viewport_aspect
    else:
        view_w_mm = content_w_mm
        view_h_mm = view_w_mm / viewport_aspect
    vw_px = max(64, int(round(_mm(view_w_mm) * dpi)))
    vh_px = max(64, int(round(_mm(view_h_mm) * dpi)))

    # 数据体大小：100% = 裁掉取景白边后充满内容区；<100% 按比例缩小；
    # >100% 允许超出内容区向页边扩展（物理上限为图幅边界）。
    try:
        body_pct = int(overrides.get("body_size", 100))
    except (TypeError, ValueError):
        body_pct = 100
    # 0% 没有可渲染意义，且 factor 不能为 0；夹到 1%（数据体缩到近不可见）
    body_factor = max(body_pct, 1) / 100.0
    # >100% 时同步放大离屏视口像素，放大的数据体才不会被视口边界裁掉；
    # 8% 余量吸收透视投影的非线性（视角缩放 ≠ 严格的投影尺寸缩放）
    enlarge = 1.0 if body_factor <= 1.0 else body_factor * 1.08
    rw_px = max(64, int(round(vw_px * enlarge)))
    rh_px = max(64, int(round(vh_px * enlarge)))

    plotter = None
    try:
        plotter = pv.Plotter(off_screen=True, window_size=(rw_px, rh_px))
        plotter.set_background("white")
        grid = pv.ImageData()
        grid.extent = tuple(int(v) for v in snapshot.payload["data_bounds"])
        grid.origin = (0.0, 0.0, 0.0)
        grid.spacing = tuple(float(v) for v in snapshot.payload["spacing"])
        buffer = np.asfortranarray(volume)
        grid.point_data["values"] = buffer.ravel(order="F")
        opacity = mapped_opacity_values(snapshot.payload["opacity_mode"], level_info)
        plotter.add_volume(
            grid,
            scalars="values",
            cmap=cmap,
            opacity=opacity,
            clim=[float(level_info["black_value"]), float(level_info["white_value"])],
            show_scalar_bar=False,
            mapper="smart",
            name="export_vol",
            render=False,
        )
        clip = snapshot.payload.get("clip_render_bounds")
        if clip is not None:
            _apply_clipping_planes(plotter, clip)
        _apply_snapshot_camera(plotter, snapshot.camera)
        _apply_body_zoom(plotter, body_factor)
        plotter.render()
        image = plotter.screenshot(None, return_img=True)
        show_axes = bool(snapshot.payload.get("show_axes", True))
        if show_axes:
            axes_artists = lambda: _draw_3d_axes(ax_img, plotter.renderer, snapshot, params, overrides)
        else:
            axes_artists = None
    except RenderError:
        if plotter is not None:
            plotter.close()
        raise
    except Exception as exc:
        if plotter is not None:
            plotter.close()
        raise RenderError(f"3D 离屏渲染失败：{exc}") from exc

    # 裁掉取景白边：数据体（外加坐标轴注记所需余量）成为放置内容，
    # 100% 时充满内容区，不再被主视图取景留白浪费空间
    if show_axes:
        # 余量需容纳轴线外的刻度/轴名：name_gap = tick_len + 15pt，再加轴名字高
        pad_px = max(8, int(round(
            (float(params.get("tick_length", 2.0)) + 15.0
             + 1.6 * float(params.get("axis_label_size", 7.0))) / 72.0 * dpi
        )))
    else:
        pad_px = max(6, int(round(0.01 * max(rw_px, rh_px))))
    bbox = _content_bbox(image, pad_px)
    if bbox is None:
        r0, r1, c0, c1 = 0, int(image.shape[0]), 0, int(image.shape[1])
    else:
        r0, r1, c0, c1 = bbox
    cropped = image[r0:r1, c0:c1]
    # 坐标系保持 VTK display 像素（y 自下而上），轴线叠加投影不用改
    cx0, cx1 = float(c0), float(c1)
    cy0, cy1 = float(int(image.shape[0]) - r1), float(int(image.shape[0]) - r0)

    # 放置：裁剪后纵横比适配内容区 × body_factor，居中（等比，不拉伸体渲染）
    crop_aspect = (cx1 - cx0) / max(cy1 - cy0, 1e-6)
    if content_w_mm / content_h_mm >= crop_aspect:
        place_h_mm = content_h_mm * body_factor
        place_w_mm = place_h_mm * crop_aspect
    else:
        place_w_mm = content_w_mm * body_factor
        place_h_mm = place_w_mm / crop_aspect
    view_x0 = (main_rect[0] + main_rect[2]) / 2.0 - place_w_mm / 2.0
    view_y0 = (main_rect[1] + main_rect[3]) / 2.0 - place_h_mm / 2.0
    ax_img.set_position(_fraction_rect(
        [view_x0, view_y0, view_x0 + place_w_mm, view_y0 + place_h_mm],
        width_mm, height_mm,
    ))
    ax_img.imshow(
        cropped, extent=(cx0, cx1, cy0, cy1), origin="upper",
        aspect="auto", interpolation="bilinear",
    )
    ax_img.set_xlim(cx0, cx1)
    ax_img.set_ylim(cy0, cy1)

    cbar_ax = None
    if cbar_layout is not None:
        cbar_ax = fig.add_axes([0.85, 0.15, 0.05, 0.7])
        _draw_colorbar(
            fig, cbar_ax, cmap, level_info, data_range,
            position=cbar_layout["position"],
            tick_mode=overrides.get("colorbar_tick_mode", "values"),
            target_ticks=int(overrides.get("colorbar_nticks", params.get("colorbar_ticks", 3))),
            outline=bool(overrides.get("colorbar_outline", params.get("colorbar_outline", False))),
            title=snapshot.payload.get("intensity_label", "Intensity (a.u.)"),
            style_params=params,
        )

    try:
        if axes_artists is not None:
            axes_artists()
    finally:
        plotter.close()

    # 3D 注释叠加在图像坐标系（像素）；边距仍做一遍实测防裁切
    overflow = _fit_layout_3d(
        fig, ax_img, cbar_ax, width_mm, height_mm, margins, cbar_layout,
        scale=body_factor, title_plan=title_plan,
    )
    place_title(fig, title_plan, width_mm, height_mm, margins, cbar_layout, overflow)
    return fig


def _apply_clipping_planes(plotter, clip_render_bounds) -> None:
    import vtk

    r = [float(v) for v in clip_render_bounds]
    planes = vtk.vtkPlaneCollection()
    specs = [
        ((r[0], 0, 0), (1, 0, 0)),
        ((r[1], 0, 0), (-1, 0, 0)),
        ((0, r[2], 0), (0, 1, 0)),
        ((0, r[3], 0), (0, -1, 0)),
        ((0, 0, r[4]), (0, 0, 1)),
        ((0, 0, r[5]), (0, 0, -1)),
    ]
    for origin, normal in specs:
        plane = vtk.vtkPlane()
        plane.SetOrigin(origin)
        plane.SetNormal(normal)
        planes.AddItem(plane)
    volume = plotter.renderer.GetVolumes()
    volume.InitTraversal()
    vol = volume.GetNextItem()
    if vol is not None:
        vol.mapper.SetClippingPlanes(planes)


def _fit_layout_3d(fig, ax_img, cbar_ax, width_mm, height_mm, margins_mm, cbar_layout,
                   scale: float = 1.0, title_plan: Optional[Mapping[str, Any]] = None):
    """3D 布局：主图位置由视口决定，仅根据色条/注释溢出微调位置。

    注释文字绘制在图像像素坐标系，向图外溢出时平移主图而不是缩放。
    scale 为数据体大小系数：放置尺寸 = 内容区适配 × scale，边距因注释
    溢出增大时按比例同步缩小，不会把 scale 重置回 1。

    返回最终布局下各侧注释溢出（figure 分数），供 place_title 使用。
    """
    axes_list = [ax_img] + ([cbar_ax] if cbar_ax is not None else [])
    for _ in range(2):
        main_rect, cbar_rect = _compute_rects(width_mm, height_mm, margins_mm, cbar_layout)
        # 视口在新内容区居中，保持纵横比与数据体缩放
        content_w = main_rect[2] - main_rect[0]
        content_h = main_rect[3] - main_rect[1]
        pos = ax_img.get_position()
        vw_mm = pos.width * width_mm
        vh_mm = pos.height * height_mm
        aspect = vw_mm / max(vh_mm, 1e-6)
        if content_w / content_h >= aspect:
            new_h = content_h * scale
            new_w = new_h * aspect
        else:
            new_w = content_w * scale
            new_h = new_w / aspect
        x0 = (main_rect[0] + main_rect[2]) / 2.0 - new_w / 2.0
        y0 = (main_rect[1] + main_rect[3]) / 2.0 - new_h / 2.0
        ax_img.set_position(_fraction_rect([x0, y0, x0 + new_w, y0 + new_h], width_mm, height_mm))
        if cbar_ax is not None and cbar_rect is not None:
            cbar_ax.set_position(_fraction_rect(cbar_rect, width_mm, height_mm))
        overflow = _measure_overflow(fig, axes_list)
        changed = False
        for side in ("left", "right", "bottom", "top"):
            over_mm = overflow[side] * (width_mm if side in ("left", "right") else height_mm)
            need = over_mm + 1.2
            if title_plan is not None and side == title_plan["side"]:
                need = max(need, title_margin_need_mm(title_plan, over_mm))
            if need > margins_mm[side] + 1e-6:
                margins_mm[side] = need
                changed = True
        if not changed:
            break
    # 实测即绘制：_measure_overflow 内部会 draw 一次
    return _measure_overflow(fig, axes_list)


# ---------------------------------------------------------------------------
# 分发与保存
# ---------------------------------------------------------------------------

_FAMILY_RENDERERS = {}


def register_renderer(family):
    def decorator(fn):
        _FAMILY_RENDERERS[family] = fn
        return fn

    return decorator


_FAMILY_RENDERERS.update({"2d": render_2d, "1d": render_1d, "3d": render_3d})


def render_snapshot(snapshot, style, overrides, options, dpi: Optional[int] = None) -> Figure:
    """预览与正式导出共用同一 renderer：仅 dpi 不同。"""
    renderer = _FAMILY_RENDERERS.get(snapshot.view_family)
    if renderer is None:
        raise RenderError(f"不支持的视图族：{snapshot.view_family}")
    with RENDER_LOCK:
        return renderer(snapshot, style, overrides, options, int(dpi or options.dpi))


def save_figure(fig: Figure, path: str, options) -> None:
    """原子保存：同目录临时文件 → 校验 → os.replace。失败保留旧文件。"""
    fmt = str(options.fmt).lower()
    if fmt not in ("png", "pdf"):
        raise RenderError(f"不支持的输出格式：{fmt}")
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    tmp_path = os.path.join(
        directory, f".{os.path.basename(path)}.{os.getpid()}.pubtmp"
    )
    try:
        with RENDER_LOCK:
            if fmt == "pdf":
                # TrueType 子集嵌入，文字保持可选中；rc_context 仅作用于本作用域
                with matplotlib.rc_context({"pdf.fonttype": 42}):
                    fig.savefig(tmp_path, format="pdf", facecolor="white")
            else:
                fig.savefig(
                    tmp_path, format="png", dpi=int(options.dpi), facecolor="white"
                )
        if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) <= 0:
            raise RenderError("写入产物为空。")
        # Windows 上目标文件可能被杀毒/索引/预览短暂占用：有限次重试替换
        last_error = None
        for _ in range(5):
            try:
                os.replace(tmp_path, path)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.25)
        if last_error is not None:
            raise last_error
    except Exception:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise
