"""Compatibility import; new code uses bandscope.ui.theme."""
import sys
import bandscope.ui.theme as _implementation

sys.modules[__name__] = _implementation
