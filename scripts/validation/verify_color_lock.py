# -*- coding: utf-8 -*-
"""色带锁定功能端到端验证：锁定 → 拖时间轴 → 解锁 → 再拖时间轴。

验证点（3D 主页 + 2D 切片结果页各验一遍）：
1. 未锁定时拖动时间轴，色带范围随当前帧数据重新归一化；
2. 点下顶部栏「锁定色带」后，VisualEngine 捕获当前数据范围，按钮文案/样式切换；
3. 锁定状态拖动时间轴，clim / level_info 保持不变；
4. 解锁后再次拖动时间轴，恢复按每帧数据自动归一化。

用法: .venv/Scripts/python.exe scripts/validation/verify_color_lock.py <npz路径> [--output-dir <目录>]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def log(msg):
    print(msg, flush=True)


def current_2d_clim(window):
    image = getattr(window.ax_2d, "_arpes_image", None)
    if image is None or getattr(window, "left_display_stack", None) is None:
        return None
    if window.left_display_stack.currentIndex() != 1:
        return None
    return tuple(float(v) for v in image.get_clim())


def volume_bw(window):
    info = getattr(window.volume_session, "level_info", None)
    if info is None:
        return None
    return (float(info["black_value"]), float(info["white_value"]))


def main():
    from scripts.validation._common import arguments
    args = arguments("verify_color_lock", dynamic=False)
    npz_path = str(args.data)
    out_dir = str(args.output_dir)
    os.makedirs(out_dir, exist_ok=True)

    def _excepthook(exc_type, exc, tb):
        import traceback
        traceback.print_exception(exc_type, exc, tb)
        log("[verify] FAILED")
        os._exit(1)

    sys.excepthook = _excepthook

    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app
    refactored_app.UpdateController = _NullUpdateController
    from bandscope.rendering.render_core import VisualEngine

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()

    state = {}
    slider = window.timeline_bar.slider_time

    def goto_frame(value, next_step, wait_ms=6000):
        slider.setValue(value)
        window.flush_time_slider_refresh()
        QTimer.singleShot(wait_ms, next_step)

    def step_load():
        log(f"[verify] loading {npz_path}")
        window.load_data(npz_path)
        assert window.core.raw_data is not None, "raw_data 为空，加载失败"
        log(f"[verify] loaded, shape={window.core.raw_data.shape}")
        QTimer.singleShot(12000, step_3d_frame0)

    # ---------- 3D 主页验证 ----------
    def step_3d_frame0():
        log(f"[verify] time slider range: {slider.minimum()}..{slider.maximum()}")
        state["bw0"] = volume_bw(window)
        log(f"[verify] 3d frame0 black/white: {state['bw0']}")
        goto_frame(slider.maximum(), step_3d_frame_last_unlocked)

    def step_3d_frame_last_unlocked():
        bw = volume_bw(window)
        log(f"[verify] 3d last-frame black/white (unlocked): {bw}")
        assert bw is not None and state["bw0"] is not None
        assert bw != state["bw0"], "未锁定时换帧后色带范围未变化，无法验证锁定效果"
        # 锁定
        window.btn_tb_lock.click()
        assert window.btn_tb_lock.isChecked(), "锁定按钮未进入选中态"
        assert VisualEngine.locked_data_range() is not None, "锁定后未记录范围"
        assert window.btn_tb_lock.attachment().text() == "色带已锁定", "按钮文案未切换"
        log(f"[verify] locked range: {VisualEngine.locked_data_range()}")
        QTimer.singleShot(6000, step_3d_locked_a)

    def step_3d_locked_a():
        state["bw_locked"] = volume_bw(window)
        log(f"[verify] 3d black/white right after lock: {state['bw_locked']}")
        goto_frame(0, step_3d_locked_b)

    def step_3d_locked_b():
        bw = volume_bw(window)
        log(f"[verify] 3d frame0 black/white (locked): {bw}")
        assert bw == state["bw_locked"], (
            f"锁定后 3D 色带范围发生变化: {state['bw_locked']} -> {bw}"
        )
        log("[verify] PASS: 3D locked colorband fixed across frames")
        # 解锁（停留在第 0 帧，切换按钮会立即触发一次自动归一化刷新）
        window.btn_tb_lock.click()
        assert not window.btn_tb_lock.isChecked()
        assert VisualEngine.locked_data_range() is None
        assert window.btn_tb_lock.attachment().text() == "锁定色带"
        QTimer.singleShot(6000, step_3d_unlocked)

    def step_3d_unlocked():
        bw = volume_bw(window)
        log(f"[verify] 3d frame0 black/white (unlocked again): {bw}")
        assert bw == state["bw0"], "解锁后色带范围未恢复按当前帧自动归一化"
        log("[verify] PASS: 3D unlock restored auto normalization")
        QTimer.singleShot(500, step_make_2d_page)

    # ---------- 2D 切片结果页验证 ----------
    def step_make_2d_page():
        """开启裁剪模式，把 E 上下限设为同一位置（零厚度切片），裁剪生成 2D 结果页。"""
        import numpy as np
        from bandscope.core.crop_model import CropSelection

        coords = window.core.coords
        z_vals = coords["E"]
        z_mid = float((z_vals[0] + z_vals[-1]) / 2.0)
        bounds = (
            float(np.min(coords["X"])), float(np.max(coords["X"])),
            float(np.min(coords["Y"])), float(np.max(coords["Y"])),
            z_mid, z_mid,
        )
        log(f"[verify] slice bounds for 2d page: {bounds}")
        window.btn_tb_crop.setChecked(True)
        window.crop_controller.set_selection(CropSelection("3d", ("X", "Y", "E"), bounds))
        window.on_cut()
        QTimer.singleShot(10000, step_2d_frame0)

    def step_2d_frame0():
        goto_frame(0, step_2d_frame0_done)

    def step_2d_frame0_done():
        state["clim0"] = current_2d_clim(window)
        log(f"[verify] 2d frame0 clim: {state['clim0']}")
        assert state["clim0"] is not None, "2D 切片页未激活或无图像"
        goto_frame(slider.maximum(), step_2d_last_unlocked)

    def step_2d_last_unlocked():
        clim = current_2d_clim(window)
        log(f"[verify] 2d last-frame clim (unlocked): {clim}")
        assert clim is not None and clim != state["clim0"], (
            "未锁定时换帧后 2D clim 未变化，无法验证锁定效果"
        )
        window.btn_tb_lock.click()
        assert window.btn_tb_lock.isChecked()
        assert VisualEngine.locked_data_range() is not None
        log(f"[verify] locked range (2d): {VisualEngine.locked_data_range()}")
        QTimer.singleShot(6000, step_2d_locked_a)

    def step_2d_locked_a():
        state["clim_locked"] = current_2d_clim(window)
        log(f"[verify] 2d clim right after lock: {state['clim_locked']}")
        goto_frame(0, step_2d_locked_b)

    def step_2d_locked_b():
        clim = current_2d_clim(window)
        log(f"[verify] 2d frame0 clim (locked): {clim}")
        assert clim == state["clim_locked"], (
            f"锁定后 2D clim 发生变化: {state['clim_locked']} -> {clim}"
        )
        log("[verify] PASS: 2D locked colorband fixed across frames")
        p = os.path.join(out_dir, "verify_locked_2d.png")
        window.grab().save(p)
        log(f"[verify] locked 2d window grab -> {p}")
        window.btn_tb_lock.click()
        assert not window.btn_tb_lock.isChecked()
        assert VisualEngine.locked_data_range() is None
        QTimer.singleShot(6000, step_2d_unlocked)

    def step_2d_unlocked():
        clim = current_2d_clim(window)
        log(f"[verify] 2d frame0 clim (unlocked again): {clim}")
        assert clim == state["clim0"], "解锁后 2D clim 未恢复按当前帧自动归一化"
        log("[verify] PASS: 2D unlock restored auto normalization")
        QTimer.singleShot(500, finish)

    def finish():
        window.close()
        app.quit()

    QTimer.singleShot(2000, step_load)
    exit_code = app.exec_()
    log(f"[verify] ALL PASSED (app exit code {exit_code})")


if __name__ == "__main__":
    main()
