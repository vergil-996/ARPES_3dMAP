"""Matplotlib rendering for per-segment E–k path slices."""
from __future__ import annotations

from typing import Dict, Optional, Sequence, Tuple

import numpy as np
from matplotlib import colormaps
from matplotlib.cm import ScalarMappable
from matplotlib.colors import ListedColormap, Normalize

import bandscope.ui.theme as theme
from bandscope.core.path_slice import PathSegmentStyle, PathSliceModel
from bandscope.processing import display_transfer
from bandscope.processing.path_slice import PathSample
from bandscope.rendering.render_core import VisualEngine


def segment_level_info(values, style: PathSegmentStyle, *, locked_range=None) -> Dict[str, float]:
    data_range = locked_range or display_transfer.finite_minmax(values) or (0.0, 1.0)
    return display_transfer.level_info(data_range, style.levels)


def _mapped_colors(info, intensity_map: str, count: int = 256) -> np.ndarray:
    positions = np.linspace(0.0, 1.0, count)
    mapped = display_transfer.mapped_positions(info, positions)
    ramp = display_transfer.OPACITY_MAPS.get(
        str(intensity_map), display_transfer.OPACITY_MAPS["linear"]
    )
    return np.interp(mapped, np.linspace(0.0, 1.0, len(ramp)), ramp)


def display_cmap(cmap, info, intensity_map: str) -> ListedColormap:
    base = colormaps.get_cmap(cmap) if isinstance(cmap, str) else cmap
    colors = base(_mapped_colors(info, intensity_map))
    return ListedColormap(colors, name=f"{getattr(base, 'name', 'path')}-mapped")


def segment_rgba(
    values,
    style: PathSegmentStyle,
    cmap,
    *,
    locked_range=None,
) -> Tuple[np.ndarray, Dict[str, float]]:
    source = np.asarray(values, dtype=np.float64)
    info = segment_level_info(source, style, locked_range=locked_range)
    normalized = display_transfer.normalized_color_samples(source, info)
    positions = display_transfer.mapped_positions(info, normalized)
    ramp = display_transfer.OPACITY_MAPS.get(
        str(style.intensity_map), display_transfer.OPACITY_MAPS["linear"]
    )
    mapped = np.interp(positions, np.linspace(0.0, 1.0, len(ramp)), ramp)
    base = colormaps.get_cmap(cmap) if isinstance(cmap, str) else cmap
    rgba = base(mapped)
    rgba[~np.isfinite(source)] = (0.0, 0.0, 0.0, 0.0)
    return rgba, info


def axis_edges(values: Sequence[float]) -> np.ndarray:
    coordinates = np.asarray(values, dtype=np.float64).reshape(-1)
    if coordinates.size == 1:
        step = max(abs(float(coordinates[0])) * 0.01, 0.5)
        return np.asarray([coordinates[0] - step, coordinates[0] + step])
    edges = np.empty(coordinates.size + 1, dtype=np.float64)
    edges[1:-1] = (coordinates[:-1] + coordinates[1:]) / 2.0
    edges[0] = coordinates[0] + (coordinates[0] - edges[1])
    edges[-1] = coordinates[-1] + (coordinates[-1] - edges[-2])
    return edges


def full_viewport(sample: PathSample) -> Tuple[float, float]:
    if sample.k.size == 1:
        edges = axis_edges(sample.k)
        return float(edges[0]), float(edges[-1])
    return float(sample.k[0]), float(sample.k[-1])


def clamp_viewport(viewport, sample: PathSample) -> Tuple[float, float]:
    full_low, full_high = full_viewport(sample)
    span = full_high - full_low
    if span <= 0:
        return full_low, full_high
    if viewport is None:
        return full_low, full_high
    low, high = sorted((float(viewport[0]), float(viewport[1])))
    width = min(high - low, span)
    if width <= 0:
        return full_viewport(sample)
    low = float(np.clip(low, full_low, full_high - width))
    return low, low + width


def recommended_viewport(sample: PathSample, width_px: float, height_px: float) -> Tuple[float, float]:
    full_low, full_high = full_viewport(sample)
    total = full_high - full_low
    if sample.k.size < 2 or total <= 0:
        return full_low, full_high
    spacing = float(np.median(np.abs(np.diff(sample.k))))
    energy_rows = max(1, sample.energy.size - 1)
    aspect = max(float(width_px), 1.0) / max(float(height_px), 1.0)
    visible_columns = max(12.0, aspect * energy_rows)
    width = min(total, spacing * visible_columns)
    return full_low, full_low + width


def viewport_for_segment(sample: PathSample, index: int, viewport) -> Tuple[float, float]:
    current = clamp_viewport(viewport, sample)
    if not 0 <= index < len(sample.segments):
        return current
    segment = sample.segments[index]
    width = current[1] - current[0]
    if segment.start_k >= current[0] and segment.end_k <= current[1]:
        return current
    center = (segment.start_k + segment.end_k) / 2.0
    return clamp_viewport((center - width / 2.0, center + width / 2.0), sample)


