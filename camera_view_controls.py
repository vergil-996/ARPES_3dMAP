# -*- coding: utf-8 -*-
"""「相机视角」面板 —— 相机姿态与「方位角 / 仰角 / 滚转角 / 距离」的往返换算。

坐标与旋转约定（固定世界向上轴 = +Z，即数据体的 E 轴，也是 VTK 的默认竖直轴）

3D 场景来自 :class:`render_core.VolumeRenderSession`：体数据按 spacing 缩放成
0~200 的立方体，X = Kx、Y = Ky、Z = E（能量）。应用初次展示 3D 数据时用的是
``pyvistaqt.QtInteractor`` 构造阶段调用的 ``Renderer.view_isometric()``：方向取
主题默认的 ``camera['position'] = (1, 1, 1)``、``viewup = (0, 0, 1)``。换算到本
约定就是方位角 45°、仰角 ``asin(1/√3) ≈ 35.264°``、滚转 0°（见
:data:`DEFAULT_AZIMUTH` / :data:`DEFAULT_ELEVATION` / :data:`DEFAULT_ROLL`）。

* 观察中心 ``F`` = 相机 ``focal_point``。鼠标平移会改变它，本模块只读取、
  从不把它拉回数据中心。
* ``offset = position - F``，距离 ``d = |offset|``。
* **仰角** ``e = asin(offset·Ẑ / d)``，范围 ``[-90°, +90°]``。0° 表示相机落在
  Kx-Ky 平面内平视（E 轴在屏幕上竖直），+90° 表示相机转到 +Z 正上方俯视
  Kx-Ky 平面。±90° 是极点：那里方位角不再改变相机位置，只相当于绕视线自转。
* **方位角** ``a = atan2(offset·Ŷ, offset·X̂)``，范围 ``(-180°, 180°]``。
  0° 表示相机在 +X 一侧，+90° 表示绕 +Z 轴逆时针（从 +Z 俯视）转到 +Y 一侧。
* **滚转角** ``r``：像平面内的旋转角，0° 表示画面的“上”取世界 +Z 轴在像平面内
  的投影；正值表示按右手定则绕视线方向 ``d̂ = -offset/d`` 逆时针旋转，范围
  ``(-180°, 180°]``。
* 极点处世界 +Z 的投影退化为零，此时 0° 参考取 ``e → ±90°`` 的极限方向，
  且读写两端共用同一条公式，因此极点附近往返仍然精确。

读、写两端都由同一组向量公式导出，不依赖 ``vtkCamera`` 的 ``Azimuth()`` /
``Elevation()`` / ``Roll()`` 这些增量式接口，所以「读 → 写 → 读」不会累积
误差：鼠标在任意位置松手后，输入框显示的都是相机的真实姿态。
"""

import math
from dataclasses import dataclass

import numpy as np
from PyQt5.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import theme
from ui_controls import ActionButton

#: 固定世界向上轴：数据体的 E 轴，也是 VTK 的默认竖直轴。
WORLD_UP = np.array([0.0, 0.0, 1.0])

#: 判断向量退化（平行 / 零长）的阈值。
DEGENERATE_THRESHOLD = 1e-12

#: 距离的合法区间：必须大于零，上限避免极端输入把场景推到裁剪面之外。
MIN_DISTANCE = 0.01
MAX_DISTANCE = 1e6

#: 默认观察方向 = 应用初次展示 3D 数据时的等轴视图方向。
#: pyvistaqt 构造 QtInteractor 时会调用 ``Renderer.view_isometric()``，它取主题
#: 默认的 ``camera['position'] = (1, 1, 1)``、``viewup = (0, 0, 1)``：
#: 方位角 atan2(1, 1) = 45°，仰角 asin(1/√3)，滚转 0°。
#: ``tests/test_camera_view_controls.py`` 会拿 ``get_default_cam_pos()`` 核对这组
#: 常量，pyvista 主题若改动会立刻暴露。
DEFAULT_AZIMUTH = 45.0
DEFAULT_ELEVATION = math.degrees(math.asin(1.0 / math.sqrt(3.0)))
DEFAULT_ROLL = 0.0


