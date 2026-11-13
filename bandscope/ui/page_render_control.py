from PyQt5.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt5.QtWidgets import QDoubleSpinBox, QHBoxLayout, QVBoxLayout, QWidget
from siui.components.combobox_ import SiCapsuleComboBox
from siui.components.widgets import SiLabel

import bandscope.ui.theme as theme
from bandscope.ui.camera_view_controls import CameraViewPanel
from bandscope.ui.enhancement_controls import RegionComboBox
from bandscope.ui.control_layout_utils import (
    animate_widget_visibility,
    centered_widget_row,
    combo_index_for_text,
    set_widget_visibility_instant,
)
from bandscope.ui.control_page_base import ControlPageBase
from bandscope.processing.denoise_config import DENOISE_METHODS as CONFIG_DENOISE_METHODS
from bandscope.core.display_style import (
    INDEPENDENT_MODE,
    LEGACY_MODE,
    NORMALIZATION_FULL,
    NORMALIZATION_MANUAL,
    NORMALIZATION_PERCENTILE,
    OPACITY_PRESET_CUSTOM,
    legacy_display_style,
    legacy_render_quality,
    new_display_style,
    new_render_quality,
    resolved_display_style,
    resolved_render_quality,
)


class RenderControlPage(ControlPageBase):
    CMAP_OPTIONS = [
        "magma", "inferno", "plasma", "viridis", "cividis", "turbo",
        "afmhot", "hot", "gist_heat", "coolwarm", "RdBu_r", "seismic",
        "Spectral", "jet", "rainbow", "nipy_spectral", "cubehelix",
        "twilight", "twilight_shifted", "Greys", "gray", "bone", "pink",
        "spring", "summer", "autumn", "winter", "cool", "hsv", "terrain",
        "ocean", "gnuplot", "gnuplot2",
    ]
    DENOISE_METHODS = CONFIG_DENOISE_METHODS

    #: CardGroup 的左右内边距（control_page_base.CardGroup 构造参数）。
    CARD_HORIZONTAL_PADDING = 14

    #: 「相机视角」模块的显隐目标状态：构造时可见，主窗口在启动阶段直接收起。
    _camera_view_visible_target = True

    #: 输入框提交新姿态（姿态, 本次改动的字段名）/ 请求重置相机：由主窗口连接渲染状态与相机实例。
    camera_pose_committed = pyqtSignal(object, str)
    camera_view_reset_requested = pyqtSignal()
    display_mode_changed = pyqtSignal(str)
    display_value_changed = pyqtSignal()

    def build_body(self):
        # 相机视角：只在有效 3D 场景出现，由主窗口按当前视图平滑收放。
        grp_camera, v_camera = self._create_group("相机视角")
        self.camera_panel = CameraViewPanel(grp_camera)
        self.camera_panel.pose_committed.connect(self.camera_pose_committed)
        self.camera_panel.reset_requested.connect(self.camera_view_reset_requested)
        # 复用页面的按钮宽度规则（自适应布局会统一覆盖宽度）。
        self._adaptive_buttons.append(self.camera_panel.reset_button)
        v_camera.addWidget(self.camera_panel, 0, Qt.AlignHCenter)
        self.grp_camera = grp_camera
        self.vbox.addLayout(centered_widget_row(grp_camera, self.MIN_GROUP_WIDTH))

        # 色带选择
        grp_cmap, v_cmap = self._create_group("色带选择")
        h_cmap = QHBoxLayout()
        self.combo_cmap = SiCapsuleComboBox(self)
        self.combo_cmap.setTitle("渲染色带")
        self.combo_cmap.setFixedHeight(30)
        self.combo_cmap.setFixedWidth(self.MIN_COMBO_WIDTH)
        self._adaptive_combo_controls.append(self.combo_cmap)
        self.combo_cmap.setEditable(False)
        self.combo_cmap.addItems(self.CMAP_OPTIONS)
        theme.raise_well_on_card(self.combo_cmap)
        self.btn_apply_cmap = self._create_btn("确定", "primary")

        h_cmap.addStretch()
        h_cmap.addWidget(self.combo_cmap)
        h_cmap.addWidget(self.btn_apply_cmap)
        h_cmap.addStretch()
        v_cmap.addLayout(h_cmap)

        self.vbox.addLayout(centered_widget_row(grp_cmap, self.MIN_GROUP_WIDTH))

        # 色阶调整
        grp_exp, v_exp = self._create_group("色阶调整")

        # 下拉框选择色阶滑条与强度映射的调整对象；「全部显示」展示所有区域，
        # 并在调节时同时调整背景与全部区域。锁定色带始终统一生效，
        # 各对象仍按自己的强度范围归一化。下拉框内可直接编辑文字重命名区域。
        self.region_combo = RegionComboBox(self)
        self.region_combo.sync([], None)
        self.btn_lock = self._create_btn("锁定色带", "secondary", height=30)
        self.btn_lock.setCheckable(True)
        self.btn_lock.setToolTip(
            "锁定全部色带：开启后，背景与所有增强区域各自按\n"
            "当前强度范围锁定归一化，拖动时间轴或生成新结果页时\n"
            "都按锁定时刻的范围分配颜色，便于逐帧对比；\n"
            "再次点击全部恢复按每帧数据自动分配。"
        )
        target_row = QWidget(self)
        target_row_layout = QHBoxLayout(target_row)
        target_row_layout.setContentsMargins(0, 0, 0, 0)
        target_row_layout.setSpacing(8)
        target_row_layout.addWidget(self.region_combo, 1)
        target_row_layout.addWidget(self.btn_lock)
        self._adaptive_row_controls.append(target_row)
        v_exp.addLayout(centered_widget_row(target_row, self.MIN_CONTROL_ROW_WIDTH))

        self.combo_display_mode = self._create_denoise_combo("显示模式", [])
        self.combo_display_mode.addItem("独立透明度", INDEPENDENT_MODE)
        self.combo_display_mode.addItem("兼容映射", LEGACY_MODE)
        self.combo_display_mode.setToolTip("兼容映射保留旧色阶与四条透明度曲线；独立透明度使用新的归一化和密度控制。")
        self.combo_display_mode.currentIndexChanged.connect(self._on_display_mode_combo)
        v_exp.addLayout(centered_widget_row(self.combo_display_mode, self.MIN_CONTROL_ROW_WIDTH))

        self.s_up = self._create_accent_slider()
        self.s_up.setRange(0, 100)
        self.s_up.setValue(100)
        self.s_up.setToolTip("白场：颜色强度窗口上限，映射到色带终点；不改变独立透明度。")

        self.s_gamma = self._create_accent_slider()
        self.s_gamma.setRange(0, 100)
        self.s_gamma.setValue(50)
        self.s_gamma.setToolTip("颜色 Gamma：50 为线性；独立模式下只改变颜色，不改变透明度。")

        self.s_low = self._create_accent_slider()
        self.s_low.setRange(0, 100)
        self.s_low.setValue(0)
        self.s_low.setToolTip("黑场：颜色强度窗口下限，映射到色带起点；不改变独立透明度。")

        self._add_centered_slider_block(v_exp, "白场", self.s_up)
        self._add_centered_slider_block(v_exp, "灰场（颜色 Gamma）", self.s_gamma)
        self._add_centered_slider_block(v_exp, "黑场", self.s_low)

        self.combo_map = self._create_denoise_combo("强度映射方式", ["线性", "对数", "幂函数", "sigmoid"])
        self.btn_apply_map = self._create_btn("应用设置", "primary")

        v_exp.addLayout(centered_widget_row(self.combo_map, self.MIN_CONTROL_ROW_WIDTH))
        v_exp.addLayout(centered_widget_row(self.btn_apply_map, self.BUTTON_WIDTH))

        self.vbox.addLayout(centered_widget_row(grp_exp, self.MIN_GROUP_WIDTH))

        self.grp_normalize, v_normalize = self._create_group("强度归一化")
        self.combo_normalization = self._create_denoise_combo("归一化策略", [])
        self.combo_normalization.addItem("完整范围", NORMALIZATION_FULL)
        self.combo_normalization.addItem("百分位范围（P0–P99.5）", NORMALIZATION_PERCENTILE)
        self.combo_normalization.addItem("手动范围", NORMALIZATION_MANUAL)
        self.combo_normalization.currentIndexChanged.connect(self._on_normalization_changed)
        v_normalize.addLayout(centered_widget_row(self.combo_normalization, self.MIN_CONTROL_ROW_WIDTH))
        self.manual_low = self._create_axis_value_box()
        self.manual_high = self._create_axis_value_box()
        for spin in (self.manual_low, self.manual_high):
            spin.setRange(-1.0e12, 1.0e12)
            spin.setDecimals(6)
            spin.valueChanged.connect(self._queue_display_value_change)
        manual_row = QWidget(self)
        manual_layout = QHBoxLayout(manual_row)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.addWidget(self.manual_low)
        manual_layout.addWidget(self.manual_high)
        self._adaptive_row_controls.append(manual_row)
        v_normalize.addLayout(centered_widget_row(manual_row, self.MIN_CONTROL_ROW_WIDTH))
        self.range_label = SiLabel("实际强度范围：待统计")
        self.range_label.setStyleSheet(theme.field_label_qss())
        self.range_label.setWordWrap(True)
        v_normalize.addWidget(self.range_label)
        self.vbox.addLayout(centered_widget_row(self.grp_normalize, self.MIN_GROUP_WIDTH))

        self.grp_opacity, v_opacity = self._create_group("独立透明度")
        self.display_parameters = {}
        self.display_parameter_labels = {}
        for name, label, minimum, maximum, decimals, scale, value, tooltip in (
            ("threshold", "阈值（%）", 0.0, 100.0, 3, 1000, 0.0, "阈值以下体素完全透明，用于去雾"),
            ("transition", "起始过渡宽度（%）", 0.0, 100.0, 1, 10, 0.0, "阈值边界开始的平滑过渡宽度"),
            ("gamma", "透明度 Gamma", 0.1, 4.0, 2, 100, 1.4, "控制不透明度随强度变化曲线形状"),
            ("density", "整体密度", 0.0, 100.0, 1, 10, 12.0, ""),
        ):
            slider, spin, title_label = self._create_display_parameter(
                v_opacity, label, minimum, maximum, decimals, scale, value, tooltip,
            )
            self.display_parameters[name] = (slider, spin, scale)
            self.display_parameter_labels[label] = title_label
        self.vbox.addLayout(centered_widget_row(self.grp_opacity, self.MIN_GROUP_WIDTH))

        self.grp_quality, v_quality = self._create_group("渲染质量")
        self.combo_quality_mode = self._create_denoise_combo("质量模式", [])
        self.combo_quality_mode.addItem("明确采样", "explicit")
        self.combo_quality_mode.addItem("兼容质量", LEGACY_MODE)
        self.combo_interpolation = self._create_denoise_combo("体插值", [])
        self.combo_interpolation.addItem("线性", "linear")
        self.combo_interpolation.addItem("最近邻", "nearest")
        self.combo_resting_quality = self._create_denoise_combo("静止质量", [])
        self.combo_resting_quality.addItem("标准（0.5 × 最小间距）", "standard")
        self.combo_resting_quality.addItem("精细（0.25 × 最小间距）", "fine")
        for combo in (self.combo_quality_mode, self.combo_interpolation, self.combo_resting_quality):
            combo.currentIndexChanged.connect(self._queue_display_value_change)
            v_quality.addLayout(centered_widget_row(combo, self.MIN_CONTROL_ROW_WIDTH))
        self.vbox.addLayout(centered_widget_row(self.grp_quality, self.MIN_GROUP_WIDTH))
        self._resolved_range = None
        self._display_view = "3d"
        self.set_display_style(new_display_style(), new_render_quality(), view="3d")

        # 去噪处理
        grp_noise, v_noise = self._create_group("去噪处理")
        lbl_noise = SiLabel("自上向下依次生效：")
        lbl_noise.setStyleSheet(theme.field_label_qss())
        v_noise.addWidget(lbl_noise)

        self.combo_n1 = self._create_denoise_combo("一级去噪", self.DENOISE_METHODS)
        self.combo_n2 = self._create_denoise_combo("二级去噪", self.DENOISE_METHODS)
        self.combo_n3 = self._create_denoise_combo("三级去噪", self.DENOISE_METHODS)

        self.btn_apply_noise = self._create_btn("应用设置", "primary")

        v_noise.addLayout(centered_widget_row(self.combo_n1, self.MIN_CONTROL_ROW_WIDTH))
        v_noise.addLayout(centered_widget_row(self.combo_n2, self.MIN_CONTROL_ROW_WIDTH))
        v_noise.addLayout(centered_widget_row(self.combo_n3, self.MIN_CONTROL_ROW_WIDTH))
        v_noise.addLayout(centered_widget_row(self.btn_apply_noise, self.BUTTON_WIDTH))

        self.vbox.addLayout(centered_widget_row(grp_noise, self.MIN_GROUP_WIDTH))

        # 扩展卡片挂载点：位置固定在这里，卡片的创建与显隐由宿主按已加载的插件
        # 决定。基础安装包没有插件时这里始终为空，页面外观与之前一致。
        self._extension_cards = {}
        # 卡片的显隐目标状态；构造时可见，与 set_extension_card_visible 的初值一致。
        self._extension_card_target = {}
        self._extension_slot = QVBoxLayout()
        self._extension_slot.setContentsMargins(0, 0, 0, 0)
        self._extension_slot.setSpacing(self.SECTION_SPACING)
        self.vbox.addLayout(self._extension_slot)

        # 全局计算后端（不随分析结果页保存）
        grp_backend, v_backend = self._create_group("性能与硬件")
        self.combo_backend = self._create_denoise_combo("计算后端", ["Auto", "CPU", "NVIDIA GPU"])
        v_backend.addLayout(centered_widget_row(self.combo_backend, self.MIN_CONTROL_ROW_WIDTH))

        self.vbox.addLayout(centered_widget_row(grp_backend, self.MIN_GROUP_WIDTH))

        # 启动时还没有有效 3D 场景：先直接收起，避免首帧闪现再收起。
        self.set_camera_view_visible(False, animate=False)

    def _create_display_parameter(self, layout, label, minimum, maximum, decimals, scale, value, tooltip=""):
        slider = self._create_accent_slider(axis=True)
        slider.setRange(int(round(minimum * scale)), int(round(maximum * scale)))
        slider.setValue(int(round(value * scale)))
        spin = self._create_axis_value_box()
        spin.setRange(float(minimum), float(maximum))
        spin.setDecimals(int(decimals))
        spin.setSingleStep(1.0 / float(scale))
        spin.setValue(float(value))
        container = self._create_labeled_slider_block(label, slider, spin)
        layout.addLayout(centered_widget_row(container))
        title_label = next((child for child in container.findChildren(SiLabel) if child.text() == label), None)
        if title_label is not None:
            title_label.setHint(tooltip)

        def from_slider(raw):
            blocker = QSignalBlocker(spin)
            spin.setValue(raw / float(scale))
            del blocker
            self.display_value_changed.emit()

        def from_spin(raw):
            blocker = QSignalBlocker(slider)
            slider.setValue(int(round(raw * scale)))
            del blocker
            self.display_value_changed.emit()

        slider.valueChanged.connect(from_slider)
        spin.valueChanged.connect(from_spin)
        return slider, spin, title_label

    def _on_display_mode_combo(self, _index):
        self.display_mode_changed.emit(str(self.combo_display_mode.currentData()))

    def _on_normalization_changed(self, _index):
        if self.combo_normalization.currentData() == NORMALIZATION_MANUAL and self._resolved_range is not None:
            blockers = [QSignalBlocker(widget) for widget in (self.manual_low, self.manual_high)]
            self.manual_low.setValue(float(self._resolved_range[0]))
            self.manual_high.setValue(float(self._resolved_range[1]))
            del blockers
        self._queue_display_value_change()

    def _queue_display_value_change(self, *_args):
        self._sync_display_visibility()
        self.display_value_changed.emit()

    @staticmethod
    def _set_combo_data(combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _display_style_widgets(self):
        widgets = [
            self.combo_display_mode, self.s_low, self.s_gamma, self.s_up, self.combo_map,
            self.combo_normalization, self.manual_low, self.manual_high,
            self.combo_quality_mode, self.combo_interpolation, self.combo_resting_quality,
        ]
        for slider, spin, _scale in self.display_parameters.values():
            widgets.extend((slider, spin))
        return widgets

    def set_display_style(self, style, quality=None, *, resolved_range=None, view="3d"):
        independent = isinstance(style, dict) and style.get("display_mode") == INDEPENDENT_MODE
        canonical = resolved_display_style(style)
        extended = canonical if independent else new_display_style(
            canonical["levels"], canonical["opacity_mode"],
        )
        normalization = extended["normalization"]
        opacity = extended["opacity"]
        quality = resolved_render_quality(quality)
        blockers = [QSignalBlocker(widget) for widget in self._display_style_widgets()]
        try:
            self._set_combo_data(self.combo_display_mode, INDEPENDENT_MODE if independent else LEGACY_MODE)
            for slider, value in zip((self.s_low, self.s_gamma, self.s_up), canonical["levels"]):
                slider.setValue(int(round(float(value))))
            index = combo_index_for_text(self.combo_map, canonical["opacity_mode"])
            if index < 0:
                self.combo_map.addItem(str(canonical["opacity_mode"]))
                index = self.combo_map.count() - 1
            self.combo_map.setCurrentIndex(index)
            self._set_combo_data(self.combo_normalization, normalization["mode"])
            self.manual_low.setValue(float(normalization["manual_range"][0]))
            self.manual_high.setValue(float(normalization["manual_range"][1]))
            for name, (slider, spin, scale) in self.display_parameters.items():
                slider.setValue(int(round(float(opacity[name]) * scale)))
                spin.setValue(float(opacity[name]))
            self._set_combo_data(self.combo_quality_mode, quality["mode"])
            self._set_combo_data(self.combo_interpolation, quality["interpolation"])
            self._set_combo_data(self.combo_resting_quality, quality["resting"])
        finally:
            del blockers
        self._display_view = str(view)
        self._resolved_range = None if resolved_range is None else tuple(float(v) for v in resolved_range)
        self._sync_display_visibility()
        self._update_range_label()

    def _sync_display_visibility(self):
        independent = self.combo_display_mode.currentData() == INDEPENDENT_MODE
        is_3d = self._display_view == "3d"
        self.grp_normalize.setVisible(bool(independent))
        self.grp_opacity.setVisible(bool(independent and is_3d))
        self.grp_quality.setVisible(bool(is_3d))
        self.combo_map.setVisible(not independent)
        self.btn_apply_map.setVisible(not independent)
        manual = independent and self.combo_normalization.currentData() == NORMALIZATION_MANUAL
        self.manual_low.setEnabled(bool(manual))
        self.manual_high.setEnabled(bool(manual))
        explicit = self.combo_quality_mode.currentData() == "explicit"
        self.combo_interpolation.setEnabled(bool(explicit))
        self.combo_resting_quality.setEnabled(bool(explicit))
        self.relayout_scroll_content()

    def _update_range_label(self):
        if self._resolved_range is None:
            text = "实际强度范围：待统计"
        else:
            text = f"实际强度范围：{self._resolved_range[0]:.6g} ～ {self._resolved_range[1]:.6g}"
        self.range_label.setText(text)

    def set_resolved_range(self, data_range):
        self._resolved_range = None if data_range is None else tuple(float(v) for v in data_range)
        self._update_range_label()

    def set_display_view(self, view):
        self._display_view = str(view)
        self._sync_display_visibility()

    def get_display_style(self):
        levels = tuple(float(slider.value()) for slider in (self.s_low, self.s_gamma, self.s_up))
        opacity_mode = str(self.combo_map.currentText())
        if self.combo_display_mode.currentData() != INDEPENDENT_MODE:
            return legacy_display_style(levels, opacity_mode)
        style = new_display_style(levels, opacity_mode)
        style["normalization"] = {
            "mode": str(self.combo_normalization.currentData()),
            "percentiles": (0.0, 99.5),
            "manual_range": (float(self.manual_low.value()), float(self.manual_high.value())),
        }
        opacity = {"preset": OPACITY_PRESET_CUSTOM}
        for name, (slider, _spin, scale) in self.display_parameters.items():
            opacity[name] = slider.value() / float(scale)
        style["opacity"] = opacity
        return resolved_display_style(style)

    def get_render_quality(self):
        if self.combo_quality_mode.currentData() != "explicit":
            return legacy_render_quality()
        quality = new_render_quality()
        quality["interpolation"] = str(self.combo_interpolation.currentData())
        quality["resting"] = str(self.combo_resting_quality.currentData())
        return quality

    # ------------------------------------------------------------------
    # 扩展卡片（插件提供内容，宿主负责挂载、显隐与自适应宽度）
    # ------------------------------------------------------------------
    def mount_extension_card(self, plugin_id, title, panel):
        """把一个插件面板挂成卡片；返回卡片本体。重复挂载同一 id 会先移除旧卡片。"""
        if plugin_id in self._extension_cards:
            self.remove_extension_card(plugin_id)

        group, body = self._create_group(title)
        body.addWidget(panel)
        row = centered_widget_row(group, self.MIN_GROUP_WIDTH)
        self._extension_slot.addLayout(row)
        self._extension_cards[plugin_id] = (group, row)
        self._extension_card_target[plugin_id] = True
        self._apply_adaptive_layout()
        self.relayout_scroll_content()
        return group

    def remove_extension_card(self, plugin_id):
        entry = self._extension_cards.pop(plugin_id, None)
        self._extension_card_target.pop(plugin_id, None)
        if entry is None:
            return
        group, row = entry
        if group in self._adaptive_groups:
            self._adaptive_groups.remove(group)
        # centered_widget_row 只包着这一张卡片：连行带卡片一起回收，别留下空位。
        while row.count():
            item = row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._extension_slot.removeItem(row)
        self.relayout_scroll_content()

    def set_extension_card_visible(self, plugin_id, visible, *, animate=True):
        entry = self._extension_cards.get(plugin_id)
        if entry is None:
            return
        group = entry[0]
        if bool(visible) == self._extension_card_target.get(plugin_id, True):
            # 目标状态不变就不重播动画：每次 3D 重绘都会调到这里，重播会让卡片
            # 在时间轴拖动、调色阶时反复淡入淡出。
            return
        self._extension_card_target[plugin_id] = bool(visible)
        if animate:
            animate_widget_visibility(
                group,
                visible,
                on_update=self.relayout_scroll_content,
                on_settled=self.relayout_scroll_content,
            )
        else:
            set_widget_visibility_instant(group, visible)
            self.relayout_scroll_content()

    def extension_card_ids(self):
        return list(self._extension_cards)

    # ------------------------------------------------------------------
    # 「相机视角」显隐与数值同步（相机实例由主窗口持有）
    # ------------------------------------------------------------------
    def set_camera_view_visible(self, visible, *, animate=True):
        """平滑展开/收起「相机视角」模块；目标状态不变则不重复播放动画。"""
        visible = bool(visible)
        if self._camera_view_visible_target == visible:
            return
        self._camera_view_visible_target = visible
        if animate:
            animate_widget_visibility(
                self.grp_camera,
                visible,
                on_update=self.relayout_scroll_content,
                on_settled=self.relayout_scroll_content,
            )
        else:
            set_widget_visibility_instant(self.grp_camera, visible)
            self.relayout_scroll_content()

    def camera_pose(self):
        return self.camera_panel.current_pose()

    def sync_camera_pose(self, pose, *, force=False):
        self.camera_panel.sync_pose(pose, force=force)

    def _apply_extra_widths(self, group_width, widths):
        self.camera_panel.fit_value_box_width(
            group_width - 2 * self.CARD_HORIZONTAL_PADDING
        )

    def get_selected_cmap(self):
        return self.combo_cmap.currentText()

    def closeEvent(self, event):
        # 高度动画会回调滚动页；先停掉再交给 Qt 析构页面与图形效果。
        self.scroll.widget_scroll_animation.stop()
        super().closeEvent(event)

    def get_denoise_settings(self):
        """ 获取当前选中的三级去噪配置 """
        return [
            self.combo_n1.currentText(),
            self.combo_n2.currentText(),
            self.combo_n3.currentText()
        ]

    def set_backend_mode(self, mode):
        index = combo_index_for_text(self.combo_backend, mode)
        if index >= 0:
            self.combo_backend.setCurrentIndex(index)

    def set_nvidia_backend_available(self, available):
        index = combo_index_for_text(self.combo_backend, "NVIDIA GPU")
        if index < 0:
            return
        item = self.combo_backend.model().item(index)
        if item is not None:
            item.setEnabled(bool(available))
        self.combo_backend.setItemData(
            index,
            None if available else "当前环境未安装或无法使用 CuPy/CUDA，Auto 将使用 CPU。",
            3,
        )

    def export_state(self):
        return {
            "combo_cmap": self.combo_cmap.currentText(),
            "s_low": {
                "minimum": int(self.s_low.minimum()),
                "maximum": int(self.s_low.maximum()),
                "value": int(self.s_low.value()),
            },
            "s_gamma": {
                "minimum": int(self.s_gamma.minimum()),
                "maximum": int(self.s_gamma.maximum()),
                "value": int(self.s_gamma.value()),
            },
            "s_up": {
                "minimum": int(self.s_up.minimum()),
                "maximum": int(self.s_up.maximum()),
                "value": int(self.s_up.value()),
            },
            "combo_map": self.combo_map.currentText(),
            "combo_n1": self.combo_n1.currentText(),
            "combo_n2": self.combo_n2.currentText(),
            "combo_n3": self.combo_n3.currentText(),
            "display_style": self.get_display_style(),
            "render_quality": self.get_render_quality(),
            "display_range": self._resolved_range,
        }

    def restore_state(self, state, *, block_signals=True):
        state = state or {}
        widgets = [
            self.combo_cmap,
            self.s_low,
            self.s_gamma,
            self.s_up,
            self.combo_map,
            self.combo_n1,
            self.combo_n2,
            self.combo_n3,
            *self._display_style_widgets(),
        ]
        blockers = [QSignalBlocker(widget) for widget in widgets] if block_signals else []
        try:
            defaults = {"s_low": 0, "s_gamma": 50, "s_up": 100}
            for slider_name, slider in (("s_low", self.s_low), ("s_gamma", self.s_gamma), ("s_up", self.s_up)):
                slider_state = state.get(slider_name) or {}
                slider.setRange(int(slider_state.get("minimum", 0)), int(slider_state.get("maximum", 100)))
                value = int(slider_state.get("value", defaults[slider_name]))
                slider.setValue(max(int(slider.minimum()), min(int(slider.maximum()), value)))

            for combo_name, combo, default in (
                ("combo_cmap", self.combo_cmap, "magma"),
                ("combo_map", self.combo_map, "线性"),
                ("combo_n1", self.combo_n1, "None"),
                ("combo_n2", self.combo_n2, "None"),
                ("combo_n3", self.combo_n3, "None"),
            ):
                index = combo_index_for_text(combo, state.get(combo_name, default))
                if index >= 0:
                    combo.setCurrentIndex(index)
            if "display_style" in state:
                style = state["display_style"]
                quality = state.get("render_quality", new_render_quality())
            else:
                style = legacy_display_style(self.get_display_style()["levels"], self.combo_map.currentText())
                quality = legacy_render_quality()
            self.set_display_style(
                style, quality, resolved_range=state.get("display_range"), view=self._display_view,
            )
        finally:
            del blockers
