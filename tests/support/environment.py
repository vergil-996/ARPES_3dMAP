"""Keep tests and validation runs away from the user's settings/extensions.

同时回答“这台机器真的能渲染吗”：没有可用的 OpenGL 时，渲染用例必须整条跳过，
而不是让它把测试进程带走（见 ``requires_opengl``）。
"""
import atexit
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_sandbox = None


def isolate_runtime():
    global _sandbox
    if _sandbox is not None:
        return
    _sandbox = TemporaryDirectory(prefix="bandscope-tests-")
    atexit.register(_sandbox.cleanup)
    os.environ["BANDSCOPE_EXTENSION_ROOT"] = str(Path(_sandbox.name) / "extensions")

    from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path
    configure_qt_high_dpi()
    configure_qt_plugin_path()
    from PyQt5.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope, _sandbox.name)
    _redirect_default_settings(QSettings)


def _redirect_default_settings(QSettings):
    """让 ``QSettings(org, app)`` 也落到隔离目录。

    本机 Qt 上 ``setDefaultFormat`` 对两参数构造器不生效（``format()`` 仍是
    Native），主窗口与工作区又都用这个写法，不补一层就会把测试写进用户的真实
    注册表。这里只改构造参数，格式与路径仍由上面的 setPath 决定。
    """
    original = QSettings.__init__

    def patched(self, *args, **kwargs):
        if len(args) == 2 and all(isinstance(arg, str) for arg in args):
            args = (QSettings.IniFormat, QSettings.UserScope) + args
        original(self, *args, **kwargs)

    QSettings.__init__ = patched


# GitHub 的 windows runner 没有可用的 OpenGL 驱动：VTK 拿不到像素格式，9.7 会兜底到
# OSMesa，而随包分发的 VTK 找不到 osmesa.dll，最后交回一个无法渲染的 render window。
# 第一次 screenshot() 就是访问违例，整个进程死掉，unittest 连摘要都打不出来，后面的
# 用例一条都跑不到。这种崩溃 Python 侧捕获不到，只能提前用子进程探一次。
_PROBE_MARKER = "bandscope-opengl-ok"
_PROBE_TIMEOUT_SECONDS = 180
_PROBE = f"""
import numpy as np
import pyvista as pv

grid = pv.ImageData(dimensions=(8, 8, 32))
grid.spacing = (1.0, 1.0, 0.5)
grid.point_data["values"] = np.linspace(0.0, 1.0, 8 * 8 * 32, dtype=np.float32)
plotter = pv.Plotter(off_screen=True, window_size=(64, 64))
plotter.add_volume(grid, cmap="magma")
plotter.screenshot()
print("{_PROBE_MARKER}")
"""

_NO_OPENGL_REASON = (
    "当前环境没有可用的 OpenGL，VTK 会在 screenshot() 里让整个测试进程崩溃"
    "（见 docs/handoffs/ci-no-opengl-2026-09-27.md）。"
)

_opengl_support = None


def requires_opengl(test):
    """没有可用 OpenGL 时跳过整条用例；跳过摘要里带着原因，CI 上可以核对。"""
    return unittest.skipUnless(opengl_available(), _NO_OPENGL_REASON)(test)


def opengl_available() -> bool:
    """在子进程里真渲染一帧体数据；崩溃、超时或没打标记都算没有 OpenGL。"""
    global _opengl_support
    if _opengl_support is None:
        _opengl_support = _probe_opengl()
    return _opengl_support


def _probe_opengl() -> bool:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _PROBE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and _PROBE_MARKER in (completed.stdout or "")
