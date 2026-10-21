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
        extent = physical_bounds(context, logical)
        if flip and int(slice_info["axis"]) in (0, 1):
            # 主图把 E 轴刻度与像素一起镜像，区域层必须用同一显示方向，
            # 否则叠加层会相对底图上下颠倒。
            extent = (extent[0], extent[1], extent[3], extent[2])
        layers.append(dict(region_id=region["region_id"], image=np.ma.array(image, mask=mask),
                           extent=extent, level_info=region["level_info"]))
    return layers


def draw_region_layers_2d(ax, layers, cmap, existing=()):
    """Draw region crop layers, reusing existing images when the count matches."""
    from bandscope.rendering.render_core import VisualEngine

    images = []
    for i, layer in enumerate(layers):
        info = layer["level_info"]
        display_cmap = VisualEngine._leveled_cmap(cmap, info)
        target = existing[i] if i < len(existing) else None
        if target is not None and target.axes is ax and target in ax.images:
            target.set_data(layer["image"])
            target.set_cmap(display_cmap)
            target.set_clim(info["black_value"], info["white_value"])
            target.set_extent(layer["extent"])
            target.set_zorder(2 + i)
            images.append(target)
        else:
            images.append(ax.imshow(layer["image"], cmap=display_cmap,
                                    vmin=info["black_value"], vmax=info["white_value"],
                                    origin="lower", extent=layer["extent"], interpolation="spline16",
                                    resample=True, aspect=ax.get_aspect(), zorder=2+i))
    for extra in list(existing)[len(layers):]:
        if extra.axes is not None:
            extra.remove()
    return images


def apply_volume_display(channel, color, alpha, cmap, multiplier=None, *, color_rules=None):
    """Upload color coordinates and final alpha as two independent components."""
    import vtk
    from bandscope.rendering.render_core import identity_opacity_function

    channel.attach()
    volume_shape = np.shape(color)
    # Write straight into the interleaved buffer through a transposed view: one
    # pass instead of "raven(order=F) + strided column assignment" (two passes
    # and an 18 MB temporary for a 4.5M-voxel volume).
    np.copyto(channel.buffer[:, 0].reshape(volume_shape, order="F"), np.asarray(color))
    final = np.asarray(alpha, dtype=np.float32)
    if multiplier is not None:
        final = np.clip(final * np.asarray(multiplier)[None, None, :], 0, 1)
    np.copyto(channel.buffer[:, 1].reshape(volume_shape, order="F"), final)
    channel.vtk_array.Modified()
    channel.volume.mapper.Modified()
    palette = colormaps.get_cmap(cmap) if isinstance(cmap, str) else cmap
    palette_key = str(cmap)
    cached = getattr(channel, "_display_transfer", None)
    if cached is None or cached[0] != palette_key:
        samples = np.linspace(0., 1., 256)
        # Gamma and the owner's intensity window have already been evaluated per
        # voxel. Only the base palette remains for the GPU's uniform lookup table.
        colors = palette(samples, bytes=True).astype(np.float64) / 255.
        function = vtk.vtkColorTransferFunction()
        for position, rgba in zip(samples, colors):
            function.AddRGBPoint(float(position), *map(float, rgba[:3]))
        cached = (palette_key, function, identity_opacity_function())
        channel._display_transfer = cached
        # The palette table only depends on the colormap; rebuilding it for every
        # level tick is the expensive half of this call.
        channel.volume.mapper.lookup_table.apply_cmap(palette, n_values=256)
    _key, function, opacity_function = cached
    high = 1.
    channel.volume.mapper.scalar_range = (0.0, high)
    channel.volume.mapper.lookup_table.scalar_range = (0.0, high)
    prop = channel.volume.GetProperty()
    prop.SetColor(function)
    prop.SetScalarOpacity(opacity_function)


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


def draw_region_colorbars_2d(ax, regions, cmap, existing=()):
    """在 2D 视图左下角为每个可见区域叠一条小色带；返回 colorbar 列表（调用方负责移除）。

    色带用区域自己的 level_info 做映射与刻度，与区域内图像的着色保持一致；
    区域锁定时这里自然显示锁定的范围。可见区域由调用方按下拉框选择过滤。
    数量不变时复用现有 colorbar，只更新 mappable 与刻度。
    """
    from bandscope.rendering.render_core import VisualEngine
    from bandscope.ui import theme

    colorbars = []
    fig = getattr(ax, "figure", None)
    if fig is None:
        return colorbars
    visible = _colorbar_regions(regions)
    existing = list(existing)
    if len(existing) != len(visible):
        for bar in existing:
            try:
                bar.remove()
            except Exception:
                pass
        existing = []
    for i, region in enumerate(visible):
        info = region["level_info"]
        mappable = VisualEngine._colorbar_mappable(cmap, info)
        if i < len(existing):
            colorbar = existing[i]
            colorbar.update_normal(mappable)
        else:
            # 每条色带占一个竖向槽位，从左下角向上堆叠：标签在色带上方，刻度在下方。
            cax = ax.inset_axes([0.03, 0.05 + i * 0.105, 0.30, 0.032])
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
        try:
            # 右键菜单据此把这条色带解析回它的增强区域。
            colorbar.ax._arpes_band_target = ("region", region["region_id"])
        except Exception:
            pass
        colorbars.append(colorbar)
    return colorbars


def add_region_scalar_bars_3d(plotter, regions, cmap):
    """为每个区域在 3D 视图左侧加一条竖向 scalar bar。

    返回与色带一一对应的记录（title / region_id / actor），调用方负责按
    title 移除。actor 可能为 None（同名色带被 PyVista 复用），此时右键命中
    回退到按 title 查找。
    """
    from bandscope.rendering.render_core import VisualEngine

    records = []
    for i, region in enumerate(_colorbar_regions(regions)):
        info = region["level_info"]
        title = str(region.get("label", region["name"]))
        lut = VisualEngine._3d_colorbar_lut(cmap, info)
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
            bar.SetDrawTickLabels(False)
            bar.SetDrawAnnotations(True)
        except AttributeError:
            pass
        records.append({"title": title, "region_id": region["region_id"], "actor": bar})
    return records
