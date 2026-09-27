"""Keep tests and validation runs away from the user's settings/extensions."""
import atexit
import os
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