@dataclass(frozen=True)
class CameraPose:
    """相机姿态读数：角度单位为度，距离为渲染坐标单位（场景为 200³ 立方体）。"""

    azimuth: float
    elevation: float
    roll: float
    distance: float


def wrap_signed_degrees(value):
    """把角度折进 ``(-180°, 180°]``，``-0.0`` 归一为 ``0.0``。"""
    wrapped = math.fmod(float(value), 360.0)
    if wrapped <= -180.0:
        wrapped += 360.0
    elif wrapped > 180.0:
        wrapped -= 360.0
    return 0.0 if wrapped == 0.0 else wrapped


def clamp_elevation(value):
    """仰角是极角，直接夹紧到 ``[-90°, 90°]``，不做环绕。"""
    clamped = max(-90.0, min(90.0, float(value)))
    return 0.0 if clamped == 0.0 else clamped


def _image_up_reference(view_dir, azimuth, elevation):
    """像平面内滚转为 0° 时的向上方向（单位向量，必然垂直于 ``view_dir``）。"""
    projected = WORLD_UP - float(np.dot(WORLD_UP, view_dir)) * view_dir
    length = float(np.linalg.norm(projected))
    if length > DEGENERATE_THRESHOLD:
        return projected / length

    # 极点：世界向上轴与视线共线，投影退化为零。取 e→±90° 的极限方向
    # (-cos a, -sin a, 0)/(+cos a, +sin a, 0)，读写两端共用，保证往返一致。
    az = math.radians(azimuth)
    horizontal = np.array([math.cos(az), math.sin(az), 0.0])
    return horizontal if math.sin(math.radians(elevation)) < 0.0 else -horizontal


def _signed_angle(from_vector, to_vector, axis):
    """``from_vector`` 到 ``to_vector`` 绕单位轴 ``axis`` 的带符号夹角（度）。"""
    start = from_vector - float(np.dot(from_vector, axis)) * axis
    end = to_vector - float(np.dot(to_vector, axis)) * axis
    start_length = float(np.linalg.norm(start))
    end_length = float(np.linalg.norm(end))
    if start_length <= DEGENERATE_THRESHOLD or end_length <= DEGENERATE_THRESHOLD:
        return 0.0
    start = start / start_length
    end = end / end_length
    sine = float(np.dot(np.cross(start, end), axis))
    cosine = float(np.clip(np.dot(start, end), -1.0, 1.0))
    return math.degrees(math.atan2(sine, cosine))


def _rotate_about_axis(vector, axis, angle):
    """Rodrigues 旋转：``axis`` 为单位向量。"""
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return (
        vector * cosine
        + np.cross(axis, vector) * sine
        + axis * float(np.dot(axis, vector)) * (1.0 - cosine)
    )


def _as_vector(values):
    return np.asarray(values, dtype=np.float64).reshape(3)


def pose_from_vectors(position, focal_point, view_up):
    """由真实相机的三个向量读出姿态；输入退化（距离为零或非有限）时返回 ``None``。"""
    offset = _as_vector(position) - _as_vector(focal_point)
    distance = float(np.linalg.norm(offset))
    if not math.isfinite(distance) or distance <= 0.0:
        return None

    view_dir = -offset / distance
    elevation = math.degrees(
        math.asin(max(-1.0, min(1.0, float(offset[2]) / distance)))
    )
    azimuth = math.degrees(math.atan2(float(offset[1]), float(offset[0])))
    roll = _signed_angle(
        _image_up_reference(view_dir, azimuth, elevation),
        _as_vector(view_up),
        view_dir,
    )
    return CameraPose(
        wrap_signed_degrees(azimuth),
        clamp_elevation(elevation),
        wrap_signed_degrees(roll),
        distance,
    )


