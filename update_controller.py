"""Qt-facing update flow for BandScope."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PyQt5.QtCore import QObject, QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QApplication, QMessageBox, QProgressDialog

from app_metadata import (
    APP_NAME,
    APP_VERSION,
    GITHUB_RELEASES_URL,
    TRUSTED_SIGNER_CERT_SHA256,
    current_build_flavor,
    is_frozen_build,
)
from update_service import (
    GitHubReleaseClient,
    InstallerVerification,
    UpdateCheckResult,
    UpdateError,
    cleanup_update_cache,
    launch_installer,
    verify_installer,
)


CHECK_INTERVAL = timedelta(days=1)
LAST_CHECK_SETTING = "updates/last_check_utc"
SKIPPED_VERSION_SETTING = "updates/skipped_version"


class ReleaseCheckThread(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, client, current_version, flavor, parent=None):
        super().__init__(parent)
        self.client = client
        self.current_version = current_version
        self.flavor = flavor

    def run(self):
        try:
            result = self.client.check_for_update(self.current_version, self.flavor)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)


class InstallerDownloadThread(QThread):
    progress_changed = pyqtSignal(object, object)
    completed = pyqtSignal(object, object)
    failed = pyqtSignal(str)

    def __init__(self, client, asset, parent=None):
        super().__init__(parent)
        self.client = client
        self.asset = asset

    def _report_progress(self, received, total):
        if self.isInterruptionRequested():
            raise UpdateError("下载已取消。")
        self.progress_changed.emit(received, total)

    def run(self):
        try:
            path, digest = self.client.download_installer(
                self.asset,
                progress=self._report_progress,
            )
            if self.isInterruptionRequested():
                path.unlink(missing_ok=True)
                raise UpdateError("下载已取消。")
            verification = verify_installer(
                path,
                digest,
                trusted_signer_sha256=TRUSTED_SIGNER_CERT_SHA256,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(path, verification)


class UpdateController(QObject):
    def __init__(self, window, settings, version_button=None, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.settings = settings
        self.version_button = version_button
        self.flavor = current_build_flavor()
        self.client = GitHubReleaseClient()
        self._check_thread = None
        self._download_thread = None
        self._progress_dialog = None
        self._manual_check = False
        self._active_release = None

        try:
            cleanup_update_cache()
        except OSError:
            # Cache cleanup must never prevent the application from starting.
            pass

    def automatic_check_due(self, now=None):
        value = self.settings.value(LAST_CHECK_SETTING, "", type=str)
        if not value:
            return True
        try:
            last_check = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if last_check.tzinfo is None:
                last_check = last_check.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return True
        return (now or datetime.now(timezone.utc)) - last_check >= CHECK_INTERVAL

    def check_automatically(self):
        if self.automatic_check_due():
            self.check_for_updates(manual=False)

    def check_for_updates(self, manual=True):
        if self._check_thread is not None and self._check_thread.isRunning():
            if manual:
                self._show_message("检查更新", "正在检查 GitHub Release，请稍候。")
            return
        if self._download_thread is not None and self._download_thread.isRunning():
            if manual:
                self._show_message("更新进行中", "新版安装包正在下载。")
            return

        self._manual_check = bool(manual)
        self._set_checking_state(True)
        thread = ReleaseCheckThread(self.client, APP_VERSION, self.flavor, self)
        thread.completed.connect(self._on_check_completed)
        thread.failed.connect(self._on_check_failed)
        thread.finished.connect(self._on_check_thread_finished)
        self._check_thread = thread
        thread.start()

    def _on_check_thread_finished(self):
        thread = self._check_thread
        self._check_thread = None
        if thread is not None:
            thread.deleteLater()
        self._set_checking_state(False)

    def _record_check_time(self):
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.settings.setValue(LAST_CHECK_SETTING, timestamp)

    def _on_check_failed(self, message):
        self._record_check_time()
        if self._manual_check:
            self._show_message("检查更新失败", message, QMessageBox.Warning)

    def _on_check_completed(self, result):
        self._record_check_time()
        if not isinstance(result, UpdateCheckResult):
            if self._manual_check:
                self._show_message("检查更新失败", "更新服务返回了无法识别的数据。", QMessageBox.Warning)
            return

        if not result.update_available:
            if self._manual_check:
                self._show_message(
                    "已经是最新版本",
                    f"当前版本：v{APP_VERSION}\nGitHub 最新正式版：v{result.release.version}",
                )
            return

        skipped = self.settings.value(SKIPPED_VERSION_SETTING, "", type=str)
        if not self._manual_check and skipped == str(result.release.version):
            return
        self._active_release = result.release
        self._prompt_for_update(result)

    def _prompt_for_update(self, result):
        release = result.release
        box = QMessageBox(self.window)
        box.setWindowTitle(f"{APP_NAME} 更新")
        box.setIcon(QMessageBox.Information)
        box.setText(
            f"发现 BandScope v{release.version}\n"
            f"当前版本为 v{APP_VERSION}，构建类型为 {self.flavor}。"
        )
        notes = (release.notes or "本次 Release 未填写更新说明。").strip()
        if len(notes) > 1600:
            notes = notes[:1600].rstrip() + "\n……"
        box.setInformativeText(notes)

        if is_frozen_build():
            primary = box.addButton("下载并安装", QMessageBox.AcceptRole)
        else:
            primary = box.addButton("打开 Release 页面", QMessageBox.AcceptRole)
        later = box.addButton("稍后", QMessageBox.RejectRole)
        skip = box.addButton("跳过此版本", QMessageBox.DestructiveRole)
        box.setDefaultButton(primary)
        box.setEscapeButton(later)
        box.exec_()

        clicked = box.clickedButton()
        if clicked is skip:
            self.settings.setValue(SKIPPED_VERSION_SETTING, str(release.version))
            return
        if clicked is not primary:
            return
        self.settings.remove(SKIPPED_VERSION_SETTING)
        if not is_frozen_build():
            QDesktopServices.openUrl(QUrl(release.page_url or GITHUB_RELEASES_URL))
            return
        self._start_download(result)

    def _start_download(self, result):
        asset = result.installer
        if asset is None:
            self._show_message(
                "无法自动更新",
                f"Release 中没有适用于 {self.flavor} 的安装包。",
                QMessageBox.Warning,
            )
            return

        dialog = QProgressDialog("正在下载新版安装包……", "取消", 0, 1000, self.window)
        dialog.setWindowTitle(f"更新 {APP_NAME}")
        dialog.setWindowModality(True)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setValue(0)

        thread = InstallerDownloadThread(self.client, asset, self)
        thread.progress_changed.connect(self._on_download_progress)
        thread.completed.connect(self._on_download_completed)
        thread.failed.connect(self._on_download_failed)
        thread.finished.connect(self._on_download_thread_finished)
        dialog.canceled.connect(thread.requestInterruption)

        self._progress_dialog = dialog
        self._download_thread = thread
        dialog.show()
        thread.start()

    def _on_download_progress(self, received, total):
        dialog = self._progress_dialog
        if dialog is None:
            return
        received = max(0, int(received))
        total = max(0, int(total))
        if total:
            permille = min(1000, int(received * 1000 / total))
            dialog.setRange(0, 1000)
            dialog.setValue(permille)
            dialog.setLabelText(
                f"正在下载新版安装包…… {received / (1024 ** 2):.1f} / {total / (1024 ** 2):.1f} MiB"
            )
        else:
            dialog.setRange(0, 0)
            dialog.setLabelText(f"正在下载新版安装包…… {received / (1024 ** 2):.1f} MiB")

    def _on_download_failed(self, message):
        if message != "下载已取消。":
            self._show_message("更新下载失败", message, QMessageBox.Critical)

    def _on_download_completed(self, path, verification):
        dialog = self._progress_dialog
        if dialog is not None:
            dialog.setRange(0, 1000)
            dialog.setValue(1000)
        if not isinstance(verification, InstallerVerification):
            self._show_message("安装包校验失败", "无法确认安装包完整性。", QMessageBox.Critical)
            return

        if verification.signature_valid:
            signature_text = "Windows 数字签名有效。"
            icon = QMessageBox.Information
        elif TRUSTED_SIGNER_CERT_SHA256:
            # A pinned signer would already have caused verify_installer to fail.
            signature_text = "Windows 数字签名校验失败。"
            icon = QMessageBox.Critical
        else:
            signature_text = (
                f"尚未配置发布证书校验（签名状态：{verification.signature_status}）。\n"
                "首次正式发布前建议配置代码签名证书。"
            )
            icon = QMessageBox.Warning

        box = QMessageBox(self.window)
        box.setWindowTitle("安装 BandScope 更新")
        box.setIcon(icon)
        box.setText("安装包下载完成，GitHub SHA-256 校验已通过。")
        box.setInformativeText(
            f"{signature_text}\n\n启动安装程序后 BandScope 将退出；安装器会在原目录覆盖升级，不会删除用户设置。"
        )
        install_button = box.addButton("启动安装程序", QMessageBox.AcceptRole)
        cancel_button = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(install_button if verification.signature_valid else cancel_button)
        box.setEscapeButton(cancel_button)
        box.exec_()
        if box.clickedButton() is not install_button:
            return

        try:
            launch_installer(path)
        except Exception as exc:
            self._show_message("无法启动安装程序", str(exc), QMessageBox.Critical)
            return
        QApplication.quit()

    def _on_download_thread_finished(self):
        thread = self._download_thread
        self._download_thread = None
        if thread is not None:
            thread.deleteLater()
        dialog = self._progress_dialog
        self._progress_dialog = None
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()

    def _set_checking_state(self, checking):
        button = self.version_button
        if button is None:
            return
        button.setEnabled(not checking)
        button.setText("检查中…" if checking else f"v{APP_VERSION}")

    def _show_message(self, title, text, icon=QMessageBox.Information):
        box = QMessageBox(self.window)
        box.setWindowTitle(title)
        box.setIcon(icon)
        box.setText(str(text))
        box.setStandardButtons(QMessageBox.Ok)
        box.exec_()

    def shutdown(self, wait_ms=2000):
        for thread in (self._check_thread, self._download_thread):
            if thread is not None and thread.isRunning():
                thread.requestInterruption()
                if not thread.wait(wait_ms):
                    # urllib can still be blocked in a socket read.  These
                    # workers touch only the disposable update cache, so a
                    # bounded termination during process shutdown is safer
                    # than destroying a live QThread (which aborts Qt).
                    thread.terminate()
                    thread.wait(1000)
