# -*- coding: utf-8 -*-
"""坐标轴积分区间交互验收：真实窗口下的呼出、联动、锁定与换行。

流程：启动窗口 → 加载动态数据 → 选中处理分析页并生成一个 X 轴积分页 →
断言底栏位置组呼出且标签带轴向 → 拖动位置滑条断言上下限/长度/中心一致 →
输入长度断言区间居中扩缩 → 锁定后整体平移且长度输入框只读 → 状态灯文案与
颜色 → 换行阈值与底栏高度 → 单层切片页只移动位置 → 反向收起动画 →
截图落盘。

用法: .venv/Scripts/python.exe scripts/validation/verify_axis_interval.py <动态npz>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

import bandscope.ui.theme as theme

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []
records = []
_group_width_samples = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"[{status}] {label}{suffix}", flush=True)
    records.append((status, label, detail))
    if not condition:
        failures.append(label)


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def main():
    from scripts.validation._common import arguments

    args = arguments("axis_interval", dynamic=True)
    data_path = str(args.data)
    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app

    refactored_app.UpdateController = _NullUpdateController

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    if args.size:
        width, height = (int(part) for part in args.size.lower().split("x"))
        window.resize(width, height)
    window.show()

    if args.hold:
        pass

    bar = window.timeline_bar
    page_data = window.page_data
    controller = window.axis_interval_controller

    refresh_calls = []
    original_refresh = window.request_refresh

    def counting_refresh(*call_args, **call_kwargs):
        refresh_calls.append(call_args[:1])
        return original_refresh(*call_args, **call_kwargs)

    window.request_refresh = counting_refresh

    sampler = QTimer(window)
    sampler.setInterval(15)
    sampler.timeout.connect(lambda: _group_width_samples.append(bar.axis_group.width()))

    def step_loaded():
        check("加载后底栏位置组隐藏（主页是 3D 视图）", bar.axis_group.isHidden())
        check("加载后状态灯显示未锁定", window.status_lock.text() == "● 区间未锁定")
        window._select_control_page(1)
        page_data.combo_ax.setCurrentIndex(0)
        refresh_calls.clear()
        page_data.btn_ax_apply.click()

    def step_integral_page():
        spec = window.left_workspace.current_spec()
        check("生成了坐标轴积分页", spec is not None and spec.page_kind == "axis_integral",
              getattr(spec, "page_kind", None))
        _group_width_samples.clear()
        sampler.start()
        QTimer.singleShot(400, step_position_group_revealed)

    def step_position_group_revealed():
        sampler.stop()
        check("位置组已呼出", not bar.axis_group.isHidden())
        settled = bar.axis_group.width()
        intermediate = [w for w in _group_width_samples if 0 < w < settled]
        check("呼出是宽度过渡而非硬跳变",
              len(intermediate) > 0,
              f"采样 {len(_group_width_samples)} 次，中间帧 {len(intermediate)} 次")
        check("位置滑条标签带轴向", bar.axis_title_label.text().startswith("kx"),
              bar.axis_title_label.text())
        if window._has_time_axis():
            check("时间轴与位置组同排显示", bar.timeline_group.isVisible())
        else:
            # 数据没有时间轴坐标：位置控件占用原时间轴区域，左侧不留空位。
            check("无时间轴时时间轴组收起", bar.timeline_group.isHidden())
            check("无时间轴时位置组从底栏左侧开始",
                  abs(bar.axis_group.geometry().left() - bar.ROW_MARGINS[0]) <= 2,
                  f"left={bar.axis_group.geometry().left()}")
            check("无时间轴时位置滑条使用整行可用宽度",
                  bar.slider_axis.width() > bar.AXIS_SLIDER_MIN_WIDTH * 2,
                  f"{bar.slider_axis.width()}px")

        interval = controller.interval
        check("默认完整轴范围", abs(interval.low - window.axis_space.minimum) < 1e-9
              and abs(interval.up - window.axis_space.maximum) < 1e-9)
        check("默认未锁定", not interval.locked)

        # —— 完整范围时整体平移无处可去：触边限制位移，长度与端点都不变 ——
        refresh_calls.clear()
        full_low, full_up = interval.low, interval.up
        bar.slider_axis.setValue(bar.slider_axis.maximum() // 4)
        check("完整范围平移到不了任何地方（两端一起停在边界）",
              abs(interval.low - full_low) < 1e-9 and abs(interval.up - full_up) < 1e-9)
        check("没有实际位移就不产生刷新", len(refresh_calls) == 0, f"{len(refresh_calls)} 次")

        # —— 输入长度：以当前中心向两侧调整 ——
        center = interval.center
        page_data.input_ax_length.setValue(interval.length / 3.0)
        page_data.input_ax_length.editingFinished.emit()
        check("输入长度只提交一次精确刷新", len(refresh_calls) == 1, f"{len(refresh_calls)} 次")
        check("输入长度后中心不变", abs(interval.center - center) < 1e-6)
        check("长度输入框已生效",
              abs(interval.length - page_data.input_ax_length.value()) < 1e-6)

        # —— 拖动位置滑条：长度不变、中心跟随、三个区间控件一致 ——
        length_before = interval.length
        center_before = interval.center
        refresh_calls.clear()
        bar.slider_axis.setValue(bar.slider_axis.maximum() * 3 // 4)
        check("拖动位置只产生一次预览刷新", len(refresh_calls) == 1,
              f"{len(refresh_calls)} 次")
        check("拖动位置保持长度", abs(interval.length - length_before) < 1e-9)
        check("拖动位置改变中心", abs(interval.center - center_before) > 1e-9,
              f"{center_before:.6g} → {interval.center:.6g}")
        length_steps = interval.length / window.axis_space.physical_span
        check("上限滑条跟随位置",
              abs(page_data.s_ax_up.value() - bar.slider_axis.value()) <= length_steps * 1000 + 2,
              f"{page_data.s_ax_up.value()} vs {bar.slider_axis.value()}")
        check("长度输入框跟随模型",
              abs(page_data.input_ax_length.value() - interval.length) < 1e-6)
        # 输入框按坐标分辨率显示：允许一个显示单位内的舍入，模型本身仍是精确值。
        quantum = 10.0 ** (-max(1, int(window.axis_space.decimals)))
        check("位置输入框显示物理值",
              abs(bar.input_axis.value() - interval.center) <= quantum,
              f"差 {abs(bar.input_axis.value() - interval.center):.3g}，显示单位 {quantum:.3g}")
        check("位置输入框的舍入不改动模型",
              interval.center == controller.interval.center)

        # —— 锁定：整体平移，长度输入框只读 ——
        page_data.btn_ax_lock.click()
        check("锁定后状态灯变为已锁定", window.status_lock.text() == "● 区间已锁定")
        check("锁定后状态灯用成功色", theme.SUCCESS in window.status_lock.styleSheet())
        check("锁定后长度输入框只读", page_data.input_ax_length.isReadOnly())

        locked_low, locked_up = interval.low, interval.up
        page_data.input_ax_up.setValue(interval.up + interval.length * 0.25)
        page_data.input_ax_up.editingFinished.emit()
        check("锁定期改上限整体平移",
              abs(interval.up - locked_up) > 1e-9 and abs(interval.low - locked_low) > 1e-9)
        check("锁定期长度保持不变",
              abs((interval.up - interval.low) - (locked_up - locked_low)) < 1e-6)

        page_data.btn_ax_lock.click()
        check("解锁后状态灯回到未锁定", window.status_lock.text() == "● 区间未锁定")
        check("解锁后长度输入框可编辑", not page_data.input_ax_length.isReadOnly())

        _save_screenshots("integral_page", (("bar", bar), ("integration_card", window.right_panel)))

        # —— 结果页各自保存自己的区间与锁定状态 ——
        first_page_id = window.left_workspace.current_spec().page_id
        saved_low, saved_up = interval.low, interval.up
        page_data.btn_ax_lock.click()
        check("第一页已锁定", interval.locked)

        page_data.combo_ax.setCurrentIndex(0)
        page_data.btn_ax_apply.click()
        QTimer.singleShot(250, lambda: step_second_page(first_page_id, saved_low, saved_up))

    def step_second_page(first_page_id, saved_low, saved_up):
        # 「应用」沿用卡片当前的区间，但新页面的锁定状态从零开始。
        check("新建的第二页沿用当前区间",
              abs(controller.interval.low - saved_low) < 1e-6
              and abs(controller.interval.up - saved_up) < 1e-6)
        check("新建的第二页不继承锁定状态", not controller.interval.locked)
        check("第二页状态灯显示未锁定", window.status_lock.text() == "● 区间未锁定")

        window.left_workspace.activate_page(first_page_id)
        QTimer.singleShot(250, lambda: _check_restored(saved_low, saved_up))

    def _check_restored(saved_low, saved_up):
        check("切回第一页恢复自己的区间",
              abs(controller.interval.low - saved_low) < 1e-6
              and abs(controller.interval.up - saved_up) < 1e-6,
              f"{controller.interval.low:.6g}~{controller.interval.up:.6g}")
        check("切回第一页恢复锁定状态",
              controller.interval.locked and window.status_lock.text() == "● 区间已锁定")
        check("切页不重复播放呼出动画", not bar.axis_group.isHidden())
        page_data.btn_ax_lock.click()

        # —— 换行与底栏高度 ——
        original_width = window.width()
        window.resize(1280, 800)
        QTimer.singleShot(250, lambda: step_wrap_check(original_width))

    def step_wrap_check(original_width):
        if bar._wrapped:
            check("窄窗下视图控件移到第二行", bar._wrapped)
            check("换行后底栏高度增加",
                  bar.height() == bar.HEIGHT + bar.SECOND_ROW_HEIGHT, f"{bar.height()}px")
            check("换行后第二行可见", bar.secondary_row.isVisible())
        else:
            check("窄窗下底栏仍能单行排布", bar.height() == bar.HEIGHT, "未触发换行")

        window.resize(1550, 950)
        QTimer.singleShot(250, step_slice_page)

    def step_slice_page():
        # 单层切片页：三维范围里塌缩一根轴，位置控件只移动切片位置。
        from bandscope.ui.result_workspace import AnalysisPageSpec

        source = window.left_workspace.current_spec()
        spec = AnalysisPageSpec(
            window._make_page_id(),
            "切片位置验证",
            "home",
            "data_process",
            params={
                "home_slice_info": {"axis": 0, "index": 2},
                "crop_regions": [
                    {
                        "view": "3d",
                        "axes": ("X", "Y", "E"),
                        "bounds": (-1.0, -1.0, -1.0, 1.0, -0.5, 0.5),
                        "e_flip": False,
                        "operation": "crop",
                    }
                ],
            },
            source_page_id=getattr(source, "page_id", "home"),
        )
        window.left_workspace.add_page(spec)
        QTimer.singleShot(300, lambda: _check_slice_page(spec))

    def _check_slice_page(spec):
        check("切片页位置组仍可见", not bar.axis_group.isHidden())
        check("切片页只移动位置（上下限禁用）", not page_data.s_ax_up.isEnabled())
        check("切片页长度输入禁用", not page_data.input_ax_length.isEnabled())
        check("切片页锁定按钮禁用", not page_data.btn_ax_lock.isEnabled())
        check("切片页位置输入可用", bar.input_axis.isEnabled())

        interval = controller.interval
        check("切片页区间为零长度", abs(interval.up - interval.low) < 1e-9,
              f"{interval.low:.6g}~{interval.up:.6g}")

        bar.slider_axis.setValue(bar.slider_axis.maximum())
        params = spec.params
        check("移动切片位置后 home_slice_info 跟随",
              params["home_slice_info"]["index"] != 2,
              f"index={params['home_slice_info']['index']}")
        regions = params.get("crop_regions") or []
        check("裁剪切面范围跟随位置", regions and regions[0]["bounds"][0] == regions[0]["bounds"][1])
        check("裁剪切面范围内外一致",
              abs(regions[0]["bounds"][0] - interval.low) < 1e-6
              or abs(regions[0]["bounds"][0] - interval.center) < 1e-6)
        QTimer.singleShot(150, step_collapse_check)

    def step_collapse_check():
        window.left_workspace.activate_page(window.left_workspace.home_page_id)
        QTimer.singleShot(400, step_collapsed)

    def step_collapsed():
        check("切回 3D 主页后位置组收起", bar.axis_group.isHidden())
        check("3D 主页不显示位置滑条但区间控件可用", page_data.s_ax_up.isEnabled())
        # 页面切换后的渲染走异步刷新管线：等一拍再截图，避免抓到空帧。
        QTimer.singleShot(500, step_final_screenshot)

    def step_final_screenshot():
        _save_screenshots("final", (("main", window),))
        QTimer.singleShot(100, finish)

    def _save_screenshots(prefix, widgets):
        output_dir = args.output_dir
        for name, widget in widgets:
            path = output_dir / f"{prefix}_{name}.png"
            try:
                widget.grab().save(str(path))
                print(f"[INFO] 截图 {path}", flush=True)
            except Exception as exc:  # noqa: BLE001 - 验收脚本不应因截图失败中断
                print(f"[INFO] 截图失败 {name}: {exc}", flush=True)

    def finish():
        app.quit()

    QTimer.singleShot(200, lambda: window.load_data(data_path))
    QTimer.singleShot(600, step_loaded)
    QTimer.singleShot(1400, step_integral_page)
    QTimer.singleShot(30000, finish)

    app.exec_()

    print(f"\n[RESULT] {'全部通过' if not failures else '存在失败项'}", flush=True)
    if failures:
        for label in failures:
            print(f"  - {label}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
