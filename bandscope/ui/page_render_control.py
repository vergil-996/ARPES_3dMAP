from PyQt5.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt5.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from siui.components.combobox_ import SiCapsuleComboBox
from siui.components.widgets import SiLabel

import bandscope.ui.theme as theme
from bandscope.ui.camera_view_controls import CameraViewPanel
from bandscope.ui.control_layout_utils import (
    animate_widget_visibility,
    centered_widget_row,
    combo_index_for_text,
    set_widget_visibility_instant,
)
from bandscope.ui.control_page_base import ControlPageBase
from bandscope.processing.denoise_config import DENOISE_METHODS as CONFIG_DENOISE_METHODS


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
    #: 能带面叠加：某一层被用户勾选/取消（层键, 是否可见）。
    overlay_visibility_changed = pyqtSignal(str, bool)

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

        self.s_up = self._create_accent_slider()
        self.s_up.setRange(0, 100)
        self.s_up.setValue(100)  # 默认不截断高光

        self.s_gamma = self._create_accent_slider()
        self.s_gamma.setRange(0, 100)
        self.s_gamma.setValue(50)  # 默认线性映射 (Gamma 1.0)

        self.s_low = self._create_accent_slider()
        self.s_low.setRange(0, 100)
        self.s_low.setValue(0)    # 默认不截断低光

        self._add_centered_slider_block(v_exp, "白场", self.s_up)
        self._add_centered_slider_block(v_exp, "灰场", self.s_gamma)
        self._add_centered_slider_block(v_exp, "黑场", self.s_low)

        self.combo_map = self._create_denoise_combo("强度映射方式", ["线性", "对数", "幂函数", "sigmoid"])
        self.btn_apply_map = self._create_btn("应用设置", "primary")

        v_exp.addLayout(centered_widget_row(self.combo_map, self.MIN_CONTROL_ROW_WIDTH))
        v_exp.addLayout(centered_widget_row(self.btn_apply_map, self.BUTTON_WIDTH))

        self.vbox.addLayout(centered_widget_row(grp_exp, self.MIN_GROUP_WIDTH))

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

        # 能带面叠加：插件面结果在三维视图里的显隐开关。没有面结果时整张卡片
        # 收起（与扩展卡片一样，基础安装包看不到它）。
        grp_overlay, v_overlay = self._create_group("能带面叠加")
        self.grp_overlay = grp_overlay
        self._overlay_rows = {}
        self._overlay_signature = ()
        self._overlay_visible_target = True
        self._overlay_slot = QVBoxLayout()
        self._overlay_slot.setContentsMargins(0, 0, 0, 0)
        self._overlay_slot.setSpacing(4)
        v_overlay.addLayout(self._overlay_slot)
        self.overlay_hint = SiLabel(
            "还没有可叠加的能带面：在三维视图上用能带重构插件跑一次，结果会自动出现在这里。",
            grp_overlay,
        )
        self.overlay_hint.setWordWrap(True)
        self.overlay_hint.setStyleSheet(f"color: {theme.TEXT_3}; font-size: 11px;")
        v_overlay.addWidget(self.overlay_hint, 0, Qt.AlignHCenter)
        self.vbox.addLayout(centered_widget_row(grp_overlay, self.MIN_GROUP_WIDTH))
        self.set_overlay_card_visible(False, animate=False)

        # 全局计算后端（不随分析结果页保存）
        grp_backend, v_backend = self._create_group("性能与硬件")
        self.combo_backend = self._create_denoise_combo("计算后端", ["Auto", "CPU", "NVIDIA GPU"])
        v_backend.addLayout(centered_widget_row(self.combo_backend, self.MIN_CONTROL_ROW_WIDTH))

        self.vbox.addLayout(centered_widget_row(grp_backend, self.MIN_GROUP_WIDTH))

        # 启动时还没有有效 3D 场景：先直接收起，避免首帧闪现再收起。
        self.set_camera_view_visible(False, animate=False)

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
    # 能带面叠加卡片（宿主提供层列表，勾选状态由宿主保存）
    # ------------------------------------------------------------------
    def set_overlay_layers(self, layers):
        """按 ``(key, label, color, visible)`` 列表刷新卡片。

        层集合与文案没变时只同步勾选状态（不重建控件），避免每次三维重绘都把
        正在点的勾选框换掉。
        """
        signature = tuple((str(key), str(label), str(color or "")) for key, label, color, _v in layers)
        if signature != self._overlay_signature:
            self._overlay_signature = signature
            self._rebuild_overlay_rows(layers)
        for key, _label, _color, visible in layers:
            self._set_overlay_row_checked(str(key), bool(visible))

    def _rebuild_overlay_rows(self, layers):
        while self._overlay_slot.count():
            item = self._overlay_slot.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())
        self._overlay_rows = {}
        for key, label, color, visible in layers:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            chip = QLabel("■")
            chip.setStyleSheet(f"color: {color or theme.ACCENT}; font-size: 13px;")
            row.addWidget(chip)
            check = QCheckBox(str(label))
            check.setChecked(bool(visible))
            check.toggled.connect(
                lambda state, layer_key=str(key): self.overlay_visibility_changed.emit(
                    layer_key, bool(state)
                )
            )
            row.addWidget(check)
            row.addStretch(1)
            container = QWidget(self)
            container.setLayout(row)
            self._overlay_slot.addWidget(container)
            self._overlay_rows[str(key)] = check

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _set_overlay_row_checked(self, key, visible):
        check = self._overlay_rows.get(key)
        if check is None or check.isChecked() == bool(visible):
            return
        blocker = QSignalBlocker(check)
        try:
            check.setChecked(bool(visible))
        finally:
            del blocker

    def set_overlay_card_visible(self, visible, *, animate=True):
        visible = bool(visible)
        if self._overlay_visible_target == visible:
            return
        self._overlay_visible_target = visible
        if animate:
            animate_widget_visibility(
                self.grp_overlay,
                visible,
                on_update=self.relayout_scroll_content,
                on_settled=self.relayout_scroll_content,
            )
        else:
            set_widget_visibility_instant(self.grp_overlay, visible)
            self.relayout_scroll_content()

    def overlay_layer_keys(self):
        return list(self._overlay_rows)

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
        ]
        blockers = [QSignalBlocker(widget) for widget in widgets] if block_signals else []

        try:
            for slider_name, slider in (("s_low", self.s_low), ("s_gamma", self.s_gamma), ("s_up", self.s_up)):
                slider_state = state.get(slider_name) or {}
                minimum = slider_state.get("minimum")
                maximum = slider_state.get("maximum")
                if minimum is not None and maximum is not None:
                    slider.setRange(int(minimum), int(maximum))
                if "value" in slider_state:
                    value = int(slider_state["value"])
                    value = max(int(slider.minimum()), min(int(slider.maximum()), value))
                    slider.setValue(value)

            for combo_name, combo in (
                ("combo_cmap", self.combo_cmap),
                ("combo_map", self.combo_map),
                ("combo_n1", self.combo_n1),
                ("combo_n2", self.combo_n2),
                ("combo_n3", self.combo_n3),
            ):
                if combo_name not in state:
                    continue
                index = combo_index_for_text(combo, state[combo_name])
                if index >= 0:
                    combo.setCurrentIndex(index)
        finally:
            del blockers
