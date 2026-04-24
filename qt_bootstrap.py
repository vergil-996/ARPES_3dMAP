import os
from pathlib import Path


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
