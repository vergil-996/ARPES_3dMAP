"""File dialogs sharing the last directory selected by the user."""
from pathlib import Path

from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QFileDialog as QtFileDialog


LAST_DIRECTORY_KEY = "file_dialogs/last_directory"


def _settings(parent):
    while parent is not None:
        settings = getattr(parent, "settings", None)
        if settings is not None:
            return settings
        parent_method = getattr(parent, "parent", None)
        parent = parent_method() if callable(parent_method) else None
    return QSettings("ARPES", "ARPES_3dMAP")


def _initial_path(settings, directory):
    remembered = settings.value(LAST_DIRECTORY_KEY, "")
    if not remembered or not Path(str(remembered)).is_dir():
        return str(directory)
    # Keep the suggested filename, but always start in the latest chosen folder.
    suggested = Path(directory) if directory else None
    if suggested is not None and not suggested.is_dir():
        return str(Path(remembered) / suggested.name)
    return str(remembered)


def _remember(settings, path):
    if not path:
        return
    settings.setValue(LAST_DIRECTORY_KEY, str(Path(path).resolve().parent))
    sync = getattr(settings, "sync", None)
    if callable(sync):
        sync()


class RememberingFileDialog(QtFileDialog):
    """Qt-compatible open/save helpers; cancellation leaves history untouched."""

    @staticmethod
    def getOpenFileName(parent=None, caption="", directory="", filter="", *args, **kwargs):
        settings = _settings(parent)
        result = QtFileDialog.getOpenFileName(
            parent, caption, _initial_path(settings, directory), filter, *args, **kwargs
        )
        _remember(settings, result[0])
        return result

    @staticmethod
    def getSaveFileName(parent=None, caption="", directory="", filter="", *args, **kwargs):
        settings = _settings(parent)
        result = QtFileDialog.getSaveFileName(
            parent, caption, _initial_path(settings, directory), filter, *args, **kwargs
        )
        _remember(settings, result[0])
        return result
