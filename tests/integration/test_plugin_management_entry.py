# -*- coding: utf-8 -*-
"""齿轮设置窗口、插件管理窗口复用与画布右键入口回归。

构造完整主窗口需要真实 OpenGL（离屏平台拿不到 VTK 的像素格式），因此本用例
在子进程里用真实平台构造，没有 OpenGL 的环境整条跳过——与其它渲染用例一致。
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.environment import requires_opengl

REPO_ROOT = Path(__file__).resolve().parents[2]

_MARKER = "bandscope-plugin-entry-ok"

_SCRIPT = f"""
import os
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})

# import tests.* 会把 QT_QPA_PLATFORM 设成 offscreen（离屏平台拿不到 VTK 的
# 像素格式）；本脚本要真实窗口，导入隔离环境后再去掉。
import tests.support.environment as environment
environment.isolate_runtime()
os.environ.pop("QT_QPA_PLATFORM", None)

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path
configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication

app = QApplication([])
from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.app_metadata import APP_VERSION

window = My3DAnalyzer()
window.resize(1020, 670)
window.show()
app.processEvents()

# 顶部不再显示计算硬件指示灯，原扩展和版本按钮由齿轮设置窗口取代。
assert not hasattr(window, "backend_chip"), "backend_chip 应该已移除"
assert not hasattr(window, "backend_badge"), "backend_badge 应该已移除"
assert not hasattr(window, "btn_tb_plugins")
assert not hasattr(window, "btn_tb_version")
settings_button = window.btn_tb_settings
assert settings_button.parent() is window.toolbar
assert (settings_button.width(), settings_button.height()) == (28, 26)
assert settings_button.property("fluentIconName") == "ic_fluent_settings_regular"
assert not settings_button.icon().isNull()
assert window.status_version.text() == f"v{{APP_VERSION}}"
assert window.status_version.parent() is window.statusbar

settings_button.click()
settings = window.settings_dialog
assert settings.isVisible() and not settings.isModal()
nav = settings.navigation
assert nav.currentItem().text(0) == "计算后端"
nav.setCurrentItem(nav.topLevelItem(2).child(0))
plugin_action = settings.plugins
assert plugin_action.isVisible() and plugin_action.isEnabled()
assert plugin_action.toolTip() == "安装、卸载及启用/停用插件"
plugin_action.click()
dialog = window.open_plugin_manager()
assert dialog is not None and dialog.isVisible()
assert dialog.windowTitle() == "插件管理"
assert window.open_plugin_manager() is dialog
assert dialog.btn_install.text() == "安装插件…"
dialog.close()

nav.setCurrentItem(nav.topLevelItem(1).child(0))
assert settings.denoise.isVisible()
settings.close()
settings_button.click()
assert window.settings_dialog is settings and settings.denoise.isVisible()

# 只有管理器自身无法构造时菜单动作才禁用，并在悬停提示原因。
saved_session = window.plugin_session
window.plugin_session = None
window._update_plugin_button_state()
assert not plugin_action.isEnabled()
assert "不可用" in plugin_action.toolTip()
window.plugin_session = saved_session
window._update_plugin_button_state()
assert plugin_action.isEnabled()

# 画布右键不再包含已经集中到齿轮菜单的设置与更新入口。
menu, _pos = My3DAnalyzer._create_canvas_context_menu(window, window, QPoint(4, 6))
texts = [action.text() for action in menu.actions()]
assert not any("扩展管理" in text or "插件管理" in text for text in texts), texts
assert not any("去噪参数设置" in text for text in texts), texts
assert not any("检查更新" in text or "更新设置" in text for text in texts), texts
menu.deleteLater()

# 画布不再显示计算状态浮层，底部状态栏仍接收计算状态更新。
assert not hasattr(window, "render_status_label"), "render_status_label 应该已移除"
window._update_render_status("complete", "CPU")
assert "就绪" in window.status_state.text()

window.close()
app.processEvents()
assert not settings.isVisible()
print("{_MARKER}", flush=True)
"""


class PluginManagementEntryTests(unittest.TestCase):
    @requires_opengl
    def test_gear_settings_window_opens_and_reuses_the_manager_window(self):
        env = dict(os.environ)
        env.pop("QT_QPA_PLATFORM", None)
        completed = subprocess.run(
            [sys.executable, "-X", "faulthandler", "-u", "-c", _SCRIPT],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + "\n" + completed.stderr,
        )
        self.assertIn(_MARKER, completed.stdout)


if __name__ == "__main__":
    unittest.main()
