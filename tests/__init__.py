"""Shared regression suite; safe to run from a fresh checkout."""
import os
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.support.environment import isolate_runtime

isolate_runtime()


#: 加载 NPZ 时会在后台线程计算文件摘要（`SourceIdentity`）。Windows 上该线程
#: 短暂持有文件句柄，紧接着删除临时目录可能撞上共享冲突（WinError 32）。这里
#: 只对**清理动作**做有界重试；任何测试断言仍按原样执行。
_CLEANUP_RETRY_SECONDS = 5.0
_original_tempdir_cleanup = tempfile.TemporaryDirectory.cleanup


def _cleanup_with_retry(self):
    deadline = time.monotonic() + _CLEANUP_RETRY_SECONDS
    while True:
        try:
            return _original_tempdir_cleanup(self)
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


tempfile.TemporaryDirectory.cleanup = _cleanup_with_retry
