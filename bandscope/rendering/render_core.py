import numpy as np
import vtk
import pyvista as pv
from matplotlib import colormaps, transforms as mtransforms
from matplotlib.colors import ListedColormap
from PIL import Image
from pyvista.plotting.cube_axes_actor import make_axis_labels
from vtkmodules.util.numpy_support import numpy_to_vtk

import bandscope.ui.theme as theme


def build_volume_grid(shape, data_bounds, spacing, *, masked=False):
    """体渲染网格；掩膜路径用零基 extent，ROI 起点写进 origin。

    非零 extent 与 ``SetMaskInput`` 一起使用时，NVIDIA 驱动会在渲染阶段访问
    越界（本机稳定复现：先拖动裁剪、再裁空，nvoglv64.dll 0xc0000005，崩溃点
    就在 ``plotter.render()``）。掩膜路径因此回到零基 extent：世界坐标由
    ``origin + index * spacing`` 给出，与非零 extent 的表示逐个采样点等价，
    只是 VTK 内部贴图不再带起始索引偏移。无掩膜路径保持原有表示，避免重新
    引入旧注释里记录过的整体 origin 偏移问题。
    """
    grid = pv.ImageData()
    grid.spacing = tuple(float(value) for value in spacing)
    if masked:
        grid.extent = tuple(value for size in shape[:3] for value in (0, int(size) - 1))
        grid.origin = tuple(
            float(data_bounds[2 * axis]) * float(spacing[axis]) for axis in range(3)
        )
    else:
        grid.extent = tuple(int(value) for value in data_bounds)
        grid.origin = (0.0, 0.0, 0.0)
    return grid


def configure_volume_mask(volume, grid, data):
    """Hide missing voxels explicitly; NaNs alone are unreliable in VTK textures.

    网格必须已经是零基 extent（见 ``build_volume_grid``）：掩膜结构从网格复制，
    带非零起始索引会让驱动崩溃。
    """
    if not hasattr(volume.mapper, "SetMaskInput"):
        return
    finite = np.isfinite(data)
    if np.all(finite):
        volume.mapper.SetMaskInput(None)
        return
    mask = pv.ImageData()
    mask.CopyStructure(grid)
    mask.point_data["mask"] = finite.astype(np.uint8).ravel(order="F")
    volume.mapper.SetMaskTypeToBinary()
    volume.mapper.SetMaskInput(mask)


# ---------------------------------------------------------------------------
# 空间透明度通道
#
# VTK 的体渲染按“标量值 → 不透明度”查表，同一强度在任何能量处必然得到相同
# 透明度。要让透明度还能随能量变化，需要第二个分量：vtkVolumeProperty 的
# IndependentComponents 关闭时，二分量数据的第一个分量进颜色传递函数、第二个
# 分量进标量不透明度传递函数（见 vtkVolumeProperty::SetIndependentComponents
# 文档）。因此宿主把“最终不透明度”写进第二个分量，颜色仍由第一个分量决定，
# 原始强度数组保持逐位不变。
#
# 注意本版本 VTK 的 IndependentComponents 默认为 On：单分量体数据必须保持 On，
# 二分量通道必须显式关掉，否则每个分量会各自查表。
# ---------------------------------------------------------------------------


def opacity_ramp_points(opac_mode, level_info):
    """现有“强度 → 不透明度”折线的采样点，供一维查表和逐体素求值共用。"""
    values = VolumeRenderSession.OPACITY_MAPS.get(
        str(opac_mode), VolumeRenderSession.OPACITY_MAPS["linear"]
    )
    opacities = np.asarray(
        VisualEngine._mapped_opacity(values, level_info), dtype=np.float64
    )
    low = float(level_info["black_value"])
    high = float(level_info["white_value"])
    if high <= low:
        high = low + 1.0
    divisor = max(len(opacities) - 1, 1)
    sample_values = low + (high - low) * np.arange(len(opacities)) / divisor
    return sample_values, opacities


