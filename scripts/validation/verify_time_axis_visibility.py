# -*- coding: utf-8 -*-
"""时间双端区间条验收：动态展开、静态隐藏、解锁积分不建页。

流程：启动窗口 → 初始隐藏 → 动态数据展开、普通 3D 主页切片积分按钮禁用 →
持久化含能量轴的 2D 主页后框选工具可用并操作双端区间 → 静态页按钮禁用 →
再次加载动态数据并以同样的 2D 主页验证恢复。右键「处理分析」始终无内置切片积分。
悬浮视图控件常驻，底栏按区间组显隐。
全程 QTimer 链驱动，不手动 processEvents。

用法: .venv/Scripts/python.exe scripts/validation/verify_time_axis_visibility.py <动态npz> <静态npz>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DYNAMIC_NPZ = None
STATIC_NPZ = None

failures = []
bar_height_samples = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}", flush=True)
    if not condition:
        failures.append(label)


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def create_action_keys(window):
    spec = window.left_workspace.current_spec()
    if spec is None:
        return set()
    actions = window._integration_actions_for_parent(spec.page_id)
    return {key for key, _label, _order in actions}


def check_slice_dos_entry(window, button_enabled, label):
    check(f"{label}：右键处理分析始终无内置切片积分",
          "builtin:slice_dos" not in create_action_keys(window))
    button = window.canvas_tool_overlay.slice_dos_button
    check(f"{label}：框选次级切片积分按钮{'可用' if button_enabled else '禁用'}",
          button.isEnabled() is bool(button_enabled))


def prepare_energy_home_slice(window):
    spec = window.left_workspace.current_spec()
    axis_index = 0
    window.home_slice_info = {
        "axis": axis_index,
        "index": int(window.core.raw_data.shape[axis_index] // 2),
    }
    # 沿 X 轴取单层，保留 Y-E 平面；动作查询读取持久化后的主页参数。
    window._persist_home_page_state(spec)
    window.global_refresh()
    return spec


def main():
    global DYNAMIC_NPZ, STATIC_NPZ
    from scripts.validation._common import arguments
    args = arguments("time_axis", two_datasets=True)
    DYNAMIC_NPZ, STATIC_NPZ = str(args.data), str(args.static_data)
    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app
    refactored_app.UpdateController = _NullUpdateController

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()

    bar = window.timeline_bar
    group = bar.timeline_group
    overlay = window.canvas_tool_overlay

    # 高频采样时间轴分组高度：展开动画（220ms）期间应出现 0 < h < 终值的中间帧。
    sampler = QTimer(window)
    sampler.setInterval(15)
    sampler.timeout.connect(lambda: bar_height_samples.append(group.height()))

    def step_initial():
        check("启动时时间轴分组隐藏", group.isHidden())
        check("启动时无区间组，底条折叠", bar.isHidden())
        check("启动时悬浮视图控件仍可见", all(
            button.isVisible()
            for button in (overlay.switch_axes, overlay.switch_flip, overlay.rotation_button)
        ))
        check_slice_dos_entry(window, False, "启动时无来源页")

        bar_height_samples.clear()
        sampler.start()
        window.load_data(DYNAMIC_NPZ)

    def step_dynamic_loaded():
        sampler.stop()
        settled = group.height()
        intermediate = [h for h in bar_height_samples if 0 < h < settled]
        check(
            f"时间轴分组展开有中间帧（采样 {len(bar_height_samples)} 次，中间帧 {len(intermediate)} 次）",
            len(intermediate) > 0,
        )
        check("动态数据加载后时间轴分组可见", not group.isHidden())
        check(f"动态数据加载后分组回到自然高度（{settled}px）", settled > 0)
        check("动态数据加载后底条保持单行高度", bar.height() == bar.HEIGHT)
        check_slice_dos_entry(window, False, "普通动态 3D 主页")
        control = bar.time_interval_control
        check("时间轴使用双端区间控件", control.title_label.text() == "时间延迟")
        check("动态数据初始为合拢单帧", window.time_interval.locked and window.time_interval.length == 0.0)

        spec = prepare_energy_home_slice(window)
        check("含能量轴的 2D 主页切片状态已持久化",
              (spec.params.get("home_slice_info") or {}).get("axis") == 0)
        check_slice_dos_entry(window, True, "合法动态 Y-E 2D 主页")
        page_count = len(window.left_workspace.page_specs)
        control.range_slider.lock_badge.click()
        control.input_length.setValue(control.input_length.maximum() * 0.25)
        control.input_length.editingFinished.emit()
        check("解锁分离后形成非零时间区间", window.time_interval.length > 0.0)
        check("时间积分只更新当前页", window.left_workspace.current_spec() is spec
              and len(window.left_workspace.page_specs) == page_count)
        window.grab().save(str(args.output_dir / "time-interval-expanded.png"))
        control.input_length.setValue(0.0)
        control.input_length.editingFinished.emit()
        check("长度归零后恢复合拢单帧", window.time_interval.locked
              and window.time_interval.length == 0.0)
        window.load_data(STATIC_NPZ)

    def step_static_loaded():
        check("静态数据加载后时间轴分组隐藏", group.isHidden())
        check("静态 3D 主页无区间组，底条折叠", bar.isHidden())
        check("静态数据加载后悬浮视图控件仍在", all(
            button.isVisible()
            for button in (overlay.switch_axes, overlay.switch_flip, overlay.rotation_button)
        ))
        check_slice_dos_entry(window, False, "静态 3D 主页")
        check("静态数据下时间区间模型已清空", window.time_interval is None)
        spec = prepare_energy_home_slice(window)
        check("静态 Y-E 主页切片状态已持久化",
              (spec.params.get("home_slice_info") or {}).get("axis") == 0)
        check_slice_dos_entry(window, False, "静态 Y-E 2D 主页")
        window.grab().save(str(args.output_dir / "time-axis-static.png"))
        window.load_data(DYNAMIC_NPZ)

    def step_dynamic_reloaded():
        check("再次加载动态数据后时间轴分组重新可见", not group.isHidden())
        spec = prepare_energy_home_slice(window)
        check("再次加载后含能量轴的 2D 主页切片状态已持久化",
              (spec.params.get("home_slice_info") or {}).get("axis") == 0)
        check_slice_dos_entry(window, True, "再次加载后的合法动态 Y-E 2D 主页")
        window.grab().save(str(args.output_dir / "time-axis-restored.png"))
        QTimer.singleShot(500, finish)

    def finish():
        if failures:
            print(f"[RESULT] {len(failures)} 项失败", flush=True)
            app.exit(1)
        else:
            print("[RESULT] 全部通过", flush=True)
            app.exit(0)

    QTimer.singleShot(300, step_initial)
    QTimer.singleShot(4000, step_dynamic_loaded)
    QTimer.singleShot(8000, step_static_loaded)
    QTimer.singleShot(12000, step_dynamic_reloaded)
    QTimer.singleShot(20000, lambda: app.exit(2))  # 兜底防挂死

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