def vectors_from_pose(azimuth, elevation, roll, distance, focal_point):
    """由姿态参数与观察中心算出 ``(position, view_up)``，与 :func:`pose_from_vectors` 互逆。

    只改变相机姿态：观察中心 ``focal_point`` 原样保留，距离是相机到观察中心的距离。
    """
    azimuth = wrap_signed_degrees(azimuth)
    elevation = clamp_elevation(elevation)
    roll = wrap_signed_degrees(roll)
    distance = float(distance)
    if not math.isfinite(distance) or distance <= 0.0:
        raise ValueError("相机距离必须是大于零的有限数值。")

    focal_point = _as_vector(focal_point)
    az = math.radians(azimuth)
    el = math.radians(elevation)
    horizontal = math.cos(el)
    offset = distance * np.array(
        [math.cos(az) * horizontal, math.sin(az) * horizontal, math.sin(el)]
    )
    view_dir = -offset / distance
    up = _rotate_about_axis(
        _image_up_reference(view_dir, azimuth, elevation),
        view_dir,
        math.radians(roll),
    )
    return (
        tuple(float(component) for component in focal_point + offset),
        tuple(float(component) for component in up),
    )


class _CameraValueBox(QDoubleSpinBox):
    """「相机视角」数值框基类。

    - 编辑被 Esc 中止时通知面板：Qt 中止后既不还原文本也不发
      ``editingFinished``，只靠 ``textEdited`` 记录的“编辑中”标记会永远留着，
      输入框就再也跟不上相机了。
    - 滚轮只在获得焦点时才用于微调：否则面板会吃掉滚轮，右上控制页就没法
      上下滚动了。
    """

    edit_cancelled = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.edit_cancelled.emit()
        super().keyPressEvent(event)

    def _wheel_adjusts_value(self):
        """滚轮是否用于微调：只有已经获得焦点时才交给 Qt，否则让页面滚动。"""
        return self.hasFocus()

    def wheelEvent(self, event):
        if self._wheel_adjusts_value():
            super().wheelEvent(event)
            return
        event.ignore()


class _AngleSpinBox(_CameraValueBox):
    """角度输入框：在 ``valueFromText`` 里规范化，交给 Qt 自己把结果写回文本。

    这样「输入 270°」在 Qt 解释文本时就已经变成等价的 -90°，不会出现
    “提交 → 回读 → 再写值”的递归改写。
    """

    def __init__(self, normalize, parent=None):
        super().__init__(parent)
        self._normalize = normalize
        self.setDecimals(CameraViewPanel.ANGLE_DECIMALS)
        # 允许输入 ±360° 之类的等价角度，规范化交给 valueFromText。
        self.setRange(-360.0, 360.0)
        self.setSingleStep(CameraViewPanel.ANGLE_STEP)
        self.setSuffix("°")
        self.setButtonSymbols(QAbstractSpinBox.NoButtons)

    def valueFromText(self, text):
        cleaned = str(text).replace(self.suffix(), "").strip()
        try:
            value = float(cleaned)
        except (TypeError, ValueError):
            return self.value()
        if not math.isfinite(value):
            return self.value()
        return self._normalize(value)


