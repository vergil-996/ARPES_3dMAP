# -*- coding: utf-8 -*-
"""2D 图像比例端到端验证：小窗与全屏下 kx–ky 面必须保持同一形状、同一 1:1 比例。

验证点：
1. E 轴积分结果（kx–ky 面）在小窗和全屏下每数据单位的像素数相同，图像形状不变；
2. 色条紧贴图像右缘、与图像等高，窗口变化后仍然贴合；
3. X 轴积分结果（kx–E 图）两轴单位不同，继续铺满画布并保持填满主轴位置；
4. 切到 1D 曲线页后比例释放，曲线重新铺满画布。

用法: .venv/Scripts/python.exe scripts/validation/verify_2d_aspect.py <npz路径> [--output-dir <目录>]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

RESULT = {"failures": []}


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def log(msg):
    print(msg, flush=True)


def check(label, ok, detail=""):
    log(f"  [{'ok ' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    if not ok:
        RESULT["failures"].append(label)


def measure(window):
    """图像框尺寸、两轴的每单位像素数、色条贴合情况；不在 2D 页时返回 None。"""
    axes = window.ax_2d
    image = getattr(axes, "_arpes_image", None)
    if image is None or window.left_display_stack.currentIndex() != 1:
        return None
    window.canvas_2d.draw()
    box = axes.bbox
    x0, x1 = axes.get_xlim()
    y0, y1 = axes.get_ylim()
    colorbar = getattr(axes, "_arpes_colorbar", None)
    cbox = colorbar.ax.bbox if colorbar is not None else None
    return {
        "image_w": box.width,
        "image_h": box.height,
        "scale_x": box.width / (x1 - x0),
        "scale_y": box.height / (y1 - y0),
        "data_aspect": (x1 - x0) / (y1 - y0),
        "cbar_gap": (cbox.x0 - box.x1) if cbox is not None else None,
        "cbar_dh": (cbox.height - box.height) if cbox is not None else None,
        "slot_w": axes.get_position(original=True).width * axes.figure.get_figwidth()
        * axes.figure.dpi,
    }


def describe(tag, m):
    log(
        f"  {tag}: image={m['image_w']:.0f}x{m['image_h']:.0f}px "
        f"px/unit x={m['scale_x']:.2f} y={m['scale_y']:.2f} "
        f"shape={m['image_w'] / m['image_h']:.3f} (data {m['data_aspect']:.3f}) "
        f"cbar gap={m['cbar_gap']:.1f}px dh={m['cbar_dh']:.1f}px"
    )


def resize_window(window, width, height):
    window.showNormal()
    window.resize(width, height)
    window.main_splitter.setSizes([width - 420, 400])
    window.canvas_2d.draw()


def main():
    from scripts.validation._common import arguments
    args = arguments("verify_2d_aspect", dynamic=False)
    npz_path = str(args.data)
    out_dir = str(args.output_dir)
    os.makedirs(out_dir, exist_ok=True)

    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app

    refactored_app.UpdateController = _NullUpdateController

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()

    small = (1180, 760)
    large = (1920, 1000)

    def step_load():
        log(f"[aspect] loading {npz_path}")
        window.load_data(npz_path)
        assert window.core.raw_data is not None, "raw_data 为空，加载失败"
        QTimer.singleShot(12000, step_small)

    def apply_axis_integral(axis_index, follow_up):
        """回到主页再改轴向：轴向下拉会联动刷新当前 2D 页，先离开它。"""
        window.left_workspace.activate_page(window.left_workspace.home_page_id)
        window.page_data.combo_ax.setCurrentIndex(axis_index)

        def click():
            window.page_data.btn_ax_apply.click()
            QTimer.singleShot(7000, follow_up)

        QTimer.singleShot(700, click)

    def step_small():
        resize_window(window, *small)
        # Z 轴（能量）积分 → kx–ky 面
        apply_axis_integral(2, measure_small)

    def measure_small():
        first = measure(window)
        assert first is not None, "2D 图像未渲染"
        describe("small ", first)
        window.grab().save(os.path.join(out_dir, "aspect_kxky_small.png"))
        RESULT["small"] = first

        resize_window(window, *large)
        QTimer.singleShot(2500, measure_large)

    def measure_large():
        second = measure(window)
        assert second is not None, "全屏 2D 图像未渲染"
        describe("large ", second)
        window.grab().save(os.path.join(out_dir, "aspect_kxky_large.png"))
        RESULT["large"] = second

        small_m, large_m = RESULT["small"], RESULT["large"]
        check(
            "kx–ky 两轴保持 1:1（小窗）",
            abs(small_m["scale_x"] - small_m["scale_y"]) < 0.02 * small_m["scale_x"],
        )
        check(
            "kx–ky 两轴保持 1:1（全屏）",
            abs(large_m["scale_x"] - large_m["scale_y"]) < 0.02 * large_m["scale_x"],
        )
        check(
            "图像形状不随窗口改变",
            abs(small_m["image_w"] / small_m["image_h"]
                - large_m["image_w"] / large_m["image_h"]) < 0.01,
            f"{small_m['image_w'] / small_m['image_h']:.3f} -> "
            f"{large_m['image_w'] / large_m['image_h']:.3f}",
        )
        for tag, m in (("小窗", small_m), ("全屏", large_m)):
            check(
                f"色条紧贴图像且等高（{tag}）",
                m["cbar_gap"] is not None
                and 0 < m["cbar_gap"] < 0.06 * m["image_w"]
                and abs(m["cbar_dh"]) < 1.5,
                f"gap={m['cbar_gap']:.1f}px dh={m['cbar_dh']:.1f}px",
            )
        QTimer.singleShot(300, step_ek)

    def step_ek():
        # X 轴积分 → kx–E 图：两轴单位不同，继续铺满画布
        apply_axis_integral(0, measure_ek)

    def measure_ek():
        m = measure(window)
        assert m is not None, "E–k 图未渲染"
        describe("kx-E  ", m)
        window.grab().save(os.path.join(out_dir, "aspect_ek_large.png"))
        check(
            "E–k 图继续铺满主轴位置",
            abs(m["image_w"] - m["slot_w"]) < 2.0,
            f"image={m['image_w']:.0f}px slot={m['slot_w']:.0f}px",
        )
        check(
            "E–k 图色条紧贴图像",
            m["cbar_gap"] is not None and 0 < m["cbar_gap"] < 0.06 * m["image_w"],
            f"gap={m['cbar_gap']:.1f}px dh={m['cbar_dh']:.1f}px",
        )
        QTimer.singleShot(300, step_1d)

    def step_1d():
        # 能级态密度（1D 曲线）：比例必须释放，曲线重新铺满画布
        window.left_workspace.activate_page(window.left_workspace.home_page_id)
        combo = window.page_data.combo_other
        index = combo.findText("能级态密度")
        assert index >= 0, "找不到「能级态密度」选项"
        combo.setCurrentIndex(index)

        def click():
            window.page_data.btn_other_apply.click()
            QTimer.singleShot(8000, measure_1d)

        QTimer.singleShot(700, click)

    def measure_1d():
        axes = window.ax_2d
        window.canvas_2d.draw()
        aspect = axes.get_aspect()
        box = axes.bbox
        window.grab().save(os.path.join(out_dir, "aspect_curve_large.png"))
        check("1D 曲线页恢复自由比例", aspect == "auto", f"aspect={aspect}")
        check(
            "1D 曲线已绘制且未被锁成方形",
            bool(axes.lines) and abs(box.width - box.height) > 20.0,
            f"lines={len(axes.lines)} box={box.width:.0f}x{box.height:.0f}px",
        )
        finish()

    def finish():
        window.close()
        app.quit()

    QTimer.singleShot(2000, step_load)
    app.exec_()

    failures = RESULT["failures"]
    log(f"[aspect] {'PASS' if not failures else 'FAIL: ' + '; '.join(failures)}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
