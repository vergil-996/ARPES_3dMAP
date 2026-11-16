"""Application-wide wheel stepping at the numeric edit caret."""

import math
from dataclasses import dataclass

from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtGui import QDoubleValidator, QIntValidator
from PyQt5.QtWidgets import QAbstractSpinBox, QDoubleSpinBox, QLineEdit, QSpinBox


_NUMERIC_WHEEL_PROPERTY = "bandscopeNumericWheel"
_FILTER_ATTRIBUTE = "_bandscope_numeric_wheel_filter"


@dataclass(frozen=True)
class _DigitStep:
    value: float
    exponent: int


def enable_numeric_wheel(editor, after_edit=None):
    editor.setProperty(_NUMERIC_WHEEL_PROPERTY, True)
    if after_edit is not None:
        editor._numeric_wheel_after_edit = after_edit


def _number_body(text, prefix, suffix):
    start = len(prefix) if prefix and text.startswith(prefix) else 0
    end = len(text) - len(suffix) if suffix and text.endswith(suffix) else len(text)
    return text[start:max(start, end)], start


def _scientific_exponent(body):
    positions = [position for position in (body.find("e"), body.find("E")) if position >= 0]
    if not positions:
        return len(body), 0
    position = min(positions)
    try:
        return position, int(body[position + 1:])
    except ValueError:
        return position, 0


def _digit_step(text, cursor_position, prefix="", suffix="", decimal_point="."):
    body, body_start = _number_body(text, prefix, suffix)
    mantissa_end, scientific_exponent = _scientific_exponent(body)
    mantissa = body[:mantissa_end]
    cursor_position = max(0, min(cursor_position - body_start, len(mantissa)))

    digit_index = next(
        (index for index in range(cursor_position - 1, -1, -1) if mantissa[index].isdigit()),
        None,
    )
    if digit_index is None:
        digit_index = next(
            (index for index in range(cursor_position, len(mantissa)) if mantissa[index].isdigit()),
            None,
        )
    if digit_index is None:
        return None

    decimal_index = mantissa.find(decimal_point)
    integer_end = decimal_index if decimal_index >= 0 else len(mantissa)
    if digit_index < integer_end:
        exponent = sum(
            character.isdigit() for character in mantissa[digit_index + 1:integer_end]
        )
    elif decimal_index >= 0 and digit_index > decimal_index:
        exponent = -sum(
            character.isdigit() for character in mantissa[decimal_index + len(decimal_point):digit_index + 1]
        )
    else:
        return None

    exponent += scientific_exponent
    try:
        value = 10.0 ** exponent
    except OverflowError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return _DigitStep(value, exponent)


def _cursor_for_exponent(text, exponent, prefix="", suffix="", decimal_point="."):
    body, body_start = _number_body(text, prefix, suffix)
    mantissa_end, scientific_exponent = _scientific_exponent(body)
    mantissa = body[:mantissa_end]
    exponent -= scientific_exponent
    decimal_index = mantissa.find(decimal_point)
    integer_end = decimal_index if decimal_index >= 0 else len(mantissa)
    integer_digits = [index for index, character in enumerate(mantissa[:integer_end]) if character.isdigit()]
    fraction_digits = []
    if decimal_index >= 0:
        fraction_digits = [
            index
            for index, character in enumerate(mantissa[decimal_index + len(decimal_point):], decimal_index + len(decimal_point))
            if character.isdigit()
        ]

    if exponent >= 0 and integer_digits:
        index = max(0, min(len(integer_digits) - 1, len(integer_digits) - 1 - exponent))
        digit_index = integer_digits[index]
    elif exponent < 0 and fraction_digits:
        index = max(0, min(len(fraction_digits) - 1, -exponent - 1))
        digit_index = fraction_digits[index]
    elif integer_digits:
        digit_index = integer_digits[-1]
    elif fraction_digits:
        digit_index = fraction_digits[0]
    else:
        return body_start
    return body_start + digit_index + 1


def _decimal_point(widget):
    return str(widget.locale().decimalPoint())


def _spin_context(spin_box):
    if isinstance(spin_box, QAbstractSpinBox):
        editor = spin_box.lineEdit()
        prefix = spin_box.prefix() if hasattr(spin_box, "prefix") else ""
        suffix = spin_box.suffix() if hasattr(spin_box, "suffix") else ""
    else:
        editor = spin_box
        prefix = suffix = ""
    return editor, prefix, suffix


