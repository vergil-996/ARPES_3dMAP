def bounded_width(width, minimum, maximum):
    return max(int(minimum), min(int(maximum), int(width)))


def scroll_content_width(scroll, minimum, maximum, *, gutter=12):
    available = int(scroll.width()) - int(gutter)
    return bounded_width(available, minimum, maximum)


def align_scroll_content(scroll, container, *, center_y_when_short=True):
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
