# -*- coding: utf-8 -*-
"""「相机视角」面板验收：显隐动画、双向同步、重置与 E 轴翻转。

流程：启动窗口 → 断言初始隐藏 → 加载数据后断言平滑展开（高频采样高度，
必须出现中间帧）→ 普通 3D 重绘不重播动画 → 30 Hz 交互回读 → 真实键入角度
与距离后画面立即跟随且观察中心不跳变 → 重置视角 → E 轴翻转一致性 →
切到 2D 平滑收起 → 快速来回切换后终态正确、无残留高度 → 窄窗口不截断。

全程 QTimer 链驱动，不手动 processEvents（Windows 上会触发
RPC_E_CANTCALLOUT_ININPUTSYNCCALL 致命错误）。

用法: .venv/Scripts/python.exe scripts/verify_camera_view_panel.py
"""
import faulthandler
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

faulthandler.enable()

from qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

from PyQt5.QtCore import QPoint, Qt, QTimer
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NPZ = os.path.join(ROOT, "smoke_data", "NiHITP_calibrated_2.npz")

failures = []
height_samples = []

# 输入框只显示到小数位（角度 1 位、距离 2 位），回读比对要按显示精度取容差；
# 相机本身始终保留完整精度（显示舍入不得反写损失相机精度）。
ANGLE_TOLERANCE = 0.05
DISTANCE_TOLERANCE = 0.005


def same_angle(left, right):
    return abs(float(left) - float(right)) <= ANGLE_TOLERANCE


def same_distance(left, right):
    return abs(float(left) - float(right)) <= DISTANCE_TOLERANCE


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    suffix = f"  {detail}" if detail else ""
    print(f"[{status}] {label}{suffix}", flush=True)
    if not condition:
        failures.append(label)


def wait_for(desc, predicate, next_step, timeout_ms=60000, settle_ms=700):
    """等到 predicate 成立后再留出 settle_ms 让收放动画跑完，然后进入下一步。

    首次轮询必须推迟到事件循环里：渲染条件可能在该步的同步代码里就已成立，
    此时动画还没开始推进，立刻断言高度只会读到 0。
    """
    deadline = time.perf_counter() + timeout_ms / 1000.0

    def poll():
        try:
            ready = bool(predicate())
        except Exception:  # noqa: BLE001 - 验收脚本等待超时而不是中断
            ready = False
        if ready:
            QTimer.singleShot(settle_ms, next_step)
        elif time.perf_counter() < deadline:
            QTimer.singleShot(200, poll)
        else:
            check(f"等待「{desc}」", False, "超时")
            QTimer.singleShot(settle_ms, next_step)

    QTimer.singleShot(200, poll)


class _NullUpdateController:
    def __init__(self, *args, **kwargs):
        pass

    def check_automatically(self):
        pass

    def shutdown(self, **kwargs):
        pass


