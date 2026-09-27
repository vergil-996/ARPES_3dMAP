"""Compatibility import; new code uses bandscope.ui.ui_controls."""
import sys
import bandscope.ui.ui_controls as _implementation

sys.modules[__name__] = _implementation
