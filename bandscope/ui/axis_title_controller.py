# -*- coding: utf-8 -*-
"""轴标题：会话状态、编辑弹窗与 2D 命中/拖动交互。

轴标题只存在于本次会话，键为 ``(page_id, view, (轴名...))``：2D 用实际的横纵轴
组合（如 ``("X", "E")``），3D 固定 ``("X", "Y", "E")``。同一页面的 2D 与 3D
设置因此互不影响，刷新、切帧、调色带、切页都保留；关页或换数据由宿主清理。

2D 画布上的命中与拖动由 :class:`AxisTitleController` 独占：标题文字命中优先于
裁剪选区（命中即拿画布 widget lock，``RectangleSelector`` 会自行让路），未命中
时完全不干预原有操作。
"""
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

import bandscope.ui.theme as theme

#: 编辑框返回该值表示「恢复默认文字」（撤销本轴的文字覆盖）。
RESTORE_DEFAULT_TEXT = object()

AXIS_KEYS = ("x", "y")


def axis_title_key(page_id, view, axes) -> Tuple[str, str, Tuple[str, ...]]:
    """轴标题设置的会话键：页面 + 视图类型 + 轴组合。"""
    return (
        str(page_id or ""),
        str(view or ""),
        tuple(str(axis) for axis in (axes or ())),
    )


class AxisTitleState:
    """会话内的轴标题文字与位置覆盖。

    文字与位置分别记录：``text`` 为 ``None`` 表示该轴用默认文字，空串表示用户
    明确不要标题；``position`` 为 ``None`` 表示按坐标轴自动排布。
    """

    def __init__(self):
        self._texts: Dict[tuple, Dict[str, str]] = {}
        self._positions: Dict[tuple, Dict[str, Tuple[float, float]]] = {}

    # ------------------------------------------------------------ 文字
    def text(self, key, axis) -> Optional[str]:
        return self._texts.get(key, {}).get(axis)

    def texts(self, key) -> Dict[str, str]:
        return dict(self._texts.get(key, {}))

    def resolve(self, key, defaults: Dict[str, str]) -> Dict[str, str]:
        """默认文字与用户覆盖合成出实际使用的轴名。"""
        overrides = self._texts.get(key, {})
        return {
            axis: overrides.get(axis, str(default))
            for axis, default in defaults.items()
        }

    def set_text(self, key, axis, text) -> bool:
        text = str(text).strip()
        bucket = self._texts.setdefault(key, {})
        if bucket.get(axis) == text:
            return False
        bucket[axis] = text
        return True

    def reset_text(self, key, axis) -> bool:
        return self._drop(self._texts, key, axis)

    def reset_texts(self, key) -> bool:
        return bool(self._texts.pop(key, None))

    # ------------------------------------------------------------ 位置
    def position(self, key, axis) -> Optional[Tuple[float, float]]:
        return self._positions.get(key, {}).get(axis)

    def set_position(self, key, axis, position) -> bool:
        x, y = (float(value) for value in position)
        bucket = self._positions.setdefault(key, {})
        if bucket.get(axis) == (x, y):
            return False
        bucket[axis] = (x, y)
        return True

    def reset_position(self, key, axis) -> bool:
        return self._drop(self._positions, key, axis)

    def reset_positions(self, key) -> bool:
        return bool(self._positions.pop(key, None))

    # ------------------------------------------------------------ 生命周期
    def forget_page(self, page_id):
        """关闭页面时丢掉该页的全部设置。"""
        page_id = str(page_id or "")
        for store in (self._texts, self._positions):
            for key in [key for key in store if key[0] == page_id]:
                store.pop(key, None)

    def clear(self):
        """加载新数据时整体清空。"""
        self._texts.clear()
        self._positions.clear()

    @staticmethod
    def _drop(store, key, axis) -> bool:
        bucket = store.get(key)
        if not bucket or axis not in bucket:
            return False
        bucket.pop(axis)
        if not bucket:
            store.pop(key, None)
        return True