def matching_colorbar(model: PathSliceModel, sample: PathSample, locked_range=None):
    selected = [index for index in model.selected if 0 <= index < len(sample.segments)]
    if not selected:
        return None
    reference = None
    for index in selected:
        item = sample.segments[index]
        style = model.segments[index]
        info = segment_level_info(item.intensity, style, locked_range=locked_range)
        signature = (style.intensity_map, tuple(round(float(info[key]), 12) for key in (
            "black_value", "gray_value", "white_value", "gamma_power"
        )))
        if reference is None:
            reference = (signature, info, style)
        elif signature != reference[0]:
            return None
    return reference[1:]


def clear_path_artists(ax) -> None:
    for artist in getattr(ax, "_path_slice_artists", ()):
        if getattr(artist, "axes", None) is not ax:
            continue
        try:
            artist.remove()
        except (ValueError, AttributeError):
            pass
    ax._path_slice_artists = []


def render_path_slice(
    ax,
    canvas,
    sample: PathSample,
    model: PathSliceModel,
    *,
    cmap="magma",
    title="",
    viewport=None,
    display_e_flip=False,
    locked_range=None,
    energy_label="E",
) -> Dict[str, object]:
    VisualEngine.clear_2d_colorbar(ax)
    VisualEngine._last_data_range = display_transfer.finite_minmax(sample.intensity)
    clear_path_artists(ax)
    ax.clear()
    ax._path_slice_artists = []
    ax._arpes_image = None
    ax.set_facecolor(theme.CANVAS_BG)
    for spine in ax.spines.values():
        spine.set_color(theme.BORDER_HEX)
    ax.tick_params(colors=theme.TEXT_2, labelsize=9)
    ax.set_title(
        str(title or model.path_name()), color=theme.TEXT_1, fontsize=11, pad=12,
        fontfamily=theme.MPL_FONT_FAMILIES,
    )
    VisualEngine.reset_2d_axis_labels(ax)
    ax.set_xlabel(
        sample.calibration.label, color=theme.TEXT_1,
        labelpad=22 if model.show_labels else 8, fontfamily=theme.MPL_FONT_FAMILIES,
    )
    ax.set_ylabel(str(energy_label), color=theme.TEXT_1, fontfamily=theme.MPL_FONT_FAMILIES)

    energy = np.asarray(sample.energy, dtype=np.float64)
    descending = energy.size > 1 and energy[0] > energy[-1]
    for item in sample.segments:
        values = np.asarray(item.intensity, dtype=np.float64)
        k = np.linspace(item.start_k, item.end_k, values.shape[0])
        if descending:
            values = np.flip(values, axis=1)
        rgba, _ = segment_rgba(values, model.segments[item.index], cmap, locked_range=locked_range)
        if item.index and rgba.shape[0]:
            rgba[0, :, 3] = 0.0
        image = ax.pcolormesh(
            axis_edges(k),
            axis_edges(energy),
            np.transpose(rgba, (1, 0, 2)),
            shading="auto",
            rasterized=True,
        )
        ax._path_slice_artists.append(image)

    viewport = clamp_viewport(viewport, sample)
    ax.set_xlim(*viewport)
    y_edges = axis_edges(energy)
    y_low, y_high = sorted((float(y_edges[0]), float(y_edges[-1])))
    ax.set_ylim(y_high, y_low) if display_e_flip else ax.set_ylim(y_low, y_high)
    ax.set_xticks([])
    if model.show_guides:
        for position in sample.node_k:
            line = ax.axvline(
                float(position), color=theme.TEXT_2, linestyle=(0, (4, 4)),
                linewidth=0.8, alpha=0.72, zorder=5,
            )
            ax._path_slice_artists.append(line)
    if model.show_labels:
        transform = ax.get_xaxis_transform()
        for position, node in zip(sample.node_k, model.nodes):
            label = ax.text(
                float(position), -0.035, node.label, transform=transform,
                ha="center", va="top", color=theme.TEXT_1, fontsize=9,
                fontfamily=theme.MPL_FONT_FAMILIES, clip_on=False, zorder=6,
            )
            ax._path_slice_artists.append(label)

    try:
        ax.figure.subplots_adjust(left=0.13, right=0.92, top=0.88, bottom=0.22)
    except Exception:
        pass
    colorbar_state = matching_colorbar(model, sample, locked_range)
    if colorbar_state is not None:
        info, style = colorbar_state
        image = ScalarMappable(
            norm=Normalize(vmin=info["black_value"], vmax=info["white_value"]),
            cmap=display_cmap(cmap, info, style.intensity_map),
        )
        ax.set_position(VisualEngine._2d_image_position(ax))
        colorbar_ax = ax.inset_axes([
            1.0 + VisualEngine.COLORBAR_INSET_PAD,
            0.0,
            VisualEngine.COLORBAR_INSET_WIDTH,
            1.0,
        ])
        colorbar_ax._arpes_colorbar_axis = True
        colorbar = ax.figure.colorbar(image, cax=colorbar_ax)
        VisualEngine._configure_2d_colorbar(colorbar, info)
        if len(model.selected) == 1:
            colorbar.set_label(
                f"{VisualEngine.COLORBAR_TITLE} · {model.segment_name(model.selected[0])}",
                color=theme.TEXT_1,
            )
        ax._arpes_colorbar = colorbar
        ax._arpes_colorbar_ax = colorbar_ax
    canvas.draw_idle()
    return {
        "viewport": viewport,
        "colorbar": colorbar_state is not None,
        "full_viewport": full_viewport(sample),
    }
