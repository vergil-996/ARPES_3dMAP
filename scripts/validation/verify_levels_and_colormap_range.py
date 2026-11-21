"""真实窗口验证累计直方图、输入色阶和输出色带范围。"""
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
    from scripts.validation._common import arguments, create_axis_slice

    args = arguments(
        "levels-and-colormap-range", dynamic=True,
        default_data=Path(".local/data/WSe2_step.npz"),
    )
    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app
    from bandscope.ui.enhancement_controls import SHOW_ALL_REGIONS_ID

    refactored_app.UpdateController = _NullUpdateController
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    checks = []
    screenshots = []

    def wait_ready(timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QTest.qWait(80)
            if (window.__dict__.get("_render_exact_ready")
                    and window.__dict__.get("_display_stats_job") is None
                    and window.__dict__.get("_enhancement_roi_job") is None):
                return
        raise AssertionError("等待精确渲染和后台统计超时")

    def capture(name):
        path = args.output_dir / name
        QApplication.processEvents()
        QTest.qWait(200)
        if not window.grab().save(str(path)):
            raise AssertionError(f"截图失败：{path}")
        screenshots.append(path.name)

    def capture_volume(name):
        path = args.output_dir / name
        window.plotter.render()
        window.plotter.screenshot(str(path))
        if not path.is_file() or path.stat().st_size == 0:
            raise AssertionError(f"三维帧缓冲截图失败：{path}")
        screenshots.append(path.name)

    def drag(editor, name, fractions):
        position = editor._handle_positions()[name]
        y = editor._track_rect().center().y()
        editor._active_name = name
        QTest.mousePress(editor, Qt.LeftButton, pos=QPoint(int(round(position)), y))
        if not editor._dragging:
            raise AssertionError(f"滑块未命中：{name}")
        track = editor._track_rect()
        for fraction in fractions:
            point = QPoint(int(round(track.left() + track.width() * fraction)), y)
            event = QMouseEvent(
                QEvent.MouseMove, QPointF(point), Qt.NoButton, Qt.LeftButton, Qt.NoModifier,
            )
            QApplication.sendEvent(editor, event)
            QTest.qWait(45)
        point = QPoint(int(round(track.left() + track.width() * fractions[-1])), y)
        QTest.mouseRelease(editor, Qt.LeftButton, pos=point)
        wait_ready()

    def select_palette(name):
        combo = window.page_render.combo_cmap
        index = combo.findText(name)
        if index < 0:
            raise AssertionError(f"缺少色带：{name}")
        combo.setCurrentIndex(index)
        window.page_render.btn_apply_cmap.click()
        wait_ready()

    window.load_data(str(args.data))
    wait_ready()
    page = window.page_render
    select_palette("gray")
    page.s_low.setValue(0)
    page.s_gamma.setValue(50)
    page.s_up.setValue(100)
    page.s_cmap_low.setValue(200)
    page.s_cmap_high.setValue(800)
    window.flush_render_levels_refresh()
    wait_ready()
    assert page.get_display_style()["colormap_range"] == (0.2, 0.8)
    assert page.levels_editor._histogram is not None
    assert page.levels_editor._histogram["total"] > 0
    assert np.all(np.diff(page.levels_editor._histogram["counts"]) <= 0)
    checks.append("3D 当前帧累计直方图与 20%–80% 输出范围")
    capture_volume("volume-gray.png")

    histogram = page.levels_editor._histogram
    black, white = page.s_low.value(), page.s_up.value()
    drag(page.levels_editor, "gray", [0.50, 0.45, 0.40, 0.45, 0.50])
    assert page.levels_editor._histogram is histogram
    assert (page.s_low.value(), page.s_up.value()) == (black, white)
    drag(page.colormap_range_editor, "cmap_low", [0.2, 0.3, 0.4, 0.3, 0.2])
    assert page.levels_editor._histogram is histogram
    actual_range = page.get_display_style()["colormap_range"]
    np.testing.assert_allclose(actual_range, (0.2, 0.8), atol=0.0011, rtol=0.0)
    page.s_cmap_low.setValue(200)
    page.s_cmap_high.setValue(800)
    window.flush_render_levels_refresh()
    wait_ready()
    checks.append("连续拖动只更新映射并复用直方图缓存")

    select_palette("viridis")
    assert page.get_display_style()["colormap_range"] == (0.2, 0.8)
    select_palette("gray")
    assert page.get_display_style()["colormap_range"] == (0.2, 0.8)
    checks.append("切换色带保留输出端点")

    first_histogram = page.levels_editor._histogram
    slider = window.timeline_bar.slider_time
    if slider.maximum() > slider.minimum():
        slider.setValue(slider.maximum())
        window.flush_time_slider_refresh()
        wait_ready()
        assert page.levels_editor._histogram is not first_histogram
        checks.append(f"切帧至 {slider.value()} 后重新统计当前有效体素")

    window._add_enhancement_region()
    wait_ready()
    fields = window._enhancement_cache[1]
    region = next(item for item in fields["regions"] if item["status"] in {"ready", "constant"})
    expected = int(np.isfinite(fields["region_data"][region["region_id"]]).sum())
    assert page.levels_editor._histogram["total"] == expected
    capture("region-light.png")
    checks.append("局部增强区域使用区域内上色前强度")

    index = page.region_combo.findData(SHOW_ALL_REGIONS_ID)
    page.region_combo.setCurrentIndex(index)
    wait_ready()
    fields = window._enhancement_cache[1]
    expected = int(np.isfinite(window.current_render_context["data"]).sum())
    assert page.levels_editor._histogram["total"] == expected
    checks.append("全部显示统计当前视图全部有效点")

    window._set_theme("dark")
    window.resize(1000, 720)
    window.showNormal()
    QTest.qWait(500)
    capture("all-dark-narrow.png")
    assert page.levels_editor.width() == page.colormap_range_editor.width()
    assert page.get_display_style()["colormap_range"] == (0.2, 0.8)
    window._set_theme("light")
    QTest.qWait(400)
    capture("all-light-narrow.png")
    checks.append("深浅主题与窄面板下两组控件同宽且参数保持")

    create_axis_slice(window, 2)
    wait_ready()
    image = window.ax_2d._arpes_image
    assert image is not None
    actual = np.asarray(image.cmap([0.0, 0.5, 1.0]))
    from matplotlib import colormaps
    np.testing.assert_array_equal(actual, colormaps["gray"]([0.2, 0.5, 0.8]))
    capture("slice-2d-light.png")
    checks.append("二维切片和屏幕色标使用相同截断色带")

    report = {
        "data": str(args.data),
        "frame": int(slider.value()),
        "colormap_range": list(page.get_display_style()["colormap_range"]),
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