def main():
    app = QApplication(sys.argv)

    import refactored_app
    refactored_app.UpdateController = _NullUpdateController

    window = refactored_app.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()

    from camera_view_controls import CameraPose

    from camera_view_controls import (
        DEFAULT_AZIMUTH,
        DEFAULT_ELEVATION,
        DEFAULT_ROLL,
    )

    page = window.page_render
    panel = page.camera_panel
    group = page.grp_camera

    def pose():
        from camera_view_controls import pose_from_vectors
        camera = window.plotter.camera
        return pose_from_vectors(camera.position, camera.focal_point, camera.up)

    def is_3d():
        context = window.current_render_context
        return isinstance(context, dict) and context.get("view") == "3d"

    def panel_pose():
        return page.camera_pose()

    def pan_camera(right_amount, up_amount):
        """复刻 vtkInteractorStyleTrackballCamera 的平移：相机与观察中心一起在像平面内平移。

        pyvista 的 Camera 包装层没有暴露 vtkCamera.Pan，这里按同样的定义直接平移两个
        向量：视线方向与距离不变，只有观察中心移动。
        """
        camera = window.plotter.camera
        focal = np.asarray(camera.focal_point, dtype=float)
        position = np.asarray(camera.position, dtype=float)
        up = np.asarray(camera.up, dtype=float)
        up = up / np.linalg.norm(up)
        direction = focal - position
        direction = direction / np.linalg.norm(direction)
        right = np.cross(direction, up)
        right = right / np.linalg.norm(right)
        screen_up = np.cross(right, direction)
        scale = float(camera.distance)
        offset = right * (right_amount * scale) + screen_up * (up_amount * scale)
        camera.focal_point = tuple(focal + offset)
        camera.position = tuple(position + offset)

    def type_into(key, text):
        box = panel._boxes[key]
        box.lineEdit().setFocus()
        box.lineEdit().selectAll()
        QTest.keyClicks(box.lineEdit(), text)
        QTest.keyClick(box.lineEdit(), Qt.Key_Return)

    sampler = QTimer(window)
    sampler.setInterval(15)
    sampler.timeout.connect(lambda: height_samples.append(group.height()))

    def guarded(step):
        """Qt 槽里未捕获的异常会被 PyQt 直接 abort，这里转成可读的失败报告。"""

        def wrapper(*args, **kwargs):
            try:
                return step(*args, **kwargs)
            except Exception:  # noqa: BLE001 - 验收脚本记录而不是中断
                import traceback
                traceback.print_exc()
                check(f"步骤 {getattr(step, '__name__', step)}", False, "抛出异常")
                app.exit(3)

        return wrapper

    def soon(delay_ms, step):
        QTimer.singleShot(delay_ms, guarded(step))

    def save_shot(name):
        os.makedirs("smoke_output", exist_ok=True)
        path = os.path.join("smoke_output", name)
        window.grab().save(path)
        print(f"[accept] 截图 -> {path}", flush=True)

    # ------------------------------------------------------------------ 启动
    def step_initial():
        check("启动时「相机视角」隐藏（不闪现）", group.isHidden())
        check("启动时显隐目标状态为收起", page._camera_view_visible_target is False)
        check("启动时渲染控制页仍可见", page.isVisible())
        height_samples.clear()
        sampler.start()
        window.load_data(NPZ)
        wait_for("3D 主页渲染", is_3d, guarded(step_3d_loaded))

    # ------------------------------------------------------------ 3D 展开态
    def step_3d_loaded():
        sampler.stop()
        settled = group.height()
        intermediate = [h for h in height_samples if 0 < h < settled]
        check(
            f"进入 3D 视图时平滑展开（采样 {len(height_samples)} 次，中间帧 {len(intermediate)} 次）",
            len(intermediate) > 0,
        )
        check("3D 视图下模块不再隐藏", not group.isHidden())
        check(f"3D 视图下模块回到自然高度（{settled}px）", settled > 0)
        from control_page_base import CardGroup

        cards = page.container.findChildren(CardGroup)
        topmost = min(cards, key=lambda card: card.y())
        check(
            f"模块位于渲染控制页顶部（最上方卡片 y={topmost.y()}，共 {len(cards)} 张卡片）",
            topmost is page.grp_camera,
        )
        labels = [label.text() for label in panel._labels]
        check(f"四个输入框文案正确（{'/'.join(labels)}）",
              labels == ["方位角", "仰角", "滚转角", "距离"])
        check("唯一的操作按钮为「重置视角」", panel.reset_button.text() == "重置视角")

        initial = pose()
        check(
            f"初始读数等于应用初次展示的等轴视图（方位 {initial.azimuth:.1f}° "
            f"仰角 {initial.elevation:.1f}° 滚转 {initial.roll:.1f}°）",
            same_angle(initial.azimuth, DEFAULT_AZIMUTH)
            and same_angle(initial.elevation, DEFAULT_ELEVATION)
            and same_angle(initial.roll, DEFAULT_ROLL),
        )
        check(
            "初始输入框与相机同步",
            same_angle(panel_pose().azimuth, initial.azimuth)
            and same_angle(panel_pose().elevation, initial.elevation)
            and same_angle(panel_pose().roll, initial.roll)
            and same_distance(panel_pose().distance, initial.distance),
        )
        save_shot("camera_panel_3d.png")

        # 面板展开后内容变高：滚动条必须跟着内容高度走，否则底部卡片够不着。
        scroll = page.scroll
        overflows = page.container.height() > scroll.height()
        check(
            f"内容高出视口时滚动条可见（内容 {page.container.height()}px / 视口 {scroll.height()}px）",
            overflows and not scroll.scroll_bar_vertical.isHidden(),
        )
        bottom = max(cards, key=lambda card: card.y())
        check(
            f"底部卡片初始显示不全（最下方卡片 y={bottom.y()} 高 {bottom.height()}）",
            bottom.y() + bottom.height() > scroll.height(),
        )
        event = QWheelEvent(
            QPoint(10, 10), QPoint(10, 10), QPoint(0, 0), QPoint(0, -120),
            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
        )
        scroll.wheelEvent(event)
        QTest.qWait(700)
        check(
            f"滚轮可以滚动控制页（容器 y {page.container.y()}）",
            event.isAccepted() and page.container.y() < 0,
        )
        check(
            "滚动后底部卡片完整进入视口",
            bottom.y() + bottom.height() + page.container.y() <= scroll.height(),
        )
        save_shot("camera_panel_scrolled.png")
        scroll.widget_scroll_animation.setCurrent([page.container.x(), 0])
        scroll.widget_scroll_animation.setTarget([page.container.x(), 0])
        page.container.move(page.container.x(), 0)

        height_samples.clear()
        sampler.start()
        window.request_refresh(
            refactored_app.RefreshCause.TRANSFER_FUNCTION,
            refactored_app.RenderQuality.EXACT,
            immediate=True,
        )
        soon(800, step_no_replay)

    def step_no_replay():
        sampler.stop()
        current = group.height()
        changed = [h for h in height_samples if h != current]
        check(
            f"普通 3D 刷新不重播动画（采样 {len(height_samples)} 次，高度变化 {len(changed)} 次）",
            len(changed) == 0,
        )
        window._select_control_page(1)
        soon(200, step_other_tab)

    def step_other_tab():
        check("切到其他控制标签页时 3D 状态未被打乱", not group.isHidden())
        window._select_control_page(0)
        soon(200, step_interaction)

    # ------------------------------------------------------------ 相机交互
    def step_interaction():
        camera = window.plotter.camera
        before = pose()

        window._on_camera_interaction_started()
        check(
            "交互开始时启动 30 Hz 回读计时器",
            window._camera_view_sync_timer.isActive(),
        )
        # 模拟左键拖动（VTK 轨道球：Azimuth + Elevation）。
        camera.Azimuth(35.0)
        camera.Elevation(20.0)
        camera.OrthogonalizeViewUp()
        soon(120, step_dragged)

    def step_dragged():
        midway = pose()
        check(
            f"拖动过程中输入框已跟随（方位 {midway.azimuth:.1f}°，仰角 {midway.elevation:.1f}°）",
            same_angle(panel_pose().azimuth, midway.azimuth)
            and same_angle(panel_pose().elevation, midway.elevation),
        )
        # 交互结束：EndInteractionEvent 会走到这里，停表并做一次精确回读。
        window._capture_3d_camera_position()
        after = pose()
        check("交互结束时停表", not window._camera_view_sync_timer.isActive())
        check("鼠标旋转保持距离不变", abs(after.distance - midway.distance) < 1e-9)
        check(
            "交互结束后输入框为精确结果",
            same_angle(panel_pose().azimuth, after.azimuth)
            and same_angle(panel_pose().roll, after.roll),
        )
        camera_before = window._current_3d_camera_position()
        window._sync_camera_view_controls()
        check(
            "输入框显示舍入不反写相机精度",
            window._current_3d_camera_position() == camera_before,
        )

        camera = window.plotter.camera
        center_before = tuple(float(v) for v in camera.focal_point)
        camera.Dolly(1.5)
        window._capture_3d_camera_position()
        zoomed = pose()
        check(
            f"滚轮缩放改变距离（{after.distance:.1f} → {zoomed.distance:.1f}）",
            abs(zoomed.distance - after.distance / 1.5) < 1e-6,
        )
        check("滚轮缩放保持观察中心", tuple(float(v) for v in camera.focal_point) == center_before)
        check("滚轮缩放保持姿态角", same_angle(zoomed.azimuth, after.azimuth))

        distance_before = zoomed.distance
        pan_camera(0.15, 0.10)
        window._capture_3d_camera_position()
        camera = window.plotter.camera
        panned = pose()
        check(
            f"平移改变观察中心（{tuple(round(v, 1) for v in center_before)} → "
            f"{tuple(round(v, 1) for v in camera.focal_point)}）",
            tuple(float(v) for v in camera.focal_point) != center_before,
        )
        check("平移不改变距离", abs(panned.distance - distance_before) < 1e-6)
        check("平移后输入框显示真实距离（不跳回数据中心）",
              same_distance(panel_pose().distance, panned.distance))

        window._panned_center = tuple(float(v) for v in camera.focal_point)
        window._panned_pose = panned
        soon(50, step_type_angles)

    def step_type_angles():
        window._panned = window._panned_pose
        type_into("azimuth", "120")
        soon(150, step_azimuth_typed)

    def step_azimuth_typed():
        panned = window._panned
        typed = pose()
        check(
            f"键入方位角 120° 后画面立即跟随（{typed.azimuth:.1f}°）",
            abs(typed.azimuth - 120.0) < 1e-4,
        )
        check("键入角度不改变距离", abs(typed.distance - panned.distance) < 1e-6)
        check(
            "键入角度后观察中心不跳变",
            tuple(float(v) for v in window.plotter.camera.focal_point) == window._panned_center,
        )
        check("键入后输入框回显规范值", same_angle(panel_pose().azimuth, 120.0))

        type_into("azimuth", "270")
        soon(150, step_azimuth_wrapped)

    def step_azimuth_wrapped():
        typed = pose()
        check(
            f"方位角 270° 折进 (-180°, 180°] 且画面等价（读到 {typed.azimuth:.1f}°）",
            abs(typed.azimuth + 90.0) < 1e-4,
        )
        check("折角后输入框显示规范值", same_angle(panel_pose().azimuth, -90.0))

        type_into("roll", "-400")
        soon(150, step_roll_wrapped)

    def step_roll_wrapped():
        typed = pose()
        check(
            f"滚转角 -400° 折进 (-180°, 180°]（读到 {typed.roll:.1f}°）",
            abs(typed.roll - (-40.0)) < 1e-4,
        )

        type_into("elevation", "25")
        type_into("distance", "350")
        soon(200, step_distance_typed)

    def step_distance_typed():
        typed = pose()
        check(f"键入距离 350 后画面立即跟随（{typed.distance:.2f}）",
              abs(typed.distance - 350.0) < 1e-4)
        check(
            "键入距离只沿视线方向调整（中心与姿态角不变）",
            tuple(float(v) for v in window.plotter.camera.focal_point) == window._panned_center
            and abs(typed.elevation - 25.0) < 1e-4,
        )
        check("键入距离后输入框与相机一致", same_distance(panel_pose().distance, 350.0))

        type_into("distance", "0")
        soon(150, step_distance_zero)
        return None

    def step_distance_zero():
        typed = pose()
        check(
            f"距离 0 被就近修正为合法正数（{typed.distance:.2f}）",
            typed.distance > 0.0,
        )
        # 收尾前先回到普通姿态，再测极点（极点处方位角本就不可定义）。
        type_into("elevation", "90")
        soon(150, step_pole)

    def step_pole():
        camera = window.plotter.camera
        direction = np.asarray(camera.focal_point) - np.asarray(camera.position)
        direction = direction / np.linalg.norm(direction)
        up = np.asarray(camera.up)
        up = up / np.linalg.norm(up)
        check(
            f"仰角 90° 得到沿 -E 的俯视（视线 {tuple(round(v, 2) for v in direction)}）",
            abs(direction[0]) < 1e-6 and abs(direction[1]) < 1e-6 and direction[2] < -0.999999,
        )
        check("极点处屏幕上方落在 Kx-Ky 平面内", abs(up[2]) < 1e-6)
        check("极点处姿态读数与相机一致", same_angle(panel_pose().elevation, 90.0))
        soon(50, step_reset)

    # ------------------------------------------------------------ 重置
    def step_reset():
        window.on_camera_view_reset()
        reset = pose()
        check(
            f"重置恢复默认等轴视图（方位 {reset.azimuth:.1f}° 仰角 {reset.elevation:.1f}° "
            f"滚转 {reset.roll:.1f}°）",
            same_angle(reset.azimuth, DEFAULT_AZIMUTH)
            and same_angle(reset.elevation, DEFAULT_ELEVATION)
            and same_angle(reset.roll, DEFAULT_ROLL),
        )
        camera = window.plotter.camera
        center = tuple(float(v) for v in camera.focal_point)
        check(
            f"重置把观察中心对准当前内容（{tuple(round(v, 1) for v in center)}）",
            all(abs(value - 100.0) < 1.0 for value in center),
        )
        check(f"重置后取景距离覆盖场景（{reset.distance:.1f}）", reset.distance > 200.0)
        check("重置后输入框与相机同步",
              same_angle(panel_pose().azimuth, reset.azimuth)
              and same_distance(panel_pose().distance, reset.distance))
        check("重置后翻转标记与开关一致（未翻转）", window._camera_e_flip_applied is False)

        window.request_refresh(
            refactored_app.RefreshCause.TRANSFER_FUNCTION,
            refactored_app.RenderQuality.EXACT,
            immediate=True,
        )
        soon(700, step_after_refresh)

    def step_after_refresh():
        after = pose()
        check(
            f"重置后刷新仍保持默认姿态（方位 {after.azimuth:.1f}° 仰角 {after.elevation:.1f}°）",
            same_angle(after.azimuth, DEFAULT_AZIMUTH)
            and same_angle(after.elevation, DEFAULT_ELEVATION)
            and same_angle(after.roll, DEFAULT_ROLL),
        )
        window.timeline_bar.switch_flip.setChecked(True)
        soon(900, step_flip_reset)

    def step_flip_reset():
        window.on_camera_view_reset()
        first = pose()
        # E 轴翻转是绕相机水平轴转 180°：本约定下默认姿态变成
        # 方位角 -135°、仰角 -35.264°、滚转 180°。
        check(
            f"E 轴翻转打开时重置与开关一致（方位 {first.azimuth:.1f}° 仰角 {first.elevation:.1f}° "
            f"滚转 {first.roll:.1f}°）",
            same_angle(first.azimuth, -135.0)
            and same_angle(first.elevation, -DEFAULT_ELEVATION)
            and same_angle(first.roll, 180.0),
        )
        check("E 轴翻转标记与开关一致", window._camera_e_flip_applied is True)
        window.on_camera_view_reset()
        second = pose()
        check(
            "重复重置不再叠加第二个 180°",
            same_angle(second.azimuth, first.azimuth)
            and same_angle(second.roll, first.roll)
            and same_angle(second.elevation, first.elevation),
        )
        # 刷新一次：E 翻转机制不能再补一次 180°。
        window.request_refresh(
            refactored_app.RefreshCause.TRANSFER_FUNCTION,
            refactored_app.RenderQuality.EXACT,
            immediate=True,
        )
        soon(700, step_flip_refreshed)

    def step_flip_refreshed():
        after = pose()
        check(
            f"翻转开启时刷新不再旋转 180°（方位 {after.azimuth:.1f}° 滚转 {after.roll:.1f}°）",
            same_angle(after.azimuth, -135.0) and same_angle(after.roll, 180.0),
        )
        window.timeline_bar.switch_flip.setChecked(False)
        soon(900, step_2d)

    # ------------------------------------------------------------ 收放动画
    def step_2d():
        window.home_slice_info = {"axis": 2, "index": int(window.core.raw_data.shape[2] // 2)}
        height_samples.clear()
        sampler.start()
        window.global_refresh()
        wait_for(
            "2D 切片渲染",
            lambda: isinstance(window.current_render_context, dict)
            and window.current_render_context.get("view") == "2d",
            guarded(step_2d_settled),
        )

    def step_2d_settled():
        sampler.stop()
        start_height = height_samples[0] if height_samples else 0
        intermediate = [h for h in height_samples if 0 < h < start_height]
        check(
            f"切到 2D 时平滑收起（采样 {len(height_samples)} 次，中间帧 {len(intermediate)} 次）",
            len(intermediate) > 0,
        )
        check("2D 视图下模块隐藏", group.isHidden())
        check("2D 视图下不留空白占位", group.height() == 0, f"height={group.height()}")

        for _ in range(3):
            window.home_slice_info = None
            window.global_refresh()
            window.home_slice_info = {"axis": 2, "index": 5}
            window.global_refresh()
        window.home_slice_info = None
        window.global_refresh()
        soon(1500, step_rapid_3d)

    def step_rapid_3d():
        check("快速 3D/2D 来回切换后终态为展开（3D）",
              not group.isHidden() and group.height() > 0,
              f"height={group.height()}")
        window.home_slice_info = {"axis": 2, "index": 5}
        window.global_refresh()
        soon(1200, step_rapid_2d)

    def step_rapid_2d():
        check("快速切换后停在 2D 时正确收起",
              group.isHidden() and group.height() == 0, f"height={group.height()}")
        save_shot("camera_panel_2d.png")
        window.home_slice_info = None
        window.global_refresh()
        soon(1200, step_narrow_start)

    # ------------------------------------------------------------ 窄窗口
    def step_narrow_start():
        window._narrow_widths = [1000, 1100, 1240, 1550, 1100]
        step_narrow_next()

    def step_narrow_next():
        widths = window._narrow_widths
        if not widths:
            check_narrow()
            return
        window.resize(widths.pop(0), 900)
        soon(250, step_narrow_next)

    def check_narrow():
        labels_ok = all(
            label.width() >= label.sizeHint().width() for label in panel._labels
        )
        detail = " ".join(
            f"{label.text()}={label.width()}/{label.sizeHint().width()}"
            for label in panel._labels
        )
        check("窄窗口下字段标签完整可读", labels_ok, detail)

        boxes_ok = all(box.width() >= box.sizeHint().width() for box in panel._boxes.values())
        box_detail = " ".join(
            f"{box.value():.1f}={box.width()}/{box.sizeHint().width()}"
            for box in panel._boxes.values()
        )
        check("窄窗口下数值框不截断", boxes_ok, box_detail)
        check(
            f"面板宽度不超出卡片内容（面板 {panel.width()}px / 卡片 {group.width()}px）",
            panel.width() <= group.width() - 2 * page.CARD_HORIZONTAL_PADDING,
        )
        save_shot("camera_panel_narrow.png")
        soon(100, finish)

    def finish():
        # 走一遍真实的关闭路径：新增的观察器与计时器必须在关窗时被清掉。
        try:
            window.close()
            check("关窗后相机交互计时器已停止并解除观察器",
                  window._camera_view_sync_timer.isActive() is False
                  and window._camera_observer_id is None
                  and window._camera_interaction_observer_id is None)
        except Exception:  # noqa: BLE001 - 验收脚本记录而不是中断
            import traceback
            traceback.print_exc()
            check("窗口关闭路径", False, "抛出异常")
        if failures:
            print(f"[RESULT] {len(failures)} 项失败", flush=True)
            for item in failures:
                print(f"  - {item}", flush=True)
            app.exit(1)
        else:
            print("[RESULT] 全部通过", flush=True)
            app.exit(0)

    soon(400, step_initial)
    soon(180000, lambda: (print("[RESULT] 超时", flush=True), app.exit(2)))

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
