"""Shared UI surface available to plugins (requires Qt)."""
from bandscope.ui import theme
from bandscope.ui.ui_controls import ActionButton, SyncedSlider, SyncedSwitch

__all__ = ["theme", "ActionButton", "SyncedSlider", "SyncedSwitch"]
