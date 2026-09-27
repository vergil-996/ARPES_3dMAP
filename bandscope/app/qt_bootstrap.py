import os
from pathlib import Path


def configure_qt_high_dpi():
    """Enable per-monitor Qt scaling before QApplication is created."""
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

    try:
        from PyQt5.QtCore import QCoreApplication, Qt
    except Exception:
        return

    if QCoreApplication.instance() is not None:
        return

    for attribute_name in ("AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"):
        attribute = getattr(Qt, attribute_name, None)
        if attribute is not None:
            QCoreApplication.setAttribute(attribute, True)


def configure_qt_plugin_path():
    """Help Qt find platform plugins when the app lives in a non-ASCII path."""
    try:
        import PyQt5
    except Exception:
        return

    pyqt_root = Path(PyQt5.__file__).resolve().parent
    plugins_dir = pyqt_root / "Qt5" / "plugins"
    platforms_dir = plugins_dir / "platforms"
    if not platforms_dir.exists():
        return

    os.environ.setdefault("QT_PLUGIN_PATH", str(plugins_dir))
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(platforms_dir))
