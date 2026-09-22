from PyQt5.QtCore import QEasingCurve, QParallelAnimationGroup, QPropertyAnimation
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QWIDGETSIZE_MAX


def bounded_width(width, minimum, maximum):
    return max(int(minimum), min(int(maximum), int(width)))


def scroll_content_width(scroll, minimum, maximum, *, gutter=12):
    available = int(scroll.width()) - int(gutter)
    return bounded_width(available, minimum, maximum)


def align_scroll_content(scroll, container, *, center_y_when_short=False):
    """Center control pages horizontally and keep their first section at the top."""
    x = max(0, (int(scroll.width()) - int(container.width())) // 2)

    if center_y_when_short and int(container.height()) <= int(scroll.height()):
        y = max(0, (int(scroll.height()) - int(container.height())) // 2)
    else:
        min_y = min(0, int(scroll.height()) - int(container.height()))
        y = min(0, max(int(container.y()), min_y))

    container.move(x, y)

    animation = getattr(scroll, "widget_scroll_animation", None)
    if animation is not None:
        animation.setCurrent([x, y])
        animation.setTarget([x, y])

    scroll.update()


def centered_widget_row(widget, max_width=None):
    if max_width is not None:
        widget.setMaximumWidth(max_width)
    row = QHBoxLayout()
    row.addStretch()
    row.addWidget(widget)
    row.addStretch()
    return row


def _stop_visibility_animation(widget):
    """停掉控件上进行中的显隐动画（若有），避免新旧动画互相打架。"""
    previous = getattr(widget, "_visibility_animation", None)
    if previous is not None:
        try:
            previous.stop()
        except RuntimeError:
            pass
        widget._visibility_animation = None


def set_widget_visibility_instant(widget, visible, *, settle_show=None):
    """无动画地落定控件显隐状态（用于启动初始化等不需要过渡的场合）。"""
    _stop_visibility_animation(widget)
    widget.setMinimumHeight(0)
    widget.setMaximumHeight(QWIDGETSIZE_MAX)
    if isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect):
        widget.setGraphicsEffect(None)
    widget.setVisible(bool(visible))
    if visible and settle_show is not None:
        settle_show()


def animate_widget_visibility(widget, visible, *, duration=220, target_height=None, settle_show=None,
                              on_update=None, on_settled=None):
    """平滑展开/收起一个布局内控件：高度收放与透明度淡入淡出联动。

    - ``visible=True``：先 ``setVisible(True)``，从 0 高度淡入展开到
      ``target_height``（缺省取 ``sizeHint``），结束后回调 ``settle_show``
      （没有回调则清除高度约束，恢复自然布局）；
    - ``visible=False``：从当前高度淡出收起到 0，结束后 ``setVisible(False)``
      并还原高度约束；
    - ``on_update``：动画每帧触发（用于同步父容器高度，避免挤压相邻控件）；
    - ``on_settled``：动画落定后触发（两个方向都会调）；
    - 重复调用会从当前视觉状态继续，不打断出硬跳变。
    """
    _stop_visibility_animation(widget)
    visible = bool(visible)

    # 注意用 isHidden()（自身显隐标志）而非 isVisible()：控件可能位于
    # 未激活的 QStackedWidget 标签页里，祖先隐藏时 isVisible() 也是 False，
    # 但显隐状态仍必须落定，否则切回该页时控件会错误地保持可见。
    if not visible and widget.isHidden():
        return

    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(1.0)
        widget.setGraphicsEffect(effect)

    start_height = int(widget.height()) if not widget.isHidden() else 0
    if visible:
        end_height = int(target_height or widget.sizeHint().height() or start_height or 1)
        start_opacity = float(effect.opacity()) if not widget.isHidden() else 0.0
        widget.setVisible(True)
    else:
        end_height = 0
        start_opacity = float(effect.opacity())

    widget.setMinimumHeight(start_height)
    widget.setMaximumHeight(start_height)

    group = QParallelAnimationGroup(widget)
    height_anims = []
    for prop in (b"minimumHeight", b"maximumHeight"):
        anim = QPropertyAnimation(widget, prop, group)
        anim.setDuration(duration)
        anim.setStartValue(start_height)
        anim.setEndValue(end_height)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        group.addAnimation(anim)
        height_anims.append(anim)

    if on_update is not None:
        height_anims[0].valueChanged.connect(lambda _value: on_update())

    fade = QPropertyAnimation(effect, b"opacity", group)
    fade.setDuration(duration)
    fade.setStartValue(start_opacity)
    fade.setEndValue(1.0 if visible else 0.0)
    fade.setEasingCurve(QEasingCurve.InOutCubic)
    group.addAnimation(fade)

    def _finish():
        widget._visibility_animation = None
        widget.setMinimumHeight(0)
        widget.setMaximumHeight(QWIDGETSIZE_MAX)
        if visible:
            if settle_show is not None:
                settle_show()
        else:
            widget.setVisible(False)
        # 动画只借用透明度效果做过渡，结束后撤掉，恢复正常绘制管线。
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)
        if on_settled is not None:
            on_settled()

    group.finished.connect(_finish)
    widget._visibility_animation = group
    group.start()


def combo_index_for_text(combo_box, text, *, aliases=None):
    target = (aliases or {}).get(text, text)
    for index in range(combo_box.count()):
        if combo_box.itemText(index) == target:
            return index
    return -1


def apply_label_color(group, color, *, suppress_errors=True):
    from siui.components.widgets import SiLabel
    from siui.core import SiColor

    for child in group.findChildren(SiLabel):
        try:
            child.colorGroup().assign(SiColor.TEXT_A, color)
            child.reloadStyleSheet()
        except Exception:
            if not suppress_errors:
                raise


def sync_slider_visual(slider):
    minimum = int(slider.minimum())
    maximum = int(slider.maximum())
    value = int(slider.value())
    progress = 0.0 if maximum == minimum else (value - minimum) / (maximum - minimum)

    try:
        slider.setProperty(slider.Property.TrackProgress, progress)
    except Exception:
        pass

    progress_ani = getattr(slider, "progress_ani", None)
    if progress_ani is not None:
        try:
            progress_ani.fromProperty()
            progress_ani.setCurrentValue(progress)
            progress_ani.setEndValue(progress)
        except Exception:
            pass

    update_tooltip = getattr(slider, "_updateToolTip", None)
    if callable(update_tooltip):
        try:
            update_tooltip(flash=False)
        except Exception:
            pass

    slider.update()


def sync_switch_visual(switch):
    """Sync a SiSwitchRefactor's painted thumb to its checked state.

    SiSwitchRefactor drives its visual on/off position from the ``progress``
    property (0.0 = off, 1.0 = on) which is only animated when the user clicks
    the widget.  Programmatic ``setChecked()`` calls change the logical state
    but leave the painted thumb untouched, so restoring a page would show a
    stale switch.  This helper mirrors ``sync_slider_visual`` and snaps the
    thumb (and its animation) to the current checked state without animating.
    """
    progress = 1.0 if bool(switch.isChecked()) else 0.0

    try:
        switch.setProperty(switch.Property.Progress, progress)
    except Exception:
        pass

    progress_ani = getattr(switch, "progress_ani", None)
    if progress_ani is not None:
        try:
            progress_ani.fromProperty()
            progress_ani.setCurrentValue(progress)
            progress_ani.setEndValue(progress)
        except Exception:
            pass

    switch.update()
