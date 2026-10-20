# -*- coding: utf-8 -*-
"""多数据源工作区的真实窗口验收：加载两份数据、切换、改名与源级关闭。

默认用同一份真实 NPZ 连续加载两次（验证"重复加载也新增独立工作区"），输出
截图、`acceptance.json` 与进程内存/切换耗时到 `.local/outputs/multi_source_workspaces/`。
需要桌面 OpenGL；纯自动化回归见 `tests/integration/test_source_workspaces.py`。
"""
import ctypes
import faulthandler
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
faulthandler.enable()

from scripts.validation._common import arguments


class _MemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _rss_mib():
    try:
        kernel32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_MemoryCounters),
            ctypes.c_ulong,
        ]
        counters = _MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            return round(counters.WorkingSetSize / (1024 * 1024), 1)
    except Exception:
        pass
    return None


def main():
    args = arguments("multi_source_workspaces", default_data=".local/data/WSe2_step.npz")
    from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

    configure_qt_high_dpi()
    configure_qt_plugin_path()

    from PyQt5.QtCore import QTimer
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    import numpy as np

    import bandscope.app.refactored_app as module

    class NoUpdates:
        def __init__(self, *a, **kw):
            pass

        def check_automatically(self):
            pass

        def shutdown(self, **kw):
            pass

    module.UpdateController = NoUpdates
    app = QApplication(sys.argv)
    window = module.My3DAnalyzer()
    window.show()
    report = {
        "data": str(args.data.resolve()),
        "checks": [],
        "metrics": {},
        "visual_review": "pending",
    }

    def finish(code=0):
        (args.output_dir / "acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        window.close()
        app.exit(code)

    def step(work):
        try:
            work()
        except Exception:
            report["error"] = traceback.format_exc()
            print(report["error"], flush=True)
            finish(1)

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        report["checks"].append(name)
        print(f"PASS {name}", flush=True)

    def wait_ready(work, timeout=120):
        deadline = time.monotonic() + timeout

        def poll():
            ready = window._render_exact_ready and window.current_render_context is not None
            if ready:
                QTimer.singleShot(350, lambda: step(work))
            elif time.monotonic() > deadline:
                step(lambda: check("等待精确结果", False))
            else:
                QTimer.singleShot(150, poll)

        QTimer.singleShot(150, poll)

    def load_first():
        report["metrics"]["rss_before_mib"] = _rss_mib()
        started = time.perf_counter()
        check("第一份加载成功", window.load_data(str(args.data)))
        report["metrics"]["first_load_s"] = round(time.perf_counter() - started, 2)
        check("第一份只有一个数据源", len(window.source_manager) == 1)
        check("单份数据切换器收起", window.source_switcher_bar.isHidden())
        report["metrics"]["rss_after_first_mib"] = _rss_mib()
        wait_ready(load_second)

    def load_second():
        started = time.perf_counter()
        check("第二份加载成功", window.load_data(str(args.data)))
        report["metrics"]["second_load_s"] = round(time.perf_counter() - started, 2)
        check("两份数据各占一个工作区", len(window.source_manager) == 2)
        check(
            "别名自动去重",
            [alias for _sid, alias in window.source_manager.source_items()]
            == ["WSe2_step", "WSe2_step_2"],
        )
        QTest.qWait(450)
        check("两份数据切换器展开", not window.source_switcher_bar.isHidden())
        check("切换器条目数", window.source_switcher_bar.switcher.count() == 2)
        report["metrics"]["rss_after_second_mib"] = _rss_mib()
        wait_ready(rename_alias)

    def rename_alias():
        first_id, second_id = window.source_manager.session_ids()
        names = ["材料 A", "材料 B"]
        for session_id, alias in zip((first_id, second_id), names):
            window._commit_source_alias(session_id, alias)
        check(
            "别名编辑落地",
            [alias for _sid, alias in window.source_manager.source_items()] == names,
        )
        window._commit_source_alias(second_id, "")
        check("空名称恢复默认别名", window.source_manager.alias_for(second_id) == "WSe2_step")
        window._commit_source_alias(second_id, "材料 A")
        check("重名自动追加序号", window.source_manager.alias_for(second_id) == "材料 A_2")
        switch_between()

    def switch_between():
        first_id, second_id = window.source_manager.session_ids()
        window.timeline_bar.slider_time.setValue(1)
        started = time.perf_counter()
        check("切回第一份", window.activate_source(first_id))
        report["metrics"]["switch_to_first_s"] = round(time.perf_counter() - started, 3)
        check("第一份帧号恢复", window.timeline_bar.slider_time.value() == 0)

        window.timeline_bar.slider_time.setValue(2)
        started = time.perf_counter()
        check("切回第二份", window.activate_source(second_id))
        report["metrics"]["switch_back_s"] = round(time.perf_counter() - started, 3)
        check("第二份帧号恢复", window.timeline_bar.slider_time.value() == 1)
        report["metrics"]["rss_after_switches_mib"] = _rss_mib()
        # QWidget.grab() 抓不到原生 OpenGL 表面，3D 场景单独取像素判断非空。
        pixels = window.plotter.screenshot(
            str(args.output_dir / "scene_after_switch.png"), return_img=True
        )
        rgb = np.asarray(pixels[..., :3], dtype=np.float64)
        # 只统计左侧画布区域：右侧色条本身就能让 std 大于 0，不能当作"体积可见"。
        canvas = rgb[:, : int(rgb.shape[1] * 0.75)]
        visible_pixels = int(np.count_nonzero(np.any(canvas < 200, axis=2)))
        report["metrics"]["scene_visible_pixels"] = visible_pixels
        check("切源后 3D 场景非空", visible_pixels > 500)
        window.grab().save(str(args.output_dir / "two_sources_window.png"))
        window.source_switcher_bar.grab().save(str(args.output_dir / "source_switcher.png"))
        wait_ready(close_second)

    def close_second():
        first_id, second_id = window.source_manager.session_ids()
        started = time.perf_counter()
        check("关闭第二份数据源", window.close_source(second_id))
        report["metrics"]["close_source_s"] = round(time.perf_counter() - started, 3)
        check("剩余一份", window.source_manager.session_ids() == [first_id])
        check("切换器重新收起", window.source_switcher_bar.is_expanded() is False)
        QTest.qWait(450)
        check("切换器不占布局", window.source_switcher_bar.isHidden())
        report["metrics"]["rss_after_close_mib"] = _rss_mib()
        window.grab().save(str(args.output_dir / "after_close_window.png"))
        report["automation"] = "passed"
        finish()

    QTimer.singleShot(900, lambda: step(load_first))
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
