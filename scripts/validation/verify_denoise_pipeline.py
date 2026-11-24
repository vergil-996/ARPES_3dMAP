"""Reproducible Qt visual acceptance for the immediate denoise pipeline.

Run from the repository root:
    .venv/Scripts/python.exe scripts/validation/verify_denoise_pipeline.py

The default Qt platform is offscreen. Set QT_QPA_PLATFORM explicitly for a
native desktop run. Artifacts go to .local/outputs/denoise-pipeline/.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import traceback

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.support.environment import isolate_runtime

isolate_runtime()

from PyQt5.QtCore import QCoreApplication, QPoint, QTimer, Qt
from PyQt5.QtGui import QColor, QImage, QPainter, QPixmap, qGray
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel, QMenu, QVBoxLayout, QWidget

from bandscope.processing import denoise_config as config
from bandscope.ui.denoise_pipeline import DenoisePipelineWidget
from bandscope.ui.page_render_control import RenderControlPage
import bandscope.ui.theme as theme


class Acceptance:
    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.failures = []
        self.report = {
            "script": str(Path(__file__).resolve()),
            "qt_platform": os.environ.get("QT_QPA_PLATFORM", "offscreen"),
            "scope": "Real DenoisePipelineWidget and RenderControlPage; no ARPES data required.",
            "checks": [],
            "warnings": [
                "Validation cleanup explicitly disconnects each temporary RenderControlPage pipeline from theme.changed before deleteLater; without this cleanup, a deleted widget wrapper received a later theme callback during development of this script.",
            ],
            "interactions": [],
            "captures": [],
            "unexecuted": [
                "Real pointer-driven drag-and-drop reordering was not executed; programmatic QTest does not validate native QDrag movement and drop coordinates. Menu reorder was executed instead.",
                "Running/applied/error are visual states supplied through set_execution_state; backend denoise computation and numerical output quality were not executed by this visual script.",
                "Native desktop window-manager placement, scaling on multiple monitors, and physical mouse/menu rendering were not executed in offscreen mode.",
                "Maintainer human visual review is still required; screenshot generation alone is not a visual-quality approval.",
            ],
        }

    def require(self, name, condition, detail=""):
        entry = {"name": name, "passed": bool(condition), "detail": detail}
        self.report["checks"].append(entry)
        print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
        if not condition:
            self.failures.append(name)
            raise AssertionError(f"{name}: {detail}")

    def interaction(self, action, mechanism, detail=""):
        self.report["interactions"].append({
            "action": action,
            "mechanism": mechanism,
            "detail": detail,
            "executed": True,
        })

    def capture(self, widget, filename, theme_name, scenario, content_width=None, background=None):
        settle()
        pixmap = widget.grab()
        self.require(f"{filename} can be grabbed", not pixmap.isNull())
        if background is not None:
            composed = QPixmap(pixmap.size())
            composed.setDevicePixelRatio(pixmap.devicePixelRatio())
            composed.fill(QColor(background))
            painter = QPainter(composed)
            painter.drawPixmap(0, 0, pixmap)
            painter.end()
            pixmap = composed
        path = self.output_dir / filename
        self.require(f"{filename} saves successfully", pixmap.save(str(path)), str(path))
        image = QImage(str(path))
        size = path.stat().st_size if path.is_file() else 0
        self.require(
            f"{filename} exists, is non-empty, and decodes as PNG",
            path.is_file() and size > 0 and not image.isNull(),
            f"bytes={size}",
        )
        entry = {
            "file": str(path.resolve()),
            "theme": theme_name,
            "scenario": scenario,
            "image_size": [image.width(), image.height()],
            "widget_size": [widget.width(), widget.height()],
            "bytes": size,
        }
        if content_width is not None:
            entry["pipeline_content_width"] = content_width
        self.report["captures"].append(entry)
        print(f"[SHOT] {path.resolve()}", flush=True)


def settle(milliseconds=100):
    QApplication.processEvents()
    if milliseconds:
        QTest.qWait(milliseconds)
    QApplication.processEvents()


def set_theme(name):
    theme.set_theme(name)
    settle(60)


def node_state(widget):
    return widget.export_state()["nodes"]


def node_ids(widget):
    return [node["id"] for node in node_state(widget)]


def close_new_dialogs(widget, previous_ids):
    for node_id, dialog in list(widget._parameter_dialogs.items()):
        if node_id not in previous_ids:
            dialog.close()
    settle()


def add_method(acceptance, widget, name):
    previous_ids = set(node_ids(widget))
    previous_dialogs = set(widget._parameter_dialogs)
    widget.add_button.click()
    settle()
    popup = widget.method_popup
    acceptance.require(f"Add popup opens for {name}", popup.isVisible())
    choice = next((item for item in popup.choices if item.name == name), None)
    acceptance.require(f"Real method choice exists for {name}", choice is not None)
    QTest.mouseClick(choice, Qt.LeftButton, pos=choice.rect().center())
    settle()
    created = [node_id for node_id in node_ids(widget) if node_id not in previous_ids]
    acceptance.require(f"{name} is added through its real choice", len(created) == 1)
    acceptance.interaction(
        f"add:{name}",
        "add_button.click -> visible _MethodChoice QTest.mouseClick",
        f"node_id={created[0]}",
    )
    close_new_dialogs(widget, previous_dialogs)
    return created[0]


def trigger_node_action(acceptance, widget, node_id, action_text):
    card = widget._node_cards[node_id]
    outcome = {"triggered": False, "error": ""}

    def visible_menu():
        active = QApplication.activePopupWidget()
        if isinstance(active, QMenu):
            return active
        menus = [item for item in QApplication.topLevelWidgets() if isinstance(item, QMenu) and item.isVisible()]
        return menus[-1] if menus else None

    def trigger():
        menu = visible_menu()
        if menu is None:
            outcome["error"] = "No active or visible QMenu"
            return
        action = next((item for item in menu.actions() if item.text() == action_text), None)
        if action is None:
            outcome["error"] = f"Action not found: {[item.text() for item in menu.actions()]}"
            menu.close()
            return
        outcome["triggered"] = True
        action.trigger()
        menu.close()

    def unblock_menu():
        menu = visible_menu()
        if menu is not None:
            outcome["error"] = outcome["error"] or "Timed out waiting for menu action"
            menu.close()

    guard = QTimer(widget)
    guard.setSingleShot(True)
    guard.setInterval(3000)
    guard.timeout.connect(unblock_menu)
    QTimer.singleShot(100, trigger)
    guard.start()
    QTest.mouseClick(card.more, Qt.LeftButton, pos=card.more.rect().center())
    guard.stop()
    guard.deleteLater()
    settle()
    acceptance.require(
        f"Node menu action executes: {action_text}",
        outcome["triggered"] and not outcome["error"],
        outcome["error"],
    )
    acceptance.interaction(
        f"node-menu:{action_text}",
        "QTest.mouseClick(node more button) -> active QMenu action.trigger",
        f"node_id={node_id}",
    )


def open_parameters(acceptance, widget, node_id):
    card = widget._node_cards[node_id]
    point = QPoint(max(20, card.width() - 45), max(20, card.height() - 12))
    QTest.mouseClick(card, Qt.LeftButton, pos=point)
    settle()
    dialog = widget._parameter_dialogs.get(node_id)
    acceptance.require("Card click opens a real parameter dialog", dialog is not None and dialog.isVisible())
    acceptance.interaction("open-parameters", "QTest.mouseClick(node card)", f"node_id={node_id}")
    return dialog


def edit_numeric(acceptance, widget, dialog, key, value, mode):
    control = dialog._controls[key]
    previous_commits = getattr(widget, "_acceptance_commit_count", 0)
    line_edit = control.lineEdit()
    line_edit.setFocus(Qt.OtherFocusReason)
    line_edit.selectAll()
    QTest.keyClicks(line_edit, str(value))
    if mode == "editingFinished":
        QTest.keyClick(line_edit, Qt.Key_Return)
        settle(80)
    elif mode == "debounce":
        settle(760)
    else:
        raise ValueError(mode)
    actual_commits = getattr(widget, "_acceptance_commit_count", 0)
    acceptance.require(
        f"Numeric edit commits via {mode}: {key}",
        actual_commits > previous_commits and abs(float(control.value()) - float(value)) < 1e-6,
        f"value={control.value()}, commits={previous_commits}->{actual_commits}",
    )
    acceptance.interaction(
        f"numeric:{key}",
        f"QTest.keyClicks on real spin-box lineEdit; {mode}",
        f"value={value}",
    )


def choose_option(acceptance, widget, dialog, key, value):
    control = dialog._controls[key]
    previous_commits = getattr(widget, "_acceptance_commit_count", 0)
    index = control.findData(value)
    acceptance.require(f"Option {key}={value} exists", index >= 0)
    control.setCurrentIndex(index)
    settle(80)
    actual_commits = getattr(widget, "_acceptance_commit_count", 0)
    acceptance.require(
        f"Option commits immediately: {key}",
        actual_commits > previous_commits and control.currentData() == value,
    )
    acceptance.interaction(
        f"option:{key}",
        "real QComboBox.setCurrentIndex",
        f"value={value}; immediate commit observed",
    )


def resize_stage(stage, widget, content_width):
    widget.setFixedWidth(content_width)
    stage.resize(content_width + 24, max(240, widget.sizeHint().height() + 24))
    settle()
    stage.resize(content_width + 24, max(240, widget.sizeHint().height() + 24))
    settle()
    acceptance_width = widget.width()
    if acceptance_width != content_width:
        raise AssertionError(f"Pipeline width {acceptance_width} != requested {content_width}")


def capture_render_page(acceptance, state, theme_name, group_width, filename):
    set_theme(theme_name)
    page = RenderControlPage()
    try:
        page.denoise_pipeline.restore_state(state)
        page.set_denoise_execution_state("applied")
        page.resize(group_width + page.SECTION_MARGIN * 2, 700)
        page.show()
        settle(160)
        group = page.denoise_pipeline.parentWidget()
        while group is not None and group.objectName() != "CardGroup":
            group = group.parentWidget()
        acceptance.require(f"RenderControlPage {theme_name} CardGroup found", group is not None)
        for _ in range(3):
            width_correction = group_width - group.width()
            if width_correction == 0:
                break
            page.resize(page.width() + width_correction, page.height())
            settle(100)
        page.relayout_scroll_content()
        settle(160)
        expected_content_width = min(page.MAX_SLIDER_BLOCK_WIDTH, max(page.MIN_SLIDER_BLOCK_WIDTH, group_width - 30))
        acceptance.require(
            f"RenderControlPage {theme_name} group width {group_width}",
            group.width() == group_width,
            f"actual={group.width()}",
        )
        acceptance.require(
            f"RenderControlPage {theme_name} pipeline width at group {group_width}",
            page.denoise_pipeline.width() == expected_content_width,
            f"actual={page.denoise_pipeline.width()}, expected={expected_content_width}",
        )
        acceptance.capture(
            group,
            filename,
            theme_name,
            f"Real RenderControlPage denoise CardGroup; group width {group_width}px",
            page.denoise_pipeline.width(),
            background=theme.BG_1,
        )
    finally:
        theme.changed.disconnect(page.denoise_pipeline.apply_theme)
        page.close()
        page.deleteLater()
        settle()


def verify_text_rendering(acceptance):
    probe = QLabel("BandScope 高斯平滑")
    probe.setStyleSheet("background: white; color: black; font-size: 18px;")
    probe.resize(300, 60)
    probe.show()
    settle()
    image = probe.grab().toImage().convertToFormat(QImage.Format_RGB32)
    dark_pixels = sum(
        qGray(image.pixel(x, y)) < 128
        for y in range(image.height())
        for x in range(image.width())
    )
    probe.close()
    acceptance.require(
        "Qt platform renders Chinese and Latin text",
        dark_pixels > 20,
        f"dark_pixels={dark_pixels}; use QT_QPA_PLATFORM=windows when offscreen font output is blank",
    )


def run(acceptance):
    if QApplication.instance() is None and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        os.environ["QT_SCALE_FACTOR"] = "1"
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_FONT_DPI"] = "96"
        QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, False)
    app = QApplication.instance() or QApplication([])
    screen = app.primaryScreen()
    acceptance.report["qt_platform"] = app.platformName()
    acceptance.report["device_pixel_ratio"] = screen.devicePixelRatio() if screen is not None else None
    acceptance.report["available_screen_geometry"] = list(screen.availableGeometry().getRect()) if screen is not None else None
    if app.platformName() != "offscreen":
        acceptance.report["unexecuted"][2] = "Multi-monitor positioning, non-default display scaling, and physical pointer-driven menu use were not executed; this run used native Qt windows with programmatic QTest input."
    theme.apply_app_font()
    set_theme("light")
    verify_text_rendering(acceptance)

    stage = QWidget()
    stage.setObjectName("denoise_validation_stage")
    layout = QVBoxLayout(stage)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(0)
    widget = DenoisePipelineWidget(stage)
    layout.addWidget(widget, 0, Qt.AlignTop)
    widget._acceptance_commit_count = 0
    widget._acceptance_edit_count = 0
    widget.pipeline_committed.connect(lambda: setattr(widget, "_acceptance_commit_count", widget._acceptance_commit_count + 1))
    widget.pipeline_edited.connect(lambda: setattr(widget, "_acceptance_edit_count", widget._acceptance_edit_count + 1))
    stage.show()

    try:
        resize_stage(stage, widget, 252)
        acceptance.require("Initial pipeline is empty", node_ids(widget) == [])
        acceptance.capture(
            stage,
            "01-light-narrow-252-empty.png",
            "light",
            "Empty pipeline at 252px content width",
            widget.width(),
        )

        widget.add_button.click()
        settle()
        popup = widget.method_popup
        acceptance.require("Algorithm popup uses two columns", popup._columns == 2, f"columns={popup._columns}")
        acceptance.capture(
            popup,
            "02-light-method-popup-two-columns.png",
            "light",
            "Real add-algorithm popup with six choices in two columns",
        )
        popup.close()
        settle()
        acceptance.interaction("open-method-popup", "real add_button.click", "two-column popup captured and closed")

        gaussian_id = add_method(acceptance, widget, config.GAUSSIAN_METHOD_NAME)
        wavelet_id = add_method(acceptance, widget, config.WAVELET_METHOD_NAME)
        add_method(acceptance, widget, config.MEDIAN_METHOD_NAME)
        temporary_id = add_method(acceptance, widget, config.LOW_RANK_METHOD_NAME)
        acceptance.require("Four different algorithms can coexist", len(set(node["label"] for node in node_state(widget))) == 4)

        before_remove = set(node_ids(widget))
        trigger_node_action(acceptance, widget, temporary_id, "移除节点")
        acceptance.require(
            "Delete removes exactly the selected node",
            set(node_ids(widget)) == before_remove - {temporary_id},
        )

        before_duplicate = set(node_ids(widget))
        trigger_node_action(acceptance, widget, gaussian_id, "复制节点")
        duplicates = [node_id for node_id in node_ids(widget) if node_id not in before_duplicate]
        acceptance.require("Duplicate creates one independent node", len(duplicates) == 1)
        duplicate_id = duplicates[0]
        duplicate_node = next(node for node in node_state(widget) if node["id"] == duplicate_id)
        acceptance.require("Repeated algorithm has a distinct identity", duplicate_id != gaussian_id and duplicate_node["label"] == config.GAUSSIAN_METHOD_NAME)

        trigger_node_action(acceptance, widget, duplicate_id, "向后移动")
        trigger_node_action(acceptance, widget, duplicate_id, "向后移动")
        acceptance.require(
            "Menu reorder moves the duplicate to the end",
            node_ids(widget)[-1] == duplicate_id,
            f"order={node_ids(widget)}",
        )

        gaussian_dialog = open_parameters(acceptance, widget, duplicate_id)
        edit_numeric(acceptance, widget, gaussian_dialog, "sigma_kx", 2.4, "editingFinished")
        edit_numeric(acceptance, widget, gaussian_dialog, "sigma_ky", 1.6, "editingFinished")
        edit_numeric(acceptance, widget, gaussian_dialog, "sigma_e", 0.4, "editingFinished")
        first_gaussian = next(node for node in node_state(widget) if node["id"] == gaussian_id)
        repeated_gaussian = next(node for node in node_state(widget) if node["id"] == duplicate_id)
        acceptance.require(
            "Repeated Gaussian nodes retain independent parameters",
            first_gaussian["params"]["sigma_kx"] == 1.0 and repeated_gaussian["params"]["sigma_kx"] == 2.4,
        )

        wavelet_dialog = open_parameters(acceptance, widget, wavelet_id)
        choose_option(acceptance, widget, wavelet_dialog, "wavelet", "sym4")
        choose_option(acceptance, widget, wavelet_dialog, "threshold_rule", "sure")
        choose_option(acceptance, widget, wavelet_dialog, "threshold_mode", "hard")
        edit_numeric(acceptance, widget, wavelet_dialog, "strength", 0.8, "debounce")

        set_theme("light")
        widget.set_execution_state("applied")
        gaussian_dialog.set_auto_status("applied")
        acceptance.capture(
            gaussian_dialog,
            "03-light-gaussian-independent-parameters.png",
            "light",
            "Repeated Gaussian node with independently edited parameters; editingFinished commits",
        )
        gaussian_dialog.close()

        set_theme("dark")
        wavelet_dialog.set_auto_status("applied")
        acceptance.capture(
            wavelet_dialog,
            "04-dark-wavelet-parameters.png",
            "dark",
            "Wavelet dialog with three option commits and 600ms numeric debounce",
        )
        wavelet_dialog.close()

        resize_stage(stage, widget, 252)
        widget.set_execution_state("applied")
        set_theme("light")
        widget.set_execution_state("applied")
        acceptance.capture(
            stage,
            "05-light-narrow-252-pipeline-applied.png",
            "light",
            "Four nodes, three algorithms, one repeated Gaussian; applied state",
            widget.width(),
        )

        set_theme("dark")
        resize_stage(stage, widget, 470)
        widget.set_execution_state("running")
        acceptance.capture(
            stage,
            "06-dark-wide-470-pipeline-running.png",
            "dark",
            "Wide four-node pipeline with running indicators",
            widget.width(),
        )

        resize_stage(stage, widget, 252)
        error_message = "小波分解层数超出当前数据范围；参数已保留，请调整层数后重试。"
        widget.set_execution_state("error", error_message)
        acceptance.require("Error state exposes its detail panel", widget.error_panel.isVisible() and widget.error_label.text() == error_message)
        acceptance.capture(
            stage,
            "07-dark-narrow-252-pipeline-error.png",
            "dark",
            "Narrow pipeline with error state and wrapped diagnostic",
            widget.width(),
        )

        saved_state = widget.export_state()
        capture_render_page(
            acceptance,
            saved_state,
            "light",
            252,
            "08-render-page-light-narrow-group-252.png",
        )
        capture_render_page(
            acceptance,
            saved_state,
            "dark",
            470,
            "09-render-page-dark-wide-group-470.png",
        )
        acceptance.interaction(
            "execution-state-gallery",
            "DenoisePipelineWidget.set_execution_state",
            "applied, running, and error visuals captured; no backend claimed",
        )
    finally:
        widget._close_all_dialogs()
        widget.method_popup.close()
        stage.close()
        settle()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / ".local" / "outputs" / "denoise-pipeline",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    acceptance = Acceptance(args.output_dir)
    exit_code = 0
    try:
        run(acceptance)
    except Exception as exc:
        if not acceptance.failures:
            acceptance.failures.append(f"Unhandled exception: {exc!r}")
        acceptance.report["exception"] = traceback.format_exc()
        print(acceptance.report["exception"], flush=True)
        exit_code = 1
    finally:
        acceptance.report["passed"] = not acceptance.failures
        acceptance.report["failures"] = acceptance.failures
        report_path = args.output_dir / "report.json"
        report_path.write_text(
            json.dumps(acceptance.report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[REPORT] {report_path.resolve()}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
