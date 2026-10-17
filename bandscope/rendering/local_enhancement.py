"""Single-actor display channels shared by the main scene and publication export."""
import numpy as np
from matplotlib import colormaps

from bandscope.core.local_enhancement import geometry, physical_bounds
from bandscope.processing.local_enhancement import orient_2d


def region_layers_2d(fields, data, slice_info, coords):
    """Scalar crop layers preserve the original background's interpolation path."""
    context = dict(fields.get("geometry_context", {}), view="2d", data=data, coords=coords,
                   slice_info=slice_info)
    axes, domain = geometry(context)
    layers = []
    flip = bool(slice_info.get("display_e_flip"))
    for region in fields["regions"]:
        if region["status"] not in {"ready", "constant"}:
            continue
        slices = region["slices"]
        values = fields["region_data"][region["region_id"]]
        # Later scalar images cover earlier ones in display space. Masking by
        # sample ownership here would cut interpolation holes beyond the later
        # crop's exact rectangle, changing pixels outside that rectangle.
        masked = np.ma.masked_invalid(values)
        logical = tuple(v + domain[2*i] for i, s in enumerate(slices) for v in (s.start, s.stop-1))
        raw_extent = tuple(float(coords[key][int(v)]) for key, pair in zip(axes, zip(logical[::2], logical[1::2])) for v in pair)
        # Keep the mask oriented along with the samples (np.asarray loses it).
        image = orient_2d(masked.data, slice_info, raw_extent)
        mask = orient_2d(np.ma.getmaskarray(masked), slice_info, raw_extent)
        extent = physical_bounds(context, logical, e_flip=flip)
        layers.append(dict(region_id=region["region_id"], image=np.ma.array(image, mask=mask),
                           extent=extent, level_info=region["level_info"]))
    return layers


def draw_region_layers_2d(ax, layers, cmap):
    from bandscope.rendering.render_core import VisualEngine
    return [ax.imshow(layer["image"], cmap=VisualEngine._leveled_cmap(cmap, layer["level_info"]),
                      vmin=layer["level_info"]["black_value"], vmax=layer["level_info"]["white_value"],
                      origin="lower", extent=layer["extent"], interpolation="spline16", resample=True,
                      aspect=ax.get_aspect(), zorder=2+i)
            for i, layer in enumerate(layers)]


def apply_volume_display(channel, color, alpha, cmap, multiplier=None, *, color_rules=None):
    """Upload color coordinates and final alpha as two independent components."""
    import vtk
    from bandscope.rendering.render_core import identity_opacity_function

    channel.attach()
    channel.buffer[:, 0] = np.asarray(color).ravel(order="F")
    final = np.asarray(alpha, dtype=np.float32)
    if multiplier is not None:
        final = np.clip(final * np.asarray(multiplier)[None, None, :], 0, 1)
    channel.buffer[:, 1] = final.ravel(order="F")
    channel.vtk_array.Modified()
    channel.volume.mapper.Modified()
    palette = colormaps.get_cmap(cmap) if isinstance(cmap, str) else cmap
    function = vtk.vtkColorTransferFunction()
    from bandscope.processing.display_transfer import mapped_positions
    samples = np.linspace(0., 1., 256)
    if color_rules:
        # Each owner gets the exact 256-node color transfer function used by
        # its crop. Gaps separate discontinuous endpoints between owners.
        for rule in color_rules:
            # The crop's PyVista lookup table stores the palette as uint8.
            colors = palette(mapped_positions(rule["level_info"], samples), bytes=True).astype(np.float64) / 255.
            for position, rgba in zip(samples + rule["offset"], colors):
                function.AddRGBPoint(float(position), *map(float, rgba[:3]))
        high = max(r["offset"] for r in color_rules) + 1.
    else:
        for position, rgba in zip(samples, palette(samples)):
            function.AddRGBPoint(float(position), *map(float, rgba[:3]))
        high = 1.
    channel.volume.mapper.lookup_table.apply_cmap(palette, n_values=256)
    channel.volume.mapper.scalar_range = (0.0, high)
    channel.volume.mapper.lookup_table.scalar_range = (0.0, high)
    prop = channel.volume.GetProperty()
    prop.SetColor(function)
    prop.SetScalarOpacity(identity_opacity_function())


