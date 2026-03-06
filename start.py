import sys
import numpy as np
from PyQt5.QtWidgets import QApplication, QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget, QFrame, QButtonGroup, \
    QFileDialog, QMessageBox
from PyQt5.QtCore import Qt
# 导入 SIUI 核心组件
import siui
from siui.core import SiGlobal, SiColor
from siui.components.widgets import SiLabel
from siui.components.button import SiCapsuleButton
from siui.components.tooltip import ToolTipWindow


# 导入渲染引擎和核心算法
from render_core import VisualEngine
from analyzer_core import AnalyzerCore

# 导入你的自定义页面
from page_image_control import ImageControlPage
from page_render_control import RenderControlPage
from page_data_process import DataProcessPage

# 渲染器相关（PyVista 和 Matplotlib）
from pyvistaqt import QtInteractor
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas


class My3DAnalyzer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowOpacity(0)

        # 1. 初始化核心状态与算法
        self.core = AnalyzerCore()
        self.clip_ranges = None
        self.is_dynamics_mode = False
        self.current_display_data = None
        self.mode_1d = None

        # 2. 注册全局气泡窗口（SiliconUI 规范）
        if "TOOL_TIP" not in SiGlobal.siui.windows:
            SiGlobal.siui.windows["TOOL_TIP"] = ToolTipWindow()
            SiGlobal.siui.windows["TOOL_TIP"].show()
            SiGlobal.siui.windows["TOOL_TIP"].setOpacity(0)

        self.setWindowTitle("3D 能带分析工具")
        self.resize(1550, 950)
        self.setStyleSheet("background-color: #151525;")

        self.init_ui()
        self.bind_all_events()
    def showEvent(self, event):
        super().showEvent(event)
        # 只执行一次初始化“体操”
        if not hasattr(self, "_initialized_layout"):
            from PyQt5.QtCore import QTimer
            # 给系统一瞬间的响应时间开始执行动作
            QTimer.singleShot(50, self.run_brute_force_layout)
            self._initialized_layout = True

    def run_brute_force_layout(self):
        """
        瞒天过海逻辑：
        在后台执行 放大 -> Page2 -> 缩小 -> Page3 -> 放大 -> Page1
        """
        # --- 步骤 1：窗口放大 ---
        self.showMaximized()

        # --- 步骤 2：轮转页面强制刷新 ---
        # 切换到第二页并轻微改变大小触发重绘
        self.page_container.setCurrentIndex(1)
        self.showNormal()  # 缩小

        # 切换到第三页并再次放大
        self.page_container.setCurrentIndex(2)
        self.showMaximized()  # 放大

        # --- 步骤 3：回归第一页并展示 ---
        self.page_container.setCurrentIndex(0)
        self.btn_page1.setChecked(True)  # 别忘了更新导航按钮状态

        # 让所有组件强制同步一次
        self.updateGeometry()

        # 最后，瞬移回用户面前并恢复透明度
        self.setWindowOpacity(1)
        self.activateWindow()  # 强行夺回焦点
        self.raise_()

    def init_ui(self):
        # 主布局
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        # --- 左侧：展示区 (使用 QStackedWidget 切换 3D/2D) ---
        self.left_display_stack = QStackedWidget()
        self.left_display_stack.setStyleSheet("background-color: #1A1A2E; border-radius: 12px;")

        # 3D 视图 (Index 0)
        self.plotter = QtInteractor(self.left_display_stack)
        self.plotter.set_background("#1A1A2E")
        self.left_display_stack.addWidget(self.plotter)

        # 2D 视图 (Index 1)
        self.fig = Figure(figsize=(5, 4), dpi=100, facecolor='#1A1A2E')
        self.canvas_2d = FigureCanvas(self.fig)
        self.ax_2d = self.fig.add_subplot(111)
        self.left_display_stack.addWidget(self.canvas_2d)

        main_layout.addWidget(self.left_display_stack, stretch=7)

        # --- 右侧：控制面板 ---
        self.right_panel = QFrame()
        self.right_panel.setStyleSheet("background-color: #2A2A3A; border-radius: 12px;")
        right_vbox = QVBoxLayout(self.right_panel)
        right_vbox.setContentsMargins(15, 10, 15, 15)
        right_vbox.setSpacing(10)

        # 1. 顶部选项卡导航
        nav_group = QFrame()
        nav_group.setFixedHeight(60)
        nav_layout = QHBoxLayout(nav_group)
        nav_layout.addStretch()

        self.btn_page1 = SiCapsuleButton(self)
        self.btn_page1.setText("图像控制")
        self.btn_page1.setCheckable(True)
        self.btn_page1.setChecked(True)
        self.btn_page2 = SiCapsuleButton(self)
        self.btn_page2.setText("渲染控制")
        self.btn_page2.setCheckable(True)
        self.btn_page3 = SiCapsuleButton(self)
        self.btn_page3.setText("处理分析")
        self.btn_page3.setCheckable(True)

        self.button_group = QButtonGroup(self)
        for i, btn in enumerate([self.btn_page1, self.btn_page2, self.btn_page3]):
            self.button_group.addButton(btn)
            nav_layout.addWidget(btn)
        self.button_group.setExclusive(True)

        nav_layout.addStretch()
        right_vbox.addWidget(nav_group)

        # 2. 页面容器
        self.page_container = QStackedWidget()
        self.page_image = ImageControlPage()
        self.page_render = RenderControlPage()
        self.page_data = DataProcessPage()

        self.page_container.addWidget(self.page_image)
        self.page_container.addWidget(self.page_render)
        self.page_container.addWidget(self.page_data)

        right_vbox.addWidget(self.page_container)
        main_layout.addWidget(self.right_panel, stretch=4)

    def bind_all_events(self):
        """中央调度绑定"""
        # 页面切换
        self.btn_page1.clicked.connect(lambda: self.page_container.setCurrentIndex(0))
        self.btn_page2.clicked.connect(lambda: self.page_container.setCurrentIndex(1))
        self.btn_page3.clicked.connect(lambda: self.page_container.setCurrentIndex(2))

        # Page 1: 基础逻辑
        self.page_image.btn_load.clicked.connect(self.on_load)
        self.page_image.btn_cut.clicked.connect(self.on_cut)
        self.page_image.btn_back.clicked.connect(self.on_back)
        self.page_image.btn_save.clicked.connect(self.on_screenshot)
        self.page_image.slider_time.valueChanged.connect(self.global_refresh)
        self.page_image.switch_axes.toggled.connect(self.global_refresh)
        self.page_image.switch_coord.toggled.connect(self.on_toggle_interactive_box)

        # Page 2: 渲染逻辑
        self.page_render.btn_apply_map.clicked.connect(self.global_refresh)
        self.page_render.btn_apply_noise.clicked.connect(self.global_refresh)

        # Page 3: 积分逻辑
        self.page_data.btn_t_apply.clicked.connect(self.on_apply_time_integral)

    # --- 核心刷新函数 ---
        self.page_data.s_ax_mid.valueChanged.connect(self.auto_refresh_integral)
        self.page_data.btn_ax_apply.clicked.connect(self.on_apply_axis_integral)
        self.page_data.s_ax_mid.valueChanged.connect(self.auto_refresh_integral)
        self.page_data.btn_other_apply.clicked.connect(self.on_apply_other_integral)

    def global_refresh(self):
        if self.core.raw_data is None: return

        # 1. 采集色阶参数
        black = self.page_render.s_low.value()
        gamma = self.page_render.s_gamma.value()
        white = self.page_render.s_up.value()
        levels = (black, gamma, white)
        mapping_mode = self.page_render.combo_map.currentText()

        #1d模式拦截
        if self.mode_1d:
            self.left_display_stack.setCurrentIndex(1)  # 切换到 Matplotlib 页面
            self.render_1d_plots()
            return

        # 2. 确定数据源
        if self.current_display_data is not None:
            base_3d = self.current_display_data
        else:
            t_idx = self.page_image.slider_time.value()
            base_3d = self.core.get_data_for_t(t_idx)

        # 3. 渲染逻辑
        if self.core.is_2d_mode:
            self.left_display_stack.setCurrentIndex(1)

            # 判断是否是坐标轴投影模式
            if hasattr(self.core, "slice_info") and self.core.slice_info.get("mode") == "integral":
                # 执行空间轴降维积分
                display_2d = self.core.get_axis_integrated_data(base_3d, self.page_data.combo_ax.currentText(),
                    self.page_data.s_ax_low.value(), self.page_data.s_ax_up.value())
            else:
                # 普通切片模式
                display_2d = base_3d

            VisualEngine.render_2d_slice(self.ax_2d, self.canvas_2d, display_2d, self.core.slice_info, levels,
                                         self.core.coords)
        else:
            self.left_display_stack.setCurrentIndex(0)
            VisualEngine.render_3d(self.plotter, base_3d, levels, opac_mode=mapping_mode, clip_ranges=self.clip_ranges,
                                   show_axes=self.page_image.switch_axes.isChecked(), core_coords=self.core.coords)
    # --- 调度执行函数 ---

    def on_load(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择.npz文件", "", "Data (*.npz)")
        if not path: return

        success, info = self.core.load_npz(path)
        if success:
            # --- 关键：先断开信号连接，防止初始化时的信号风暴 ---
            try:
                self.page_data.combo_ax.currentIndexChanged.disconnect()
                self.page_data.s_ax_low.valueChanged.disconnect()
                self.page_data.s_ax_up.valueChanged.disconnect()
            except:
                pass

            # 1. 初始化状态
            self.current_display_data = None
            self.clip_ranges = None
            self.core.is_2d_mode = False

            t_max = info[3] - 1

            # --- 定义一个统一的物理时间转换函数 (支持负数) ---
            # 这样三个滑块显示的悬浮气泡就完全一致了
            time_func = lambda v: f"Delay: {self.core.coords['delay'][int(v)]:.4f} ps"

            # 2. 设置 Page 1 的单帧时间滑块
            self.page_image.slider_time.setRange(0, t_max)
            self.page_image.slider_time.setToolTipConvertionFunc(time_func)

            # 3. 初始化 Page 3 (处理分析页) 的时间积分范围滑块
            # 设置索引范围 (0 到 N-1)
            self.page_data.s_t_low.setRange(0, t_max)
            self.page_data.s_t_up.setRange(0, t_max)

            # 设置悬浮气泡显示物理时间 (这里就会显示负数了)
            self.page_data.s_t_low.setToolTipConvertionFunc(time_func)
            self.page_data.s_t_up.setToolTipConvertionFunc(time_func)

            # 默认将上限设为最大值
            self.page_data.s_t_up.setValue(t_max)

            # 4. 重新绑定 Page 3 信号并更新轴滑块
            self.page_data.combo_ax.currentIndexChanged.connect(self.update_ax_slider_range)

            # 初始化轴滑块范围
            self.update_ax_slider_range()

            # 5. 执行首次渲染
            self.plotter.set_background("white")
            self.global_refresh()
            self.plotter.reset_camera()

    def update_ax_slider_range(self):
        if self.core.raw_data is None: return
        axis_idx = self.page_data.combo_ax.currentIndex()
        max_val = self.core.raw_data.shape[axis_idx] - 1

        # 所有的滑块范围保持一致
        for s in [self.page_data.s_ax_low, self.page_data.s_ax_up, self.page_data.s_ax_mid]:
            s.setRange(0, max_val)

    def on_cut(self):
        """指挥核心处理坐标字符串并刷新"""
        texts = self.page_image.get_slice_values()
        res = self.core.process_cut_logic(texts)
        if res:
            self.clip_ranges = res.get("clip_ranges")
            self.global_refresh()

    def on_toggle_interactive_box(self, checked):
        if checked and self.core.raw_data is not None:
            shape = self.core.raw_data.shape
            init_b = self.clip_ranges if self.clip_ranges else [0, shape[0], 0, shape[1], 0, shape[2]]
            self.plotter.add_box_widget(callback=lambda poly: self.page_image.set_slice_values(poly.bounds),
                bounds=init_b, color="#FF69B4", rotation_enabled=False)
        else:
            self.plotter.clear_box_widgets()
        self.plotter.render()

    def on_back(self):
        self.current_display_data = None  # 清除积分数据，回到单帧模式
        self.clip_ranges = None
        self.mode_1d = None
        self.core.is_2d_mode = False
        self.plotter.clear_actors()
        self.plotter.clear_box_widgets()
        self.global_refresh()
        self.plotter.reset_camera()

    def on_screenshot(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存截图", "capture.png", "PNG (*.png)")
        if path:
            if self.left_display_stack.currentIndex() == 1:
                self.fig.savefig(path)
            else:
                self.plotter.screenshot(path)

    def on_apply_time_integral(self):
        if self.core.raw_data is None: return

        low = self.page_data.s_t_low.value()
        up = self.page_data.s_t_up.value()

        # 获取积分数据
        self.current_display_data = self.core.get_time_integrated_data(low, up)

        # 切换到 3D 视图刷新
        self.global_refresh()


    def sync_ax_sliders_to_box(self):
        """滑块 -> Box 的单向联动"""
        if self.page_image.switch_coord.isChecked() and self.core.raw_data is not None:
            # 1. 采集当前滑块定义的区间
            ax_name = self.page_data.combo_ax.currentText()
            low = self.page_data.s_ax_low.value()
            up = self.page_data.s_ax_up.value()

            # 2. 获取基础范围 (如果已经有点切，就在切片基础上改；否则用原始形状)
            shape = self.core.raw_data.shape
            r = list(self.clip_ranges) if self.clip_ranges else [0, shape[0], 0, shape[1], 0, shape[2]]

            # 3. 覆盖对应轴的范围
            if ax_name == "X轴":
                r[0], r[1] = low, up
            elif ax_name == "Y轴":
                r[2], r[3] = low, up
            else:
                r[4], r[5] = low, up

            # 清除所有现有的 Box 挂件，防止叠加
            self.plotter.clear_box_widgets()

            # 4. 重新添加唯一的交互盒
            self.plotter.add_box_widget(bounds=r, color="#FF69B4", rotation_enabled=False,
                callback=lambda poly: self.page_image.set_slice_values(
                    poly.bounds))  # ----------------------------------------------

    def on_apply_axis_integral(self):
        """点击应用：执行坐标轴积分并显示 2D 投影图"""
        if self.core.raw_data is None: return

        # 标记当前进入 2D 投影模式
        self.core.is_2d_mode = True

        # 封装切片信息给 VisualEngine 使用
        # 这里的 axis 对应积分掉的那个轴，index 用于标识这是“积分投影”
        self.core.slice_info = {"axis": self.page_data.combo_ax.currentIndex(), "mode": "integral",
            "range": (self.page_data.s_ax_low.value(), self.page_data.s_ax_up.value())}

        self.global_refresh()

    def auto_refresh_integral(self):
        # 只有中点变动时，才去同步 slice_info 并刷新图像
        if self.core.is_2d_mode and hasattr(self.core, "slice_info"):
            if self.core.slice_info.get("mode") == "integral":
                self.core.slice_info["range"] = (self.page_data.s_ax_low.value(), self.page_data.s_ax_up.value())
                self.global_refresh()

                # 同时更新 3D 交互盒（如果开启了交互框）
                if self.page_image.switch_coord.isChecked():
                    self.sync_ax_sliders_to_box()

    def on_apply_other_integral(self):
        if self.core.raw_data is None: return

        text = self.page_data.combo_other.currentText()

        if text == "切片态密度":
            if self.clip_ranges is None:
                msg = QMessageBox(self)
                msg.setWindowTitle("未进行切片设置")
                msg.setText("请先在‘图像控制’页开启交互盒或手动设置切片范围！")
                msg.setIcon(QMessageBox.Warning)

                msg.setStyleSheet("""
                                    QMessageBox {
                                        background-color: #2A2A3A;
                                    }
                                    QLabel {
                                        color: #FFFFFF;
                                        font-family: "Segoe UI";
                                        font-size: 14px;
                                    }
                                    QPushButton {
                                        background-color: #E81123;
                                        color: white;
                                        border-radius: 4px;
                                        padding: 5px 15px;
                                    }
                                """)
                msg.exec_()
                return
            self.mode_1d = "Slice-DOS"
        elif text == "能级态密度":
            self.mode_1d = "Energy-DOS"

        self.core.is_2d_mode = True  # 借用 2D 视图容器（Matplotlib 窗口）

        self.global_refresh()

    def render_1d_plots(self):
        self.ax_2d.clear()

        if self.mode_1d == "Slice-DOS":
            # 获取交互盒范围，如果没有则取全体
            r = self.clip_ranges if self.clip_ranges else [0, *self.core.raw_data.shape[:3]]
            y_data = self.core.get_slice_dos_dynamics(r)
            x_data = self.core.coords['delay']

            self.ax_2d.plot(x_data, y_data, color='#FF69B4', linewidth=2)
            self.ax_2d.set_title("Slice Integrated Intensity vs Time", color='white')
            self.ax_2d.set_xlabel("Delay (ps)", color='white')

        elif self.mode_1d == "Energy-DOS":
            t_idx = self.page_image.slider_time.value()
            y_data = self.core.get_energy_dos(t_idx)
            x_data = self.core.coords['E']

            self.ax_2d.plot(x_data, y_data, color='#00F5FF', linewidth=2)
            self.ax_2d.set_title(f"Energy DOS (T={self.core.coords['delay'][t_idx]:.3f})", color='white')
            self.ax_2d.set_xlabel("Energy (eV)", color='white')

        self.ax_2d.set_ylabel("Intensity (a.u.)", color='white')
        self.ax_2d.tick_params(colors='white')
        self.fig.tight_layout()
        self.canvas_2d.draw()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = My3DAnalyzer()
    window.showMaximized()
    sys.exit(app.exec_())