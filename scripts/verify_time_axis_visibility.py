# -*- coding: utf-8 -*-
"""时间轴相关 UI 显隐验证：静态数据隐藏 / 动态数据平滑展开。

流程：启动窗口 → 断言初始隐藏 → 下拉过滤/选中保持单元级断言 →
加载动态数据（高频采样时间轴分组高度验证动画平滑）→ 断言全部可见 →
加载静态数据 → 断言全部隐藏 → 再加载动态数据断言恢复。
底条本身常驻可见（右侧显示坐标 / E轴翻转 / Z轴旋转与时间轴无关），
收放的只是底条左侧的时间轴分组。
全程 QTimer 链驱动，不手动 processEvents。

用法: .venv/Scripts/python.exe scripts/verify_time_axis_visibility.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DYNAMIC_NPZ = os.path.join(ROOT, "smoke_data", "scan07_dynamic.npz")
STATIC_NPZ = os.path.join(ROOT, "smoke_data", "NiHITP_calibrated_2.npz")

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


def combo_items(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def main():
    app = QApplication(sys.argv)

    import refactored_app
    refactored_app.UpdateController = _NullUpdateController

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()

    bar = window.timeline_bar
    group = bar.timeline_group
    page_data = window.page_data
    combo = page_data.combo_other

    # 高频采样时间轴分组高度：展开动画（220ms）期间应出现 0 < h < 终值的中间帧。
    sampler = QTimer(window)
    sampler.setInterval(15)
    sampler.timeout.connect(lambda: bar_height_samples.append(group.height()))

    def step_initial():
        check("启动时时间轴分组隐藏", group.isHidden())
        check("启动时底条仍可见（视图控件常驻）", bar.isVisible())
        # page_data 位于未激活标签页，祖先不可见，用 isHidden() 判断自身显隐标志。
        check("启动时“对时间轴积分”卡片隐藏", page_data.grp_t.isHidden())
        check("启动时“其他积分”无时间相关项", "切片内强度积分" not in combo_items(combo))

        # —— 单元级：过滤不丢普通选中项 ——
        page_data.set_time_axis_available(True, animate=False)
        combo.setCurrentIndex(combo_items(combo).index("EDC瀑布图"))
        page_data.set_time_axis_available(False, animate=False)
        check("过滤后普通选中项保留（EDC瀑布图）", combo.currentText() == "EDC瀑布图")
        check("过滤后下拉剩 4 项", combo.count() == 4)

        # —— 单元级：被过滤掉的选中项恢复后能选回 ——
        page_data.set_time_axis_available(True, animate=False)
        combo.setCurrentIndex(0)  # 切片内强度积分
        page_data.set_time_axis_available(False, animate=False)
        check("选中项被过滤时回退到就近项", combo.currentText() == "能级态密度")
        page_data.set_time_axis_available(True, animate=False)
        check("恢复后选回被过滤的选中项", combo.currentText() == "切片内强度积分")
        check("恢复后下拉回到 5 项", combo.count() == 5)
        page_data.set_time_axis_available(False, animate=False)

        # —— 真实加载：动态数据 ——
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
        check("动态数据加载后底条保持固定高度 56", bar.height() == 56)
        check("动态数据加载后“对时间轴积分”卡片不再隐藏", not page_data.grp_t.isHidden())
        check("动态数据加载后“其他积分”含时间相关项", "切片内强度积分" in combo_items(combo))
        check("时间积分控件可用", page_data.btn_t_apply.isEnabled())
        window.load_data(STATIC_NPZ)

    def step_static_loaded():
        check("静态数据加载后时间轴分组隐藏", group.isHidden())
        check("静态数据加载后底条与视图控件仍在", bar.isVisible() and bar.view_controls.isVisible())
        check("静态数据加载后“对时间轴积分”卡片隐藏", page_data.grp_t.isHidden())
        items = combo_items(combo)
        check("静态数据加载后“其他积分”剩余 4 项", len(items) == 4)
        check("静态数据加载后“切片内强度积分”已移除", "切片内强度积分" not in items)
        check("静态数据加载后时间积分控件禁用", not page_data.btn_t_apply.isEnabled())
        window.load_data(DYNAMIC_NPZ)

    def step_dynamic_reloaded():
        check("再次加载动态数据后时间轴分组重新可见", not group.isHidden())
        check("再次加载动态数据后卡片重新可见", not page_data.grp_t.isHidden())
        check("再次加载动态数据后时间相关项恢复", "切片内强度积分" in combo_items(combo))
        QTimer.singleShot(100, finish)

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