class _AxisTitleDialogBase(QDialog):
    """轴标题编辑框的公共外观：确定 / 取消在右，恢复默认文字在左。"""

    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setStyleSheet(self._stylesheet())
        self._restored = False

    @staticmethod
    def _stylesheet() -> str:
        return (
            "QDialog { background-color: %(BG2)s; }"
            "QLabel { color: %(T2)s; background: transparent; font-size: 12px; }"
            % {"BG2": theme.BG_2, "T2": theme.TEXT_2}
        )

    def _create_edit(self, text, placeholder):
        edit = QLineEdit(self)
        edit.setFixedHeight(32)
        edit.setMinimumWidth(260)
        edit.setStyleSheet(theme.value_input_qss())
        edit.setText(str(text or ""))
        edit.setPlaceholderText(placeholder)
        return edit

    def restored_default(self) -> bool:
        return self._restored

    def _on_restore(self):
        self._restored = True
        self.accept()

    def _create_button_row(self):
        """恢复默认文字在左，确定 / 取消在右。"""
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_restore = QPushButton("恢复默认文字", self)
        self.btn_restore.setFixedHeight(32)
        theme.style_push_button(self.btn_restore, "secondary")
        self.btn_restore.clicked.connect(self._on_restore)
        row.addWidget(self.btn_restore)
        row.addStretch()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self
        )
        self.buttons.button(QDialogButtonBox.Ok).setText("确定")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        for button in self.buttons.buttons():
            button.setFixedHeight(32)
            accept_role = self.buttons.buttonRole(button) == QDialogButtonBox.AcceptRole
            theme.style_push_button(button, "primary" if accept_role else "secondary")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        row.addWidget(self.buttons)
        return row


class AxisTitleDialog(_AxisTitleDialogBase):
    """单个轴标题的编辑框。"""

    def __init__(self, parent, *, axis_label, text, default_text):
        super().__init__(parent, "编辑轴标题")
        self.setMinimumWidth(360)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        label = QLabel(f"{axis_label}标题：", self)
        label.setStyleSheet(theme.field_label_qss())
        root.addWidget(label)

        self.edit = self._create_edit(text, "留空则隐藏该轴标题")
        root.addWidget(self.edit)
        self.edit.selectAll()
        self.edit.returnPressed.connect(self.accept)

        hint = QLabel("清空表示不显示该轴标题；仍可从右键菜单重新编辑。", self)
        hint.setWordWrap(True)
        root.addWidget(hint)

        root.addLayout(self._create_button_row())

    def title_text(self) -> str:
        return self.edit.text()


class ThreeAxisTitlesDialog(_AxisTitleDialogBase):
    """3D 主画布的三条轴名（X / Y / E）。"""

    def __init__(self, parent, *, axis_names, texts, defaults):
        super().__init__(parent, "轴标题")
        self.setMinimumWidth(420)
        self._defaults = dict(zip(axis_names, defaults))
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(8)

        self.edits: Dict[str, QLineEdit] = {}
        for axis in axis_names:
            row = QHBoxLayout()
            row.setSpacing(10)
            label = QLabel(f"{axis} 轴：", self)
            label.setFixedWidth(46)
            label.setStyleSheet(theme.field_label_qss())
            row.addWidget(label)
            edit = self._create_edit(texts.get(axis, ""), "留空则隐藏该轴标题")
            row.addWidget(edit, 1)
            self.edits[str(axis)] = edit
            root.addLayout(row)

        hint = QLabel("轴名位置仍随相机自动排布；清空表示不显示该轴标题。", self)
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addLayout(self._create_button_row())

    def title_texts(self) -> Dict[str, str]:
        return {axis: edit.text() for axis, edit in self.edits.items()}


@dataclass
class _DragState:
    axis: str
    key: tuple
    press: Tuple[float, float]
    anchor: Tuple[float, float]
    extent: object
    #: 按下时的标签几何（变换 / 位置 / mpl 的自动排布标志），取消拖动时原样放回。
    label_state: Tuple[object, Tuple[float, float], bool]
    moved: bool = False


