import os
import unittest
from unittest.mock import Mock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QWidget,
)

from bandscope.updates.matelab_source import MatelabCredentials
from bandscope.updates.sources import SOURCE_GITHUB, SOURCE_MATELAB
from bandscope.updates.update_settings_dialog import (
    MatelabLoginDialog,
    UpdateSettingsPanel,
)


class UpdateSettingsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _panel(self, **kwargs):
        panel = UpdateSettingsPanel(**kwargs)
        self.addCleanup(panel.deleteLater)
        return panel

    def test_panel_is_a_widget_without_dialog_result(self):
        panel = self._panel()

        self.assertIsInstance(panel, QWidget)
        self.assertFalse(hasattr(panel, "exec_"))
        self.assertFalse(panel.is_active())
        self.assertFalse(panel.is_busy())

    def test_saved_account_is_displayed_without_exposing_password(self):
        credentials = MatelabCredentials("张三", "saved-password")
        panel = self._panel(source=SOURCE_MATELAB, credentials=credentials)

        self.assertEqual(panel.selected_source(), SOURCE_MATELAB)
        self.assertEqual(panel.credentials(), credentials)
        self.assertFalse(panel.account_group.isHidden())
        self.assertIn(credentials.username, panel.account_label.text())
        self.assertNotIn(credentials.password, panel.account_label.text())

    def test_switching_sources_hides_account_and_keeps_credentials_draft(self):
        credentials = MatelabCredentials("user", "password")
        panel = self._panel(source=SOURCE_MATELAB, credentials=credentials)

        panel.source_combo.setCurrentIndex(panel.source_combo.findData(SOURCE_GITHUB))

        self.assertEqual(panel.selected_source(), SOURCE_GITHUB)
        self.assertTrue(panel.account_group.isHidden())
        self.assertEqual(panel.credentials(), credentials)

        panel.source_combo.setCurrentIndex(panel.source_combo.findData(SOURCE_MATELAB))

        self.assertFalse(panel.account_group.isHidden())
        self.assertEqual(panel.credentials(), credentials)

    def test_clear_account_changes_only_draft(self):
        credentials = MatelabCredentials("user", "password")
        panel = self._panel(source=SOURCE_MATELAB, credentials=credentials)

        panel.clear_account_button.click()

        self.assertIsNone(panel.credentials())
        self.assertNotIn(credentials.username, panel.account_label.text())
        self.assertEqual(credentials, MatelabCredentials("user", "password"))
        self.assertEqual(panel.selected_source(), SOURCE_MATELAB)

    def test_edit_account_prefills_username_but_never_password(self):
        original = MatelabCredentials("old-user", "old-password")
        updated = MatelabCredentials("new-user", "new-password")
        panel = self._panel(source=SOURCE_MATELAB, credentials=original)

        def login_dialog(*args, **kwargs):
            login = MatelabLoginDialog(*args, **kwargs)
            self.addCleanup(login.deleteLater)
            self.assertEqual(login.username_edit.text(), original.username)
            self.assertEqual(login.password_edit.text(), "")
            login.username_edit.setText(updated.username)
            login.password_edit.setText(updated.password)
            login.exec_ = Mock(return_value=QDialog.Accepted)
            return login

        with patch(
            "bandscope.updates.update_settings_dialog.MatelabLoginDialog",
            side_effect=login_dialog,
        ):
            panel.edit_account_button.click()

        self.assertEqual(panel.credentials(), updated)
        self.assertIn(updated.username, panel.account_label.text())
        self.assertEqual(original.password, "old-password")

    def test_cancelled_account_edit_keeps_draft(self):
        credentials = MatelabCredentials("user", "password")
        panel = self._panel(source=SOURCE_MATELAB, credentials=credentials)

        with patch(
            "bandscope.updates.update_settings_dialog.MatelabLoginDialog"
        ) as login_cls:
            login_cls.return_value.exec_.return_value = QDialog.Rejected
            login_cls.return_value.credentials.return_value = MatelabCredentials("other", "new")
            panel._edit_account()

        self.assertEqual(panel.credentials(), credentials)

    def test_save_request_stays_open_and_does_not_advance_baseline_itself(self):
        credentials = MatelabCredentials("user", "password")
        panel = self._panel(source=SOURCE_MATELAB, credentials=credentials)
        save_requested = Mock()
        panel.save_requested.connect(save_requested)
        panel.show()
        panel._clear_account()

        panel.buttons.button(QDialogButtonBox.Save).click()

        save_requested.assert_called_once_with()
        self.assertTrue(panel.isVisible())
        self.assertIsNone(panel.credentials())
        panel.cancel()
        self.assertEqual(panel.credentials(), credentials)
        self.assertTrue(panel.isVisible())

    def test_mark_saved_advances_the_cancel_baseline(self):
        credentials = MatelabCredentials("user", "password")
        panel = self._panel(source=SOURCE_MATELAB, credentials=credentials)
        panel.source_combo.setCurrentIndex(panel.source_combo.findData(SOURCE_GITHUB))
        panel._clear_account()

        panel.mark_saved()
        panel.source_combo.setCurrentIndex(panel.source_combo.findData(SOURCE_MATELAB))
        panel.cancel()

        self.assertEqual(panel.selected_source(), SOURCE_GITHUB)
        self.assertIsNone(panel.credentials())

    def test_check_updates_requests_check_without_closing(self):
        credentials = MatelabCredentials("user", "password")
        panel = self._panel(source=SOURCE_MATELAB, credentials=credentials)
        check_requested = Mock()
        panel.check_requested.connect(check_requested)
        panel.show()

        panel.check_updates_button.click()

        check_requested.assert_called_once_with()
        self.assertTrue(panel.isVisible())
        self.assertEqual(panel.selected_source(), SOURCE_MATELAB)
        self.assertEqual(panel.credentials(), credentials)

    def test_cancel_restores_saved_source_and_credentials(self):
        credentials = MatelabCredentials("user", "password")
        panel = self._panel(source=SOURCE_MATELAB, credentials=credentials)
        panel.source_combo.setCurrentIndex(panel.source_combo.findData(SOURCE_GITHUB))
        panel._clear_account()

        panel.buttons.button(QDialogButtonBox.Cancel).click()

        self.assertEqual(panel.selected_source(), SOURCE_MATELAB)
        self.assertEqual(panel.credentials(), credentials)

    def test_discard_and_deactivate_restores_saved_draft_and_reports_inactive(self):
        credentials = MatelabCredentials("user", "password")
        panel = self._panel(source=SOURCE_MATELAB, credentials=credentials)
        active_changed = Mock()
        panel.active_changed.connect(active_changed)
        panel.set_active(True)
        panel._clear_account()

        panel.discard_and_deactivate()

        self.assertEqual(panel.credentials(), credentials)
        self.assertFalse(panel.is_active())
        self.assertEqual(active_changed.call_args_list, [call(True), call(False)])

    def test_busy_disables_all_editing_and_check_controls_until_cleared(self):
        credentials = MatelabCredentials("user", "password")
        panel = self._panel(source=SOURCE_MATELAB, credentials=credentials)
        save_requested = Mock()
        check_requested = Mock()
        panel.save_requested.connect(save_requested)
        panel.check_requested.connect(check_requested)

        panel.set_busy(True)

        self.assertTrue(panel.is_busy())
        self.assertFalse(panel.source_combo.isEnabled())
        self.assertFalse(panel.account_group.isEnabled())
        self.assertFalse(panel.edit_account_button.isEnabled())
        self.assertFalse(panel.clear_account_button.isEnabled())
        self.assertFalse(panel.buttons.button(QDialogButtonBox.Save).isEnabled())
        self.assertFalse(panel.check_updates_button.isEnabled())
        self.assertTrue(panel.buttons.button(QDialogButtonBox.Cancel).isEnabled())
        panel.request_save()
        panel.request_update_check()
        save_requested.assert_not_called()
        check_requested.assert_not_called()

        panel.set_busy(False)

        self.assertTrue(panel.source_combo.isEnabled())
        self.assertTrue(panel.account_group.isEnabled())
        self.assertTrue(panel.edit_account_button.isEnabled())
        self.assertTrue(panel.clear_account_button.isEnabled())
        self.assertTrue(panel.buttons.button(QDialogButtonBox.Save).isEnabled())
        self.assertTrue(panel.check_updates_button.isEnabled())


class MatelabLoginDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, username=""):
        dialog = MatelabLoginDialog(username=username)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_password_starts_empty_and_uses_password_echo_mode(self):
        dialog = self._dialog(username="saved-user")

        self.assertEqual(dialog.username_edit.text(), "saved-user")
        self.assertEqual(dialog.password_edit.text(), "")
        self.assertEqual(dialog.password_edit.echoMode(), QLineEdit.Password)

    def test_empty_login_stays_open_and_shows_inline_validation(self):
        for username, password in (("", "secret"), ("user", ""), ("   ", "secret")):
            with self.subTest(username=username, password=password):
                dialog = self._dialog(username=username)
                dialog.password_edit.setText(password)
                dialog.show()

                dialog.accept()

                self.assertNotEqual(dialog.result(), QDialog.Accepted)
                self.assertTrue(dialog.isVisible())
                self.assertIsNone(dialog.credentials())
                hints = [label for label in dialog.findChildren(QLabel) if label.isVisible()]
                self.assertTrue(any("不能为空" in label.text() for label in hints))
                dialog.reject()

    def test_valid_login_accepts_and_trims_username(self):
        dialog = self._dialog(username="  user  ")
        dialog.password_edit.setText("secret")

        dialog.accept()

        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(dialog.credentials(), MatelabCredentials("user", "secret"))


if __name__ == "__main__":
    unittest.main()