class CameraViewPanel(QWidget):
    """「相机视角」卡片内容：四个数值框 + 唯一的「重置视角」按钮。

    面板只负责输入与回显，不直接接触相机：数值提交后发 :attr:`pose_committed`，
    重置按钮发 :attr:`reset_requested`，由主窗口连接渲染状态与相机实例。
    """

    ANGLE_DECIMALS = 1
    ANGLE_STEP = 1.0
    DISTANCE_DECIMALS = 2
    DISTANCE_STEP = 10.0

    VALUE_BOX_HEIGHT = 30
    MIN_VALUE_BOX_WIDTH = 84
    MAX_VALUE_BOX_WIDTH = 116
    COLUMN_SPACING = 10
    ROW_SPACING = 8

    BUTTON_HEIGHT = 32
    BUTTON_WIDTH = 92

    #: 字段顺序即 2×2 网格的排布顺序（一行两个参数）。
    FIELDS = (
        ("azimuth", "方位角", "绕世界 +Z 轴（数据体 E 轴）的水平方位角。\n"
                              "0° = 相机在 +Kx 一侧，+90° = 转到 +Ky 一侧，范围 -180° ~ 180°；\n"
                              "↑↓ 微调，聚焦后滚轮按 1° 同样生效。"),
        ("elevation", "仰角", "相机相对 Kx-Ky 平面的仰角。\n"
                              "0° = 平视，+90° = 从 +E 方向俯视（极点），范围 -90° ~ 90°；\n"
                              "↑↓ 微调，聚焦后滚轮按 1° 同样生效。"),
        ("roll", "滚转角", "画面绕视线方向的旋转角。\n"
                           "0° = 画面向上对齐世界 +Z 轴（E 轴），范围 -180° ~ 180°；\n"
                           "↑↓ 微调，聚焦后滚轮按 1° 同样生效。"),
        ("distance", "距离", "相机到观察中心的距离（渲染坐标单位，场景为 200×200×200）。\n"
                             "只沿视线方向平移相机，不会改变观察中心；↑↓ 微调，聚焦后滚轮同样生效。"),
    )

    #: 第一个参数是输入框当前姿态，第二个是本次被用户改动的那一项的名字
    #: （``"azimuth"`` / ``"elevation"`` / ``"roll"`` / ``"distance"``）。
    #: 接收方只应采用该项、其余沿用相机的精确值，否则输入框的显示舍入会反写相机。
    pose_committed = pyqtSignal(object, str)
    reset_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._syncing = False
        # 用户键入但尚未提交的字段：后台回读时跳过，避免覆盖正在编辑的文本。
        self._dirty = set()
        self._boxes = {}
        self._value_box_width = self.MIN_VALUE_BOX_WIDTH

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(self.COLUMN_SPACING)
        grid.setVerticalSpacing(self.ROW_SPACING)
        self._labels = []
        for index, (key, text, tooltip) in enumerate(self.FIELDS):
            row, column = divmod(index, 2)
            label = QLabel(text, self)
            label.setStyleSheet(theme.field_label_qss())
            label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
            box = self._create_value_box(key, tooltip)
            label.setBuddy(box)
            grid.addWidget(label, row, column * 2, Qt.AlignVCenter)
            grid.addWidget(box, row, column * 2 + 1, Qt.AlignVCenter)
            self._labels.append(label)
            self._boxes[key] = box
        layout.addLayout(grid)

        self.reset_button = self._create_reset_button()
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch()
        button_row.addWidget(self.reset_button)
        button_row.addStretch()
        layout.addLayout(button_row)
        self.fit_value_box_width(self._preferred_content_width())

    # ------------------------------------------------------------------
    # 控件创建
    # ------------------------------------------------------------------
    def _create_value_box(self, key, tooltip):
        if key == "distance":
            box = _CameraValueBox(self)
            box.setDecimals(self.DISTANCE_DECIMALS)
            box.setRange(MIN_DISTANCE, MAX_DISTANCE)
            box.setSingleStep(self.DISTANCE_STEP)
            box.setButtonSymbols(QAbstractSpinBox.NoButtons)
            # 超出 [MIN_DISTANCE, MAX_DISTANCE] 时就近修正（默认会退回上一个值），
            # 保证距离永远是合法的正数。
            box.setCorrectionMode(QAbstractSpinBox.CorrectToNearestValue)
        else:
            normalize = clamp_elevation if key == "elevation" else wrap_signed_degrees
            box = _AngleSpinBox(normalize, self)

        box.setToolTip(tooltip)
        box.setKeyboardTracking(False)
        box.setAlignment(Qt.AlignCenter)
        box.setFixedHeight(self.VALUE_BOX_HEIGHT)
        box.setFixedWidth(self.MIN_VALUE_BOX_WIDTH)
        box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        box.setStyleSheet(theme.value_input_qss())

        line_edit = box.lineEdit()
        if line_edit is not None:
            line_edit.textEdited.connect(lambda _text, name=key: self._mark_edited(name))
        box.valueChanged.connect(lambda _value, name=key: self._on_field_committed(name))
        # 失焦或回车后文本已被 Qt 解释，无论成功与否都解除“编辑中”标记；
        # 无法解释的残留文本会在下一次回读时被规范值替换。
        box.editingFinished.connect(lambda name=key: self._dirty.discard(name))
        box.edit_cancelled.connect(lambda name=key: self._cancel_edit(name))
        return box

    def _create_reset_button(self):
        button = ActionButton(self)
        button.setFixedHeight(self.BUTTON_HEIGHT)
        button.setFixedWidth(self.BUTTON_WIDTH)
        button.setText("重置视角")
        button.setToolTip(
            "恢复默认观察方向与滚转（应用初次展示 3D 数据的等轴视图），\n"
            "观察中心对准当前展示内容，并自动调整距离完整显示。\n"
            "不改变数据、裁剪范围、时间帧、色带、色阶与去噪参数。"
        )
        theme.style_push_button(button, "primary")
        button.clicked.connect(self.reset_requested)
        return button

    def _preferred_content_width(self):
        """构造期先按最小卡片宽度定一个可读的数值框宽度，随后由页面自适应覆盖。"""
        return (
            2 * self._label_width()
            + 2 * self.MIN_VALUE_BOX_WIDTH
            + 3 * self.COLUMN_SPACING
        )

    def _label_width(self):
        return max(label.sizeHint().width() for label in self._labels)

    def fit_value_box_width(self, available_width):
        """按卡片内的可用宽度分配数值框宽度，保证标签与数值都不被截断。"""
        width = (
            (int(available_width) - 3 * self.COLUMN_SPACING) // 2 - self._label_width()
        )
        width = max(self.MIN_VALUE_BOX_WIDTH, min(self.MAX_VALUE_BOX_WIDTH, width))
        if width == self._value_box_width:
            return
        self._value_box_width = width
        for box in self._boxes.values():
            box.setFixedWidth(width)

    # ------------------------------------------------------------------
    # 读 / 写
    # ------------------------------------------------------------------
    def current_pose(self):
        """当前输入框代表的姿态（已规范化）。"""
        return CameraPose(
            wrap_signed_degrees(self._boxes["azimuth"].value()),
            clamp_elevation(self._boxes["elevation"].value()),
            wrap_signed_degrees(self._boxes["roll"].value()),
            max(MIN_DISTANCE, min(MAX_DISTANCE, self._boxes["distance"].value())),
        )

    def sync_pose(self, pose, *, force=False):
        """按真实相机姿态回读输入框；编辑中（未提交）的框跳过，除非 ``force``。"""
        if pose is None:
            return
        values = {
            "azimuth": wrap_signed_degrees(pose.azimuth),
            "elevation": clamp_elevation(pose.elevation),
            "roll": wrap_signed_degrees(pose.roll),
            "distance": pose.distance,
        }
        self._syncing = True
        blockers = []
        try:
            for key, box in self._boxes.items():
                if not force and self._is_editing(key, box):
                    continue
                blockers.append(QSignalBlocker(box))
                box.setValue(values[key])
        finally:
            self._syncing = False
            del blockers

    def _is_editing(self, key, box):
        """只有确实存在未提交的用户输入时才跳过回读，避免标记卡死后输入框失联。

        ``isModified()`` 由 Qt 在用户键入时置位、在 ``setValue`` 重写文本后复位，
        正好表示“显示的不是已提交的值”。Esc 中止编辑与失焦提交都会解除它。
        """
        if key not in self._dirty:
            return False
        line_edit = box.lineEdit()
        return line_edit is not None and line_edit.isModified()

    def _mark_edited(self, key):
        self._dirty.add(key)

    def _cancel_edit(self, key):
        """Esc 中止编辑：解除编辑中标记，并按已提交的值重画文本。"""
        self._dirty.discard(key)
        box = self._boxes[key]
        blockers = [QSignalBlocker(box)]
        try:
            # QT 的 setValue 即使值不变也会重写文本，用它还原中止编辑后的显示。
            box.setValue(box.value())
        finally:
            del blockers

    def _on_field_committed(self, key):
        if self._syncing:
            return
        self._dirty.discard(key)
        self.pose_committed.emit(self.current_pose(), key)
