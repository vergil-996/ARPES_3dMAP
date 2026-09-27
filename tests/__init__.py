"""Shared regression suite; safe to run from a fresh checkout."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.support.environment import isolate_runtime

isolate_runtime()
