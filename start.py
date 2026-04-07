import sys
import os
import numpy as np
from PyQt5.QtWidgets import QApplication, QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget, QFrame, QButtonGroup, \
    QFileDialog, QMessageBox
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QIcon
# 导入 SIUI 核心组件
import siui
from siui.core import SiGlobal, SiColor
from siui.components.widgets import SiLabel
from siui.components.button import SiCapsuleButton
from siui.components.tooltip import ToolTipWindow


# 导入渲染引擎和核心算法
from render_core import VisualEngine
from analyzer_core import AnalyzerCore
from data_trans import convert as convert_mat_to_npz

# 导入你的自定义页面
from page_image_control_v2 import ImageControlPage
from page_render_control import RenderControlPage
from page_data_process_v2 import DataProcessPage

# 渲染器相关（PyVista 和 Matplotlib）
from pyvistaqt import QtInteractor
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from scipy.io import savemat


def resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, relative_path)


class My3DAnalyzer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowOpacity(0)

        # 1. 初始化核心状态与算法
        self.core = AnalyzerCore()
        self.clip_ranges = None
        self.is_dynamics_mode = False
        self.current_display_data = None
        self.base_raw_data = None
        self.base_coords = None
        self.base_current_display_data = None
        self.original_coords = None
        self.precise_logical_bounds = None
        self.last_synced_slice_texts = None
        self.mode_1d = None
        self.applied_other_mode = None
        self.back_click_count = 1  # 记录返回按钮点击次数
        self.is_denoised_mode = False
        self.axis_refresh_timer = QTimer(self)
        self.axis_refresh_timer.setSingleShot(True)
        self.axis_refresh_timer.setInterval(40)
        self.axis_refresh_timer.timeout.connect(self.auto_refresh_integral)

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
        self._update_export_button_states()
    def showEvent(self, event):
        super().showEvent(event)
        # 只执行一次初始化
        if not hasattr(self, "_initialized_layout"):
            from PyQt5.QtCore import QTimer
            # 给系统一瞬间的响应时间开始执行动作
            QTimer.singleShot(50, self.run_brute_force_layout)
            self._initialized_layout = True

    def run_brute_force_layout(self):
        # 窗口放大
        self.showMaximized()

        # 轮转页面强制刷新
        # 切换到第二页并轻微改变大小触发重绘
        self.page_container.setCurrentIndex(1)
        self.showNormal()  # 缩小

        # 切换到第三页并再次放大
        self.page_container.setCurrentIndex(2)
        self.showMaximized()  # 放大

        # 回归第一页并展示
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
        self.page_image.btn_export.clicked.connect(self.on_save_cube_data)
        self.page_image.btn_back.clicked.connect(self.on_back)
        self.page_image.btn_save.clicked.connect(self.on_screenshot)
        self.page_image.slider_time.valueChanged.connect(self.global_refresh)
        self.page_image.switch_axes.toggled.connect(self.global_refresh)
        self.page_image.switch_coord.toggled.connect(self.on_toggle_interactive_box)
        self.page_image.switch_flip.toggled.connect(self.on_toggle_e_flip)

        # Page 2: 渲染逻辑
        self.page_render.btn_apply_cmap.clicked.connect(self.global_refresh)
        self.page_render.btn_apply_map.clicked.connect(self.global_refresh)
        self.page_render.btn_apply_noise.clicked.connect(self.on_apply_denoise)

        # Page 3: 积分逻辑
        self.page_data.btn_t_apply.clicked.connect(self.on_apply_time_integral)

    # --- 核心刷新函数 ---
        self.page_data.s_ax_mid.valueChanged.connect(self.schedule_axis_refresh)
        self.page_data.s_ax_low.sliderReleased.connect(self.on_axis_bound_released)
        self.page_data.s_ax_up.sliderReleased.connect(self.on_axis_bound_released)
        self.page_data.s_ax_mid.sliderReleased.connect(self.flush_axis_refresh)
        self.page_data.btn_ax_apply.clicked.connect(self.on_apply_axis_integral)
        self.page_data.btn_other_apply.clicked.connect(self.on_apply_other_integral)
        self.page_data.btn_other_save.clicked.connect(self.on_save_other_integral)
        self.page_data.combo_other.currentIndexChanged.connect(self.on_other_mode_selection_changed)

    def _get_spatial_shape(self):
        if self.current_display_data is not None:
            return self.current_display_data.shape

        if self.core.raw_data is None:
            return None

        return self.core.raw_data.shape[:3]

    def _clone_coords(self, coords=None):
        source = coords if coords is not None else self.core.coords
        return {
            key: None if value is None else np.array(value, copy=True)
            for key, value in source.items()
        }

    def _apply_display_state(self):
        if self.base_raw_data is None or self.base_coords is None:
            return

        display_raw = np.array(self.base_raw_data, copy=True)
        display_coords = self._clone_coords(self.base_coords)

        if self.page_image.switch_flip.isChecked():
            display_raw = np.flip(display_raw, axis=2)
            display_coords["E"] = np.flip(display_coords["E"])

        self.core.raw_data = display_raw
        self.core.coords = display_coords

        if self.base_current_display_data is None:
            self.current_display_data = None
        else:
            display_3d = np.array(self.base_current_display_data, copy=True)
            if self.page_image.switch_flip.isChecked():
                display_3d = np.flip(display_3d, axis=2)
            self.current_display_data = display_3d

    def _get_full_logical_bounds(self):
        shape = self._get_spatial_shape()
        if shape is None:
            return None

        return [
            0.0, max(shape[0] - 1, 0),
            0.0, max(shape[1] - 1, 0),
            0.0, max(shape[2] - 1, 0),
        ]

    @staticmethod
    def _logical_bounds_to_texts(logical_bounds):
        labels = ["X轴下限", "X轴上限", "Y轴下限", "Y轴上限", "Z轴下限", "Z轴上限"]
        return {label: str(logical_bounds[idx]) for idx, label in enumerate(labels)}

    def _sync_slice_edits_from_logical_bounds(self, logical_bounds=None):
        if self.core.raw_data is None:
            return

        bounds = logical_bounds if logical_bounds is not None else self._get_full_logical_bounds()
        if bounds is None:
            return

        self.precise_logical_bounds = list(bounds)
        physical_bounds = self.core.logical_bounds_to_physical_bounds(bounds)
        self.page_image.set_slice_values(physical_bounds)
        self.last_synced_slice_texts = self.page_image.get_slice_values()

    def _sync_slice_edits_from_render_bounds(self, render_bounds):
        shape = self._get_spatial_shape()
        if shape is None:
            return

        logical_bounds = self.core.render_to_logical_bounds(render_bounds, shape)
        self._sync_slice_edits_from_logical_bounds(logical_bounds)

    def _get_render_bounds_for_box(self, logical_bounds=None):
        shape = self._get_spatial_shape()
        if shape is None:
            return None

        bounds = logical_bounds if logical_bounds is not None else self.clip_ranges
        if bounds is None:
            bounds = self._get_full_logical_bounds()

        return self.core.logical_to_render_bounds(bounds, shape)

    def _rebuild_interactive_box(self, logical_bounds=None):
        if not self.page_image.switch_coord.isChecked() or self.core.raw_data is None:
            return

        box_bounds = self._get_render_bounds_for_box(logical_bounds)
        if box_bounds is None:
            return

        self.plotter.clear_box_widgets()
        self.plotter.add_box_widget(
            callback=lambda poly: self._sync_slice_edits_from_render_bounds(poly.bounds),
            bounds=box_bounds,
            factor=1.0,
            color="#FF69B4",
            rotation_enabled=False
        )

    def on_toggle_e_flip(self, checked):
        if self.base_raw_data is None:
            return

        self._apply_display_state()
        self.update_ax_slider_range()
        self._sync_slice_edits_from_logical_bounds(self.clip_ranges)
        self.global_refresh()

        if self.page_image.switch_coord.isChecked():
            self._rebuild_interactive_box()
            self.plotter.render()

    def _configure_time_controls(self):
        has_time_axis = self.core.has_time_axis and self.core.raw_data is not None and self.core.raw_data.shape[3] > 1

        self.page_image.slider_time.setEnabled(has_time_axis)
        self.page_data.s_t_low.setEnabled(has_time_axis)
        self.page_data.s_t_up.setEnabled(has_time_axis)
        self.page_data.btn_t_apply.setEnabled(has_time_axis)

    def _update_export_button_states(self):
        has_data = self.base_raw_data is not None
        self.page_image.btn_export.setEnabled(has_data and self.mode_1d is None)
        self.page_data.btn_other_save.setEnabled(self.applied_other_mode is not None)

    @staticmethod
    def _sanitize_save_path(path, selected_filter=""):
        root, ext = os.path.splitext(path)
        if ext.lower() not in {".mat", ".npz"}:
            if "npz" in selected_filter.lower():
                return path + ".npz"
            return path + ".mat"
        return path

    def _choose_export_path(self, title, default_name):
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            title,
            default_name,
            "MATLAB Files (*.mat);;NumPy Files (*.npz)"
        )
        if not path:
            return ""
        return self._sanitize_save_path(path, selected_filter)

    @staticmethod
    def _save_dict_to_path(path, data_dict):
        serializable = {}
        for key, value in data_dict.items():
            if value is None:
                continue
            serializable[key] = np.asarray(value)

        if path.lower().endswith(".npz"):
            np.savez(path, **serializable)
        else:
            savemat(path, serializable)

    def _get_clip_slices(self, logical_bounds=None):
        if self.base_raw_data is None:
            return None

        bounds = logical_bounds if logical_bounds is not None else self.clip_ranges
        if bounds is None:
            return None

        slices = []
        index_bounds = []
        shape = self.base_raw_data.shape[:3]

        for axis_idx in range(3):
            axis_max = max(shape[axis_idx] - 1, 0)
            low = float(bounds[axis_idx * 2])
            up = float(bounds[axis_idx * 2 + 1])
            low_idx = int(np.clip(np.floor(min(low, up)), 0, axis_max))
            up_idx = int(np.clip(np.ceil(max(low, up)), 0, axis_max))
            slices.append(slice(low_idx, up_idx + 1))
            index_bounds.extend([low_idx, up_idx])

        return slices, index_bounds

    def load_data(self, path):
        target_path = path

        if path.lower().endswith(".mat"):
            default_name = os.path.splitext(os.path.basename(path))[0] + ".npz"
            target_path, _ = QFileDialog.getSaveFileName(self, "保存转换后的 npz", default_name, "NumPy Files (*.npz)")
            if not target_path:
                return

            try:
                convert_mat_to_npz(path, target_path)
            except Exception as exc:
                msg = QMessageBox(self)
                msg.setWindowTitle("MAT 转换失败")
                msg.setIcon(QMessageBox.Critical)
                msg.setText(f"无法将 .mat 文件转换为 .npz：\n{exc}")
                msg.exec_()
                return

        success, info = self.core.load_npz(target_path, is_flip=False)
        if not success:
            msg = QMessageBox(self)
            msg.setWindowTitle("数据加载失败")
            msg.setIcon(QMessageBox.Critical)
            msg.setText(f"无法加载文件：\n{info}")
            msg.exec_()
            return

        self.original_raw_data = self.core.raw_data.copy()
        self.original_coords = self._clone_coords()
        self.base_raw_data = self.original_raw_data.copy()
        self.base_coords = self._clone_coords(self.original_coords)
        self.base_current_display_data = None
        self._apply_display_state()

        try:
            self.page_data.combo_ax.currentIndexChanged.disconnect()
            self.page_data.s_ax_low.valueChanged.disconnect()
            self.page_data.s_ax_up.valueChanged.disconnect()
        except:
            pass

        self.current_display_data = None
        self.base_current_display_data = None
        self.clip_ranges = None
        self.core.is_2d_mode = False
        self.mode_1d = None
        self.applied_other_mode = None
        self.back_click_count = 1
        self.is_denoised_mode = False

        t_max = info[3] - 1
        slider_max = max(t_max, 1)
        time_func = lambda v: f"Delay: {self.core.coords['delay'][min(int(v), len(self.core.coords['delay']) - 1)]:.4f} fs"

        self.page_image.slider_time.setRange(0, slider_max)
        self.page_image.slider_time.setValue(0)
        self.page_image.slider_time.setToolTipConvertionFunc(time_func)

        self.page_data.s_t_low.setRange(0, slider_max)
        self.page_data.s_t_up.setRange(0, slider_max)
        self.page_data.s_t_low.setValue(0)
        self.page_data.s_t_up.setValue(0 if t_max == 0 else t_max)
        self.page_data.s_t_low.setToolTipConvertionFunc(time_func)
        self.page_data.s_t_up.setToolTipConvertionFunc(time_func)

        self.page_data.combo_ax.currentIndexChanged.connect(self.update_ax_slider_range)

        self.update_ax_slider_range()
        self._sync_slice_edits_from_logical_bounds()
        self._configure_time_controls()
        self._update_export_button_states()

        self.plotter.set_background("white")
        self.global_refresh()
        self.plotter.reset_camera()
        if self.page_image.switch_coord.isChecked():
            self._rebuild_interactive_box()

    def global_refresh(self):
        if self.core.raw_data is None: return

        if self.mode_1d:
            self.left_display_stack.setCurrentIndex(1)  # 切换到 Matplotlib 页面
            self.render_1d_plots()
            return

        # 1. 采集色阶参数
        black = self.page_render.s_low.value()
        gamma = self.page_render.s_gamma.value()
        white = self.page_render.s_up.value()
        levels = (black, gamma, white)
        mapping_mode = self.page_render.combo_map.currentText()
        current_cmap = self.page_render.get_selected_cmap()


        # 2. 确定数据源
        if self.current_display_data is not None:
            # 只有在“积分模式”下才走这里
            base_3d = self.current_display_data
        else:
            # 正常模式：根据时间滑块读取（此时读到的是去噪后的 4D 里的某一帧）
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
                                         self.core.coords, cmap=current_cmap)
        else:
            self.left_display_stack.setCurrentIndex(0)
            render_clip_ranges = self.core.logical_to_render_bounds(self.clip_ranges, base_3d.shape) if self.clip_ranges else None
            VisualEngine.render_3d(self.plotter, base_3d, levels, opac_mode=mapping_mode, clip_ranges=render_clip_ranges,
                                   show_axes=self.page_image.switch_axes.isChecked(), core_coords=self.core.coords,
                                   cmap=current_cmap)
            self.plotter.reset_camera()
            self.plotter.render()

    # --- 调度执行函数 ---
    def on_apply_denoise(self):
        if not hasattr(self, 'original_raw_data'): return
        from denoise_engines import DenoiseEngines
        methods = self.page_render.get_denoise_settings()

        # 1. 始终基于原始底片去噪

        temp_data = DenoiseEngines.apply_pipeline(self.original_raw_data, methods)

        # 只要有一维超过 200，就执行 2x 采样
        if max(temp_data.shape[1:]) > 200:

            self.base_raw_data = temp_data[:, ::2, ::2, ::2]
            self.base_coords = self._clone_coords(self.original_coords)
            for k in ["X", "Y", "E"]:
                self.base_coords[k] = self.base_coords[k][::2]
        else:
            self.base_raw_data = temp_data
            self.base_coords = self._clone_coords(self.original_coords)

        # 3. 更新 UI 滑块范围，防止越界触发 0xC0000409
        self.base_current_display_data = None
        self._apply_display_state()
        self.update_ax_slider_range()
        self._sync_slice_edits_from_logical_bounds(self.clip_ranges)

        self.back_click_count = 2  # 标记已改动
        self.is_denoised_mode = True

        # 4. 强制清理并刷新
        self.applied_other_mode = None
        self.plotter.clear_actors()
        QApplication.processEvents()
        self.global_refresh()
        if self.page_image.switch_coord.isChecked():
            self._rebuild_interactive_box(self.clip_ranges)

    def on_load(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择数据文件", "", "Supported Data (*.npz *.mat);;NumPy Files (*.npz);;MATLAB Files (*.mat)")
        if not path: return
        self.load_data(path)

    def update_ax_slider_range(self):
        if self.core.raw_data is None: return
        axis_idx = self.page_data.combo_ax.currentIndex()
        max_val = self.core.raw_data.shape[axis_idx] - 1
        axis_labels = {0: "X", 1: "Y", 2: "Z"}
        tooltip_func = lambda value, idx=axis_idx: f"{axis_labels.get(idx, 'Axis')}: {self.core.logical_to_physical(idx, value):.2f}"

        # 所有的滑块范围保持一致
        for s in [self.page_data.s_ax_low, self.page_data.s_ax_up, self.page_data.s_ax_mid]:
            s.setRange(0, max_val)
            s.setToolTipConvertionFunc(tooltip_func)

    def on_cut(self):
        """指挥核心处理坐标字符串并刷新"""
        texts = self.page_image.get_slice_values()
        if self.precise_logical_bounds is not None and texts == self.last_synced_slice_texts:
            logical_texts = self._logical_bounds_to_texts(self.precise_logical_bounds)
        else:
            logical_texts = self.core.physical_texts_to_logical_texts(texts)

        res = self.core.process_cut_logic(logical_texts)
        if res:
            self.clip_ranges = res.get("clip_ranges")
            self._sync_slice_edits_from_logical_bounds(res.get("logical_bounds"))
            self.global_refresh()

    def on_toggle_interactive_box(self, checked):
        if checked and self.core.raw_data is not None:
            self._rebuild_interactive_box()
            self._sync_slice_edits_from_logical_bounds(self.clip_ranges)
        else:
            self.plotter.clear_box_widgets()
        self.plotter.render()
        self._update_export_button_states()

    def on_back(self):
        # 1. 状态回退逻辑
        if self.back_click_count == 0:
            self.core.is_2d_mode = False
            self.mode_1d = None
            self.base_current_display_data = None
            self._apply_display_state()
            self.left_display_stack.setCurrentIndex(0)
            self.back_click_count = 1
        elif self.back_click_count == 1:
            self.clip_ranges = None
            self.core.is_2d_mode = False
            self.mode_1d = None
            self.base_current_display_data = None
            self._apply_display_state()
            self.left_display_stack.setCurrentIndex(0)
            self.left_display_stack.setCurrentIndex(0)
            self.back_click_count = 1  # 保持在1
        elif self.back_click_count == 2:
            if hasattr(self, 'original_raw_data'):
                self.base_raw_data = self.original_raw_data.copy()
                self.base_coords = self._clone_coords(self.original_coords)
            self.core.is_2d_mode = False
            self.mode_1d = None
            self.base_current_display_data = None
            self._apply_display_state()
            self.left_display_stack.setCurrentIndex(0)
            self.back_click_count = 1
        elif self.back_click_count == 3:
            self.core.is_2d_mode = False
            self.mode_1d = None
            self.base_current_display_data = None
            self._apply_display_state()
            self.left_display_stack.setCurrentIndex(0)
            self.back_click_count = 2


        # 2. 彻底清场
        self.plotter.clear_actors()
        self.plotter.clear_box_widgets()

        # 3. 重新加载 3D 数据本体
        self.global_refresh()

        # 4. 如果开关开着，手动“重启”交互盒
        if self.page_image.switch_coord.isChecked():
            # 强制系统处理完前面的“清除”指令，防止句柄冲突
            QApplication.processEvents()

            # 重新根据最新的 clip_ranges 画盒子
            self._rebuild_interactive_box()

        self._sync_slice_edits_from_logical_bounds(self.clip_ranges)

        # 5. 视角和渲染
        if self.clip_ranges is None:
            self.plotter.reset_camera()
        self.plotter.render()

    def on_screenshot(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存截图", "capture.png", "PNG (*.png)")
        if path:
            if self.left_display_stack.currentIndex() == 1:
                self.fig.savefig(path)
            else:
                self.plotter.screenshot(path)

    def on_save_cube_data(self):
        if self.base_raw_data is None or self.mode_1d is not None:
            return

        clip_info = self._get_clip_slices()
        if clip_info is None:
            msg = QMessageBox(self)
            msg.setWindowTitle("未进行切片设置")
            msg.setIcon(QMessageBox.Warning)
            msg.setText("请先进行切片设置")
            msg.exec_()
            return

        slices, _ = clip_info
        sample = self.base_raw_data[slices[0], slices[1], slices[2], :]
        if not self.core.has_time_axis:
            sample = sample[..., 0]

        export_data = {
            "sample": np.asarray(sample, dtype=np.float32),
            "kx": np.asarray(self.base_coords["X"][slices[0]], dtype=np.float32),
            "ky": np.asarray(self.base_coords["Y"][slices[1]], dtype=np.float32),
            "E": np.asarray(self.base_coords["E"][slices[2]], dtype=np.float32),
            "time": np.asarray(self.base_coords["delay"], dtype=np.float32),
        }

        path = self._choose_export_path("保存切片立方体数据", "slice_cube.mat")
        if not path:
            return

        self._save_dict_to_path(path, export_data)

    def on_save_other_integral(self):
        if self.base_raw_data is None or self.applied_other_mode is None:
            return

        if self.applied_other_mode == "Slice-DOS":
            if self.clip_ranges is None:
                return
            clip_info = self._get_clip_slices()
            if clip_info is None:
                return
            slices, _ = clip_info
            intensity = np.sum(self.base_raw_data[slices[0], slices[1], slices[2], :], axis=(0, 1, 2))
            export_data = {
                "time": np.asarray(self.base_coords["delay"], dtype=np.float32),
                "intensity": np.asarray(intensity, dtype=np.float32),
            }
            default_name = "slice_dos.mat"
        else:
            intensity = np.sum(self.base_raw_data, axis=(0, 1)).T
            export_data = {
                "time": np.asarray(self.base_coords["delay"], dtype=np.float32),
                "E": np.asarray(self.base_coords["E"], dtype=np.float32),
                "intensity": np.asarray(intensity, dtype=np.float32),
            }
            default_name = "energy_dos.mat"

        path = self._choose_export_path("保存积分结果", default_name)
        if not path:
            return

        self._save_dict_to_path(path, export_data)

    def on_other_mode_selection_changed(self, _):
        self.applied_other_mode = None
        self._update_export_button_states()

    def on_apply_time_integral(self):
        if self.core.raw_data is None or not self.core.has_time_axis: return
        if self.is_denoised_mode:
            self.back_click_count = 3  # 重置计数器状态
        else:
            self.back_click_count = 0

        low = self.page_data.s_t_low.value()
        up = self.page_data.s_t_up.value()

        # 获取积分数据
        self.base_current_display_data = np.sum(self.base_raw_data[:, :, :, low:up + 1], axis=3)
        self._apply_display_state()

        # 切换到 3D 视图刷新
        self.global_refresh()


    def sync_ax_sliders_to_box(self):
        """滑块 -> Box 的单向联动"""
        if self.page_image.switch_coord.isChecked() and self.core.raw_data is not None:
            # 1. 直接获取当前选的是第几个轴 (0, 1, 2)
            axis_idx = self.page_data.combo_ax.currentIndex()
            low = self.page_data.s_ax_low.value()
            up = self.page_data.s_ax_up.value()

            # 2. 获取基础范围
            shape = self.core.raw_data.shape
            # 注意：这里的 r 长度必须是 6
            r = list(self.clip_ranges) if self.clip_ranges else list(self._get_full_logical_bounds())

            # 3. 根据索引精准覆盖 (基于 Kx, Ky, E, T 的重排顺序)
            if axis_idx == 0:     # X轴 (Kx)
                r[0], r[1] = low, up
            elif axis_idx == 1:   # Y轴 (Ky)
                r[2], r[3] = low, up
            elif axis_idx == 2:   # 能量轴 (E)
                r[4], r[5] = low, up

            # 4. 重新绘制
            current_bounds = list(self.precise_logical_bounds) if self.precise_logical_bounds is not None else None
            if current_bounds == r:
                return

            self._rebuild_interactive_box(r)
            self._sync_slice_edits_from_logical_bounds(r)

    def schedule_axis_refresh(self):
        if self.core.raw_data is None:
            return

        self.axis_refresh_timer.start()

    def flush_axis_refresh(self):
        if self.core.raw_data is None:
            return

        if self.axis_refresh_timer.isActive():
            self.axis_refresh_timer.stop()
        self.auto_refresh_integral()

    def on_axis_bound_released(self):
        if self.core.raw_data is None:
            return

        if self.axis_refresh_timer.isActive():
            self.axis_refresh_timer.stop()
        self.auto_refresh_integral()

    def on_apply_axis_integral(self):
        """点击应用：执行坐标轴积分并显示 2D 投影图"""
        if self.core.raw_data is None: return
        self.back_click_count = 0  # 重置计数器状态

        # 标记当前进入 2D 投影模式
        self.core.is_2d_mode = True

        # 封装切片信息给 VisualEngine 使用
        # 这里的 axis 对应积分掉的那个轴，index 用于标识这是“积分投影”
        self.core.slice_info = {"axis": self.page_data.combo_ax.currentIndex(), "mode": "integral",
            "range": (self.page_data.s_ax_low.value(), self.page_data.s_ax_up.value())}

        self.global_refresh()

    def auto_refresh_integral(self):
        # --- 逻辑 A：同步 3D 交互盒 (只要开启了开关，不受 is_2d_mode 限制) ---
        if self.page_image.switch_coord.isChecked():
            self.sync_ax_sliders_to_box()

        # --- 逻辑 B：刷新 2D 投影图像 (只有在点击了应用按钮后才执行) ---
        if self.core.is_2d_mode and hasattr(self.core, "slice_info"):
            if self.core.slice_info.get("mode") == "integral":
                self.core.slice_info["range"] = (self.page_data.s_ax_low.value(), self.page_data.s_ax_up.value())
                self.global_refresh()

    def on_apply_other_integral(self):
        if self.core.raw_data is None: return

        self.applied_other_mode = None
        self._update_export_button_states()
        text = self.page_data.combo_other.currentText()

        if text == "切片态密度":
            if not self.core.has_time_axis:
                msg = QMessageBox(self)
                msg.setWindowTitle("静谱数据")
                msg.setIcon(QMessageBox.Information)
                msg.setText("当前数据不包含时间轴，无法计算切片态密度")
                msg.exec_()
                return
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
            self.back_click_count = 0
            self.mode_1d = "Slice-DOS"
        elif text == "能级态密度":
            self.mode_1d = "Energy-DOS"

        self.core.is_2d_mode = True  # 借用 2D 视图容器（Matplotlib 窗口）
        self.applied_other_mode = self.mode_1d
        self._update_export_button_states()

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
            self.ax_2d.set_xlim(float(x_data[0]), float(x_data[-1]))
            if self.core.has_time_axis:
                self.ax_2d.set_title(f"Energy DOS (T={self.core.coords['delay'][t_idx]:.3f})", color='white')
            else:
                self.ax_2d.set_title("Energy DOS (Static)", color='white')
            self.ax_2d.set_xlabel("Energy (eV)", color='white')

        self.ax_2d.set_ylabel("Intensity (a.u.)", color='white')
        self.ax_2d.tick_params(colors='white')
        self.fig.tight_layout()
        self.canvas_2d.draw()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    icon_path = resource_path("app.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    window = My3DAnalyzer()
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))
    window.showMaximized()
    sys.exit(app.exec_())
