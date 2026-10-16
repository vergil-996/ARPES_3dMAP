import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.updates.matelab_source import MatelabCredentials, MatelabReleaseClient
from bandscope.updates.sources import (
    SOURCE_GITHUB,
    SOURCE_MATELAB,
    SOURCE_SETTING,
    normalize_source,
)
from bandscope.updates.update_controller import (
    MATELAB_PASSWORD_SETTING,
    MATELAB_USERNAME_SETTING,
    UpdateController,
)
from bandscope.updates.update_service import GitHubReleaseClient


def _settings(values=None):
    stored = dict(values or {})
    settings = Mock()
    settings.value.side_effect = lambda key, default="", **kwargs: stored.get(key, default)
    settings.setValue.side_effect = lambda key, value: stored.__setitem__(key, value)
    settings.remove.side_effect = lambda key: stored.pop(key, None)
    return settings


class UpdateSourceSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _controller(self, values=None):
        window = QWidget()
        self.addCleanup(window.deleteLater)
        settings = _settings(values)
        controller = UpdateController(window, settings)
        controller._show_message = Mock(return_value=False)
        return controller, settings

    def test_default_source_is_github(self):
        controller, _ = self._controller()
        self.assertEqual(controller.current_source(), SOURCE_GITHUB)
        self.assertIsInstance(controller.client, GitHubReleaseClient)
        self.assertEqual(normalize_source("unknown"), SOURCE_GITHUB)

    def test_plugin_catalog_stays_on_github_after_switch(self):
        controller, _ = self._controller()
        controller._prompt_matelab_login = Mock(return_value=None)
        controller.set_source(SOURCE_MATELAB)
        self.assertIsInstance(controller.catalog_client.release_client, GitHubReleaseClient)

    def test_switch_to_matelab_persists_and_rebuilds_client(self):
        controller, settings = self._controller()
        controller._prompt_matelab_login = Mock(return_value=None)
        controller.set_source(SOURCE_MATELAB)
        settings.setValue.assert_any_call(SOURCE_SETTING, SOURCE_MATELAB)
        self.assertEqual(controller.current_source(), SOURCE_MATELAB)
        self.assertIsInstance(controller.client, MatelabReleaseClient)
        # 无凭证时切换会请求登录。
        controller._prompt_matelab_login.assert_called_once()

        controller.set_source(SOURCE_GITHUB)
        self.assertIsInstance(controller.client, GitHubReleaseClient)

    def test_credentials_round_trip(self):
        controller, settings = self._controller()
        credentials = MatelabCredentials(username="张三", password="p@ss word")
        controller._store_matelab_credentials(credentials)
        restored = controller._stored_matelab_credentials()
        self.assertEqual(restored, credentials)
        # 存储值不可读明文。
        stored = settings.value(MATELAB_PASSWORD_SETTING, "")
        self.assertNotIn("p@ss word", stored)
        self.assertEqual(settings.value(MATELAB_USERNAME_SETTING, ""), "张三")

    def test_automatic_matelab_check_without_credentials_skips_quietly(self):
        controller, _ = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        with patch(
            "bandscope.updates.update_controller.ReleaseCheckThread"
        ) as thread_cls:
            controller.check_for_updates(manual=False)
            thread_cls.assert_not_called()

    def test_manual_matelab_check_prompts_login_and_respects_cancel(self):
        controller, _ = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        controller._prompt_matelab_login = Mock(return_value=None)
        with patch(
            "bandscope.updates.update_controller.ReleaseCheckThread"
        ) as thread_cls:
            controller.check_for_updates(manual=True)
            controller._prompt_matelab_login.assert_called_once()
            thread_cls.assert_not_called()

    def test_manual_matelab_check_with_credentials_starts_thread(self):
        controller, _ = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        controller._store_matelab_credentials(
            MatelabCredentials(username="zhangsan", password="secret")
        )
        with patch(
            "bandscope.updates.update_controller.ReleaseCheckThread"
        ) as thread_cls:
            thread = thread_cls.return_value
            controller.check_for_updates(manual=True)
            thread_cls.assert_called_once()
            client = thread_cls.call_args[0][0]
            self.assertIsInstance(client, MatelabReleaseClient)
            thread.start.assert_called_once()

    def test_source_menu_marks_current_and_switches(self):
        controller, _ = self._controller()
        controller._prompt_matelab_login = Mock(return_value=None)
        menu_parent = QWidget()
        self.addCleanup(menu_parent.deleteLater)
        from PyQt5.QtWidgets import QMenu

        menu = QMenu(menu_parent)
        controller.add_source_menu(menu)
        submenu = menu.actions()[0].menu()
        actions = {a.text(): a for a in submenu.actions()}
        self.assertEqual(set(actions), {"GitHub（默认）", "MatElab 实验记录本（所内）"})
        self.assertTrue(actions["GitHub（默认）"].isChecked())
        self.assertFalse(actions["MatElab 实验记录本（所内）"].isChecked())

        actions["MatElab 实验记录本（所内）"].trigger()
        self.assertEqual(controller.current_source(), SOURCE_MATELAB)
        menu.deleteLater()

    def test_matelab_failure_hint_replaces_proxy_hint(self):
        controller, _ = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        controller._manual_check = False
        controller._on_check_failed("无法连接 MatElab 更新服务：timed out")
        args, _kwargs = controller._show_message.call_args
        self.assertIn("eln.iphy.ac.cn", args[1])
        self.assertNotIn("VPN", args[1])


if __name__ == "__main__":
    unittest.main()
