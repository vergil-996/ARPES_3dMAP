from PyQt5.QtCore import QSignalBlocker
from PyQt5.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from siui.components.combobox_ import SiCapsuleComboBox
from siui.components.widgets import SiLabel

import bandscope.ui.theme as theme
from bandscope.ui.control_layout_utils import (
    animate_widget_visibility,
    centered_widget_row,
    combo_index_for_text,
    set_widget_visibility_instant,
)
from bandscope.ui.control_page_base import ControlPageBase


class DataProcessPage(ControlPageBase):
    COMBO_TEXT_ALIASES = {"切片态密度": "切片内强度积分"}

    # 内置动作：稳定键、展示文本、排序权重。插件注册的动作以
    # ``插件ID:动作ID`` 并入同一列表，按 ``(权重, 文本)`` 排序；EDC 瀑布图权重
    # 留出的位置正好是它迁移前所在的位置。
    BUILTIN_OTHER_ACTIONS = (
        ("builtin:slice_dos", "切片内强度积分", 0.0),
        ("builtin:energy_dos", "能级态密度", 10.0),
        ("builtin:edc_curve", "单条 EDC 曲线", 30.0),
        ("builtin:second_derivative", "二阶导", 40.0),
    )
    # “其他积分”下拉的完整内置选项；其中时间相关项只在数据含时间轴时保留。
    OTHER_INTEGRAL_ITEMS = [item[1] for item in BUILTIN_OTHER_ACTIONS]
    TIME_DEPENDENT_OTHER_ITEMS = ("切片内强度积分",)
    #: 插件动作缺失时的兜底项：回退到第一个可用内置项而不是旧下标。
    OTHER_FALLBACK_KEY = "builtin:slice_dos"

    def __init__(self, parent=None):
        self._is_updating = False
        self._time_axis_available = True
        self._filtered_out_other_selection = None
        #: 已进入下拉的插件动作：``[(稳定键, 展示文本, 权重)]``。
        self._plugin_other_actions = []
        #: 当前下拉每一项对应的稳定键，按显示顺序排列。
        self._other_keys = []
        super().__init__(parent)

    def _create_combo(self, title, items, registry):
        combo = SiCapsuleComboBox(self)
        combo.setTitle(title)
        combo.setFixedHeight(30)
        combo.setFixedWidth(self.MIN_COMBO_WIDTH)
        registry.append(combo)
        combo.setEditable(False)
        combo.addItems(items)
        theme.raise_well_on_card(combo)
        return combo

    def _create_length_block(self):
        """「积分长度」输入行：与上下限滑条块同高，数值框右对齐。"""
        container = QWidget(self)
        container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        container.setFixedWidth(self.MIN_SLIDER_BLOCK_WIDTH)
        self._adaptive_slider_blocks.append(container)

        block = QVBoxLayout(container)
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        label = SiLabel("积分长度", container)
        label.setStyleSheet(theme.field_label_qss())
        head.addWidget(label)
        head.addStretch()
        block.addLayout(head)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch()
        row.addWidget(self.input_ax_length)
        block.addLayout(row)
        return container

    def _create_button_row(self, buttons):
        container = QWidget(self)
        container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(self.AXIS_VALUE_BOX_SPACING)
        for button in buttons:
            row.addWidget(button)
        return container

    def build_body(self):
        grp_t, v_t = self._create_group("对时间轴积分")
        self.grp_t = grp_t

        self.s_t_up = self._create_accent_slider(axis=True)
        self.input_t_up = self._create_axis_value_box()

        self.s_t_low = self._create_accent_slider(axis=True)
        self.input_t_low = self._create_axis_value_box()

        self.s_t_low.valueChanged.connect(self._on_t_low_changed)
        self.s_t_up.valueChanged.connect(self._on_t_up_changed)

        self.btn_t_apply = self._create_btn("应用", "primary")
        self._add_centered_slider_block(v_t, "积分上限", self.s_t_up, self.input_t_up)
        self._add_centered_slider_block(v_t, "积分下限", self.s_t_low, self.input_t_low)
        v_t.addLayout(centered_widget_row(self.btn_t_apply, self.BUTTON_WIDTH))
        self.vbox.addLayout(centered_widget_row(grp_t, self.MIN_GROUP_WIDTH))

        grp_ax, v_ax = self._create_group("对坐标轴积分")

        self.combo_ax = self._create_combo("选择轴向", ["X轴", "Y轴", "Z轴"], self._adaptive_combo_controls)

        self.s_ax_up = self._create_accent_slider(axis=True)
        self.input_ax_up = self._create_axis_value_box()
        self.input_ax_up.setToolTip("积分上限（物理坐标）")

        self.s_ax_low = self._create_accent_slider(axis=True)
        self.input_ax_low = self._create_axis_value_box()
        self.input_ax_low.setToolTip("积分下限（物理坐标）")

        # 中心位置控件已删除：位置由画布底栏的位置滑条控制，这里只保留
        # 区间本身的端点、长度与锁定。
        self.input_ax_length = self._create_axis_value_box()
        self.input_ax_length.setToolTip("积分长度（物理跨度，可直接输入）")

        self.btn_ax_lock = self._create_btn("锁定区间", "secondary")
        self.btn_ax_lock.setCheckable(True)
        self.btn_ax_lock.setToolTip("锁定后上下限一起平移，区间长度保持不变")

        self.btn_ax_apply = self._create_btn("应用", "primary")

        v_ax.addLayout(centered_widget_row(self.combo_ax, self.MIN_COMBO_WIDTH))
        self._add_centered_slider_block(v_ax, "积分上限", self.s_ax_up, self.input_ax_up)
        self._add_centered_slider_block(v_ax, "积分下限", self.s_ax_low, self.input_ax_low)
        v_ax.addLayout(centered_widget_row(self._create_length_block(), self.MIN_SLIDER_BLOCK_WIDTH))
        v_ax.addLayout(
            centered_widget_row(self._create_button_row((self.btn_ax_lock, self.btn_ax_apply)), self.MIN_GROUP_WIDTH)
        )
        self.vbox.addLayout(centered_widget_row(grp_ax, self.MIN_GROUP_WIDTH))

        grp_other, v_other = self._create_group("其他积分")

        entries = self._available_other_entries()
        self.combo_other = self._create_combo(
            "积分类型", [text for _key, text, _order in entries], self._adaptive_row_controls
        )
        self._other_keys = [key for key, _text, _order in entries]
        self.btn_other_apply = self._create_btn("应用", "primary")

        v_other.addLayout(centered_widget_row(self.combo_other, self.MIN_COMBO_WIDTH))
        v_other.addLayout(centered_widget_row(self.btn_other_apply, self.BUTTON_WIDTH))

        self.vbox.addLayout(centered_widget_row(grp_other, self.MIN_GROUP_WIDTH))

        # 分析插件挂载点：二维结果页的插件面板放在这里，由宿主按插件能力挂载与
        # 显隐。基础安装包没有分析插件时这里始终为空，页面外观与之前一致。
        self._analysis_cards = {}
        self._analysis_card_target = {}
        self._analysis_slot = QVBoxLayout()
        self._analysis_slot.setContentsMargins(0, 0, 0, 0)
        self._analysis_slot.setSpacing(self.SECTION_SPACING)
        self.vbox.addLayout(self._analysis_slot)

    # ------------------------------------------------------------------
    # 分析插件卡片（宿主负责挂载、显隐与释放）
    # ------------------------------------------------------------------
    def mount_analysis_card(self, plugin_id, title, panel):
        """挂一张分析插件卡片；重复挂载同一 id 会先移除旧卡片。"""
        if plugin_id in self._analysis_cards:
            self.remove_analysis_card(plugin_id)
        group, body = self._create_group(title)
        body.addWidget(panel)
        row = centered_widget_row(group, self.MIN_GROUP_WIDTH)
        self._analysis_slot.addLayout(row)
        self._analysis_cards[plugin_id] = (group, row)
        # 分析面板只在二维结果页显示，新挂载的卡片先按“不可见”登记：
        # 主窗口在渲染二维页时会把它打开，避免在三维页里闪一下。
        self._analysis_card_target[plugin_id] = False
        set_widget_visibility_instant(group, False)
        self._apply_adaptive_layout()
        self.relayout_scroll_content()
        return group

    def remove_analysis_card(self, plugin_id):
        entry = self._analysis_cards.pop(plugin_id, None)
        self._analysis_card_target.pop(plugin_id, None)
        if entry is None:
            return
        group, row = entry
        if group in self._adaptive_groups:
            self._adaptive_groups.remove(group)
        while row.count():
            item = row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._analysis_slot.removeItem(row)
        self.relayout_scroll_content()

    def set_analysis_card_visible(self, plugin_id, visible, *, animate=True):
        entry = self._analysis_cards.get(plugin_id)
        if entry is None:
            return
        group = entry[0]
        if bool(visible) == self._analysis_card_target.get(plugin_id, False):
            # 目标状态不变就不重播动画：每次二维重绘都会调到这里。
            return
        self._analysis_card_target[plugin_id] = bool(visible)
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

    def analysis_card_ids(self):
        return list(self._analysis_cards)

    # ------------------------------------------------------------------
    # 时间轴相关控件的显隐
    # ------------------------------------------------------------------
    def set_time_axis_available(self, available, *, animate=True):
        """按数据是否含时间轴，平滑显隐“对时间轴积分”卡片并过滤时间相关积分项。"""
        available = bool(available)
        if self._time_axis_available == available:
            return
        self._time_axis_available = available

        self._filter_time_dependent_other_items(available)
        if animate:
            animate_widget_visibility(
                self.grp_t,
                available,
                on_update=self.relayout_scroll_content,
                on_settled=self.relayout_scroll_content,
            )
        else:
            set_widget_visibility_instant(self.grp_t, available)
            self.relayout_scroll_content()

    # ------------------------------------------------------------------
    # 「其他积分」的动作列表（内置项 + 插件注册项）
    # ------------------------------------------------------------------
    def _available_other_entries(self):
        """当前可用的完整动作列表：``[(稳定键, 展示文本, 权重)]``，按权重排序。"""
        entries = []
        for key, text, order in self.BUILTIN_OTHER_ACTIONS:
            if text in self.TIME_DEPENDENT_OTHER_ITEMS and not self._time_axis_available:
                continue
            entries.append((key, text, float(order)))
        entries.extend(self._plugin_other_actions)
        entries.sort(key=lambda item: (item[2], item[1]))
        return entries

    def current_other_key(self):
        """当前选中项的稳定键；列表为空时返回空串。"""
        index = int(self.combo_other.currentIndex())
        if 0 <= index < len(self._other_keys):
            return str(self._other_keys[index])
        return ""

    def set_integration_actions(self, actions):
        """并入插件注册的动作（``(稳定键, 展示文本, 权重)``）；保持当前选择。"""
        normalized = []
        for item in actions or ():
            try:
                key, label, order = item[0], item[1], float(item[2])
            except (IndexError, TypeError, ValueError):
                continue
            key = str(key)
            label = str(label)
            if not key or not label:
                continue
            normalized.append((key, label, order))
        if normalized == self._plugin_other_actions:
            return
        self._plugin_other_actions = normalized
        self._rebuild_other_items()

    def _rebuild_other_items(self):
        """按最新动作集合重建下拉，尽量保持当前选择（先按键、再按文本）。"""
        combo = self.combo_other
        previous_key = self.current_other_key()
        entries = self._available_other_entries()
        blocker = QSignalBlocker(combo)
        try:
            combo.clear()
            for _key, text, _order in entries:
                combo.addItem(text)
            self._other_keys = [key for key, _text, _order in entries]
            combo.setCurrentIndex(self._other_restore_index(entries, previous_key))
        finally:
            del blocker

    def _other_restore_index(self, entries, key=None, text=""):
        """恢复选中项：稳定键 → 展示文本 → 可用内置项。

        旧状态没有稳定键时按展示文本恢复；键与文本都找不到（例如插件动作缺失）
        时回退到第一个可用内置项——旧下标会随着下拉项增删指向别的功能，一律
        不采用。
        """
        keys = [key_ for key_, _text, _order in entries]
        if key and key in keys:
            return keys.index(key)
        if text:
            for index, (_key, label, _order) in enumerate(entries):
                if label == str(text):
                    return index
        return next(
            (
                index
                for index, (key_, _text, _order) in enumerate(entries)
                if key_ == self.OTHER_FALLBACK_KEY
            ),
            0 if entries else -1,
        )

    def _filter_time_dependent_other_items(self, available):
        """无时间轴时从“其他积分”下拉移除时间相关项，恢复时插回原位置。

        过滤按稳定键整表重建：插件动作与内置项共用一份顺序，移除过的时间相关
        选中项在恢复后优先选回。
        """
        combo = self.combo_other
        blocker = QSignalBlocker(combo)
        try:
            current_key = self.current_other_key()
            current_text = combo.currentText()
            if available:
                if self._filtered_out_other_selection is not None:
                    current_key = self._filtered_out_other_selection
                    self._filtered_out_other_selection = None
            else:
                removed_key = self._key_for_time_dependent_text(current_text)
                if removed_key is not None:
                    self._filtered_out_other_selection = removed_key

            entries = self._available_other_entries()
            combo.clear()
            for _key, text, _order in entries:
                combo.addItem(text)
            self._other_keys = [key for key, _text, _order in entries]
            restore = self._other_restore_index(entries, current_key)
            combo.setCurrentIndex(restore if entries else -1)
        finally:
            del blocker

    def _key_for_time_dependent_text(self, text):
        for key, label, _order in self.BUILTIN_OTHER_ACTIONS:
            if label in self.TIME_DEPENDENT_OTHER_ITEMS and label == str(text):
                return key
        return None

    def _on_t_low_changed(self, value):
        if value > self.s_t_up.value():
            self.s_t_up.setValue(value)

    def _on_t_up_changed(self, value):
        if value < self.s_t_low.value():
            self.s_t_low.setValue(value)

    # 上下限的约束与联动由 bandscope.ui.axis_interval_controller 统一负责：
    # 本页只在控件上暴露意图，不再自己维护中心/半宽，也不再在事件里回写。

    def export_state(self):
        return {
            "s_t_low": {
                "minimum": int(self.s_t_low.minimum()),
                "maximum": int(self.s_t_low.maximum()),
                "value": int(self.s_t_low.value()),
            },
            "s_t_up": {
                "minimum": int(self.s_t_up.minimum()),
                "maximum": int(self.s_t_up.maximum()),
                "value": int(self.s_t_up.value()),
            },
            "input_t_low": {
                "minimum": float(self.input_t_low.minimum()),
                "maximum": float(self.input_t_low.maximum()),
                "value": float(self.input_t_low.value()),
            },
            "input_t_up": {
                "minimum": float(self.input_t_up.minimum()),
                "maximum": float(self.input_t_up.maximum()),
                "value": float(self.input_t_up.value()),
            },
            "combo_ax": {
                "index": int(self.combo_ax.currentIndex()),
                "text": self.combo_ax.currentText(),
            },
            # 积分区间的真值放在页面参数的 axis_interval 里；本页只保存
            # 轴向选择和「其他积分」下拉，避免同一份状态存两份。下拉状态以稳定
            # 动作键为主：展示文本与下标都可能随插件加载变化。
            "combo_other": {
                "key": self.current_other_key(),
                "index": int(self.combo_other.currentIndex()),
                "text": self.combo_other.currentText(),
            },
        }

    def restore_state(self, state, *, block_signals=True):
        state = state or {}
        widgets = [
            self.s_t_low,
            self.s_t_up,
            self.input_t_low,
            self.input_t_up,
            self.combo_ax,
            self.combo_other,
        ]
        blockers = [QSignalBlocker(widget) for widget in widgets] if block_signals else []
        previous_updating = self._is_updating
        self._is_updating = True

        try:
            time_controls = (
                ("s_t_low", "input_t_low", self.s_t_low, self.input_t_low),
                ("s_t_up", "input_t_up", self.s_t_up, self.input_t_up),
            )
            for slider_name, input_name, slider, value_box in time_controls:
                slider_state = state.get(slider_name) or {}
                input_state = state.get(input_name) or {}
                minimum = slider_state.get("minimum")
                maximum = slider_state.get("maximum")
                if minimum is not None and maximum is not None:
                    slider.setRange(int(minimum), int(maximum))
                if "value" in slider_state:
                    value = int(slider_state["value"])
                    value = max(int(slider.minimum()), min(int(slider.maximum()), value))
                    slider.setValue(value)
                if "value" in input_state:
                    box_value = float(input_state["value"])
                    box_value = max(float(value_box.minimum()), min(float(value_box.maximum()), box_value))
                    value_box.setValue(box_value)
                elif "value" in slider_state:
                    value_box.setValue(float(slider.value()))

            combo_ax_state = state.get("combo_ax") or {}
            combo_ax_index = combo_ax_state.get("index")
            if combo_ax_index is None and "text" in combo_ax_state:
                combo_ax_index = combo_index_for_text(
                    self.combo_ax,
                    combo_ax_state["text"],
                    aliases=self.COMBO_TEXT_ALIASES,
                )
            if combo_ax_index is not None and 0 <= int(combo_ax_index) < self.combo_ax.count():
                self.combo_ax.setCurrentIndex(int(combo_ax_index))

            combo_other_state = state.get("combo_other") or {}
            # 下拉项会随时间轴有无与插件加载动态变化：先按稳定键恢复，其次按
            # 展示文本（含旧别名）。两者都找不到时回退到可用内置项——旧下标可能
            # 已经指向别的功能（例如插件未加载时原本的瀑布图项不存在）。
            saved_key = str(combo_other_state.get("key") or "")
            saved_text = str(combo_other_state.get("text") or "")
            saved_text = self.COMBO_TEXT_ALIASES.get(saved_text, saved_text)
            entries = self._available_other_entries()
            restore = self._other_restore_index(entries, saved_key, saved_text)
            if restore >= 0 and entries:
                self.combo_other.setCurrentIndex(restore)
        finally:
            self._is_updating = previous_updating
            del blockers
