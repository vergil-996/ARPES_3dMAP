"""可编辑的三维不透明度曲线：底轨加点、拖点编辑、右键或 Delete 删除。"""
from __future__ import annotations

import math
import time
import weakref

import numpy as np
from PyQt5 import sip
from PyQt5.QtCore import QLineF, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QButtonGroup, QHBoxLayout, QMenu, QToolButton, QToolTip, QVBoxLayout, QWidget,
)

import bandscope.ui.theme as theme
from bandscope.ui.groove_painting import paint_groove
from bandscope.ui.relief_painting import paint_raised_ellipse
from bandscope.ui.opacity_interpolation_switch import OpacityInterpolationSwitch
from bandscope.core.display_style import normalize_opacity_points
from bandscope.core.opacity_curve import (
    DEFAULT_OPACITY_INTERPOLATION,
    OPACITY_LINEAR,
    OPACITY_SMOOTH,
    evaluate_opacity_curve,
    insert_opacity_point,
    normalize_opacity_interpolation,
    sanitized_opacity_segments,
)


class OpacityInterpolationSelector(QWidget):
    """可见的动画开关，以及供既有调用方使用的隐藏按钮接口。"""

    interpolationChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.switch = OpacityInterpolationSwitch(self)
        layout.addWidget(self.switch)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for value, text in ((OPACITY_SMOOTH, "平滑"), (OPACITY_LINEAR, "直线")):
            button = QToolButton(self)
            button.setText(text)
            button.setCheckable(True)
            button.setFixedSize(44, 24)
            button.setAccessibleName(f"{text}插值")
            button.toggled.connect(
                lambda checked, mode=value: self._on_toggled(mode, checked)
            )
            self.group.addButton(button)
            self.buttons[value] = button
            button.hide()
        self.switch.interpolationChanged.connect(self._on_switch_changed)
        self.set_interpolation(DEFAULT_OPACITY_INTERPOLATION, animate=False)

    def _set_buttons(self, mode):
        for name, button in self.buttons.items():
            previous = button.blockSignals(True)
            button.setChecked(name == mode)
            button.blockSignals(previous)

    def _on_switch_changed(self, mode):
        self._set_buttons(mode)
        self.interpolationChanged.emit(mode)

    def _on_toggled(self, mode, checked):
        if checked:
            self.set_interpolation(mode, animate=True)
            self.interpolationChanged.emit(mode)

    def interpolation(self):
        return self.switch.interpolation()

    def set_interpolation(self, value, animate=True):
        mode = normalize_opacity_interpolation(value, DEFAULT_OPACITY_INTERPOLATION)
        self.switch.set_interpolation(mode, animate=animate)
        self._set_buttons(mode)


