import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QThread
from PyQt5.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMenu, QWidget

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
from bandscope.updates.update_settings_dialog import UpdateSettingsPanel


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
        settings_dialog = QWidget(window)
        settings_dialog.select_page = Mock()
        settings_dialog.exec_ = Mock()
        window.open_settings = Mock(return_value=settings_dialog)
        settings = _settings(values)
        controller = UpdateController(window, settings)
        controller._show_message = Mock(return_value=False)
        return controller, settings

    def _panel(self, controller):
        panel = controller.create_settings_panel()
        self.addCleanup(panel.deleteLater)
        return panel

    def _save(self, panel):
        panel.buttons.button(QDialogButtonBox.Save).click()

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

    def test_saved_settings_can_replace_account_without_switching_source(self):
        controller, settings = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        old_credentials = MatelabCredentials("old-user", "old-password")
        new_credentials = MatelabCredentials("new-user", "new-password")
        controller._store_matelab_credentials(old_credentials)
        old_client = controller.client
        self._seed_previous_result(controller)
        panel = self._panel(controller)
        self.assertEqual(panel.credentials(), old_credentials)

        with patch("bandscope.updates.update_settings_dialog.MatelabLoginDialog") as login_cls:
            login_cls.return_value.exec_.return_value = QDialog.Accepted
            login_cls.return_value.credentials.return_value = new_credentials
            panel.edit_account_button.click()
            self._save(panel)

        self.assertEqual(controller.current_source(), SOURCE_MATELAB)
        self.assertEqual(controller._stored_matelab_credentials(), new_credentials)
        self.assertIsInstance(controller.client, MatelabReleaseClient)
        self.assertIsNot(controller.client, old_client)
        self._assert_previous_result_cleared(controller)
        self.assertEqual(settings.value(MATELAB_USERNAME_SETTING), "new-user")

    def test_saved_settings_clear_saved_account(self):
        controller, settings = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        controller._store_matelab_credentials(MatelabCredentials("user", "password"))
        self._seed_previous_result(controller)
        panel = self._panel(controller)

        panel.clear_account_button.click()
        self._save(panel)

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
        panel = self._panel(controller)
        panel.source_combo.setCurrentIndex(panel.source_combo.findData(SOURCE_GITHUB))
        panel.clear_account_button.click()
        settings.reset_mock()

        panel.buttons.button(QDialogButtonBox.Cancel).click()

        settings.setValue.assert_not_called()
        settings.remove.assert_not_called()
        self.assertEqual(controller.current_source(), SOURCE_MATELAB)
        self.assertEqual(controller._stored_matelab_credentials(), credentials)
        self.assertEqual(panel.selected_source(), SOURCE_MATELAB)
        self.assertEqual(panel.credentials(), credentials)
        self.assertIs(controller.client, old_client)
        self.assertIs(controller._active_release, previous_release)

    def test_settings_switch_to_github_keeps_saved_matelab_account(self):
        controller, _ = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        credentials = MatelabCredentials("user", "password")
        controller._store_matelab_credentials(credentials)
        panel = self._panel(controller)

        panel.source_combo.setCurrentIndex(panel.source_combo.findData(SOURCE_GITHUB))
        self._save(panel)

        self.assertEqual(controller.current_source(), SOURCE_GITHUB)
        self.assertIsInstance(controller.client, GitHubReleaseClient)
        self.assertEqual(controller._stored_matelab_credentials(), credentials)

    def test_settings_can_save_matelab_without_account_and_prompt_on_manual_check(self):
        controller, _ = self._controller()
        controller._prompt_matelab_login = Mock(return_value=None)
        panel = self._panel(controller)
        panel.source_combo.setCurrentIndex(panel.source_combo.findData(SOURCE_MATELAB))

        self._save(panel)

        self.assertEqual(controller.current_source(), SOURCE_MATELAB)
        controller._prompt_matelab_login.assert_not_called()
        with patch("bandscope.updates.update_controller.ReleaseCheckThread") as thread_cls:
            panel.check_updates_button.click()
            controller._prompt_matelab_login.assert_called_once()
            thread_cls.assert_not_called()

    def test_check_updates_from_settings_saves_then_starts_without_closing(self):
        controller, _settings = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        controller._store_matelab_credentials(MatelabCredentials("user", "password"))
        panel = self._panel(controller)
        panel.show()
        controller.check_for_updates = Mock(
            side_effect=lambda manual: self.assertTrue(panel.isVisible())
        )

        panel.clear_account_button.click()
        panel.check_updates_button.click()

        self.assertIsNone(controller._stored_matelab_credentials())
        controller.check_for_updates.assert_called_once_with(manual=True)
        self.assertTrue(panel.isVisible())
        panel.cancel()
        self.assertIsNone(panel.credentials())

    def test_open_settings_selects_embedded_page_and_returns_same_panel(self):
        controller, _ = self._controller()
        panel = self._panel(controller)

        returned = controller.open_settings()

        self.assertIs(returned, panel)
        controller.window.open_settings.assert_called_once_with()
        controller.window.open_settings.return_value.select_page.assert_called_once_with("updates")
        controller.window.open_settings.return_value.exec_.assert_not_called()
        self.assertTrue(panel.is_active())
        self.assertFalse(hasattr(controller, "_settings_dialog"))

    def test_busy_update_opens_read_only_panel_and_blocks_source_changes(self):
        for thread_attribute in ("_check_thread", "_download_thread"):
            for running in (True, False):
                with self.subTest(thread=thread_attribute, running=running):
                    controller, settings = self._controller()
                    setattr(controller, thread_attribute, Mock(isRunning=Mock(return_value=running)))
                    old_client = controller.client

                    panel = controller.open_settings()
                    settings.reset_mock()
                    controller._save_settings_panel(panel)
                    controller.set_source(SOURCE_MATELAB)

                    self.assertIsInstance(panel, UpdateSettingsPanel)
                    self.assertTrue(panel.is_busy())
                    self.assertFalse(panel.source_combo.isEnabled())
                    self.assertFalse(panel.account_group.isEnabled())
                    self.assertFalse(panel.buttons.button(QDialogButtonBox.Save).isEnabled())
                    self.assertFalse(panel.check_updates_button.isEnabled())
                    settings.setValue.assert_not_called()
                    settings.remove.assert_not_called()
                    self.assertEqual(controller.current_source(), SOURCE_GITHUB)
                    self.assertIs(controller.client, old_client)
                    controller._show_message.assert_called()

    def test_thread_finished_restores_panel_controls(self):
        for thread_attribute, finished in (
            ("_check_thread", "_on_check_thread_finished"),
            ("_download_thread", "_on_download_thread_finished"),
        ):
            with self.subTest(thread=thread_attribute):
                controller, _ = self._controller()
                panel = self._panel(controller)
                thread = Mock()
                setattr(controller, thread_attribute, thread)
                controller._sync_settings_panel_busy()
                self.assertTrue(panel.is_busy())

                with patch.object(controller, "sender", return_value=thread):
                    getattr(controller, finished)()

                self.assertIsNone(getattr(controller, thread_attribute))
                self.assertFalse(panel.is_busy())
                self.assertTrue(panel.source_combo.isEnabled())
                self.assertTrue(panel.buttons.button(QDialogButtonBox.Save).isEnabled())
                self.assertTrue(panel.check_updates_button.isEnabled())
                thread.deleteLater.assert_called_once_with()

    def test_finished_thread_reference_blocks_another_check(self):
        for thread_attribute in ("_check_thread", "_download_thread"):
            for manual in (True, False):
                with self.subTest(thread=thread_attribute, manual=manual):
                    controller, _ = self._controller()
                    old_thread = QThread(controller)
                    self.assertFalse(old_thread.isRunning())
                    setattr(controller, thread_attribute, old_thread)

                    with patch("bandscope.updates.update_controller.ReleaseCheckThread") as thread_cls:
                        controller.check_for_updates(manual=manual)

                    thread_cls.assert_not_called()
                    self.assertIs(getattr(controller, thread_attribute), old_thread)
                    if manual:
                        controller._show_message.assert_called_once()
                    else:
                        controller._show_message.assert_not_called()

    def test_stale_finished_signal_does_not_clear_new_thread(self):
        for thread_attribute, finished in (
            ("_check_thread", "_on_check_thread_finished"),
            ("_download_thread", "_on_download_thread_finished"),
        ):
            with self.subTest(thread=thread_attribute):
                controller, _ = self._controller()
                panel = self._panel(controller)
                old_thread = QThread(controller)
                old_thread.finished.connect(getattr(controller, finished))
                new_thread = QThread(controller)
                setattr(controller, thread_attribute, new_thread)
                controller._sync_settings_panel_busy()
                self.assertTrue(panel.is_busy())

                old_thread.finished.emit()

                self.assertIs(getattr(controller, thread_attribute), new_thread)
                self.assertTrue(panel.is_busy())

    def test_automatic_check_pauses_only_while_settings_panel_is_active(self):
        controller, _ = self._controller()
        panel = self._panel(controller)
        panel.set_active(True)

        with patch("bandscope.updates.update_controller.ReleaseCheckThread") as thread_cls:
            controller.check_automatically()
            thread_cls.assert_not_called()

            controller.check_for_updates(manual=True)
            thread_cls.assert_called_once()
            thread_cls.return_value.start.assert_called_once()

        controller, _ = self._controller()
        panel = self._panel(controller)
        panel.set_active(False)
        with patch("bandscope.updates.update_controller.ReleaseCheckThread") as thread_cls:
            controller.check_automatically()
            thread_cls.assert_called_once()

    def test_credentials_round_trip(self):
        controller, settings = self._controller()
        credentials = MatelabCredentials(username="张三", password="p@ss word")
        controller._store_matelab_credentials(credentials)
        restored = controller._stored_matelab_credentials()
        self.assertEqual(restored, credentials)
        stored = settings.value(MATELAB_PASSWORD_SETTING, "")
        self.assertNotIn("p@ss word", stored)
        self.assertEqual(settings.value(MATELAB_USERNAME_SETTING, ""), "张三")

    def test_automatic_matelab_check_without_credentials_skips_quietly(self):
        controller, _ = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        with patch("bandscope.updates.update_controller.ReleaseCheckThread") as thread_cls:
            controller.check_for_updates(manual=False)
            thread_cls.assert_not_called()

    def test_manual_matelab_check_prompts_login_and_respects_cancel(self):
        controller, _ = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        controller._prompt_matelab_login = Mock(return_value=None)
        with patch("bandscope.updates.update_controller.ReleaseCheckThread") as thread_cls:
            controller.check_for_updates(manual=True)
            controller._prompt_matelab_login.assert_called_once()
            thread_cls.assert_not_called()

    def test_manual_matelab_check_with_credentials_starts_thread(self):
        controller, _ = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        controller._store_matelab_credentials(
            MatelabCredentials(username="zhangsan", password="secret")
        )
        with patch("bandscope.updates.update_controller.ReleaseCheckThread") as thread_cls:
            thread = thread_cls.return_value
            controller.check_for_updates(manual=True)
            thread_cls.assert_called_once()
            client = thread_cls.call_args[0][0]
            self.assertIsInstance(client, MatelabReleaseClient)
            thread.start.assert_called_once()

    def test_successful_manual_login_updates_panel_and_saved_baseline(self):
        controller, settings = self._controller({SOURCE_SETTING: SOURCE_MATELAB})
        panel = self._panel(controller)
        credentials = MatelabCredentials("new-user", "new-password")

        with patch("bandscope.updates.update_controller.MatelabLoginDialog") as login_cls, patch(
            "bandscope.updates.update_controller.ReleaseCheckThread"
        ) as thread_cls:
            login_cls.return_value.exec_.return_value = QDialog.Accepted
            login_cls.return_value.credentials.return_value = credentials
            panel.check_updates_button.click()

        self.assertEqual(panel.credentials(), credentials)
        self.assertEqual(controller._stored_matelab_credentials(), credentials)
        self.assertEqual(settings.value(MATELAB_USERNAME_SETTING), "new-user")
        thread_cls.return_value.start.assert_called_once()
        panel.clear_account_button.click()
        panel.cancel()
        self.assertEqual(panel.credentials(), credentials)

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
