"""Compare real ActionButton Fluent icons with target-pixel SVG rendering."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

SCALES = (1.0, 1.25, 1.5, 1.5625, 1.75, 2.0)
ICON_SPECS = (
    ("打开数据", "ic_fluent_folder_open_regular", 16),
    ("Blender / 主页", "ic_fluent_cube_regular", 16),
    ("导出", "ic_fluent_arrow_export_regular", 16),
    ("设置", "ic_fluent_settings_regular", 18),
    ("深色主题", "ic_fluent_weather_moon_regular", 14),
    ("浅色主题", "ic_fluent_weather_sunny_regular", 14),
    ("截图样式", "ic_fluent_camera_regular", 16),
    ("重置 / 更新预览", "ic_fluent_arrow_reset_regular", 16),
    ("显示坐标", "ic_fluent_grid_regular", 16),
    ("图像翻转", "ic_fluent_flip_horizontal_regular", 16),
    ("Z 轴旋转", "ic_fluent_arrow_rotate_clockwise_regular", 16),
    ("折线路径选点", "ic_fluent_bezier_curve_square_regular", 16),
    ("裁剪", "ic_fluent_crop_regular", 16),
    ("裁空", "ic_fluent_eraser_regular", 16),
    ("专注画布", "ic_fluent_full_screen_maximize_regular", 16),
    ("恢复侧栏", "ic_fluent_full_screen_minimize_regular", 16),
    ("数据源", "ic_fluent_database_regular", 15),
    ("新建分组", "ic_fluent_folder_add_regular", 20),
    ("控制面板", "ic_fluent_wrench_screwdriver_filled", 16),
    ("积分 / 曲线", "ic_fluent_data_trending_filled", 16),
    ("路径切片页", "ic_fluent_bezier_curve_square_filled", 16),
    ("切片态密度页", "ic_fluent_table_stack_right_filled", 16),
    ("结果文档", "ic_fluent_document_data_filled", 16),
    ("插件列表", "ic_fluent_puzzle_piece_regular", 16),
)


def _scale_name(scale):
    return f"{scale:g}".replace(".", "_")


def _native_windows_scale():
    if sys.platform != "win32":
        return 1.0
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


def _image_bytes(image):
    return image.bits().asstring(image.sizeInBytes())


def _run_child(scale, native_scale, output_dir, result_path):
    os.environ["QT_QPA_PLATFORM"] = "windows"
    os.environ["QT_SCALE_FACTOR"] = str(scale / native_scale)

    from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

    configure_qt_high_dpi()
    configure_qt_plugin_path()

    from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, QSize, Qt
    from PyQt5.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap
    from PyQt5.QtSvg import QSvgRenderer
    from PyQt5.QtWidgets import QApplication, QStyleOption, QWidget

    app = QApplication([])

    from siui.core import SiGlobal
    from bandscope.ui import theme
    from bandscope.ui.fluent_icon import FluentIconEngine
    from bandscope.ui.ui_controls import ActionButton

    theme.set_theme("dark")
    theme.apply_app_font()
    actual_dpr = app.primaryScreen().devicePixelRatio()
    requests = []
    original_pixmap = FluentIconEngine.pixmap

    def traced_pixmap(self, size, mode, state):
        requests.append((size.width(), size.height(), int(mode), int(state)))
        return original_pixmap(self, size, mode, state)

    FluentIconEngine.pixmap = traced_pixmap
    host = QWidget()
    host.setAttribute(Qt.WA_DontShowOnScreen, True)
    host.resize(80, 48)
    host.show()

    def capture(name, logical_size, color, *, legacy=False, enabled=True, checked=False):
        button = ActionButton(host)
        button.setFixedSize(52, 34)
        button.setIconSize(QSize(logical_size, logical_size))
        button.setEnabled(enabled)
        button.setCheckable(checked)
        button.setChecked(checked)
        on_accent = color == theme.ACCENT_ON
        if legacy:
            button.setIcon(
                SiGlobal.siui.iconpack.toIcon(
                    name, QSize(logical_size, logical_size), color
                )
            )
        else:
            theme.set_button_icon(button, name, logical_size, on_accent=on_accent)
        theme.style_push_button(button, "primary" if on_accent else "quiet")
        button.show()
        app.processEvents()
        requests.clear()
        grabbed = button.grab().toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
        observed = list(requests)
        logical_button_size = (button.width(), button.height())
        button.hide()
        button.deleteLater()
        app.processEvents()
        return grabbed, observed, logical_button_size

    def icon_region(image, logical_button_size, logical_size, target):
        logical_x = (logical_button_size[0] - logical_size) // 2
        logical_y = (logical_button_size[1] - logical_size) // 2
        x = int(logical_x * actual_dpr + 0.5)
        y = int(logical_y * actual_dpr + 0.5)
        return image.copy(x, y, target, target)

    def reference(name, target, color, mode, background):
        image = QImage(target, target, QImage.Format_ARGB32_Premultiplied)
        image.fill(background)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if mode == QIcon.Normal:
            QSvgRenderer(SiGlobal.siui.iconpack.get(name, color)).render(painter)
        else:
            normal = SiGlobal.siui.iconpack.toPixmap(name, QSize(target, target), color)
            generated = app.style().generatedIconPixmap(mode, normal, QStyleOption())
            painter.drawPixmap(0, 0, generated)
        painter.end()
        return image

    def qt_reference(
        name, target, color, mode, logical_size, logical_button_size, image_size, background
    ):
        layer = QImage(image_size, QImage.Format_ARGB32_Premultiplied)
        layer.fill(Qt.transparent)
        pixmap = SiGlobal.siui.iconpack.toPixmap(name, QSize(target, target), color)
        if mode != QIcon.Normal:
            pixmap = app.style().generatedIconPixmap(mode, pixmap, QStyleOption())
        pixmap.setDevicePixelRatio(actual_dpr)
        painter = QPainter(layer)
        painter.scale(actual_dpr, actual_dpr)
        draw_size = int(target / actual_dpr)
        rect_x = (logical_button_size[0] - draw_size) // 2
        rect_y = (logical_button_size[1] - draw_size) // 2
        painter.drawPixmap(rect_x, rect_y, draw_size, draw_size, pixmap)
        painter.end()
        cropped = icon_region(layer, logical_button_size, logical_size, target)
        image = QImage(target, target, QImage.Format_ARGB32_Premultiplied)
        image.fill(background)
        painter = QPainter(image)
        painter.drawImage(0, 0, cropped)
        painter.end()
        return image

    def compose(entries, path, title):
        columns = 4
        cell_width = 240
        cell_height = 112
        rows = (len(entries) + columns - 1) // columns
        image = QImage(
            columns * cell_width,
            66 + rows * cell_height,
            QImage.Format_ARGB32_Premultiplied,
        )
        image.fill(QColor(theme.BG_1))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setPen(QColor(theme.TEXT_1))
        title_font = QFont(app.font())
        title_font.setPixelSize(19)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(18, 27, title)
        painter.setPen(QColor(theme.TEXT_2))
        painter.setFont(QFont(app.font()))
        painter.drawText(18, 49, "每格依次为旧版位图、新版引擎、同尺寸 SVG 参考；图像均按物理像素原尺寸绘制。")
        label_font = QFont(app.font())
        label_font.setPixelSize(12)
        caption_font = QFont(app.font())
        caption_font.setPixelSize(10)
        for index, entry in enumerate(entries):
            cell_x = (index % columns) * cell_width
            cell_y = 66 + (index // columns) * cell_height
            painter.setPen(QColor(theme.TEXT_1))
            painter.setFont(label_font)
            painter.drawText(cell_x + 10, cell_y + 17, entry["label"])
            painter.setPen(QColor(theme.TEXT_3))
            painter.drawText(cell_x + 174, cell_y + 17, f"{entry['target']} px")
            for column, (key, caption) in enumerate(
                (("legacy", "旧版"), ("current", "新版"), ("reference", "SVG"))
            ):
                x = cell_x + 12 + column * 72
                painter.drawImage(x, cell_y + 30, entry[key])
                painter.setFont(caption_font)
                painter.drawText(x, cell_y + 101, caption)
        painter.end()
        if not image.save(str(path)):
            raise RuntimeError(f"无法保存对照图：{path}")

    errors = []
    normal_cases = []
    comparison_entries = []
    if abs(actual_dpr - scale) > 1e-6:
        errors.append(f"实际 DPR {actual_dpr} 与目标 {scale} 不一致")

    for label, name, logical_size in ICON_SPECS:
        color = theme.TEXT_2
        legacy_image, _, legacy_button_size = capture(
            name, logical_size, color, legacy=True
        )
        current_image, observed, current_button_size = capture(name, logical_size, color)
        target = int(logical_size * actual_dpr + 0.5)
        expected_requests = (target, target)
        if not observed or any(item[:2] != expected_requests for item in observed):
            errors.append(f"{label} 的引擎请求尺寸错误：{observed}，期望 {expected_requests}")
        if legacy_button_size != current_button_size or legacy_image.size() != current_image.size():
            errors.append(f"{label} 的按钮布局尺寸发生变化")
        current = icon_region(current_image, current_button_size, logical_size, target)
        legacy = icon_region(legacy_image, legacy_button_size, logical_size, target)
        expected = reference(name, target, color, QIcon.Normal, current.pixelColor(0, 0))
        qt_expected = qt_reference(
            name,
            target,
            color,
            QIcon.Normal,
            logical_size,
            current_button_size,
            current_image.size(),
            current.pixelColor(0, 0),
        )
        matches = current.size() == expected.size() and _image_bytes(current) == _image_bytes(expected)
        matches_qt = (
            current.size() == qt_expected.size()
            and _image_bytes(current) == _image_bytes(qt_expected)
        )
        legacy_equal = _image_bytes(legacy_image) == _image_bytes(current_image)
        if not matches_qt or (scale == 1.0 and not matches):
            errors.append(f"{label} 的实际按钮图标与 {target}px SVG / Qt 参考不一致")
        if scale == 1.0 and not legacy_equal:
            errors.append(f"{label} 在 100% 下与旧版按钮逐像素不一致")
        normal_cases.append({
            "label": label,
            "name": name,
            "logical_size": logical_size,
            "target_pixels": target,
            "requests": observed,
            "matches_reference": matches,
            "matches_qt_target": matches_qt,
            "legacy_equals_current": legacy_equal,
        })
        comparison_entries.append({
            "label": label,
            "target": target,
            "legacy": legacy,
            "current": current,
            "reference": expected,
        })

    state_cases = []
    state_entries = []
    for label, name, logical_size, color, enabled, checked in (
        ("禁用按钮", "ic_fluent_arrow_export_regular", 16, theme.ACCENT_ON, False, False),
        ("勾选按钮", "ic_fluent_crop_regular", 16, theme.TEXT_2, True, True),
    ):
        legacy_image, _, legacy_button_size = capture(
            name, logical_size, color, legacy=True, enabled=enabled, checked=checked
        )
        current_image, observed, current_button_size = capture(
            name, logical_size, color, enabled=enabled, checked=checked
        )
        target = int(logical_size * actual_dpr + 0.5)
        current = icon_region(current_image, current_button_size, logical_size, target)
        legacy = icon_region(legacy_image, legacy_button_size, logical_size, target)
        mode = observed[-1][2] if observed else QIcon.Normal
        expected = reference(name, target, color, mode, current.pixelColor(0, 0))
        qt_expected = qt_reference(
            name,
            target,
            color,
            mode,
            logical_size,
            current_button_size,
            current_image.size(),
            current.pixelColor(0, 0),
        )
        matches = current.size() == expected.size() and _image_bytes(current) == _image_bytes(expected)
        matches_qt = (
            current.size() == qt_expected.size()
            and _image_bytes(current) == _image_bytes(qt_expected)
        )
        legacy_equal = _image_bytes(legacy_image) == _image_bytes(current_image)
        if not matches_qt or (scale == 1.0 and not matches):
            errors.append(f"{label} 状态与 {target}px SVG / Qt 参考不一致")
        if scale == 1.0 and not legacy_equal:
            errors.append(f"{label} 状态在 100% 下与旧版按钮逐像素不一致")
        state_cases.append({
            "label": label,
            "target_pixels": target,
            "requests": observed,
            "matches_reference": matches,
            "matches_qt_target": matches_qt,
            "legacy_equals_current": legacy_equal,
        })
        state_entries.append({
            "label": label,
            "target": target,
            "legacy": legacy,
            "current": current,
            "reference": expected,
        })

    suffix = _scale_name(scale)
    comparison_path = output_dir / f"fluent-button-comparison-{suffix}.png"
    states_path = output_dir / f"fluent-button-states-{suffix}.png"
    compose(
        comparison_entries,
        comparison_path,
        f"BandScope 24 个 Fluent 图标 · DPR {scale:g}",
    )
    compose(state_entries, states_path, f"BandScope 图标状态 · DPR {scale:g}")
    host.close()
    FluentIconEngine.pixmap = original_pixmap
    result = {
        "scale": scale,
        "native_scale": native_scale,
        "qt_scale_factor": scale / native_scale,
        "actual_dpr": actual_dpr,
        "qt_version": QT_VERSION_STR,
        "pyqt_version": PYQT_VERSION_STR,
        "platform": app.platformName(),
        "normal_cases": normal_cases,
        "state_cases": state_cases,
        "comparison": comparison_path.name,
        "states_image": states_path.name,
        "errors": errors,
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"DPR {scale:g}: {len(normal_cases)} icons, {len(state_cases)} states, "
        f"{'PASS' if not errors else 'FAIL'}",
        flush=True,
    )
    return 0 if not errors else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / ".local" / "outputs" / "icon-dpi-audit",
    )
    parser.add_argument("--child-scale", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--native-scale", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--result-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.child_scale is not None:
        return _run_child(
            args.child_scale,
            args.native_scale or _native_windows_scale(),
            args.output_dir,
            args.result_file,
        )

    native_scale = _native_windows_scale()
    reports = []
    log_dir = REPO_ROOT / ".local" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    for scale in SCALES:
        suffix = _scale_name(scale)
        result_path = args.output_dir / f"fluent-button-pixel-{suffix}.json"
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--output-dir",
            str(args.output_dir),
            "--child-scale",
            str(scale),
            "--native-scale",
            str(native_scale),
            "--result-file",
            str(result_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        (log_dir / f"fluent-icon-dpi-{suffix}.log").write_text(
            (completed.stdout or "") + (completed.stderr or ""), encoding="utf-8"
        )
        if not result_path.is_file():
            raise RuntimeError(f"DPR {scale} 验收未生成报告；退出码 {completed.returncode}")
        report = json.loads(result_path.read_text(encoding="utf-8"))
        reports.append(report)
        print(
            f"DPR {scale:g}: actual={report['actual_dpr']:g}, "
            f"icons={len(report['normal_cases'])}, errors={len(report['errors'])}",
            flush=True,
        )
        if completed.returncode != 0 or report["errors"]:
            raise AssertionError(f"DPR {scale} 验收失败：{report['errors']}")

    summary = {
        "status": "PASS",
        "platform": "windows",
        "native_scale": native_scale,
        "scales": [
            {
                "scale": report["scale"],
                "actual_dpr": report["actual_dpr"],
                "icon_count": len(report["normal_cases"]),
                "state_count": len(report["state_cases"]),
                "errors": report["errors"],
                "comparison": report["comparison"],
                "states_image": report["states_image"],
            }
            for report in reports
        ],
    }
    (args.output_dir / "fluent-button-pixel-report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    shutil.copyfile(
        args.output_dir / "fluent-button-comparison-2.png",
        args.output_dir / "fluent-button-comparison.png",
    )
    print(f"PASS: {args.output_dir / 'fluent-button-pixel-report.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