def normalize_opacity_multiplier(multiplier, energy_length):
    """把插件给的倍率规整成与能量轴等长、非负、有限的一维数组。"""
    if multiplier is None:
        return None
    values = np.asarray(multiplier, dtype=np.float64).reshape(-1)
    if values.size != int(energy_length):
        raise ValueError(
            f"Opacity multiplier length {values.size} does not match the "
            f"energy axis length {int(energy_length)}."
        )
    if not np.all(np.isfinite(values)):
        values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
    return np.maximum(values, 0.0).astype(np.float32)


def identity_opacity_function():
    """二分量模式下的不透明度传递函数：第二个分量本身就是最终 alpha。

    必须先于查色表操作之外的顺序调用：PyVista 的 ``apply_cmap`` 会用查色表重建
    体属性的不透明度折线，先设会被覆盖回“强度 → 不透明度”。
    """
    function = vtk.vtkPiecewiseFunction()
    function.AddPoint(0.0, 0.0)
    function.AddPoint(1.0, 1.0)
    return function


def attach_volume_alpha(volume, grid, source, multiplier, opac_mode, level_info):
    """给任意体渲染 actor 装上独立 alpha 通道；返回通道对象（调用方需持有）。

    主视图与离屏导出共用这一条路径，保证屏幕和图片采用同一 alpha 语义。
    """
    channel = VolumeAlphaChannel(volume, grid, source)
    channel.apply(multiplier, opac_mode, level_info, ("volume-alpha",))
    volume.GetProperty().SetScalarOpacity(identity_opacity_function())
    return channel


