import numpy as np
import vtk
from PyQt5.QtGui import QColor
import pyvista as pv


class VisualEngine:
    """渲染与绘图引擎，负责所有 3D 和 2D 的视觉呈现"""

    @staticmethod
    def apply_levels(data, black, gamma, white):
        """
        线性色阶处理
        black, gamma, white 均为 0-100 的整数
        """
        # 归一化到 0-1
        d_min, d_max = data.min(), data.max()
        if d_max <= d_min: return data
        img = (data - d_min) / (d_max - d_min)

        # 黑白场拉伸
        b = black / 100.0
        w = white / 100.0
        if w <= b: w = b + 0.01
        img = (img - b) / (w - b)
        img = np.clip(img, 0, 1)

        # Gamma 矫正 (灰场)
        # 50 对应 gamma=1.0, 越小图像越亮, 越大图像越暗
        gamma_val = np.power(10, (50 - gamma) / 50.0)
        img = np.power(img, gamma_val)

        return img

    @staticmethod
    def render_3d(plotter, data, levels_params, opac_mode, clip_ranges=None, show_axes=True, core_coords=None):
        try:
            b, g, w = levels_params

            # 1. 记录相机位置
            try:
                saved_cam = plotter.camera_position
            except:
                saved_cam = None

            # 2. 应用色阶
            processed_data = VisualEngine.apply_levels(data, b, g, w)

            # 3. 设置透明度
            opac_dict = {"线性": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
                "对数": [0.000, 0.157, 0.249, 0.320, 0.383, 0.441, 0.494, 0.544, 0.591, 0.636, 1.000],
                "幂函数": [0.000, 0.188, 0.266, 0.327, 0.378, 0.424, 0.467, 0.507, 0.545, 0.583, 1.000],
                "sigmoid": [0.006, 0.018, 0.049, 0.118, 0.268, 0.500, 0.732, 0.882, 0.951, 0.982, 0.994]}
            selected_opac = opac_dict.get(opac_mode, opac_dict["线性"])

            # --- 【核心修改】：重新定义数据的空间几何 ---
            sh = data.shape
            target_size = 200.0

            # 计算步长，强制全轴填满 200 单位
            dx = target_size / (sh[0] - 1) if sh[0] > 1 else 1.0
            dy = target_size / (sh[1] - 1) if sh[1] > 1 else 1.0
            dz = target_size / (sh[2] - 1) if sh[2] > 1 else 1.0

            # 创建网格并强制赋予 0-200 的坐标系
            if hasattr(pv, 'ImageData'):
                grid = pv.ImageData()
            else:
                grid = pv.UniformGrid()

            grid.dimensions = np.array(sh)
            grid.origin = (0, 0, 0)
            grid.spacing = (dx, dy, dz)
            grid.point_data["values"] = processed_data.flatten(order="F")

            # 4. 添加体渲染
            vol = plotter.add_volume(grid, cmap="magma", opacity=selected_opac, clim=[0, 1], show_scalar_bar=False,
                name="main_vol", render=False)

            # 5. 处理切片限制 (Clipping Planes)
            # 注意：此处的 clip_ranges 输入必须已经是 0-200 的值
            if clip_ranges:
                r = clip_ranges
                planes = vtk.vtkPlaneCollection()
                specs = [((r[0], 0, 0), (1, 0, 0)), ((r[1], 0, 0), (-1, 0, 0)), ((0, r[2], 0), (0, 1, 0)),
                         ((0, r[3], 0), (0, -1, 0)), ((0, 0, r[4]), (0, 0, 1)), ((0, 0, r[5]), (0, 0, -1))]
                for o, n in specs:
                    p = vtk.vtkPlane()
                    p.SetOrigin(o)
                    p.SetNormal(n)
                    planes.AddItem(p)
                vol.mapper.SetClippingPlanes(planes)
            else:
                vol.mapper.RemoveAllClippingPlanes()

            # 6. 处理标尺 (强制 0-200 逻辑)
            if show_axes and core_coords:
                VisualEngine.render_axes(plotter, grid.dimensions, core_coords)
            else:
                plotter.remove_bounds_axes()

            # 7. 恢复相机并渲染
            if saved_cam:
                plotter.camera_position = saved_cam
            plotter.render()

        except Exception as e:
            print(f"3D Render Error: {e}")

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

            # --- 【核心修改】：强制 Bounds 为 0-200 ---
            plotter.show_bounds(bounds=[0, 200, 0, 200, 0, 200], grid='back', location='outer', ticks='both',
                font_size=10, color=ax_color, # 标题依然显示物理范围，但刻度数字会是 0, 50, 100, 150, 200
                xtitle=f"Kx Index ({xp[0]:.2f}~{xp[-1]:.2f})", ytitle=f"Ky Index ({yp[0]:.2f}~{yp[-1]:.2f})",
                ztitle=f"E Index ({zp[0]:.2f}~{zp[-1]:.2f} eV)", render=False)
        except Exception as e:
            print(f"Axes Error: {e}")

    @staticmethod
    def render_2d_slice(ax, canvas, data, slice_info, levels_params, coords):
        try:
            b, g, w = levels_params
            xp, yp, zp = coords['X'], coords['Y'], coords['E']

            # 判断是普通切片还是积分投影
            if slice_info.get("mode") == "integral":
                # 这里的 data 已经是 AnalyzerCore 积分完传进来的 2D 矩阵了
                idx = slice_info["axis"]
                low, up = slice_info["range"]

                if idx == 0:  # X轴积分，剩下 Y-E 面
                    img, ext, title = data.T, [yp[0], yp[-1], zp[0], zp[-1]], f"X-Integral ({low}~{up})"
                elif idx == 1:  # Y轴积分，剩下 X-E 面
                    img, ext, title = data.T, [xp[0], xp[-1], zp[0], zp[-1]], f"Y-Integral ({low}~{up})"
                else:  # Z轴积分，剩下 X-Y 面
                    img, ext, title = data.T, [xp[0], xp[-1], yp[0], yp[-1]], f"E-Integral ({low}~{up})"
            else:
                # 原有的普通切片逻辑... (略)
                pass

            processed_slice = VisualEngine.apply_levels(img, b, g, w)
            ax.clear()
            ax.imshow(processed_slice, cmap="magma", aspect='auto', origin='lower', extent=ext,
                      interpolation='spline16')

            text_color = 'white'  # 强制白色适配暗色主题
            ax.set_title(title, color=text_color)
            canvas.draw()
        except Exception as e:
            print(f"2D Render Error: {e}")

    @staticmethod
    def render_integral_dynamics(ax, canvas, x_data, y_data):
        """绘制 Page3 需要的积分动力学曲线"""
        try:
            ax.clear()
            ax.plot(x_data, y_data, color='#FF69B4', linewidth=2, marker='o', markersize=4)

            text_color = 'black' if ax.get_facecolor() == (1, 1, 1, 1) else 'white'
            ax.set_title("Integrated Intensity Dynamics", color=text_color, fontsize=12)
            ax.set_xlabel("Delay", color=text_color)
            ax.set_ylabel("Summed Intensity", color=text_color)
            ax.tick_params(colors=text_color)

            for spine in ax.spines.values():
                spine.set_color('#555555')

            canvas.draw()
        except Exception as e:
            print(f"Integral Plot Error: {e}")