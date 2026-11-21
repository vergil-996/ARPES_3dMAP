"""可编辑的三维不透明度曲线：底轨加点、拖点编辑、右键或 Delete 删除。"""
from __future__ import annotations

import math
import time
import weakref

import numpy as np
from PyQt5 import sip
from PyQt5.QtCore import QLineF, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QMenu, QToolTip, QVBoxLayout, QWidget

import bandscope.ui.theme as theme
from bandscope.core.display_style import MIN_OPACITY_POINT_GAP, normalize_opacity_points


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
        self._selected = None
        self._hover = None
        self._mode = None
        self._drag_index = None
        self._point_moved = False
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

    def set_points(self, points):
        self._points = self._canonical(points)
        if self._selected is not None and self._selected >= len(self._points):
            self._selected = None
        self._clamp_rail()
        self.update()

    def curve_alpha(self, intensity):
        xs = np.asarray([point[0] for point in self._points], dtype=np.float64)
        alphas = np.asarray([point[1] for point in self._points], dtype=np.float64)
        return float(np.interp(float(intensity), xs, alphas))

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

    def _point_at(self, position):
        target = QPointF(position)
        best = None
        for index, point in enumerate(self._points):
            center = self.point_position(index)
            distance = math.hypot(target.x() - center.x(), target.y() - center.y())
            if distance <= self.HIT_RADIUS and (best is None or distance < best[0]):
                best = (distance, index)
        return None if best is None else best[1]

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
        index = self._point_at(event.pos())
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
            self._point_moved = False
            event.accept()
            self._update_hover(event.pos())
            if moved:
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
        existing = self._point_at(position)
        if existing is not None:
            # 碰到已有点：选中并接着拖动该点。
            self._selected = existing
            self._drag_index = existing
            self._mode = "point"
            self._move_point(existing, position)
            return
        curve_alpha = self.curve_alpha(intensity)
        if self._insert_y > self._y(curve_alpha) + 0.5:
            # 黑点还在底轨与横轴之间或尚未到达曲线。
            return
        index = next(
            (position for position, (x, _value) in enumerate(self._points) if x > intensity),
            len(self._points),
        )
        if index <= 0 or index >= len(self._points):
            return
        low = self._points[index - 1][0]
        high = self._points[index][0]
        if intensity <= low + MIN_OPACITY_POINT_GAP or intensity >= high - MIN_OPACITY_POINT_GAP:
            return
        self._points.insert(index, (float(intensity), float(curve_alpha)))
        self._selected = index
        self._drag_index = index
        self._mode = "point"
        self._start_backfill(self._insert_origin if self._insert_origin is not None else intensity)
        # 插入点落在原曲线上，形状不变；选中后继续拖动才提交预览。
        self.update()

    def _move_point(self, index, position):
        if not 0 <= index < len(self._points):
            return
        intensity = self._intensity_at(position.x())
        alpha = self._alpha_at(position.y())
        if index <= 0:
            intensity = 0.0
        elif index >= len(self._points) - 1:
            intensity = 1.0
        else:
            low, high = self._neighbor_bounds(index)
            intensity = float(np.clip(
                intensity, low + MIN_OPACITY_POINT_GAP, high - MIN_OPACITY_POINT_GAP,
            ))
        self._points[index] = (float(intensity), float(alpha))
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
        low, high = self._neighbor_bounds(index)
        if index <= 0:
            intensity = 0.0
        elif index >= len(self._points) - 1:
            intensity = 1.0
        else:
            intensity = float(np.clip(
                intensity, low + MIN_OPACITY_POINT_GAP, high - MIN_OPACITY_POINT_GAP,
            ))
        self._points[index] = (float(intensity), float(np.clip(alpha, 0.0, 1.0)))
        self.pointsChanged.emit()
        self.pointsCommitted.emit()
        self.update()

    def delete_selected_point(self):
        index = self._selected
        if not self.is_internal(index):
            return False
        self._points.pop(index)
        self._selected = None
        self._hover = None
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
        rail = self._rail_rect()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BG_4))
        painter.drawRoundedRect(QRectF(rail), rail.height() / 2.0, rail.height() / 2.0)
        painter.setPen(QPen(QColor(theme.BORDER_HEX), 0.8))
        painter.drawLine(QLineF(float(rail.left()), rail.center().y() + 0.5,
                                float(rail.right()), rail.center().y() + 0.5))

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
        path = QPainterPath(QPointF(self._x(self._points[0][0]), self._y(self._points[0][1])))
        for intensity, alpha in self._points[1:]:
            path.lineTo(self._x(intensity), self._y(alpha))
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
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#111111"))
            painter.drawEllipse(center, fill, fill)

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
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#111111"))
            painter.drawEllipse(center, fill, fill)

    def moving_dot_position(self):
        if self._mode != "insert":
            return None
        return QPointF(self._x(self._insert_intensity), self._insert_y)

    def _paint_moving_dot(self, painter):
        center = self.moving_dot_position()
        if center is None:
            return
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#111111"))
        painter.drawEllipse(center, self.RAIL_DOT_RADIUS, self.RAIL_DOT_RADIUS)

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

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.curve = OpacityCurveCanvas(self)
        self.curve.pointsChanged.connect(self.valueChanged)
        self.curve.pointsCommitted.connect(self.valueCommitted)
        layout.addWidget(self.curve)
        self.setMinimumHeight(self.curve.height())
        self.setAccessibleName("不透明度曲线")

    def points(self):
        return self.curve.points()

    def set_points(self, points):
        self.curve.set_points(points)

    def selected_index(self):
        return self.curve.selected_index()

    def delete_selected_point(self):
        return self.curve.delete_selected_point()

    def curve_alpha(self, intensity):
        return self.curve.curve_alpha(intensity)

    def sync_from_proxies(self):
        self.curve.update()
