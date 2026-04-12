import os
import re
import uuid
from pathlib import Path

import numpy as np
from PyQt5.QtCore import QEvent, QTimer, Qt
from PyQt5.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from pyvistaqt import QtInteractor
from scipy.io import savemat
from siui.components.button import SiCapsuleButton
from siui.components.tooltip import ToolTipWindow
from siui.core import SiGlobal

from analyzer_core import AnalyzerCore
from blank_control_page import BlankControlPage
from data_trans import convert as convert_mat_to_npz
from page_data_process_v2 import DataProcessPage
from page_image_control_v2 import ImageControlPage
from page_render_control import RenderControlPage
from render_core import VisualEngine
from result_workspace import AnalysisPageSpec, ResultWorkspace


class QuickCloseMessageBox(QMessageBox):
    def showEvent(self, event):
        super().showEvent(event)
        self._install_right_click_filters()

    def _install_right_click_filters(self):
        self.installEventFilter(self)
        for child in self.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.RightButton:
            self.reject()
            return True
        return super().eventFilter(watched, event)


class My3DAnalyzer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowOpacity(0)

        self.core = AnalyzerCore()
        self.base_raw_data = None
        self.base_coords = None
        self.original_raw_data = None
        self.original_coords = None
        self.clip_ranges = None
        self.home_slice_info = None
        self.precise_logical_bounds = None
        self.last_synced_slice_texts = None
        self.active_page_spec = None
        self._syncing_controls = False
        self.axis_source_mode = "frame"
        self.loaded_npz_stem = "data"

        self.axis_refresh_timer = QTimer(self)
        self.axis_refresh_timer.setSingleShot(True)
        self.axis_refresh_timer.setInterval(40)
        self.axis_refresh_timer.timeout.connect(self.auto_refresh_integral)

        if "TOOL_TIP" not in SiGlobal.siui.windows:
            SiGlobal.siui.windows["TOOL_TIP"] = ToolTipWindow()
            SiGlobal.siui.windows["TOOL_TIP"].show()
            SiGlobal.siui.windows["TOOL_TIP"].setOpacity(0)
        self._apply_feedback_styles()

        self.setWindowTitle("3D 能带分析工具")
        self.resize(1550, 950)
        self.setStyleSheet("background-color: #151525;")

        self.init_ui()
        self._install_data_process_save_controls()
        self.bind_all_events()
        self._initialize_result_workspace()
        self._update_export_button_states()

    def _apply_feedback_styles(self):
        tooltip_window = SiGlobal.siui.windows.get("TOOL_TIP")
        if tooltip_window is None:
            return

        tooltip_window.bg_label.setColor("#2A2A3A")
        tooltip_window.text_label.setStyleSheet("color: #FFFFFF; padding: 8px;")
        tooltip_window.highlight_mask.setFixedStyleSheet("border-radius: 6px")

    def showEvent(self, event):
        super().showEvent(event)
        if not hasattr(self, "_initialized_layout"):
            QTimer.singleShot(50, self.run_brute_force_layout)
            self._initialized_layout = True

    def run_brute_force_layout(self):
        self.showMaximized()
        self.page_container.setCurrentIndex(1)
        self.showNormal()
        self.page_container.setCurrentIndex(2)
        self.showMaximized()
        self.page_container.setCurrentIndex(0)
        self.btn_page1.setChecked(True)
        self.updateGeometry()
        self.setWindowOpacity(1)
        self.activateWindow()
        self.raise_()

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        self.left_display_stack = QStackedWidget()
        self.left_display_stack.setStyleSheet("background-color: #1A1A2E; border-radius: 12px;")

        self.plotter = QtInteractor(self.left_display_stack)
        self.plotter.set_background("#1A1A2E")
        self.left_display_stack.addWidget(self.plotter)

        self.fig = Figure(figsize=(5, 4), dpi=100, facecolor="#1A1A2E")
        self.canvas_2d = FigureCanvas(self.fig)
        self.ax_2d = self.fig.add_subplot(111)
        self.left_display_stack.addWidget(self.canvas_2d)

        self.page_control_blank = BlankControlPage(self.left_display_stack)
        self.left_display_stack.addWidget(self.page_control_blank)

        self.left_workspace = ResultWorkspace(self.left_display_stack, self)
        main_layout.addWidget(self.left_workspace, stretch=7)

        self.right_panel = QFrame()
        self.right_panel.setStyleSheet("background-color: #2A2A3A; border-radius: 12px;")
        right_vbox = QVBoxLayout(self.right_panel)
        right_vbox.setContentsMargins(15, 10, 15, 15)
        right_vbox.setSpacing(10)

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
        for btn in [self.btn_page1, self.btn_page2, self.btn_page3]:
            self.button_group.addButton(btn)
            nav_layout.addWidget(btn)
        self.button_group.setExclusive(True)

        nav_layout.addStretch()
        right_vbox.addWidget(nav_group)

        self.page_container = QStackedWidget()
        self.page_image = ImageControlPage()
        self.page_render = RenderControlPage()
        self.page_data = DataProcessPage()

        self.page_container.addWidget(self.page_image)
        self.page_container.addWidget(self.page_render)
        self.page_container.addWidget(self.page_data)

        right_vbox.addWidget(self.page_container)
        main_layout.addWidget(self.right_panel, stretch=4)

    def _install_data_process_save_controls(self):
        if hasattr(self.page_data, "btn_left_view_save") and hasattr(self.page_data, "btn_view_data_save"):
            return

        legacy_save_btn = getattr(self.page_data, "btn_other_save", None)
        if legacy_save_btn is not None:
            legacy_layout = legacy_save_btn.parentWidget().layout() if legacy_save_btn.parentWidget() is not None else None
            if legacy_layout is not None:
                legacy_layout.removeWidget(legacy_save_btn)
            legacy_save_btn.hide()
            legacy_save_btn.setParent(None)

        self.page_data.btn_left_view_save = self.page_data._create_red_btn("左侧视图保存")
        self.page_data.btn_view_data_save = self.page_data._create_red_btn("视图数据保存")
        self.page_data.btn_left_view_save.setFixedWidth(130)
        self.page_data.btn_view_data_save.setFixedWidth(130)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save_row.addWidget(self.page_data.btn_left_view_save)
        save_row.addSpacing(12)
        save_row.addWidget(self.page_data.btn_view_data_save)
        save_row.addStretch()

        insert_index = max(self.page_data.vbox.count() - 1, 0)
        self.page_data.vbox.insertLayout(insert_index, save_row)

    def bind_all_events(self):
        self.btn_page1.clicked.connect(lambda: self._select_control_page(0))
        self.btn_page2.clicked.connect(lambda: self._select_control_page(1))
        self.btn_page3.clicked.connect(lambda: self._select_control_page(2))

        self.page_image.btn_load.clicked.connect(self.on_load)
        self.page_image.btn_cut.clicked.connect(self.on_cut)
        self.page_image.btn_export.clicked.connect(self.export_current_result)
        self.page_image.btn_save.clicked.connect(self.on_screenshot)
        self.page_image.btn_back.clicked.connect(self.on_back)
        self.page_image.slider_time.valueChanged.connect(self.global_refresh)
        self.page_image.slider_time.valueChanged.connect(self.on_image_time_changed)
        self.page_image.switch_axes.toggled.connect(self.global_refresh)
        self.page_image.switch_coord.toggled.connect(self.on_toggle_interactive_box)
        self.page_image.switch_flip.toggled.connect(self.on_toggle_e_flip)

        self.page_render.btn_apply_cmap.clicked.connect(self.global_refresh)
        self.page_render.btn_apply_map.clicked.connect(self.global_refresh)
        self.page_render.btn_apply_noise.clicked.connect(self.on_apply_denoise)

        self.page_data.combo_ax.currentIndexChanged.connect(self.update_ax_slider_range)
        self.page_data.btn_t_apply.clicked.connect(self.on_apply_time_integral)
        self.page_data.s_t_low.valueChanged.connect(self.on_time_integral_controls_changed)
        self.page_data.s_t_up.valueChanged.connect(self.on_time_integral_controls_changed)
        self.page_data.s_ax_low.valueChanged.connect(self.schedule_axis_refresh)
        self.page_data.s_ax_up.valueChanged.connect(self.schedule_axis_refresh)
        self.page_data.s_ax_mid.valueChanged.connect(self.schedule_axis_refresh)
        self.page_data.s_ax_low.sliderReleased.connect(self.on_axis_bound_released)
        self.page_data.s_ax_up.sliderReleased.connect(self.on_axis_bound_released)
        self.page_data.s_ax_mid.sliderReleased.connect(self.flush_axis_refresh)
        self.page_data.btn_ax_apply.clicked.connect(self.on_apply_axis_integral)
        self.page_data.btn_other_apply.clicked.connect(self.on_apply_other_integral)
        self.page_data.btn_left_view_save.clicked.connect(self.on_screenshot)
        self.page_data.btn_view_data_save.clicked.connect(self.export_current_result)
        self.page_data.combo_other.currentIndexChanged.connect(self.on_other_mode_selection_changed)

        self.left_workspace.page_activated.connect(self.on_result_page_activated)

    def _initialize_result_workspace(self):
        home_spec = AnalysisPageSpec(
            page_id="home",
            title="原始视图",
            page_kind="home",
            source_module="system",
            closeable=False,
        )
        self.left_workspace.set_home_page(home_spec)
        self.left_workspace.add_pinned_page(
            AnalysisPageSpec(
                page_id="control_panel",
                title="鍘诲櫔鍙傛暟",
                page_kind="control_panel",
                source_module="system",
                closeable=False,
            ),
            activate=False,
        )
        self.left_workspace.update_page("control_panel", title="控件页")
        self.active_page_spec = home_spec

    def _select_control_page(self, index):
        self.page_container.setCurrentIndex(index)
        [self.btn_page1, self.btn_page2, self.btn_page3][index].setChecked(True)

    def _clone_coords(self, coords=None):
        source = coords if coords is not None else self.core.coords
        return {
            key: None if value is None else np.array(value, copy=True)
            for key, value in source.items()
        }

    def _refresh_core_display_state(self):
        if self.base_raw_data is None or self.base_coords is None:
            return

        display_raw = np.array(self.base_raw_data, copy=True)
        display_coords = self._clone_coords(self.base_coords)

        if self.page_image.switch_flip.isChecked():
            display_raw = np.flip(display_raw, axis=2)
            display_coords["E"] = np.flip(display_coords["E"])

        self.core.raw_data = display_raw
        self.core.coords = display_coords

    def _get_full_logical_bounds(self):
        if self.core.raw_data is None:
            return None

        shape = self.core.raw_data.shape[:3]
        return [
            0.0,
            max(shape[0] - 1, 0),
            0.0,
            max(shape[1] - 1, 0),
            0.0,
            max(shape[2] - 1, 0),
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
        if self.core.raw_data is None:
            return

        logical_bounds = self.core.render_to_logical_bounds(render_bounds, self.core.raw_data.shape[:3])
        self._sync_slice_edits_from_logical_bounds(logical_bounds)

    def _get_render_bounds_for_box(self, logical_bounds=None):
        if self.core.raw_data is None:
            return None

        bounds = logical_bounds if logical_bounds is not None else self.clip_ranges
        if bounds is None:
            bounds = self._get_full_logical_bounds()

        return self.core.logical_to_render_bounds(bounds, self.core.raw_data.shape[:3])

    def _can_show_interactive_box(self):
        active_spec = self.left_workspace.current_spec()
        return (
            active_spec is not None
            and active_spec.page_kind == "home"
            and self.page_image.switch_coord.isChecked()
            and self.core.raw_data is not None
            and self.left_display_stack.currentIndex() == 0
        )

    def _rebuild_interactive_box(self, logical_bounds=None):
        if not self._can_show_interactive_box():
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
            rotation_enabled=False,
        )

    def _get_display_levels(self):
        return (
            self.page_render.s_low.value(),
            self.page_render.s_gamma.value(),
            self.page_render.s_up.value(),
        )

    def _current_delay_text(self, t_index):
        delays = self.core.coords.get("delay")
        if delays is None or len(delays) == 0:
            return str(t_index)
        safe_index = min(max(int(t_index), 0), len(delays) - 1)
        return f"{float(delays[safe_index]):.3f}"

    def _make_page_id(self):
        return uuid.uuid4().hex

    def _get_clip_slices(self, logical_bounds=None):
        if self.core.raw_data is None:
            return None

        bounds = logical_bounds if logical_bounds is not None else self.clip_ranges
        if bounds is None:
            return None

        slices = []
        index_bounds = []
        shape = self.core.raw_data.shape[:3]

        for axis_idx in range(3):
            axis_max = max(shape[axis_idx] - 1, 0)
            low = float(bounds[axis_idx * 2])
            up = float(bounds[axis_idx * 2 + 1])
            low_idx = int(np.clip(np.floor(min(low, up)), 0, axis_max))
            up_idx = int(np.clip(np.ceil(max(low, up)), 0, axis_max))
            slices.append(slice(low_idx, up_idx + 1))
            index_bounds.extend([low_idx, up_idx])

        return tuple(slices), index_bounds

    def _create_message_box(self, title, text, icon=QMessageBox.Information, buttons=QMessageBox.Ok, default_button=QMessageBox.NoButton, escape_button=None):
        msg = QuickCloseMessageBox(self)
        msg.setWindowTitle(title)
        msg.setIcon(icon)
        msg.setText(text)
        msg.setStandardButtons(buttons)
        if default_button != QMessageBox.NoButton:
            msg.setDefaultButton(default_button)
        if escape_button is not None:
            msg.setEscapeButton(escape_button)
        return msg

    def _show_message(self, title, text, icon=QMessageBox.Information):
        msg = self._create_message_box(title, text, icon, buttons=QMessageBox.Ok, default_button=QMessageBox.Ok, escape_button=QMessageBox.Ok)
        msg.exec_()

    def _confirm_create_axis_page(self):
        msg = self._create_message_box(
            "是否新增页面？",
            "当前坐标轴积分页面参数已经变化。是否新增一个结果页面？",
            QMessageBox.Question,
            buttons=QMessageBox.Yes | QMessageBox.No,
            default_button=QMessageBox.No,
            escape_button=QMessageBox.No,
        )
        return msg.exec_() == QMessageBox.Yes

    @staticmethod
    def _sanitize_save_path(path, selected_filter=""):
        root, ext = os.path.splitext(path)
        if ext.lower() not in {".mat", ".npz", ".png"}:
            if "npz" in selected_filter.lower():
                return path + ".npz"
            if "png" in selected_filter.lower():
                return path + ".png"
            return path + ".mat"
        return path

    def _choose_export_path(self, title, default_name):
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            title,
            default_name,
            "MATLAB Files (*.mat);;NumPy Files (*.npz)",
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

    def _is_time_integral_axis_page(self, spec):
        return (
            spec is not None
            and spec.page_kind == "axis_integral"
            and self._normalize_axis_source_mode(spec.params.get("source_mode")) == "time_integral"
        )

    def _update_time_slider_state(self):
        has_time_axis = self.core.has_time_axis and self.core.raw_data is not None and self.core.raw_data.shape[3] > 1
        active_spec = self.left_workspace.current_spec()
        self.page_image.slider_time.setEnabled(has_time_axis and not self._is_time_integral_axis_page(active_spec))

    def _configure_time_controls(self):
        has_time_axis = self.core.has_time_axis and self.core.raw_data is not None and self.core.raw_data.shape[3] > 1
        self._update_time_slider_state()
        self.page_data.s_t_low.setEnabled(has_time_axis)
        self.page_data.s_t_up.setEnabled(has_time_axis)
        self.page_data.btn_t_apply.setEnabled(has_time_axis)

    def _update_export_button_states(self):
        has_data = self.base_raw_data is not None
        active_spec = self.left_workspace.current_spec()
        can_capture = active_spec is not None
        can_export = has_data and active_spec is not None and active_spec.page_kind != "control_panel"
        self.page_image.btn_export.setEnabled(can_export)
        self.page_data.btn_left_view_save.setEnabled(can_capture)
        self.page_data.btn_view_data_save.setEnabled(can_export)

    @staticmethod
    def _format_filename_number(value):
        numeric_value = float(value)
        if abs(numeric_value) < 5e-7:
            numeric_value = 0.0
        text = f"{numeric_value:.2f}".rstrip("0").rstrip(".")
        return text or "0"

    @staticmethod
    def _sanitize_filename_component(text):
        cleaned = re.sub(r"\s+", "_", str(text).strip())
        cleaned = re.sub(r'[<>:"/\\|?*]+', "_", cleaned)
        cleaned = cleaned.strip("._")
        return cleaned or "data"

    def _get_loaded_npz_stem(self):
        return self._sanitize_filename_component(self.loaded_npz_stem)

    def _build_time_integral_default_name(self, t_low, t_up):
        low_text = self._format_filename_number(self.core.logical_to_physical("delay", t_low))
        up_text = self._format_filename_number(self.core.logical_to_physical("delay", t_up))
        return self._sanitize_filename_component(f"{low_text}_{up_text}_t_{self._get_loaded_npz_stem()}")

    def _get_axis_integral_export_context(self, spec):
        if self.core.raw_data is None:
            return None

        if self._is_current_page(spec):
            axis_index = int(self.page_data.combo_ax.currentIndex())
            low = int(self.page_data.s_ax_low.value())
            up = int(self.page_data.s_ax_up.value())
            mid = int(self.page_data.s_ax_mid.value())
        else:
            axis_index = int(spec.params["axis_index"])
            low = int(spec.params["low"])
            up = int(spec.params["up"])
            mid = int(spec.params.get("mid", round((low + up) / 2)))

        if low > up:
            low, up = up, low

        axis_key = {0: "X", 1: "Y", 2: "E"}.get(axis_index, "X")
        axis_tag = {0: "x", 1: "y", 2: "z"}.get(axis_index, "x")
        axis_max = self.core.raw_data.shape[axis_index] - 1
        low = int(np.clip(low, 0, axis_max))
        up = int(np.clip(up, 0, axis_max))
        mid = int(np.clip(mid, low, up))

        if axis_index == 0:
            sample = np.sum(self.core.raw_data[low:up + 1, :, :, :], axis=0)
            export_data = {
                "sample": np.asarray(sample, dtype=np.float32),
                "ky": np.asarray(self.core.coords["Y"], dtype=np.float32),
                "E": np.asarray(self.core.coords["E"], dtype=np.float32),
                "time": np.asarray(self.core.coords["delay"], dtype=np.float32),
            }
        elif axis_index == 1:
            sample = np.sum(self.core.raw_data[:, low:up + 1, :, :], axis=1)
            export_data = {
                "sample": np.asarray(sample, dtype=np.float32),
                "kx": np.asarray(self.core.coords["X"], dtype=np.float32),
                "E": np.asarray(self.core.coords["E"], dtype=np.float32),
                "time": np.asarray(self.core.coords["delay"], dtype=np.float32),
            }
        else:
            sample = np.sum(self.core.raw_data[:, :, low:up + 1, :], axis=2)
            export_data = {
                "sample": np.asarray(sample, dtype=np.float32),
                "kx": np.asarray(self.core.coords["X"], dtype=np.float32),
                "ky": np.asarray(self.core.coords["Y"], dtype=np.float32),
                "time": np.asarray(self.core.coords["delay"], dtype=np.float32),
            }

        low_text = self._format_filename_number(self.core.logical_to_physical(axis_key, low))
        up_text = self._format_filename_number(self.core.logical_to_physical(axis_key, up))
        mid_text = self._format_filename_number(self.core.logical_to_physical(axis_key, mid))
        default_name = self._sanitize_filename_component(
            f"{low_text}_{up_text}_{mid_text}_{axis_tag}_{self._get_loaded_npz_stem()}"
        )

        return {"export_data": export_data, "default_name": default_name}

    def _extract_slice_data(self, data_3d, axis_idx, index):
        safe_index = int(np.clip(index, 0, data_3d.shape[axis_idx] - 1))
        if axis_idx == 0:
            return data_3d[safe_index, :, :]
        if axis_idx == 1:
            return data_3d[:, safe_index, :]
        return data_3d[:, :, safe_index]

    def _get_home_render_context(self):
        if self.core.raw_data is None:
            return None

        t_idx = int(self.page_image.slider_time.value())
        data_3d = self.core.get_data_for_t(t_idx)

        if self.home_slice_info is not None:
            data_2d = self._extract_slice_data(data_3d, self.home_slice_info["axis"], self.home_slice_info["index"])
            return {"view": "2d", "data": data_2d, "slice_info": self.home_slice_info}

        return {"view": "3d", "data": data_3d, "clip_ranges": self.clip_ranges}

    def _is_current_page(self, spec):
        current_spec = self.left_workspace.current_spec()
        return current_spec is not None and current_spec.page_id == spec.page_id

    def _normalize_axis_source_mode(self, mode):
        if not self.core.has_time_axis:
            return "frame"
        return "time_integral" if mode == "time_integral" else "frame"

    def _persist_time_integral_page_state(self, spec=None):
        target_spec = spec or self.left_workspace.current_spec()
        if target_spec is None or target_spec.page_kind != "time_integral":
            return

        target_spec.params["t_low"] = int(self.page_data.s_t_low.value())
        target_spec.params["t_up"] = int(self.page_data.s_t_up.value())

    def _build_axis_request_params(self, source_mode=None):
        axis_index = self.page_data.combo_ax.currentIndex()
        axis_name = ["X轴", "Y轴", "Z轴"][axis_index]
        normalized_source_mode = self._normalize_axis_source_mode(source_mode or self.axis_source_mode)

        return {
            "axis_index": axis_index,
            "axis_name": axis_name,
            "low": int(self.page_data.s_ax_low.value()),
            "up": int(self.page_data.s_ax_up.value()),
            "mid": int(self.page_data.s_ax_mid.value()),
            "source_mode": normalized_source_mode,
            "source_page_kind": "time_integral" if normalized_source_mode == "time_integral" else "home",
            "source_t_index": int(self.page_image.slider_time.value()),
            "source_t_low": int(self.page_data.s_t_low.value()),
            "source_t_up": int(self.page_data.s_t_up.value()),
        }

    @staticmethod
    def _axis_request_signature(title, params):
        source_mode = params.get("source_mode", "frame")
        signature = [
            title,
            int(params["axis_index"]),
            int(params["low"]),
            int(params["up"]),
            source_mode,
        ]
        if source_mode == "time_integral":
            signature.extend([int(params["source_t_low"]), int(params["source_t_up"])])
        else:
            signature.append(int(params["source_t_index"]))
        return tuple(signature)

    def _is_same_axis_request(self, spec, candidate_spec):
        if spec is None or spec.page_kind != "axis_integral":
            return False
        return self._axis_request_signature(spec.title, spec.params) == self._axis_request_signature(candidate_spec.title, candidate_spec.params)

    def _overwrite_axis_page(self, current_spec, candidate_spec):
        self.left_workspace.update_page(
            current_spec.page_id,
            title=candidate_spec.title,
            params=candidate_spec.params,
            source_page_id=candidate_spec.source_page_id,
        )
        self.on_result_page_activated(current_spec.page_id)

    def _get_3d_source_context_for_axis(self, spec):
        params = spec.params
        source_mode = self._normalize_axis_source_mode(params.get("source_mode"))
        if source_mode == "time_integral":
            t_low = params.get("source_t_low")
            t_up = params.get("source_t_up")
            if t_low is not None and t_up is not None:
                return {"view": "3d", "data": self.core.get_time_integrated_data(int(t_low), int(t_up))}

        source_kind = params.get("source_page_kind", "home")
        if source_kind == "time_integral":
            source_spec = self.left_workspace.page_by_id(spec.source_page_id)
            if source_spec is not None:
                return self._get_time_integral_context(source_spec)

        t_index = int(params.get("source_t_index", int(self.page_image.slider_time.value())))
        return {"view": "3d", "data": self.core.get_data_for_t(t_index)}

    def _get_time_integral_context(self, spec):
        if self._is_current_page(spec):
            self._persist_time_integral_page_state(spec)

        t_low = int(spec.params["t_low"])
        t_up = int(spec.params["t_up"])
        return {"view": "3d", "data": self.core.get_time_integrated_data(t_low, t_up)}

    def _get_axis_integral_context(self, spec):
        if self._is_current_page(spec):
            source_mode = self._normalize_axis_source_mode(self.axis_source_mode)
            if source_mode == "time_integral":
                source_context = {
                    "view": "3d",
                    "data": self.core.get_time_integrated_data(
                        int(self.page_data.s_t_low.value()),
                        int(self.page_data.s_t_up.value()),
                    ),
                }
            else:
                source_context = {
                    "view": "3d",
                    "data": self.core.get_data_for_t(int(self.page_image.slider_time.value())),
                }
            axis_index = int(self.page_data.combo_ax.currentIndex())
            low = int(self.page_data.s_ax_low.value())
            up = int(self.page_data.s_ax_up.value())
            axis_name = ["X轴", "Y轴", "Z轴"][axis_index]
        else:
            source_context = self._get_3d_source_context_for_axis(spec)
            axis_index = int(spec.params["axis_index"])
            low = int(spec.params["low"])
            up = int(spec.params["up"])
            axis_name = spec.params["axis_name"]
        data_2d = self.core.get_axis_integrated_data(source_context["data"], axis_name, low, up)
        return {
            "view": "2d",
            "data": data_2d,
            "slice_info": {"axis": axis_index, "mode": "integral", "range": (low, up)},
        }

    def _compute_slice_dos(self, logical_bounds):
        clip_info = self._get_clip_slices(logical_bounds)
        if clip_info is None:
            return None

        slices, _ = clip_info
        return np.sum(self.core.raw_data[slices[0], slices[1], slices[2], :], axis=(0, 1, 2))

    def _get_slice_dos_context(self, spec):
        return {
            "view": "1d",
            "x_data": self.core.coords["delay"],
            "y_data": self._compute_slice_dos(spec.params["clip_ranges"]),
            "title": "Slice Integrated Intensity vs Time",
            "xlabel": "Delay (ps)",
        }

    def _get_energy_dos_context(self, spec):
        if self._is_current_page(spec):
            t_index = int(self.page_image.slider_time.value())
        else:
            t_index = int(spec.params["t_index"])
        return {
            "view": "1d",
            "x_data": self.core.coords["E"],
            "y_data": self.core.get_energy_dos(t_index),
            "title": f"Energy DOS (T={self._current_delay_text(t_index)})",
            "xlabel": "Energy (eV)",
        }

    def _compute_render_context(self, spec):
        if spec.page_kind == "control_panel":
            return {"view": "config"}

        if self.core.raw_data is None:
            return None

        if spec.page_kind == "home":
            return self._get_home_render_context()
        if spec.page_kind == "time_integral":
            return self._get_time_integral_context(spec)
        if spec.page_kind == "axis_integral":
            return self._get_axis_integral_context(spec)
        if spec.page_kind == "slice_dos":
            return self._get_slice_dos_context(spec)
        if spec.page_kind == "energy_dos":
            return self._get_energy_dos_context(spec)
        return self._get_home_render_context()

    def _render_1d_plot(self, context):
        self.ax_2d.clear()
        self.ax_2d.plot(context["x_data"], context["y_data"], color="#FF69B4", linewidth=2)
        self.ax_2d.set_title(context["title"], color="white")
        self.ax_2d.set_xlabel(context["xlabel"], color="white")
        self.ax_2d.set_ylabel("Intensity (a.u.)", color="white")
        self.ax_2d.tick_params(colors="white")
        self.fig.tight_layout()
        self.canvas_2d.draw()

    def _render_active_page(self):
        spec = self.left_workspace.current_spec() or self.left_workspace.home_spec()
        if spec is None:
            return

        if spec.page_kind == "control_panel":
            self.left_display_stack.setCurrentIndex(2)
            self.plotter.clear_box_widgets()
            self.plotter.render()
            return

        if self.core.raw_data is None:
            self.left_display_stack.setCurrentIndex(0)
            self.plotter.clear_actors()
            self.plotter.clear_box_widgets()
            self.plotter.render()
            self.ax_2d.clear()
            self.canvas_2d.draw()
            return

        context = self._compute_render_context(spec)
        if context is None:
            return

        levels = self._get_display_levels()
        mapping_mode = self.page_render.combo_map.currentText()
        current_cmap = self.page_render.get_selected_cmap()

        if context["view"] == "3d":
            self.left_display_stack.setCurrentIndex(0)
            clip_ranges = context.get("clip_ranges")
            render_clip = None
            if clip_ranges is not None:
                render_clip = self.core.logical_to_render_bounds(clip_ranges, context["data"].shape)

            VisualEngine.render_3d(
                self.plotter,
                context["data"],
                levels,
                opac_mode=mapping_mode,
                clip_ranges=render_clip,
                show_axes=self.page_image.switch_axes.isChecked(),
                core_coords=self.core.coords,
                cmap=current_cmap,
            )
        elif context["view"] == "2d":
            self.left_display_stack.setCurrentIndex(1)
            self.plotter.clear_box_widgets()
            VisualEngine.render_2d_slice(
                self.ax_2d,
                self.canvas_2d,
                context["data"],
                context["slice_info"],
                levels,
                self.core.coords,
                cmap=current_cmap,
            )
        else:
            self.left_display_stack.setCurrentIndex(1)
            self.plotter.clear_box_widgets()
            self._render_1d_plot(context)

        if self._can_show_interactive_box():
            self._rebuild_interactive_box()
            self.plotter.render()
        else:
            self.plotter.clear_box_widgets()
            self.plotter.render()

    def global_refresh(self):
        self._render_active_page()
        self._update_export_button_states()

    def on_result_page_activated(self, page_id):
        spec = self.left_workspace.page_by_id(page_id)
        if spec is None:
            return

        self.active_page_spec = spec
        self._sync_controls_from_page(spec)
        self._update_time_slider_state()
        self.global_refresh()

    def _sync_controls_from_page(self, spec):
        self._syncing_controls = True
        try:
            if spec.page_kind == "home":
                self.axis_source_mode = "frame"
                return

            if spec.page_kind == "control_panel":
                self._select_control_page(1)
                return

            self._select_control_page(2)

            if spec.page_kind == "time_integral":
                self.page_data.s_t_low.setValue(spec.params["t_low"])
                self.page_data.s_t_up.setValue(spec.params["t_up"])
                self.axis_source_mode = "time_integral"
                return

            if spec.page_kind == "axis_integral":
                self.page_data.combo_ax.setCurrentIndex(spec.params["axis_index"])
                self.page_data.s_ax_low.setValue(spec.params["low"])
                self.page_data.s_ax_up.setValue(spec.params["up"])
                self.page_data.s_ax_mid.setValue(spec.params.get("mid", (spec.params["low"] + spec.params["up"]) // 2))
                source_mode = self._normalize_axis_source_mode(
                    spec.params.get(
                        "source_mode",
                        "time_integral" if spec.params.get("source_page_kind") == "time_integral" else "frame",
                    )
                )
                self.axis_source_mode = source_mode
                if source_mode == "frame":
                    self.page_image.slider_time.setValue(spec.params.get("source_t_index", self.page_image.slider_time.value()))
                else:
                    self.page_data.s_t_low.setValue(spec.params.get("source_t_low", self.page_data.s_t_low.value()))
                    self.page_data.s_t_up.setValue(spec.params.get("source_t_up", self.page_data.s_t_up.value()))
                return

            if spec.page_kind == "slice_dos":
                self.axis_source_mode = "frame"
                self.page_data.combo_other.setCurrentIndex(0)
                self.clip_ranges = list(spec.params["clip_ranges"])
                self.home_slice_info = None
                self._sync_slice_edits_from_logical_bounds(self.clip_ranges)
                return

            if spec.page_kind == "energy_dos":
                self.axis_source_mode = "frame"
                self.page_data.combo_other.setCurrentIndex(1)
                self.page_image.slider_time.setValue(spec.params["t_index"])
        finally:
            self._syncing_controls = False

    def on_toggle_e_flip(self, checked):
        if self.base_raw_data is None:
            return

        self._refresh_core_display_state()
        self.update_ax_slider_range()
        self._sync_slice_edits_from_logical_bounds(self.clip_ranges)
        self.global_refresh()

    def on_apply_denoise(self):
        if self.original_raw_data is None:
            return

        from denoise_engines import DenoiseEngines
        current_spec = self.left_workspace.current_spec()
        keep_control_panel = current_spec is not None and current_spec.page_kind == "control_panel"
        keep_page_id = current_spec.page_id if keep_control_panel else None

        try:
            methods = self.page_control_blank.build_method_specs(
                self.page_render.get_denoise_settings(),
                data_shape=self.original_raw_data.shape,
            )
        except ValueError as exc:
            self._show_message("Savitzky-Golay 参数无效", str(exc), QMessageBox.Warning)
            return

        temp_data = DenoiseEngines.apply_pipeline(self.original_raw_data, methods)

        if max(temp_data.shape[1:]) > 200:
            self.base_raw_data = temp_data[:, ::2, ::2, ::2]
            self.base_coords = self._clone_coords(self.original_coords)
            for key in ["X", "Y", "E"]:
                self.base_coords[key] = self.base_coords[key][::2]
        else:
            self.base_raw_data = temp_data
            self.base_coords = self._clone_coords(self.original_coords)

        self._refresh_core_display_state()
        self.update_ax_slider_range()
        self._sync_slice_edits_from_logical_bounds(self.clip_ranges)
        self.global_refresh()
        if keep_page_id is not None:
            self.left_workspace.activate_page(keep_page_id)

    def on_load(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择数据文件",
            "",
            "Supported Data (*.npz *.mat);;NumPy Files (*.npz);;MATLAB Files (*.mat)",
        )
        if not path:
            return
        self.load_data(path)

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
                self._show_message("MAT 转换失败", f"无法将 .mat 文件转换为 .npz：\n{exc}", QMessageBox.Critical)
                return

        success, info = self.core.load_npz(target_path, is_flip=False)
        if not success:
            self._show_message("数据加载失败", f"无法加载文件：\n{info}", QMessageBox.Critical)
            return

        self.loaded_npz_stem = Path(target_path).stem
        self.original_raw_data = np.array(self.core.raw_data, copy=True)
        self.original_coords = self._clone_coords()
        self.base_raw_data = np.array(self.original_raw_data, copy=True)
        self.base_coords = self._clone_coords(self.original_coords)
        self.clip_ranges = None
        self.home_slice_info = None
        self.axis_source_mode = "frame"
        self._refresh_core_display_state()

        t_max = info[3] - 1
        slider_max = max(t_max, 1)
        time_func = lambda value: f"Delay: {self.core.coords['delay'][min(int(value), len(self.core.coords['delay']) - 1)]:.4f} fs"

        self.page_image.slider_time.setRange(0, slider_max)
        self.page_image.slider_time.setValue(0)
        self.page_image.slider_time.setToolTipConvertionFunc(time_func)

        self.page_data.s_t_low.setRange(0, slider_max)
        self.page_data.s_t_up.setRange(0, slider_max)
        self.page_data.s_t_low.setValue(0)
        self.page_data.s_t_up.setValue(0 if t_max == 0 else t_max)
        self.page_data.s_t_low.setToolTipConvertionFunc(time_func)
        self.page_data.s_t_up.setToolTipConvertionFunc(time_func)

        self.update_ax_slider_range()
        self._sync_slice_edits_from_logical_bounds()
        self._configure_time_controls()
        self.left_workspace.reset_to_home()
        self.plotter.set_background("white")
        self.global_refresh()
        self.plotter.reset_camera()

    def update_ax_slider_range(self):
        if self.core.raw_data is None:
            return

        axis_idx = self.page_data.combo_ax.currentIndex()
        max_val = self.core.raw_data.shape[axis_idx] - 1
        axis_labels = {0: "X", 1: "Y", 2: "Z"}
        tooltip_func = lambda value, idx=axis_idx: f"{axis_labels.get(idx, 'Axis')}: {self.core.logical_to_physical(idx, value):.2f}"

        for slider in [self.page_data.s_ax_low, self.page_data.s_ax_up, self.page_data.s_ax_mid]:
            slider.setRange(0, max_val)
            slider.setToolTipConvertionFunc(tooltip_func)

    def on_cut(self):
        texts = self.page_image.get_slice_values()
        if self.precise_logical_bounds is not None and texts == self.last_synced_slice_texts:
            logical_texts = self._logical_bounds_to_texts(self.precise_logical_bounds)
        else:
            logical_texts = self.core.physical_texts_to_logical_texts(texts)

        result = self.core.process_cut_logic(logical_texts)
        if not result:
            return

        self.clip_ranges = result.get("clip_ranges")
        self.home_slice_info = result.get("slice_info")
        self._sync_slice_edits_from_logical_bounds(result.get("logical_bounds"))
        self.global_refresh()

    def on_back(self):
        if self.left_workspace.home_page_id is not None:
            self.left_workspace.activate_page(self.left_workspace.home_page_id)

        if self.core.raw_data is None:
            return

        self.clip_ranges = None
        self.home_slice_info = None
        self._sync_slice_edits_from_logical_bounds(self._get_full_logical_bounds())
        self.global_refresh()
        self.plotter.reset_camera()
        self.plotter.render()

    def on_toggle_interactive_box(self, checked):
        if checked and self._can_show_interactive_box():
            self._rebuild_interactive_box()
            self._sync_slice_edits_from_logical_bounds(self.clip_ranges)
        else:
            self.plotter.clear_box_widgets()
        self.plotter.render()

    def on_screenshot(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存截图", "capture.png", "PNG (*.png)")
        if not path:
            return

        save_path = self._sanitize_save_path(path, "PNG")
        current_index = self.left_display_stack.currentIndex()
        if current_index == 1:
            self.fig.savefig(save_path)
        elif current_index == 2:
            self.left_display_stack.currentWidget().grab().save(save_path)
        else:
            self.plotter.screenshot(save_path)

    def _build_time_integral_spec(self):
        low = self.page_data.s_t_low.value()
        up = self.page_data.s_t_up.value()
        return AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=f"时间积分 [{low}~{up}]",
            page_kind="time_integral",
            source_module="data_process",
            params={"t_low": low, "t_up": up},
        )

    def _resolve_axis_source(self):
        active_spec = self.left_workspace.current_spec() or self.left_workspace.home_spec()
        while active_spec is not None and active_spec.page_kind not in {"home", "time_integral"}:
            active_spec = self.left_workspace.page_by_id(active_spec.source_page_id)
        return active_spec or self.left_workspace.home_spec()

    def _build_axis_integral_spec(self, source_mode=None):
        params = self._build_axis_request_params(source_mode=source_mode)
        title = f"{params['axis_name']}积分 [{params['low']}~{params['up']}]"

        return AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=title,
            page_kind="axis_integral",
            source_module="data_process",
            params=params,
        )

    def _build_slice_dos_spec(self):
        clip_info = self._get_clip_slices(self.clip_ranges)
        if clip_info is None:
            return None

        _, index_bounds = clip_info
        x1, x2, y1, y2, z1, z2 = index_bounds
        return AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=f"Slice-DOS [{x1}:{x2}, {y1}:{y2}, {z1}:{z2}]",
            page_kind="slice_dos",
            source_module="data_process",
            params={"clip_ranges": list(self.clip_ranges)},
        )

    def _build_energy_dos_spec(self):
        t_index = int(self.page_image.slider_time.value())
        delay_text = self._current_delay_text(t_index)
        return AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=f"Energy-DOS [T={t_index}/{delay_text}]",
            page_kind="energy_dos",
            source_module="data_process",
            params={"t_index": t_index},
        )

    def on_apply_time_integral(self):
        if self.core.raw_data is None or not self.core.has_time_axis:
            return

        current_spec = self.left_workspace.current_spec()
        if current_spec is None or current_spec.page_kind != "axis_integral":
            self.left_workspace.ensure_page(self._build_time_integral_spec())
            return

        candidate_spec = self._build_axis_integral_spec(source_mode="time_integral")
        if self._is_same_axis_request(current_spec, candidate_spec):
            self.left_workspace.activate_page(current_spec.page_id)
            self._update_time_slider_state()
            return

        if self._confirm_create_axis_page():
            self.left_workspace.add_page(candidate_spec)
            return

        self._overwrite_axis_page(current_spec, candidate_spec)

    def on_time_integral_controls_changed(self, _):
        if self.core.raw_data is None or self._syncing_controls:
            return

        current_spec = self.left_workspace.current_spec()
        if current_spec is None:
            return

        if current_spec.page_kind == "time_integral":
            self.axis_source_mode = "time_integral"
            self._persist_time_integral_page_state(current_spec)
            self.global_refresh()
        elif self._is_time_integral_axis_page(current_spec):
            self.axis_source_mode = "time_integral"
            self._update_time_slider_state()
            self.global_refresh()

    def on_image_time_changed(self, _):
        if self.core.raw_data is None or self._syncing_controls:
            return

        current_spec = self.left_workspace.current_spec()
        if current_spec is not None and current_spec.page_kind == "time_integral":
            return

        self.axis_source_mode = "frame"
        if current_spec is not None and current_spec.page_kind == "axis_integral":
            self.global_refresh()

    def sync_ax_sliders_to_box(self):
        if not self._can_show_interactive_box():
            return

        axis_idx = self.page_data.combo_ax.currentIndex()
        low = self.page_data.s_ax_low.value()
        up = self.page_data.s_ax_up.value()
        bounds = list(self.clip_ranges) if self.clip_ranges else list(self._get_full_logical_bounds())

        if axis_idx == 0:
            bounds[0], bounds[1] = low, up
        elif axis_idx == 1:
            bounds[2], bounds[3] = low, up
        else:
            bounds[4], bounds[5] = low, up

        current_bounds = list(self.precise_logical_bounds) if self.precise_logical_bounds is not None else None
        if current_bounds == bounds:
            return

        self._rebuild_interactive_box(bounds)
        self._sync_slice_edits_from_logical_bounds(bounds)

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
        if self.core.raw_data is None:
            return

        candidate_spec = self._build_axis_integral_spec()
        current_spec = self.left_workspace.current_spec()

        if current_spec is None or current_spec.page_kind != "axis_integral":
            self.left_workspace.ensure_page(candidate_spec)
            return

        if self._is_same_axis_request(current_spec, candidate_spec):
            self.left_workspace.activate_page(current_spec.page_id)
            return

        if self._confirm_create_axis_page():
            self.left_workspace.add_page(candidate_spec)
            return

        self._overwrite_axis_page(current_spec, candidate_spec)

    def auto_refresh_integral(self):
        if self.page_image.switch_coord.isChecked():
            self.sync_ax_sliders_to_box()

        current_spec = self.left_workspace.current_spec()
        if current_spec is not None and current_spec.page_kind == "axis_integral":
            self.global_refresh()

    def on_apply_other_integral(self):
        if self.core.raw_data is None:
            return

        current_text = self.page_data.combo_other.currentText()
        if current_text == "切片态密度":
            if not self.core.has_time_axis:
                self._show_message("静态数据", "当前数据不包含时间轴，无法计算切片态密度。", QMessageBox.Information)
                return
            if self.clip_ranges is None:
                self._show_message("未进行切片设置", "请先在“图像控制”页设置切片范围。", QMessageBox.Warning)
                return
            spec = self._build_slice_dos_spec()
        else:
            spec = self._build_energy_dos_spec()

        if spec is not None:
            self.left_workspace.ensure_page(spec)

    def on_other_mode_selection_changed(self, _):
        if self._syncing_controls:
            return
        self._update_export_button_states()

    def export_current_result(self):
        spec = self.left_workspace.current_spec()
        if spec is None or self.core.raw_data is None:
            return
        if spec.page_kind == "control_panel":
            return

        if spec.page_kind == "home":
            clip_info = self._get_clip_slices()
            if clip_info is None:
                self._show_message("未进行切片设置", "请先进行切片设置。", QMessageBox.Warning)
                return

            slices, _ = clip_info
            sample = self.core.raw_data[slices[0], slices[1], slices[2], :]
            if not self.core.has_time_axis:
                sample = sample[..., 0]

            export_data = {
                "sample": np.asarray(sample, dtype=np.float32),
                "kx": np.asarray(self.core.coords["X"][slices[0]], dtype=np.float32),
                "ky": np.asarray(self.core.coords["Y"][slices[1]], dtype=np.float32),
                "E": np.asarray(self.core.coords["E"][slices[2]], dtype=np.float32),
                "time": np.asarray(self.core.coords["delay"], dtype=np.float32),
            }
            title = "保存当前立方体数据"
            default_name = "slice_cube.mat"
        elif spec.page_kind == "time_integral":
            self._persist_time_integral_page_state(spec)
            context = self._get_time_integral_context(spec)
            t_low = int(spec.params["t_low"])
            t_up = int(spec.params["t_up"])
            export_data = {
                "sample": np.asarray(context["data"], dtype=np.float32),
                "kx": np.asarray(self.core.coords["X"], dtype=np.float32),
                "ky": np.asarray(self.core.coords["Y"], dtype=np.float32),
                "E": np.asarray(self.core.coords["E"], dtype=np.float32),
            }
            title = "保存时间积分结果"
            default_name = self._build_time_integral_default_name(t_low, t_up)
        elif spec.page_kind == "axis_integral":
            axis_context = self._get_axis_integral_export_context(spec)
            if axis_context is None:
                return
            export_data = axis_context["export_data"]
            title = "保存坐标轴积分结果"
            default_name = axis_context["default_name"]
        elif spec.page_kind == "slice_dos":
            export_data = {
                "time": np.asarray(self.core.coords["delay"], dtype=np.float32),
                "intensity": np.asarray(self._compute_slice_dos(spec.params["clip_ranges"]), dtype=np.float32),
            }
            title = "保存 Slice-DOS 结果"
            default_name = "slice_dos.mat"
        else:
            context = self._get_energy_dos_context(spec)
            if self._is_current_page(spec):
                t_index = int(self.page_image.slider_time.value())
            else:
                t_index = int(spec.params["t_index"])
            export_data = {
                "time": np.asarray([self.core.coords["delay"][t_index]], dtype=np.float32),
                "E": np.asarray(self.core.coords["E"], dtype=np.float32),
                "intensity": np.asarray(context["y_data"], dtype=np.float32),
            }
            title = "保存 Energy-DOS 结果"
            default_name = "energy_dos.mat"

        path = self._choose_export_path(title, default_name)
        if not path:
            return

        self._save_dict_to_path(path, export_data)
