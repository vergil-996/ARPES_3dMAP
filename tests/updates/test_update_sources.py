import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QDialog, QMenu, QWidget

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

    def _seed_previous_result(self, controller):
        controller._active_release = object()
        controller._plugin_catalog = object()
        controller._plugin_catalog_error = "previous error"
        controller._plugin_impact = object()

    def _assert_previous_result_cleared(self, controller):
        self.assertIsNone(controller._active_release)
        self.assertIsNone(controller._plugin_catalog)
        self.assertEqual(controller._plugin_catalog_error, "")
        self.assertIsNone(controller._plugin_impact)

    def test_switch_source_invalidates_previous_update_result(self):
        controller, _ = self._controller()
        self._seed_previous_result(controller)
        controller._prompt_matelab_login = Mock(return_value=None)

        controller.set_source(SOURCE_MATELAB)

        self._assert_previous_result_cleared(controller)

    def test_accepted_settings_can_replace_account_without_switching_source(self):
        controller, settings = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        old_credentials = MatelabCredentials("old-user", "old-password")
        new_credentials = MatelabCredentials("new-user", "new-password")
        controller._store_matelab_credentials(old_credentials)
        old_client = controller.client
        self._seed_previous_result(controller)

        with patch("bandscope.updates.update_controller.UpdateSettingsDialog") as dialog_cls:
            dialog = dialog_cls.return_value
            dialog.exec_.return_value = QDialog.Accepted
            dialog.selected_source.return_value = SOURCE_MATELAB
            dialog.credentials.return_value = new_credentials

            controller.open_settings()

        self.assertEqual(controller.current_source(), SOURCE_MATELAB)
        self.assertEqual(controller._stored_matelab_credentials(), new_credentials)
        self.assertIsInstance(controller.client, MatelabReleaseClient)
        self.assertIsNot(controller.client, old_client)
        self._assert_previous_result_cleared(controller)
        self.assertEqual(dialog_cls.call_args.kwargs["credentials"], old_credentials)
        self.assertEqual(settings.value(MATELAB_USERNAME_SETTING), "new-user")

    def test_accepted_settings_clear_saved_account(self):
        controller, settings = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        controller._store_matelab_credentials(MatelabCredentials("user", "password"))
        self._seed_previous_result(controller)

        with patch("bandscope.updates.update_controller.UpdateSettingsDialog") as dialog_cls:
            dialog = dialog_cls.return_value
            dialog.exec_.return_value = QDialog.Accepted
            dialog.selected_source.return_value = SOURCE_MATELAB
            dialog.credentials.return_value = None

            controller.open_settings()

        self.assertIsNone(controller._stored_matelab_credentials())
        self.assertEqual(settings.value(MATELAB_USERNAME_SETTING, ""), "")
        self.assertEqual(settings.value(MATELAB_PASSWORD_SETTING, ""), "")
        self._assert_previous_result_cleared(controller)

    def test_cancelled_settings_preserve_saved_source_account_and_result(self):
        controller, settings = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        credentials = MatelabCredentials("user", "password")
        controller._store_matelab_credentials(credentials)
        old_client = controller.client
        self._seed_previous_result(controller)
        previous_release = controller._active_release
        settings.reset_mock()

        with patch("bandscope.updates.update_controller.UpdateSettingsDialog") as dialog_cls:
            dialog = dialog_cls.return_value
            dialog.exec_.return_value = QDialog.Rejected
            dialog.selected_source.return_value = SOURCE_GITHUB
            dialog.credentials.return_value = None

            controller.open_settings()

        settings.setValue.assert_not_called()
        settings.remove.assert_not_called()
        self.assertEqual(controller.current_source(), SOURCE_MATELAB)
        self.assertEqual(controller._stored_matelab_credentials(), credentials)
        self.assertIs(controller.client, old_client)
        self.assertIs(controller._active_release, previous_release)

    def test_settings_switch_to_github_keeps_saved_matelab_account(self):
        controller, _ = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        credentials = MatelabCredentials("user", "password")
        controller._store_matelab_credentials(credentials)

        with patch("bandscope.updates.update_controller.UpdateSettingsDialog") as dialog_cls:
            dialog = dialog_cls.return_value
            dialog.exec_.return_value = QDialog.Accepted
            dialog.selected_source.return_value = SOURCE_GITHUB
            dialog.credentials.return_value = credentials

            controller.open_settings()

        self.assertEqual(controller.current_source(), SOURCE_GITHUB)
        self.assertIsInstance(controller.client, GitHubReleaseClient)
        self.assertEqual(controller._stored_matelab_credentials(), credentials)

    def test_settings_can_save_matelab_without_account_and_prompt_on_manual_check(self):
        controller, _ = self._controller()
        controller._prompt_matelab_login = Mock(return_value=None)

        with patch("bandscope.updates.update_controller.UpdateSettingsDialog") as dialog_cls:
            dialog = dialog_cls.return_value
            dialog.exec_.return_value = QDialog.Accepted
            dialog.selected_source.return_value = SOURCE_MATELAB
            dialog.credentials.return_value = None

            controller.open_settings()

        self.assertEqual(controller.current_source(), SOURCE_MATELAB)
        controller._prompt_matelab_login.assert_not_called()
        with patch("bandscope.updates.update_controller.ReleaseCheckThread") as thread_cls:
            controller.check_for_updates(manual=True)
            controller._prompt_matelab_login.assert_called_once()
            thread_cls.assert_not_called()

    def test_busy_update_blocks_settings_and_source_changes(self):
        for thread_attribute in ("_check_thread", "_download_thread"):
            for running in (True, False):
                # run() 已结束但 finished 尚未清理的引用，也必须阻止修改。
                with self.subTest(thread=thread_attribute, running=running):
                    controller, settings = self._controller()
                    setattr(controller, thread_attribute, Mock(isRunning=Mock(return_value=running)))
                    old_client = controller.client
                    settings.reset_mock()

                    with patch("bandscope.updates.update_controller.UpdateSettingsDialog") as dialog_cls:
                        controller.open_settings()
                        dialog_cls.assert_not_called()
                    controller.set_source(SOURCE_MATELAB)

                    settings.setValue.assert_not_called()
                    settings.remove.assert_not_called()
                    self.assertEqual(controller.current_source(), SOURCE_GITHUB)
                    self.assertIs(controller.client, old_client)
                    controller._show_message.assert_called()

    def test_automatic_check_waits_until_settings_dialog_is_closed(self):
        controller, _ = self._controller()

        with patch("bandscope.updates.update_controller.UpdateSettingsDialog") as dialog_cls, patch(
            "bandscope.updates.update_controller.ReleaseCheckThread"
        ) as thread_cls:
            def edit_settings():
                # 模态对话框仍会派发启动时的自动检查计时器事件。
                controller.check_automatically()
                thread_cls.assert_not_called()
                return QDialog.Rejected

            dialog_cls.return_value.exec_.side_effect = edit_settings
            controller.open_settings()

            controller.check_automatically()

            thread_cls.assert_called_once()
            thread_cls.return_value.start.assert_called_once()

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

    def test_settings_menu_action_opens_settings(self):
        controller, _ = self._controller()
        controller.open_settings = Mock()
        menu_parent = QWidget()
        self.addCleanup(menu_parent.deleteLater)
        menu = QMenu(menu_parent)
        self.addCleanup(menu.deleteLater)

        action = controller.add_settings_action(menu)

        self.assertEqual(action.text(), "更新设置…")
        self.assertIn(action, menu.actions())
        action.trigger()
        controller.open_settings.assert_called_once()

    def test_matelab_failure_hint_replaces_proxy_hint(self):
        controller, _ = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        controller._manual_check = False
        controller._on_check_failed("无法连接 MatElab 更新服务：timed out")
        args, _kwargs = controller._show_message.call_args
        self.assertIn("eln.iphy.ac.cn", args[1])
        self.assertNotIn("VPN", args[1])


if __name__ == "__main__":
    unittest.main()
