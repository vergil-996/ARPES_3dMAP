import sys
import numpy as np
from PyQt5.QtWidgets import (QApplication, QVBoxLayout, QHBoxLayout, QWidget, QFrame, QStackedWidget, QFileDialog,
                             QMessageBox)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

# SiliconUI 核心
from siui.core import SiGlobal, SiColor
from siui.components.widgets import SiLabel, SiPushButton, SiScrollArea
from siui.components.editbox import SiLabeledLineEdit
from siui.components.slider_ import SiSlider
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.tooltip import ToolTipWindow
from siui.components.button import SiSwitchRefactor

# 渲染引擎
import pyvista as pv
from pyvistaqt import QtInteractor
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar2

from analyzer_core import AnalyzerCore


class My3DAnalyzer(QWidget):
    def __init__(self):
        super().__init__()

        # 1. 注册全局气泡窗口
        if "TOOL_TIP" not in SiGlobal.siui.windows:
            SiGlobal.siui.windows["TOOL_TIP"] = ToolTipWindow()
            SiGlobal.siui.windows["TOOL_TIP"].show()
            SiGlobal.siui.windows["TOOL_TIP"].setOpacity(0)

        self.core = AnalyzerCore()
        self.show_axes_flag = False
        self.clip_ranges = None
        self.slice_info = None
        self.is_dynamics_mode = False

        self.setWindowTitle("3D 能带结构分析工具")
        self.resize(1366, 850)
        self.setStyleSheet("background-color: #151525;")

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(30)

        # --- 左侧：双展示区 ---
        self.left_panel_container = QStackedWidget()
        self.left_panel_container.setStyleSheet("background-color: #1A1A2E; border-radius: 8px;")

        # --- 3D 渲染器配置 ---
        self.plotter = QtInteractor(self.left_panel_container)
        self.plotter.set_background("#1A1A2E")

        # 屏蔽原有的鼠标回调
        self.plotter.track_mouse_position()
        self.plotter.add_on_render_callback(self.on_mouse_moved_in_3d)

        # 交互状态文字
        self.coord_label = SiLabel("交互模式: 拖拽盒子的面以同步坐标", self.plotter)
        self.coord_label.setStyleSheet("color: #FF69B4; background: rgba(0,0,0,120); padding: 8px; border-radius: 4px;")
        self.coord_label.move(15, 15)
        self.coord_label.hide()

        self.left_panel_container.addWidget(self.plotter)

        # --- 2D / 曲线展示区 ---
        self.fig = Figure(figsize=(5, 4), dpi=100, facecolor='#1A1A2E')
        self.canvas_2d = FigureCanvas(self.fig)
        self.ax_2d = self.fig.add_subplot(111)
        self.ax_2d.set_facecolor('#1A1A2E')

        self.mpl_toolbar = NavigationToolbar2(self.canvas_2d, self)
        self.mpl_toolbar.hide()

        self.left_panel_container.addWidget(self.canvas_2d)
        main_layout.addWidget(self.left_panel_container, stretch=7)

        # --- 右侧：控制面板 ---
        self.right_frame = QFrame()
        self.right_frame.setStyleSheet("QFrame { background-color: #2A2A3A; border-radius: 12px; }")
        right_frame_layout = QVBoxLayout(self.right_frame)
        right_frame_layout.setContentsMargins(5, 5, 5, 5)

        self.scroll_area = SiScrollArea(self.right_frame)
        self.container = QWidget()
        self.container.setStyleSheet("background: transparent;")
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(20)
        self.vbox.setContentsMargins(20, 20, 20, 20)

        def create_group(title, widget):
            grp = SiTitledWidgetGroup(self)
            grp.addTitle(title)
            for child in grp.findChildren(SiLabel):
                child.colorGroup().assign(SiColor.TEXT_A, "#FF69B4")
                child.reloadStyleSheet()
            v = QVBoxLayout(grp)
            v.setContentsMargins(15, 55, 15, 20)
            v.addWidget(widget)
            return grp

        def create_slider_row():
            row = QHBoxLayout()
            slider = SiSlider(self)
            slider.setFixedHeight(32)
            slider.style_data.main_color = QColor("#FF69B4")
            slider.style_data.background_color = QColor(255, 105, 180, 64)
            slider.style_data.handle_color = QColor("#FFFFFF")
            row.addWidget(slider)
            return row, slider

        # 1. 时间轴控制
        time_w = QWidget()
        time_l = QVBoxLayout(time_w)
        row_t, self.slider_time = create_slider_row()
        self.slider_time.valueChanged.connect(self.update_visual)
        time_l.addLayout(row_t)
        self.vbox.addWidget(create_group("时间轴控制", time_w))

        # 2. 曝光度控制
        exposure_w = QWidget()
        exposure_l = QVBoxLayout(exposure_w)
        row_e, self.slider_exposure = create_slider_row()
        self.slider_exposure.setValue(50)
        self.slider_exposure.valueChanged.connect(self.update_visual)
        exposure_l.addLayout(row_e)
        self.vbox.addWidget(create_group("曝光度控制", exposure_w))

        # 3. 切片立方体设置
        slice_w = QWidget()
        slice_l = QVBoxLayout(slice_w)
        slice_l.setSpacing(12)
        self.edits = {}
        axes_cfg = [("X轴下限", "X轴上限"), ("Y轴下限", "Y轴上限"), ("Z轴下限", "Z轴上限")]
        for min_label, max_label in axes_cfg:
            h_row = QHBoxLayout()
            h_row.setSpacing(10)
            e_min = SiLabeledLineEdit(self)
            e_min.setFixedHeight(45)
            e_min.setTitle(min_label)
            e_max = SiLabeledLineEdit(self)
            e_max.setFixedHeight(45)
            e_max.setTitle(max_label)
            h_row.addWidget(e_min, stretch=1)
            h_row.addWidget(e_max, stretch=1)
            slice_l.addLayout(h_row)
            self.edits[min_label], self.edits[max_label] = e_min, e_max
        self.vbox.addWidget(create_group("切片立方体设置", slice_w))

        # --- 标尺与坐标开关组 ---
        switch_row = QHBoxLayout()
        switch_row.setSpacing(15)

        self.switch_axes = SiSwitchRefactor(self)
        self.switch_axes.toggled.connect(self.on_toggle_axes)
        lbl_axes = SiLabel("显示标尺")
        lbl_axes.setStyleSheet("color: white; font-weight: bold;")

        self.switch_coord = SiSwitchRefactor(self)
        self.switch_coord.toggled.connect(self.on_toggle_coord)
        lbl_coord = SiLabel("切片交互")
        lbl_coord.setStyleSheet("color: white; font-weight: bold;")

        switch_row.addStretch()
        switch_row.addWidget(lbl_axes)
        switch_row.addWidget(self.switch_axes)
        switch_row.addSpacing(20)
        switch_row.addWidget(lbl_coord)
        switch_row.addWidget(self.switch_coord)
        switch_row.addStretch()
        self.vbox.addLayout(switch_row)

        # 4. 按钮组
        btn_row = QHBoxLayout()

        def create_red_btn(t, callback):
            btn = SiPushButton(self)
            btn.setFixedHeight(40)
            btn.attachment().setText(t)
            btn.colorGroup().assign(SiColor.BUTTON_PANEL, "#E81123")
            btn.colorGroup().assign(SiColor.TEXT_B, "#FFFFFF")
            btn.clicked.connect(callback)
            btn.reloadStyleSheet()
            return btn

        btn_row.addWidget(create_red_btn("加载", self.on_load), stretch=1)
        btn_row.addWidget(create_red_btn("截取", self.on_cut), stretch=1)
        btn_row.addWidget(create_red_btn("积分", self.on_integral), stretch=1)
        btn_row.addWidget(create_red_btn("截图", self.on_screenshot), stretch=1)
        btn_row.addWidget(create_red_btn("返回", self.on_back), stretch=1)
        self.vbox.addLayout(btn_row)

        self.vbox.addStretch()
        self.scroll_area.setCenterWidget(self.container)
        right_frame_layout.addWidget(self.scroll_area)
        main_layout.addWidget(self.right_frame, stretch=4)
        SiGlobal.siui.reloadAllWindowsStyleSheet()

    def on_toggle_coord(self, checked):
        """显示坐标开关彻底改为交互盒开关"""
        if not checked:
            self.coord_label.hide()
            self.plotter.clear_box_widgets()
        else:
            if self.core.raw_data is None:
                self.switch_coord.setChecked(False)
                return
            if self.left_panel_container.currentIndex() == 0:
                self.coord_label.show()
                shape = self.core.raw_data.shape
                init_b = self.clip_ranges if self.clip_ranges else [0, shape[0], 0, shape[1], 0, shape[2]]
                self.plotter.add_box_widget(callback=self.sync_box_to_edits, bounds=init_b, color="#FF69B4",
                    rotation_enabled=False  # 禁用旋转
                )
        self.plotter.render()

    def sync_box_to_edits(self, box_polydata):
        """将盒子坐标填入右侧输入框"""
        try:
            b = box_polydata.bounds
            self.edits["X轴下限"].setText(f"{int(round(b[0]))}")
            self.edits["X轴上限"].setText(f"{int(round(b[1]))}")
            self.edits["Y轴下限"].setText(f"{int(round(b[2]))}")
            self.edits["Y轴上限"].setText(f"{int(round(b[3]))}")
            self.edits["Z轴下限"].setText(f"{int(round(b[4]))}")
            self.edits["Z轴上限"].setText(f"{int(round(b[5]))}")
        except:
            pass

    def on_mouse_moved_in_3d(self, *args):
        """ 彻底移除原来的辅助线逻辑 """
        pass

    def on_toggle_axes(self, checked):
        """标尺开关逻辑修复"""
        self.show_axes_flag = checked
        if not checked:
            try: self.plotter.remove_bounds_axes()
            except: pass
        self.update_visual() # 通过 update_visual 触发 render_3d，进而调用 render_axes

    def render_axes(self):
        """标尺渲染逻辑修复：强制颜色同步"""
        try:
            if self.core.raw_data is None: return

            # 获取数据范围
            sx, sy, se = self.core.raw_data.shape[0:3]
            xp, yp, zp = self.core.coords['X'], self.core.coords['Y'], self.core.coords['E']

            # 彻底清除旧标尺
            self.plotter.remove_bounds_axes()

            # 确定颜色：如果是白色背景(1,1,1)，标尺用黑色；否则用白色
            bg = self.plotter.background_color
            ax_color = 'black' if (bg[0] > 0.9 and bg[1] > 0.9 and bg[2] > 0.9) else 'white'

            # 重新绘制标尺
            self.plotter.show_bounds(bounds=[0, sx, 0, sy, 0, se], grid='back', location='outer', ticks='both',
                font_size=10, color=ax_color,  # 这里的颜色现在是强制跟随背景的
                xtitle=f"Kx ({xp[0]:.2f}~{xp[-1]:.2f})", ytitle=f"Ky ({yp[0]:.2f}~{yp[-1]:.2f})",
                ztitle=f"E ({zp[0]:.2f}~{zp[-1]:.2f} eV)", render=False)
        except Exception as e:
            print(f"Axes error: {e}")
    def on_load(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择.npz文件", "", "Data (*.npz)")
        if path:
            success, info = self.core.load_npz(path)
            if success:
                # 加载时背景设为白色
                self.plotter.set_background("white")
                self.fig.set_facecolor('white')
                self.ax_2d.set_facecolor('white')

                self.slider_time.setRange(0, info[3] - 1)
                self.slider_time.setValue(0)
                # 保留 Delay 显示
                self.slider_time.setToolTipConvertionFunc(
                    lambda _: f"Delay: {self.core.coords['delay'][int(self.slider_time.value())]:.4f}")

                self.update_visual()
                self.plotter.reset_camera()
                QMessageBox.information(self, "成功", "数据已成功加载！")

    def update_visual(self):
        if self.core.raw_data is None: return
        data = self.core.get_data_for_t(self.slider_time.value())
        if self.core.is_2d_mode:
            self.left_panel_container.setCurrentIndex(1)
            self.coord_label.hide()
            self.render_2d(data)
        else:
            self.left_panel_container.setCurrentIndex(0)
            if self.switch_coord.isChecked() and not self.is_dynamics_mode:
                self.coord_label.show()
            else:
                self.coord_label.hide()
            self.render_3d(data, self.slider_exposure.value())

    def render_3d(self, data, exposure):
        try:
            try:
                saved_cam = self.plotter.camera_position
            except:
                saved_cam = None
            ratio = (exposure / 50.0)
            opac = [0.0, 0.15 * ratio, 0.45 * ratio, 0.75 * ratio, 0.95 * ratio, 1.0, 1.0]
            vol = self.plotter.add_volume(data, cmap="magma", opacity=opac, show_scalar_bar=False, name="main_vol",
                                          render=False)

            if self.clip_ranges:
                import vtk
                r = self.clip_ranges
                planes = vtk.vtkPlaneCollection()
                specs = [((r[0], 0, 0), (1, 0, 0)), ((r[1], 0, 0), (-1, 0, 0)), ((0, r[2], 0), (0, 1, 0)),
                         ((0, r[3], 0), (0, -1, 0)), ((0, 0, r[4]), (0, 0, 1)), ((0, 0, r[5]), (0, 0, -1))]
                for o, n in specs:
                    p = vtk.vtkPlane();
                    p.SetOrigin(o);
                    p.SetNormal(n);
                    planes.AddItem(p)
                vol.mapper.SetClippingPlanes(planes)

                pick_box = pv.Box(bounds=r)
                self.plotter.add_mesh(pick_box, name="pick_target", opacity=0.0, render=False)
            else:
                self.plotter.remove_actor("pick_target")

            if self.show_axes_flag: self.render_axes()
            if saved_cam: self.plotter.camera_position = saved_cam
            self.plotter.render()
        except:
            pass

    def render_2d(self, data):
        try:
            if self.slice_info is None: return
            idx, cut, exp = self.slice_info["axis"], self.slice_info["index"], self.slider_exposure.value()
            xp, yp, zp = self.core.coords['X'], self.core.coords['Y'], self.core.coords['E']
            if idx == 0:
                slice_img, ext, title = data[cut, :, :].T, [yp[0], yp[-1], zp[0], zp[-1]], f"Kx={xp[cut]:.3f}"
            elif idx == 1:
                slice_img, ext, title = data[:, cut, :].T, [xp[0], xp[-1], zp[0], zp[-1]], f"Ky={yp[cut]:.3f}"
            else:
                slice_img, ext, title = data[:, :, cut].T, [xp[0], xp[-1], yp[0], yp[-1]], f"E={zp[cut]:.3f}"

            vmax = np.max(slice_img) * (1.1 - exp / 100.0) if np.max(slice_img) > 0 else 1.0
            self.ax_2d.clear()
            # 保留 spline16 插值
            self.ax_2d.imshow(slice_img, cmap="magma", aspect='auto', origin='lower', extent=ext, vmax=vmax,
                              interpolation='spline16')
            self.ax_2d.set_title(title, color='black' if self.fig.get_facecolor() == (1, 1, 1, 1) else 'white')
            self.canvas_2d.draw()
        except:
            pass

    def on_cut(self):
        try:
            texts = {k: v.text().strip() for k, v in self.edits.items()}
            axes_pairs = [("X轴下限", "X轴上限"), ("Y轴下限", "Y轴上限"), ("Z轴下限", "Z轴上限")]
            filled_axes = [i for i, (min_k, max_k) in enumerate(axes_pairs) if
                           texts[min_k] != "" and texts[max_k] != ""]

            if len(filled_axes) == 1:
                idx = filled_axes[0]
                v_min, v_max = float(texts[axes_pairs[idx][0]]), float(texts[axes_pairs[idx][1]])
                if v_min == v_max:
                    self.core.is_2d_mode, self.slice_info, self.clip_ranges = True, {"axis": idx,
                                                                                     "index": int(v_min)}, None
                    self.update_visual()
                    return

            self.core.is_2d_mode = False
            self.clip_ranges = [float(texts["X轴下限"] or 0), float(texts["X轴上限"] or 200),
                                float(texts["Y轴下限"] or 0), float(texts["Y轴上限"] or 200),
                                float(texts["Z轴下限"] or 0), float(texts["Z轴上限"] or 200)]
            self.update_visual()
        except Exception as e:
            QMessageBox.warning(self, "错误", f"输入内容无效: {str(e)}")

    def on_back(self):
        if self.is_dynamics_mode:
            self.is_dynamics_mode = False
            self.update_visual()
            return
        if self.clip_ranges is not None or self.slice_info is not None:
            self.clip_ranges, self.slice_info, self.core.is_2d_mode = None, None, False
            self.plotter.clear_actors()
            self.plotter.clear_box_widgets()  # 顺便清理盒子
            self.plotter.remove_bounds_axes()
            self.update_visual()
            self.plotter.reset_camera()
            return

    def on_screenshot(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存截图", "capture.png", "PNG (*.png)")
        if path:
            if self.is_dynamics_mode or self.left_panel_container.currentIndex() == 1:
                self.fig.savefig(path, facecolor=self.fig.get_facecolor())
            else:
                self.plotter.screenshot(path)

    def on_integral(self):
        if self.core.raw_data is None:
            QMessageBox.warning(self, "错误", "请先加载数据！")
            return
        if self.clip_ranges is None:
            QMessageBox.warning(self, "错误", "请先进行‘截取’操作以确定立方体范围！")
            return
        try:
            y_data = self.core.get_integrated_dynamics(self.clip_ranges)
            x_data = self.core.coords['delay']
            self.is_dynamics_mode = True
            self.left_panel_container.setCurrentIndex(1)
            self.coord_label.hide()
            self.ax_2d.clear()
            # 保留积分图风格
            self.ax_2d.plot(x_data, y_data, color='#FF69B4', linewidth=2, marker='o', markersize=4)
            self.ax_2d.set_title("Integrated Intensity Dynamics",
                                 color='black' if self.fig.get_facecolor() == (1, 1, 1, 1) else 'white', fontsize=12)
            self.ax_2d.set_xlabel("Delay", color='black' if self.fig.get_facecolor() == (1, 1, 1, 1) else 'white')
            self.ax_2d.set_ylabel("Summed Intensity",
                                  color='black' if self.fig.get_facecolor() == (1, 1, 1, 1) else 'white')
            self.ax_2d.tick_params(colors='black' if self.fig.get_facecolor() == (1, 1, 1, 1) else 'white')
            for spine in self.ax_2d.spines.values(): spine.set_color('#555555')
            self.canvas_2d.draw()
        except Exception as e:
            QMessageBox.critical(self, "计算错误", f"积分计算失败: {str(e)}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet("""
        QMessageBox { background-color: #2A2A3A; }
        QMessageBox QLabel { color: white; font-family: "Segoe UI"; font-size: 14px; }
        QMessageBox QPushButton { background-color: #E81123; color: white; border-radius: 4px; padding: 6px 18px; min-width: 70px; }
        QMessageBox QPushButton:hover { background-color: #FF2E3D; }
    """)
    window = My3DAnalyzer()
    window.showMaximized()
    sys.exit(app.exec_())