def _step_spin_box(spin_box, event):
    if not spin_box.isEnabled() or event.angleDelta().y() == 0:
        return False
    editor, prefix, suffix = _spin_context(spin_box)
    if editor.isReadOnly():
        return False
    target = _digit_step(
        editor.text(), editor.cursorPosition(), prefix, suffix, _decimal_point(spin_box)
    )
    if target is None:
        return False

    original_step = spin_box.singleStep()
    validator = editor.validator()
    integer_step = isinstance(spin_box, QSpinBox) or isinstance(validator, QIntValidator)
    step = int(target.value) if integer_step else float(target.value)
    try:
        spin_box.setSingleStep(step)
        spin_box.wheelEvent(event)
    finally:
        spin_box.setSingleStep(original_step)
    if event.isAccepted():
        editor.setCursorPosition(
            _cursor_for_exponent(
                editor.text(), target.exponent, prefix, suffix, _decimal_point(spin_box)
            )
        )
    return event.isAccepted()


def _is_numeric_line_edit(editor):
    return bool(editor.property(_NUMERIC_WHEEL_PROPERTY)) or isinstance(
        editor.validator(), (QIntValidator, QDoubleValidator)
    )


def _line_edit_value(editor):
    value_getter = getattr(editor, "value", None)
    if callable(value_getter):
        try:
            value = value_getter()
        except (TypeError, ValueError):
            return None
    else:
        text = editor.text().strip()
        validator = editor.validator()
        locale = validator.locale() if validator is not None else editor.locale()
        value, ok = locale.toDouble(text)
        if not ok:
            return None
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _set_line_edit_value(editor, value):
    validator = editor.validator()
    if isinstance(validator, QIntValidator):
        value = max(validator.bottom(), min(validator.top(), int(round(value))))
    elif isinstance(validator, QDoubleValidator):
        value = max(validator.bottom(), min(validator.top(), value))

    value_setter = getattr(editor, "setValue", None)
    if callable(value_setter):
        value_setter(value)
    elif isinstance(validator, QIntValidator):
        editor.setText(str(int(value)))
    else:
        editor.setText(format(value, ".12g"))


def _step_line_edit(editor, event):
    delta = event.angleDelta().y()
    if (
        not delta
        or not editor.isEnabled()
        or editor.isReadOnly()
        or not editor.hasFocus()
        or not _is_numeric_line_edit(editor)
    ):
        return False
    value = _line_edit_value(editor)
    target = _digit_step(editor.text(), editor.cursorPosition(), decimal_point=_decimal_point(editor))
    if value is None or target is None:
        return False

    _set_line_edit_value(editor, value + (1 if delta > 0 else -1) * target.value)
    after_edit = getattr(editor, "_numeric_wheel_after_edit", None)
    if callable(after_edit):
        after_edit()
    else:
        editor.textEdited.emit(editor.text())
    editor.setCursorPosition(
        _cursor_for_exponent(editor.text(), target.exponent, decimal_point=_decimal_point(editor))
    )
    event.accept()
    return True


class NumericWheelEventFilter(QObject):
    def eventFilter(self, watched, event):
        if event.type() != QEvent.Wheel:
            return False
        if isinstance(watched, QLineEdit):
            parent = watched.parentWidget()
            if isinstance(parent, (QSpinBox, QDoubleSpinBox)) and parent.lineEdit() is watched:
                watched = parent
        if isinstance(watched, (QSpinBox, QDoubleSpinBox)) or (
            isinstance(watched, QLineEdit)
            and callable(getattr(watched, "singleStep", None))
            and callable(getattr(watched, "setSingleStep", None))
        ):
            return _step_spin_box(watched, event)
        if isinstance(watched, QLineEdit):
            return _step_line_edit(watched, event)
        return False


def install_numeric_wheel_filter(app):
    if app is None:
        return None
    existing = getattr(app, _FILTER_ATTRIBUTE, None)
    if existing is not None:
        return existing
    event_filter = NumericWheelEventFilter(app)
    app.installEventFilter(event_filter)
    setattr(app, _FILTER_ATTRIBUTE, event_filter)
    return event_filter
