"""Native-window crop handle gestures on the default real ARPES dataset."""
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.validation._common import arguments


def main():
    args = arguments("interactive_crop_box", default_data=".local/data/WSe2_step.npz")
    import numpy as np
    from PyQt5.QtCore import QEvent, QPointF, Qt, QTimer
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtWidgets import QApplication
    import bandscope.app.refactored_app as app_module

    class NoUpdates:
        def __init__(self, *args, **kwargs):
            pass

        def check_automatically(self):
            pass

        def shutdown(self, **kwargs):
            pass

    app_module.UpdateController = NoUpdates
    app = QApplication([])
    window = app_module.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    state = {"step": 0, "settle": 0}
    checks = []

    def check(label, condition):
        assert condition, label
        checks.append(label)
        print(f"[PASS] {label}", flush=True)

    def mouse(kind, xy, button, buttons):
        canvas = window.canvas_2d
        ratio = canvas.devicePixelRatioF()
        height = canvas.get_width_height(physical=True)[1]
        x, y = window.ax_2d.transData.transform(xy)
        pos = QPointF(x / ratio, (height - y) / ratio)
        global_pos = QPointF(canvas.mapToGlobal(pos.toPoint()))
        QApplication.sendEvent(canvas, QMouseEvent(
            kind, pos, global_pos, button, buttons, Qt.NoModifier))

    def gesture(start, end):
        mouse(QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
        for fraction in np.linspace(0.1, 1, 10):
            point = np.asarray(start) + fraction * (np.asarray(end) - start)
            mouse(QEvent.MouseMove, point, Qt.NoButton, Qt.LeftButton)
        mouse(QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)

    def handles_painted():
        from PyQt5.QtGui import QColor
        from bandscope.ui import theme
        box = window.axis_crop_selector
        centers = list(zip(*box.corners)) + list(zip(*box.edge_centers)) + [box.center]
        image = window.canvas_2d.grab().toImage()
        color = QColor(theme.ACCENT)
        target = (color.red(), color.green(), color.blue())
        for point in centers:
            x, y = window.ax_2d.transData.transform(point)
            x, y = round(x), image.height() - round(y)
            if not (5 < x < image.width() - 5 and 5 < y < image.height() - 5):
                return False
            count = 0
            for px in range(x - 4, x + 5):
                for py in range(y - 4, y + 5):
                    actual = image.pixelColor(px, py)
                    values = (actual.red(), actual.green(), actual.blue())
                    count += max(abs(a - b) for a, b in zip(values, target)) < 5
            if count <= 5:
                return False
        return True

    def snapshot(name):
        # Capture the current paint buffer. An explicit draw would hide bugs
        # where a tooltip hover has erased the animated crop overlay.
        app.processEvents()
        window.canvas_2d.grab().save(str(args.output_dir / f"{name}_canvas.png"))
        window.grab().save(str(args.output_dir / f"{name}_window.png"))
        window.crop_controller.popup.grab().save(str(args.output_dir / f"{name}_popup.png"))

    def tick():
        try:
            if state["settle"]:
                state["settle"] -= 1
                return
            if not window._render_exact_ready:
                return
            step = state["step"]
            controller = window.crop_controller
            if step == 0:
                check("3D source ready", window.current_render_context["view"] == "3d")
                window.btn_tb_crop.setChecked(True)
                check("3D interactive box retained", bool(window.plotter.box_widgets))
                selection = controller.selection
                bounds = list(selection.bounds)
                bounds[4] = bounds[5] = window.core.coords["E"][len(window.core.coords["E"]) // 2]
                controller.set_selection(replace(selection, bounds=tuple(bounds)))
                window.on_cut()
            elif step == 1:
                check("collapsed slice is 2D", window.current_render_context["view"] == "2d")
                box = window.axis_crop_selector
                check("initial rectangle and handles visible", all(a.get_visible() for a in box.artists))
                xlim, ylim = sorted(window.ax_2d.get_xlim()), sorted(window.ax_2d.get_ylim())
                x0, x1, y0, y1 = box.extents
                check("initial rectangle leaves room around nine handles",
                      xlim[0] < x0 < x1 < xlim[1] and ylim[0] < y0 < y1 < ylim[1]
                      and handles_painted())
                snapshot("initial")
                before = controller.selection.bounds
                x0, x1, y0, y1 = before
                end = (x0 + (x1 - x0) * .2, y0 + (y1 - y0) * .2)
                gesture((x0, y0), end)
                after = controller.selection.bounds
                check("Qt corner drag changes draft", after[0] > x0 and after[2] > y0)
                check("release keeps selector instance", window.axis_crop_selector is box)
                check("popup follows mouse draft", np.allclose(
                    controller.popup.read_bounds(4), after))
                app.processEvents()
                check("nine bright handles remain painted after release", handles_painted())
                hover = (xlim[1] - .03 * (xlim[1] - xlim[0]),
                         ylim[0] + .03 * (ylim[1] - ylim[0]))
                mouse(QEvent.MouseMove, hover, Qt.NoButton, Qt.NoButton)
                check("tooltip hover preserves nine handles", handles_painted())
                QApplication.sendEvent(window.canvas_2d, QEvent(QEvent.Leave))
                check("pointer leaving canvas preserves nine handles", handles_painted())
                snapshot("resized")
                x0, x1, y0, y1 = after
                center = ((x0 + x1) / 2, (y0 + y1) / 2)
                gesture(center, (center[0] - .05 * (x1 - x0), center[1] - .05 * (y1 - y0)))
                moved = controller.selection.bounds
                check("interior drag translates rectangle", not np.allclose(after, moved))
                check("translation preserves width and height", np.allclose(
                    np.diff(np.asarray(after).reshape(2, 2)),
                    np.diff(np.asarray(moved).reshape(2, 2))))
                # The margin left by the resized draft lies inside the plot.
                gesture((before[0] + .01 * (before[1] - before[0]),
                         before[2] + .01 * (before[3] - before[2])), center)
                check("outside drag preserves draft", np.allclose(controller.selection.bounds, moved))
                state["draft"] = moved
                window.global_refresh()
            elif step == 2:
                check("full refresh restores saved rectangle", np.allclose(
                    window.axis_crop_selector.extents, state["draft"]))
                box = window.axis_crop_selector
                x0, x1, y0, y1 = controller.selection.bounds
                numeric = (x0 + .02 * (x1 - x0), x1, y0, y1)
                for edit, value in zip(controller.popup.edits, numeric):
                    edit.setText(str(value))
                check("numeric commit accepted", controller.commit_edits())
                check("numeric edit updates existing rectangle", window.axis_crop_selector is box
                      and np.allclose(box.extents, numeric))
                state["draft"] = numeric
                window.timeline_bar.switch_flip.setChecked(True)
                window.global_refresh()
            elif step == 3:
                check("E flip retains physical rectangle", np.allclose(
                    window.axis_crop_selector.extents, state["draft"]))
                state["source"] = window.left_workspace.current_spec().page_id
                window.on_cut()
            elif step == 4:
                check("apply creates cropped result page", window.left_workspace.current_spec().page_id != state["source"])
                check("cropped result retains active handles", window.axis_crop_selector.get_visible())
                snapshot("cropped")
                window.btn_tb_erase.setChecked(True)
                selection = controller.selection
                x0, x1, y0, y1 = selection.bounds
                controller.set_selection(replace(selection, bounds=(
                    x0 + .3 * (x1 - x0), x0 + .6 * (x1 - x0),
                    y0 + .3 * (y1 - y0), y0 + .6 * (y1 - y0))))
                box = window.axis_crop_selector
                gesture((box.extents[0], box.extents[2]), (
                    x0 + .25 * (x1 - x0), y0 + .25 * (y1 - y0)))
                check("erase mode uses same interactive handles", controller.selection.operation == "erase"
                      and window.axis_crop_selector is box)
                window.on_cut()
            else:
                check("crop then erase leaves masked data", np.isnan(window.current_render_context["data"]).any())
                snapshot("erased")
                window.btn_tb_erase.setChecked(False)
                check("mode off removes rectangle", window.axis_crop_selector is None)
                print(f"[RESULT] {len(checks)} checks passed", flush=True)
                timer.stop()
                window.close()
                app.exit(0)
                return
            state["step"] += 1
            state["settle"] = 6
        except Exception:
            import traceback
            traceback.print_exc()
            timer.stop()
            window.close()
            app.exit(1)

    timer = QTimer()
    timer.timeout.connect(tick)
    window.load_data(str(args.data))
    timer.start(250)
    QTimer.singleShot(90000, lambda: app.exit(2))
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
