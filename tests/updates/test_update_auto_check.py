import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QWidget

from bandscope.app_metadata import APP_VERSION
from bandscope.updates.update_controller import (
    CHECK_INTERVAL,
    FAILURE_SNOOZE_INTERVAL,
    FAILURE_SNOOZE_SETTING,
    LAST_CHECK_SETTING,
    NEW_VERSION_SNOOZE_INTERVAL,
    NEW_VERSION_SNOOZE_SETTING,
    UpdateController,
)
from bandscope.updates.update_service import ReleaseInfo, SemanticVersion, UpdateCheckResult


def _iso(moment):
    return moment.isoformat().replace("+00:00", "Z")


def _in(delta):
    return _iso(datetime.now(timezone.utc) + delta)


def _settings(values=None):
    stored = dict(values or {})
    settings = Mock()
    settings.value.side_effect = lambda key, default="", **kwargs: stored.get(key, default)
    settings.setValue.side_effect = lambda key, value: stored.__setitem__(key, value)
    return settings


def _new_release(version="9.9.9"):
    release = ReleaseInfo(
        version=SemanticVersion.parse(version),
        tag_name=f"v{version}",
        title=f"BandScope {version}",
        notes="",
        page_url="",
        published_at="",
        assets=(),
    )
    return UpdateCheckResult(SemanticVersion.parse(APP_VERSION), release, None)


class AutomaticUpdateCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _controller(self, values=None):
        window = QWidget()
        self.addCleanup(window.deleteLater)
        settings = _settings(values)
        controller = UpdateController(window, settings)
        controller._show_message = Mock(return_value=False)
        controller._prompt_for_update = Mock()
        return controller, settings

    def test_background_check_interval_is_twelve_hours(self):
        self.assertEqual(CHECK_INTERVAL, timedelta(hours=12))

        controller, _ = self._controller({LAST_CHECK_SETTING: _in(timedelta(hours=-11))})
        self.assertFalse(controller.automatic_check_due())

        controller, _ = self._controller({LAST_CHECK_SETTING: _in(timedelta(hours=-13))})
        self.assertTrue(controller.automatic_check_due())

    def test_automatic_failure_is_reported_with_proxy_hint(self):
        controller, _ = self._controller()
        controller._manual_check = False

        controller._on_check_failed("无法连接 GitHub 更新服务：timed out")

        args, kwargs = controller._show_message.call_args
        self.assertIn("自动", args[0])
        self.assertIn("VPN", args[1])
        self.assertEqual(kwargs["extra_button"], "三天内不再提示")

    def test_failure_still_records_check_time(self):
        controller, settings = self._controller()
        controller._manual_check = False

        controller._on_check_failed("无法连接 GitHub 更新服务：timed out")

        settings.setValue.assert_called_once()
        key, timestamp = settings.setValue.call_args[0]
        self.assertEqual(key, LAST_CHECK_SETTING)
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        self.assertLess(abs((datetime.now(timezone.utc) - parsed).total_seconds()), 60)

    def test_clicking_snooze_mutes_failures_for_three_days(self):
        controller, settings = self._controller()
        controller._manual_check = False
        controller._show_message = Mock(return_value=True)

        controller._on_check_failed("无法连接 GitHub 更新服务：timed out")

        written = {call[0][0]: call[0][1] for call in settings.setValue.call_args_list}
        self.assertIn(LAST_CHECK_SETTING, written)
        deadline = datetime.fromisoformat(written[FAILURE_SNOOZE_SETTING].replace("Z", "+00:00"))
        self.assertAlmostEqual(
            (deadline - datetime.now(timezone.utc)).total_seconds(),
            FAILURE_SNOOZE_INTERVAL.total_seconds(),
            delta=60,
        )

    def test_active_failure_snooze_mutes_dialog_but_keeps_checking(self):
        controller, _ = self._controller(
            {
                FAILURE_SNOOZE_SETTING: _in(timedelta(days=2)),
                LAST_CHECK_SETTING: _in(timedelta(hours=-13)),
            }
        )
        controller._manual_check = False
        # Muting the dialog must not stop the 12-hour background check.
        self.assertTrue(controller.automatic_check_due())

        controller._on_check_failed("无法连接 GitHub 更新服务：timed out")

        controller._show_message.assert_not_called()

    def test_expired_failure_snooze_reports_again(self):
        controller, _ = self._controller(
            {FAILURE_SNOOZE_SETTING: _in(timedelta(minutes=-1))}
        )
        controller._manual_check = False

        controller._on_check_failed("无法连接 GitHub 更新服务：timed out")

        controller._show_message.assert_called_once()

    def test_manual_failure_ignores_failure_snooze(self):
        controller, _ = self._controller(
            {FAILURE_SNOOZE_SETTING: _in(timedelta(days=2))}
        )
        controller._manual_check = True

        controller._on_check_failed("无法连接 GitHub 更新服务：timed out")

        controller._show_message.assert_called_once()

    def test_new_version_snooze_suppresses_only_automatic_prompt(self):
        result = _new_release()
        controller, _ = self._controller(
            {NEW_VERSION_SNOOZE_SETTING: _in(timedelta(days=6))}
        )

        controller._manual_check = False
        controller._on_check_completed(result)
        controller._prompt_for_update.assert_not_called()

        controller._manual_check = True
        controller._on_check_completed(result)
        controller._prompt_for_update.assert_called_once()

    def test_new_version_alert_is_shown_when_not_snoozed(self):
        self.assertEqual(NEW_VERSION_SNOOZE_INTERVAL, timedelta(days=7))

        controller, _ = self._controller()
        controller._manual_check = False

        controller._on_check_completed(_new_release())

        controller._prompt_for_update.assert_called_once()


if __name__ == "__main__":
    unittest.main()
