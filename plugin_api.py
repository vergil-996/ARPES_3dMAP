"""Compatibility import; new code uses bandscope.extensions.api."""
import sys
import bandscope.extensions.api as _implementation

sys.modules[__name__] = _implementation