class AxisTitleController:
    """2D 画布上的轴标题命中、双击改名、拖动与恢复。

    只认宿主给的上下文（页面 + 轴组合 + 默认文字），不读主窗口私有字段；
    改名弹窗、光标、重绘与状态变更通知都由宿主注入。
    """

    HIT_PADDING = 4.0
    DRAG_THRESHOLD = 2.0

    def __init__(
        self,
        canvas,
        axes,
        state: AxisTitleState,
        *,
        title_context: Callable[[], Optional[dict]],
        edit_request: Callable[[str, str, str], object],
        active_provider: Optional[Callable[[], bool]] = None,
        cursor_sink: Optional[Callable[[Optional[object]], None]] = None,
        on_change: Optional[Callable[[], None]] = None,
        draw: Optional[Callable[[], None]] = None,
    ):
        self.canvas = canvas
        self.axes = axes
        self.state = state
        self.title_context = title_context
        self.edit_request = edit_request
        self.active_provider = active_provider
        self.cursor_sink = cursor_sink
        self.on_change = on_change
        self.draw = draw
        self._drag: Optional[_DragState] = None
        self._hover_axis: Optional[str] = None
        self._locked = False
        self._blit_background = None
        self._cids = [
            canvas.mpl_connect("button_press_event", self._on_press),
            canvas.mpl_connect("button_release_event", self._on_release),
            canvas.mpl_connect("motion_notify_event", self._on_motion),
            canvas.mpl_connect("figure_leave_event", self._on_leave),
            canvas.mpl_connect("resize_event", self._on_resize),
        ]

    # ------------------------------------------------------------ 对外接口
    @property
    def dragging(self) -> bool:
        return self._drag is not None

    def disconnect(self):
        for cid in self._cids:
            try:
                self.canvas.mpl_disconnect(cid)
            except Exception:
                pass
        self._cids = []

    def context(self) -> Optional[dict]:
        """当前 2D 页的轴标题上下文；不在 2D 视图时返回 None。"""
        if not self._is_active():
            return None
        try:
            context = self.title_context()
        except Exception:
            return None
        if not isinstance(context, dict) or not context.get("defaults"):
            return None
        if not context.get("key"):
            return None
        return context

    def current_text(self, axis) -> Optional[str]:
        context = self.context()
        if context is None:
            return None
        return self._resolve(axis, context)

    def edit(self, axis) -> bool:
        """弹窗改名；取消返回 False。"""
        context = self.context()
        if context is None:
            return False
        defaults = context["defaults"]
        result = self.edit_request(
            axis, self._resolve(axis, context), str(defaults.get(axis, ""))
        )
        if result is None:
            return False
        if result is RESTORE_DEFAULT_TEXT:
            changed = self.state.reset_text(context["key"], axis)
        else:
            changed = self.state.set_text(context["key"], axis, result)
        self._after_change(changed)
        return changed

    def reset_text(self, axis) -> bool:
        context = self.context()
        if context is None:
            return False
        self._after_change(self.state.reset_text(context["key"], axis))
        return True

    def reset_texts(self) -> bool:
        context = self.context()
        if context is None:
            return False
        self._after_change(self.state.reset_texts(context["key"]))
        return True

    def reset_position(self, axis) -> bool:
        context = self.context()
        if context is None:
            return False
        self._after_change(self.state.reset_position(context["key"], axis))
        return True

    def reset_positions(self) -> bool:
        context = self.context()
        if context is None:
            return False
        self._after_change(self.state.reset_positions(context["key"]))
        return True

    def cancel_drag(self):
        """切页或失焦时结束拖动，保留上次落定的位置。"""
        if self._drag is None:
            return
        self._finish_drag(commit=False)

    def reset_interaction(self):
        """离开 2D 视图时清掉悬停/拖动状态与画布锁。"""
        self.cancel_drag()
        self._hover_axis = None
        self._release_lock()
        self._set_cursor(None)

    # ------------------------------------------------------------ 事件
    def _on_press(self, event):
        context = self.context()
        if context is None or event.button != 1:
            return
        axis = self._hit_test(event)
        if axis is None:
            return
        # 命中标题就先把画布锁拿在手里：RectangleSelector 的 ignore() 会因为
        # 锁不可用而让路，标题拖动因此优先于裁剪选区。
        self._acquire_lock()
        if getattr(event, "dblclick", False):
            self._finish_drag(commit=False)
            self.edit(axis)
            return
        self._start_drag(axis, event, context)

    def _on_motion(self, event):
        if self._drag is not None:
            self._drag_to(event)
            return
        context = self.context()
        axis = self._hit_test(event) if context is not None else None
        if axis != self._hover_axis:
            self._hover_axis = axis
            self._set_cursor(axis)

    def _on_release(self, event):
        if self._drag is None or event.button != 1:
            return
        self._finish_drag(commit=True)

    def _on_leave(self, _event):
        if self._drag is not None:
            self._finish_drag(commit=True)
            return
        if self._hover_axis is not None:
            self._hover_axis = None
            self._set_cursor(None)

    def _on_resize(self, _event):
        # 拖动中窗口尺寸变化会让缓存的背景失效，直接结束本次拖动。
        if self._drag is not None:
            self._finish_drag(commit=True)

    # ------------------------------------------------------------ 拖动
    def _start_drag(self, axis, event, context):
        label = self._label(axis)
        if label is None:
            return
        extent = self._label_extent(axis)
        if extent is None:
            return
        self._drag = _DragState(
            axis=axis,
            key=context["key"],
            press=(float(event.x), float(event.y)),
            anchor=self._label_anchor(axis),
            extent=extent,
            label_state=self._label_state(self._axis(axis)),
        )
        self._prepare_drag_render()

    def _drag_to(self, event):
        drag = self._drag
        if drag is None:
            return
        dx = float(event.x) - drag.press[0]
        dy = float(event.y) - drag.press[1]
        target = (drag.anchor[0] + dx, drag.anchor[1] + dy)
        target = self._clamp_to_canvas(drag.extent, drag.anchor, target)
        try:
            axes_xy = self.axes.transAxes.inverted().transform(target)
        except Exception:
            return
        self._axis(drag.axis).set_label_coords(float(axes_xy[0]), float(axes_xy[1]))
        if abs(dx) >= self.DRAG_THRESHOLD or abs(dy) >= self.DRAG_THRESHOLD:
            drag.moved = True
        self._drag_redraw()

    def _finish_drag(self, *, commit):
        drag = self._drag
        self._drag = None
        self._end_drag_render()
        self._release_lock()
        if drag is not None and commit and drag.moved:
            label = self._label(drag.axis)
            if label is not None:
                position = tuple(float(value) for value in label.get_position())
                self._after_change(
                    self.state.set_position(drag.key, drag.axis, position)
                )
                return
        if drag is not None and not commit:
            # 取消（失焦、切页）要连画面一起回滚：只丢 _drag 的话，标签会停在
            # 拖动落点，而状态里没有位置，下一次整帧重绘又会无声地跳回去。
            self._restore_label_state(drag)
        self._request_draw()

    # ------------------------------------------------------------ 命中
    def _hit_test(self, event):
        if event.x is None or event.y is None:
            return None
        for axis in AXIS_KEYS:
            extent = self._label_extent(axis)
            if extent is None:
                continue
            if extent.padded(self.HIT_PADDING).contains(event.x, event.y):
                return axis
        return None

    def _label_extent(self, axis):
        label = self._label(axis)
        if label is None or not str(label.get_text() or "").strip():
            return None
        renderer = self._renderer()
        if renderer is None:
            return None
        try:
            return label.get_window_extent(renderer)
        except Exception:
            return None

    @staticmethod
    def _label_state(axis_object):
        """标签的几何状态：变换 / 位置 / mpl 是否自动排布（``XAxis._init``）。"""
        label = axis_object.label
        return (
            label.get_transform(),
            tuple(float(value) for value in label.get_position()),
            bool(getattr(axis_object, "_autolabelpos", True)),
        )

    def _restore_label_state(self, drag):
        axis_object = self._axis(drag.axis)
        transform, position, autolabelpos = drag.label_state
        axis_object.label.set_transform(transform)
        axis_object.label.set_position(position)
        axis_object._autolabelpos = autolabelpos
        axis_object.stale = True

    def _label_anchor(self, axis) -> Tuple[float, float]:
        label = self._label(axis)
        position = label.get_position()
        return tuple(float(value) for value in label.get_transform().transform(position))

    def _clamp_to_canvas(self, extent, anchor, target):
        """把标题留在可见画布内（画布按像素给出边界）。

        ``anchor`` 是按下时标签锚点的显示坐标，``extent`` 是同一时刻的外框；
        两者之差就是外框相对锚点的偏移，拖动过程中文字尺寸不变，偏移也就固定。
        """
        figure_bbox = self.canvas.figure.bbox
        offset_x = anchor[0] - extent.x0
        offset_y = anchor[1] - extent.y0
        low_x = figure_bbox.x0 + offset_x
        high_x = figure_bbox.x1 - (extent.width - offset_x)
        low_y = figure_bbox.y0 + offset_y
        high_y = figure_bbox.y1 - (extent.height - offset_y)
        x = (low_x + high_x) / 2.0 if low_x > high_x else min(max(target[0], low_x), high_x)
        y = (low_y + high_y) / 2.0 if low_y > high_y else min(max(target[1], low_y), high_y)
        return (x, y)

    # ------------------------------------------------------------ 拖动期间的绘制
    def _prepare_drag_render(self):
        """标签暂时不参与整帧绘制，改由拖动时单独 blit，避免每次重采样图像。"""
        self._blit_background = None
        if not self._supports_blit():
            self._request_draw()
            return
        for axis in AXIS_KEYS:
            label = self._label(axis)
            if label is not None and label.get_visible() and str(label.get_text() or "").strip():
                label.set_visible(False)
        try:
            self.canvas.draw()
        except Exception:
            self._restore_labels()
            self._request_draw()
            return
        try:
            self._blit_background = self.canvas.copy_from_bbox(self.canvas.figure.bbox)
        except Exception:
            self._blit_background = None
        self._restore_labels()

    def _drag_redraw(self):
        if self._blit_background is None:
            self._request_draw()
            return
        renderer = self._renderer()
        if renderer is None:
            self._request_draw()
            return
        try:
            self.canvas.restore_region(self._blit_background)
            for axis in AXIS_KEYS:
                label = self._label(axis)
                if label is not None and label.get_visible():
                    label.draw(renderer)
            self.canvas.blit(self.canvas.figure.bbox)
        except Exception:
            self._blit_background = None
            self._request_draw()

    def _end_drag_render(self):
        self._blit_background = None
        self._restore_labels()

    def _restore_labels(self):
        for axis in AXIS_KEYS:
            label = self._label(axis)
            if label is not None and not label.get_visible():
                label.set_visible(True)

    # ------------------------------------------------------------ 杂项
    def _resolve(self, axis, context) -> str:
        return self.state.resolve(context["key"], context["defaults"]).get(axis, "")

    def _axis(self, axis):
        return self.axes.xaxis if axis == "x" else self.axes.yaxis

    def _label(self, axis):
        axis_object = self._axis(axis)
        return getattr(axis_object, "label", None)

    def _renderer(self):
        getter = getattr(self.canvas, "get_renderer", None)
        if getter is None:
            return None
        try:
            return getter()
        except Exception:
            return None

    def _supports_blit(self):
        return bool(
            getattr(self.canvas, "supports_blit", False)
            and hasattr(self.canvas, "copy_from_bbox")
            and hasattr(self.canvas, "restore_region")
            and hasattr(self.canvas, "blit")
        )

    def _is_active(self):
        if self.active_provider is None:
            return True
        try:
            return bool(self.active_provider())
        except (AttributeError, RuntimeError):
            return False

    def _acquire_lock(self):
        if self._locked or self.canvas is None:
            return
        lock = getattr(self.canvas, "widgetlock", None)
        if lock is None:
            return
        try:
            lock(self)
            self._locked = True
        except ValueError:
            self._locked = False

    def _release_lock(self):
        if not self._locked:
            return
        self._locked = False
        lock = getattr(self.canvas, "widgetlock", None)
        if lock is None:
            return
        try:
            lock.release(self)
        except ValueError:
            pass

    def _set_cursor(self, axis):
        if self.cursor_sink is None:
            return
        self.cursor_sink(None if axis is None else Qt.SizeAllCursor)

    def _request_draw(self):
        if self.draw is not None:
            try:
                self.draw()
                return
            except Exception:
                pass
        try:
            self.canvas.draw_idle()
        except Exception:
            pass

    def _after_change(self, changed):
        if not changed:
            self._request_draw()
            return
        if self.on_change is not None:
            try:
                self.on_change()
                return
            except Exception:
                pass
        self._request_draw()
