import numpy as np
import vtk
import pyvista as pv
from matplotlib import colormaps
from matplotlib.colors import ListedColormap
from PIL import Image
from pyvista.plotting.cube_axes_actor import make_axis_labels
from vtkmodules.util.numpy_support import numpy_to_vtk


class VisualEngine:
    """渲染与绘图引擎，负责所有 3D 和 2D 的视觉呈现"""

    COLORBAR_TITLE = "Intensity"
    @staticmethod
    def _level_info(data, levels_params, include_zero=False):
        black, gamma, white = levels_params
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

    @staticmethod
    def _level_mapped_positions(level_info, samples):
        samples = np.asarray(samples, dtype=np.float64)
        mapped = np.power(np.clip(samples, 0.0, 1.0), level_info["gamma_power"])
        return level_info["black_pos"] + (
            level_info["white_pos"] - level_info["black_pos"]
        ) * mapped

    @staticmethod
    def _leveled_cmap(cmap, level_info):
        base_cmap = colormaps.get_cmap(cmap) if isinstance(cmap, str) else cmap
        samples = np.linspace(0.0, 1.0, 256)
        colors = base_cmap(VisualEngine._level_mapped_positions(level_info, samples))
        name = f"{getattr(base_cmap, 'name', 'cmap')}_levels_{level_info['gamma_power']:.4g}"
        return ListedColormap(colors, name=name)

    @staticmethod
    def _mapped_opacity(opacity, level_info):
        values = np.asarray(opacity, dtype=np.float64)
        if values.size <= 1:
            return opacity

        x = np.linspace(0.0, 1.0, values.size)
        mapped_x = np.power(x, level_info["gamma_power"])
        return np.interp(mapped_x, x, values).tolist()

    @staticmethod
    def _level_ticks(level_info):
        ticks = [
            float(level_info["black_value"]),
            float(level_info["gray_value"]),
            float(level_info["white_value"]),
        ]
        deduped = []
        for tick in ticks:
            if not deduped or not np.isclose(tick, deduped[-1], rtol=1e-9, atol=1e-12):
                deduped.append(tick)
        return deduped

    @staticmethod
    def _format_level_tick(value):
        return f"{float(value):.4g}"

    @staticmethod
    def _plotter_text_color(plotter):
        try:
            bg = plotter.background_color
            return "black" if bg[0] > 0.9 and bg[1] > 0.9 and bg[2] > 0.9 else "white"
        except Exception:
            return "white"

    @staticmethod
    def _remove_3d_colorbar(plotter):
        try:
            plotter.remove_scalar_bar(VisualEngine.COLORBAR_TITLE, render=False)
        except Exception:
            pass

    @staticmethod
    def clear_3d_colorbar(plotter):
        VisualEngine._remove_3d_colorbar(plotter)

    @staticmethod
    def _3d_colorbar_args(plotter):
        return {
            "title": VisualEngine.COLORBAR_TITLE,
            "vertical": True,
            "position_x": 0.88,
            "position_y": 0.08,
            "width": 0.08,
            "height": 0.84,
            "n_labels": 3,
            "fmt": "%.4g",
            "color": VisualEngine._plotter_text_color(plotter),
            "title_font_size": 12,
            "label_font_size": 10,
            "use_opacity": False,
        }

    @staticmethod
    def _apply_3d_colorbar_ticks(plotter, level_info):
        try:
            scalar_bar = plotter.scalar_bars[VisualEngine.COLORBAR_TITLE]
            labels = vtk.vtkDoubleArray()
            for tick in VisualEngine._level_ticks(level_info):
                labels.InsertNextValue(float(tick))
            scalar_bar.SetCustomLabels(labels)
            scalar_bar.SetUseCustomLabels(True)
            scalar_bar.SetNumberOfLabels(labels.GetNumberOfValues())
            scalar_bar.SetLabelFormat("%.4g")
        except Exception:
            pass

    @staticmethod
    def clear_2d_colorbar(ax):
        ax._arpes_image = None
        ax._arpes_render_signature = None
        ax._arpes_preview_background = None
        ax._arpes_preview_background_signature = None
        colorbar = getattr(ax, "_arpes_colorbar", None)
        if colorbar is not None:
            try:
                colorbar.remove()
            except Exception:
                pass
            ax._arpes_colorbar = None
        ax._arpes_colorbar_ax = None

        fig = getattr(ax, "figure", None)
        if fig is None:
            return

        for extra_ax in list(fig.axes):
            if extra_ax is not ax and getattr(extra_ax, "_arpes_colorbar_axis", False):
                try:
                    extra_ax.remove()
                except Exception:
                    pass

        base_position = getattr(ax, "_arpes_base_position", None)
        if base_position is not None:
            try:
                ax.set_position(base_position)
            except Exception:
                pass

    @staticmethod
    def _2d_colorbar_positions(ax):
        fig = getattr(ax, "figure", None)
        base_position = getattr(ax, "_arpes_base_position", None)
        if base_position is None:
            try:
                base_position = ax.get_subplotspec().get_position(fig).frozen()
            except Exception:
                base_position = ax.get_position().frozen()
            ax._arpes_base_position = base_position

        pad = max(0.010, min(0.025, base_position.width * 0.025))
        colorbar_width = max(0.018, min(0.035, base_position.width * 0.045))
        main_width = max(0.10, base_position.width - pad - colorbar_width)
        colorbar_x = base_position.x0 + main_width + pad

        return (
            [base_position.x0, base_position.y0, main_width, base_position.height],
            [colorbar_x, base_position.y0, colorbar_width, base_position.height],
        )

    @staticmethod
    def _add_2d_colorbar(ax, image, level_info):
        fig = getattr(ax, "figure", None)
        if fig is None:
            return

        VisualEngine.clear_2d_colorbar(ax)
        main_position, colorbar_position = VisualEngine._2d_colorbar_positions(ax)
        ax.set_position(main_position)

        colorbar_ax = fig.add_axes(colorbar_position)
        colorbar_ax._arpes_colorbar_axis = True
        colorbar = fig.colorbar(image, cax=colorbar_ax)
        VisualEngine._configure_2d_colorbar(colorbar, level_info)
        ax._arpes_colorbar = colorbar
        ax._arpes_colorbar_ax = colorbar_ax

    @staticmethod
    def _configure_2d_colorbar(colorbar, level_info):
        ticks = VisualEngine._level_ticks(level_info)
        colorbar.set_ticks(ticks)
        colorbar.set_ticklabels([VisualEngine._format_level_tick(tick) for tick in ticks])
        colorbar.set_label(VisualEngine.COLORBAR_TITLE, color="white")
        colorbar.ax.tick_params(colors="white")
        colorbar.ax.yaxis.label.set_color("white")
        for tick_label in colorbar.ax.get_yticklabels():
            tick_label.set_color("white")
        try:
            colorbar.outline.set_edgecolor("#CCCCCC")
        except Exception:
            pass

    @staticmethod
    def _update_2d_colorbar(ax, image, level_info):
        colorbar = getattr(ax, "_arpes_colorbar", None)
        if colorbar is None or getattr(colorbar, "ax", None) is None:
            VisualEngine._add_2d_colorbar(ax, image, level_info)
            return
        try:
            colorbar.update_normal(image)
            VisualEngine._configure_2d_colorbar(colorbar, level_info)
        except Exception:
            VisualEngine._add_2d_colorbar(ax, image, level_info)

    @staticmethod
    def _supports_2d_blit(canvas):
        return bool(
            getattr(canvas, "supports_blit", False)
            and hasattr(canvas, "copy_from_bbox")
            and hasattr(canvas, "restore_region")
            and hasattr(canvas, "blit")
        )

    @staticmethod
    def _ensure_2d_preview_cache(ax, canvas):
        """Keep an axes background for image-only PREVIEW redraws."""
        if getattr(ax, "_arpes_preview_canvas", None) is canvas:
            return

        ax._arpes_preview_canvas = canvas
        ax._arpes_preview_background = None
        ax._arpes_preview_background_signature = None

        def cache_background(_event):
            if not VisualEngine._supports_2d_blit(canvas):
                ax._arpes_preview_background = None
                ax._arpes_preview_background_signature = None
                return
            try:
                ax._arpes_preview_background = canvas.copy_from_bbox(ax.bbox)
                ax._arpes_preview_background_signature = getattr(
                    ax, "_arpes_render_signature", None
                )
            except (AttributeError, RuntimeError):
                ax._arpes_preview_background = None
                ax._arpes_preview_background_signature = None

        def invalidate_background(_event):
            ax._arpes_preview_background = None
            ax._arpes_preview_background_signature = None

        try:
            ax._arpes_preview_draw_cid = canvas.mpl_connect("draw_event", cache_background)
            ax._arpes_preview_resize_cid = canvas.mpl_connect(
                "resize_event", invalidate_background
            )
        except (AttributeError, RuntimeError):
            ax._arpes_preview_canvas = None

    @staticmethod
    def _preview_2d_rgba(preview_img, display_cmap, level_info):
        low = float(level_info["black_value"])
        high = float(level_info["white_value"])
        span = max(high - low, 1e-12)
        normalized = (np.asarray(preview_img) - low) / span
        return np.asarray(display_cmap(normalized, bytes=True), dtype=np.uint8)

    @staticmethod
    def _write_2d_preview_rgba(ax, canvas, preview_rgba):
        """Write a preview directly into the Agg buffer, bypassing imshow."""
        try:
            frame = np.asarray(canvas.buffer_rgba())
            if frame.ndim != 3 or frame.shape[2] != 4 or not frame.flags.writeable:
                return False

            height, width = frame.shape[:2]
            x0 = max(0, int(np.ceil(ax.bbox.x0)))
            x1 = min(width, int(np.floor(ax.bbox.x1)))
            y0 = max(0, height - int(np.floor(ax.bbox.y1)))
            y1 = min(height, height - int(np.ceil(ax.bbox.y0)))
            target_height = y1 - y0
            target_width = x1 - x0
            if target_height <= 0 or target_width <= 0:
                return False

            source = np.asarray(preview_rgba, dtype=np.uint8)
            if source.ndim != 3 or source.shape[2] != 4 or source.size == 0:
                return False

            # imshow(origin="lower") displays row zero at the bottom, while
            # the Agg frame buffer starts at the top.
            scaled = np.asarray(
                Image.fromarray(source[::-1]).resize(
                    (target_width, target_height),
                    resample=Image.Resampling.NEAREST,
                )
            )
            destination = frame[y0:y1, x0:x1]
            alpha = scaled[..., 3]
            if np.all(alpha == 255):
                destination[...] = scaled
            elif np.all((alpha == 0) | (alpha == 255)):
                opaque = alpha == 255
                destination[opaque] = scaled[opaque]
            else:
                opacity = alpha[..., None].astype(np.float32) / 255.0
                destination[..., :3] = (
                    scaled[..., :3] * opacity
                    + destination[..., :3] * (1.0 - opacity)
                ).astype(np.uint8)
                destination[..., 3] = 255
            return True
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False

    @staticmethod
    def _configure_2d_preview_artist(
        image,
        preview_img,
        display_cmap,
        level_info,
        extent,
    ):
        image.set_data(preview_img)
        image.set_cmap(display_cmap)
        image.set_clim(level_info["black_value"], level_info["white_value"])
        image.set_extent(extent)
        image.set_interpolation("nearest")
        image.set_resample(False)

    @staticmethod
    def _blit_2d_preview(
        ax,
        canvas,
        image,
        render_signature,
        preview_img,
        preview_rgba,
        display_cmap,
        level_info,
        extent,
        overlay_artists=(),
    ):
        background = getattr(ax, "_arpes_preview_background", None)
        if (
            not VisualEngine._supports_2d_blit(canvas)
            or background is None
            or getattr(ax, "_arpes_preview_background_signature", None)
            != render_signature
        ):
            VisualEngine._configure_2d_preview_artist(
                image,
                preview_img,
                display_cmap,
                level_info,
                extent,
            )
            canvas.draw_idle()
            return False

        try:
            canvas.restore_region(background)
            used_direct_raster = VisualEngine._write_2d_preview_rgba(
                ax,
                canvas,
                preview_rgba,
            )
            if not used_direct_raster:
                VisualEngine._configure_2d_preview_artist(
                    image,
                    preview_img,
                    display_cmap,
                    level_info,
                    extent,
                )
                ax.draw_artist(image)

            for spine in ax.spines.values():
                ax.draw_artist(spine)
            seen = {id(image)}
            animated_artists = []
            for artist in overlay_artists or ():
                if artist is None or id(artist) in seen:
                    continue
                seen.add(id(artist))
                if (
                    getattr(artist, "axes", None) is ax
                    and artist.get_visible()
                ):
                    if artist.get_animated():
                        animated_artists.append(artist)
                    else:
                        ax.draw_artist(artist)

            # The tooltip and RectangleSelector maintain their own blit
            # backgrounds.  Cache the newly rendered preview before animated
            # overlays are painted so all three paths can share it safely.
            ax._arpes_preview_background = canvas.copy_from_bbox(ax.bbox)
            for artist in animated_artists:
                ax.draw_artist(artist)
            canvas.blit(ax.bbox)
            return True
        except (AttributeError, RuntimeError, TypeError, ValueError):
            ax._arpes_preview_background = None
            ax._arpes_preview_background_signature = None
            VisualEngine._configure_2d_preview_artist(
                image,
                preview_img,
                display_cmap,
                level_info,
                extent,
            )
            canvas.draw_idle()
            return False

    @staticmethod
    def render_axes(plotter, data_shape, coords):
        try:
            # 获取物理范围用于 Title 显示
            xp, yp, zp = coords['X'], coords['Y'], coords['E']

            plotter.remove_bounds_axes()

            # 根据背景色自动调整标尺颜色
            bg = plotter.background_color
            # 优化颜色：如果是深色背景，使用淡紫色/灰色避免纯白太刺眼
            if (bg[0] > 0.9 and bg[1] > 0.9 and bg[2] > 0.9):
                ax_color = 'black'
            else:
                ax_color = '#A0A0B0'  # 浅淡紫灰，匹配深色主题

            actor = plotter.show_bounds(bounds=[0, 200, 0, 200, 0, 200], grid='back', location='outer', ticks='both',
                axes_ranges=[float(np.min(xp)), float(np.max(xp)), float(np.min(yp)), float(np.max(yp)),
                    float(np.min(zp)), float(np.max(zp))], font_size=10, color=ax_color, fmt="%.2f", xtitle="Kx",
                ytitle="Ky", ztitle="E (eV)", render=False)

            actor.SetAxisLabels(0, make_axis_labels(vmin=float(xp[0]), vmax=float(xp[-1]), n=actor.n_xlabels, fmt="%.2f"))
            actor.SetAxisLabels(1, make_axis_labels(vmin=float(yp[0]), vmax=float(yp[-1]), n=actor.n_ylabels, fmt="%.2f"))
            actor.SetAxisLabels(2, make_axis_labels(vmin=float(zp[0]), vmax=float(zp[-1]), n=actor.n_zlabels, fmt="%.2f"))
        except Exception as e:
            print(f"Axes Error: {e}")

    @staticmethod
    def render_2d_slice(
        ax,
        canvas,
        data,
        slice_info,
        levels_params,
        coords,
        cmap="magma",
        *,
        quality="exact",
        overlay_artists=(),
    ):
        try:
            VisualEngine._ensure_2d_preview_cache(ax, canvas)
            b, g, w = levels_params
            xp, yp, zp = coords['X'], coords['Y'], coords['E']

            x_start, x_end = float(xp[0]), float(xp[-1])
            y_start, y_end = float(yp[0]), float(yp[-1])
            e_start, e_end = float(zp[0]), float(zp[-1])

            if slice_info.get("mode") == "integral":
                idx = slice_info["axis"]
                low, up = slice_info["range"]

                if idx == 0:  # X轴积分，横轴 Y，纵轴 E
                    img = data.T
                    ext = [y_start, y_end, e_start, e_end]
                    title = f"X-Integral ({low}~{up})"
                elif idx == 1:  # Y轴积分，横轴 X，纵轴 E
                    img = data.T
                    ext = [x_start, x_end, e_start, e_end]
                    title = f"Y-Integral ({low}~{up})"
                else:  # E轴积分，横轴 X，纵轴 Y
                    img = data.T
                    ext = [x_start, x_end, y_start, y_end]
                    title = f"E-Integral ({low}~{up})"
            else:
                idx = slice_info["axis"]
                index = slice_info["index"]

                if idx == 0:  # X切片，横轴 Y，纵轴 E
                    img = data.T
                    ext = [y_start, y_end, e_start, e_end]
                    title = f"X-Slice ({index})"
                elif idx == 1:  # Y切片，横轴 X，纵轴 E
                    img = data.T
                    ext = [x_start, x_end, e_start, e_end]
                    title = f"Y-Slice ({index})"
                else:  # E切片，横轴 X，纵轴 Y
                    img = data.T
                    ext = [x_start, x_end, y_start, y_end]
                    title = f"E-Slice ({index})"

            # 应用色阶处理
            # E-axis flip is a data-orientation correction.  It never reverses
            # the momentum axis and it does not leave descending 2D ticks.
            if slice_info.get("display_e_flip") and idx in (0, 1):
                img = np.flip(img, axis=0)

            level_info = VisualEngine._level_info(img, (b, g, w))
            cmap_name = getattr(cmap, "name", str(cmap))
            transfer_signature = (
                str(cmap_name),
                float(b),
                float(g),
                float(w),
            )
            title = slice_info.get("title_override", title)
            ext = slice_info.get("extent_override", ext)

            # Matplotlib accepts descending extents, but that makes the lower
            # or left edge show the larger value.  Normalize both axes while
            # flipping the corresponding pixels to preserve data mapping.
            ext = [float(value) for value in ext]
            if ext[0] > ext[1]:
                img = np.flip(img, axis=1)
                ext[0], ext[1] = ext[1], ext[0]
            if ext[2] > ext[3]:
                img = np.flip(img, axis=0)
                ext[2], ext[3] = ext[3], ext[2]

            render_signature = (slice_info.get("mode", "slice"), int(idx), tuple(img.shape))
            image = getattr(ax, "_arpes_image", None)
            can_update = (
                image is not None
                and getattr(ax, "_arpes_render_signature", None) == render_signature
                and getattr(image, "axes", None) is ax
                and image in ax.images
            )
            is_preview = str(getattr(quality, "value", quality)).lower() == "preview"
            same_extent = bool(
                can_update
                and np.allclose(
                    np.asarray(image.get_extent(), dtype=np.float64),
                    np.asarray(ext, dtype=np.float64),
                    rtol=0.0,
                    atol=1e-12,
                )
            )
            if is_preview and can_update and same_extent:
                preview_img = img
                if getattr(image, "_arpes_transfer_signature", None) == transfer_signature:
                    display_cmap = image.get_cmap()
                else:
                    display_cmap = VisualEngine._leveled_cmap(cmap, level_info)
                preview_rgba = VisualEngine._preview_2d_rgba(
                    preview_img,
                    display_cmap,
                    level_info,
                )
                return VisualEngine._blit_2d_preview(
                    ax,
                    canvas,
                    image,
                    render_signature,
                    preview_img,
                    preview_rgba,
                    display_cmap,
                    level_info,
                    ext,
                    overlay_artists=overlay_artists,
                )

            ax._arpes_preview_background = None
            ax._arpes_preview_background_signature = None
            display_cmap = VisualEngine._leveled_cmap(cmap, level_info)
            if can_update:
                image.set_data(img)
                image.set_cmap(display_cmap)
                image.set_clim(level_info["black_value"], level_info["white_value"])
                image.set_extent(ext)
                image.set_interpolation("spline16")
                image.set_resample(True)
                VisualEngine._update_2d_colorbar(ax, image, level_info)
            else:
                VisualEngine.clear_2d_colorbar(ax)
                ax.clear()
                image = ax.imshow(
                    img,
                    cmap=display_cmap,
                    aspect='auto',
                    origin='lower',
                    extent=ext,
                    interpolation='spline16',
                    vmin=level_info["black_value"],
                    vmax=level_info["white_value"],
                )
                VisualEngine._add_2d_colorbar(ax, image, level_info)

            ax._arpes_image = image
            ax._arpes_render_signature = render_signature
            image._arpes_transfer_signature = transfer_signature
            ax.set_xlim(ext[0], ext[1])
            ax.set_ylim(ext[2], ext[3])

            ax.set_title(title, color='white')

            # 额外加固：强制坐标轴刻度显示
            ax.tick_params(colors='white')
            canvas.draw_idle()

        except Exception as e:
            print(f"2D Render Error: {e}")

