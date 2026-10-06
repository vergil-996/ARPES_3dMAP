# -*- coding: utf-8 -*-
"""插件管理入口：顶部按钮、管理窗口复用与右键菜单去重（计划阶段 A）。

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

window = My3DAnalyzer()

# 顶部计算信息控件已被插件管理按钮替换。
assert not hasattr(window, "backend_chip"), "backend_chip 应该已移除"
assert window.btn_tb_plugins.isEnabled(), window.btn_tb_plugins.toolTip()
assert window.btn_tb_plugins.toolTip() == "安装、卸载及启用/停用插件"
assert window.btn_tb_plugins.text() == "插件管理"

# 点击打开管理窗口；重复点击复用同一个窗口。
dialog = window.open_plugin_manager()
assert dialog is not None
assert dialog.windowTitle() == "插件管理"
assert window.open_plugin_manager() is dialog
assert dialog.btn_install.text() == "安装插件…"

# 只有管理器自身无法构造时按钮才禁用，并在悬停提示原因。
saved_session = window.plugin_session
window.plugin_session = None
window._update_plugin_button_state()
assert not window.btn_tb_plugins.isEnabled()
assert "不可用" in window.btn_tb_plugins.toolTip()
window.plugin_session = saved_session
window._update_plugin_button_state()
assert window.btn_tb_plugins.isEnabled()

# 右键菜单不再有重复的扩展管理入口，但仍可检查更新。
menu, _pos = My3DAnalyzer._create_settings_context_menu(window, window, QPoint(4, 6))
texts = [action.text() for action in menu.actions()]
assert not any("扩展管理" in text for text in texts), texts
assert any("检查更新" in text for text in texts), texts

# 底部状态栏仍接收计算状态更新。
window._update_render_status("complete", "CPU")

window.close()
print("{_MARKER}")
"""


class PluginManagementEntryTests(unittest.TestCase):
    @requires_opengl
    def test_top_toolbar_entry_opens_and_reuses_the_manager_window(self):
        env = dict(os.environ)
        env.pop("QT_QPA_PLATFORM", None)
        completed = subprocess.run(
            [sys.executable, "-c", _SCRIPT],
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
