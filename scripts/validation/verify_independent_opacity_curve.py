"""真实窗口验证完整强度范围和独立不透明度曲线。"""
import copy
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

import numpy as np
from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def main():
    from scripts.validation._common import arguments

    args = arguments(
        "independent-opacity-curve", dynamic=True,
        default_data=Path(".local/data/WSe2_step.npz"),
    )
    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app
    from bandscope.core.local_enhancement import STATE_KEY
    from bandscope.ui.enhancement_controls import SHOW_ALL_REGIONS_ID

    refactored_app.UpdateController = _NullUpdateController
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    checks = []
    screenshots = []

    def wait_ready(timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QTest.qWait(80)
            if (window.__dict__.get("_render_exact_ready")
                    and window.__dict__.get("_display_stats_job") is None
                    and window.__dict__.get("_enhancement_roi_job") is None):
                return
        raise AssertionError("等待精确渲染和后台统计超时")

    def capture(name, widget=None):
        path = args.output_dir / name
        QApplication.processEvents()
        QTest.qWait(200)
        target = window if widget is None else widget
        if not target.grab().save(str(path)):
            raise AssertionError(f"截图失败：{path}")
        screenshots.append(path.name)

    def capture_volume(name):
        path = args.output_dir / name
        window.plotter.render()
        window.plotter.screenshot(str(path))
        if not path.is_file() or path.stat().st_size == 0:
            raise AssertionError(f"三维帧缓冲截图失败：{path}")
        screenshots.append(path.name)

    def move(point):
        event = QMouseEvent(
            QEvent.MouseMove, QPointF(point), Qt.NoButton, Qt.LeftButton, Qt.NoModifier,
        )
        QApplication.sendEvent(page.opacity_editor.curve, event)
        QTest.qWait(55)

    def drag(name, dx=0, dy=0, steps=4):
        curve = page.opacity_editor.curve
        position = curve._handle_positions()[name]
        start = QPoint(int(round(position.x())), int(round(position.y())))
        end = QPoint(start.x() + dx, start.y() + dy)
        QTest.mousePress(curve, Qt.LeftButton, pos=start)
        if not curve._dragging or curve._active_name != name:
            raise AssertionError(f"曲线手柄未命中：{name}")
        for index in range(1, steps + 1):
            move(QPoint(
                int(round(start.x() + dx * index / steps)),
                int(round(start.y() + dy * index / steps)),
            ))
        QTest.mouseRelease(curve, Qt.LeftButton, pos=end)
        wait_ready()

    def set_values(**values):
        for name, value in values.items():
            page.opacity_editor.display_parameters[name][1].setValue(float(value))
        window.flush_render_levels_refresh()
        wait_ready()

    window.load_data(str(args.data))
    wait_ready()
    page = window.page_render
    assert not hasattr(page, "grp_normalize")
    assert not hasattr(page, "combo_normalization")
    assert page.grp_opacity.isVisible()
    fields = window._enhancement_cache[1]
    finite = window.current_render_context["data"][np.isfinite(window.current_render_context["data"])]
    assert fields["background_range"] == (float(finite.min()), float(finite.max()))
    checks.append("主窗口无归一化卡片，独立模式使用含极值的完整范围")

    set_values(threshold=12.0, transition=8.0, gamma=1.4, density=12.0)
    capture_volume("volume-baseline.png")
    before = window._enhancement_cache[1]
    color = before["color"]
    alpha = before["alpha"]
    drag("density", dy=-35, steps=6)
    after = window._enhancement_cache[1]
    assert after["color"] is color
    assert after["alpha"] is not alpha
    assert window._render_exact_ready
    checks.append("密度连续拖动复用颜色通道，松手生成最终精确帧")

    set_values(threshold=25.0, transition=0.0, gamma=1.4, density=15.0)
    positions = page.opacity_editor.curve._handle_positions()
    assert positions["threshold"].x() == positions["transition"].x()
    assert positions["threshold"].y() < positions["transition"].y()
    drag("threshold", dx=18)
    assert page.opacity_editor.values()["transition"] == 0.0
    threshold = page.opacity_editor.values()["threshold"]
    drag("transition", dx=24)
    assert page.opacity_editor.values()["threshold"] == threshold
    assert page.opacity_editor.values()["transition"] > 0.0
    checks.append("零宽度重合时黑场和过渡终点仍可独立选取")

    set_values(threshold=20.0, transition=10.0, gamma=1.4, density=12.0)
    drag("gamma", dx=-20)
    assert page.opacity_editor.values()["gamma"] < 1.4
    weaker = page.opacity_editor.values()["gamma"]
    drag("gamma", dx=30)
    assert page.opacity_editor.values()["gamma"] > weaker
    checks.append("Gamma 左拖提亮弱信号、右拖压低弱信号")

    set_values(threshold=25.0, transition=15.0, gamma=1.8, density=0.0)
    curve = page.opacity_editor.curve
    before_x = {name: point.x() for name, point in curve._handle_positions().items()}
    start = QPoint(int(round(curve._x(35.0))), int(round(curve._y(0.0))))
    end = QPoint(start.x(), start.y() - 42)
    QTest.mousePress(curve, Qt.LeftButton, pos=start)
    if not curve._dragging or not curve._direct_curve:
        raise AssertionError("零密度曲线未能直接起拖")
    for index in range(1, 6):
        move(QPoint(start.x(), int(round(start.y() - 42 * index / 5))))
    QTest.mouseRelease(curve, Qt.LeftButton, pos=end)
    wait_ready()
    assert page.opacity_editor.values()["density"] > 0.0
    assert page.opacity_editor.values()["threshold"] == 25.0
    assert page.opacity_editor.values()["transition"] == 15.0
    assert page.opacity_editor.values()["gamma"] == 1.8
    after_x = {name: point.x() for name, point in curve._handle_positions().items()}
    assert after_x == before_x
    checks.append("零密度可直接向上拖起，阈值和全部手柄横坐标稳定")

    set_values(threshold=100.0, density=0.0)
    page.opacity_editor.display_parameters["gamma"][1].setValue(3.25)
    assert page.opacity_editor.values()["gamma"] == 3.25
    drag("density", dy=-30)
    assert page.opacity_editor.values()["density"] > 0.0
    checks.append("阈值为 100% 时 Gamma 数值框和密度拖动仍可用")

    set_values(threshold=60.0, transition=50.0, gamma=1.2, density=20.0)
    assert curve._axis_maximum() == 110.0
    window.resize(1000, 720)
    window.showNormal()
    QTest.qWait(400)
    plot = curve._plot_rect()
    start = curve._handle_positions()["transition"]
    start = QPoint(int(round(start.x())), int(round(start.y())))
    end = QPoint(plot.right() + 12, start.y())
    QTest.mousePress(curve, Qt.LeftButton, pos=start)
    move(end)
    QTest.mouseRelease(curve, Qt.LeftButton, pos=end)
    wait_ready()
    assert page.opacity_editor.values()["transition"] > 50.0
    assert page.opacity_editor.values()["threshold"] + page.opacity_editor.values()["transition"] > 100.0
    capture("opacity-narrow-light.png", page.opacity_editor)
    checks.append("窄面板下终点可超过 100%，手柄命中和紧凑数值框可用")

    window._set_theme("dark")
    QTest.qWait(400)
    capture("opacity-narrow-dark.png", page.opacity_editor)
    window._set_theme("light")
    window.resize(1280, 800)
    QTest.qWait(400)

    window._add_enhancement_region()
    wait_ready()
    state = window.active_page_spec.params[STATE_KEY]
    background = copy.deepcopy(state["background"])
    selected_id = state["selected_id"]
    density = page.opacity_editor.values()["density"]
    set_values(density=min(100.0, density + 7.0))
    assert state["background"] == background
    selected = next(region for region in state["regions"] if region["region_id"] == selected_id)
    assert selected["style"]["opacity"]["density"] == min(100.0, density + 7.0)
    capture("region-opacity.png")
    checks.append("局部区域只修改当前目标，背景参数保持不变")

    index = page.region_combo.findData(SHOW_ALL_REGIONS_ID)
    page.region_combo.setCurrentIndex(index)
    wait_ready()
    set_values(threshold=12.0, transition=8.0, gamma=0.8, density=35.0)
    assert state["background"]["opacity"]["gamma"] == 0.8
    assert all(region["style"]["opacity"]["gamma"] == 0.8 for region in state["regions"])
    capture_volume("volume-weak-band.png")
    capture("window-final-light.png")
    checks.append("全部显示同步调整背景与区域，弱带最终帧已输出")

    report = {
        "data": str(args.data),
        "frame": int(window.timeline_bar.slider_time.value()),
        "opacity": page.opacity_editor.values(),
        "checks": checks,
        "screenshots": screenshots,
        "status": "passed",
    }
    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if args.hold:
        app.exec_()
    else:
        window.close()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
