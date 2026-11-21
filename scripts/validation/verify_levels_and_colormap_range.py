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
from PyQt5.QtCore import QEvent, QPoint, QPointF, QRect, Qt
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
    from bandscope.rendering.refresh_pipeline import RefreshCause, RenderQuality
    from bandscope.ui.colormap_selector import ColormapMenuRow
    from bandscope.ui.enhancement_controls import SHOW_ALL_REGIONS_ID

    refactored_app.UpdateController = _NullUpdateController
    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    checks = []
    screenshots = []
    # 记录选色期间实际请求与完成的刷新，核对“换色带只精确刷新一次”。
    refresh_requests = []
    completed_refreshes = []
    original_request_refresh = window.request_refresh

    def counting_request_refresh(*args, **kwargs):
        refresh_requests.append((args, kwargs))
        return original_request_refresh(*args, **kwargs)

    window.request_refresh = counting_request_refresh
    window.refreshFinished.connect(
        lambda request: completed_refreshes.append((request.cause, request.quality))
    )

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

    def capture_widget(widget, name, padding=6):
        path = args.output_dir / name
        QApplication.processEvents()
        QTest.qWait(80)
        widget_rect = QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())
        if window.rect().contains(widget_rect):
            capture_rect = widget_rect.adjusted(-padding, -padding, padding, padding)
            pixmap = window.grab(capture_rect.intersected(window.rect()))
        else:
            # 控件不在窗口可见区域时仍渲染控件本身，避免裁剪到相邻卡片。
            pixmap = widget.grab()
        if pixmap.isNull() or not pixmap.save(str(path)):
            raise AssertionError(f"控件截图失败：{path}")
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
        y = int(round(editor._handle_center_y()))
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

    def select_palette(name, *, changed=True):
        """通过「输出色带」标题行的新菜单选色（不再有独立的色带卡片和确定按钮）。"""
        selector = window.page_render.colormap_selector
        menu = selector.open_menu()
        row = menu.row_for(name)
        if row is None:
            raise AssertionError(f"色带菜单缺少：{name}")
        QTest.qWait(80)
        refresh_requests.clear()
        completed_refreshes.clear()
        QTest.mouseClick(row, Qt.LeftButton)
        QApplication.processEvents()
        if menu.isVisible():
            raise AssertionError("选中色带后菜单没有收起")
        if not changed:
            if refresh_requests:
                raise AssertionError(f"重选当前色带不应触发刷新：{refresh_requests}")
            return
        if window.page_render.get_selected_cmap() != name:
            raise AssertionError(f"菜单选择未生效：{name}")
        transfer_requests = [
            entry for entry in refresh_requests
            if entry[0] and entry[0][0] == RefreshCause.TRANSFER_FUNCTION
        ]
        if len(transfer_requests) != 1 or len(refresh_requests) != 1:
            raise AssertionError(f"选择色带应只请求一次传输刷新，实际 {refresh_requests}")
        wait_ready()
        exact = [
            entry for entry in completed_refreshes
            if entry == (RefreshCause.TRANSFER_FUNCTION, RenderQuality.EXACT)
        ]
        if len(exact) != 1:
            raise AssertionError(f"选择色带应只完成一次精确刷新，实际 {completed_refreshes}")

    def capture_popup(name):
        menu = window.page_render.colormap_selector.menu()
        if menu is None or not menu.isVisible():
            raise AssertionError("色带菜单没有展开")
        path = args.output_dir / name
        if not menu.grab().save(str(path)):
            raise AssertionError(f"色带菜单截图失败：{path}")
        screenshots.append(path.name)

    def verify_colormap_menu(theme_name):
        selector = window.page_render.colormap_selector
        menu = selector.open_menu()
        QTest.qWait(150)
        capture_popup(f"colormap-menu-{theme_name}.png")
        if menu.content_height() <= menu.height():
            raise AssertionError("长色带列表应出现滚动")
        if menu.scroll.verticalScrollBar().maximum() <= 0:
            raise AssertionError("色带菜单缺少滚动范围")
        row = menu.row_for("coolwarm")
        if row is None:
            raise AssertionError("色带菜单缺少 coolwarm")
        image = row.grab().toImage()
        y = row.height() // 2
        left = image.pixelColor(ColormapMenuRow.PADDING + 3, y)
        right = image.pixelColor(
            ColormapMenuRow.PADDING + ColormapMenuRow.SWATCH_WIDTH - 3, y,
        )
        if left.name() == right.name():
            raise AssertionError("色带菜单色样没有绘制完整渐变")
        current = [item.name for item in menu.rows if item.is_current()]
        if current != [selector.current_colormap()]:
            raise AssertionError(f"菜单勾选与当前色带不一致：{current}")
        selector.close_menu()

    def verify_colormap_header(theme_name):
        selector = window.page_render.colormap_selector
        capture_widget(selector, f"colormap-header-{theme_name}.png")
        if selector.width() != window.page_render.colormap_range_editor.width():
            raise AssertionError("色带标题行与渐变条宽度不一致")
        metrics = selector.name_label.fontMetrics()
        longest = max(selector.options(), key=metrics.horizontalAdvance)
        required = (
            selector.title_label.fontMetrics().horizontalAdvance(selector.title_label.text())
            + metrics.horizontalAdvance(longest)
            + selector.arrow_label.fontMetrics().horizontalAdvance(selector.arrow_label.text())
            + 18
        )
        if selector.width() < required:
            raise AssertionError(
                f"窄侧栏下色名空间不足：{selector.width()} < {required}（最长 {longest}）"
            )
        if selector.name_label.text() != window.page_render.get_selected_cmap():
            raise AssertionError("色带标题行与当前色带不同步")

    window.load_data(str(args.data))
    wait_ready()
    page = window.page_render
    card = page.levels_editor.parentWidget()
    while card is not None and card.metaObject().className() != "CardGroup":
        card = card.parentWidget()
    assert card is not None, "找不到“色阶”卡片"
    selector = page.colormap_selector
    options = selector.options()
    assert page.get_selected_cmap() == "magma", page.get_selected_cmap()
    assert selector.name_label.text() == "magma"
    assert options[:2] == ["magma", "coolwarm"], options[:2]
    assert options.count("coolwarm") == 1
    assert len(options) == len(set(options))
    assert not hasattr(page, "combo_cmap") and not hasattr(page, "btn_apply_cmap")
    checks.append("默认 magma、菜单首两项与 coolwarm 唯一，旧卡片和确定按钮已移除")
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
    # 再次选择当前项与取消菜单都不应触发刷新。
    select_palette("gray", changed=False)
    menu = page.colormap_selector.open_menu()
    menu.close()
    assert page.get_selected_cmap() == "gray"
    checks.append("切换色带保留输出端点，且每次只精确刷新一次；重选／取消不刷新")

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
    capture_widget(card, "levels-card-dark.png")
    capture_widget(page.levels_editor, "levels-dark.png")
    capture_widget(page.colormap_range_editor, "colormap-range-dark.png")
    verify_colormap_header("dark")
    verify_colormap_menu("dark")
    assert page.levels_editor.width() == page.colormap_range_editor.width()
    assert page.get_display_style()["colormap_range"] == (0.2, 0.8)
    window._set_theme("light")
    QTest.qWait(400)
    capture("all-light-narrow.png")
    capture_widget(card, "levels-card-light.png")
    capture_widget(page.levels_editor, "levels-light.png")
    capture_widget(page.colormap_range_editor, "colormap-range-light.png")
    verify_colormap_header("light")
    verify_colormap_menu("light")
    checks.append("深浅主题与窄面板下色名、色样、手柄布局与参数保持")

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
