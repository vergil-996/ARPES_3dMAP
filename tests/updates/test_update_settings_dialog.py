import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QLineEdit

from bandscope.updates.matelab_source import MatelabCredentials
from bandscope.updates.sources import SOURCE_GITHUB, SOURCE_MATELAB
from bandscope.updates.update_settings_dialog import (
    MatelabLoginDialog,
    UpdateSettingsDialog,
)


class UpdateSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, **kwargs):
        dialog = UpdateSettingsDialog(**kwargs)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_saved_account_is_displayed_without_exposing_password(self):
        credentials = MatelabCredentials("张三", "saved-password")
        dialog = self._dialog(source=SOURCE_MATELAB, credentials=credentials)

        self.assertEqual(dialog.selected_source(), SOURCE_MATELAB)
        self.assertEqual(dialog.credentials(), credentials)
        self.assertFalse(dialog.account_group.isHidden())
        self.assertIn(credentials.username, dialog.account_label.text())
        self.assertNotIn(credentials.password, dialog.account_label.text())

    def test_switching_sources_hides_account_and_keeps_credentials_draft(self):
        credentials = MatelabCredentials("user", "password")
        dialog = self._dialog(source=SOURCE_MATELAB, credentials=credentials)

        dialog.source_combo.setCurrentIndex(dialog.source_combo.findData(SOURCE_GITHUB))

        self.assertEqual(dialog.selected_source(), SOURCE_GITHUB)
        self.assertTrue(dialog.account_group.isHidden())
        self.assertEqual(dialog.credentials(), credentials)

        dialog.source_combo.setCurrentIndex(dialog.source_combo.findData(SOURCE_MATELAB))

        self.assertFalse(dialog.account_group.isHidden())
        self.assertEqual(dialog.credentials(), credentials)

    def test_clear_account_changes_only_draft(self):
        credentials = MatelabCredentials("user", "password")
        dialog = self._dialog(source=SOURCE_MATELAB, credentials=credentials)

        dialog.clear_account_button.click()

        self.assertIsNone(dialog.credentials())
        self.assertNotIn(credentials.username, dialog.account_label.text())
        self.assertEqual(credentials, MatelabCredentials("user", "password"))
        self.assertEqual(dialog.selected_source(), SOURCE_MATELAB)

    def test_edit_account_prefills_username_but_never_password(self):
        original = MatelabCredentials("old-user", "old-password")
        updated = MatelabCredentials("new-user", "new-password")
        dialog = self._dialog(source=SOURCE_MATELAB, credentials=original)

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
            dialog.edit_account_button.click()

        self.assertEqual(dialog.credentials(), updated)
        self.assertIn(updated.username, dialog.account_label.text())
        self.assertEqual(original.password, "old-password")

    def test_cancelled_account_edit_keeps_draft(self):
        credentials = MatelabCredentials("user", "password")
        dialog = self._dialog(source=SOURCE_MATELAB, credentials=credentials)

        with patch(
            "bandscope.updates.update_settings_dialog.MatelabLoginDialog"
        ) as login_cls:
            login_cls.return_value.exec_.return_value = QDialog.Rejected
            login_cls.return_value.credentials.return_value = MatelabCredentials("other", "new")
            dialog._edit_account()

        self.assertEqual(dialog.credentials(), credentials)

    def test_outer_cancel_does_not_change_original_credentials(self):
        credentials = MatelabCredentials("user", "password")
        dialog = self._dialog(source=SOURCE_MATELAB, credentials=credentials)

        dialog._clear_account()
        dialog.reject()

        self.assertEqual(dialog.result(), QDialog.Rejected)
        self.assertEqual(credentials, MatelabCredentials("user", "password"))


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
