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

            # 1. 记录相机... (略)
            try:
                saved_cam = plotter.camera_position
            except:
                saved_cam = None

            # 2. 应用色阶 (这里已经处理了黑场、白场和 Gamma)
            processed_data = VisualEngine.apply_levels(data, b, g, w)

            # 3. 设置透明度... (略)
            opac_dict = {"线性": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
                "对数": [0.000, 0.157, 0.249, 0.320, 0.383, 0.441, 0.494, 0.544, 0.591, 0.636, 1.000],
                "幂函数": [0.000, 0.188, 0.266, 0.327, 0.378, 0.424, 0.467, 0.507, 0.545, 0.583, 1.000],
                "sigmoid": [0.006, 0.018, 0.049, 0.118, 0.268, 0.500, 0.732, 0.882, 0.951, 0.982, 0.994]}
            selected_opac = opac_dict.get(opac_mode, opac_dict["线性"])

            # 4. 【关键修改】：调整 Colormap 的映射区间
            # 我们将映射区间固定在 [0, 1]，因为 apply_levels 已经把数据规范化了。
            # 这样，Colormap 的最深色永远对应你设定的“黑场”，最亮色对应“白场”。
            vol = plotter.add_volume(processed_data, cmap="magma", opacity=selected_opac, clim=[0, 1],  # <--- 强制颜色映射在 0 到 1 之间
                show_scalar_bar=False, name="main_vol", render=False)

            # ... 后续 Clipping 和 Axes 逻辑 (略) ...
            # 5. 处理切片限制 (Clipping Planes) - 完整保留逻辑
            if clip_ranges:
                r = clip_ranges
                planes = vtk.vtkPlaneCollection()
                # 定义 6 个面的法线和原点
                specs = [((r[0], 0, 0), (1, 0, 0)), ((r[1], 0, 0), (-1, 0, 0)), ((0, r[2], 0), (0, 1, 0)),
                    ((0, r[3], 0), (0, -1, 0)), ((0, 0, r[4]), (0, 0, 1)), ((0, 0, r[5]), (0, 0, -1))]
                for o, n in specs:
                    p = vtk.vtkPlane()
                    p.SetOrigin(o)
                    p.SetNormal(n)
                    planes.AddItem(p)
                vol.mapper.SetClippingPlanes(planes)
            else:
                plotter.remove_actor("pick_target")

            # 6. 处理标尺
            if show_axes and core_coords:
                VisualEngine.render_axes(plotter, data.shape, core_coords)
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
        """标尺渲染逻辑"""
        try:
            sx, sy, se = data_shape[0:3]
            xp, yp, zp = coords['X'], coords['Y'], coords['E']

            plotter.remove_bounds_axes()

            # 根据背景色自动调整标尺颜色
            bg = plotter.background_color
            ax_color = 'black' if (bg[0] > 0.9 and bg[1] > 0.9 and bg[2] > 0.9) else 'white'

            plotter.show_bounds(bounds=[0, sx, 0, sy, 0, se], grid='back', location='outer', ticks='both', font_size=10,
                color=ax_color, xtitle=f"Kx ({xp[0]:.2f}~{xp[-1]:.2f})", ytitle=f"Ky ({yp[0]:.2f}~{yp[-1]:.2f})",
                ztitle=f"E ({zp[0]:.2f}~{zp[-1]:.2f} eV)", render=False)
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