class VolumeRenderSession:
    """Persistent VTK volume scene backed by retained NumPy buffers."""

    OPACITY_MAPS = {
        "linear": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
        "线性": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
        "log": [0.000, 0.157, 0.249, 0.320, 0.383, 0.441, 0.494, 0.544, 0.591, 0.636, 1.000],
        "对数": [0.000, 0.157, 0.249, 0.320, 0.383, 0.441, 0.494, 0.544, 0.591, 0.636, 1.000],
        "power": [0.000, 0.188, 0.266, 0.327, 0.378, 0.424, 0.467, 0.507, 0.545, 0.583, 1.000],
        "幂函数": [0.000, 0.188, 0.266, 0.327, 0.378, 0.424, 0.467, 0.507, 0.545, 0.583, 1.000],
        "sigmoid": [0.006, 0.018, 0.049, 0.118, 0.268, 0.500, 0.732, 0.882, 0.951, 0.982, 0.994],
    }

    def __init__(self, plotter):
        self.plotter = plotter
        self.grid = None
        self.volume = None
        self.host_buffer = None
        self.vtk_scalars = None
        self.shape = None
        self.data_token = None
        self.level_info = None
        self._style_signature = None
        self._clip_signature = None
        self._axes_signature = None
        self._domain_signature = None
        self.rebuild_count = 0
        self.data_update_count = 0
        self.render_count = 0

    @staticmethod
    def _array_token(data):
        source = np.asarray(data)
        pointer = int(source.__array_interface__["data"][0]) if source.size else 0
        return pointer, tuple(source.shape), tuple(source.strides), source.dtype.str

    @property
    def active(self):
        return self.volume is not None and self.grid is not None

    def clear(self, *, render=False):
        try:
            self.plotter.remove_actor("main_vol", render=False)
        except Exception:
            pass
        VisualEngine.clear_3d_colorbar(self.plotter)
        self.grid = None
        self.volume = None
        self.host_buffer = None
        self.vtk_scalars = None
        self.shape = None
        self.data_token = None
        self.level_info = None
        self._style_signature = None
        self._clip_signature = None
        self._axes_signature = None
        self._domain_signature = None
        if render:
            self.plotter.render()

    def render(
        self,
        data,
        levels_params,
        opac_mode,
        *,
        clip_ranges=None,
        show_axes=True,
        core_coords=None,
        cmap="magma",
        quality="exact",
        force_data=False,
        data_bounds=None,
        full_shape=None,
        include_zero=False,
    ):
        source = np.asarray(data, dtype=np.float32)
        if source.ndim != 3:
            raise ValueError("Persistent volume rendering requires a 3D array.")

        saved_camera = None
        try:
            saved_camera = self.plotter.camera_position
        except Exception:
            pass

        token = self._array_token(source)
        normalized_full_shape = tuple(int(size) for size in (full_shape or source.shape))
        normalized_bounds = (
            tuple(int(value) for value in data_bounds)
            if data_bounds is not None
            else (
                0,
                normalized_full_shape[0] - 1,
                0,
                normalized_full_shape[1] - 1,
                0,
                normalized_full_shape[2] - 1,
            )
        )
        if len(normalized_bounds) != 6:
            raise ValueError("Volume data bounds must contain six inclusive indices.")
        expected_shape = tuple(
            normalized_bounds[2 * axis + 1] - normalized_bounds[2 * axis] + 1
            for axis in range(3)
        )
        if any(
            normalized_bounds[2 * axis] < 0
            or normalized_bounds[2 * axis + 1] < normalized_bounds[2 * axis]
            or normalized_bounds[2 * axis + 1] >= normalized_full_shape[axis]
            for axis in range(3)
        ):
            raise ValueError(
                f"Volume data bounds {normalized_bounds} exceed full shape "
                f"{normalized_full_shape}."
            )
        if tuple(source.shape) != expected_shape:
            raise ValueError(
                f"Volume data shape {source.shape} does not match data bounds "
                f"{normalized_bounds}; expected {expected_shape}."
            )
        domain_signature = (normalized_full_shape, normalized_bounds)
        if (
            not self.active
            or self.shape != tuple(source.shape)
            or self._domain_signature != domain_signature
        ):
            self._build_scene(
                source,
                levels_params,
                opac_mode,
                cmap,
                data_bounds=normalized_bounds,
                full_shape=normalized_full_shape,
                include_zero=bool(include_zero),
            )
        elif force_data or token != self.data_token:
            self._attach_data(source)

        self._update_style(levels_params, opac_mode, cmap, include_zero=bool(include_zero))
        self._update_clipping(clip_ranges)
        self._update_axes(bool(show_axes), core_coords)
        self._set_interactive_quality(quality)

        if saved_camera is not None:
            try:
                self.plotter.camera_position = saved_camera
            except Exception:
                pass
        self.plotter.render()
        self.render_count += 1
        return self.volume

    def _build_scene(
        self,
        data,
        levels_params,
        opac_mode,
        cmap,
        *,
        data_bounds,
        full_shape,
        include_zero=False,
    ):
        self.clear(render=False)
        shape = tuple(int(size) for size in data.shape)
        spacing = tuple(
            200.0 / (int(full_shape[axis]) - 1) if int(full_shape[axis]) > 1 else 1.0
            for axis in range(3)
        )
        # Preserve absolute full-domain voxel indices in VTK itself.  Moving
        # ImageData.origin for every ROI leaves the compact texture zero-based
        # and has produced stale texture/transform behaviour on some integrated
        # GPU drivers after a second crop.  A non-zero extent carries the same
        # compact point count while making the ROI's absolute X/Y/E indices
        # explicit and keeping the dataset origin stable across scope changes.
        self.grid = pv.ImageData()
        self.grid.extent = tuple(int(value) for value in data_bounds)
        self.grid.origin = (0.0, 0.0, 0.0)
        self.grid.spacing = spacing
        if tuple(int(size) for size in self.grid.dimensions) != shape:
            raise ValueError(
                f"VTK extent {data_bounds} produced dimensions "
                f"{self.grid.dimensions}, expected {shape}."
            )
        self._attach_data(data)
        self.level_info = VisualEngine._level_info(
            data,
            levels_params,
            include_zero=include_zero,
        )
        display_cmap = VisualEngine._leveled_cmap(cmap, self.level_info)
        opacity = self._opacity_values(opac_mode, self.level_info)
        self.volume = self.plotter.add_volume(
            self.grid,
            scalars="values",
            cmap=display_cmap,
            opacity=opacity,
            clim=[self.level_info["black_value"], self.level_info["white_value"]],
            show_scalar_bar=True,
            scalar_bar_args=VisualEngine._3d_colorbar_args(self.plotter),
            mapper="smart",
            name="main_vol",
            render=False,
        )
        self.shape = shape
        self._style_signature = None
        self._clip_signature = None
        self._axes_signature = None
        self._domain_signature = (tuple(full_shape), tuple(data_bounds))
        self.rebuild_count += 1

    def _attach_data(self, data):
        buffer = np.asfortranarray(np.asarray(data, dtype=np.float32))
        flat = buffer.ravel(order="F")
        if self.vtk_scalars is None:
            vtk_scalars = numpy_to_vtk(flat, deep=False)
            vtk_scalars.SetName("values")
            self.grid.GetPointData().SetScalars(vtk_scalars)
            self.vtk_scalars = vtk_scalars
        else:
            # add_volume keeps a shallow copy of the ImageData and therefore
            # retains this vtkDataArray object.  Replacing the grid's scalar
            # object leaves the mapper connected to the old array.  Repoint
            # the existing shared array instead so an exact same-shape result
            # immediately invalidates the GPU volume texture.
            self.vtk_scalars.SetVoidArray(flat, int(flat.size), 1)

        self.vtk_scalars.Modified()
        self.grid.GetPointData().Modified()
        self.grid.Modified()
        if self.volume is not None:
            self.volume.mapper.Modified()
        self.host_buffer = buffer
        self.shape = tuple(buffer.shape)
        self.data_token = self._array_token(data)
        self.level_info = None
        self._style_signature = None
        self.data_update_count += 1

    def _update_style(self, levels_params, opac_mode, cmap, *, include_zero=False):
        signature = (
            tuple(float(value) for value in levels_params),
            str(opac_mode),
            str(cmap),
            self.data_token,
            bool(include_zero),
        )
        if signature == self._style_signature:
            return
        self.level_info = VisualEngine._level_info(
            self.host_buffer,
            levels_params,
            include_zero=include_zero,
        )
        display_cmap = VisualEngine._leveled_cmap(cmap, self.level_info)
        colors = display_cmap(np.linspace(0.0, 1.0, 256))
        low = float(self.level_info["black_value"])
        high = float(self.level_info["white_value"])
        if high <= low:
            high = low + 1.0

        color_function = vtk.vtkColorTransferFunction()
        for index, rgba in enumerate(colors):
            value = low + (high - low) * index / max(len(colors) - 1, 1)
            color_function.AddRGBPoint(value, float(rgba[0]), float(rgba[1]), float(rgba[2]))

        opacity_values = self._opacity_values(opac_mode, self.level_info)
        opacity_function = vtk.vtkPiecewiseFunction()
        for index, opacity in enumerate(opacity_values):
            value = low + (high - low) * index / max(len(opacity_values) - 1, 1)
            opacity_function.AddPoint(value, float(opacity))

        prop = self.volume.GetProperty()
        prop.SetColor(color_function)
        prop.SetScalarOpacity(opacity_function)
        try:
            self.volume.mapper.scalar_range = (low, high)
            self.volume.mapper.lookup_table.apply_cmap(display_cmap, n_values=256)
            self.volume.mapper.lookup_table.scalar_range = (low, high)
        except Exception:
            pass
        VisualEngine._apply_3d_colorbar_ticks(self.plotter, self.level_info)
        self._style_signature = signature

    def _opacity_values(self, opac_mode, level_info):
        values = self.OPACITY_MAPS.get(str(opac_mode), self.OPACITY_MAPS["linear"])
        return VisualEngine._mapped_opacity(values, level_info)

    def _update_clipping(self, clip_ranges):
        signature = None if clip_ranges is None else tuple(float(value) for value in clip_ranges)
        if signature == self._clip_signature or self.volume is None:
            return
        mapper = self.volume.mapper
        if signature is None:
            mapper.RemoveAllClippingPlanes()
        else:
            r = signature
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
            mapper.SetClippingPlanes(planes)
        self._clip_signature = signature

    def _update_axes(self, show_axes, coords):
        coord_signature = None
        if show_axes and coords:
            pieces = []
            for key in ("X", "Y", "E"):
                values = np.asarray(coords.get(key, []))
                if values.size == 0:
                    pieces.append((key, 0, 0.0, 0.0))
                else:
                    pieces.append((key, len(values), float(values[0]), float(values[-1])))
            coord_signature = tuple(pieces)
        signature = bool(show_axes), coord_signature
        if signature == self._axes_signature:
            return
        if show_axes and coords:
            VisualEngine.render_axes(self.plotter, self.grid.dimensions, coords)
        else:
            self.plotter.remove_bounds_axes()
        self._axes_signature = signature

    def _set_interactive_quality(self, quality):
        if self.volume is None:
            return
        try:
            self.volume.mapper.SetAutoAdjustSampleDistances(True)
        except Exception:
            pass
        interactor = getattr(self.plotter, "iren", None)
        if interactor is not None:
            try:
                interactor.SetDesiredUpdateRate(15.0 if str(quality) == "preview" else 2.0)
            except Exception:
                pass