class OpacityCurveCanvas(QWidget):
    """曲线本体与底轨。左键拖点编辑；从底轨向上拖加点；右键或 Delete 删除内部点。"""

    pointsChanged = pyqtSignal()
    pointsCommitted = pyqtSignal()

    PLOT_MARGIN_LEFT = 32
    PLOT_MARGIN_RIGHT = 14
    PLOT_TOP = 12
    PLOT_HEIGHT = 116
    RAIL_TOP_GAP = 26
    RAIL_HEIGHT = 12
    POINT_RADIUS = 5.5
    RAIL_DOT_RADIUS = 6.0
    HIT_RADIUS = 11.0
    RAIL_HIT_PADDING = 9
    INSERT_UP_THRESHOLD = 8
    BACKFILL_DURATION_MS = 200
    BACKFILL_INTERVAL_MS = 16

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setFixedHeight(self.PLOT_TOP + self.PLOT_HEIGHT + self.RAIL_TOP_GAP
                            + self.RAIL_HEIGHT + 24)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("不透明度曲线")
        self.setToolTip(
            "拖动控制点同时调整强度和 α；从底轨向上拖到曲线上添加控制点；\n"
            "右键内部控制点或按 Delete 删除。"
        )
        self._points = [(float(x), float(a)) for x, a in normalize_opacity_points(None)]
        self._interpolation = DEFAULT_OPACITY_INTERPOLATION
        self._segments = None
        self._selected = None
        self._hover = None
        self._mode = None
        self._drag_index = None
        self._point_moved = False
        self._inserted_pending_commit = False
        self._cycle_position = None
        self._rail_intensity = 0.5
        self._press_y = 0
        self._insert_intensity = 0.5
        self._insert_y = 0.0
        self._insert_origin = None
        self._backfill_start = None
        self._backfill_intensity = 0.0
        self._backfill_timer = QTimer(self)
        self._backfill_timer.setInterval(self.BACKFILL_INTERVAL_MS)
        self._backfill_timer.timeout.connect(self._advance_backfill)
        reference = weakref.ref(self)

        def on_theme_changed(_name):
            canvas = reference()
            if canvas is not None and not sip.isdeleted(canvas):
                canvas.update()

        self._theme_listener = on_theme_changed
        theme.changed.connect(self._theme_listener)
        self.destroyed.connect(self._disconnect_theme)

    def _disconnect_theme(self, *_args):
        theme.changed.disconnect(self._theme_listener)

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    @staticmethod
    def _canonical(points):
        return [(float(x), float(a)) for x, a in normalize_opacity_points(points)]

    def points(self):
        return [list(point) for point in self._points]

    def segments(self):
        return None if self._segments is None else [
            None if entry is None else list(entry) for entry in self._segments
        ]

    def interpolation(self):
        return self._interpolation

    def set_points(self, points):
        self._points = self._canonical(points)
        self._segments = None
        self._reset_selection()
        self._clamp_rail()
        self.update()

    def set_interpolation(self, value):
        self._interpolation = normalize_opacity_interpolation(
            value, DEFAULT_OPACITY_INTERPOLATION,
        )
        self.update()

    def set_opacity(self, opacity):
        opacity = opacity if isinstance(opacity, dict) else {}
        self._points = self._canonical(opacity.get("points"))
        self._interpolation = normalize_opacity_interpolation(
            opacity.get("interpolation"), OPACITY_LINEAR,
        )
        self._segments = sanitized_opacity_segments(self._points, opacity.get("segments"))
        self._reset_selection()
        self._clamp_rail()
        self.update()

    def _reset_selection(self):
        self._selected = None
        self._hover = None
        self._cycle_position = None

    def curve_alpha(self, intensity):
        return float(evaluate_opacity_curve(
            float(intensity), self._points, interpolation=self._interpolation,
            segments=self._segments,
        ))

    def selected_index(self):
        return self._selected

    def is_internal(self, index):
        return index is not None and 0 < index < len(self._points) - 1

    def point_position(self, index):
        intensity, alpha = self._points[index]
        return QPointF(self._x(intensity), self._y(alpha))

    # ------------------------------------------------------------------
    # 几何
    # ------------------------------------------------------------------
    def _plot_rect(self):
        return QRect(
            self.PLOT_MARGIN_LEFT,
            self.PLOT_TOP,
            max(1, self.width() - self.PLOT_MARGIN_LEFT - self.PLOT_MARGIN_RIGHT),
            self.PLOT_HEIGHT,
        )

    def _rail_rect(self):
        plot = self._plot_rect()
        return QRect(plot.left(), plot.bottom() + self.RAIL_TOP_GAP, plot.width(), self.RAIL_HEIGHT)

    def _x(self, intensity):
        plot = self._plot_rect()
        return plot.left() + float(intensity) * plot.width()

    def _y(self, alpha):
        plot = self._plot_rect()
        return plot.bottom() - float(np.clip(alpha, 0.0, 1.0)) * plot.height()

    def _intensity_at(self, x):
        plot = self._plot_rect()
        return float(np.clip((float(x) - plot.left()) / max(plot.width(), 1), 0.0, 1.0))

    def _alpha_at(self, y):
        plot = self._plot_rect()
        return float(np.clip((plot.bottom() - float(y)) / max(plot.height(), 1), 0.0, 1.0))

    def _rail_point(self):
        return QPointF(self._x(self._rail_intensity), self._rail_rect().center().y())

    def _clamp_rail(self):
        self._rail_intensity = float(np.clip(self._rail_intensity, 0.0, 1.0))

    def _point_candidates(self, position):
        target = QPointF(position)
        candidates = []
        for index, point in enumerate(self._points):
            center = self.point_position(index)
            distance = math.hypot(target.x() - center.x(), target.y() - center.y())
            if distance <= self.HIT_RADIUS:
                candidates.append((distance, index))
        return [index for _distance, index in sorted(candidates)]

    def _point_at(self, position):
        candidates = self._point_candidates(position)
        return candidates[0] if candidates else None

    def _point_for_press(self, position):
        candidates = self._point_candidates(position)
        if not candidates:
            return None
        can_cycle = (
            self._cycle_position is not None and self._selected in candidates
            and math.hypot(position.x() - self._cycle_position.x(),
                           position.y() - self._cycle_position.y()) <= 2.0
        )
        if can_cycle and len(candidates) > 1:
            return candidates[(candidates.index(self._selected) + 1) % len(candidates)]
        return candidates[0]

    def _rail_hit(self, position):
        rail = self._rail_rect().adjusted(0, -self.RAIL_HIT_PADDING, 0, self.RAIL_HIT_PADDING)
        return rail.contains(position)

    def _clamp_insert_y(self, y):
        """黑点可在底轨与横轴之间连续移动，只限制在图框顶部与底轨中心之间。"""
        plot = self._plot_rect()
        return float(min(max(float(y), float(plot.top())),
                         float(self._rail_rect().center().y())))

    def _neighbor_bounds(self, index):
        if index <= 0:
            return 0.0, 0.0
        if index >= len(self._points) - 1:
            return 1.0, 1.0
        return self._points[index - 1][0], self._points[index + 1][0]

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus(Qt.MouseFocusReason)
        index = self._point_for_press(event.pos())
        self._inserted_pending_commit = False
        if index is not None:
            self._selected = index
            self._hover = index
            self._mode = "point"
            self._drag_index = index
            self._point_moved = False
            event.accept()
            self.update()
            return
        if self._rail_hit(event.pos()):
            self._mode = "rail"
            self._rail_intensity = self._intensity_at(event.pos().x())
            self._press_y = event.pos().y()
            self.update()
            event.accept()
            return
        self._selected = None
        super().mousePressEvent(event)
        self.update()

    def mouseMoveEvent(self, event):
        if self._mode == "rail":
            self._rail_intensity = self._intensity_at(event.pos().x())
            if (self._press_y - event.pos().y() >= self.INSERT_UP_THRESHOLD
                    and event.pos().y() < self._rail_rect().top()):
                self._mode = "insert"
                self._insert_intensity = self._rail_intensity
                self._insert_origin = self._rail_intensity
                self._insert_y = self._clamp_insert_y(event.pos().y())
                self._try_insert(event.pos())
            self.update()
            event.accept()
            return
        if self._mode == "insert":
            if event.pos().y() >= self._rail_rect().center().y() - 1:
                # 黑点被拖回底轨轮廓内：重新挂回底轨，可直接继续水平拖动。
                self._mode = "rail"
                self._insert_origin = None
                self._rail_intensity = self._intensity_at(event.pos().x())
                self._press_y = event.pos().y()
                self.update()
                event.accept()
                return
            # 候选强度在开始上推时锁定；黑点沿竖直虚线连续跟随光标上移。
            self._insert_y = self._clamp_insert_y(event.pos().y())
            self._try_insert(event.pos())
            self.update()
            event.accept()
            return
        if self._mode == "point" and self._drag_index is not None:
            self._move_point(self._drag_index, event.pos())
            event.accept()
            return
        self._update_hover(event.pos())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        if self._mode == "point":
            self._mode = None
            self._drag_index = None
            moved = self._point_moved
            inserted = self._inserted_pending_commit
            self._point_moved = False
            self._inserted_pending_commit = False
            self._cycle_position = None if moved else QPointF(event.pos())
            event.accept()
            self._update_hover(event.pos())
            if moved or inserted:
                self.pointsCommitted.emit()
            return
        if self._mode == "rail":
            self._mode = None
            event.accept()
            self._update_hover(event.pos())
            return
        if self._mode == "insert":
            # 碰线前松手：不生成点，圆点留在底轨原位。
            self._mode = None
            self._insert_origin = None
            event.accept()
            self._update_hover(event.pos())
            return
        super().mouseReleaseEvent(event)

    def _try_insert(self, position):
        intensity = self._insert_intensity
        target = QPointF(position)
        for index, (x, _alpha) in enumerate(self._points):
            if not math.isclose(x, intensity, rel_tol=0.0, abs_tol=1.0e-12):
                continue
            center = self.point_position(index)
            if math.hypot(target.x() - center.x(), target.y() - center.y()) <= self.POINT_RADIUS:
                self._selected = index
                self._drag_index = index
                self._mode = "point"
                self._point_moved = False
                return
        curve_alpha = self.curve_alpha(intensity)
        if self._insert_y > self._y(curve_alpha) + 0.5:
            return
        try:
            points, segments, index = insert_opacity_point(
                self._points, intensity, interpolation=self._interpolation,
                segments=self._segments,
            )
        except ValueError:
            return
        self._points = self._canonical(points)
        self._segments = segments
        self._selected = index
        self._drag_index = index
        self._mode = "point"
        self._point_moved = False
        self._inserted_pending_commit = True
        self._start_backfill(self._insert_origin if self._insert_origin is not None else intensity)
        self.update()

    def _bounded_point(self, index, intensity, alpha):
        if index <= 0:
            intensity = 0.0
        elif index >= len(self._points) - 1:
            intensity = 1.0
        else:
            low, high = self._neighbor_bounds(index)
            intensity = float(np.clip(intensity, low, high))
        return float(intensity), float(np.clip(alpha, 0.0, 1.0))

    def _move_point(self, index, position):
        if not 0 <= index < len(self._points):
            return
        intensity, alpha = self._bounded_point(
            index, self._intensity_at(position.x()), self._alpha_at(position.y()),
        )
        if self._points[index] == (intensity, alpha):
            return
        self._points[index] = (intensity, alpha)
        self._segments = None
        self._cycle_position = None
        self._point_moved = True
        self.pointsChanged.emit()
        self.update()

    def _update_hover(self, position):
        index = self._point_at(position)
        if index != self._hover:
            self._hover = index
            self.update()
        on_rail = self._rail_hit(position)
        self.setCursor(Qt.PointingHandCursor if index is not None or on_rail else Qt.ArrowCursor)
        if index is None:
            if on_rail:
                intensity = self._intensity_at(position.x())
                QToolTip.showText(
                    self.mapToGlobal(position),
                    f"强度 {intensity * 100.0:.1f}%\n向上拖到曲线上添加控制点", self,
                )
            else:
                QToolTip.hideText()
            return
        intensity, alpha = self._points[index]
        QToolTip.showText(
            self.mapToGlobal(position),
            f"强度 {intensity * 100.0:.1f}%　α {alpha:.3f}", self,
        )

    def leaveEvent(self, event):
        QToolTip.hideText()
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self._selected is not None:
            if self.delete_selected_point():
                event.accept()
                return
        steps = {
            Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0),
            Qt.Key_Down: (0, -1), Qt.Key_Up: (0, 1),
            Qt.Key_PageDown: (0, -10), Qt.Key_PageUp: (0, 10),
        }
        if event.key() in steps and self._selected is not None:
            self._nudge_selected(*steps[event.key()])
            event.accept()
            return
        super().keyPressEvent(event)

    def _nudge_selected(self, intensity_step, alpha_step):
        index = self._selected
        intensity, alpha = self._points[index]
        if intensity_step:
            intensity += intensity_step / 1000.0
        if alpha_step:
            alpha += alpha_step / 100.0
        intensity, alpha = self._bounded_point(index, intensity, alpha)
        self._points[index] = (intensity, alpha)
        self._segments = None
        self._cycle_position = None
        self.pointsChanged.emit()
        self.pointsCommitted.emit()
        self.update()

    def delete_selected_point(self):
        index = self._selected
        if not self.is_internal(index):
            return False
        self._points.pop(index)
        self._segments = None
        self._selected = None
        self._hover = None
        self._cycle_position = None
        self.pointsChanged.emit()
        self.pointsCommitted.emit()
        self.update()
        return True

    def contextMenuEvent(self, event):
        index = self._point_at(event.pos())
        if not self.is_internal(index):
            return super().contextMenuEvent(event)
        self._selected = index
        self.update()
        menu = QMenu(self)
        action = menu.addAction("删除控制点")
        action.triggered.connect(self.delete_selected_point)
        menu.exec_(event.globalPos())
        event.accept()

    # ------------------------------------------------------------------
    # 底轨回填动画：只重绘控件
    # ------------------------------------------------------------------
    def _start_backfill(self, intensity):
        self._backfill_intensity = float(np.clip(intensity, 0.0, 1.0))
        self._backfill_start = time.monotonic()
        if not self._backfill_timer.isActive():
            self._backfill_timer.start()
        self.update()

    def backfill_progress(self):
        if self._backfill_start is None:
            return None
        elapsed = (time.monotonic() - self._backfill_start) * 1000.0
        return float(np.clip(elapsed / self.BACKFILL_DURATION_MS, 0.0, 1.0))

    def _advance_backfill(self):
        if self._backfill_start is None or self.backfill_progress() >= 1.0:
            self._backfill_timer.stop()
            self._backfill_start = None
        self.update()

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    @staticmethod
    def _contrast_color():
        return QColor(theme.TEXT_1)

    def _paint_background(self, painter, plot):
        painter.fillRect(plot, QColor(theme.BG_2))
        font = painter.font()
        font.setPointSizeF(7.5)
        painter.setFont(font)
        for fraction in (0.0, 0.5, 1.0):
            y = self._y(fraction)
            painter.setPen(QPen(QColor(theme.BORDER_HEX), 0.7, Qt.DotLine))
            painter.drawLine(QLineF(plot.left(), y, plot.right(), y))
            painter.setPen(QColor(theme.TEXT_3))
            painter.drawText(QRectF(1, y - 7, self.PLOT_MARGIN_LEFT - 5, 14),
                             Qt.AlignRight | Qt.AlignVCenter, f"{fraction:.1f}")
        for fraction in (0.0, 0.5, 1.0):
            x = self._x(fraction)
            painter.setPen(QPen(QColor(theme.BORDER_HEX), 0.7, Qt.DotLine))
            painter.drawLine(QLineF(x, plot.top(), x, plot.bottom()))
            painter.setPen(QColor(theme.TEXT_3))
            painter.drawText(QRectF(x - 24, plot.bottom() - 15, 48, 13), Qt.AlignCenter,
                             f"{fraction * 100.0:.0f}%")
        painter.setPen(QPen(QColor(theme.BORDER_HEX), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(plot)

    def _paint_rail(self, painter):
        paint_groove(painter, self._rail_rect())

    def _paint_connector(self, painter):
        rail = self._rail_rect()
        painter.setPen(QPen(QColor(theme.TEXT_3), 0.9, Qt.DashLine))
        if self._mode == "insert":
            # 竖直虚线是黑点向上移动的导轨，从底轨中心一直画到黑点。
            x = self._x(self._insert_intensity)
            painter.drawLine(QLineF(x, self._insert_y, x, float(rail.center().y())))
            return
        x = self._x(self._rail_intensity)
        alpha = self.curve_alpha(self._rail_intensity)
        painter.drawLine(QLineF(x, self._y(alpha), x, rail.top()))

    def _paint_curve(self, painter):
        first = self._points[0]
        path = QPainterPath(QPointF(self._x(first[0]), self._y(first[1])))
        groups = []
        for left, right in zip(self._points, self._points[1:]):
            if right[0] <= left[0]:
                groups.append((right, None))
                continue
            count = max(2, min(self._plot_rect().width() * 2,
                               int(math.ceil((right[0] - left[0]) * self._plot_rect().width() * 2))))
            groups.append((right, np.linspace(left[0], right[0], count + 1)[1:-1]))
        nonempty = [samples for _right, samples in groups if samples is not None and samples.size]
        alphas = iter(evaluate_opacity_curve(
            np.concatenate(nonempty) if nonempty else np.asarray([]),
            self._points, interpolation=self._interpolation, segments=self._segments,
        ))
        for right, samples in groups:
            if samples is not None:
                for intensity in samples:
                    path.lineTo(self._x(float(intensity)), self._y(float(next(alphas))))
            path.lineTo(self._x(right[0]), self._y(right[1]))
        painter.setPen(QPen(QColor(theme.ACCENT), 2.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def _paint_point(self, painter, index):
        # 曲线上的控制点只有黑色实心圆，不带外轮廓。
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#111111"))
        painter.drawEllipse(self.point_position(index), self.POINT_RADIUS, self.POINT_RADIUS)

    def _paint_points(self, painter):
        for index in range(len(self._points)):
            self._paint_point(painter, index)

    @staticmethod
    def _paint_raised_dot(painter, center, radius, *, pressed=False):
        rect = QRectF(center.x() - radius, center.y() - radius, radius * 2.0, radius * 2.0)
        paint_raised_ellipse(
            painter, rect, "#111111", "#050505", pressed=pressed,
        )

    def _paint_rail_handle(self, painter):
        center = self._rail_point()
        painter.setPen(QPen(self._contrast_color(), 1.4))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, self.RAIL_DOT_RADIUS + 2.5, self.RAIL_DOT_RADIUS + 2.5)
        if self._mode == "insert":
            # 黑点已脱离外轮廓，原位只留下空心轮廓。
            return
        progress = self.backfill_progress()
        fill = self.RAIL_DOT_RADIUS
        if progress is not None:
            if abs(self._rail_intensity - self._backfill_intensity) < 1.0e-9:
                fill *= progress
            else:
                # 动画期间底轨被移走时，回填仍发生在放点时的原位。
                self._paint_backfill_marker(painter)
        if fill > 0.0:
            self._paint_raised_dot(painter, center, fill, pressed=self._mode == "rail")

    def _paint_backfill_marker(self, painter):
        progress = self.backfill_progress()
        if progress is None:
            return
        center = QPointF(self._x(self._backfill_intensity), self._rail_rect().center().y())
        painter.setPen(QPen(self._contrast_color(), 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, self.RAIL_DOT_RADIUS, self.RAIL_DOT_RADIUS)
        fill = self.RAIL_DOT_RADIUS * float(progress)
        if fill > 0.0:
            self._paint_raised_dot(painter, center, fill)

    def moving_dot_position(self):
        if self._mode != "insert":
            return None
        return QPointF(self._x(self._insert_intensity), self._insert_y)

    def _paint_moving_dot(self, painter):
        center = self.moving_dot_position()
        if center is None:
            return
        self._paint_raised_dot(painter, center, self.RAIL_DOT_RADIUS, pressed=True)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        plot = self._plot_rect()
        self._paint_background(painter, plot)
        self._paint_rail(painter)
        self._paint_connector(painter)
        self._paint_curve(painter)
        self._paint_points(painter)
        self._paint_rail_handle(painter)
        self._paint_moving_dot(painter)


class OpacityCurveEditor(QWidget):
    valueChanged = pyqtSignal()
    valueCommitted = pyqtSignal()

    def __init__(self, parent=None, *, show_interpolation=True):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.interpolation_selector = OpacityInterpolationSelector(self)
        self.interpolation_selector.interpolationChanged.connect(self._on_interpolation_changed)
        if show_interpolation:
            header = QHBoxLayout()
            header.setContentsMargins(0, 0, 0, 4)
            header.addStretch()
            header.addWidget(self.interpolation_selector)
            layout.addLayout(header)
        else:
            self.interpolation_selector.hide()
        self.curve = OpacityCurveCanvas(self)
        self.curve.pointsChanged.connect(self.valueChanged)
        self.curve.pointsCommitted.connect(self.valueCommitted)
        layout.addWidget(self.curve)
        self.setMinimumHeight(self.curve.height() + (28 if show_interpolation else 0))
        self.setAccessibleName("不透明度曲线")

    def _on_interpolation_changed(self, mode):
        self.curve.set_interpolation(mode)
        self.valueChanged.emit()
        self.valueCommitted.emit()

    def points(self):
        return self.curve.points()

    def set_points(self, points):
        self.curve.set_points(points)

    def interpolation(self):
        return self.curve.interpolation()

    def set_interpolation(self, value):
        self.interpolation_selector.set_interpolation(value, animate=False)
        self.curve.set_interpolation(value)

    def segments(self):
        return self.curve.segments()

    def opacity(self):
        result = {
            "points": self.points(),
            "interpolation": self.interpolation(),
        }
        segments = self.segments()
        if segments is not None:
            result["segments"] = segments
        return result

    def set_opacity(self, opacity):
        self.curve.set_opacity(opacity)
        self.interpolation_selector.set_interpolation(
            self.curve.interpolation(), animate=False,
        )

    def selected_index(self):
        return self.curve.selected_index()

    def delete_selected_point(self):
        return self.curve.delete_selected_point()

    def curve_alpha(self, intensity):
        return self.curve.curve_alpha(intensity)

    def sync_from_proxies(self):
        self.curve.update()
