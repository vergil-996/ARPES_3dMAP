from PyQt5.QtWidgets import QHBoxLayout


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
