from PyQt5.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from siui.components.widgets import SiLabel

import bandscope.ui.theme as theme
from bandscope.ui.camera_view_controls import CameraViewPanel
from bandscope.ui.colormap_selector import (
    COLORMAP_OPTIONS,
    DEFAULT_COLORMAP,
    OutputColormapSelector,
)
from bandscope.ui.enhancement_controls import RegionComboBox
from bandscope.ui.levels_controls import ColormapRangeEditor, LevelsEditor
from bandscope.ui.opacity_controls import OpacityCurveEditor
from bandscope.ui.control_layout_utils import (
    animate_widget_visibility,
    centered_widget_row,
    set_widget_visibility_instant,
)
from bandscope.ui.control_page_base import ControlPageBase
from bandscope.ui.denoise_pipeline import DenoisePipelineWidget
from bandscope.ui.ui_controls import AnimatedLockButton
from bandscope.core.display_style import (
    INDEPENDENT_MODE,
    is_independent,
    new_display_style,
    resolved_display_style,
    with_display_mode,
)


class RenderControlPage(ControlPageBase):
    #: 色带顺序的兼容引用；权威列表在 ``colormap_selector``，Blender 导出窗口共用同一份。
    CMAP_OPTIONS = COLORMAP_OPTIONS

    #: CardGroup 的左右内边距（control_page_base.CardGroup 构造参数）。
    CARD_HORIZONTAL_PADDING = 14

    #: 「视角」模块的显隐目标状态：构造时可见，主窗口在启动阶段直接收起。
    _camera_view_visible_target = True

    #: 输入框提交新姿态（姿态, 本次改动的字段名）：由主窗口连接渲染状态与相机实例。
    camera_pose_committed = pyqtSignal(object, str)
    #: 用户从「输出色带」菜单选中另一条色带；由主窗口接一次精确刷新。
    colormap_changed = pyqtSignal()
    display_value_changed = pyqtSignal()
    display_value_committed = pyqtSignal()
    denoise_edited = pyqtSignal()
    denoise_committed = pyqtSignal()

    def build_body(self):
        # 视角：只在有效 3D 场景出现，由主窗口按当前视图平滑收放。
        grp_camera, v_camera = self._create_group("视角")
        self.camera_panel = CameraViewPanel(grp_camera)
        self.camera_panel.pose_committed.connect(self.camera_pose_committed)
        v_camera.addWidget(self.camera_panel, 0, Qt.AlignHCenter)
        self.grp_camera = grp_camera
        self.vbox.addLayout(centered_widget_row(grp_camera, self.MIN_GROUP_WIDTH))

        # 显示对象统领下方色阶与不透明度；「全部显示」下实际调节会同时写入
        # 背景和全部区域。锁定始终统一冻结各对象自己的强度归一化范围。
        self.display_target_label = SiLabel("显示对象")
        self.display_target_label.setStyleSheet(theme.field_label_qss())
        self.display_target_label.setFixedHeight(22)
        self._adaptive_row_controls.append(self.display_target_label)
        self.vbox.addLayout(centered_widget_row(self.display_target_label, self.MIN_CONTROL_ROW_WIDTH))
        self.region_combo = RegionComboBox(self)
        self.region_combo.sync([], None)
        self.btn_lock = AnimatedLockButton(self)
        self.btn_lock.setAccessibleName("锁定全部强度范围")
        theme.style_push_button(self.btn_lock, "secondary")
        self.btn_lock.setToolTip(
            "锁定全部强度范围：开启后，背景与所有增强区域分别按\n"
            "各自当前强度范围固定归一化，颜色与不透明度共用该范围。\n"
            "拖动时间轴或生成新结果页时仍按锁定范围映射，便于逐帧对比；\n"
            "再次点击恢复按每帧数据自动分配。锁定不会禁止编辑色阶或曲线。"
        )
        self.display_target_row = QWidget(self)
        target_row_layout = QHBoxLayout(self.display_target_row)
        target_row_layout.setContentsMargins(0, 0, 0, 0)
        target_row_layout.setSpacing(8)
        target_row_layout.addWidget(self.region_combo, 1)
        target_row_layout.addWidget(self.btn_lock)
        self._adaptive_row_controls.append(self.display_target_row)
        self.vbox.addLayout(centered_widget_row(self.display_target_row, self.MIN_CONTROL_ROW_WIDTH))

        # 色阶
        grp_exp, v_exp = self._create_group("色阶")

        input_label = SiLabel("输入色阶 · 累计直方图")
        input_label.setStyleSheet(theme.field_label_qss())
        v_exp.addWidget(input_label)
        self.levels_editor = LevelsEditor(self)
        self._adaptive_slider_blocks.append(self.levels_editor)
        self.s_low = self.levels_editor.black
        self.s_gamma = self.levels_editor.gray
        self.s_up = self.levels_editor.white
        self.s_low.setToolTip("黑场：输入强度窗口下限。")
        self.s_gamma.setToolTip("灰场：映射到色带中点的输入强度；向左提亮，向右压暗。")
        self.s_up.setToolTip("白场：输入强度窗口上限。")
        v_exp.addLayout(centered_widget_row(self.levels_editor))

        # 「输出色带」标题行是主界面唯一的色带选择入口：左侧标题，右侧当前
        # 色带名和下拉箭头，点击展开带色样的单列菜单。
        self.colormap_selector = OutputColormapSelector(self)
        self.colormap_selector.setToolTip(
            "全部显示对象共用同一输出色带；点击展开列表，选择后立即刷新当前视图。"
        )
        self.colormap_selector.colormap_changed.connect(self._on_colormap_selected)
        self._adaptive_slider_blocks.append(self.colormap_selector)
        v_exp.addLayout(centered_widget_row(self.colormap_selector))
        self.colormap_range_editor = ColormapRangeEditor(self.get_selected_cmap(), self)
        self._adaptive_slider_blocks.append(self.colormap_range_editor)
        self.s_cmap_low = self.colormap_range_editor.cmap_low
        self.s_cmap_high = self.colormap_range_editor.cmap_high
        v_exp.addLayout(centered_widget_row(self.colormap_range_editor))

        self.vbox.addLayout(centered_widget_row(grp_exp, self.MIN_GROUP_WIDTH))

        self.grp_opacity, v_opacity = self._create_group("不透明度")
        self.opacity_editor = OpacityCurveEditor(self, show_interpolation=False)
        self.grp_opacity.add_header_widget(self.opacity_editor.interpolation_selector)
        self._adaptive_slider_blocks.append(self.opacity_editor)
        self.opacity_editor.valueChanged.connect(self.display_value_changed)
        self.opacity_editor.valueCommitted.connect(self.display_value_committed)
        v_opacity.addLayout(centered_widget_row(self.opacity_editor))
        self.vbox.addLayout(centered_widget_row(self.grp_opacity, self.MIN_GROUP_WIDTH))

        self._resolved_range = None
        self._display_view = "3d"
        self.set_display_style(new_display_style(), view="3d")

        # 去噪属于当前结果页的数据范围，不随显示对象下拉框切换。
        grp_noise, v_noise = self._create_group("去噪")
        self.denoise_scope_label = SiLabel("当前数据范围 · 全局共享")
        self.denoise_scope_label.setStyleSheet(theme.field_label_qss())
        self.denoise_scope_label.setToolTip(
            "去噪配置由所有显示对象共享；应用范围由当前结果页的数据范围决定，"
            "不随显示对象切换。节点按顺序处理，结构与参数修改自动生效。"
        )
        v_noise.addWidget(self.denoise_scope_label)

        self.denoise_pipeline = DenoisePipelineWidget(grp_noise)
        self.denoise_pipeline.pipeline_edited.connect(self.denoise_edited)
        self.denoise_pipeline.pipeline_committed.connect(self.denoise_committed)
        self.denoise_pipeline.pipeline_edited.connect(self.relayout_scroll_content)
        self._adaptive_slider_blocks.append(self.denoise_pipeline)
        v_noise.addLayout(centered_widget_row(self.denoise_pipeline))

        self.vbox.addLayout(centered_widget_row(grp_noise, self.MIN_GROUP_WIDTH))

        # 三维渲染与二维分析插件共用这里的通用卡片接口，由宿主分开登记并按
        # 当前视图控制显隐。基础安装包没有插件时这里始终为空。
        self._extension_cards = {}
        # 卡片的显隐目标状态；普通三维卡片构造时可见，分析卡片挂载后由宿主立即隐藏。
        self._extension_card_target = {}
        self._extension_slot = QVBoxLayout()
        self._extension_slot.setContentsMargins(0, 0, 0, 0)
        self._extension_slot.setSpacing(self.SECTION_SPACING)
        self.vbox.addLayout(self._extension_slot)

        # 启动时还没有有效 3D 场景：先直接收起，避免首帧闪现再收起。
        self.set_camera_view_visible(False, animate=False)

    def _on_colormap_selected(self, name):
        # 菜单收起后渐变条立即更新；刷新由页面信号交给主窗口，只发一次。
        self.colormap_range_editor.set_colormap(name)
        self.colormap_changed.emit()

    def _restore_colormap(self, name):
        """按保存的名称静默同步标题与渐变条，不触发用户选色的刷新流程。"""
        self.colormap_selector.set_colormap(str(name or DEFAULT_COLORMAP))
        self.colormap_range_editor.set_colormap(self.colormap_selector.current_colormap())

    def _display_style_widgets(self):
        return [
            self.s_low, self.s_gamma, self.s_up,
            self.s_cmap_low, self.s_cmap_high,
        ]

    def set_display_style(self, style, *, resolved_range=None, view="3d"):
        canonical = (resolved_display_style(style) if is_independent(style)
                     else with_display_mode(style, INDEPENDENT_MODE))
        blockers = [QSignalBlocker(widget) for widget in self._display_style_widgets()]
        try:
            for slider, value in zip((self.s_low, self.s_gamma, self.s_up), canonical["levels"]):
                slider.setValue(int(round(float(value))))
            for slider, value in zip(
                (self.s_cmap_low, self.s_cmap_high), canonical["colormap_range"],
            ):
                slider.setValue(int(round(float(value) * 1000.0)))
            self.opacity_editor.set_opacity(canonical["opacity"])
        finally:
            del blockers
        self._display_view = str(view)
        self._resolved_range = None if resolved_range is None else tuple(float(v) for v in resolved_range)
        self._opacity_mode = str(canonical.get("opacity_mode", "线性"))
        self._sync_display_visibility()
        self.levels_editor.sync_from_proxies()
        self.colormap_range_editor.sync_from_proxies()
        self.opacity_editor.sync_from_proxies()

    def _sync_display_visibility(self):
        is_3d = self._display_view == "3d"
        self.grp_opacity.setVisible(bool(is_3d))
        self.relayout_scroll_content()

    def set_display_view(self, view):
        self._display_view = str(view)
        self._sync_display_visibility()

    def set_levels_statistics(self, histogram, data_range=None):
        self.levels_editor.set_statistics(histogram, data_range)

    def get_colormap_range(self):
        return self.colormap_range_editor.get_range()

    def get_display_style(self):
        levels = tuple(float(slider.value()) for slider in (self.s_low, self.s_gamma, self.s_up))
        return new_display_style(
            levels, self._opacity_mode, self.get_colormap_range(),
            self.opacity_editor.points(),
            opacity_interpolation=self.opacity_editor.interpolation(),
            opacity_segments=self.opacity_editor.segments(),
        )

    def get_opacity_points(self):
        return self.opacity_editor.points()

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
    # 「视角」显隐与数值同步（相机实例由主窗口持有）
    # ------------------------------------------------------------------
    def set_camera_view_visible(self, visible, *, animate=True):
        """平滑展开/收起「视角」模块；目标状态不变则不重复播放动画。"""
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
        return self.colormap_selector.current_colormap()

    def closeEvent(self, event):
        # 高度动画会回调滚动页；先停掉再交给 Qt 析构页面与图形效果。
        self.scroll.widget_scroll_animation.stop()
        super().closeEvent(event)

    def get_denoise_settings(self):
        return self.denoise_pipeline.get_denoise_settings()

    def set_denoise_execution_state(self, state, message=""):
        self.denoise_pipeline.set_execution_state(state, message)

    def export_state(self):
        return {
            "combo_cmap": self.get_selected_cmap(),
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
            "s_cmap_low": {
                "minimum": int(self.s_cmap_low.minimum()),
                "maximum": int(self.s_cmap_low.maximum()),
                "value": int(self.s_cmap_low.value()),
            },
            "s_cmap_high": {
                "minimum": int(self.s_cmap_high.minimum()),
                "maximum": int(self.s_cmap_high.maximum()),
                "value": int(self.s_cmap_high.value()),
            },
            "denoise_pipeline": self.denoise_pipeline.export_state(),
            "display_style": self.get_display_style(),
            "display_range": self._resolved_range,
        }

    def restore_state(self, state, *, block_signals=True):
        state = state or {}
        widgets = [
            self.s_low,
            self.s_gamma,
            self.s_up,
            self.s_cmap_low,
            self.s_cmap_high,
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
            for slider_name, slider, default in (
                ("s_cmap_low", self.s_cmap_low, 0),
                ("s_cmap_high", self.s_cmap_high, 1000),
            ):
                slider_state = state.get(slider_name) or {}
                slider.setRange(int(slider_state.get("minimum", 0)),
                                int(slider_state.get("maximum", 1000)))
                slider.setValue(int(slider_state.get("value", default)))

            # 色带按名称恢复：列表重排不影响选中项，也不触发用户选色的刷新。
            self._restore_colormap(state.get("combo_cmap", DEFAULT_COLORMAP))
            if "denoise_pipeline" in state:
                denoise_state = state.get("denoise_pipeline") or {"nodes": []}
            else:
                denoise_state = {
                    "methods": [
                        state.get(combo_name, "None")
                        for combo_name in ("combo_n1", "combo_n2", "combo_n3")
                    ],
                    "legacy_controls": state.get("legacy_controls") or {},
                }
            self.denoise_pipeline.restore_state(
                denoise_state, block_signals=block_signals,
            )
            if "display_style" in state:
                style = state["display_style"]
            else:
                has_colormap_range = "s_cmap_low" in state or "s_cmap_high" in state
                colormap_range = self.get_colormap_range() if has_colormap_range else (0.0, 1.0)
                style = new_display_style(
                    self.get_display_style()["levels"],
                    state.get("combo_map", "线性"), colormap_range,
                )
            self.set_display_style(
                style, resolved_range=state.get("display_range"), view=self._display_view,
            )
        finally:
            del blockers
