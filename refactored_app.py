import copy
import os
import re
import uuid
from pathlib import Path

from qt_bootstrap import configure_qt_plugin_path

configure_qt_plugin_path()

import numpy as np
import vtk
from PyQt5.QtCore import QEvent, QSettings, QSignalBlocker, QTimer, Qt
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.backend_bases import MouseButton
from matplotlib.patches import Rectangle
from matplotlib.widgets import RectangleSelector
from pyvistaqt import QtInteractor
from scipy.ndimage import gaussian_filter1d
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
from plot_coordinate_tooltip import PlotCoordinateTooltip
from compute_backends import ArrayLRUCache, BackendManager
from refresh_pipeline import ComputeJob, ComputeResult, RefreshCause, RefreshCoordinator, RenderQuality
from render_core import VisualEngine, VolumeRenderSession
from result_workspace import AnalysisPageSpec, ResultWorkspace
from settings_popups import DenoiseSettingsPopup, SecondDerivativeSettingsPopup, WaterfallSettingsPopup


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
    COMPARABLE_1D_PAGE_KINDS = {"slice_dos", "energy_dos", "edc_curve"}
    COMPARISON_PAGE_KIND = "curve_comparison_1d"
    ROTATION_CACHE_LIMIT = 4

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
        self.last_visual_page_id = None
        self.page_denoise_cache = {}
        self._syncing_controls = False
        self._syncing_axis_value_boxes = False
        self.axis_source_mode = "frame"
        self.loaded_npz_stem = "data"
        self.global_waterfall_step = BlankControlPage.DEFAULT_WATERFALL_STEP
        self.global_waterfall_step_custom = False
        self._modifier_page_shortcut_candidate = None
        self._modifier_page_shortcut_cancelled = False
        self._preserve_control_tab_on_next_page_sync = False
        self.curve_clipboard = None
        self._orig_volume_opacity = None
        self._box_interacting = False
        self._box_bounds_signature = None
        self.rotation_angle = 0.0
        self._rendered_rotation_angle = 0.0
        self._rotation_cache = {}
        self._computed_volume_cache = ArrayLRUCache()
        # A preview result must not enter the exact-result cache.  It is
        # exposed only while the corresponding PREVIEW request is rendered.
        self._render_volume_override = None
        self._axis_prefix_cache = None
        self._second_derivative_volume_cache = None
        self._render_exact_ready = True
        self._last_compute_backend = "CPU"
        self._last_ui_transfer_signature = None

        self.settings = QSettings("ARPES", "ARPES_3dMAP")
        saved_backend = self.settings.value("compute_backend", "Auto", type=str)
        self.backend_manager = BackendManager(saved_backend)
        self.refresh_coordinator = RefreshCoordinator(self)
        self.refresh_coordinator.dispatch_requested.connect(self._dispatch_refresh_request)
        self.refresh_coordinator.result_ready.connect(self._on_refresh_result)
        self.refresh_coordinator.failed.connect(self._on_refresh_failed)
        self.refresh_coordinator.status_changed.connect(self._on_refresh_status_changed)

        self.axis_refresh_timer = QTimer(self)
        self.axis_refresh_timer.setSingleShot(True)
        self.axis_refresh_timer.setInterval(66)
        self.axis_refresh_timer.timeout.connect(self.auto_refresh_integral)

        self.current_render_context = None
        self.axis_crop_selector = None
        self.axis_crop_overlay = None
        self.axis_crop_candidates = {}
        self.axis_crop_canvas_cid = None

        if "TOOL_TIP" not in SiGlobal.siui.windows:
            SiGlobal.siui.windows["TOOL_TIP"] = ToolTipWindow()
            SiGlobal.siui.windows["TOOL_TIP"].show()
            SiGlobal.siui.windows["TOOL_TIP"].setOpacity(0)
        self._apply_feedback_styles()

        self.setWindowTitle("能带分析工具")
        self.resize(1550, 950)
        self.setStyleSheet("background-color: #151525;")

        self.init_ui()
        self.volume_session = VolumeRenderSession(self.plotter)
        self.page_render.set_nvidia_backend_available(self.backend_manager.gpu_available)
        self.page_render.set_backend_mode(self.backend_manager.mode)
        self._update_render_status("complete", self.backend_manager.selected_backend_name())
        self._install_data_process_save_controls()
        self.bind_all_events()
        self._initialize_result_workspace()
        self.denoise_popup = DenoiseSettingsPopup(self.page_control_blank)
        self.waterfall_popup = WaterfallSettingsPopup(self.page_control_blank)
        self.sd_popup = SecondDerivativeSettingsPopup(self.page_control_blank)
        self._install_page_keyboard_shortcuts()
        self._sync_global_waterfall_step_from_ui()
        self.initial_control_state = self._capture_control_state()
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

    def _install_page_keyboard_shortcuts(self):
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is getattr(self, "left_display_stack", None) and event.type() == QEvent.Resize:
            self._position_render_status()
        if event.type() == QEvent.KeyPress:
            if not self._page_keyboard_shortcuts_enabled():
                self._reset_page_keyboard_shortcut()
                return super().eventFilter(watched, event)
            if self._handle_curve_clipboard_shortcut(event):
                return True
            if self._handle_number_page_shortcut(event):
                return True
            return self._handle_page_shortcut_key_press(event)
        if event.type() == QEvent.KeyRelease:
            if not self._page_keyboard_shortcuts_enabled():
                self._reset_page_keyboard_shortcut()
                return super().eventFilter(watched, event)
            return self._handle_page_shortcut_key_release(event)
        return super().eventFilter(watched, event)

    def _page_keyboard_shortcuts_enabled(self):
        return (
            self.isVisible()
            and QApplication.activeWindow() is self
            and QApplication.activeModalWidget() is None
        )

    def _reset_page_keyboard_shortcut(self):
        self._modifier_page_shortcut_candidate = None
        self._modifier_page_shortcut_cancelled = False

    @staticmethod
    def _number_shortcut_index(event):
        key_to_index = {
            Qt.Key_1: 0,
            Qt.Key_2: 1,
            Qt.Key_3: 2,
            Qt.Key_4: 3,
        }
        return key_to_index.get(event.key())

    def _focus_accepts_number_input(self):
        focus_widget = QApplication.focusWidget()
        if focus_widget is None:
            return False

        class_names = []
        current_class = focus_widget.__class__
        while current_class is not object:
            class_names.append(current_class.__name__)
            current_class = current_class.__base__

        editable_markers = ("LineEdit", "EditBox", "SpinBox", "TextEdit", "PlainTextEdit")
        return any(marker in class_name for class_name in class_names for marker in editable_markers)

    @staticmethod
    def _widget_is_descendant(widget, ancestor):
        current = widget
        while current is not None:
            if current is ancestor:
                return True
            current = current.parentWidget()
        return False

    def _curve_clipboard_shortcuts_target_left_workspace(self):
        if self._focus_accepts_number_input():
            return False

        focus_widget = QApplication.focusWidget()
        if focus_widget is not None:
            if self._widget_is_descendant(focus_widget, self.left_workspace):
                return True
            if self._widget_is_descendant(focus_widget, self.left_display_stack):
                return True

        return self._cursor_inside_widget(self.left_workspace, QCursor.pos())

    def _handle_curve_clipboard_shortcut(self, event):
        if event.isAutoRepeat() or event.modifiers() != Qt.ControlModifier:
            return False
        if event.key() not in (Qt.Key_C, Qt.Key_V):
            return False
        if not self._curve_clipboard_shortcuts_target_left_workspace():
            return False

        self._reset_page_keyboard_shortcut()
        if event.key() == Qt.Key_C:
            self._copy_current_curve_to_clipboard(show_message=True)
        else:
            self._paste_curve_to_comparison_page(show_message=True)
        return True

    def _handle_number_page_shortcut(self, event):
        if event.isAutoRepeat() or event.modifiers() not in (Qt.NoModifier, Qt.KeypadModifier):
            return False

        index = self._number_shortcut_index(event)
        if index is None or self._focus_accepts_number_input():
            return False

        return self._select_page_under_cursor(index)

    def _handle_page_shortcut_key_press(self, event):
        if event.isAutoRepeat():
            return False

        key = event.key()
        if key in (Qt.Key_Shift, Qt.Key_Control):
            if self._modifier_page_shortcut_candidate is None:
                self._modifier_page_shortcut_candidate = key
                self._modifier_page_shortcut_cancelled = False
            elif self._modifier_page_shortcut_candidate != key:
                self._modifier_page_shortcut_cancelled = True
            return False

        if self._modifier_page_shortcut_candidate is not None:
            self._modifier_page_shortcut_cancelled = True
        return False

    def _handle_page_shortcut_key_release(self, event):
        if event.isAutoRepeat():
            return False

        key = event.key()
        if key != self._modifier_page_shortcut_candidate:
            return False

        if not self._modifier_page_shortcut_cancelled:
            if key == Qt.Key_Shift:
                self._step_page_under_cursor(1)
            elif key == Qt.Key_Control:
                self._step_page_under_cursor(-1)

        self._modifier_page_shortcut_candidate = None
        self._modifier_page_shortcut_cancelled = False
        return False

    @staticmethod
    def _cursor_inside_widget(widget, global_pos):
        if widget is None or not widget.isVisible():
            return False

        if not widget.rect().contains(widget.mapFromGlobal(global_pos)):
            return False

        hovered = QApplication.widgetAt(global_pos)
        if hovered is None:
            return True

        current = hovered
        while current is not None:
            if current is widget:
                return True
            current = current.parentWidget()
        return False

    def _step_page_under_cursor(self, step):
        cursor_pos = QCursor.pos()
        if self._cursor_inside_widget(self.right_panel, cursor_pos):
            self._step_control_page(step)
        elif self._cursor_inside_widget(self.left_workspace, cursor_pos):
            self._step_left_workspace_page(step)

    def _select_page_under_cursor(self, index):
        cursor_pos = QCursor.pos()
        if self._cursor_inside_widget(self.right_panel, cursor_pos):
            self._select_control_page_by_index(index)
            return True
        if self._cursor_inside_widget(self.left_workspace, cursor_pos):
            self._select_left_workspace_page_by_index(index)
            return True
        return False

    def _select_control_page_by_index(self, index):
        if 0 <= index < self.page_container.count():
            self._select_control_page(index)
            return True
        return False

    def _step_control_page(self, step):
        page_count = int(self.page_container.count())
        if page_count <= 0:
            return
        current_index = int(self.page_container.currentIndex())
        target_index = (current_index + int(step)) % page_count
        self._select_control_page(target_index)

    def _ordered_left_workspace_page_ids(self):
        return [
            page_id
            for page_id in self.left_workspace.page_buttons.keys()
            if page_id in self.left_workspace.page_specs
        ]

    def _select_left_workspace_page_by_index(self, index):
        page_ids = self._ordered_left_workspace_page_ids()
        if 0 <= index < len(page_ids):
            self._activate_left_workspace_page_from_shortcut(page_ids[index])
            return True
        return False

    def _activate_left_workspace_page_from_shortcut(self, page_id):
        self._preserve_control_tab_on_next_page_sync = True
        try:
            self.left_workspace.activate_page(page_id)
        finally:
            self._preserve_control_tab_on_next_page_sync = False

    def _step_left_workspace_page(self, step):
        page_ids = self._ordered_left_workspace_page_ids()
        if not page_ids:
            return

        current_page_id = self.left_workspace.current_page_id
        try:
            current_index = page_ids.index(current_page_id)
        except ValueError:
            current_index = 0

        target_index = (current_index + int(step)) % len(page_ids)
        self._activate_left_workspace_page_from_shortcut(page_ids[target_index])

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
        self.canvas_2d.setMinimumSize(0, 0)
        self.canvas_2d.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.canvas_2d.setContextMenuPolicy(Qt.CustomContextMenu)
        self.canvas_2d.customContextMenuRequested.connect(self._show_curve_context_menu)
        self.plotter.setContextMenuPolicy(Qt.CustomContextMenu)
        self.plotter.customContextMenuRequested.connect(self._show_main_context_menu)
        self.ax_2d = self.fig.add_subplot(111)
        self.axis_crop_canvas_cid = self.canvas_2d.mpl_connect("button_press_event", self._on_axis_crop_canvas_click)
        self.plot_coordinate_tooltip = PlotCoordinateTooltip(
            self.canvas_2d,
            self.ax_2d,
            context_provider=lambda: self.current_render_context,
            active_provider=lambda: self.left_display_stack.currentWidget() is self.canvas_2d,
        )
        self.left_display_stack.addWidget(self.canvas_2d)

        self.page_control_blank = BlankControlPage(self.left_display_stack)
        self.left_display_stack.addWidget(self.page_control_blank)

        self.render_status_label = QLabel(self.left_display_stack)
        self.render_status_label.setFixedHeight(26)
        self.render_status_label.setMinimumWidth(210)
        self.render_status_label.setAlignment(Qt.AlignCenter)
        self.render_status_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.render_status_label.setStyleSheet(
            "QLabel { color: white; background: rgba(18, 18, 30, 190); "
            "border: 1px solid rgba(255, 255, 255, 45); border-radius: 8px; padding: 2px 8px; }"
        )
        self.left_display_stack.installEventFilter(self)
        self._position_render_status()

        self.left_workspace = ResultWorkspace(self.left_display_stack, self)
        main_layout.addWidget(self.left_workspace, stretch=7)

        self.right_panel = QFrame()
        self.right_panel.setMinimumWidth(430)
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

    def _position_render_status(self):
        label = getattr(self, "render_status_label", None)
        stack = getattr(self, "left_display_stack", None)
        if label is None or stack is None:
            return
        label.adjustSize()
        width = max(210, min(label.sizeHint().width() + 10, 330))
        label.setFixedWidth(width)
        label.move(max(10, stack.width() - width - 14), 12)
        label.raise_()

    def _render_device_label(self):
        cached = getattr(self, "_render_device_name", None)
        if cached:
            return cached
        name = "OpenGL渲染"
        try:
            report = str(self.plotter.ren_win.ReportCapabilities()).lower()
            if "nvidia" in report:
                name = "NVIDIA GPU渲染"
            elif "amd" in report or "radeon" in report:
                name = "AMD GPU渲染"
            elif "intel" in report:
                name = "Intel GPU渲染"
            elif "llvmpipe" in report or "software" in report:
                name = "软件渲染"
        except Exception:
            pass
        self._render_device_name = name
        return name

    def _update_render_status(self, phase, backend=""):
        label = getattr(self, "render_status_label", None)
        if label is None:
            return
        phase_text = {
            "preview": "预览",
            "computing": "计算中",
            "complete": "完整",
            "failed": "计算失败",
        }.get(str(phase), str(phase))
        compute_name = backend or getattr(self, "_last_compute_backend", "CPU")
        label.setText(f"{phase_text} · {compute_name}计算 / {self._render_device_label()}")
        label.show()
        self._position_render_status()

    def _on_refresh_status_changed(self, phase, backend):
        if backend:
            self._last_compute_backend = backend
        self._update_render_status(phase, backend)

    def on_backend_mode_changed(self, mode):
        selected = self.backend_manager.set_mode(mode)
        if selected != mode:
            blocker = QSignalBlocker(self.page_render.combo_backend)
            self.page_render.set_backend_mode(selected)
            del blocker
        self.settings.setValue("compute_backend", selected)
        self.backend_manager.clear_caches()
        self._computed_volume_cache.clear()
        self._rotation_cache.clear()
        self._clear_axis_prefix_cache()
        self._second_derivative_volume_cache = None
        self._last_compute_backend = self.backend_manager.selected_backend_name()
        if self.core.raw_data is not None:
            self.request_refresh(RefreshCause.DATA_SOURCE, RenderQuality.EXACT, immediate=True)
        else:
            self._update_render_status("complete", self._last_compute_backend)

    def _active_refresh_page_id(self):
        spec = self.left_workspace.current_spec() if hasattr(self, "left_workspace") else None
        return spec.page_id if spec is not None else "home"

    def request_refresh(self, cause, quality=RenderQuality.EXACT, *, immediate=False, interactive=False, snapshot=None):
        if self._syncing_controls:
            return
        page_id = self._active_refresh_page_id()
        values = dict(snapshot or {})
        values.setdefault("rotation_angle", float(self.rotation_angle))
        if interactive:
            preview, exact = self.refresh_coordinator.make_quality_pair(page_id, cause, values)
            self._render_exact_ready = False
            self._update_export_button_states()
            self.refresh_coordinator.schedule_interactive(preview, exact)
            return

        request = self.refresh_coordinator.make_request(page_id, cause, quality, values)
        if quality == RenderQuality.PREVIEW:
            self._render_exact_ready = False
            self._update_export_button_states()
            if immediate:
                self._update_render_status("preview", self._last_compute_backend)
                self.refresh_coordinator.dispatch_now(request)
            else:
                self.refresh_coordinator.schedule_preview(request)
        elif immediate:
            self._render_exact_ready = False
            self._update_export_button_states()
            self.refresh_coordinator.schedule_exact(request, immediate=True)
        else:
            self._render_exact_ready = False
            self._update_export_button_states()
            self.refresh_coordinator.schedule_exact(request)

    @staticmethod
    def _volume_cache_key(raw_data, source_mode, frame_index, t_low, t_up, angle):
        if source_mode == "time_integral":
            return (
                "integral",
                id(raw_data),
                int(t_low),
                int(t_up),
                round(float(angle), 2),
            )
        return ("frame", id(raw_data), int(frame_index), round(float(angle), 2))

    def _volume_source_descriptor(self, spec, request):
        if spec is None or spec.page_kind in {"control_panel", self.COMPARISON_PAGE_KIND, "slice_dos"}:
            return None
        raw_data, _coords = self._get_display_state_for_spec(spec)
        if raw_data is None or raw_data.ndim != 4:
            return None

        params = spec.params
        source_mode = "frame"
        frame_index = int(params.get("source_t_index", params.get("t_index", self.page_image.slider_time.value())))
        t_low = int(params.get("source_t_low", params.get("t_low", self.page_data.s_t_low.value())))
        t_up = int(params.get("source_t_up", params.get("t_up", self.page_data.s_t_up.value())))

        if spec.page_kind == "time_integral":
            source_mode = "time_integral"
        elif spec.page_kind in {
            "axis_integral",
            "axis_integral_crop",
            "waterfall_edc",
            "edc_curve",
            "second_derivative",
        }:
            source_mode = self._normalize_axis_source_mode(params.get("source_mode"))
        if spec.page_kind == "second_derivative" and params.get("source_page_kind") == "time_integral":
            source_mode = "time_integral"

        if t_low > t_up:
            t_low, t_up = t_up, t_low
        angle = float(request.snapshot.get("rotation_angle", self.rotation_angle))
        key = self._volume_cache_key(raw_data, source_mode, frame_index, t_low, t_up, angle)
        needs_compute = source_mode == "time_integral" or abs(angle) >= 1e-6
        extra_metadata = {}
        if not needs_compute:
            operation = None
        else:
            operation = "prepare_volume"
        resampled_preview = (
            request.cause == RefreshCause.ROTATION
            and request.quality == RenderQuality.PREVIEW
            and spec.page_kind in {"home", "time_integral"}
        )
        payload = {
            "raw_data": raw_data,
            "source_mode": source_mode,
            "frame_index": frame_index,
            "t_low": t_low,
            "t_up": t_up,
            "angle": angle,
            # Rotation previews use the same resampling geometry as the exact
            # result, but on a bounded voxel count.  Rotating the VTK actor
            # itself rotates the volume's bounding box and visibly snaps back
            # when the exact, axis-aligned grid replaces it.
            "preview": resampled_preview,
            "max_voxels": 500_000 if resampled_preview else 2_500_000,
        }
        if resampled_preview:
            extra_metadata["resampled_preview"] = True

        if spec.page_kind in {"axis_integral", "axis_integral_crop"}:
            resolved = self._resolved_axis_integral_params(spec)
            prefix_key = self._axis_prefix_cache_key(spec, resolved, raw_data)
            if self._axis_prefix_cache is None or self._axis_prefix_cache.get("key") != prefix_key:
                operation = "prepare_axis_prefix"
                payload["axis"] = int(resolved["axis_index"])
                extra_metadata.update(
                    {
                        "axis_prefix_key": prefix_key,
                        "axis_prefix_page_id": spec.page_id,
                        "axis_prefix_axis": int(resolved["axis_index"]),
                    }
                )

        if spec.page_kind == "second_derivative" and params.get("source_view") == "3d":
            derivative_axis = int(params.get("derivative_axis", 2))
            coordinate_key = {0: "X", 1: "Y", 2: "E"}[derivative_axis]
            coordinate_values = np.asarray(self.original_coords[coordinate_key], dtype=np.float64)
            source_signature = (
                ("time_integral", t_low, t_up)
                if source_mode == "time_integral"
                else ("frame", frame_index)
            )
            derivative_key = (
                tuple(source_signature),
                round(float(angle), 2),
                derivative_axis,
                tuple(int(size) for size in raw_data.shape[:3]),
                coordinate_values.dtype.str,
                coordinate_values.tobytes(),
            )
            cache = self._second_derivative_volume_cache
            if cache is None or cache.get("raw_data") is not raw_data or cache.get("key") != derivative_key:
                operation = "prepare_second_derivative"
                sd_params = params.get("second_derivative_params") or {}
                payload.update({
                    "axis": derivative_axis,
                    "coordinates": coordinate_values,
                    "sigma": float(sd_params.get("sigma", 1.5)),
                    "threshold": float(sd_params.get("threshold", 0.0)),
                })
                extra_metadata.update(
                    {
                        "derivative_key": derivative_key,
                        "derivative_raw_data": raw_data,
                    }
                )

        if operation is None:
            return None
        return key, ComputeJob(operation, payload), extra_metadata

    def _dispatch_refresh_request(self, request):
        spec = self.left_workspace.current_spec() or self.left_workspace.home_spec()
        if spec is None or request.page_id != spec.page_id:
            return
        self.rotation_angle = round(float(request.snapshot.get("rotation_angle", self.rotation_angle)), 2)
        if request.cause == RefreshCause.ROTATION and request.quality == RenderQuality.PREVIEW:
            self._apply_rotation_actor_preview(self.rotation_angle)
            # Simple 3D source pages immediately follow the actor feedback with
            # a low-resolution resampled preview.  Derived pages retain the
            # lightweight actor path until their exact analysis is ready.
            if spec.page_kind not in {"home", "time_integral"}:
                self._finish_ui_refresh(request, self._last_compute_backend)
                return

        if request.cause == RefreshCause.TRANSFER_FUNCTION:
            signature = self._current_transfer_signature()
            if request.quality == RenderQuality.EXACT and signature == self._last_ui_transfer_signature:
                self._finish_ui_refresh(request, self._last_compute_backend)
                return
        if request.cause in {RefreshCause.TRANSFER_FUNCTION, RefreshCause.GEOMETRY, RefreshCause.OVERLAY}:
            self._render_active_page(request.quality, request.cause)
            self._finish_ui_refresh(request, self._last_compute_backend)
            return

        descriptor = self._volume_source_descriptor(spec, request)
        if descriptor is None:
            self._render_active_page(request.quality, request.cause)
            self._finish_ui_refresh(request, self._last_compute_backend)
            return
        cache_key, job, extra_metadata = descriptor
        base_cached = self._computed_volume_cache.get(cache_key) is not None
        def analysis_cache_ready():
            ready = not extra_metadata
            if "axis_prefix_key" in extra_metadata:
                ready = (
                    self._axis_prefix_cache is not None
                    and self._axis_prefix_cache.get("key") == extra_metadata["axis_prefix_key"]
                )
            if "derivative_key" in extra_metadata:
                derivative_cache = self._second_derivative_volume_cache
                ready = (
                    derivative_cache is not None
                    and derivative_cache.get("raw_data") is extra_metadata["derivative_raw_data"]
                    and derivative_cache.get("key") == extra_metadata["derivative_key"]
                )
            return ready

        analysis_cached = analysis_cache_ready()
        if base_cached and analysis_cached:
            self._render_active_page(request.quality, request.cause)
            self._finish_ui_refresh(request, self._last_compute_backend)
            return

        def work():
            cached = self._computed_volume_cache.get(cache_key)
            if cached is not None and analysis_cache_ready():
                return ComputeResult(
                    value=cached,
                    backend=self._last_compute_backend,
                    metadata={"cache_key": cache_key, "cache_hit": True},
                )
            result = self.backend_manager.execute(job)
            result.metadata["cache_key"] = cache_key
            result.metadata.update(extra_metadata)
            return result

        self.refresh_coordinator.submit_compute(request, work)

    def _on_refresh_result(self, request, result):
        cache_key = result.metadata.get("cache_key")
        value = result.value
        if isinstance(value, dict):
            volume_value = value.get("volume")
        else:
            volume_value = value
        is_resampled_preview = bool(result.metadata.get("resampled_preview"))
        if cache_key is not None and volume_value is not None and not is_resampled_preview:
            self._computed_volume_cache.put(cache_key, volume_value)
        if isinstance(value, dict) and "axis_prefix_key" in result.metadata:
            self._axis_prefix_cache = {
                "key": result.metadata["axis_prefix_key"],
                "page_id": result.metadata["axis_prefix_page_id"],
                "axis_index": result.metadata["axis_prefix_axis"],
                "prefix": value["prefix"],
            }
        if isinstance(value, dict) and "derivative_key" in result.metadata:
            self._second_derivative_volume_cache = {
                "raw_data": result.metadata["derivative_raw_data"],
                "key": result.metadata["derivative_key"],
                "data": value["derivative"],
            }
        if request.page_id != self._active_refresh_page_id():
            return
        self._last_compute_backend = result.backend
        if is_resampled_preview and cache_key is not None and volume_value is not None:
            self._render_volume_override = (cache_key, volume_value)
        try:
            self._render_active_page(request.quality, request.cause)
        finally:
            self._render_volume_override = None
        self._finish_ui_refresh(request, result.backend)

    def _on_refresh_failed(self, request, message):
        self._render_exact_ready = False
        self._update_export_button_states()
        self._update_render_status("failed", self.backend_manager.selected_backend_name())
        print(f"Refresh failed ({request.page_id}): {message}")

    def _finish_ui_refresh(self, request, backend):
        if request.quality == RenderQuality.EXACT:
            self._render_exact_ready = True
            self._update_render_status("complete", backend)
        else:
            self._update_render_status("preview", backend)
        self._update_export_button_states()

    def _current_transfer_signature(self):
        return (
            tuple(self._get_display_levels()),
            str(self.page_render.combo_map.currentText()),
            str(self.page_render.get_selected_cmap()),
        )

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
        self.page_image.slider_time.valueChanged.connect(self.on_time_slider_changed)
        self.page_image.slider_time.sliderReleased.connect(self.flush_time_slider_refresh)
        self.page_image.input_time.editingFinished.connect(self.flush_time_slider_refresh)
        self.page_image.switch_axes.toggled.connect(
            lambda _checked: self.request_refresh(RefreshCause.OVERLAY, RenderQuality.EXACT, immediate=True)
        )
        self.page_image.switch_coord.toggled.connect(self.on_toggle_interactive_box)
        self.page_image.switch_flip.toggled.connect(self.on_toggle_e_flip)
        self.page_image.edit_rotation.textChanged.connect(self.on_rotation_angle_preview)
        self.page_image.edit_rotation.editingFinished.connect(self.on_rotation_angle_changed)

        self.page_render.btn_apply_cmap.clicked.connect(
            lambda: self.request_refresh(RefreshCause.TRANSFER_FUNCTION, RenderQuality.EXACT, immediate=True)
        )
        self.page_render.s_low.valueChanged.connect(self.schedule_render_levels_refresh)
        self.page_render.s_gamma.valueChanged.connect(self.schedule_render_levels_refresh)
        self.page_render.s_up.valueChanged.connect(self.schedule_render_levels_refresh)
        self.page_render.s_low.sliderReleased.connect(self.flush_render_levels_refresh)
        self.page_render.s_gamma.sliderReleased.connect(self.flush_render_levels_refresh)
        self.page_render.s_up.sliderReleased.connect(self.flush_render_levels_refresh)
        self.page_render.btn_apply_map.clicked.connect(
            lambda: self.request_refresh(RefreshCause.TRANSFER_FUNCTION, RenderQuality.EXACT, immediate=True)
        )
        self.page_render.btn_apply_noise.clicked.connect(self.on_apply_denoise)
        self.page_render.combo_backend.currentTextChanged.connect(self.on_backend_mode_changed)

        self.page_control_blank.waterfall_step_box.editingFinished.connect(self.on_waterfall_step_changed)
        for button_name in ("button_increase", "button_decrease"):
            button = getattr(self.page_control_blank.waterfall_step_box, button_name, None)
            if button is not None:
                button.clicked.connect(self.on_waterfall_step_changed)

        self.page_data.combo_ax.currentIndexChanged.connect(self.update_ax_slider_range)
        self.page_data.btn_t_apply.clicked.connect(self.on_apply_time_integral)
        self.page_data.s_t_low.valueChanged.connect(self.on_time_integral_controls_changed)
        self.page_data.s_t_up.valueChanged.connect(self.on_time_integral_controls_changed)
        self.page_data.s_t_low.sliderReleased.connect(self.flush_axis_refresh)
        self.page_data.s_t_up.sliderReleased.connect(self.flush_axis_refresh)
        self.page_data.s_ax_low.valueChanged.connect(
            lambda value: self.on_axis_slider_value_changed(self.page_data.input_ax_low, value)
        )
        self.page_data.s_ax_up.valueChanged.connect(
            lambda value: self.on_axis_slider_value_changed(self.page_data.input_ax_up, value)
        )
        self.page_data.s_ax_mid.valueChanged.connect(
            lambda value: self.on_axis_slider_value_changed(self.page_data.input_ax_mid, value)
        )
        self.page_data.input_ax_low.valueChanged.connect(
            lambda _value: self.on_axis_physical_value_changed(self.page_data.input_ax_low, self.page_data.s_ax_low)
        )
        self.page_data.input_ax_up.valueChanged.connect(
            lambda _value: self.on_axis_physical_value_changed(self.page_data.input_ax_up, self.page_data.s_ax_up)
        )
        self.page_data.input_ax_mid.valueChanged.connect(
            lambda _value: self.on_axis_physical_value_changed(self.page_data.input_ax_mid, self.page_data.s_ax_mid)
        )
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
        self.left_workspace.page_closed.connect(self.on_result_page_closed)

    def _initialize_result_workspace(self):
        home_spec = AnalysisPageSpec(
            page_id="home",
            title="原始视图",
            page_kind="home",
            source_module="system",
            closeable=False,
        )
        self.left_workspace.set_home_page(home_spec)
        self.active_page_spec = home_spec
        self.last_visual_page_id = home_spec.page_id
        home_spec.params["control_state"] = self._capture_control_state()

    def _select_control_page(self, index):
        self.page_container.setCurrentIndex(index)
        [self.btn_page1, self.btn_page2, self.btn_page3][index].setChecked(True)
        if not self._syncing_controls:
            self._persist_page_ui_state()

    def _clone_coords(self, coords=None):
        source = coords if coords is not None else self.core.coords
        return {
            key: None if value is None else np.array(value, copy=True)
            for key, value in source.items()
        }

    def _persist_active_page_state(self):
        self._persist_page_ui_state(self.active_page_spec)

    @staticmethod
    def _copy_state(value):
        return copy.deepcopy(value)

    def _current_visual_spec(self):
        target_page_id = self.last_visual_page_id or self.left_workspace.home_page_id
        target_spec = self.left_workspace.page_by_id(target_page_id)
        return target_spec or self.left_workspace.home_spec()

    def _control_state_owner(self, spec=None):
        candidate = spec or self.active_page_spec or self.left_workspace.current_spec()
        if candidate is not None and candidate.page_kind != "control_panel":
            return candidate
        return self._current_visual_spec()

    def _capture_control_state(self):
        self._sync_global_waterfall_step_from_ui()
        return {
            "active_control_tab": int(self.page_container.currentIndex()),
            "axis_source_mode": self.axis_source_mode,
            "image": self.page_image.export_state(),
            "render": self.page_render.export_state(),
            "denoise_detail": self.page_control_blank.export_state(),
            "data_process": self.page_data.export_state(),
        }

    def _store_control_state(self, spec, control_state):
        if spec is None:
            return
        spec.params["control_state"] = self._copy_state(control_state)

    def _sync_global_waterfall_step_from_ui(self):
        self.global_waterfall_step = float(self.page_control_blank.get_waterfall_step())
        self.global_waterfall_step_custom = bool(self.page_control_blank.is_waterfall_step_custom())

    def _apply_global_waterfall_step_to_ui(self):
        self.page_control_blank.set_waterfall_step(
            float(self.global_waterfall_step),
            custom=self.global_waterfall_step_custom,
        )

    def _apply_waterfall_step_for_spec_to_ui(self, spec):
        if spec is not None and spec.page_kind == "waterfall_edc" and "k_step" in spec.params:
            self.page_control_blank.set_waterfall_step(float(spec.params["k_step"]), custom=True)
            return
        self._apply_global_waterfall_step_to_ui()

    def _seed_control_state_for_spec(self, spec):
        if spec is None:
            return
        control_state = self._capture_control_state()
        control_state["axis_source_mode"] = self._page_axis_source_mode(spec)
        self._store_control_state(spec, control_state)

    def _page_axis_source_mode(self, spec):
        if spec is None:
            return "frame"
        if spec.page_kind == "time_integral":
            return "time_integral"
        if spec.page_kind in {"axis_integral", "axis_integral_crop", "waterfall_edc", "edc_curve", "second_derivative"}:
            return self._normalize_axis_source_mode(
                spec.params.get(
                    "source_mode",
                    "time_integral" if spec.params.get("source_page_kind") == "time_integral" else "frame",
                )
            )
        return "frame"

    def _persist_home_page_state(self, spec):
        if spec is None:
            return
        spec.params["clip_ranges"] = self._copy_state(self.clip_ranges)
        spec.params["home_slice_info"] = self._copy_state(self.home_slice_info)
        spec.params["precise_logical_bounds"] = self._copy_state(self.precise_logical_bounds)
        spec.params["last_synced_slice_texts"] = self._copy_state(self.last_synced_slice_texts)

    def _restore_home_page_state(self, spec):
        params = spec.params if spec is not None else {}
        self.clip_ranges = self._copy_state(params.get("clip_ranges"))
        self.home_slice_info = self._copy_state(params.get("home_slice_info"))
        self.precise_logical_bounds = self._copy_state(params.get("precise_logical_bounds"))
        self.last_synced_slice_texts = self._copy_state(params.get("last_synced_slice_texts"))

    def _persist_page_specific_state(self, spec):
        if spec is None:
            return
        if spec.page_kind == "home":
            self._persist_home_page_state(spec)
        elif spec.page_kind == "time_integral":
            self._persist_time_integral_page_state(spec)
        elif spec.page_kind in {"axis_integral", "axis_integral_crop"}:
            self._persist_axis_integral_page_state(spec)
        elif spec.page_kind == "second_derivative":
            self._persist_second_derivative_page_state(spec)
        elif spec.page_kind == "waterfall_edc":
            self._persist_waterfall_page_state(spec)
        elif spec.page_kind == "energy_dos":
            spec.params["t_index"] = int(self.page_image.slider_time.value())

    def _persist_page_ui_state(self, spec=None):
        owner_spec = self._control_state_owner(spec)
        if owner_spec is None:
            return
        self._store_control_state(owner_spec, self._capture_control_state())
        self._persist_page_specific_state(owner_spec)

    def _restore_page_ui_state(self, spec):
        owner_spec = self._control_state_owner(spec)
        if owner_spec is None:
            return

        control_state = self._copy_state(owner_spec.params.get("control_state"))
        if control_state is None:
            control_state = self._capture_control_state()
            control_state["axis_source_mode"] = self._page_axis_source_mode(owner_spec)
            self._store_control_state(owner_spec, control_state)

        if self.axis_refresh_timer.isActive():
            self.axis_refresh_timer.stop()

        self._syncing_controls = True
        try:
            if owner_spec.page_kind == "home":
                self._restore_home_page_state(owner_spec)

            self.axis_source_mode = control_state.get("axis_source_mode", self._page_axis_source_mode(owner_spec))
            self.page_image.restore_state(control_state.get("image"), block_signals=True)
            self.rotation_angle = self.page_image.get_rotation_angle()
            self.page_render.restore_state(control_state.get("render"), block_signals=True)
            self.page_control_blank.restore_state(control_state.get("denoise_detail"), block_signals=True)
            self.page_data.restore_state(control_state.get("data_process"), block_signals=True)
            self._configure_second_derivative_axis_controls(owner_spec)

            if self.core.raw_data is not None:
                self.update_ax_slider_range()
                data_state = control_state.get("data_process") or {}
                self.page_data.restore_state(
                    {
                        "combo_ax": data_state.get("combo_ax"),
                        "s_ax_low": data_state.get("s_ax_low"),
                        "s_ax_up": data_state.get("s_ax_up"),
                        "s_ax_mid": data_state.get("s_ax_mid"),
                        "locked_half_width": data_state.get("locked_half_width"),
                    },
                    block_signals=True,
                )
                self.sync_axis_value_boxes_from_sliders()

            if self._preserve_control_tab_on_next_page_sync:
                tab_index = int(self.page_container.currentIndex())
            else:
                tab_index = int(control_state.get("active_control_tab", self.page_container.currentIndex()))
            tab_index = max(0, min(self.page_container.count() - 1, tab_index))
            self._select_control_page(tab_index)
            self._apply_waterfall_step_for_spec_to_ui(owner_spec)
        finally:
            self._syncing_controls = False

    def _persist_axis_integral_page_state(self, spec=None):
        target_spec = spec or self.left_workspace.current_spec()
        if target_spec is None or target_spec.page_kind not in {"axis_integral", "axis_integral_crop"}:
            return

        axis_index = int(self.page_data.combo_ax.currentIndex())
        axis_name = ["X轴", "Y轴", "Z轴"][axis_index]
        source_mode = self._normalize_axis_source_mode(self.axis_source_mode)
        low, up, mid = self._axis_input_logical_values()

        target_spec.params["axis_index"] = axis_index
        target_spec.params["axis_name"] = axis_name
        target_spec.params["low"] = int(low)
        target_spec.params["up"] = int(up)
        target_spec.params["mid"] = int(mid)
        target_spec.params["source_mode"] = source_mode
        target_spec.params["source_page_kind"] = "time_integral" if source_mode == "time_integral" else "home"
        target_spec.params["source_t_index"] = int(self.page_image.slider_time.value())
        target_spec.params["source_t_low"] = int(self.page_data.s_t_low.value())
        target_spec.params["source_t_up"] = int(self.page_data.s_t_up.value())

    def _persist_second_derivative_page_state(self, spec=None):
        target_spec = spec or self.left_workspace.current_spec()
        if (
            target_spec is None
            or target_spec.page_kind != "second_derivative"
            or target_spec.params.get("source_view") != "2d"
        ):
            return

        params = target_spec.params
        if params.get("source_page_kind") == "home":
            axis_index = int(params.get("slice_axis", -1))
            if axis_index not in (0, 1) or self.core.raw_data is None:
                return
            axis_max = max(int(self.core.raw_data.shape[axis_index]) - 1, 0)
            params["slice_index"] = int(
                np.clip(int(self.page_data.s_ax_mid.value()), 0, axis_max)
            )
            return

        if params.get("source_page_kind") != "axis_integral":
            return
        axis_index = int(params.get("axis_index", -1))
        if axis_index not in (0, 1) or self.core.raw_data is None:
            return
        axis_max = max(int(self.core.raw_data.shape[axis_index]) - 1, 0)
        low = int(np.clip(int(self.page_data.s_ax_low.value()), 0, axis_max))
        up = int(np.clip(int(self.page_data.s_ax_up.value()), 0, axis_max))
        if low > up:
            low, up = up, low
        mid = int(np.clip(int(self.page_data.s_ax_mid.value()), low, up))
        params["integral_low"] = low
        params["integral_up"] = up
        params["integral_mid"] = mid

    def _configure_second_derivative_axis_controls(self, spec):
        is_derivative = spec is not None and spec.page_kind == "second_derivative"
        is_2d = is_derivative and spec.params.get("source_view") == "2d"
        is_slice = is_2d and spec.params.get("source_page_kind") == "home"
        is_integral = is_2d and spec.params.get("source_page_kind") == "axis_integral"

        self.page_data.combo_ax.setEnabled(not is_derivative)
        low_up_enabled = not is_derivative or is_integral
        mid_enabled = not is_derivative or is_slice or is_integral
        for widget in (
            self.page_data.s_ax_low,
            self.page_data.s_ax_up,
            self.page_data.input_ax_low,
            self.page_data.input_ax_up,
        ):
            widget.setEnabled(low_up_enabled)
        for widget in (self.page_data.s_ax_mid, self.page_data.input_ax_mid):
            widget.setEnabled(mid_enabled)

    def _waterfall_title_from_params(self, params, k_step=None, *, ascii_only=False):
        axis_info = self._resolve_waterfall_axis_info(int(params.get("axis_index", -1)))
        if axis_info is None:
            return None

        low_idx = int(params.get("integral_low", params.get("low", 0)))
        up_idx = int(params.get("integral_up", params.get("up", low_idx)))
        mid_idx = int(params.get("integral_mid", round((low_idx + up_idx) / 2)))
        center_physical = self.core.logical_to_physical(axis_info["integrated_axis_label"], mid_idx)
        step_value = float(k_step if k_step is not None else params.get("k_step", BlankControlPage.DEFAULT_WATERFALL_STEP))
        return self._waterfall_title_summary(
            axis_info["k_axis_label"],
            axis_info["integrated_axis_label"],
            center_physical,
            step_value,
            ascii_only=ascii_only,
        ) + self._waterfall_crop_suffix(params, ascii_only=ascii_only)

    def _persist_waterfall_page_state(self, spec=None):
        target_spec = spec or self.left_workspace.current_spec()
        if target_spec is None or target_spec.page_kind != "waterfall_edc":
            return

        k_step = float(self.page_control_blank.get_waterfall_step())
        target_spec.params["k_step"] = k_step

        title = self._waterfall_title_from_params(target_spec.params, k_step)
        if title is None:
            return

        title = self._make_unique_page_title(title, exclude_page_id=target_spec.page_id)
        self.left_workspace.update_page(
            target_spec.page_id,
            title=title,
            params=target_spec.params,
            source_page_id=target_spec.source_page_id,
        )

    def on_waterfall_step_changed(self):
        if self._syncing_controls:
            return

        k_step = float(self.page_control_blank.get_waterfall_step())
        self.page_control_blank.set_waterfall_step(k_step, custom=True)
        self.global_waterfall_step = k_step
        self.global_waterfall_step_custom = True

        current_spec = self.left_workspace.current_spec()
        if current_spec is not None and current_spec.page_kind == "waterfall_edc":
            self._persist_waterfall_page_state(current_spec)
            self.global_refresh()

    def flush_render_levels_refresh(self):
        if self._syncing_controls:
            return
        self.request_refresh(
            RefreshCause.TRANSFER_FUNCTION,
            RenderQuality.EXACT,
            immediate=True,
        )

    def schedule_render_levels_refresh(self, _value):
        if self._syncing_controls:
            return
        self.request_refresh(RefreshCause.TRANSFER_FUNCTION, interactive=True)

    def on_time_slider_changed(self, value):
        self.on_image_time_changed(value)
        if self._syncing_controls or self.core.raw_data is None:
            return
        self.request_refresh(RefreshCause.DATA_SOURCE, interactive=True)

    def flush_time_slider_refresh(self):
        if self._syncing_controls or self.core.raw_data is None:
            return
        self.request_refresh(RefreshCause.DATA_SOURCE, RenderQuality.EXACT, immediate=True)

    @staticmethod
    def _normalize_denoise_methods(methods):
        return [method for method in methods if method not in (None, "None")]

    def _get_spec_denoise_methods(self, spec):
        if spec is None:
            return []
        return self._normalize_denoise_methods(spec.params.get("denoise_methods", []))

    @staticmethod
    def _denoise_signature(methods):
        return repr(methods)

    @staticmethod
    def _get_data_for_t_from_raw(raw_data, t_idx):
        t_idx = int(np.clip(t_idx, 0, raw_data.shape[3] - 1))
        return raw_data[:, :, :, t_idx]

    @staticmethod
    def _get_time_integrated_data_from_raw(raw_data, t_low, t_up):
        t_low = max(0, int(t_low))
        t_up = min(raw_data.shape[3] - 1, int(t_up))
        if t_low > t_up:
            t_low, t_up = t_up, t_low
        return np.sum(raw_data[:, :, :, t_low:t_up + 1], axis=3)

    @staticmethod
    def _get_axis_integrated_data_from_raw(data_3d, axis_index, low_idx, up_idx):
        axis_index = int(axis_index)
        low = max(0, int(low_idx))
        up = min(data_3d.shape[axis_index] - 1, int(up_idx))
        if low > up:
            low, up = up, low

        if axis_index == 0:
            return np.sum(data_3d[low:up + 1, :, :], axis=0)
        if axis_index == 1:
            return np.sum(data_3d[:, low:up + 1, :], axis=1)
        return np.sum(data_3d[:, :, low:up + 1], axis=2)

    def _clear_axis_prefix_cache(self, page_id=None):
        cache = self._axis_prefix_cache
        if cache is None:
            return
        if page_id is None or cache.get("page_id") == page_id:
            self._axis_prefix_cache = None

    def _axis_prefix_cache_key(self, spec, params, raw_data):
        source_mode = self._normalize_axis_source_mode(params.get("source_mode"))
        if source_mode == "time_integral":
            t_low, t_up = sorted((int(params["source_t_low"]), int(params["source_t_up"])))
            time_key = ("time_integral", t_low, t_up)
        else:
            time_key = ("frame", int(params["source_t_index"]))

        return (
            spec.page_id,
            self._denoise_signature(self._get_spec_denoise_methods(spec)),
            time_key,
            round(float(self.rotation_angle), 2),
            int(params["axis_index"]),
            tuple(int(size) for size in raw_data.shape),
        )

    def _get_cached_axis_range_sum(self, spec, raw_data, params):
        axis_index = int(params["axis_index"])
        cache_key = self._axis_prefix_cache_key(spec, params, raw_data)
        cache = self._axis_prefix_cache
        if cache is None or cache.get("key") != cache_key:
            source_context = self._get_3d_source_context_for_axis(spec, raw_data)
            prefix = AnalyzerCore.build_axis_prefix_sum(source_context["data"], axis_index)
            cache = {
                "key": cache_key,
                "page_id": spec.page_id,
                "axis_index": axis_index,
                "prefix": prefix,
            }
            self._axis_prefix_cache = cache

        return AnalyzerCore.range_sum_from_axis_prefix(
            cache["prefix"],
            axis_index,
            int(params["low"]),
            int(params["up"]),
        )

    def _rotation_cache_get(self, key):
        cached = self._rotation_cache.pop(key, None)
        if cached is not None:
            self._rotation_cache[key] = cached
        return cached

    def _rotation_cache_store(self, key, value):
        self._rotation_cache[key] = value
        while len(self._rotation_cache) > self.ROTATION_CACHE_LIMIT:
            oldest_key = next(iter(self._rotation_cache))
            self._rotation_cache.pop(oldest_key, None)

    def _get_rotated_frame(self, raw_data, t_idx):
        """Extract a time frame and apply Z-axis (Kx-Ky plane) rotation.

        Caches the rotated volume by (t_idx, angle). Returns the
        original frame directly when rotation_angle is effectively 0.
        """
        angle = self.rotation_angle
        t_idx = int(np.clip(t_idx, 0, raw_data.shape[3] - 1))
        key = ("frame", id(raw_data), int(t_idx), round(float(angle), 2))
        override = self.__dict__.get("_render_volume_override")
        if override is not None and override[0] == key:
            return override[1]
        computed_cache = self.__dict__.get("_computed_volume_cache")
        cached = computed_cache.get(key) if computed_cache is not None else None
        if cached is not None:
            return cached
        if angle is None or abs(float(angle)) < 1e-6:
            return self._get_data_for_t_from_raw(raw_data, t_idx)

        cached = self._rotation_cache_get(key)
        if cached is not None:
            return cached

        data_3d = self._get_data_for_t_from_raw(raw_data, t_idx)
        rotated = self.backend_manager.cpu.rotate_volume(data_3d, float(angle))
        self._rotation_cache_store(key, rotated)
        return rotated

    def _get_rotated_time_integral(self, raw_data, t_low, t_up):
        """Compute time-integrated 3D data and apply Z-axis rotation.

        Caches the result by (t_low, t_up, angle). Skips rotation
        entirely when rotation_angle is effectively 0.
        """
        angle = self.rotation_angle
        t_low = max(0, int(t_low))
        t_up = min(raw_data.shape[3] - 1, int(t_up))
        if t_low > t_up:
            t_low, t_up = t_up, t_low

        key = ("integral", id(raw_data), int(t_low), int(t_up), round(float(angle), 2))
        override = self.__dict__.get("_render_volume_override")
        if override is not None and override[0] == key:
            return override[1]
        computed_cache = self.__dict__.get("_computed_volume_cache")
        cached = computed_cache.get(key) if computed_cache is not None else None
        if cached is not None:
            return cached
        cached = self._rotation_cache_get(key)
        if cached is not None:
            return cached

        data_3d = self._get_time_integrated_data_from_raw(raw_data, t_low, t_up)
        if angle is None or abs(float(angle)) < 1e-6:
            rotated = np.asfortranarray(data_3d, dtype=np.float32)
        else:
            rotated = self.backend_manager.cpu.rotate_volume(data_3d, float(angle))
        self._rotation_cache_store(key, rotated)
        return rotated

    def _apply_denoise_methods_to_raw(self, raw_data, methods):
        if not methods:
            return np.asarray(raw_data, dtype=np.float32)

        from denoise_engines import DenoiseEngines

        return DenoiseEngines.apply_pipeline(raw_data, methods)

    def _get_display_state_for_spec(self, spec):
        if self.original_raw_data is None or self.original_coords is None:
            return None, None

        methods = self._get_spec_denoise_methods(spec)
        if not methods:
            raw_data = self.original_raw_data
        else:
            signature = self._denoise_signature(methods)
            cache_entry = self.page_denoise_cache.get(spec.page_id)
            if cache_entry is not None and cache_entry["signature"] == signature:
                raw_data = cache_entry["raw_data"]
            else:
                raw_data = np.ascontiguousarray(
                    self._apply_denoise_methods_to_raw(self.original_raw_data, methods),
                    dtype=np.float32,
                )
                self.page_denoise_cache[spec.page_id] = {
                    "signature": signature,
                    "raw_data": raw_data,
                }

        coords = self._clone_coords(self.original_coords)
        return np.asarray(raw_data, dtype=np.float32), coords

    def _refresh_core_display_state(self):
        if self.base_raw_data is None or self.base_coords is None:
            return

        display_coords = self._clone_coords(self.base_coords)

        self.core.raw_data = self.base_raw_data
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

        shape = self.core.raw_data.shape[:3]
        logical_bounds = self.core.render_to_logical_bounds(render_bounds, shape)
        logical_bounds = self._map_visual_flip_logical_bounds(logical_bounds, shape)
        self._sync_slice_edits_from_logical_bounds(logical_bounds)

    def _get_render_bounds_for_box(self, logical_bounds=None):
        if self.core.raw_data is None:
            return None

        bounds = logical_bounds if logical_bounds is not None else self.clip_ranges
        if bounds is None:
            bounds = self._get_full_logical_bounds()

        shape = self.core.raw_data.shape[:3]
        bounds = self._map_visual_flip_logical_bounds(bounds, shape)
        return self.core.logical_to_render_bounds(bounds, shape)

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
        signature = tuple(round(float(value), 5) for value in box_bounds)
        if signature == self._box_bounds_signature:
            return

        self._restore_volume_opacity_if_dimmed()
        self.plotter.clear_box_widgets()
        box_widget = self.plotter.add_box_widget(
            callback=lambda poly: self._sync_slice_edits_from_render_bounds(poly.bounds),
            bounds=box_bounds,
            factor=1.0,
            color="#FF69B4",
            rotation_enabled=False,
        )
        box_widget.AddObserver("InteractionEvent", self._on_box_interaction_start)
        box_widget.AddObserver("EndInteractionEvent", self._on_box_interaction_end)
        self._box_bounds_signature = signature

    def _clear_interactive_box(self):
        self.plotter.clear_box_widgets()
        self._box_bounds_signature = None

    def _find_main_volume(self):
        volumes = self.plotter.renderer.GetVolumes()
        volumes.InitTraversal()
        for _ in range(volumes.GetNumberOfItems()):
            vol = volumes.GetNextItem()
            if isinstance(vol, vtk.vtkVolume):
                return vol
        return None

    def _on_box_interaction_start(self, obj, event):
        if self._box_interacting:
            return
        self._dim_current_volume()
        self._box_interacting = True

    def _dim_current_volume(self):
        vol = self._find_main_volume()
        if vol is None:
            return
        prop = vol.GetProperty()
        self._orig_volume_opacity = prop.GetScalarOpacity()
        lowered = vtk.vtkPiecewiseFunction()
        count = self._orig_volume_opacity.GetSize()
        for i in range(count):
            node = [0.0, 0.0, 0.0, 0.0]
            self._orig_volume_opacity.GetNodeValue(i, node)
            lowered.AddPoint(node[0], node[1] * 0.15, node[2], node[3])
        prop.SetScalarOpacity(lowered)

    def _on_box_interaction_end(self, obj, event):
        if not self._box_interacting:
            return
        vol = self._find_main_volume()
        if vol is not None and self._orig_volume_opacity is not None:
            vol.GetProperty().SetScalarOpacity(self._orig_volume_opacity)
        self._orig_volume_opacity = None
        self._box_interacting = False

    def _restore_volume_opacity_if_dimmed(self):
        if self._box_interacting:
            self._on_box_interaction_end(None, None)

    def _get_display_levels(self):
        return (
            self.page_render.s_low.value(),
            self.page_render.s_gamma.value(),
            self.page_render.s_up.value(),
        )

    def _mirror_logical_bounds_for_display(self, bounds, shape):
        if bounds is None:
            return None

        mirrored = list(bounds)
        axis_idx = 2
        axis_max = max(int(shape[axis_idx]) - 1, 0)
        low = float(bounds[axis_idx * 2])
        up = float(bounds[axis_idx * 2 + 1])
        mirrored[axis_idx * 2] = axis_max - up
        mirrored[axis_idx * 2 + 1] = axis_max - low
        return mirrored

    def _map_visual_flip_logical_bounds(self, bounds, shape):
        if not self.page_image.switch_flip.isChecked():
            return bounds
        return self._mirror_logical_bounds_for_display(bounds, shape)

    def _render_context_for_visual_flip(self, context):
        if not self.page_image.switch_flip.isChecked() or context is None:
            return context

        view = context.get("view")
        if view == "2d":
            render_context = dict(context)
            slice_info = dict(render_context["slice_info"])
            slice_info["display_e_flip"] = True
            render_context["slice_info"] = slice_info
            return render_context

        if view == "1d":
            render_context = dict(context)
            if "energy" in str(render_context.get("xlabel", "")).lower():
                render_context["y_data"] = np.flip(np.asarray(render_context["y_data"]), axis=0)
            return render_context

        if view == "waterfall":
            render_context = dict(context)
            render_context["curves"] = np.flip(np.asarray(render_context["curves"]), axis=1)
            if render_context.get("raw_curves") is not None:
                render_context["raw_curves"] = np.flip(np.asarray(render_context["raw_curves"]), axis=1)
            return render_context

        if view == "1d_comparison":
            render_context = dict(context)
            if render_context.get("comparison_kind") not in {"energy_dos", "edc_curve"}:
                return render_context
            curves = []
            for curve in render_context.get("curves", []):
                flipped_curve = dict(curve)
                flipped_curve["y_data"] = np.flip(np.asarray(flipped_curve["y_data"]), axis=0)
                curves.append(flipped_curve)
            render_context["curves"] = curves
            return render_context

        if view != "3d":
            return context

        render_context = dict(context)
        data = np.asarray(render_context["data"])
        render_context["data"] = np.flip(data, axis=2)

        coords = self._clone_coords(render_context.get("coords", self.core.coords))
        if coords.get("E") is not None:
            coords["E"] = np.flip(coords["E"])
        render_context["coords"] = coords

        if render_context.get("clip_ranges") is not None:
            render_context["clip_ranges"] = self._mirror_logical_bounds_for_display(
                render_context["clip_ranges"],
                data.shape,
            )
        return render_context

    def _current_delay_text(self, t_index):
        delays = self.core.coords.get("delay")
        if delays is None or len(delays) == 0:
            return str(t_index)
        safe_index = min(max(int(t_index), 0), len(delays) - 1)
        return f"{float(delays[safe_index]):.3f}"

    @staticmethod
    def _compute_axis_spacing(axis_values, fallback=0.01):
        values = np.asarray(axis_values, dtype=np.float64).flatten()
        if values.size < 2:
            return float(fallback)

        diffs = np.abs(np.diff(values))
        diffs = diffs[np.isfinite(diffs) & (diffs > 1e-12)]
        if diffs.size == 0:
            return float(fallback)

        return float(max(np.median(diffs), 1e-4))

    def _suggest_waterfall_step(self, spec=None):
        axis_key = self._waterfall_step_reference_axis(spec or self.left_workspace.current_spec())
        if axis_key is None:
            return BlankControlPage.DEFAULT_WATERFALL_STEP

        if self.core.raw_data is not None:
            coords = self.core.coords
        else:
            coords = self.original_coords or {}
        return self._compute_axis_spacing(coords.get(axis_key), BlankControlPage.DEFAULT_WATERFALL_STEP)

    def _refresh_waterfall_step_default(self, spec=None):
        if self.global_waterfall_step_custom:
            return
        self.global_waterfall_step = float(self._suggest_waterfall_step(spec))
        self.global_waterfall_step_custom = False
        self._apply_global_waterfall_step_to_ui()

    def _make_page_id(self):
        return uuid.uuid4().hex

    @staticmethod
    def _integral_length(low, up):
        low = int(low)
        up = int(up)
        return abs(up - low) + 1

    def _make_unique_page_title(self, base_title, exclude_page_id=None):
        existing_titles = {
            spec.title
            for page_id, spec in self.left_workspace.page_specs.items()
            if page_id != exclude_page_id
        }
        if base_title not in existing_titles:
            return base_title

        suffix = 2
        while True:
            candidate = f"{base_title}_{suffix}"
            if candidate not in existing_titles:
                return candidate
            suffix += 1

    def _curve_kind_for_spec(self, spec):
        if spec is None:
            return None
        if spec.page_kind in self.COMPARABLE_1D_PAGE_KINDS:
            return spec.page_kind
        if spec.page_kind == self.COMPARISON_PAGE_KIND:
            kind = spec.params.get("comparison_kind")
            if kind in self.COMPARABLE_1D_PAGE_KINDS:
                return kind
        return None

    @staticmethod
    def _normalize_curve_snapshot(snapshot):
        if not isinstance(snapshot, dict):
            return None

        try:
            x_data = np.asarray(snapshot.get("x_data"), dtype=np.float64).ravel()
            y_data = np.asarray(snapshot.get("y_data"), dtype=np.float64).ravel()
        except (TypeError, ValueError):
            return None
        if x_data.size == 0 or x_data.size != y_data.size:
            return None

        curve_kind = snapshot.get("curve_kind")
        title = str(snapshot.get("title") or snapshot.get("source_title") or "1D Curve")
        source_title = str(snapshot.get("source_title") or title)
        label = str(snapshot.get("label") or source_title)
        xlabel = str(snapshot.get("xlabel") or "")
        return {
            "curve_kind": curve_kind,
            "title": title,
            "source_title": source_title,
            "label": label,
            "xlabel": xlabel,
            "x_data": x_data.astype(float).tolist(),
            "y_data": y_data.astype(float).tolist(),
        }

    def _curve_snapshot_from_context(self, spec, context, *, label=None):
        if spec is None or context is None or context.get("view") != "1d":
            return None

        curve_kind = self._curve_kind_for_spec(spec)
        if curve_kind not in self.COMPARABLE_1D_PAGE_KINDS:
            return None

        snapshot = self._normalize_curve_snapshot(
            {
                "curve_kind": curve_kind,
                "title": context.get("title") or spec.title,
                "source_title": spec.title,
                "label": label or spec.title,
                "xlabel": context.get("xlabel") or "",
                "x_data": context.get("x_data"),
                "y_data": context.get("y_data"),
            }
        )
        if snapshot is not None:
            snapshot["curve_kind"] = curve_kind
        return snapshot

    def _current_single_curve_snapshot(self):
        spec = self.left_workspace.current_spec()
        if spec is None or spec.page_kind not in self.COMPARABLE_1D_PAGE_KINDS:
            return None

        context = self.current_render_context
        if context is None or context.get("view") != "1d":
            context = self._compute_render_context(spec)
        return self._curve_snapshot_from_context(spec, context)

    @staticmethod
    def _curve_x_data_matches(reference, candidate):
        ref_x = np.asarray(reference.get("x_data"), dtype=np.float64).ravel()
        candidate_x = np.asarray(candidate.get("x_data"), dtype=np.float64).ravel()
        return ref_x.size == candidate_x.size and np.allclose(ref_x, candidate_x, rtol=1e-6, atol=1e-8)

    def _current_curve_group_snapshot(self):
        spec = self.left_workspace.current_spec()
        kind = self._curve_kind_for_spec(spec)
        if spec is None or kind not in self.COMPARABLE_1D_PAGE_KINDS:
            return None

        if spec.page_kind == self.COMPARISON_PAGE_KIND:
            base_curve = self._normalize_curve_snapshot(spec.params.get("base_curve"))
            if base_curve is None:
                return None
            base_curve["curve_kind"] = kind
            overlay_curves = []
            for curve in spec.params.get("overlay_curves", []):
                normalized = self._normalize_curve_snapshot(curve)
                if normalized is None:
                    continue
                normalized["curve_kind"] = kind
                overlay_curves.append(normalized)
            return kind, base_curve, overlay_curves

        base_curve = self._current_single_curve_snapshot()
        if base_curve is None:
            return None
        return kind, base_curve, []

    def _can_copy_current_curve(self):
        return self._current_single_curve_snapshot() is not None

    def _can_paste_curve_to_current_page(self, *, show_message=False):
        clipboard_curve = self._normalize_curve_snapshot(self.curve_clipboard)
        if clipboard_curve is None:
            if show_message:
                self._show_message("无法粘贴曲线", "没有可粘贴的 1D 曲线。", QMessageBox.Information)
            return False

        group = self._current_curve_group_snapshot()
        if group is None:
            if show_message:
                self._show_message("无法粘贴曲线", "当前页不是可比较的 1D 曲线结果页。", QMessageBox.Information)
            return False

        target_kind, base_curve, _ = group
        if clipboard_curve.get("curve_kind") != target_kind:
            if show_message:
                self._show_message("无法粘贴曲线", "只能粘贴同类型的 1D 曲线。", QMessageBox.Warning)
            return False

        if not self._curve_x_data_matches(base_curve, clipboard_curve):
            if show_message:
                self._show_message("无法粘贴曲线", "曲线横轴不匹配，无法粘贴比较。", QMessageBox.Warning)
            return False

        return True

    def _copy_current_curve_to_clipboard(self, *, show_message=False):
        snapshot = self._current_single_curve_snapshot()
        if snapshot is None:
            if show_message:
                self._show_message("无法复制曲线", "当前页没有可复制的单条 1D 曲线。", QMessageBox.Information)
            return False

        self.curve_clipboard = copy.deepcopy(snapshot)
        return True

    def _paste_curve_to_comparison_page(self, *, show_message=False):
        if not self._can_paste_curve_to_current_page(show_message=show_message):
            return False

        clipboard_curve = self._normalize_curve_snapshot(self.curve_clipboard)
        group = self._current_curve_group_snapshot()
        if clipboard_curve is None or group is None:
            return False

        target_kind, base_curve, overlay_curves = group
        clipboard_curve["curve_kind"] = target_kind
        overlay_curves = [copy.deepcopy(curve) for curve in overlay_curves]
        overlay_curves.append(copy.deepcopy(clipboard_curve))

        current_spec = self.left_workspace.current_spec()
        base_title = base_curve.get("source_title") or base_curve.get("title") or "1D 曲线"
        spec = AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=self._make_unique_page_title(f"比较 - {base_title}"),
            page_kind=self.COMPARISON_PAGE_KIND,
            source_module="data_process",
            source_page_id=current_spec.page_id if current_spec is not None else None,
            params={
                "comparison_kind": target_kind,
                "base_curve": copy.deepcopy(base_curve),
                "overlay_curves": overlay_curves,
            },
        )
        self._seed_control_state_for_spec(spec)
        self.left_workspace.add_page(spec)
        return True

    def _show_curve_context_menu(self, pos):
        context = self.current_render_context
        show_curve_actions = (
            context is not None
            and context.get("view") in {"1d", "1d_comparison"}
        )

        can_copy = self._can_copy_current_curve() if show_curve_actions else False
        can_paste = self._can_paste_curve_to_current_page(show_message=False) if show_curve_actions else False

        menu = QMenu(self.canvas_2d)
        menu.setStyleSheet("QMenu { color: white; background-color: #2A2A3A; } QMenu::item:selected { background-color: #3A3A5A; }")
        denoise_action = menu.addAction("去噪参数设置")
        denoise_action.triggered.connect(
            lambda: self.denoise_popup.show_at(self.canvas_2d.mapToGlobal(pos))
        )
        waterfall_action = menu.addAction("瀑布图步长设置")
        waterfall_action.triggered.connect(
            lambda: self.waterfall_popup.show_at(self.canvas_2d.mapToGlobal(pos))
        )
        sd_action = menu.addAction("二阶导参数设置")
        sd_action.triggered.connect(
            lambda: self.sd_popup.show_at(self.canvas_2d.mapToGlobal(pos))
        )

        if can_copy or can_paste:
            menu.addSeparator()
        if can_copy:
            copy_action = menu.addAction("复制")
            copy_action.triggered.connect(lambda: self._copy_current_curve_to_clipboard(show_message=True))
        if can_paste:
            paste_action = menu.addAction("粘贴")
            paste_action.triggered.connect(lambda: self._paste_curve_to_comparison_page(show_message=True))
        menu.exec_(self.canvas_2d.mapToGlobal(pos))

    def _show_main_context_menu(self, pos):
        menu = QMenu(self.plotter)
        menu.setStyleSheet("QMenu { color: white; background-color: #2A2A3A; } QMenu::item:selected { background-color: #3A3A5A; }")
        denoise_action = menu.addAction("去噪参数设置")
        denoise_action.triggered.connect(
            lambda: self.denoise_popup.show_at(self.plotter.mapToGlobal(pos))
        )
        waterfall_action = menu.addAction("瀑布图步长设置")
        waterfall_action.triggered.connect(
            lambda: self.waterfall_popup.show_at(self.plotter.mapToGlobal(pos))
        )
        sd_action = menu.addAction("二阶导参数设置")
        sd_action.triggered.connect(
            lambda: self.sd_popup.show_at(self.plotter.mapToGlobal(pos))
        )
        menu.exec_(self.plotter.mapToGlobal(pos))

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
            and spec.page_kind in {"axis_integral", "axis_integral_crop"}
            and self._normalize_axis_source_mode(spec.params.get("source_mode")) == "time_integral"
        )

    def _is_time_locked_page(self, spec):
        if spec is None:
            return False
        if self._is_time_integral_axis_page(spec):
            return True
        return (
            spec.page_kind in {"waterfall_edc", "edc_curve", "second_derivative"}
            and self._normalize_axis_source_mode(spec.params.get("source_mode")) == "time_integral"
        )

    def _update_time_slider_state(self):
        has_time_axis = self.core.has_time_axis and self.core.raw_data is not None and self.core.raw_data.shape[3] > 1
        active_spec = self._control_state_owner(self.left_workspace.current_spec())
        is_enabled = has_time_axis and not self._is_time_locked_page(active_spec)
        self.page_image.slider_time.setEnabled(is_enabled)
        self.page_image.input_time.setEnabled(is_enabled)

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
        can_export = (
            has_data
            and self._render_exact_ready
            and active_spec is not None
            and active_spec.page_kind not in {"control_panel", self.COMPARISON_PAGE_KIND}
        )
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

    @staticmethod
    def _axis_plot_info(axis_index):
        axis_index = int(axis_index)
        if axis_index == 0:
            return {
                "x_key": "Y",
                "y_key": "E",
                "x_label": "ky",
                "y_label": "E",
                "integrated_axis_key": "X",
                "integrated_axis_label": "X",
            }
        if axis_index == 1:
            return {
                "x_key": "X",
                "y_key": "E",
                "x_label": "kx",
                "y_label": "E",
                "integrated_axis_key": "Y",
                "integrated_axis_label": "Y",
            }
        return {
            "x_key": "X",
            "y_key": "Y",
            "x_label": "kx",
            "y_label": "ky",
            "integrated_axis_key": "E",
            "integrated_axis_label": "E",
        }

    @staticmethod
    def _normalize_plot_rect(rect):
        if rect is None:
            return None
        x_low = int(min(rect["x_low"], rect["x_up"]))
        x_up = int(max(rect["x_low"], rect["x_up"]))
        y_low = int(min(rect["y_low"], rect["y_up"]))
        y_up = int(max(rect["y_low"], rect["y_up"]))
        return {
            "x_low": x_low,
            "x_up": x_up,
            "y_low": y_low,
            "y_up": y_up,
        }

    @staticmethod
    def _copy_plot_rect(rect):
        if rect is None:
            return None
        return {
            "x_low": int(rect["x_low"]),
            "x_up": int(rect["x_up"]),
            "y_low": int(rect["y_low"]),
            "y_up": int(rect["y_up"]),
        }

    @staticmethod
    def _rect_matches_plot_bounds(rect, plot_bounds):
        if rect is None or plot_bounds is None:
            return False
        return (
            int(rect["x_low"]) == int(plot_bounds["x_low"])
            and int(rect["x_up"]) == int(plot_bounds["x_up"])
            and int(rect["y_low"]) == int(plot_bounds["y_low"])
            and int(rect["y_up"]) == int(plot_bounds["y_up"])
        )

    @staticmethod
    def _intersect_plot_rects(rect_a, rect_b):
        if rect_a is None:
            return None if rect_b is None else dict(rect_b)
        if rect_b is None:
            return dict(rect_a)
        x_low = max(int(rect_a["x_low"]), int(rect_b["x_low"]))
        x_up = min(int(rect_a["x_up"]), int(rect_b["x_up"]))
        y_low = max(int(rect_a["y_low"]), int(rect_b["y_low"]))
        y_up = min(int(rect_a["y_up"]), int(rect_b["y_up"]))
        if x_low > x_up or y_low > y_up:
            return None
        return {
            "x_low": x_low,
            "x_up": x_up,
            "y_low": y_low,
            "y_up": y_up,
        }

    def _axis_crop_rect_from_params(self, params):
        required = ("crop_k_low", "crop_k_up", "crop_e_low", "crop_e_up")
        if any(key not in params for key in required):
            return None
        return self._normalize_plot_rect(
            {
                "x_low": int(params["crop_k_low"]),
                "x_up": int(params["crop_k_up"]),
                "y_low": int(params["crop_e_low"]),
                "y_up": int(params["crop_e_up"]),
            }
        )

    def _resolved_axis_integral_params(self, spec):
        if spec is None:
            return None

        if self._is_current_page(spec) and spec.page_kind == "axis_integral":
            self._persist_axis_integral_page_state(spec)

        params = spec.params
        axis_index = int(params.get("axis_index", 0))
        low = int(params.get("low", 0))
        up = int(params.get("up", low))
        if low > up:
            low, up = up, low
        mid_default = round((low + up) / 2)
        mid = int(np.clip(int(params.get("mid", mid_default)), low, up))
        return {
            "axis_index": axis_index,
            "axis_name": params.get("axis_name"),
            "low": low,
            "up": up,
            "mid": mid,
            "source_mode": self._normalize_axis_source_mode(params.get("source_mode")),
            "source_t_index": int(params.get("source_t_index", int(self.page_image.slider_time.value()))),
            "source_t_low": int(params.get("source_t_low", int(self.page_data.s_t_low.value()))),
            "source_t_up": int(params.get("source_t_up", int(self.page_data.s_t_up.value()))),
        }

    def _build_axis_integral_base_context(self, spec, raw_data, coords):
        params = self._resolved_axis_integral_params(spec)
        if params is None:
            return None

        axis_index = int(params["axis_index"])
        data_2d = self._get_cached_axis_range_sum(spec, raw_data, params)
        axis_info = self._axis_plot_info(axis_index)
        plot_bounds = {
            "x_low": 0,
            "x_up": max(int(data_2d.shape[0]) - 1, 0),
            "y_low": 0,
            "y_up": max(int(data_2d.shape[1]) - 1, 0),
        }
        crop_rect = None
        if spec.page_kind == "axis_integral_crop":
            crop_rect = self._axis_crop_rect_from_params(spec.params)
        else:
            crop_rect = self._copy_plot_rect(self.axis_crop_candidates.get(spec.page_id))

        slice_info = {
            "axis": axis_index,
            "mode": "integral",
            "range": (int(params["low"]), int(params["up"])),
        }
        if params["source_mode"] == "time_integral":
            t_low, t_up = sorted((int(params["source_t_low"]), int(params["source_t_up"])))
            delay_axis = np.asarray(coords["delay"], dtype=np.float64)
            t_low = int(np.clip(t_low, 0, len(delay_axis) - 1))
            t_up = int(np.clip(t_up, 0, len(delay_axis) - 1))
            spatial_low = self.core.logical_to_physical(axis_info["integrated_axis_key"], int(params["low"]))
            spatial_up = self.core.logical_to_physical(axis_info["integrated_axis_key"], int(params["up"]))
            if int(params["low"]) == int(params["up"]):
                spatial_text = f"{axis_info['integrated_axis_label']}-Slice {spatial_low:.4g}"
            else:
                spatial_text = (
                    f"{axis_info['integrated_axis_label']}-Integral "
                    f"{spatial_low:.4g}~{spatial_up:.4g}"
                )
            slice_info["title_override"] = (
                f"Time-Integrated {spatial_text} "
                f"(Delay {delay_axis[t_low]:.4g}~{delay_axis[t_up]:.4g})"
            )

        return {
            "view": "2d",
            "data": np.asarray(data_2d, dtype=np.float64),
            "slice_info": slice_info,
            "coords": coords,
            "plot_axes": {
                "x_key": axis_info["x_key"],
                "y_key": axis_info["y_key"],
                "x_label": axis_info["x_label"],
                "y_label": axis_info["y_label"],
            },
            "plot_logical_bounds": plot_bounds,
            "crop_rect": crop_rect,
            "integral_params": params,
        }

    def _apply_axis_crop_to_context(self, context, crop_rect):
        if context is None or crop_rect is None:
            return context

        plot_bounds = context.get("plot_logical_bounds")
        cropped_rect = self._intersect_plot_rects(self._normalize_plot_rect(crop_rect), plot_bounds)
        if cropped_rect is None:
            return context

        x_low = int(cropped_rect["x_low"])
        x_up = int(cropped_rect["x_up"])
        y_low = int(cropped_rect["y_low"])
        y_up = int(cropped_rect["y_up"])

        x_slice = slice(x_low, x_up + 1)
        y_slice = slice(y_low, y_up + 1)
        plot_axes = context["plot_axes"]
        coords = context["coords"]
        x_coords = np.asarray(coords[plot_axes["x_key"]], dtype=np.float64)[x_slice]
        y_coords = np.asarray(coords[plot_axes["y_key"]], dtype=np.float64)[y_slice]

        slice_info = dict(context["slice_info"])
        slice_info["extent_override"] = [
            float(x_coords[0]),
            float(x_coords[-1]),
            float(y_coords[0]),
            float(y_coords[-1]),
        ]

        updated = dict(context)
        updated["data"] = np.asarray(context["data"][x_slice, y_slice], dtype=np.float64)
        updated["slice_info"] = slice_info
        updated["plot_logical_bounds"] = cropped_rect
        updated["crop_rect"] = cropped_rect
        return updated

    def _axis_crop_title(self, params, crop_rect):
        axis_index = int(params["axis_index"])
        axis_info = self._axis_plot_info(axis_index)
        crop_rect = self._normalize_plot_rect(crop_rect)
        k_low = self._format_filename_number(
            self.core.logical_to_physical(axis_info["x_key"], int(crop_rect["x_low"]))
        )
        k_up = self._format_filename_number(
            self.core.logical_to_physical(axis_info["x_key"], int(crop_rect["x_up"]))
        )
        e_low = self._format_filename_number(
            self.core.logical_to_physical(axis_info["y_key"], int(crop_rect["y_low"]))
        )
        e_up = self._format_filename_number(
            self.core.logical_to_physical(axis_info["y_key"], int(crop_rect["y_up"]))
        )
        return (
            f"{axis_info['integrated_axis_label']}-Integral Crop "
            f"[{axis_info['x_label']} {k_low}~{k_up}, {axis_info['y_label']} {e_low}~{e_up}]"
        )

    def _get_axis_integral_export_context(self, spec, raw_data, coords):
        if raw_data is None:
            return None

        params = self._resolved_axis_integral_params(spec)
        if params is None:
            return None

        axis_index = int(params["axis_index"])
        low = int(params["low"])
        up = int(params["up"])
        mid = int(params["mid"])

        if low > up:
            low, up = up, low

        axis_key = {0: "X", 1: "Y", 2: "E"}.get(axis_index, "X")
        axis_tag = {0: "x", 1: "y", 2: "z"}.get(axis_index, "x")
        axis_max = raw_data.shape[axis_index] - 1
        low = int(np.clip(low, 0, axis_max))
        up = int(np.clip(up, 0, axis_max))
        mid = int(np.clip(mid, low, up))

        if params["source_mode"] == "time_integral":
            context = self._build_axis_integral_base_context(spec, raw_data, coords)
            if context is None:
                return None

            axis_info = self._axis_plot_info(axis_index)
            export_data = {
                "sample": np.asarray(context["data"], dtype=np.float32),
                "integrated_axis": np.asarray([axis_info["integrated_axis_label"]]),
                "integrated_range": np.asarray(
                    [
                        self.core.logical_to_physical(axis_key, low),
                        self.core.logical_to_physical(axis_key, up),
                    ],
                    dtype=np.float32,
                ),
                "integrated_center": np.asarray(
                    [self.core.logical_to_physical(axis_key, mid)],
                    dtype=np.float32,
                ),
                "source_mode": np.asarray(["time_integral"]),
            }
            if axis_index == 0:
                export_data["ky"] = np.asarray(coords["Y"], dtype=np.float32)
                export_data["E"] = np.asarray(coords["E"], dtype=np.float32)
            elif axis_index == 1:
                export_data["kx"] = np.asarray(coords["X"], dtype=np.float32)
                export_data["E"] = np.asarray(coords["E"], dtype=np.float32)
            else:
                export_data["kx"] = np.asarray(coords["X"], dtype=np.float32)
                export_data["ky"] = np.asarray(coords["Y"], dtype=np.float32)
            export_data.update(self._time_integral_export_metadata(params, coords))

            low_text = self._format_filename_number(self.core.logical_to_physical(axis_key, low))
            up_text = self._format_filename_number(self.core.logical_to_physical(axis_key, up))
            mid_text = self._format_filename_number(self.core.logical_to_physical(axis_key, mid))
            default_name = self._sanitize_filename_component(
                f"time_integral_{low_text}_{up_text}_{mid_text}_{axis_tag}_{self._get_loaded_npz_stem()}"
            )
            return {"export_data": export_data, "default_name": default_name}

        if axis_index == 0:
            sample = np.sum(raw_data[low:up + 1, :, :, :], axis=0)
            export_data = {
                "sample": np.asarray(sample, dtype=np.float32),
                "ky": np.asarray(coords["Y"], dtype=np.float32),
                "E": np.asarray(coords["E"], dtype=np.float32),
                "time": np.asarray(coords["delay"], dtype=np.float32),
            }
        elif axis_index == 1:
            sample = np.sum(raw_data[:, low:up + 1, :, :], axis=1)
            export_data = {
                "sample": np.asarray(sample, dtype=np.float32),
                "kx": np.asarray(coords["X"], dtype=np.float32),
                "E": np.asarray(coords["E"], dtype=np.float32),
                "time": np.asarray(coords["delay"], dtype=np.float32),
            }
        else:
            sample = np.sum(raw_data[:, :, low:up + 1, :], axis=2)
            export_data = {
                "sample": np.asarray(sample, dtype=np.float32),
                "kx": np.asarray(coords["X"], dtype=np.float32),
                "ky": np.asarray(coords["Y"], dtype=np.float32),
                "time": np.asarray(coords["delay"], dtype=np.float32),
            }

        low_text = self._format_filename_number(self.core.logical_to_physical(axis_key, low))
        up_text = self._format_filename_number(self.core.logical_to_physical(axis_key, up))
        mid_text = self._format_filename_number(self.core.logical_to_physical(axis_key, mid))
        default_name = self._sanitize_filename_component(
            f"{low_text}_{up_text}_{mid_text}_{axis_tag}_{self._get_loaded_npz_stem()}"
        )

        return {"export_data": export_data, "default_name": default_name}

    @staticmethod
    def _time_integral_export_metadata(params, coords):
        delay_axis = np.asarray(coords["delay"], dtype=np.float64)
        t_low, t_up = sorted((int(params["source_t_low"]), int(params["source_t_up"])))
        t_low = int(np.clip(t_low, 0, len(delay_axis) - 1))
        t_up = int(np.clip(t_up, 0, len(delay_axis) - 1))
        return {
            "source_t_indices": np.asarray([t_low, t_up], dtype=np.int32),
            "source_t_range": np.asarray([delay_axis[t_low], delay_axis[t_up]], dtype=np.float32),
        }

    def _get_axis_integral_crop_export_context(self, spec, raw_data, coords):
        context = self._get_axis_integral_crop_context(spec, raw_data, coords)
        if context is None:
            return None

        plot_axes = context["plot_axes"]
        plot_bounds = context["plot_logical_bounds"]
        x_low = int(plot_bounds["x_low"])
        x_up = int(plot_bounds["x_up"])
        y_low = int(plot_bounds["y_low"])
        y_up = int(plot_bounds["y_up"])
        params = self._resolved_axis_integral_params(spec)
        axis_info = self._axis_plot_info(params["axis_index"])
        x_values = np.asarray(coords[plot_axes["x_key"]][x_low:x_up + 1], dtype=np.float32)
        y_values = np.asarray(coords[plot_axes["y_key"]][y_low:y_up + 1], dtype=np.float32)

        export_data = {
            "sample": np.asarray(context["data"], dtype=np.float32),
            "integrated_axis": np.asarray([axis_info["integrated_axis_label"]]),
            "integrated_range": np.asarray(
                [
                    self.core.logical_to_physical(axis_info["integrated_axis_key"], int(params["low"])),
                    self.core.logical_to_physical(axis_info["integrated_axis_key"], int(params["up"])),
                ],
                dtype=np.float32,
            ),
            "integrated_center": np.asarray(
                [
                    self.core.logical_to_physical(
                        axis_info["integrated_axis_key"],
                        int(params["mid"]),
                    )
                ],
                dtype=np.float32,
            ),
            "crop_range": np.asarray(
                [
                    self.core.logical_to_physical(plot_axes["x_key"], x_low),
                    self.core.logical_to_physical(plot_axes["x_key"], x_up),
                    self.core.logical_to_physical(plot_axes["y_key"], y_low),
                    self.core.logical_to_physical(plot_axes["y_key"], y_up),
                ],
                dtype=np.float32,
            ),
        }
        if params["source_mode"] == "time_integral":
            export_data["source_mode"] = np.asarray(["time_integral"])
            export_data.update(self._time_integral_export_metadata(params, coords))
        if plot_axes["y_key"] == "E":
            export_data["k"] = x_values
            export_data["E"] = y_values
        else:
            export_data[plot_axes["x_label"]] = x_values
            export_data[plot_axes["y_label"]] = y_values
        default_name = self._sanitize_filename_component(
            f"{axis_info['integrated_axis_label']}_integral_crop_{plot_axes['x_label']}_{x_low}_{x_up}_{plot_axes['y_label']}_{y_low}_{y_up}_{self._get_loaded_npz_stem()}"
        )
        return {"export_data": export_data, "default_name": default_name}

    @staticmethod
    def _safe_curve_normalize(curve):
        curve = np.asarray(curve, dtype=np.float64)
        max_value = np.max(curve) if curve.size else 0.0
        if not np.isfinite(max_value) or abs(max_value) < 1e-12:
            return np.zeros_like(curve, dtype=np.float64)
        return curve / max_value

    @staticmethod
    def _nearest_axis_indices(axis_values, step):
        values = np.asarray(axis_values, dtype=np.float64).flatten()
        if values.size == 0:
            return np.array([], dtype=np.int32)
        if values.size == 1:
            return np.array([0], dtype=np.int32)

        safe_step = max(float(step), 1e-4)
        axis_min = float(np.min(values))
        axis_max = float(np.max(values))
        sampled_positions = list(np.arange(axis_min, axis_max + safe_step * 0.5, safe_step))
        sampled_positions.extend([axis_min, axis_max])

        indices = {
            int(np.argmin(np.abs(values - position)))
            for position in sampled_positions
        }
        sorted_indices = sorted(indices, key=lambda idx: float(values[idx]))
        return np.asarray(sorted_indices, dtype=np.int32)

    def _resolve_waterfall_axis_info(self, axis_index):
        axis_index = int(axis_index)
        if axis_index == 0:
            return {
                "k_axis_key": "Y",
                "k_axis_label": "ky",
                "integrated_axis_label": "X",
            }
        if axis_index == 1:
            return {
                "k_axis_key": "X",
                "k_axis_label": "kx",
                "integrated_axis_label": "Y",
            }
        return None

    def _waterfall_title_summary(self, axis_label, integrated_axis_label, center_value, k_step, *, ascii_only=False):
        center_text = self._format_filename_number(center_value)
        step_text = f"{float(k_step):.3f}"
        if ascii_only:
            return f"EDC Waterfall [{axis_label}, {integrated_axis_label} center {center_text}, step {step_text}]"
        return f"EDC瀑布图 [{axis_label}, {integrated_axis_label}中心 {center_text}, 步长 {step_text}]"


    def _get_waterfall_edc_context(self, spec, raw_data, coords):
        return self._build_waterfall_context_from_axis_params(raw_data, coords, spec.params)

    def _extract_slice_data(self, data_3d, axis_idx, index):
        safe_index = int(np.clip(index, 0, data_3d.shape[axis_idx] - 1))
        if axis_idx == 0:
            return data_3d[safe_index, :, :]
        if axis_idx == 1:
            return data_3d[:, safe_index, :]
        return data_3d[:, :, safe_index]

    def _get_home_render_context(self, spec, raw_data, coords):
        if raw_data is None:
            return None

        t_idx = int(self.page_image.slider_time.value())
        data_3d = self._get_rotated_frame(raw_data, t_idx)

        if self.home_slice_info is not None:
            data_2d = self._extract_slice_data(data_3d, self.home_slice_info["axis"], self.home_slice_info["index"])
            return {"view": "2d", "data": data_2d, "slice_info": self.home_slice_info, "coords": coords}

        return {"view": "3d", "data": data_3d, "clip_ranges": self.clip_ranges, "coords": coords}

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
        low, up, mid = self._axis_input_logical_values()

        return {
            "axis_index": axis_index,
            "axis_name": axis_name,
            "low": int(low),
            "up": int(up),
            "mid": int(mid),
            "source_mode": normalized_source_mode,
            "source_page_kind": "time_integral" if normalized_source_mode == "time_integral" else "home",
            "source_t_index": int(self.page_image.slider_time.value()),
            "source_t_low": int(self.page_data.s_t_low.value()),
            "source_t_up": int(self.page_data.s_t_up.value()),
        }

    def _get_3d_source_context_for_axis(self, spec, raw_data):
        params = spec.params
        source_mode = self._normalize_axis_source_mode(params.get("source_mode"))
        if source_mode == "time_integral":
            t_low = params.get("source_t_low")
            t_up = params.get("source_t_up")
            if t_low is not None and t_up is not None:
                return {
                    "view": "3d",
                    "data": self._get_rotated_time_integral(raw_data, int(t_low), int(t_up)),
                }

        t_index = int(params.get("source_t_index", int(self.page_image.slider_time.value())))
        return {"view": "3d", "data": self._get_rotated_frame(raw_data, t_index)}

    def _get_time_integral_context(self, spec, raw_data, coords):
        if self._is_current_page(spec):
            self._persist_time_integral_page_state(spec)

        t_low = int(spec.params["t_low"])
        t_up = int(spec.params["t_up"])
        return {
            "view": "3d",
            "data": self._get_rotated_time_integral(raw_data, t_low, t_up),
            "coords": coords,
        }


    def _compute_slice_dos(self, raw_data, logical_bounds):
        clip_info = self._get_clip_slices(logical_bounds)
        if clip_info is None:
            return None

        slices, _ = clip_info
        return np.sum(raw_data[slices[0], slices[1], slices[2], :], axis=(0, 1, 2))

    def _get_slice_dos_context(self, spec, raw_data, coords):
        return {
            "view": "1d",
            "x_data": coords["delay"],
            "y_data": self._compute_slice_dos(raw_data, spec.params["clip_ranges"]),
            "title": "Slice Integrated Intensity vs Time",
            "xlabel": "Delay (ps)",
        }

    def _build_axis_source_spec_from_params(self, params, *, page_id="axis_source", title="axis_source"):
        axis_index = int(params.get("axis_index", -1))
        if axis_index not in (0, 1, 2):
            return None

        low = int(params.get("integral_low", params.get("low", 0)))
        up = int(params.get("integral_up", params.get("up", low)))
        if low > up:
            low, up = up, low
        mid_default = round((low + up) / 2)
        spec_params = {
            "axis_index": axis_index,
            "low": int(low),
            "up": int(up),
            "mid": int(params.get("integral_mid", params.get("mid", mid_default))),
            "source_mode": self._normalize_axis_source_mode(params.get("source_mode")),
            "source_t_index": int(params.get("source_t_index", int(self.page_image.slider_time.value()))),
            "source_t_low": int(params.get("source_t_low", int(self.page_data.s_t_low.value()))),
            "source_t_up": int(params.get("source_t_up", int(self.page_data.s_t_up.value()))),
        }

        crop_rect = self._axis_crop_rect_from_params(params)
        page_kind = "axis_integral_crop" if crop_rect is not None else "axis_integral"
        if crop_rect is not None:
            spec_params.update(
                {
                    "crop_k_low": int(crop_rect["x_low"]),
                    "crop_k_up": int(crop_rect["x_up"]),
                    "crop_e_low": int(crop_rect["y_low"]),
                    "crop_e_up": int(crop_rect["y_up"]),
                }
            )

        return AnalysisPageSpec(
            page_id=page_id,
            title=title,
            page_kind=page_kind,
            source_module="data_process",
            params=spec_params,
        )

    def _build_axis_like_context_from_params(self, raw_data, coords, params):
        source_spec = self._build_axis_source_spec_from_params(params)
        if source_spec is None:
            return None
        if source_spec.page_kind == "axis_integral_crop":
            return self._get_axis_integral_crop_context(source_spec, raw_data, coords)
        return self._get_axis_integral_context(source_spec, raw_data, coords)

    def _get_energy_dos_context(self, spec, raw_data, coords):
        source_kind = spec.params.get("source_page_kind", "home")
        if source_kind == "axis_integral":
            source_context = self._build_axis_like_context_from_params(raw_data, coords, spec.params)
            if source_context is None:
                return None

            plot_axes = source_context["plot_axes"]
            plot_bounds = source_context["plot_logical_bounds"]
            y_low = int(plot_bounds["y_low"])
            y_up = int(plot_bounds["y_up"])
            energy_axis = np.asarray(coords[plot_axes["y_key"]], dtype=np.float64)[y_low:y_up + 1]
            axis_label = {0: "X-integral", 1: "Y-integral"}.get(int(spec.params.get("axis_index", -1)), "Axis-integral")
            crop_suffix = self._waterfall_crop_suffix(spec.params, ascii_only=True)
            return {
                "view": "1d",
                "x_data": energy_axis,
                "y_data": np.sum(source_context["data"], axis=0),
                "title": f"Energy DOS ({axis_label}{crop_suffix})",
                "xlabel": "Energy (eV)",
                "plot_axes": plot_axes,
                "plot_logical_bounds": plot_bounds,
            }

        if self._is_current_page(spec):
            t_index = int(self.page_image.slider_time.value())
        else:
            t_index = int(spec.params["t_index"])
        data_3d = self._get_rotated_frame(raw_data, t_index)
        energy_axis = np.asarray(coords["E"], dtype=np.float64)
        clipped = False
        clip_ranges = spec.params.get("clip_ranges")
        if clip_ranges is not None:
            clip_info = self._get_clip_slices(clip_ranges)
            if clip_info is not None:
                slices, _ = clip_info
                data_3d = data_3d[slices[0], slices[1], slices[2]]
                energy_axis = energy_axis[slices[2]]
                clipped = True
        return {
            "view": "1d",
            "x_data": energy_axis,
            "y_data": np.sum(data_3d, axis=(0, 1)),
            "title": f"Energy DOS ({'Clipped, ' if clipped else ''}T={self._current_delay_text(t_index)})",
            "xlabel": "Energy (eV)",
        }

    def _edc_curve_title(self, params):
        crop_rect = self._axis_crop_rect_from_params(params)
        if crop_rect is None:
            return "EDC Curve"

        kx_low = self._format_filename_number(self.core.logical_to_physical("X", int(crop_rect["x_low"])))
        kx_up = self._format_filename_number(self.core.logical_to_physical("X", int(crop_rect["x_up"])))
        ky_low = self._format_filename_number(self.core.logical_to_physical("Y", int(crop_rect["y_low"])))
        ky_up = self._format_filename_number(self.core.logical_to_physical("Y", int(crop_rect["y_up"])))
        return f"EDC Curve [kx {kx_low}~{kx_up}, ky {ky_low}~{ky_up}]"

    def _build_edc_curve_context_from_params(self, raw_data, coords, params):
        if raw_data is None or coords is None:
            return None
        if int(params.get("axis_index", -1)) != 2:
            return None

        crop_rect = self._axis_crop_rect_from_params(params)
        if crop_rect is None:
            return None

        source_spec = self._build_axis_source_spec_from_params(params)
        if source_spec is None:
            return None
        source_context = self._get_3d_source_context_for_axis(source_spec, raw_data)
        source_data = np.asarray(source_context["data"], dtype=np.float64)

        x_low = int(np.clip(crop_rect["x_low"], 0, source_data.shape[0] - 1))
        x_up = int(np.clip(crop_rect["x_up"], 0, source_data.shape[0] - 1))
        y_low = int(np.clip(crop_rect["y_low"], 0, source_data.shape[1] - 1))
        y_up = int(np.clip(crop_rect["y_up"], 0, source_data.shape[1] - 1))
        if x_low > x_up:
            x_low, x_up = x_up, x_low
        if y_low > y_up:
            y_low, y_up = y_up, y_low

        selected_cube = source_data[x_low:x_up + 1, y_low:y_up + 1, :]
        intensity = np.sum(selected_cube, axis=(0, 1))
        energy_axis = np.asarray(coords["E"], dtype=np.float64)

        source_mode = self._normalize_axis_source_mode(params.get("source_mode"))
        context = {
            "view": "1d",
            "x_data": energy_axis,
            "y_data": intensity,
            "title": self._edc_curve_title(params),
            "xlabel": "Energy (eV)",
            "crop_rect": {
                "x_low": x_low,
                "x_up": x_up,
                "y_low": y_low,
                "y_up": y_up,
            },
            "kx_range": (
                float(self.core.logical_to_physical("X", x_low)),
                float(self.core.logical_to_physical("X", x_up)),
            ),
            "ky_range": (
                float(self.core.logical_to_physical("Y", y_low)),
                float(self.core.logical_to_physical("Y", y_up)),
            ),
            "source_mode": source_mode,
        }
        if source_mode == "time_integral":
            t_low = int(params.get("source_t_low", int(self.page_data.s_t_low.value())))
            t_up = int(params.get("source_t_up", int(self.page_data.s_t_up.value())))
            context["source_t_range"] = (
                float(coords["delay"][t_low]),
                float(coords["delay"][t_up]),
            )
            context["source_t_indices"] = (t_low, t_up)
        else:
            t_index = int(params.get("source_t_index", int(self.page_image.slider_time.value())))
            context["source_t_index"] = t_index
            context["source_t_value"] = float(coords["delay"][t_index])
        return context

    def _get_edc_curve_context(self, spec, raw_data, coords):
        return self._build_edc_curve_context_from_params(raw_data, coords, spec.params)

    def _get_curve_comparison_context(self, spec):
        comparison_kind = self._curve_kind_for_spec(spec)
        if comparison_kind not in self.COMPARABLE_1D_PAGE_KINDS:
            return None

        base_curve = self._normalize_curve_snapshot(spec.params.get("base_curve"))
        if base_curve is None:
            return None
        base_curve["curve_kind"] = comparison_kind

        curves = [base_curve]
        for curve in spec.params.get("overlay_curves", []):
            normalized = self._normalize_curve_snapshot(curve)
            if normalized is None:
                continue
            if normalized.get("curve_kind") not in (None, comparison_kind):
                continue
            if not self._curve_x_data_matches(base_curve, normalized):
                continue
            normalized["curve_kind"] = comparison_kind
            curves.append(normalized)

        return {
            "view": "1d_comparison",
            "title": spec.title,
            "xlabel": base_curve.get("xlabel") or "",
            "ylabel": "Intensity (a.u.)",
            "comparison_kind": comparison_kind,
            "curves": curves,
        }

    @staticmethod
    def _compute_axis_second_derivative(
        data,
        coordinate_axis=None,
        derivative_axis=-1,
        sigma=1.5,
        threshold=0.0,
    ):
        """Return max(0, -d2I/dq2) along one selected array axis."""
        input_data = np.asarray(data)
        compute_dtype = (
            np.float32
            if input_data.dtype.kind == "f" and input_data.dtype.itemsize <= 4
            else np.float64
        )
        source = np.asarray(input_data, dtype=compute_dtype)
        if source.ndim == 0 or source.size == 0:
            return source.copy()

        axis_index = int(derivative_axis)
        if axis_index < 0:
            axis_index += source.ndim
        if axis_index < 0 or axis_index >= source.ndim:
            raise ValueError("Derivative axis is outside the source-data dimensions.")
        if source.shape[axis_index] < 3:
            return np.zeros_like(source)

        sigma_value = max(float(sigma), 0.0)
        smoothed = (
            gaussian_filter1d(source, sigma=sigma_value, axis=axis_index)
            if sigma_value > 0
            else source
        )

        coordinate_values = (
            np.asarray(coordinate_axis, dtype=np.float64)
            if coordinate_axis is not None
            else None
        )
        if coordinate_values is not None:
            coordinate_values = np.ravel(coordinate_values)
        if (
            coordinate_values is None
            or coordinate_values.size != source.shape[axis_index]
            or not np.all(np.isfinite(coordinate_values))
        ):
            coordinate_values = np.arange(source.shape[axis_index], dtype=np.float64)
        else:
            coordinate_steps = np.diff(coordinate_values)
            if not (np.all(coordinate_steps > 0) or np.all(coordinate_steps < 0)):
                coordinate_values = np.arange(source.shape[axis_index], dtype=np.float64)

        first_derivative = np.gradient(
            smoothed,
            coordinate_values,
            axis=axis_index,
            edge_order=2,
        )
        curvature = -np.gradient(
            first_derivative,
            coordinate_values,
            axis=axis_index,
            edge_order=2,
        )
        curvature[curvature < float(threshold)] = 0
        return curvature

    @staticmethod
    def _compute_energy_second_derivative(data_2d, energy_axis=None, sigma=1.5, threshold=0.0):
        """Return the energy-axis result for a (momentum, energy) image."""
        source = np.asarray(data_2d)
        if source.ndim != 2 or source.size == 0:
            return source.astype(np.float64, copy=True)
        return My3DAnalyzer._compute_axis_second_derivative(
            source,
            coordinate_axis=energy_axis,
            derivative_axis=1,
            sigma=sigma,
            threshold=threshold,
        )

    def _second_derivative_source_label(self, params):
        if params.get("source_view") == "3d":
            derivative_label = self._second_derivative_axis_label(params.get("derivative_axis"))
            source_prefix = "时间积分3D" if params.get("source_page_kind") == "time_integral" else "3D"
            return f"{source_prefix}-{derivative_label}方向"
        if params.get("source_page_kind") == "home":
            axis_index = int(params.get("slice_axis", -1))
            axis_name = {0: "X切片", 1: "Y切片"}.get(axis_index, "切片")
            return f"{axis_name}"

        axis_index = int(params.get("axis_index", -1))
        axis_name = {0: "X轴积分", 1: "Y轴积分"}.get(axis_index, "坐标轴积分")
        return axis_name

    def _second_derivative_plot_label(self, params):
        if params.get("source_view") == "3d":
            derivative_label = self._second_derivative_axis_label(params.get("derivative_axis"))
            source_prefix = "Time-integrated-3D" if params.get("source_page_kind") == "time_integral" else "3D"
            return f"{source_prefix}-{derivative_label}"
        if params.get("source_page_kind") == "home":
            axis_index = int(params.get("slice_axis", -1))
            return {0: "X-slice", 1: "Y-slice"}.get(axis_index, "Slice")

        axis_index = int(params.get("axis_index", -1))
        return {0: "X-integral", 1: "Y-integral"}.get(axis_index, "Axis-integral")

    @staticmethod
    def _second_derivative_axis_label(axis_index):
        try:
            normalized_axis = int(axis_index)
        except (TypeError, ValueError):
            normalized_axis = -1
        return {0: "X", 1: "Y", 2: "E"}.get(normalized_axis, "Unknown")

    def _ask_second_derivative_axis(self):
        items = ["X 轴（Kx）", "Y 轴（Ky）", "E 轴（能量）"]
        dialog = QInputDialog(self)
        dialog.setWindowTitle("选择二阶导方向")
        dialog.setLabelText("请选择 3D 数据的求导方向：")
        dialog.setComboBoxItems(items)
        dialog.setComboBoxEditable(False)
        dialog.setTextValue(items[2])
        dialog.setStyleSheet(
            """
            QInputDialog {
                color: #FFFFFF;
                background-color: #20202C;
            }
            QInputDialog QLabel {
                color: #FFFFFF;
                background-color: transparent;
            }
            QInputDialog QComboBox {
                color: #FFFFFF;
                background-color: #2A2A3A;
                border: 1px solid #5A5A70;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QInputDialog QComboBox QAbstractItemView {
                color: #FFFFFF;
                background-color: #2A2A3A;
                selection-color: #FFFFFF;
                selection-background-color: #50506A;
            }
            QInputDialog QPushButton {
                color: #FFFFFF;
                background-color: #3A3A50;
                border: 1px solid #62627A;
                border-radius: 4px;
                min-width: 64px;
                padding: 4px 10px;
            }
            QInputDialog QPushButton:hover {
                background-color: #50506A;
            }
            """
        )
        if dialog.exec_() != QInputDialog.Accepted:
            return None
        selected = dialog.textValue()
        return items.index(selected)

    def _get_cached_3d_second_derivative(
        self,
        raw_data,
        data_3d,
        coords,
        derivative_axis,
        source_signature,
        sigma=1.5,
        threshold=0.0,
    ):
        coordinate_key = {0: "X", 1: "Y", 2: "E"}[int(derivative_axis)]
        coordinate_values = np.asarray(coords[coordinate_key], dtype=np.float64)
        cache_key = (
            tuple(source_signature),
            round(float(self.rotation_angle), 2),
            int(derivative_axis),
            tuple(int(size) for size in data_3d.shape),
            coordinate_values.dtype.str,
            coordinate_values.tobytes(),
            round(float(sigma), 4),
            round(float(threshold), 4),
        )
        cache = self._second_derivative_volume_cache
        if (
            cache is not None
            and cache.get("raw_data") is raw_data
            and cache.get("key") == cache_key
        ):
            return cache["data"]

        derivative = self._compute_axis_second_derivative(
            data_3d,
            coordinate_axis=coordinate_values,
            derivative_axis=derivative_axis,
            sigma=sigma,
            threshold=threshold,
        )
        self._second_derivative_volume_cache = {
            "raw_data": raw_data,
            "key": cache_key,
            "data": derivative,
        }
        return derivative

    def _build_second_derivative_context_from_params(self, raw_data, coords, params):
        if raw_data is None or coords is None:
            return None

        source_kind = params.get("source_page_kind")
        if params.get("source_view") == "3d" and source_kind in {"home", "time_integral"}:
            if source_kind == "time_integral":
                t_low, t_up = sorted(
                    (int(params["source_t_low"]), int(params["source_t_up"]))
                )
                data_3d = self._get_rotated_time_integral(raw_data, t_low, t_up)
                source_signature = ("time_integral", t_low, t_up)
                source_metadata = {
                    "source_mode": "time_integral",
                    "source_t_indices": (t_low, t_up),
                }
            else:
                t_index = int(params.get("source_t_index", int(self.page_image.slider_time.value())))
                data_3d = self._get_rotated_frame(raw_data, t_index)
                source_signature = ("frame", t_index)
                source_metadata = {
                    "source_mode": "frame",
                    "source_t_index": t_index,
                }

            derivative_axis = int(params.get("derivative_axis", -1))
            coordinate_key = {0: "X", 1: "Y", 2: "E"}.get(derivative_axis)
            if coordinate_key is None:
                return None
            sd_params = params.get("second_derivative_params") or {}
            derivative = self._get_cached_3d_second_derivative(
                raw_data,
                data_3d,
                coords,
                derivative_axis,
                source_signature,
                sigma=float(sd_params.get("sigma", 1.5)),
                threshold=float(sd_params.get("threshold", 0.0)),
            )
            return {
                "view": "3d",
                "data": derivative,
                "coords": coords,
                "clip_ranges": self._copy_state(params.get("clip_ranges")),
                "derivative_axis": derivative_axis,
                **source_metadata,
            }

        if source_kind == "home":
            t_index = int(params.get("source_t_index", int(self.page_image.slider_time.value())))
            data_3d = self._get_rotated_frame(raw_data, t_index)
            slice_axis = int(params.get("slice_axis", -1))
            if slice_axis not in (0, 1):
                return None
            source_data = self._extract_slice_data(data_3d, slice_axis, int(params["slice_index"]))
            source_slice_info = {"axis": slice_axis, "index": int(params["slice_index"])}
            plot_axes = self._axis_plot_info(slice_axis)
            plot_bounds = {
                "x_low": 0,
                "x_up": max(int(source_data.shape[0]) - 1, 0),
                "y_low": 0,
                "y_up": max(int(source_data.shape[1]) - 1, 0),
            }
        elif source_kind == "axis_integral":
            base_context = self._build_axis_like_context_from_params(raw_data, coords, params)
            if base_context is None:
                return None
            source_data = base_context["data"]
            source_slice_info = dict(base_context["slice_info"])
            plot_axes = dict(base_context["plot_axes"])
            plot_bounds = dict(base_context["plot_logical_bounds"])
        else:
            return None

        slice_axis = int(source_slice_info.get("axis", -1))
        if slice_axis not in (0, 1):
            return None

        energy_low = int(plot_bounds["y_low"])
        energy_up = int(plot_bounds["y_up"])
        energy_axis = np.asarray(coords[plot_axes["y_key"]], dtype=np.float64)[energy_low:energy_up + 1]

        sd_params = params.get("second_derivative_params") or {}
        sigma = float(sd_params.get("sigma", 1.5))
        threshold = float(sd_params.get("threshold", 0.0))
        cross_axis_sigma = float(sd_params.get("cross_axis_sigma", 0.8))

        derivative = self._compute_energy_second_derivative(
            source_data,
            energy_axis=energy_axis,
            sigma=sigma,
            threshold=threshold,
        )

        if cross_axis_sigma > 0 and derivative.ndim == 2:
            derivative = gaussian_filter1d(
                derivative,
                sigma=cross_axis_sigma,
                axis=0,
            )
        title = f"Energy Second Derivative - {self._second_derivative_plot_label(params)}"
        source_slice_info["title_override"] = title
        return {
            "view": "2d",
            "data": derivative,
            "slice_info": source_slice_info,
            "coords": coords,
            "plot_axes": plot_axes,
            "plot_logical_bounds": plot_bounds,
        }

    def _get_second_derivative_context(self, spec, raw_data, coords):
        return self._build_second_derivative_context_from_params(raw_data, coords, spec.params)

    def _get_axis_integral_context(self, spec, raw_data, coords):
        return self._build_axis_integral_base_context(spec, raw_data, coords)

    def _get_axis_integral_crop_context(self, spec, raw_data, coords):
        context = self._build_axis_integral_base_context(spec, raw_data, coords)
        if context is None:
            return None
        return self._apply_axis_crop_to_context(context, self._axis_crop_rect_from_params(spec.params))

    def _waterfall_crop_suffix(self, params, *, ascii_only=False):
        crop_rect = self._axis_crop_rect_from_params(params)
        if crop_rect is None:
            return ""

        axis_index = int(params.get("axis_index", -1))
        axis_info = self._resolve_waterfall_axis_info(axis_index)
        if axis_info is None:
            return ""

        k_low = self._format_filename_number(self.core.logical_to_physical(axis_info["k_axis_key"], crop_rect["x_low"]))
        k_up = self._format_filename_number(self.core.logical_to_physical(axis_info["k_axis_key"], crop_rect["x_up"]))
        e_low = self._format_filename_number(self.core.logical_to_physical("E", crop_rect["y_low"]))
        e_up = self._format_filename_number(self.core.logical_to_physical("E", crop_rect["y_up"]))
        if ascii_only:
            return f", {axis_info['k_axis_label']} {k_low}~{k_up}, E {e_low}~{e_up}"
        return f", {axis_info['k_axis_label']} {k_low}~{k_up}, E {e_low}~{e_up}"

    def _build_waterfall_context_from_axis_params(self, raw_data, coords, params):
        if raw_data is None or coords is None:
            return None

        axis_index = int(params.get("axis_index", -1))
        if axis_index not in (0, 1):
            return None

        source_spec = AnalysisPageSpec(
            page_id="waterfall_source",
            title="waterfall_source",
            page_kind="axis_integral",
            source_module="data_process",
            params={
                "axis_index": int(params["axis_index"]),
                "low": int(params["integral_low"]),
                "up": int(params["integral_up"]),
                "mid": int(params.get("integral_mid", round((int(params["integral_low"]) + int(params["integral_up"])) / 2))),
                "source_mode": params.get("source_mode", "frame"),
                "source_t_index": int(params.get("source_t_index", int(self.page_image.slider_time.value()))),
                "source_t_low": int(params.get("source_t_low", int(self.page_data.s_t_low.value()))),
                "source_t_up": int(params.get("source_t_up", int(self.page_data.s_t_up.value()))),
            },
        )
        context = self._build_axis_integral_base_context(source_spec, raw_data, coords)
        crop_rect = self._axis_crop_rect_from_params(params)
        if crop_rect is not None:
            context = self._apply_axis_crop_to_context(context, crop_rect)
        if context is None:
            return None

        axis_info = self._resolve_waterfall_axis_info(axis_index)
        if axis_info is None:
            return None

        plot_bounds = context["plot_logical_bounds"]
        x_low = int(plot_bounds["x_low"])
        x_up = int(plot_bounds["x_up"])
        y_low = int(plot_bounds["y_low"])
        y_up = int(plot_bounds["y_up"])

        data_2d = np.asarray(context["data"], dtype=np.float64)
        k_coords = np.asarray(coords[axis_info["k_axis_key"]], dtype=np.float64)[x_low:x_up + 1]
        energy_axis = np.asarray(coords["E"], dtype=np.float64)[y_low:y_up + 1]

        k_step = max(float(params["k_step"]), 1e-4)
        sampled_indices = self._nearest_axis_indices(k_coords, k_step)
        if sampled_indices.size < 2:
            raise ValueError("Current k step is too large to generate at least two EDC curves.")

        valid_indices = sampled_indices[(sampled_indices >= 0) & (sampled_indices < data_2d.shape[0])]
        valid_indices = np.unique(valid_indices)
        if valid_indices.size < 2:
            raise ValueError("Current k step is too large to generate at least two EDC curves.")

        k_values = k_coords[valid_indices]
        curves = np.asarray(data_2d[valid_indices, :], dtype=np.float64)
        normalized = np.asarray([self._safe_curve_normalize(curve) for curve in curves], dtype=np.float64)

        low_physical = self.core.logical_to_physical(axis_info["integrated_axis_label"], int(params["integral_low"]))
        up_physical = self.core.logical_to_physical(axis_info["integrated_axis_label"], int(params["integral_up"]))
        center_physical = self.core.logical_to_physical(
            axis_info["integrated_axis_label"],
            int(params.get("integral_mid", round((int(params["integral_low"]) + int(params["integral_up"])) / 2))),
        )
        title = self._waterfall_title_summary(
            axis_info["k_axis_label"],
            axis_info["integrated_axis_label"],
            center_physical,
            k_step,
            ascii_only=True,
        ) + self._waterfall_crop_suffix(params, ascii_only=True)

        return {
            "view": "waterfall",
            "title": title,
            "energy_axis": energy_axis,
            "k_values": np.asarray(k_values, dtype=np.float64),
            "curves": normalized,
            "raw_curves": curves,
            "offset_step": 1.2,
            "xlabel": "Intensity (normalized, arb. u.)",
            "ylabel": "Energy (eV)",
            "k_axis_label": axis_info["k_axis_label"],
            "integrated_axis_label": axis_info["integrated_axis_label"],
            "integrated_range": (float(low_physical), float(up_physical)),
            "integrated_center": float(center_physical),
            "k_step": float(k_step),
            "crop_rect": self._copy_plot_rect(crop_rect),
            "energy_range": (
                float(energy_axis[0]),
                float(energy_axis[-1]),
            ),
        }

    def _compute_render_context(self, spec):
        if spec.page_kind == "control_panel":
            return {"view": "config"}
        if spec.page_kind == self.COMPARISON_PAGE_KIND:
            return self._get_curve_comparison_context(spec)

        raw_data, coords = self._get_display_state_for_spec(spec)
        if raw_data is None or coords is None:
            return None

        if spec.page_kind == "home":
            return self._get_home_render_context(spec, raw_data, coords)
        if spec.page_kind == "time_integral":
            return self._get_time_integral_context(spec, raw_data, coords)
        if spec.page_kind == "axis_integral":
            return self._get_axis_integral_context(spec, raw_data, coords)
        if spec.page_kind == "axis_integral_crop":
            return self._get_axis_integral_crop_context(spec, raw_data, coords)
        if spec.page_kind == "slice_dos":
            return self._get_slice_dos_context(spec, raw_data, coords)
        if spec.page_kind == "energy_dos":
            return self._get_energy_dos_context(spec, raw_data, coords)
        if spec.page_kind == "waterfall_edc":
            return self._get_waterfall_edc_context(spec, raw_data, coords)
        if spec.page_kind == "edc_curve":
            return self._get_edc_curve_context(spec, raw_data, coords)
        if spec.page_kind == "second_derivative":
            return self._get_second_derivative_context(spec, raw_data, coords)
        return self._get_home_render_context(spec, raw_data, coords)

    @staticmethod
    def _display_title_for_1d_plot(title):
        title = str(title or "")
        if title.startswith("EDC Curve [") and title.endswith("]"):
            return title.replace(" [", "\n[", 1)
        return title

    def _apply_1d_plot_layout(self, *, compact_title=False):
        try:
            self.fig.tight_layout(pad=1.8 if compact_title else 1.5)
        except Exception:
            pass

        params = self.fig.subplotpars
        left = min(max(params.left, 0.16), 0.30)
        bottom = min(max(params.bottom, 0.16), 0.28)
        right = max(min(params.right, 0.96), left + 0.35)
        top_limit = 0.82 if compact_title else 0.88
        top = max(min(params.top, top_limit), bottom + 0.35)
        self.fig.subplots_adjust(left=left, right=right, bottom=bottom, top=top)

    @staticmethod
    def _ascending_curve_data(x_data, y_data):
        x_values = np.asarray(x_data)
        y_values = np.asarray(y_data)
        if (
            x_values.size > 1
            and y_values.size == x_values.size
            and float(x_values.reshape(-1)[0]) > float(x_values.reshape(-1)[-1])
        ):
            x_values = np.flip(x_values, axis=0)
            y_values = np.flip(y_values, axis=0)
        return x_values, y_values

    def _render_1d_plot(self, context):
        VisualEngine.clear_2d_colorbar(self.ax_2d)
        x_data, y_data = self._ascending_curve_data(context["x_data"], context["y_data"])
        line = getattr(self.ax_2d, "_arpes_line", None)
        can_update = (
            line is not None
            and getattr(line, "axes", None) is self.ax_2d
            and line in self.ax_2d.lines
            and len(self.ax_2d.lines) == 1
            and not self.ax_2d.images
        )
        if can_update:
            line.set_data(x_data, y_data)
            self.ax_2d.relim()
            self.ax_2d.autoscale_view()
        else:
            self.ax_2d.clear()
            line, = self.ax_2d.plot(x_data, y_data, color="#FF69B4", linewidth=2)
        self.ax_2d._arpes_line = line
        display_title = self._display_title_for_1d_plot(context["title"])
        compact_title = display_title != str(context["title"])
        self.ax_2d.set_title(display_title, color="white", fontsize=11, pad=12)
        self.ax_2d.set_xlabel(context["xlabel"], color="white")
        self.ax_2d.set_ylabel("Intensity (a.u.)", color="white")
        self.ax_2d.tick_params(colors="white", labelsize=9)
        self.ax_2d.margins(x=0.02, y=0.08)
        self._apply_1d_plot_layout(compact_title=compact_title)
        self.canvas_2d.draw_idle()

    def _render_1d_comparison_plot(self, context):
        VisualEngine.clear_2d_colorbar(self.ax_2d)
        self.ax_2d.clear()
        self.ax_2d._arpes_line = None

        colors = ["#FF69B4", "#4CC9F0", "#F9C74F", "#90BE6D", "#F3722C", "#B388FF", "#43AA8B"]
        linestyles = ["-", "--", "-.", ":"]
        for idx, curve in enumerate(context.get("curves", [])):
            x_data, y_data = self._ascending_curve_data(
                np.asarray(curve["x_data"], dtype=np.float64),
                np.asarray(curve["y_data"], dtype=np.float64),
            )
            label = curve.get("label") or curve.get("source_title") or f"Curve {idx + 1}"
            if idx == 0:
                label = f"基准 - {label}"
            self.ax_2d.plot(
                x_data,
                y_data,
                color=colors[idx % len(colors)],
                linestyle=linestyles[(idx // len(colors)) % len(linestyles)],
                linewidth=2.0 if idx == 0 else 1.8,
                label=label,
            )

        display_title = self._display_title_for_1d_plot(context["title"])
        self.ax_2d.set_title(display_title, color="white", fontsize=11, pad=12)
        self.ax_2d.set_xlabel(context["xlabel"], color="white")
        self.ax_2d.set_ylabel(context.get("ylabel", "Intensity (a.u.)"), color="white")
        self.ax_2d.tick_params(colors="white", labelsize=9)
        legend = self.ax_2d.legend(facecolor="#2A2A3A", edgecolor="#FFFFFF", fontsize=8)
        if legend is not None:
            for text in legend.get_texts():
                text.set_color("white")
        self.ax_2d.margins(x=0.02, y=0.08)
        self._apply_1d_plot_layout(compact_title=False)
        self.canvas_2d.draw()

    def _render_waterfall_plot(self, context):
        VisualEngine.clear_2d_colorbar(self.ax_2d)
        self.ax_2d.clear()
        self.ax_2d._arpes_line = None

        energy_axis = np.asarray(context["energy_axis"], dtype=np.float64)
        curves = np.asarray(context["curves"], dtype=np.float64)
        if energy_axis.size > 1 and float(energy_axis[0]) > float(energy_axis[-1]):
            energy_axis = np.flip(energy_axis, axis=0)
            curves = np.flip(curves, axis=1)
        k_values = np.asarray(context["k_values"], dtype=np.float64)
        offset_step = float(context.get("offset_step", 1.2))
        xaxis_transform = self.ax_2d.get_xaxis_transform()

        for idx, curve in enumerate(curves):
            offset = idx * offset_step
            self.ax_2d.plot(curve + offset, energy_axis, color="black", linewidth=1.5)
            self.ax_2d.text(
                offset + 0.5,
                1.03,
                f"{k_values[idx]:.2f}",
                color="white",
                fontsize=8,
                ha="center",
                va="bottom",
                transform=xaxis_transform,
                clip_on=False,
            )

        self.ax_2d.set_title(context["title"], color="white", pad=28)
        self.ax_2d.set_xlabel(context["xlabel"], color="white")
        self.ax_2d.set_ylabel(context["ylabel"], color="white")
        self.ax_2d.tick_params(colors="white")
        self.ax_2d.set_xlim(-0.1, max(1.25, (len(curves) - 1) * offset_step + 1.1))
        self.fig.tight_layout(rect=[0, 0, 1, 0.95])
        self.canvas_2d.draw()

    def global_refresh(self):
        self.request_refresh(RefreshCause.PAGE_ACTIVATION, RenderQuality.EXACT, immediate=True)

    def on_result_page_activated(self, page_id):
        previous_page_id = self.active_page_spec.page_id if self.active_page_spec is not None else None
        if previous_page_id is not None and previous_page_id != page_id:
            self.refresh_coordinator.cancel_page(previous_page_id)
        self._persist_active_page_state()
        spec = self.left_workspace.page_by_id(page_id)
        if spec is None:
            return

        self.active_page_spec = spec
        if spec.page_kind != "control_panel":
            self.last_visual_page_id = spec.page_id
        self._sync_controls_from_page(spec)
        self._update_time_slider_state()
        self.global_refresh()

    def _sync_controls_from_page(self, spec):
        self._restore_page_ui_state(spec)

    def on_toggle_e_flip(self, checked):
        if self.base_raw_data is None:
            return

        self._refresh_core_display_state()
        self.update_ax_slider_range()
        self._sync_slice_edits_from_logical_bounds(self.clip_ranges)
        self.global_refresh()

    def on_rotation_angle_changed(self):
        """Handle edit finished in the Z-axis rotation angle spin box."""
        angle = self.page_image.get_rotation_angle()
        self.rotation_angle = round(angle, 2)
        self._clear_axis_prefix_cache()

        if self.core.raw_data is not None:
            self.request_refresh(
                RefreshCause.ROTATION,
                RenderQuality.EXACT,
                immediate=True,
                snapshot={"rotation_angle": self.rotation_angle},
            )

    def on_rotation_angle_preview(self, _text=None):
        if self._syncing_controls:
            return
        try:
            angle = self.page_image.get_rotation_angle()
        except (TypeError, ValueError):
            return
        self.rotation_angle = round(float(angle), 2)
        self._clear_axis_prefix_cache()
        if self.core.raw_data is not None:
            self.request_refresh(
                RefreshCause.ROTATION,
                interactive=True,
                snapshot={"rotation_angle": self.rotation_angle},
            )

    def _apply_rotation_actor_preview(self, angle):
        session = getattr(self, "volume_session", None)
        if session is None or not session.active or self.left_display_stack.currentWidget() is not self.plotter:
            return
        try:
            delta = float(angle) - float(self._rendered_rotation_angle)
            session.volume.SetOrigin(100.0, 100.0, 100.0)
            session.volume.SetOrientation(0.0, 0.0, delta)
            self.plotter.render()
            session.render_count += 1
        except Exception:
            pass

    def _reset_volume_actor_transform(self):
        session = getattr(self, "volume_session", None)
        if session is None or not session.active:
            return
        try:
            session.volume.SetOrientation(0.0, 0.0, 0.0)
            session.volume.SetOrigin(100.0, 100.0, 100.0)
            session.volume.SetScale(1.0, 1.0, 1.0)
        except Exception:
            pass

    def on_apply_denoise(self):
        if self.original_raw_data is None:
            return

        current_spec = self.left_workspace.current_spec()
        if current_spec is not None and current_spec.page_kind != "control_panel":
            target_spec = current_spec
        else:
            target_page_id = self.last_visual_page_id or self.left_workspace.home_page_id
            target_spec = self.left_workspace.page_by_id(target_page_id)
        if target_spec is None:
            return

        try:
            methods = self.page_control_blank.build_method_specs(
                self.page_render.get_denoise_settings(),
                data_shape=self.original_raw_data.shape,
            )
        except ValueError as exc:
            self._show_message("Savitzky-Golay 参数无效", str(exc), QMessageBox.Warning)
            return

        self._persist_page_ui_state(self.active_page_spec)
        target_spec.params["denoise_methods"] = methods
        self.page_denoise_cache.pop(target_spec.page_id, None)
        self._rotation_cache.clear()
        self._computed_volume_cache.clear()
        self._second_derivative_volume_cache = None
        self._clear_axis_prefix_cache(target_spec.page_id)
        self.left_workspace.activate_page(target_spec.page_id)

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

    @staticmethod
    def _canonical_data_aliases(data):
        canonical = np.asarray(data, dtype=np.float32)
        canonical.setflags(write=False)
        return canonical, canonical, canonical

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
        canonical, original, base = self._canonical_data_aliases(self.core.raw_data)
        self.core.raw_data = canonical
        self.original_raw_data = original
        self.original_coords = self._clone_coords()
        self.base_raw_data = base
        self.base_coords = self._clone_coords(self.original_coords)
        self.page_denoise_cache.clear()
        self._rotation_cache.clear()
        self._computed_volume_cache.clear()
        self.backend_manager.clear_caches()
        if hasattr(self, "volume_session"):
            self.volume_session.clear(render=False)
        self._second_derivative_volume_cache = None
        self._clear_axis_prefix_cache()
        self.rotation_angle = 0.0
        self._rendered_rotation_angle = 0.0
        self.clip_ranges = None
        self.home_slice_info = None
        self.axis_source_mode = "frame"

        self._syncing_controls = True
        try:
            self.page_image.restore_state(self.initial_control_state.get("image"), block_signals=True)
            self.page_render.restore_state(self.initial_control_state.get("render"), block_signals=True)
            self.page_control_blank.restore_state(self.initial_control_state.get("denoise_detail"), block_signals=True)
            self.page_data.restore_state(self.initial_control_state.get("data_process"), block_signals=True)
            self._select_control_page(int(self.initial_control_state.get("active_control_tab", 0)))
        finally:
            self._syncing_controls = False

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
        home_spec = self.left_workspace.home_spec()
        if home_spec is not None:
            home_spec.params.clear()
            home_spec.params["control_state"] = self._capture_control_state()
            self._persist_home_page_state(home_spec)
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

        physical_min, physical_max, physical_step = self._axis_physical_range(axis_idx)

        self._syncing_axis_value_boxes = True
        try:
            for slider, value_box in self._axis_slider_box_pairs():
                slider.setRange(0, max_val)
                slider.setToolTipConvertionFunc(tooltip_func)
                value_box.setRange(physical_min, physical_max)
                value_box.setSingleStep(physical_step)
                value_box.setValue(self.core.logical_to_physical(axis_idx, int(slider.value())))
        finally:
            self._syncing_axis_value_boxes = False

    def _axis_slider_box_pairs(self):
        return (
            (self.page_data.s_ax_low, self.page_data.input_ax_low),
            (self.page_data.s_ax_up, self.page_data.input_ax_up),
            (self.page_data.s_ax_mid, self.page_data.input_ax_mid),
        )

    def _axis_physical_range(self, axis_idx):
        axis_key = AnalyzerCore.AXIS_INDEX_MAP.get(int(axis_idx), "X")
        coords = self.core.coords.get(axis_key)
        coords = np.asarray(coords, dtype=np.float64).flatten() if coords is not None else np.array([], dtype=np.float64)
        finite = coords[np.isfinite(coords)]
        if finite.size == 0:
            return 0.0, 0.0, 0.01

        physical_min = float(np.min(finite))
        physical_max = float(np.max(finite))
        if np.isclose(physical_min, physical_max):
            physical_max = physical_min + 0.01

        step = self._compute_axis_spacing(finite, 0.01)
        step = max(float(step), 0.01)
        return physical_min, physical_max, step

    def _axis_physical_to_logical_index(self, axis_idx, physical_value):
        if self.core.raw_data is None:
            return 0

        max_val = max(int(self.core.raw_data.shape[int(axis_idx)]) - 1, 0)
        logical_value = self.core.physical_to_logical(axis_idx, float(physical_value))
        return int(np.clip(round(logical_value), 0, max_val))

    def _axis_input_logical_value(self, value_box, axis_idx):
        return self._axis_physical_to_logical_index(axis_idx, float(value_box.value()))

    def _axis_input_logical_values(self):
        axis_idx = int(self.page_data.combo_ax.currentIndex())
        return (
            self._axis_input_logical_value(self.page_data.input_ax_low, axis_idx),
            self._axis_input_logical_value(self.page_data.input_ax_up, axis_idx),
            self._axis_input_logical_value(self.page_data.input_ax_mid, axis_idx),
        )

    def _set_axis_value_box_from_slider(self, value_box, logical_value):
        if self.core.raw_data is None:
            return

        axis_idx = int(self.page_data.combo_ax.currentIndex())
        physical_value = self.core.logical_to_physical(axis_idx, int(logical_value))
        if physical_value < value_box.minimum():
            value_box.setMinimum(float(physical_value))
        elif physical_value > value_box.maximum():
            value_box.setMaximum(float(physical_value))
        blocker = QSignalBlocker(value_box)
        try:
            value_box.setValue(float(physical_value))
        finally:
            del blocker

    def sync_axis_value_boxes_from_sliders(self):
        for slider, value_box in self._axis_slider_box_pairs():
            self._set_axis_value_box_from_slider(value_box, int(slider.value()))

    def on_axis_slider_value_changed(self, value_box, logical_value):
        if self.core.raw_data is None:
            return
        if self._syncing_controls or self._syncing_axis_value_boxes:
            return

        self._set_axis_value_box_from_slider(value_box, int(logical_value))

    def on_axis_physical_value_changed(self, value_box, slider):
        if self.core.raw_data is None:
            return
        if self._syncing_controls or self._syncing_axis_value_boxes:
            return

        axis_idx = int(self.page_data.combo_ax.currentIndex())
        logical_value = self._axis_physical_to_logical_index(axis_idx, float(value_box.value()))
        if int(slider.value()) == logical_value:
            self._set_axis_value_box_from_slider(value_box, logical_value)
            self.schedule_axis_refresh()
            return

        slider.setValue(logical_value)
        self._set_axis_value_box_from_slider(value_box, logical_value)


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
        title = f"时间积分_{self._integral_length(low, up)}"
        spec = AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=title,
            page_kind="time_integral",
            source_module="data_process",
            params={"t_low": low, "t_up": up},
        )
        self._seed_control_state_for_spec(spec)
        return spec

    @staticmethod
    def _update_saved_widget_state(container, name, **values):
        widget_state = dict(container.get(name) or {})
        widget_state.update(values)
        container[name] = widget_state

    def _seed_time_integrated_slice_control_state(self, spec):
        params = spec.params
        axis_index = int(params["axis_index"])
        slice_index = int(params["mid"])
        axis_max = max(int(self.core.raw_data.shape[axis_index]) - 1, 0)
        physical_min, physical_max, _ = self._axis_physical_range(axis_index)
        physical_value = float(self.core.logical_to_physical(axis_index, slice_index))

        control_state = spec.params.get("control_state") or self._capture_control_state()
        control_state["axis_source_mode"] = "time_integral"
        data_state = dict(control_state.get("data_process") or {})
        data_state["combo_ax"] = {
            "index": axis_index,
            "text": ["X轴", "Y轴", "Z轴"][axis_index],
        }
        for slider_name in ("s_ax_low", "s_ax_up", "s_ax_mid"):
            self._update_saved_widget_state(
                data_state,
                slider_name,
                minimum=0,
                maximum=axis_max,
                value=slice_index,
            )
        for input_name in ("input_ax_low", "input_ax_up", "input_ax_mid"):
            self._update_saved_widget_state(
                data_state,
                input_name,
                minimum=physical_min,
                maximum=physical_max,
                value=physical_value,
            )

        self._update_saved_widget_state(data_state, "s_t_low", value=int(params["source_t_low"]))
        self._update_saved_widget_state(data_state, "s_t_up", value=int(params["source_t_up"]))
        data_state["locked_half_width"] = 0
        control_state["data_process"] = data_state
        self._store_control_state(spec, control_state)

    def _seed_second_derivative_axis_control_state(self, spec):
        if (
            spec is None
            or spec.page_kind != "second_derivative"
            or spec.params.get("source_view") != "2d"
            or self.core.raw_data is None
        ):
            return

        params = spec.params
        if params.get("source_page_kind") == "home":
            axis_index = int(params.get("slice_axis", -1))
            if axis_index not in (0, 1):
                return
            low = up = mid = int(params["slice_index"])
            locked_half_width = 0
        elif params.get("source_page_kind") == "axis_integral":
            axis_index = int(params.get("axis_index", -1))
            if axis_index not in (0, 1):
                return
            low, up = sorted(
                (int(params["integral_low"]), int(params["integral_up"]))
            )
            mid = int(params.get("integral_mid", round((low + up) / 2)))
            mid = int(np.clip(mid, low, up))
            locked_half_width = max(min(mid - low, up - mid), 0)
        else:
            return

        axis_max = max(int(self.core.raw_data.shape[axis_index]) - 1, 0)
        low = int(np.clip(low, 0, axis_max))
        up = int(np.clip(up, 0, axis_max))
        mid = int(np.clip(mid, low, up))
        physical_min, physical_max, _ = self._axis_physical_range(axis_index)

        control_state = spec.params.get("control_state") or self._capture_control_state()
        data_state = dict(control_state.get("data_process") or {})
        data_state["combo_ax"] = {
            "index": axis_index,
            "text": ["X轴", "Y轴", "Z轴"][axis_index],
        }
        for name, value in (
            ("s_ax_low", low),
            ("s_ax_up", up),
            ("s_ax_mid", mid),
        ):
            self._update_saved_widget_state(
                data_state,
                name,
                minimum=0,
                maximum=axis_max,
                value=value,
            )
        for name, value in (
            ("input_ax_low", low),
            ("input_ax_up", up),
            ("input_ax_mid", mid),
        ):
            self._update_saved_widget_state(
                data_state,
                name,
                minimum=physical_min,
                maximum=physical_max,
                value=float(self.core.logical_to_physical(axis_index, value)),
            )
        data_state["locked_half_width"] = int(locked_half_width)
        control_state["data_process"] = data_state
        self._store_control_state(spec, control_state)

    def _build_time_integrated_slice_spec(self, source_spec):
        slice_info = dict(self.home_slice_info or {})
        axis_index = int(slice_info.get("axis", 0))
        axis_max = max(int(self.core.raw_data.shape[axis_index]) - 1, 0)
        slice_index = int(np.clip(int(slice_info.get("index", 0)), 0, axis_max))
        t_low, t_up = sorted(
            (int(self.page_data.s_t_low.value()), int(self.page_data.s_t_up.value()))
        )
        axis_name = ["X轴", "Y轴", "Z轴"][axis_index]
        title = f"时间积分_{axis_name}切片_{self._integral_length(t_low, t_up)}"
        spec = AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=title,
            page_kind="axis_integral",
            source_module="data_process",
            source_page_id=source_spec.page_id if source_spec is not None else None,
            params={
                "axis_index": axis_index,
                "axis_name": axis_name,
                "low": slice_index,
                "up": slice_index,
                "mid": slice_index,
                "source_mode": "time_integral",
                "source_page_kind": "time_integral",
                "source_t_index": int(self.page_image.slider_time.value()),
                "source_t_low": t_low,
                "source_t_up": t_up,
                "source_slice_axis": axis_index,
                "source_slice_index": slice_index,
            },
        )
        self._seed_control_state_for_spec(spec)
        self._seed_time_integrated_slice_control_state(spec)
        return spec

    def _build_axis_integral_spec(self, source_mode=None):
        params = self._build_axis_request_params(source_mode=source_mode)
        title = f"{params['axis_name']}积分_{self._integral_length(params['low'], params['up'])}"

        spec = AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=title,
            page_kind="axis_integral",
            source_module="data_process",
            params=params,
        )
        self._seed_control_state_for_spec(spec)
        return spec

    def _supports_axis_crop_spec(self, spec):
        if spec is None or spec.page_kind not in {"axis_integral", "axis_integral_crop"}:
            return False

        params = self._resolved_axis_integral_params(spec)
        if params is None:
            return False
        return int(params["axis_index"]) in (0, 1, 2)

    def _build_axis_integral_crop_spec(self, current_spec, crop_rect):
        if current_spec is None or current_spec.page_kind not in {"axis_integral", "axis_integral_crop"}:
            return None

        params = self._resolved_axis_integral_params(current_spec)
        if params is None or int(params["axis_index"]) not in (0, 1, 2):
            return None

        current_context = self.current_render_context if self._is_current_page(current_spec) else None
        plot_bounds = None if current_context is None else current_context.get("plot_logical_bounds")
        normalized_rect = self._intersect_plot_rects(self._normalize_plot_rect(crop_rect), plot_bounds)
        if normalized_rect is None:
            return None

        spec_params = {
            "axis_index": int(params["axis_index"]),
            "axis_name": params.get("axis_name"),
            "low": int(params["low"]),
            "up": int(params["up"]),
            "mid": int(params["mid"]),
            "source_mode": params.get("source_mode", "frame"),
            "source_t_index": int(params["source_t_index"]),
            "source_t_low": int(params["source_t_low"]),
            "source_t_up": int(params["source_t_up"]),
            "source_page_id": current_spec.page_id,
            "source_page_kind": "axis_integral",
            "crop_k_low": int(normalized_rect["x_low"]),
            "crop_k_up": int(normalized_rect["x_up"]),
            "crop_e_low": int(normalized_rect["y_low"]),
            "crop_e_up": int(normalized_rect["y_up"]),
        }

        spec = AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=self._axis_crop_title(spec_params, normalized_rect),
            page_kind="axis_integral_crop",
            source_module="data_process",
            source_page_id=current_spec.page_id,
            params=spec_params,
        )
        self._seed_control_state_for_spec(spec)
        return spec

    def _build_slice_dos_spec(self):
        clip_info = self._get_clip_slices(self.clip_ranges)
        if clip_info is None:
            return None

        _, index_bounds = clip_info
        x1, x2, y1, y2, z1, z2 = index_bounds
        spec = AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=f"切片内强度积分 [{x1}:{x2}, {y1}:{y2}, {z1}:{z2}]",
            page_kind="slice_dos",
            source_module="data_process",
            params={"clip_ranges": list(self.clip_ranges)},
        )
        self._seed_control_state_for_spec(spec)
        return spec

    def _build_energy_dos_spec(self):
        current_spec = self.left_workspace.current_spec()
        if current_spec is None or current_spec.page_kind == "control_panel":
            current_spec = self._current_visual_spec()
        if current_spec is None:
            return None

        t_index = int(self.page_image.slider_time.value())
        delay_text = self._current_delay_text(t_index)
        spec_params = {
            "t_index": t_index,
            "source_page_kind": "home",
        }
        title = f"Energy-DOS [T={t_index}/{delay_text}]"

        if current_spec.page_kind in {"axis_integral", "axis_integral_crop"}:
            params = self._resolved_axis_integral_params(current_spec)
            if params is None or int(params["axis_index"]) not in (0, 1):
                self._show_message(
                    "Cannot create Energy DOS",
                    "Energy DOS from axis-integral pages is only available when the 2D plot contains E.",
                    QMessageBox.Warning,
                )
                return None

            spec_params = {
                "source_page_id": current_spec.page_id,
                "source_page_kind": "axis_integral",
                "axis_index": int(params["axis_index"]),
                "integral_low": int(params["low"]),
                "integral_up": int(params["up"]),
                "integral_mid": int(params["mid"]),
                "source_mode": params.get("source_mode", "frame"),
                "source_t_index": int(params["source_t_index"]),
                "source_t_low": int(params["source_t_low"]),
                "source_t_up": int(params["source_t_up"]),
            }
            crop_rect = self._axis_crop_rect_from_params(current_spec.params)
            if crop_rect is not None:
                spec_params.update(
                    {
                        "crop_k_low": int(crop_rect["x_low"]),
                        "crop_k_up": int(crop_rect["x_up"]),
                        "crop_e_low": int(crop_rect["y_low"]),
                        "crop_e_up": int(crop_rect["y_up"]),
                    }
                )
            title = f"Energy-DOS [{self._second_derivative_plot_label(spec_params)}{self._waterfall_crop_suffix(spec_params)}]"
        elif current_spec.page_kind == "home" and self.clip_ranges is not None:
            spec_params["clip_ranges"] = list(self.clip_ranges)
            title = f"Energy-DOS [Clipped T={t_index}/{delay_text}]"

        spec = AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=title,
            page_kind="energy_dos",
            source_module="data_process",
            source_page_id=current_spec.page_id,
            params=spec_params,
        )
        self._seed_control_state_for_spec(spec)
        return spec

    def _show_edc_curve_source_error(self):
        self._show_message(
            "Cannot create EDC curve",
            "Please first crop a rectangular region from a Z-axis integral result page, then create the single EDC curve.",
            QMessageBox.Warning,
        )

    def _build_edc_curve_spec(self):
        current_spec = self.left_workspace.current_spec()
        if current_spec is None or current_spec.page_kind != "axis_integral_crop":
            self._show_edc_curve_source_error()
            return None

        params = self._resolved_axis_integral_params(current_spec)
        if params is None or int(params["axis_index"]) != 2:
            self._show_edc_curve_source_error()
            return None

        crop_rect = self._axis_crop_rect_from_params(current_spec.params)
        if crop_rect is None:
            self._show_edc_curve_source_error()
            return None

        spec_params = {
            "source_page_id": current_spec.page_id,
            "source_page_kind": "axis_integral_crop",
            "axis_index": 2,
            "integral_low": int(params["low"]),
            "integral_up": int(params["up"]),
            "integral_mid": int(params["mid"]),
            "source_mode": params.get("source_mode", "frame"),
            "source_t_index": int(params["source_t_index"]),
            "source_t_low": int(params["source_t_low"]),
            "source_t_up": int(params["source_t_up"]),
            "crop_k_low": int(crop_rect["x_low"]),
            "crop_k_up": int(crop_rect["x_up"]),
            "crop_e_low": int(crop_rect["y_low"]),
            "crop_e_up": int(crop_rect["y_up"]),
        }

        spec = AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=self._edc_curve_title(spec_params),
            page_kind="edc_curve",
            source_module="data_process",
            source_page_id=current_spec.page_id,
            params=spec_params,
        )
        raw_data, coords = self._get_display_state_for_spec(current_spec)
        if self._build_edc_curve_context_from_params(raw_data, coords, spec.params) is None:
            self._show_edc_curve_source_error()
            return None
        self._seed_control_state_for_spec(spec)
        return spec

    def _build_waterfall_edc_spec(self):
        current_spec = self.left_workspace.current_spec()
        if current_spec is None or current_spec.page_kind not in {"axis_integral", "axis_integral_crop"}:
            self._show_message(
                "Cannot create waterfall",
                "EDC waterfall is only available from X-Integral / Y-Integral result pages.",
                QMessageBox.Warning,
            )
            return None

        if self._is_current_page(current_spec) and current_spec.page_kind == "axis_integral":
            self._persist_axis_integral_page_state(current_spec)

        params = self._resolved_axis_integral_params(current_spec)
        if params is None:
            return None

        axis_index = int(params["axis_index"])
        axis_info = self._resolve_waterfall_axis_info(axis_index)
        if axis_info is None:
            self._show_message(
                "Cannot create waterfall",
                "EDC waterfall is only available from X-Integral / Y-Integral result pages.",
                QMessageBox.Warning,
            )
            return None

        k_step = self.page_control_blank.get_waterfall_step()
        if k_step <= 0:
            self._show_message("Cannot create waterfall", "k step must be greater than 0.", QMessageBox.Warning)
            return None

        low_idx, up_idx = sorted((int(params["low"]), int(params["up"])))
        mid_idx = int(np.clip(int(params["mid"]), low_idx, up_idx))
        center_physical = self.core.logical_to_physical(axis_info["integrated_axis_label"], mid_idx)
        title = self._waterfall_title_summary(
            axis_info["k_axis_label"],
            axis_info["integrated_axis_label"],
            center_physical,
            k_step,
        ) + self._waterfall_crop_suffix(current_spec.params)

        spec_params = {
            "source_page_id": current_spec.page_id,
            "source_page_kind": "axis_integral",
            "axis_index": int(axis_index),
            "integral_low": int(low_idx),
            "integral_up": int(up_idx),
            "integral_mid": int(mid_idx),
            "source_mode": params.get("source_mode", "frame"),
            "source_t_index": int(params["source_t_index"]),
            "source_t_low": int(params["source_t_low"]),
            "source_t_up": int(params["source_t_up"]),
            "k_axis_label": axis_info["k_axis_label"],
            "k_step": float(k_step),
        }
        crop_rect = self._axis_crop_rect_from_params(current_spec.params)
        if crop_rect is not None:
            spec_params.update(
                {
                    "crop_k_low": int(crop_rect["x_low"]),
                    "crop_k_up": int(crop_rect["x_up"]),
                    "crop_e_low": int(crop_rect["y_low"]),
                    "crop_e_up": int(crop_rect["y_up"]),
                }
            )

        spec = AnalysisPageSpec(
            page_id=self._make_page_id(),
            title=title,
            page_kind="waterfall_edc",
            source_module="data_process",
            source_page_id=current_spec.page_id,
            params=spec_params,
        )
        raw_data, coords = self._get_display_state_for_spec(current_spec)
        try:
            self._build_waterfall_context_from_axis_params(raw_data, coords, spec.params)
        except ValueError as exc:
            self._show_message("Cannot create waterfall", str(exc), QMessageBox.Warning)
            return None
        self._seed_control_state_for_spec(spec)
        return spec

    def _build_second_derivative_spec(self):
        current_spec = self.left_workspace.current_spec()
        if current_spec is None:
            return None

        if current_spec.page_kind == "home":
            source_t_index = int(self.page_image.slider_time.value())
            if self.home_slice_info is None:
                derivative_axis = self._ask_second_derivative_axis()
                if derivative_axis is None:
                    return None
                spec_params = {
                    "source_page_id": current_spec.page_id,
                    "source_page_kind": "home",
                    "source_view": "3d",
                    "derivative_axis": derivative_axis,
                    "source_mode": "frame",
                    "source_t_index": source_t_index,
                    "clip_ranges": self._copy_state(self.clip_ranges),
                }
            else:
                slice_axis = int(self.home_slice_info.get("axis", -1))
                if slice_axis not in (0, 1):
                    self._show_message("无法生成二阶导", "当前切面不包含能量轴，暂不支持沿能量轴做二阶导。", QMessageBox.Warning)
                    return None
                spec_params = {
                    "source_page_id": current_spec.page_id,
                    "source_page_kind": "home",
                    "source_view": "2d",
                    "slice_axis": slice_axis,
                    "slice_index": int(self.home_slice_info["index"]),
                    "derivative_axis": 2,
                    "source_mode": "frame",
                    "source_t_index": source_t_index,
                }

            source_label = self._second_derivative_source_label(spec_params)
            spec = AnalysisPageSpec(
                page_id=self._make_page_id(),
                title=f"二阶导 [{source_label}]",
                page_kind="second_derivative",
                source_module="data_process",
                source_page_id=current_spec.page_id,
                params=spec_params,
            )
        elif current_spec.page_kind == "time_integral":
            self._persist_time_integral_page_state(current_spec)
            derivative_axis = self._ask_second_derivative_axis()
            if derivative_axis is None:
                return None
            t_low, t_up = sorted(
                (int(current_spec.params["t_low"]), int(current_spec.params["t_up"]))
            )
            spec_params = {
                "source_page_id": current_spec.page_id,
                "source_page_kind": "time_integral",
                "source_view": "3d",
                "derivative_axis": derivative_axis,
                "source_mode": "time_integral",
                "source_t_low": t_low,
                "source_t_up": t_up,
            }
            source_label = self._second_derivative_source_label(spec_params)
            spec = AnalysisPageSpec(
                page_id=self._make_page_id(),
                title=f"二阶导 [{source_label}]",
                page_kind="second_derivative",
                source_module="data_process",
                source_page_id=current_spec.page_id,
                params=spec_params,
            )
        elif current_spec.page_kind in {"axis_integral", "axis_integral_crop"}:
            params = self._resolved_axis_integral_params(current_spec)
            if params is None:
                return None
            axis_index = int(params["axis_index"])

            if axis_index not in (0, 1):
                self._show_message("无法生成二阶导", "当前切面不包含能量轴，暂不支持沿能量轴做二阶导。", QMessageBox.Warning)
                return None

            source_label = self._second_derivative_source_label(
                {"source_page_kind": "axis_integral", "axis_index": axis_index}
            )
            spec = AnalysisPageSpec(
                page_id=self._make_page_id(),
                title=f"二阶导 [{source_label}]",
                page_kind="second_derivative",
                source_module="data_process",
                source_page_id=current_spec.page_id,
                params={
                    "source_page_id": current_spec.page_id,
                    "source_page_kind": "axis_integral",
                    "source_view": "2d",
                    "derivative_axis": 2,
                    "axis_index": int(axis_index),
                    "integral_low": int(params["low"]),
                    "integral_up": int(params["up"]),
                    "integral_mid": int(params["mid"]),
                    "source_mode": params.get("source_mode", "frame"),
                    "source_t_index": int(params["source_t_index"]),
                    "source_t_low": int(params["source_t_low"]),
                    "source_t_up": int(params["source_t_up"]),
                },
            )
            crop_rect = self._axis_crop_rect_from_params(current_spec.params)
            if crop_rect is not None:
                spec.params.update(
                    {
                        "crop_k_low": int(crop_rect["x_low"]),
                        "crop_k_up": int(crop_rect["x_up"]),
                        "crop_e_low": int(crop_rect["y_low"]),
                        "crop_e_up": int(crop_rect["y_up"]),
                    }
                )
        else:
            self._show_message(
                "无法生成二阶导",
                "仅支持主页 3D/2D、时间积分 3D 或 X/Y 轴积分页面。",
                QMessageBox.Warning,
            )
            return None

        sd_params = self.page_control_blank.get_second_derivative_params()
        spec.params["second_derivative_params"] = sd_params

        raw_data, coords = self._get_display_state_for_spec(current_spec)
        if spec.params.get("source_view") == "3d":
            derivative_axis = int(spec.params.get("derivative_axis", -1))
            coordinate_key = {0: "X", 1: "Y", 2: "E"}.get(derivative_axis)
            context = (
                {"view": "3d"}
                if raw_data is not None
                and coords is not None
                and coordinate_key is not None
                and coords.get(coordinate_key) is not None
                else None
            )
        else:
            context = self._build_second_derivative_context_from_params(raw_data, coords, spec.params)
        if context is None:
            self._show_message("无法生成二阶导", "当前页面或所选方向不符合二阶导计算条件。", QMessageBox.Warning)
            return None

        self._seed_control_state_for_spec(spec)
        self._seed_second_derivative_axis_control_state(spec)
        return spec

    def on_apply_time_integral(self):
        if self.core.raw_data is None or not self.core.has_time_axis:
            return

        current_spec = self.left_workspace.current_spec()
        if (
            current_spec is not None
            and current_spec.page_kind == "home"
            and self.home_slice_info is not None
        ):
            candidate_spec = self._build_time_integrated_slice_spec(current_spec)
        else:
            candidate_spec = self._build_time_integral_spec()
        if current_spec is not None and "denoise_methods" in current_spec.params:
            candidate_spec.params["denoise_methods"] = current_spec.params["denoise_methods"]
        candidate_spec.title = self._make_unique_page_title(candidate_spec.title)
        self.left_workspace.add_page(candidate_spec)

    def on_time_integral_controls_changed(self, _):
        if self.core.raw_data is None or self._syncing_controls:
            return

        current_spec = self.left_workspace.current_spec()
        if current_spec is None:
            return

        if current_spec.page_kind == "time_integral":
            self.axis_source_mode = "time_integral"
            self._persist_time_integral_page_state(current_spec)
            self.schedule_axis_refresh()
        elif self._is_time_integral_axis_page(current_spec):
            self.axis_source_mode = "time_integral"
            self._persist_axis_integral_page_state(current_spec)
            self._update_time_slider_state()
            self.schedule_axis_refresh()

    def on_image_time_changed(self, _):
        if self.core.raw_data is None or self._syncing_controls:
            return

        current_spec = self.left_workspace.current_spec()
        if current_spec is not None and current_spec.page_kind == "time_integral":
            return

        self.axis_source_mode = "frame"
        if current_spec is not None and current_spec.page_kind == "axis_integral":
            self._persist_axis_integral_page_state(current_spec)

    def sync_ax_sliders_to_box(self):
        if not self._can_show_interactive_box():
            return

        axis_idx = self.page_data.combo_ax.currentIndex()
        low, up, _ = self._axis_input_logical_values()
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
        current_spec = self.left_workspace.current_spec()
        if current_spec is not None and current_spec.page_kind == "second_derivative":
            self._persist_second_derivative_page_state(current_spec)
        else:
            self._persist_axis_integral_page_state(current_spec)
        self.request_refresh(RefreshCause.ANALYSIS, interactive=True)

    def flush_axis_refresh(self):
        if self.core.raw_data is None:
            return
        if self.axis_refresh_timer.isActive():
            self.axis_refresh_timer.stop()
        self.auto_refresh_integral(exact=True)

    def on_axis_bound_released(self):
        if self.core.raw_data is None:
            return
        if self.axis_refresh_timer.isActive():
            self.axis_refresh_timer.stop()
        self.auto_refresh_integral(exact=True)

    def on_apply_axis_integral(self):
        if self.core.raw_data is None:
            return

        candidate_spec = self._build_axis_integral_spec()
        current_spec = self.left_workspace.current_spec()
        if current_spec is not None and "denoise_methods" in current_spec.params:
            candidate_spec.params["denoise_methods"] = current_spec.params["denoise_methods"]
        candidate_spec.title = self._make_unique_page_title(candidate_spec.title)
        self.left_workspace.add_page(candidate_spec)

    def auto_refresh_integral(self, exact=False):
        if self.page_image.switch_coord.isChecked():
            self.sync_ax_sliders_to_box()

        current_spec = self.left_workspace.current_spec()
        if current_spec is not None and current_spec.page_kind == "time_integral":
            self._persist_time_integral_page_state(current_spec)
        elif current_spec is not None and current_spec.page_kind in {"axis_integral", "axis_integral_crop"}:
            self._persist_axis_integral_page_state(current_spec)
        elif (
            current_spec is not None
            and current_spec.page_kind == "second_derivative"
            and current_spec.params.get("source_view") == "2d"
        ):
            self._persist_second_derivative_page_state(current_spec)
        else:
            return
        self.request_refresh(
            RefreshCause.ANALYSIS,
            RenderQuality.EXACT if exact else RenderQuality.PREVIEW,
            immediate=True,
        )

    def on_apply_other_integral(self):
        if self.core.raw_data is None:
            return

        current_index = int(self.page_data.combo_other.currentIndex())
        if current_index == 0:
            if not self.core.has_time_axis:
                self._show_message("静态数据", "当前数据不包含时间轴，无法计算切片内强度积分。", QMessageBox.Information)
                return
            if self.clip_ranges is None:
                self._show_message("未进行切片设置", "请先在“图像控制”页设置切片范围。", QMessageBox.Warning)
                return
            spec = self._build_slice_dos_spec()
        elif current_index == 1:
            spec = self._build_energy_dos_spec()
        elif current_index == 2:
            spec = self._build_waterfall_edc_spec()
        elif current_index == 3:
            spec = self._build_edc_curve_spec()
        else:
            spec = self._build_second_derivative_spec()

        if spec is not None:
            current_spec = self.left_workspace.current_spec()
            if current_spec is not None and "denoise_methods" in current_spec.params:
                spec.params["denoise_methods"] = current_spec.params["denoise_methods"]
            spec.title = self._make_unique_page_title(spec.title)
            self.left_workspace.add_page(spec)

    def on_other_mode_selection_changed(self, _):
        if self._syncing_controls:
            return
        self._update_export_button_states()


    def _waterfall_step_reference_axis(self, spec):
        if spec is None:
            return None

        if spec.page_kind in {"axis_integral", "axis_integral_crop", "waterfall_edc"}:
            axis_index = int(spec.params.get("axis_index", -1))
            if axis_index == 0:
                return "Y"
            if axis_index == 1:
                return "X"
        return None

    def _clear_axis_crop_selector(self):
        if self.axis_crop_selector is None:
            return
        try:
            self.axis_crop_selector.set_active(False)
            self.axis_crop_selector.set_visible(False)
        except Exception:
            pass
        self.axis_crop_selector = None

    def _clear_axis_crop_overlay(self, redraw=False):
        if self.axis_crop_overlay is not None:
            try:
                self.axis_crop_overlay.remove()
            except Exception:
                pass
            self.axis_crop_overlay = None
        if redraw and self.left_display_stack.currentIndex() == 1:
            self.canvas_2d.draw_idle()

    def _clear_axis_crop_interaction(self, redraw=False):
        self._clear_axis_crop_selector()
        self._clear_axis_crop_overlay(redraw=redraw)

    def _on_axis_crop_canvas_click(self, event):
        if event is None or event.button != MouseButton.RIGHT:
            return
        if self.left_display_stack.currentIndex() != 1:
            return

        spec = self.left_workspace.current_spec()
        context = self.current_render_context
        if not self._supports_axis_crop_context(spec, context):
            return

        removed = self.axis_crop_candidates.pop(spec.page_id, None)
        if removed is None:
            return
        self._refresh_axis_crop_interaction(spec, context)

    def _supports_axis_crop_context(self, spec, context=None):
        if spec is None or spec.page_kind not in {"axis_integral", "axis_integral_crop"}:
            return False
        if context is None:
            context = self.current_render_context
        if context is None or context.get("view") != "2d":
            return False
        return int(context.get("slice_info", {}).get("axis", -1)) in (0, 1, 2)

    def _current_axis_crop_rect(self, spec, context):
        if spec is None or context is None:
            return None

        candidate = self.axis_crop_candidates.get(spec.page_id)
        if candidate is not None:
            return self._copy_plot_rect(candidate)

        crop_rect = context.get("crop_rect")
        if crop_rect is None:
            return None
        if self._rect_matches_plot_bounds(crop_rect, context.get("plot_logical_bounds")):
            return None
        return self._copy_plot_rect(crop_rect)

    def _plot_rect_extents(self, context, rect):
        plot_axes = context["plot_axes"]
        coords = context["coords"]
        x_values = np.asarray(coords[plot_axes["x_key"]], dtype=np.float64)
        y_values = np.asarray(coords[plot_axes["y_key"]], dtype=np.float64)
        x0 = float(x_values[int(rect["x_low"])])
        x1 = float(x_values[int(rect["x_up"])])
        y0 = float(y_values[int(rect["y_low"])])
        y1 = float(y_values[int(rect["y_up"])])
        return min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)

    def _draw_axis_crop_overlay(self, spec, context):
        self._clear_axis_crop_overlay(redraw=False)
        rect = self._current_axis_crop_rect(spec, context)
        if rect is None:
            return

        x0, x1, y0, y1 = self._plot_rect_extents(context, rect)
        self.axis_crop_overlay = Rectangle(
            (x0, y0),
            max(x1 - x0, 1e-12),
            max(y1 - y0, 1e-12),
            fill=False,
            edgecolor="#FF69B4",
            linewidth=1.4,
            linestyle="--",
            alpha=0.9,
        )
        self.ax_2d.add_patch(self.axis_crop_overlay)

    def _refresh_axis_crop_interaction(self, spec, context):
        self._clear_axis_crop_selector()
        self._clear_axis_crop_overlay(redraw=False)

        if not self.page_image.switch_coord.isChecked():
            if self.left_display_stack.currentIndex() == 1:
                self.canvas_2d.draw_idle()
            return

        if not self._supports_axis_crop_context(spec, context):
            return

        self._draw_axis_crop_overlay(spec, context)
        self.axis_crop_selector = RectangleSelector(
            self.ax_2d,
            self._on_axis_crop_selected,
            useblit=False,
            button=[1],
            minspanx=1e-12,
            minspany=1e-12,
            spancoords="data",
            interactive=False,
            props={
                "facecolor": "#FF69B4",
                "edgecolor": "#FF69B4",
                "alpha": 0.12,
                "fill": True,
            },
        )
        self.canvas_2d.draw_idle()

    def _on_axis_crop_selected(self, eclick, erelease):
        spec = self.left_workspace.current_spec()
        context = self.current_render_context
        if not self._supports_axis_crop_context(spec, context):
            return
        if eclick.xdata is None or eclick.ydata is None or erelease.xdata is None or erelease.ydata is None:
            return

        plot_axes = context["plot_axes"]
        plot_bounds = context["plot_logical_bounds"]
        x0 = self.core.physical_to_logical(plot_axes["x_key"], float(eclick.xdata))
        x1 = self.core.physical_to_logical(plot_axes["x_key"], float(erelease.xdata))
        y0 = self.core.physical_to_logical(plot_axes["y_key"], float(eclick.ydata))
        y1 = self.core.physical_to_logical(plot_axes["y_key"], float(erelease.ydata))
        rect = self._intersect_plot_rects(
            {
                "x_low": int(np.floor(min(x0, x1))),
                "x_up": int(np.ceil(max(x0, x1))),
                "y_low": int(np.floor(min(y0, y1))),
                "y_up": int(np.ceil(max(y0, y1))),
            },
            plot_bounds,
        )
        if rect is None:
            return

        self.axis_crop_candidates[spec.page_id] = rect
        self._refresh_axis_crop_interaction(spec, context)

    def _render_active_page(self, quality=RenderQuality.EXACT, cause=None):
        spec = self.left_workspace.current_spec() or self.left_workspace.home_spec()
        if spec is None:
            return

        self.plot_coordinate_tooltip.hide(redraw=False)
        self.current_render_context = None
        self._clear_axis_crop_interaction(redraw=False)

        if spec.page_kind == "control_panel":
            self.left_display_stack.setCurrentIndex(2)
            self._clear_interactive_box()
            self.plotter.render()
            return

        if self.core.raw_data is None:
            self.left_display_stack.setCurrentIndex(0)
            self.volume_session.clear(render=False)
            self._clear_interactive_box()
            self.plotter.render()
            VisualEngine.clear_2d_colorbar(self.ax_2d)
            self.ax_2d.clear()
            self.canvas_2d.draw()
            return

        try:
            context = self._compute_render_context(spec)
        except ValueError as exc:
            self._show_message("Render failed", str(exc), QMessageBox.Warning)
            return
        if context is None:
            return

        self.current_render_context = context
        render_context = self._render_context_for_visual_flip(context)
        levels = self._get_display_levels()
        mapping_mode = self.page_render.combo_map.currentText()
        current_cmap = self.page_render.get_selected_cmap()
        self._last_ui_transfer_signature = (tuple(levels), str(mapping_mode), str(current_cmap))

        if render_context["view"] == "3d":
            self.left_display_stack.setCurrentIndex(0)
            clip_ranges = render_context.get("clip_ranges")
            render_clip = None
            if clip_ranges is not None:
                render_clip = self.core.logical_to_render_bounds(clip_ranges, render_context["data"].shape)

            volume_data = render_context["data"]
            if (
                quality == RenderQuality.PREVIEW
                and cause not in {RefreshCause.TRANSFER_FUNCTION, RefreshCause.GEOMETRY, RefreshCause.OVERLAY}
                and int(np.prod(volume_data.shape, dtype=np.int64)) > 2_500_000
            ):
                stride = max(
                    1,
                    int(np.ceil((np.prod(volume_data.shape) / 2_500_000.0) ** (1.0 / 3.0))),
                )
                volume_data = np.asfortranarray(volume_data[::stride, ::stride, ::stride], dtype=np.float32)

            self._reset_volume_actor_transform()
            self.volume_session.render(
                volume_data,
                levels,
                opac_mode=mapping_mode,
                clip_ranges=render_clip,
                show_axes=self.page_image.switch_axes.isChecked(),
                core_coords=render_context.get("coords", self.core.coords),
                cmap=current_cmap,
                quality=quality.value,
            )
            self._rendered_rotation_angle = float(self.rotation_angle)
            if self._box_interacting:
                self._orig_volume_opacity = None
                self._dim_current_volume()
        elif render_context["view"] == "2d":
            self.left_display_stack.setCurrentIndex(1)
            self._clear_interactive_box()
            VisualEngine.render_2d_slice(
                self.ax_2d,
                self.canvas_2d,
                render_context["data"],
                render_context["slice_info"],
                levels,
                render_context.get("coords", self.core.coords),
                cmap=current_cmap,
            )
        else:
            self.left_display_stack.setCurrentIndex(1)
            self._clear_interactive_box()
            if render_context["view"] == "waterfall":
                self._render_waterfall_plot(render_context)
            elif render_context["view"] == "1d_comparison":
                self._render_1d_comparison_plot(render_context)
            else:
                self._render_1d_plot(render_context)

        if render_context["view"] == "3d":
            if self._can_show_interactive_box():
                self._rebuild_interactive_box()
            else:
                self._restore_volume_opacity_if_dimmed()
                self._clear_interactive_box()
        else:
            self._restore_volume_opacity_if_dimmed()
            self._clear_interactive_box()

        self._refresh_axis_crop_interaction(spec, context)

    def on_result_page_closed(self, page_id):
        self.refresh_coordinator.cancel_page(page_id)
        self.page_denoise_cache.pop(page_id, None)
        self._clear_axis_prefix_cache(page_id)
        self._rotation_cache.clear()
        self._computed_volume_cache.clear()
        self._second_derivative_volume_cache = None
        self.axis_crop_candidates.pop(page_id, None)
        if self.active_page_spec is not None and self.active_page_spec.page_id == page_id:
            self.current_render_context = None
            self._clear_axis_crop_interaction(redraw=False)

        if self.last_visual_page_id == page_id:
            fallback_spec = self.left_workspace.current_spec()
            if fallback_spec is None or fallback_spec.page_kind == "control_panel":
                fallback_spec = self.left_workspace.home_spec()
            if fallback_spec is not None and fallback_spec.page_kind != "control_panel":
                self.last_visual_page_id = fallback_spec.page_id
            else:
                self.last_visual_page_id = self.left_workspace.home_page_id

    def closeEvent(self, event):
        self.refresh_coordinator.shutdown(wait_ms=2000)
        self._computed_volume_cache.clear()
        self.backend_manager.clear_caches()
        if hasattr(self, "volume_session"):
            self.volume_session.clear(render=False)
        super().closeEvent(event)

    def on_toggle_interactive_box(self, checked):
        if checked and self._can_show_interactive_box():
            self._rebuild_interactive_box()
            self._sync_slice_edits_from_logical_bounds(self.clip_ranges)
        else:
            self._restore_volume_opacity_if_dimmed()
            self._clear_interactive_box()

        if checked:
            self._refresh_axis_crop_interaction(self.left_workspace.current_spec(), self.current_render_context)
        else:
            self._clear_axis_crop_interaction(redraw=self.left_display_stack.currentIndex() == 1)
        self.plotter.render()

    def on_cut(self):
        current_spec = self.left_workspace.current_spec()
        if current_spec is not None and current_spec.page_kind in {"axis_integral", "axis_integral_crop"}:
            if not self._supports_axis_crop_spec(current_spec):
                self._show_message(
                    "2D crop unavailable",
                    "Rectangular 2D crop is only supported for axis-integral result pages.",
                    QMessageBox.Warning,
                )
                return

            crop_rect = self.axis_crop_candidates.get(current_spec.page_id)
            if crop_rect is None:
                self._show_message(
                    "No crop selected",
                    "Enable crop interaction and drag a rectangle on the 2D axis-integral plot first.",
                    QMessageBox.Information,
                )
                return

            spec = self._build_axis_integral_crop_spec(current_spec, crop_rect)
            if spec is None:
                self._show_message("Crop failed", "Unable to build a cropped axis-integral page from the current selection.", QMessageBox.Warning)
                return

            if "denoise_methods" in current_spec.params:
                spec.params["denoise_methods"] = current_spec.params["denoise_methods"]
            spec.title = self._make_unique_page_title(spec.title)
            self.left_workspace.add_page(spec)
            return

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

    def export_current_result(self):
        spec = self.left_workspace.current_spec()
        if spec is None or self.core.raw_data is None:
            return
        if spec.page_kind == "control_panel":
            return
        if spec.page_kind == self.COMPARISON_PAGE_KIND:
            self._show_message("无法导出数据", "比较页暂不导出叠加曲线数据，请使用左侧视图保存导出图片。", QMessageBox.Information)
            return

        raw_data, coords = self._get_display_state_for_spec(spec)
        if raw_data is None or coords is None:
            return

        if spec.page_kind == "home":
            clip_info = self._get_clip_slices()
            if clip_info is None:
                self._show_message("No slice configured", "Please configure a slice or cut range first.", QMessageBox.Warning)
                return

            slices, _ = clip_info
            sample = raw_data[slices[0], slices[1], slices[2], :]
            if not self.core.has_time_axis:
                sample = sample[..., 0]

            export_data = {
                "sample": np.asarray(sample, dtype=np.float32),
                "kx": np.asarray(coords["X"][slices[0]], dtype=np.float32),
                "ky": np.asarray(coords["Y"][slices[1]], dtype=np.float32),
                "E": np.asarray(coords["E"][slices[2]], dtype=np.float32),
                "time": np.asarray(coords["delay"], dtype=np.float32),
            }
            title = "Save current slice cube"
            default_name = "slice_cube.mat"
        elif spec.page_kind == "time_integral":
            self._persist_time_integral_page_state(spec)
            context = self._get_time_integral_context(spec, raw_data, coords)
            t_low = int(spec.params["t_low"])
            t_up = int(spec.params["t_up"])
            export_data = {
                "sample": np.asarray(context["data"], dtype=np.float32),
                "kx": np.asarray(coords["X"], dtype=np.float32),
                "ky": np.asarray(coords["Y"], dtype=np.float32),
                "E": np.asarray(coords["E"], dtype=np.float32),
            }
            title = "Save time-integral result"
            default_name = self._build_time_integral_default_name(t_low, t_up)
        elif spec.page_kind == "axis_integral":
            axis_context = self._get_axis_integral_export_context(spec, raw_data, coords)
            if axis_context is None:
                return
            export_data = axis_context["export_data"]
            title = "Save axis-integral result"
            default_name = axis_context["default_name"]
        elif spec.page_kind == "axis_integral_crop":
            axis_context = self._get_axis_integral_crop_export_context(spec, raw_data, coords)
            if axis_context is None:
                return
            export_data = axis_context["export_data"]
            title = "Save cropped axis-integral result"
            default_name = axis_context["default_name"]
        elif spec.page_kind == "slice_dos":
            export_data = {
                "time": np.asarray(coords["delay"], dtype=np.float32),
                "intensity": np.asarray(self._compute_slice_dos(raw_data, spec.params["clip_ranges"]), dtype=np.float32),
            }
            title = "Save slice-integrated intensity"
            default_name = "slice_dos.mat"
        elif spec.page_kind == "edc_curve":
            context = self._get_edc_curve_context(spec, raw_data, coords)
            if context is None:
                return
            export_data = {
                "E": np.asarray(context["x_data"], dtype=np.float32),
                "intensity": np.asarray(context["y_data"], dtype=np.float32),
                "kx_range": np.asarray(context["kx_range"], dtype=np.float32),
                "ky_range": np.asarray(context["ky_range"], dtype=np.float32),
                "source_mode": np.asarray([context["source_mode"]]),
                "crop_rect": np.asarray(
                    [
                        int(context["crop_rect"]["x_low"]),
                        int(context["crop_rect"]["x_up"]),
                        int(context["crop_rect"]["y_low"]),
                        int(context["crop_rect"]["y_up"]),
                    ],
                    dtype=np.int32,
                ),
            }
            if context["source_mode"] == "time_integral":
                export_data["source_t_range"] = np.asarray(context["source_t_range"], dtype=np.float32)
                export_data["source_t_indices"] = np.asarray(context["source_t_indices"], dtype=np.int32)
            else:
                export_data["source_t_index"] = np.asarray([context["source_t_index"]], dtype=np.int32)
                export_data["source_t_value"] = np.asarray([context["source_t_value"]], dtype=np.float32)
            title = "Save EDC curve result"
            default_name = "edc_curve.mat"
        elif spec.page_kind == "waterfall_edc":
            try:
                context = self._get_waterfall_edc_context(spec, raw_data, coords)
            except ValueError as exc:
                self._show_message("Save failed", str(exc), QMessageBox.Warning)
                return
            if context is None:
                return
            export_data = {
                "k": np.asarray(context["k_values"], dtype=np.float32),
                "E": np.asarray(context["energy_axis"], dtype=np.float32),
                "intensity": np.asarray(context["raw_curves"], dtype=np.float32),
                "integrated_axis": np.asarray([context["integrated_axis_label"]]),
                "integrated_range": np.asarray(context["integrated_range"], dtype=np.float32),
            }
            if context.get("crop_rect") is not None:
                export_data["crop_rect"] = np.asarray(
                    [
                        int(context["crop_rect"]["x_low"]),
                        int(context["crop_rect"]["x_up"]),
                        int(context["crop_rect"]["y_low"]),
                        int(context["crop_rect"]["y_up"]),
                    ],
                    dtype=np.int32,
                )
            title = "Save EDC waterfall result"
            default_name = "waterfall_edc.mat"
        elif spec.page_kind == "second_derivative":
            context = self._get_second_derivative_context(spec, raw_data, coords)
            if context is None:
                return
            if context.get("view") == "3d":
                derivative_axis = int(context["derivative_axis"])
                derivative_label = self._second_derivative_axis_label(derivative_axis)
                export_data = {
                    "sample": np.asarray(context["data"], dtype=np.float32),
                    "kx": np.asarray(coords["X"], dtype=np.float32),
                    "ky": np.asarray(coords["Y"], dtype=np.float32),
                    "E": np.asarray(coords["E"], dtype=np.float32),
                    "derivative_axis": np.asarray([derivative_label]),
                    "source_mode": np.asarray([context["source_mode"]]),
                }
                if context["source_mode"] == "time_integral":
                    t_low, t_up = context["source_t_indices"]
                    export_data["source_t_indices"] = np.asarray(
                        [t_low, t_up],
                        dtype=np.int32,
                    )
                    export_data["source_t_range"] = np.asarray(
                        [coords["delay"][t_low], coords["delay"][t_up]],
                        dtype=np.float32,
                    )
                else:
                    source_t_index = int(context["source_t_index"])
                    export_data["source_t_index"] = np.asarray(
                        [source_t_index],
                        dtype=np.int32,
                    )
                    if coords.get("delay") is not None and len(coords["delay"]) > source_t_index:
                        export_data["time"] = np.asarray(
                            [coords["delay"][source_t_index]],
                            dtype=np.float32,
                        )
                title = "Save 3D second-derivative result"
                default_name = f"second_derivative_3d_{derivative_label}.mat"
            else:
                axis_index = int(context["slice_info"].get("axis", -1))
                plot_axes = context.get("plot_axes")
                plot_bounds = context.get("plot_logical_bounds")
                energy_axis = np.asarray(coords["E"], dtype=np.float32)
                export_data = {
                    "sample": np.asarray(context["data"], dtype=np.float32),
                    "E": energy_axis,
                }
                if plot_axes is not None and plot_bounds is not None:
                    y_low = int(plot_bounds["y_low"])
                    y_up = int(plot_bounds["y_up"])
                    export_data["E"] = np.asarray(coords[plot_axes["y_key"]][y_low:y_up + 1], dtype=np.float32)
                    if axis_index == 0:
                        export_data["ky"] = np.asarray(
                            coords[plot_axes["x_key"]][int(plot_bounds["x_low"]):int(plot_bounds["x_up"]) + 1],
                            dtype=np.float32,
                        )
                    elif axis_index == 1:
                        export_data["kx"] = np.asarray(
                            coords[plot_axes["x_key"]][int(plot_bounds["x_low"]):int(plot_bounds["x_up"]) + 1],
                            dtype=np.float32,
                        )
                elif axis_index == 0:
                    export_data["ky"] = np.asarray(coords["Y"], dtype=np.float32)
                elif axis_index == 1:
                    export_data["kx"] = np.asarray(coords["X"], dtype=np.float32)
                title = "Save second-derivative result"
                default_name = "second_derivative.mat"
        else:
            context = self._get_energy_dos_context(spec, raw_data, coords)
            if context is None:
                return
            export_data = {
                "E": np.asarray(context["x_data"], dtype=np.float32),
                "intensity": np.asarray(context["y_data"], dtype=np.float32),
            }
            source_kind = spec.params.get("source_page_kind", "home")
            if source_kind == "axis_integral":
                if spec.params.get("source_mode") == "time_integral":
                    export_data["time_range"] = np.asarray(
                        [
                            coords["delay"][int(spec.params["source_t_low"])],
                            coords["delay"][int(spec.params["source_t_up"])],
                        ],
                        dtype=np.float32,
                    )
                else:
                    export_data["time"] = np.asarray(
                        [coords["delay"][int(spec.params.get("source_t_index", int(self.page_image.slider_time.value())))]] ,
                        dtype=np.float32,
                    )
            else:
                if self._is_current_page(spec):
                    t_index = int(self.page_image.slider_time.value())
                else:
                    t_index = int(spec.params.get("t_index", int(self.page_image.slider_time.value())))
                export_data["time"] = np.asarray([coords["delay"][t_index]], dtype=np.float32)
            if "clip_ranges" in spec.params:
                export_data["clip_ranges"] = np.asarray(spec.params["clip_ranges"], dtype=np.float32)
            title = "Save Energy-DOS result"
            default_name = "energy_dos.mat"

        path = self._choose_export_path(title, default_name)
        if not path:
            return

        self._save_dict_to_path(path, export_data)