def display_rgba(color, alpha, cmap):
    palette = colormaps.get_cmap(cmap) if isinstance(cmap, str) else cmap
    rgba = palette(np.clip(color, 0, 1), bytes=True)
    rgba[..., 3] = np.clip(np.asarray(alpha) * 255, 0, 255).astype(np.uint8)
    return rgba


def enhancement_legend(fields):
    parts = []
    for region in fields["regions"]:
        if region["status"] == "ready":
            low, high = region["data_range"]
            parts.append(f"{region.get('label', region['name'])}: {low:.4g}..{high:.4g}")
    mode = " (inactive)" if not fields.get("active", True) else " (shared)" if fields.get("common") else ""
    return "Local normalization" + mode + "\n" + "; ".join(parts)


def _colorbar_regions(regions):
    """可以画色带的区域：有有效动态范围（锁定或实测）的启用区域。"""
    return [
        r for r in regions
        if r["status"] in {"ready", "constant"}
        and r["level_info"]["white_value"] > r["level_info"]["black_value"]
    ]


def draw_region_colorbars_2d(ax, regions, cmap):
    """在 2D 视图左下角为每个可见区域叠一条小色带；返回 colorbar 列表（调用方负责移除）。

    色带用区域自己的 level_info 做映射与刻度，与区域内图像的着色保持一致；
    区域锁定时这里自然显示锁定的范围。可见区域由调用方按下拉框选择过滤。
    """
    from matplotlib import cm
    from matplotlib.colors import Normalize

    from bandscope.rendering.render_core import VisualEngine
    from bandscope.ui import theme

    colorbars = []
    fig = getattr(ax, "figure", None)
    if fig is None:
        return colorbars
    for i, region in enumerate(_colorbar_regions(regions)):
        info = region["level_info"]
        # 每条色带占一个竖向槽位，从左下角向上堆叠：标签在色带上方，刻度在下方。
        cax = ax.inset_axes([0.03, 0.05 + i * 0.105, 0.30, 0.032])
        mappable = cm.ScalarMappable(
            norm=Normalize(info["black_value"], info["white_value"]),
            cmap=VisualEngine._leveled_cmap(cmap, info),
        )
        colorbar = fig.colorbar(mappable, cax=cax, orientation="horizontal")
        ticks = VisualEngine._level_ticks(info)
        colorbar.set_ticks(ticks)
        colorbar.set_ticklabels([VisualEngine._format_level_tick(t) for t in ticks])
        colorbar.ax.xaxis.set_label_position("top")
        colorbar.set_label(str(region.get("label", region["name"])), color=theme.TEXT_1, fontsize=8)
        colorbar.ax.tick_params(colors=theme.TEXT_2, labelsize=7, length=2, pad=1)
        try:
            colorbar.outline.set_edgecolor(theme.BORDER_HEX)
        except Exception:
            pass
        colorbars.append(colorbar)
    return colorbars


def add_region_scalar_bars_3d(plotter, regions, cmap):
    """为每个区域在 3D 视图左侧加一条竖向 scalar bar；返回标题列表（用于移除）。"""
    import pyvista as pv

    from bandscope.rendering.render_core import VisualEngine

    titles = []
    for i, region in enumerate(_colorbar_regions(regions)):
        info = region["level_info"]
        title = str(region.get("label", region["name"]))
        lut = pv.LookupTable(
            cmap=VisualEngine._leveled_cmap(cmap, info),
            scalar_range=(info["black_value"], info["white_value"]),
        )
        bar = plotter.add_scalar_bar(
            title=title,
            n_labels=3,
            vertical=True,
            position_x=0.02,
            position_y=min(0.08 + i * 0.30, 0.66),
            width=0.06,
            height=0.26,
            fmt="%.4g",
            color=VisualEngine._plotter_text_color(plotter),
            title_font_size=10,
            label_font_size=8,
            use_opacity=False,
            render=False,
        )
        try:
            bar.SetLookupTable(lut)
        except AttributeError:
            pass
        titles.append(title)
    return titles
