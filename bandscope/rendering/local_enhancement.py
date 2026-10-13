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
