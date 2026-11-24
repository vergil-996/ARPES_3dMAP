import os
import unittest
from copy import deepcopy
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QDialogButtonBox, QMenu, QPushButton

from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.processing.denoise_config import (
    DENOISE_METHODS,
    GAUSSIAN_METHOD_NAME,
    LOW_RANK_METHOD_NAME,
    MEDIAN_METHOD_NAME,
    NLM_METHOD_NAME,
    SG_METHOD_NAME,
    WAVELET_METHOD_NAME,
    default_method_params,
)
from bandscope.ui import theme
from bandscope.ui.denoise_pipeline import DenoisePipelineWidget


configure_qt_plugin_path()


class DenoisePipelineWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widget = DenoisePipelineWidget()
        self.widget.resize(470, 700)
        self.widget.show()
        self.addCleanup(self.widget.deleteLater)
        self.addCleanup(self.widget.close)

    def make_widget(self):
        widget = DenoisePipelineWidget()
        self.addCleanup(widget.deleteLater)
        self.addCleanup(widget.close)
        return widget

    def test_add_popup_offers_and_adds_all_six_algorithms(self):
        committed = QSignalSpy(self.widget.pipeline_committed)
        for name in DENOISE_METHODS[1:]:
            with self.subTest(method=name):
                self.widget._add_node(name)

        self.app.processEvents()
        settings = self.widget.get_denoise_settings()
        self.assertEqual([node["label"] for node in settings], list(DENOISE_METHODS[1:]))
        self.assertEqual(len({node["id"] for node in settings}), 6)
        self.assertEqual(len(committed), 6)
        for node in settings:
            self.assertEqual(node["params"], default_method_params(node["label"]))
            self.assertTrue(self.widget._node_cards[node["id"]].isVisibleTo(self.widget))

    def test_repeated_algorithm_and_duplicate_action_keep_ids_and_params_independent(self):
        self.widget._add_node(GAUSSIAN_METHOD_NAME)
        original = self.widget.get_denoise_settings()[0]
        original_dialog = self.widget.open_parameter_dialog(original["id"])
        original_dialog._controls["sigma_kx"].setValue(2.5)
        original_dialog._controls["sigma_kx"].editingFinished.emit()

        self.widget._duplicate_node(original["id"])
        nodes = self.widget.export_state()["nodes"]
        self.assertEqual(len(nodes), 2)
        self.assertNotEqual(nodes[0]["id"], nodes[1]["id"])
        self.assertEqual(nodes[0]["label"], nodes[1]["label"])
        self.assertEqual(nodes[1]["params"], nodes[0]["params"])

        duplicate_dialog = self.widget.open_parameter_dialog(nodes[1]["id"])
        duplicate_dialog._controls["sigma_kx"].setValue(4.5)
        duplicate_dialog._controls["sigma_kx"].editingFinished.emit()
        nodes = self.widget.export_state()["nodes"]
        self.assertEqual(nodes[0]["params"]["sigma_kx"], 2.5)
        self.assertEqual(nodes[1]["params"]["sigma_kx"], 4.5)

    def test_restore_regenerates_colliding_ids_without_merging_parameters(self):
        self.widget.restore_state({"nodes": [
            {
                "id": "shared-id",
                "label": GAUSSIAN_METHOD_NAME,
                "params": {"sigma_kx": 1.5},
            },
            {
                "id": "shared-id",
                "label": GAUSSIAN_METHOD_NAME,
                "params": {"sigma_kx": 6.5},
            },
        ]})

        nodes = self.widget.export_state()["nodes"]
        self.assertEqual(len(nodes), 2)
        self.assertNotEqual(nodes[0]["id"], nodes[1]["id"])
        self.assertEqual(nodes[0]["params"]["sigma_kx"], 1.5)
        self.assertEqual(nodes[1]["params"]["sigma_kx"], 6.5)

    def test_disabled_nodes_are_filtered_from_execution_but_remain_in_state(self):
        self.widget.restore_state({"nodes": [
            {"id": "off", "label": GAUSSIAN_METHOD_NAME, "enabled": False},
            {"id": "on", "label": MEDIAN_METHOD_NAME, "enabled": True},
        ]})
        committed = QSignalSpy(self.widget.pipeline_committed)

        self.assertEqual([node["id"] for node in self.widget.get_denoise_settings()], ["on"])
        self.assertEqual(len(self.widget.export_state()["nodes"]), 2)
        self.assertFalse(self.widget._node_cards["off"].property("nodeEnabled"))

        self.widget._toggle_node("off")
        self.assertEqual(
            [node["id"] for node in self.widget.get_denoise_settings()],
            ["off", "on"],
        )
        self.widget._toggle_node("on")
        self.assertEqual([node["id"] for node in self.widget.get_denoise_settings()], ["off"])
        self.assertEqual(len(committed), 2)
        self.assertEqual(len(self.widget.export_state()["nodes"]), 2)

    def test_every_structure_operation_emits_exactly_one_immediate_commit(self):
        edited = QSignalSpy(self.widget.pipeline_edited)
        committed = QSignalSpy(self.widget.pipeline_committed)

        with self.subTest(operation="add"):
            self.widget._add_node(GAUSSIAN_METHOD_NAME)
            self.assertEqual(len(edited), 1)
            self.assertEqual(len(committed), 1)
        original_id = self.widget.export_state()["nodes"][0]["id"]
        operations = [
            ("duplicate", lambda: self.widget._duplicate_node(original_id)),
            ("move", lambda: self.widget._move_to(original_id, 1)),
            ("reset", lambda: self.widget._reset_node(original_id)),
            ("toggle", lambda: self.widget._toggle_node(original_id)),
        ]
        for name, operation in operations:
            with self.subTest(operation=name):
                before_edited = len(edited)
                before_committed = len(committed)
                operation()
                self.assertEqual(len(edited), before_edited + 1)
                self.assertEqual(len(committed), before_committed + 1)

        self.assertEqual(self.widget.export_state()["nodes"][1]["id"], original_id)
        duplicate_id = next(
            node["id"] for node in self.widget.export_state()["nodes"]
            if node["id"] != original_id
        )
        for name, node_id in (("remove duplicate", duplicate_id), ("remove original", original_id)):
            with self.subTest(operation=name):
                before_edited = len(edited)
                before_committed = len(committed)
                self.widget._remove_node(node_id)
                self.assertEqual(len(edited), before_edited + 1)
                self.assertEqual(len(committed), before_committed + 1)
        self.assertEqual(self.widget.export_state(), {"nodes": []})

    def test_numeric_edit_emits_edited_then_debounces_one_commit(self):
        self.widget.restore_state({"nodes": [
            {"id": "gaussian", "label": GAUSSIAN_METHOD_NAME},
        ]})
        dialog = self.widget.open_parameter_dialog("gaussian")
        control = dialog._controls["sigma_kx"]
        edited = QSignalSpy(self.widget.pipeline_edited)
        committed = QSignalSpy(self.widget.pipeline_committed)

        control.setValue(2.0)
        self.assertEqual(len(edited), 1)
        self.assertEqual(len(committed), 0)
        self.assertTrue(dialog._debounce.isActive())
        self.assertTrue(dialog._debounce.isSingleShot())
        self.assertEqual(dialog._debounce.interval(), 600)
        self.assertEqual(self.widget._execution_state, "pending")

        QTest.qWait(350)
        control.setValue(2.5)
        QTest.qWait(350)
        self.assertEqual(len(committed), 0)
        QTest.qWait(350)
        self.assertEqual(len(committed), 1)
        self.assertEqual(
            self.widget.get_denoise_settings()[0]["params"]["sigma_kx"],
            2.5,
        )

        before_edited = len(edited)
        before_committed = len(committed)
        control.setValue(3.25)
        self.assertEqual(len(edited), before_edited + 1)
        self.assertEqual(len(committed), before_committed)
        control.editingFinished.emit()
        self.assertEqual(len(committed), before_committed + 1)
        self.assertFalse(dialog._debounce.isActive())
        self.assertEqual(
            self.widget.get_denoise_settings()[0]["params"]["sigma_kx"],
            3.25,
        )

    def test_choice_edit_commits_immediately_without_debounce(self):
        self.widget.restore_state({"nodes": [
            {"id": "sg", "label": SG_METHOD_NAME},
        ]})
        dialog = self.widget.open_parameter_dialog("sg")
        control = dialog._controls["smoothing_axis"]
        edited = QSignalSpy(self.widget.pipeline_edited)
        committed = QSignalSpy(self.widget.pipeline_committed)

        control.setCurrentIndex(control.findData("kx"))

        self.assertEqual(len(edited), 1)
        self.assertEqual(len(committed), 1)
        self.assertFalse(dialog._debounce.isActive())
        self.assertEqual(
            self.widget.get_denoise_settings()[0]["params"]["smoothing_axis"],
            "kx",
        )

    def test_parameter_dialog_has_no_save_apply_or_ok_buttons(self):
        self.widget.restore_state({"nodes": [
            {"id": "wavelet", "label": WAVELET_METHOD_NAME},
        ]})
        dialog = self.widget.open_parameter_dialog("wavelet")

        self.assertEqual(dialog.findChildren(QDialogButtonBox), [])
        button_texts = {
            button.text().replace("&", "").strip().lower()
            for button in dialog.findChildren(QPushButton)
        }
        self.assertTrue(button_texts.isdisjoint({
            "保存", "应用", "确定", "save", "apply", "ok",
        }))
        self.assertIn("自动", dialog.auto_status.text())
        self.assertIn("Enter", dialog.findChild(type(dialog.auto_status), "denoise_dialog_hint").text())
        self.assertIs(self.widget.open_parameter_dialog("wavelet"), dialog)

    def test_single_parameter_dialog_is_more_compact_than_multi_parameter_dialog(self):
        self.widget.restore_state({"nodes": [
            {"id": "low-rank", "label": LOW_RANK_METHOD_NAME},
            {"id": "wavelet", "label": WAVELET_METHOD_NAME},
        ]})
        compact = self.widget.open_parameter_dialog("low-rank")
        expanded = self.widget.open_parameter_dialog("wavelet")
        self.app.processEvents()

        self.assertLess(len(compact._controls), len(expanded._controls))
        self.assertLess(compact.height(), expanded.height())

    def test_multi_parameter_dialog_keeps_last_field_and_footer_reachable(self):
        self.widget.restore_state({"nodes": [
            {"id": "wavelet", "label": WAVELET_METHOD_NAME},
        ]})
        dialog = self.widget.open_parameter_dialog("wavelet")
        self.app.processEvents()
        last_control = dialog._controls["noise_sigma"]
        dialog.scroll.verticalScrollBar().setValue(
            dialog.scroll.verticalScrollBar().maximum()
        )
        dialog.scroll.ensureWidgetVisible(last_control)
        self.app.processEvents()

        viewport = dialog.scroll.viewport()
        control_top = last_control.mapTo(viewport, QPoint(0, 0)).y()
        control_bottom = last_control.mapTo(
            viewport, QPoint(0, last_control.height())
        ).y()
        self.assertGreaterEqual(control_top, 0)
        self.assertLessEqual(control_bottom, viewport.height())

        hint = dialog.findChild(type(dialog.auto_status), "denoise_dialog_hint")
        for footer_widget in (dialog.auto_status, hint):
            self.assertIsNotNone(footer_widget)
            self.assertTrue(footer_widget.isVisible())
            top_left = footer_widget.mapTo(dialog, QPoint(0, 0))
            bottom_right = footer_widget.mapTo(
                dialog, QPoint(footer_widget.width(), footer_widget.height())
            )
            self.assertGreaterEqual(top_left.x(), 0)
            self.assertGreaterEqual(top_left.y(), 0)
            self.assertLessEqual(bottom_right.x(), dialog.width())
            self.assertLessEqual(bottom_right.y(), dialog.height())

    def test_state_round_trip_preserves_disabled_nodes_and_uses_deep_copies(self):
        source = {"nodes": [
            {
                "id": "gaussian-off",
                "label": GAUSSIAN_METHOD_NAME,
                "params": {"sigma_kx": 2.5},
                "enabled": False,
            },
            {
                "id": "sg-on",
                "label": SG_METHOD_NAME,
                "params": {"window_length": 9, "polyorder": 3, "smoothing_axis": "ky"},
                "enabled": True,
            },
        ]}
        edited = QSignalSpy(self.widget.pipeline_edited)
        committed = QSignalSpy(self.widget.pipeline_committed)
        self.widget.restore_state(source)
        saved = deepcopy(self.widget.export_state())

        source["nodes"][0]["params"]["sigma_kx"] = 19.0
        self.assertEqual(self.widget.export_state(), saved)
        self.assertEqual(len(edited), 0)
        self.assertEqual(len(committed), 0)
        self.assertFalse(saved["nodes"][0]["enabled"])
        self.assertEqual(
            [node["id"] for node in self.widget.get_denoise_settings()],
            ["sg-on"],
        )

        restored = self.make_widget()
        restored.restore_state(saved)
        self.assertEqual(restored.export_state(), saved)
        saved["nodes"][0]["params"]["sigma_kx"] = 12.0
        self.assertEqual(restored.export_state()["nodes"][0]["params"]["sigma_kx"], 2.5)

    def test_legacy_combo_aliases_and_legacy_controls_migrate_to_nodes(self):
        cases = (
            ("频域平滑", GAUSSIAN_METHOD_NAME, "gaussian", "sigma_kx", 3.5),
            ("滑动平均", MEDIAN_METHOD_NAME, "median", "size_ky", 7),
            ("Savitzky-Golay婊ゆ尝", SG_METHOD_NAME, "sg", "window_length", 9),
            ("灏忔尝鍘诲櫔", WAVELET_METHOD_NAME, "wavelet", "level", 4),
            ("贝叶斯去噪", NLM_METHOD_NAME, "nlmeans", "patch_distance", 8),
            (LOW_RANK_METHOD_NAME, LOW_RANK_METHOD_NAME, "low_rank", "rank", 6),
        )
        for alias, canonical, legacy_key, parameter, value in cases:
            with self.subTest(alias=alias):
                self.widget.restore_state({
                    "combo_n1": alias,
                    "combo_n2": "None",
                    "combo_n3": None,
                    "legacy_controls": {legacy_key: {parameter: value}},
                })
                settings = self.widget.get_denoise_settings()
                self.assertEqual(len(settings), 1)
                self.assertEqual(settings[0]["label"], canonical)
                self.assertEqual(settings[0]["params"][parameter], value)
                self.assertEqual(
                    settings[0]["params"],
                    {**default_method_params(canonical), parameter: value},
                )

    def test_unknown_legacy_algorithm_raises_clear_error_and_preserves_state(self):
        self.widget.restore_state({"nodes": [
            {"id": "valid", "label": MEDIAN_METHOD_NAME},
        ]})
        before = self.widget.export_state()
        for invalid in ("不存在的方法", "卡尔曼滤波", 42):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "第 1 个去噪节点无效"):
                    self.widget.restore_state({"combo_n1": invalid})
                self.assertEqual(self.widget.export_state(), before)

    def test_execution_states_update_indicators_dialog_and_inline_error(self):
        self.widget.restore_state({"nodes": [
            {"id": "gaussian", "label": GAUSSIAN_METHOD_NAME},
        ]})
        dialog = self.widget.open_parameter_dialog("gaussian")
        labels = {
            "idle": "修改后自动更新",
            "pending": "配置已更改 · 等待更新",
            "running": "正在计算 · 可继续编辑",
            "applied": "已应用当前流水线",
        }
        for state, text in labels.items():
            with self.subTest(state=state):
                self.widget.set_execution_state(state)
                self.app.processEvents()
                self.assertEqual(self.widget.status_indicator._state, state)
                self.assertEqual(self.widget.status_label._full_text, text)
                self.assertFalse(self.widget.error_panel.isVisible())
                row = next(
                    row for row in self.widget._node_rows()
                    if row.card.node_id == "gaussian"
                )
                self.assertEqual(row.rail.dot._state, state)

        message = "模拟失败：滑动窗口长度必须大于多项式阶数"
        self.widget.set_execution_state("error", message)
        self.app.processEvents()
        self.assertTrue(self.widget.error_panel.isVisible())
        self.assertEqual(self.widget.error_label.text(), message)
        self.assertEqual(self.widget.error_label.toolTip(), message)
        self.assertTrue(self.widget.error_label.wordWrap())
        self.assertEqual(dialog.auto_status.text(), message)
        self.assertEqual(len(self.widget.export_state()["nodes"]), 1)

        self.widget.set_execution_state("applied")
        self.app.processEvents()
        self.assertFalse(self.widget.error_panel.isVisible())
        with self.assertRaisesRegex(ValueError, "未知去噪执行状态"):
            self.widget.set_execution_state("finished")

    def test_method_popup_is_reachable_and_selects_method_through_qt(self):
        committed = QSignalSpy(self.widget.pipeline_committed)
        QTest.mouseClick(self.widget.add_button, Qt.LeftButton)
        self.app.processEvents()
        popup = self.widget.method_popup

        self.assertTrue(popup.isVisible())
        self.assertEqual(len(popup.choices), 6)
        self.assertEqual(
            [choice.name for choice in popup.choices],
            list(DENOISE_METHODS[1:]),
        )
        target = next(choice for choice in popup.choices if choice.name == SG_METHOD_NAME)
        QTest.mouseClick(target, Qt.LeftButton, pos=QPoint(4, 4))
        self.app.processEvents()

        self.assertFalse(popup.isVisible())
        self.assertEqual(
            [node["label"] for node in self.widget.get_denoise_settings()],
            [SG_METHOD_NAME],
        )
        self.assertEqual(len(committed), 1)

    def test_enabled_card_retains_parameter_affordance_without_fixed_hint(self):
        self.widget.restore_state({"nodes": [
            {"id": "gaussian", "label": GAUSSIAN_METHOD_NAME},
        ]})
        card = self.widget._node_cards["gaussian"]
        card.hint.hide()

        tooltip = card.toolTip()
        accessible_text = " ".join((card.accessibleName(), card.accessibleDescription()))
        discoverable_terms = ("参数", "调参", "编辑", "设置")
        self.assertTrue(any(term in tooltip for term in discoverable_terms))
        self.assertTrue(any(term in accessible_text for term in discoverable_terms))
        self.assertIn(GAUSSIAN_METHOD_NAME, accessible_text)

        QTest.keyClick(card, Qt.Key_Return)
        dialog = self.widget._parameter_dialogs.get("gaussian")
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.isVisible())

    def test_node_menu_exposes_structure_actions_and_move_commits_once(self):
        self.widget.restore_state({"nodes": [
            {"id": "first", "label": GAUSSIAN_METHOD_NAME},
            {"id": "second", "label": NLM_METHOD_NAME},
        ]})
        committed = QSignalSpy(self.widget.pipeline_committed)
        card = self.widget._node_cards["first"]

        menus = []

        def capture_menu(menu, *_args):
            menus.append(menu)
            return 0

        with patch.object(QMenu, "exec_", new=capture_menu):
            self.widget._show_node_menu("first", card.more)
        menu = menus[0]
        actions = {action.text(): action for action in menu.actions() if action.text()}
        self.assertEqual(set(actions), {
            "复制节点", "停用节点", "向前移动", "向后移动", "重置为默认参数", "移除节点",
        })
        self.assertFalse(actions["向前移动"].isEnabled())
        self.assertTrue(actions["向后移动"].isEnabled())

        actions["向后移动"].trigger()
        self.assertEqual(
            [node["id"] for node in self.widget.export_state()["nodes"]],
            ["second", "first"],
        )
        self.assertEqual(len(committed), 1)

    def test_light_dark_themes_and_narrow_wide_layouts_do_not_overflow(self):
        self.widget.restore_state({"nodes": [
            {"id": f"node-{index}", "label": name, "enabled": index != 1}
            for index, name in enumerate(DENOISE_METHODS[1:])
        ]})
        dialog = self.widget.open_parameter_dialog("node-0")
        previous_theme = theme.current_theme()
        try:
            for theme_name in ("light", "dark"):
                theme.set_theme(theme_name)
                self.app.processEvents()
                self.assertIn(theme.BG_2, self.widget.styleSheet())
                self.assertIn(theme.TEXT_1, self.widget.styleSheet())
                self.assertIn(theme.BG_2, dialog.styleSheet())
                self.assertIn(theme.TEXT_1, dialog.styleSheet())
                self.widget.method_popup.apply_theme()
                self.assertIn(theme.BG_3, self.widget.method_popup.styleSheet())
                self.assertIn(theme.TEXT_1, self.widget.method_popup.styleSheet())

                for width in (252, 470):
                    with self.subTest(theme=theme_name, width=width):
                        self.widget.resize(width, 800)
                        self.app.processEvents()
                        self.assertEqual(self.widget.width(), width)
                        self.assertLessEqual(
                            self.widget.layout().minimumSize().width(), width,
                        )
                        self.assertLessEqual(self.widget.flow_host.width(), width)
                        self.assertLessEqual(self.widget.add_button.width(), width)
                        for row in self.widget._node_rows():
                            self.assertLessEqual(row.width(), self.widget.flow_host.width())
                            self.assertLessEqual(row.card.width(), row.width())
                            self.assertLessEqual(
                                row.card.minimumSizeHint().width(), row.card.width(),
                            )
                            self.assertLessEqual(row.card.geometry().right(), row.width())
                            for child in (row.card.title, row.card.summary, row.card.more):
                                top_left = child.mapTo(row.card, QPoint(0, 0))
                                self.assertGreaterEqual(top_left.x(), 0)
                                self.assertLessEqual(
                                    top_left.x() + child.width(), row.card.width(),
                                )
                        image = self.widget.grab()
                        self.assertFalse(image.isNull())
                        self.assertGreaterEqual(image.width(), width)
        finally:
            theme.set_theme(previous_theme)

    def test_destroyed_pipeline_disconnects_its_theme_listener(self):
        widget = DenoisePipelineWidget()
        listener = widget._theme_listener
        listener_key = theme.changed._key(listener)

        def cleanup():
            theme.changed.disconnect(listener)
            if not sip.isdeleted(widget):
                widget.close()
                widget.deleteLater()
                QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

        self.addCleanup(cleanup)
        self.assertIn(listener_key, theme.changed._listeners)

        widget.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

        self.assertNotIn(listener_key, theme.changed._listeners)
        theme.changed.emit(theme.current_theme())


if __name__ == "__main__":
    unittest.main()
