"""1D 外观配置渲染；线条次序、图例与标签不参与科学数值计算。"""
from __future__ import annotations

import textwrap

import numpy as np
from matplotlib import colormaps
from matplotlib.ticker import AutoMinorLocator
from matplotlib.transforms import Bbox

from bandscope.exporting.curve_presentation import Figure1DPresentation, ordered_curves
from bandscope.exporting.publication_models import PUB_CURVE_PALETTE, PUB_LINESTYLES, resolve_axis_labels


def curve_style(curve, presentation, params, waterfall=False, count=1):
    slot = curve.palette_slot
    policy = presentation.figure.get("palette", "legacy")
    color = params.get("ink_color", "#000000") if waterfall else PUB_CURVE_PALETTE[slot % len(PUB_CURVE_PALETTE)]
    linestyle = "-" if waterfall else PUB_LINESTYLES[(slot // len(PUB_CURVE_PALETTE)) % len(PUB_LINESTYLES)]
    if policy == "uniform":
        color = presentation.figure.get("uniform_color", "#000000")
    elif policy == "categorical":
        color = PUB_CURVE_PALETTE[slot % len(PUB_CURVE_PALETTE)]
        linestyle = PUB_LINESTYLES[(slot // len(PUB_CURVE_PALETTE)) % len(PUB_LINESTYLES)]
    elif policy == "gray":
        color = "#333333"
        linestyle = PUB_LINESTYLES[slot % len(PUB_LINESTYLES)]
    elif policy == "gradient":
        color = colormaps["viridis"](slot / max(1, count - 1))
    width = float(params.get("curve_linewidth", 0.8))
    if curve.is_base and presentation.figure.get("base_emphasis", True):
        width *= 1.5
    result = dict(color=color, linestyle=linestyle, linewidth=width, alpha=1.0,
                  marker="", markersize=3.0, marker_filled=True, markevery=1,
                  visible=True, legend=not waterfall, label=curve.label)
    result.update(presentation.defaults)
    result.update(presentation.curves.get(curve.curve_id, {}))
    return result


def _draw_annotations(ax, presentation, params):
    for item in presentation.annotations:
        kind = item["kind"]
        x, y = item.get("x", 0), item.get("y", 0)
        x2, y2 = item.get("x2", 1), item.get("y2", 1)
        color = item.get("color", params.get("ink_color", "#000000"))
        kwargs = dict(color=color, alpha=item.get("alpha", 1),
                      linewidth=item.get("linewidth", 0.6), linestyle=item.get("linestyle", "--"))
        if kind in ("text", "arrow"):
            coords = item.get("coordinates", "data")
            arrow = dict(arrowstyle="->", **kwargs) if kind == "arrow" else None
            ax.annotate(item.get("text", ""), xy=(x, y), xytext=(x2, y2) if arrow else (x, y),
                        xycoords=coords, textcoords=coords, arrowprops=arrow,
                        fontsize=item.get("size", 7), color=color,
                        alpha=item.get("alpha", 1), fontfamily=params["font_family"],
                        annotation_clip=True, clip_on=True)
        elif kind == "hline":
            ax.axhline(y, **kwargs)
        elif kind == "vline":
            ax.axvline(x, **kwargs)
        elif kind == "hspan":
            ax.axhspan(min(y, y2), max(y, y2), **kwargs)
        elif kind == "vspan":
            ax.axvspan(min(x, x2), max(x, x2), **kwargs)


def _waterfall_labels(ax, curves, presentation, params):
    figure = presentation.figure
    mode = figure.get("waterfall_labels", "all")
    if mode == "none":
        return []
    texts = []
    for i, curve in enumerate(sorted(curves, key=lambda c: c.palette_slot)):
        if mode == "every" and i % figure.get("waterfall_every", 1):
            continue
        text = ax.annotate(f"{curve.k_value:.4g}", xy=(curve.offset + 0.5, 1.01),
                           xycoords=("data", "axes fraction"),
                           xytext=(0, figure.get("waterfall_label_gap", 0)), textcoords="offset points",
                           fontsize=figure.get("waterfall_label_size", params.get("tick_label_size", 6)),
                           rotation=figure.get("waterfall_label_rotation", 0),
                           fontfamily=params["font_family"],
                           color=figure.get("waterfall_label_color", params.get("ink_color", "#000000")),
                           ha="center", va="bottom", annotation_clip=False)
        texts.append(text)
    return texts


def _thin_labels(fig, texts):
    if not texts:
        return
    for text in texts:
        text.set_visible(True)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    bounds = [t.get_window_extent(renderer).expanded(1.12, 1.1) for t in texts]
    keep = [0]
    if len(texts) > 1 and not bounds[0].overlaps(bounds[-1]):
        keep.append(len(texts) - 1)
    for i in range(1, len(texts) - 1):
        if not any(bounds[i].overlaps(bounds[j]) for j in keep):
            keep.append(i)
    for i, text in enumerate(texts):
        text.set_visible(i in keep)


def _legend_overlaps(fig, ax, legend):
    fig.canvas.draw()
    box = legend.get_window_extent(fig.canvas.get_renderer()).expanded(1.03, 1.03)
    for line in ax.lines:
        if not line.get_visible():
            continue
        path = line.get_transform().transform_path(line.get_path())
        # Path intersection includes crossing segments, not just sampled points.
        if path.intersects_bbox(box, filled=False):
            return True
    return False


def _position_external_legend(fig, ax, legend, position):
    """图外图例排在刻度/轴名/公共指数之外，而非只按坐标框定位。"""
    if legend is None or not position.startswith("outside_"):
        return
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    bounds = ax.get_window_extent(renderer)
    decorations = [ax.xaxis.label, ax.yaxis.label,
                   ax.xaxis.get_offset_text(), ax.yaxis.get_offset_text(),
                   *ax.get_xticklabels(), *ax.get_yticklabels(), *ax.texts]
    boxes = [v.get_window_extent(renderer) for v in decorations if v.get_visible()
             and getattr(v, "get_text", lambda: "")()]
    gap = fig.dpi * 2 / 72
    x, y = bounds.x0, bounds.y1
    if position == "outside_top":
        y = max([bounds.y1] + [b.y1 for b in boxes]) + gap
    elif position == "outside_bottom":
        y = min([bounds.y0] + [b.y0 for b in boxes]) - gap
    elif position == "outside_left":
        x = min([bounds.x0] + [b.x0 for b in boxes]) - gap
    elif position == "outside_right":
        x = max([bounds.x1] + [b.x1 for b in boxes]) + gap
    legend.set_bbox_to_anchor(tuple(ax.transAxes.inverted().transform((x, y))), transform=ax.transAxes)


def _fit_presentation_layout(fig, ax, legend, position, width, height, margins, title_plan, base):
    for _ in range(8):
        _position_external_legend(fig, ax, legend, position)
        old = dict(margins)
        margins, overflow = base._fit_layout(fig, ax, None, width, height, margins, None,
                                             iterations=1, title_plan=title_plan)
        if all(abs(old[key] - margins[key]) < 0.01 for key in old):
            break
    _position_external_legend(fig, ax, legend, position)
    overflow = base._measure_overflow(fig, [ax])
    return margins, overflow


def _legend(ax, snapshot, presentation, params, handles):
    figure = presentation.figure
    position = figure.get("legend_position", "template")
    if position == "none" or not handles:
        return None, position
    rank = {key: i for i, key in enumerate(presentation.legend_order)}
    handles = sorted(handles, key=lambda item: rank.get(item[0], len(rank) + item[3]))
    kwargs = dict(ncols=figure.get("legend_columns", 1),
                  fontsize=params.get("legend_size", 6), labelcolor=params.get("ink_color", "#000000"),
                  prop={"family": params["font_family"], "size": params.get("legend_size", 6)},
                  frameon=figure.get("legend_frame", False),
                  facecolor=figure.get("legend_background", "#FFFFFF"), framealpha=figure.get("legend_alpha", 0.8),
                  labelspacing=figure.get("legend_spacing", 0.5),
                  handlelength=figure.get("legend_handlelength", 2))
    lines = [v[1] for v in handles]
    labels = [textwrap.fill(v[2], width=figure.get("legend_wrap", 32), break_long_words=True) for v in handles]
    if position == "template":
        position = "outside_top" if params.get("legend_mode") == "above" else "auto"
    def create(location):
        outside = {
            "outside_top": ("lower left", (0, 1.02)),
            "outside_bottom": ("upper left", (0, -0.18)),
            "outside_right": ("upper left", (1.03, 1)),
            "outside_left": ("upper right", (-0.22, 1)),
        }
        if location in outside:
            loc, anchor = outside[location]
            return ax.legend(lines, labels, loc=loc, bbox_to_anchor=anchor, borderaxespad=0, **kwargs)
        return ax.legend(lines, labels, loc=location, **kwargs)
    if position == "auto":
        for location in ("upper right", "upper left", "lower right", "lower left", "center"):
            legend = create(location)
            if not _legend_overlaps(ax.figure, ax, legend):
                position = location
                break
        else:
            position = "outside_right"
            legend = create(position)
    else:
        legend = create(position)
    for text in legend.get_texts():
        text.set_fontfamily(params["font_family"])
    return legend, position


def render_presentation(snapshot, style, overrides, options, dpi, presentation):
    from bandscope.exporting import publication_renderers as base
    p = Figure1DPresentation.from_dict(presentation.to_dict() if isinstance(presentation, Figure1DPresentation)
                                      else presentation)
    params = base._font_style_params(style, overrides)
    params.update(p.figure)
    width = float(options.width_mm)
    height = options.resolved_height_mm("1d")
    fig = base._new_figure(width, height, dpi, bool(options.transparent))
    if not options.transparent:
        fig.set_facecolor(params.get("canvas_color", "#FFFFFF"))
    ax = fig.add_axes([0.18, 0.18, 0.68, 0.68])
    ax.set_facecolor("none" if options.transparent else params.get("canvas_color", "#FFFFFF"))
    curves = ordered_curves(snapshot, p)
    waterfall = snapshot.view == "waterfall"
    visible, handles = [], []
    for curve in curves:
        config = curve_style(curve, p, params, waterfall, snapshot.payload.get("palette_span", len(curves)))
        x, y = np.asarray(curve.x), np.asarray(curve.y)
        if not config["visible"] or not np.any(np.isfinite(x) & np.isfinite(y)):
            continue
        if config["linestyle"] == "none" and not config["marker"]:
            raise base.RenderError("可见曲线必须显示线条或标记，请选择一种绘制方式。")
        line, = ax.plot(x, y, color=config["color"], linestyle=config["linestyle"],
                        linewidth=config["linewidth"], alpha=config["alpha"], marker=config["marker"],
                        markersize=config["markersize"], markevery=config["markevery"],
                        markerfacecolor=config["color"] if config["marker_filled"] else "none",
                        markeredgecolor=config["color"], gid=curve.curve_id)
        visible.append(curve)
        label = config["label"]
        explicit = p.curves.get(curve.curve_id, {})
        if curve.is_base and snapshot.view == "1d_comparison" and "label" not in explicit:
            label = f"基准 - {label}"
        meaningful_single = snapshot.view != "1d" or bool(snapshot.payload["curve"].get("label"))
        meaningful_single |= "label" in explicit or explicit.get("legend", False)
        if config["legend"] and label and meaningful_single:
            handles.append((curve.curve_id, line, label, len(handles)))
    if not visible:
        fig.clear()
        raise base.RenderError("至少显示一条有有效数据的曲线才能导出。")
    ax.margins(x=0.02, y=0.08)
    if waterfall:
        step = float(snapshot.payload["offset_step"])
        ax.set_xlim(-0.1, max(1.25, (len(curves) - 1) * step + 1.1))
    for name in ("x", "y"):
        if f"{name}lim" in p.figure:
            getattr(ax, f"set_{name}lim")(p.figure[f"{name}lim"])
        if p.figure.get(f"{name}_reverse", False):
            getattr(ax, f"invert_{name}axis")()
        lo, hi = getattr(ax, f"get_{name}lim")()
        axis = getattr(ax, f"{name}axis")
        base._apply_formatter(axis, base.nice_ticks(lo, hi, p.figure.get(f"{name}_ticks", params.get("max_major_ticks", 5))), lo, hi)
        if p.figure.get(f"{name}_minor", False):
            axis.set_minor_locator(AutoMinorLocator())
    base._style_axes_frame(ax, params, overrides, "1d")
    ax.tick_params(which="both", direction=params.get("tick_direction", "out"),
                   color=p.figure.get("tick_color", params.get("ink_color", "#000000")))
    ax.tick_params(axis="x", labelbottom=p.figure.get("x_ticklabels", True))
    ax.tick_params(axis="y", labelleft=p.figure.get("y_ticklabels", True))
    ax.xaxis.get_offset_text().set_visible(p.figure.get("x_ticklabels", True))
    ax.yaxis.get_offset_text().set_visible(p.figure.get("y_ticklabels", True))
    if p.figure.get("major_grid", False):
        ax.grid(True, which="major", color=params.get("ink_color", "#000000"), alpha=0.18, linewidth=0.4)
    if p.figure.get("minor_grid", False):
        ax.xaxis.set_minor_locator(AutoMinorLocator())
        ax.yaxis.set_minor_locator(AutoMinorLocator())
        ax.grid(True, which="minor", color=params.get("ink_color", "#000000"), alpha=0.1, linewidth=0.3)
    base._style_axis_labels(ax, params, *resolve_axis_labels(snapshot, overrides))
    ax.xaxis.labelpad = ax.yaxis.labelpad = p.figure.get("labelpad", 2)
    base._add_panel_label(ax, overrides, params)
    labels = _waterfall_labels(ax, visible, p, params) if waterfall else []
    # References must not enlarge data limits or change automatic scientific ranges.
    xlim, ylim = ax.get_xlim(), ax.get_ylim()
    _draw_annotations(ax, p, params)
    ax.set_xlim(xlim)
    ax.set_ylim(ylim)
    legend, position = _legend(ax, snapshot, p, params, handles)
    margins = base._initial_margins(params)
    if margins["left"] + margins["right"] >= width - 10 or margins["top"] + margins["bottom"] >= height - 10:
        fig.clear()
        raise base.RenderError("留白过大，绘图区不足 10 mm；请减小留白或增大图片尺寸。")
    title_plan = base.plan_title(snapshot, overrides, params, width)
    margins, overflow = _fit_presentation_layout(fig, ax, legend, position, width, height, margins, title_plan, base)
    if p.figure.get("waterfall_labels") == "auto":
        _thin_labels(fig, labels)
        margins, overflow = _fit_presentation_layout(fig, ax, legend, position, width, height, margins, title_plan, base)
        _thin_labels(fig, labels)
    if legend is not None and position in ("upper right", "upper left", "lower right", "lower left", "center"):
        if p.figure.get("legend_position", "template") in ("template", "auto") and _legend_overlaps(fig, ax, legend):
            p.figure["legend_position"] = "outside_right"
            legend, position = _legend(ax, snapshot, p, params, handles)
            margins, overflow = _fit_presentation_layout(fig, ax, legend, position, width, height, margins, title_plan, base)
    base.place_title(fig, title_plan, width, height, margins, None, overflow)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    canvas = Bbox.from_bounds(0, 0, fig.bbox.width, fig.bbox.height)
    artists = ([legend] if legend is not None else []) + [v for v in labels if v.get_visible()]
    for artist in artists:
        box = artist.get_window_extent(renderer)
        if box.x0 < canvas.x0 - 1 or box.x1 > canvas.x1 + 1 or box.y0 < canvas.y0 - 1 or box.y1 > canvas.y1 + 1:
            fig.clear()
            raise base.RenderError("图例或标签超出画布；请减少列数/字号、使用稀疏标签或增大图片尺寸。")
    return fig