class VolumeAlphaChannel:
    """独立透明度通道：二分量缓冲区 + VTK 数组 + 与单分量网格的切换。

    生命周期由宿主控制；插件只提供一维倍率，三维 alpha 由宿主一次性生成，
    不随插件条目数量增长。缓冲区复用，参数变化时只改写第二分量。

    额外内存 = 8 字节/体素（intensity + alpha 各 float32）。这是本功能唯一按
    体素规模增长的缓冲区：单分量渲染时为 4 字节/体素，开启插件后翻倍。副本不
    入 ROI/去噪缓存，随场景一起释放。
    """

    def __init__(self, volume, grid, source, finite_flat=None):
        self.volume = volume
        self.grid = grid
        self.finite_flat = finite_flat
        plane = int(grid.dimensions[0]) * int(grid.dimensions[1])
        self._plane = max(plane, 1)
        self.buffer = np.empty((int(source.size), 2), dtype=np.float32)
        self.buffer[:, 0] = self._as_flat(source)
        self.buffer[:, 1] = 0.0
        self.vtk_array = numpy_to_vtk(self.buffer, deep=False)
        self.vtk_array.SetName("values")
        self.grid_alpha = pv.ImageData()
        self.grid_alpha.CopyStructure(grid)
        self.grid_alpha.GetPointData().SetScalars(self.vtk_array)
        self.engine = None
        self.applied = None

    @staticmethod
    def _as_flat(source):
        """VTK 点序是 x 最快，三维缓冲必须按 Fortran 序展开；一维输入原样通过。"""
        values = np.asarray(source)
        return values.ravel(order="F") if values.ndim > 1 else values

    @property
    def enabled(self):
        return self.engine is not None

    def attach(self):
        """让 mapper 使用二分量网格，并关闭 IndependentComponents。"""
        self.volume.mapper.dataset = self.grid_alpha
        self.volume.GetProperty().SetIndependentComponents(False)
        self.volume.mapper.Modified()
        self.engine = True
        self.applied = None

    def detach(self):
        if self.engine is None:
            return
        self.volume.mapper.dataset = self.grid
        self.volume.GetProperty().SetIndependentComponents(True)
        self.volume.mapper.Modified()
        self.engine = None
        self.applied = None

    def refresh_intensity(self, source, finite_flat=None):
        """数据更新后重写第一分量与缺失掩膜；原始强度始终逐位复制，不参与计算。"""
        self.buffer[:, 0] = self._as_flat(source)
        self.finite_flat = finite_flat
        self.vtk_array.Modified()
        self.applied = None

    ALPHA_CHUNK_VOXELS = 2_000_000

    def apply(self, multiplier, opac_mode, level_info, state_prefix):
        """写入最终 alpha：alpha = clip(alpha_base(I) * m(E), 0, 1)。

        缓冲区按 x 最快展开，因此可视图为 (E, kx*ky, component)：能量索引是慢
        轴，倍率沿慢轴广播，逐体素只算一次一维查表。np.interp 没有 out 参数，
        按能量分块计算，把临时数组限制在块大小内。
        """
        if (
            self.applied is not None
            and self.applied[0] == state_prefix
            and np.array_equal(self.applied[1], multiplier)
        ):
            return
        values = np.asarray(multiplier, dtype=np.float64).reshape(-1)
        energy_count = int(self.buffer.shape[0]) // self._plane
        if values.size != energy_count:
            # 长度不符必须显式失败：广播会把过长的倍率静默截断，画出一张看起来
            # 正常、实际错位的图。
            raise ValueError(
                f"Opacity multiplier length {values.size} does not match the "
                f"volume energy axis {energy_count}."
            )
        self.attach()
        ramp_values, ramp_opacities = opacity_ramp_points(opac_mode, level_info)
        view = self.buffer.reshape(energy_count, self._plane, 2)
        gain = values[:, None]
        finite = (
            self.finite_flat.reshape(energy_count, self._plane)
            if self.finite_flat is not None
            else None
        )
        chunk = max(1, self.ALPHA_CHUNK_VOXELS // max(self._plane, 1))
        for start in range(0, energy_count, chunk):
            stop = min(energy_count, start + chunk)
            alpha = np.interp(
                view[start:stop, :, 0].reshape(-1), ramp_values, ramp_opacities
            )
            alpha *= np.broadcast_to(gain[start:stop], (stop - start, self._plane)).reshape(-1)
            np.clip(alpha, 0.0, 1.0, out=alpha)
            block = alpha.reshape(stop - start, self._plane)
            if finite is not None:
                block[~finite[start:stop]] = 0.0
            view[start:stop, :, 1] = block
        self.vtk_array.Modified()
        self.volume.mapper.Modified()
        self.applied = (state_prefix, values)


class VisualEngine:
    """渲染与绘图引擎，负责所有 3D 和 2D 的视觉呈现"""

    COLORBAR_TITLE = "Intensity"

    # 色带锁定状态。_last_data_range 记录最近一次渲染实际使用的数据范围
    # (data_min, data_max)；_locked_data_range 非空时，_level_info 不再按
    # 当前帧数据重新归一化，而是沿用锁定时刻捕获的范围分配颜色。
    _last_data_range = None
    _locked_data_range = None

    @classmethod
    def lock_data_range(cls):
        """按当前色带冻结数据范围；返回是否捕获到已渲染数据的范围。"""
        if cls._last_data_range is None:
            return False
        cls._locked_data_range = (
            float(cls._last_data_range[0]),
            float(cls._last_data_range[1]),
        )
        return True

    @classmethod
    def unlock_data_range(cls):
        """解除色带锁定，恢复按每帧数据自动归一化。"""
        cls._locked_data_range = None

    @classmethod
    def locked_data_range(cls):
        """当前锁定的数据范围；未锁定时为 None。"""
        return cls._locked_data_range

    @staticmethod
    def _level_info(data, levels_params, include_zero=False):
        black, gamma, white = levels_params
        locked = VisualEngine._locked_data_range
        if locked is not None:
            d_min, d_max = float(locked[0]), float(locked[1])
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
            VisualEngine._last_data_range = (d_min, d_max)

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
        # 等比显示属于 2D 图像自身的状态：图像撤销后必须恢复自由比例，否则
        # 1D 曲线和空态画幅会继续被锁成方形。
        try:
            ax.set_aspect("auto")
        except Exception:
            pass
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

        for extra_ax in list(fig.axes) + list(getattr(ax, "child_axes", ())):
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

    # 色条是图像主轴的子坐标系（比例相对图像宽度）。等比显示时 matplotlib 会
    # 把图像收进画幅并在两侧留白，子坐标系会跟着图像一起收缩，因此色条始终紧贴
    # 图像右缘、与图像等高，窗口缩放时不需要任何重新布局。
    COLORBAR_INSET_PAD = 0.020
    COLORBAR_INSET_WIDTH = 0.050

    @staticmethod
    def _2d_image_position(ax):
        """2D 图像主轴的位置（图幅比例），右侧预留出色条所在的空间。"""
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

        return [base_position.x0, base_position.y0, main_width, base_position.height]

    @staticmethod
    def _add_2d_colorbar(ax, image, level_info):
        fig = getattr(ax, "figure", None)
        if fig is None:
            return

        VisualEngine.clear_2d_colorbar(ax)
        ax.set_position(VisualEngine._2d_image_position(ax))

        colorbar_ax = ax.inset_axes(
            [
                1.0 + VisualEngine.COLORBAR_INSET_PAD,
                0.0,
                VisualEngine.COLORBAR_INSET_WIDTH,
                1.0,
            ]
        )
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
        colorbar.set_label(VisualEngine.COLORBAR_TITLE, color=theme.TEXT_1)
        colorbar.ax.tick_params(colors=theme.TEXT_2)
        colorbar.ax.yaxis.label.set_color(theme.TEXT_1)
        for tick_label in colorbar.ax.get_yticklabels():
            tick_label.set_color(theme.TEXT_2)
        try:
            colorbar.outline.set_edgecolor(theme.BORDER_HEX)
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

    #: 3D 三条轴名的既有默认文字（用户没有改名时使用）。
    DEFAULT_3D_AXIS_TITLES = ("Kx", "Ky", "E (eV)")

    @staticmethod
    def render_axes(plotter, data_shape, coords, axis_titles=None):
        """重画 3D 坐标轴与刻度；``axis_titles`` 为 X/Y/E 三条轴名。

        传 None 时沿用既有默认文字，保持旧调用与既有截图不变。
        """
        titles = list(VisualEngine.DEFAULT_3D_AXIS_TITLES)
        if axis_titles is not None:
            titles = [str(title or "") for title in axis_titles][:3] + [""] * 3
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
                ax_color = theme.TEXT_2  # 深底上的坐标轴文字

            actor = plotter.show_bounds(bounds=[0, 200, 0, 200, 0, 200], grid='back', location='outer', ticks='both',
                axes_ranges=[float(np.min(xp)), float(np.max(xp)), float(np.min(yp)), float(np.max(yp)),
                    float(np.min(zp)), float(np.max(zp))], font_size=10, color=ax_color, fmt="%.2f", xtitle=titles[0],
                ytitle=titles[1], ztitle=titles[2], render=False)

            actor.SetAxisLabels(0, make_axis_labels(vmin=float(xp[0]), vmax=float(xp[-1]), n=actor.n_xlabels, fmt="%.2f"))
            actor.SetAxisLabels(1, make_axis_labels(vmin=float(yp[0]), vmax=float(yp[-1]), n=actor.n_ylabels, fmt="%.2f"))
            actor.SetAxisLabels(2, make_axis_labels(vmin=float(zp[0]), vmax=float(zp[-1]), n=actor.n_zlabels, fmt="%.2f"))
        except Exception as e:
            print(f"Axes Error: {e}")

    #: mpl 的 XAxis/YAxis 只在 ``_autolabelpos`` 为真时自动排布标签（见
    #: ``XAxis._init`` / ``YAxis._init``）：默认位置与该标志一并恢复，
    #: 用户拖过位置后由 ``set_label_coords`` 关掉它。
    _2D_AUTO_LABEL_POSITIONS = {"x": (0.5, 0.0), "y": (0.0, 0.5)}

    @staticmethod
    def _reset_2d_axis_label(ax, axis_key):
        """恢复 matplotlib 的自动标签位置。"""
        axis = ax.xaxis if axis_key == "x" else ax.yaxis
        if axis_key == "x":
            transform = mtransforms.blended_transform_factory(
                ax.transAxes, mtransforms.IdentityTransform()
            )
        else:
            transform = mtransforms.blended_transform_factory(
                mtransforms.IdentityTransform(), ax.transAxes
            )
        axis.label.set_transform(transform)
        axis.label.set_position(VisualEngine._2D_AUTO_LABEL_POSITIONS[axis_key])
        axis._autolabelpos = True

    @staticmethod
    def reset_2d_axis_labels(ax):
        """把横纵轴标签交还给 matplotlib 的自动排布。

        ``ax_2d`` 是 2D 图像与 1D / 瀑布 / 对比页共用的坐标系：2D 页拖过轴标题
        后 ``_autolabelpos`` 是关着的，1D 页渲染前不清回来，标签会停在画布外。
        """
        for axis_key in ("x", "y"):
            VisualEngine._reset_2d_axis_label(ax, axis_key)

    @staticmethod
    def _apply_2d_axis_titles(ax, titles, positions=None):
        """写入横纵轴标题；``positions`` 是拖动后的相对绘图区坐标。"""
        positions = positions or {}
        for axis_key in ("x", "y"):
            axis = ax.xaxis if axis_key == "x" else ax.yaxis
            axis.set_label_text(
                str(titles.get(axis_key) or ""),
                color="white",
                fontfamily=theme.MPL_FONT_FAMILIES,
            )
            position = positions.get(axis_key)
            if position is None:
                VisualEngine._reset_2d_axis_label(ax, axis_key)
            else:
                axis.set_label_coords(float(position[0]), float(position[1]))

    @staticmethod
    def _2d_title_signature(axis_titles):
        """轴标题文字签名；标题变化会让快速刷新缓存的底图失效。"""
        if axis_titles is None:
            return None
        return tuple(str(axis_titles.get(axis_key) or "") for axis_key in ("x", "y"))

    @staticmethod
    def _2d_uses_equal_aspect(slice_info):
        """沿能量轴切片/积分得到的图像，两轴同为动量，按物理坐标等比显示。

        kx–ky 面上单位相同，1:1 才是真形：动量空间的圆保持为圆。含能量轴的
        图像横纵单位不同，等比没有物理意义，继续铺满画布。
        """
        try:
            return int(slice_info.get("axis", -1)) == 2
        except (TypeError, ValueError):
            return False

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
        axis_titles=None,
        axis_title_positions=None,
    ):
        """画 2D 图像；``axis_titles`` 是 ``{"x": ..., "y": ...}`` 轴标题。

        标题为空串表示隐藏该轴；``axis_title_positions`` 给的是相对绘图区坐标
        （用户拖过的位置），缺省时恢复 matplotlib 的自动排布。``axis_titles``
        为 None 表示这次渲染不管理轴标题（标签按重建路径的 ``ax.clear()`` 走）。
        标题属于画面的一部分却不落在 ``ax.bbox`` 内，因此它进了渲染签名：
        文字一变就让快速刷新的底图作废。
        """
        try:
            VisualEngine._ensure_2d_preview_cache(ax, canvas)
            b, g, w = levels_params
            xp, yp, zp = coords['X'], coords['Y'], coords['E']

            x_start, x_end = float(xp[0]), float(xp[-1])
            y_start, y_end = float(yp[0]), float(yp[-1])
            e_start, e_end = float(zp[0]), float(zp[-1])

            idx = int(slice_info["axis"])
            axis_views = {
                0: ("X", [y_start, y_end, e_start, e_end]),
                1: ("Y", [x_start, x_end, e_start, e_end]),
                2: ("E", [x_start, x_end, y_start, y_end]),
            }
            axis_label, ext = axis_views.get(idx, axis_views[2])
            img = data.T
            if slice_info.get("mode") == "integral":
                low, up = slice_info["range"]
                title = f"{axis_label}-Integral ({low}~{up})"
            else:
                title = f"{axis_label}-Slice ({slice_info['index']})"

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
                VisualEngine._locked_data_range,
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

            # 轴标题属于画面的一部分，但不是 ax.bbox 内的像素：标题一变就必须
            # 走整帧重绘，否则快速刷新会从旧底图里 blit 出过期的标签。
            render_signature = (
                slice_info.get("mode", "slice"),
                int(idx),
                tuple(img.shape),
                VisualEngine._2d_title_signature(axis_titles),
            )
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

            # 图像比例只能由数据决定，不能跟随窗口宽高比：等比时 matplotlib 在
            # 每次绘制时把画幅收进给定的位置并按需留白，窗口缩放不需要重新布局，
            # 图像也永远不会被拉扁。
            if VisualEngine._2d_uses_equal_aspect(slice_info):
                ax.set_aspect("equal", adjustable="box", anchor="C")
            else:
                ax.set_aspect("auto")

            ax.set_title(title, color='white')

            # 额外加固：强制坐标轴刻度显示
            ax.tick_params(colors='white')
            if axis_titles is not None:
                VisualEngine._apply_2d_axis_titles(
                    ax, axis_titles, axis_title_positions
                )
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
        self.level_data = None
        self.has_mask = False
        self.vtk_scalars = None
        self.shape = None
        self.data_token = None
        self.level_info = None
        self._style_signature = None
        self._clip_signature = None
        self._axes_signature = None
        self._domain_signature = None
        self.opacity_multiplier = None
        self.alpha_channel = None
        self.finite_flat = None
        self._alpha_state = None
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
        self.level_data = None
        self.has_mask = False
        self.shape = None
        self.data_token = None
        self.level_info = None
        self._style_signature = None
        self._clip_signature = None
        self._axes_signature = None
        self._domain_signature = None
        self.alpha_channel = None
        self.finite_flat = None
        self._alpha_state = None
        if render:
            self.plotter.render()

    def set_opacity_multiplier(self, multiplier, energy_length):
        """设置能量方向的一维透明度倍率；None 表示回到原有单分量渲染。"""
        if multiplier is None:
            self.opacity_multiplier = None
            self._release_alpha_channel()
            return
        self.opacity_multiplier = normalize_opacity_multiplier(multiplier, energy_length)

    def _release_alpha_channel(self):
        """丢弃独立 alpha 通道并让 mapper 回到单分量颜色传递函数。"""
        self._alpha_state = None
        channel = self.alpha_channel
        self.alpha_channel = None
        if channel is None:
            return
        channel.detach()
        # 不透明度传递函数要跟着切回“强度 → 不透明度”，否则会停留在恒等映射上。
        self._style_signature = None

    def _ensure_alpha_channel(self):
        """按当前倍率维护二分量通道；返回通道是否可用。"""
        if self.opacity_multiplier is None or self.volume is None or self.grid is None:
            self._release_alpha_channel()
            return False
        channel = self.alpha_channel
        if channel is not None and channel.buffer.shape[0] != int(self.host_buffer.size):
            channel.detach()
            channel = None
            self.alpha_channel = None
            self._style_signature = None
        if channel is None:
            self.alpha_channel = VolumeAlphaChannel(
                self.volume, self.grid, self.host_buffer, self.finite_flat
            )
            self.alpha_channel.attach()
            self._style_signature = None
        return True

    def _attach_alpha_channel(self):
        """建好/切换到独立 alpha 通道。

        必须在 ``_update_style`` **之前**做：不透明度传递函数的含义随分量数改变。
        失败时退回单分量渲染——视图打不开比效果没上严重得多。
        """
        if self.opacity_multiplier is None:
            self._release_alpha_channel()
            return
        try:
            self._ensure_alpha_channel()
        except Exception as exc:
            print(f"Alpha channel unavailable: {exc}")
            self._drop_alpha_effect()

    def _fill_alpha_channel(self, levels_params, opac_mode, include_zero=False):
        """填写 alpha 值。

        必须在 ``_update_style`` **之后**做：逐体素的基础不透明度来自它算出的
        ``level_info``。
        """
        if self.opacity_multiplier is None or self.alpha_channel is None:
            return
        try:
            self._update_alpha_channel(levels_params, opac_mode, include_zero)
        except Exception as exc:
            print(f"Alpha channel update failed: {exc}")
            self._drop_alpha_effect()

    def _drop_alpha_effect(self):
        """放弃插件效果，回到原有单分量渲染路径。"""
        self.opacity_multiplier = None
        self._style_signature = None
        try:
            self._release_alpha_channel()
        except Exception:
            self.alpha_channel = None
            self._alpha_state = None

    def _update_alpha_channel(self, levels_params, opac_mode, include_zero=False):
        if self.opacity_multiplier is None or self.alpha_channel is None:
            return
        state_key = (
            tuple(float(value) for value in levels_params),
            str(opac_mode),
            bool(include_zero),
            VisualEngine._locked_data_range,
            self.data_token,
        )
        self.alpha_channel.apply(
            self.opacity_multiplier, opac_mode, self.level_info, state_key
        )
        self._alpha_state = state_key

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
        opacity_multiplier=None,
        axis_titles=None,
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
        has_mask = (not np.all(np.isfinite(source))
                    if force_data or token != self.data_token else self.has_mask)
        if (
            not self.active
            or self.shape != tuple(source.shape)
            or self._domain_signature != domain_signature
            or self.has_mask != has_mask
        ):
            self._build_scene(
                source,
                levels_params,
                opac_mode,
                cmap,
                data_bounds=normalized_bounds,
                full_shape=normalized_full_shape,
                include_zero=bool(include_zero),
                masked=has_mask,
            )
        elif force_data or token != self.data_token:
            self._attach_data(source)

        # 倍率与数据同行，长度按数据的能量轴校验；None 表示不使用插件效果。
        self.set_opacity_multiplier(opacity_multiplier, int(source.shape[2]))
        # 先建通道、再定样式、最后填值：三步的顺序都不能换，见各自文档。
        self._attach_alpha_channel()
        self._update_style(levels_params, opac_mode, cmap, include_zero=bool(include_zero))
        self._fill_alpha_channel(levels_params, opac_mode, bool(include_zero))
        self._update_clipping(clip_ranges)
        self._update_axes(bool(show_axes), core_coords, axis_titles)
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
        masked=False,
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
        # 掩膜（裁空留下的 NaN 空缺）是例外：它必须零基，见 build_volume_grid。
        self.grid = build_volume_grid(shape, data_bounds, spacing, masked=masked)
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
            mapper="gpu" if self.has_mask else "smart",
            name="main_vol",
            render=False,
        )
        configure_volume_mask(self.volume, self.grid, data)
        self.shape = shape
        self._style_signature = None
        self._clip_signature = None
        self._axes_signature = None
        self._domain_signature = (tuple(full_shape), tuple(data_bounds))
        self.rebuild_count += 1

    def _attach_data(self, data):
        self.level_data = np.asarray(data, dtype=np.float32)
        self.has_mask = not np.all(np.isfinite(self.level_data))
        buffer = np.asfortranarray(np.nan_to_num(self.level_data) if self.has_mask else self.level_data)
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
            configure_volume_mask(self.volume, self.grid, data)
            self.volume.mapper.Modified()
        self.host_buffer = buffer
        self.finite_flat = (
            np.isfinite(self.level_data).ravel(order="F") if self.has_mask else None
        )
        self.shape = tuple(buffer.shape)
        self.data_token = self._array_token(data)
        self.level_info = None
        self._style_signature = None
        self._alpha_state = None
        if self.alpha_channel is not None:
            if self.alpha_channel.buffer.shape[0] == int(buffer.size):
                # 二分量网格与单分量网格共享结构；数据更新时只重写强度分量，
                # alpha 分量由 _update_alpha_channel 按当前参数补齐。
                # 掩膜必须一起换：同一形状、不同帧的缺失区域可以不一样，沿用
                # 旧掩膜会把新帧里已经有效的体素继续压成透明。
                self.alpha_channel.refresh_intensity(flat, self.finite_flat)
            else:
                self._release_alpha_channel()
        self.data_update_count += 1

    def _update_style(self, levels_params, opac_mode, cmap, *, include_zero=False):
        # 二分量模式下第二个分量已经是最终 alpha，不透明度传递函数必须是恒等
        # 映射；否则 alpha 会再被“强度 → 不透明度”折线按错误的定义域查一次表。
        alpha_active = self.alpha_channel is not None
        signature = (
            tuple(float(value) for value in levels_params),
            str(opac_mode),
            str(cmap),
            self.data_token,
            bool(include_zero),
            VisualEngine._locked_data_range,
            alpha_active,
        )
        if signature == self._style_signature:
            return
        self.level_info = VisualEngine._level_info(
            self.level_data,
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

        if alpha_active:
            opacity_function = identity_opacity_function()
        else:
            opacity_function = vtk.vtkPiecewiseFunction()
            opacity_values = self._opacity_values(opac_mode, self.level_info)
            for index, opacity in enumerate(opacity_values):
                value = low + (high - low) * index / max(len(opacity_values) - 1, 1)
                opacity_function.AddPoint(value, float(opacity))

        prop = self.volume.GetProperty()
        prop.SetColor(color_function)
        try:
            self.volume.mapper.scalar_range = (low, high)
            self.volume.mapper.lookup_table.apply_cmap(display_cmap, n_values=256)
            self.volume.mapper.lookup_table.scalar_range = (low, high)
        except Exception:
            pass
        # 必须放在查色表操作之后：PyVista 的 apply_cmap 会用查色表重建体属性的
        # 不透明度折线，先设会被覆盖回 256 点的强度映射。
        prop.SetScalarOpacity(opacity_function)
        VisualEngine._apply_3d_colorbar_ticks(self.plotter, self.level_info)
        self._style_signature = signature

    def _opacity_values(self, opac_mode, level_info):
        # 与 VolumeAlphaChannel 共用同一条折线：单分量查表和逐体素 alpha 必须
        # 得到完全相同的“强度 → 基础不透明度”，否则开关插件会改变颜色外观。
        return opacity_ramp_points(opac_mode, level_info)[1].tolist()

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

    def _update_axes(self, show_axes, coords, axis_titles=None):
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
        # 轴名要进签名：坐标没变但改了名时同样需要重画。隐藏状态下也记名字，
        # 这样先改名、后打开坐标轴开关能直接生效。
        title_signature = (
            None
            if axis_titles is None
            else tuple(str(title or "") for title in axis_titles)
        )
        signature = bool(show_axes), coord_signature, title_signature
        if signature == self._axes_signature:
            return
        if show_axes and coords:
            VisualEngine.render_axes(
                self.plotter, self.grid.dimensions, coords, axis_titles=axis_titles
            )
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
