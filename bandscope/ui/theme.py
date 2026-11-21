# -*- coding: utf-8 -*-
"""BandScope 的深色 / 浅色界面令牌与 Qt 控件样式。

界面只使用中性黑白灰；数据色带和出版输出不属于界面主题。已有模块继续
通过 BG_*、TEXT_*、ACCENT* 读取颜色，切换时由这里同步更新 QSS、SiUI
色板以及已创建的控件。
"""
import re
import sys
import weakref
from pathlib import Path

from PyQt5.QtCore import QMargins, QObject
from PyQt5.QtGui import QColor, QFont, QPalette


PALETTES = {
    "dark": {
        "BG_0": "#101114",
        "BG_1": "#121417",
        "BG_2": "#16181B",
        "BG_3": "#1B1D21",
        "BG_4": "#292C31",
        "CANVAS_BG": "#121417",
        "BORDER": "rgba(255, 255, 255, 0.10)",
        "BORDER_STRONG": "rgba(255, 255, 255, 0.18)",
        "BORDER_HEX": "#2C2F35",
        "TEXT_1": "#ECEEF1",
        "TEXT_2": "#A1A5AD",
        "TEXT_3": "#737881",
        "ACCENT": "#E7E9EC",
        "ACCENT_HOVER": "#FFFFFF",
        "ACCENT_SOFT": "rgba(255, 255, 255, 0.075)",
        "ACCENT_DIM": "rgba(255, 255, 255, 0.38)",
        "ACCENT_ON": "#17191C",
        "ACCENT_TRANSITION": "#E7E9EC",
        "ACCENT_DEEP": "#8B9098",
        "TITLE_HIGHLIGHT": "#14FFFFFF",
        "INFO": "#A1A5AD",
        "INFO_SOFT": "rgba(255, 255, 255, 0.08)",
        "SUCCESS": "#A1A5AD",
        "WARNING": "#D1B779",
        "DANGER": "#E8A1A7",
        "DANGER_HOVER": "#F0B7BC",
        "DANGER_SOFT": "rgba(232, 161, 167, 0.10)",
        "DANGER_BORDER": "rgba(232, 161, 167, 0.38)",
        "SCROLLBAR": "#3E424A",
    },
    "light": {
        "BG_0": "#EDEEF0",
        "BG_1": "#F5F6F7",
        "BG_2": "#F8F9FA",
        "BG_3": "#FFFFFF",
        "BG_4": "#E7E9EC",
        "CANVAS_BG": "#FAFBFB",
        "BORDER": "rgba(27, 31, 37, 0.10)",
        "BORDER_STRONG": "rgba(27, 31, 37, 0.20)",
        "BORDER_HEX": "#D9DCE0",
        "TEXT_1": "#282B30",
        "TEXT_2": "#656B74",
        "TEXT_3": "#8C929B",
        "ACCENT": "#292C31",
        "ACCENT_HOVER": "#08090B",
        "ACCENT_SOFT": "rgba(27, 31, 37, 0.055)",
        "ACCENT_DIM": "rgba(27, 31, 37, 0.35)",
        "ACCENT_ON": "#FFFFFF",
        "ACCENT_TRANSITION": "#292C31",
        "ACCENT_DEEP": "#15181C",
        "TITLE_HIGHLIGHT": "#14292C31",
        "INFO": "#656B74",
        "INFO_SOFT": "rgba(27, 31, 37, 0.06)",
        "SUCCESS": "#656B74",
        "WARNING": "#9C702E",
        "DANGER": "#B7555E",
        "DANGER_HOVER": "#933E48",
        "DANGER_SOFT": "rgba(183, 85, 94, 0.08)",
        "DANGER_BORDER": "rgba(183, 85, 94, 0.35)",
        "SCROLLBAR": "#C2C6CC",
    },
}

_QSS_PALETTE_ROLES = {
    "BG_0": "window",
    "BG_1": "base",
    "BG_2": "alternate-base",
    "BG_3": "button",
    "BG_4": "light",
    "BORDER_HEX": "mid",
    "TEXT_1": "window-text",
    "TEXT_2": "link",
    "TEXT_3": "placeholder-text",
    "ACCENT": "highlight",
    "ACCENT_HOVER": "bright-text",
    "ACCENT_ON": "highlighted-text",
    "ACCENT_SOFT": "link-visited",
    "SCROLLBAR": "dark",
}


def _qss_color(key):
    role = _QSS_PALETTE_ROLES.get(key)
    return f"palette({role})" if role else PALETTES[_CURRENT_THEME][key]


def _palette_qcolor(value):
    if not value.startswith("rgba("):
        return QColor(value)
    red, green, blue, alpha = (
        component.strip() for component in value[5:-1].split(",")
    )
    color = QColor(int(red), int(green), int(blue))
    color.setAlphaF(float(alpha))
    return color


_QPALETTE_ROLE_TOKENS = (
    (QPalette.Window, "BG_0"),
    (QPalette.Base, "BG_1"),
    (QPalette.AlternateBase, "BG_2"),
    (QPalette.WindowText, "TEXT_1"),
    (QPalette.Text, "TEXT_1"),
    (QPalette.Button, "BG_3"),
    (QPalette.ButtonText, "TEXT_1"),
    (QPalette.BrightText, "ACCENT_HOVER"),
    (QPalette.Light, "BG_4"),
    (QPalette.Midlight, "BG_3"),
    (QPalette.Mid, "BORDER_HEX"),
    (QPalette.Dark, "SCROLLBAR"),
    (QPalette.Shadow, "BG_0"),
    (QPalette.Highlight, "ACCENT"),
    (QPalette.HighlightedText, "ACCENT_ON"),
    (QPalette.Link, "TEXT_2"),
    (QPalette.LinkVisited, "ACCENT_SOFT"),
    (QPalette.PlaceholderText, "TEXT_3"),
    (QPalette.ToolTipBase, "BG_3"),
    (QPalette.ToolTipText, "TEXT_1"),
)
_QPALETTE_DISABLED_ROLES = (
    QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.HighlightedText,
)


def _apply_palette_colors(palette):
    for role, key in _QPALETTE_ROLE_TOKENS:
        palette.setColor(role, _palette_qcolor(PALETTES[_CURRENT_THEME][key]))
    combo_text = "#000000" if _CURRENT_THEME == "light" else PALETTES[_CURRENT_THEME]["TEXT_1"]
    palette.setColor(QPalette.Text, QColor(combo_text))
    for role in _QPALETTE_DISABLED_ROLES:
        palette.setColor(QPalette.Disabled, role, QColor(TEXT_3))


SP_1 = 4
SP_2 = 8
SP_3 = 12
SP_4 = 16
SP_5 = 24
SP_6 = 32
R_S = 6
R_M = 8
R_L = 12
FONT_FAMILY = '"Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif'
FONT_MONO = '"Cascadia Mono", "Consolas", "Courier New", monospace'
MPL_FONT_FAMILIES = ["DejaVu Sans", "Microsoft YaHei", "SimHei", "sans-serif"]
TINT_CLEAR = "#00FFFFFF"
TINT_HOVER = "#12FFFFFF"
# 时间轴区间滑块与色阶滑条共用的手柄色；固定中性色，不随深浅主题切换。
RANGE_HANDLE_DARK_FILL = "#17191C"
RANGE_HANDLE_DARK_BORDER = "#555B64"
RANGE_HANDLE_LIGHT_FILL = "#FFFFFF"
RANGE_HANDLE_LIGHT_BORDER = "#9CA1A8"
RANGE_HANDLE_MID_FILL = "#858585"
CURVE_PALETTE = []
QSS_TOKENS = {}
_CURRENT_THEME = "light"


class _ThemeChange:
    def __init__(self):
        self._listeners = {}

    @staticmethod
    def _key(listener):
        owner = getattr(listener, "__self__", None)
        return id(owner if owner is not None else listener), getattr(listener, "__name__", "")

    def connect(self, listener):
        key = self._key(listener)
        if getattr(listener, "__self__", None) is not None:
            listener = weakref.WeakMethod(listener)
        self._listeners[key] = listener

    def disconnect(self, listener):
        self._listeners.pop(self._key(listener), None)

    def emit(self, name):
        for listener in tuple(self._listeners.values()):
            if isinstance(listener, weakref.WeakMethod):
                listener = listener()
            if listener is not None:
                listener(name)


changed = _ThemeChange()


def current_theme():
    return _CURRENT_THEME


def asset_path(relative_path):
    base = getattr(sys, "_MEIPASS", str(Path(__file__).resolve().parents[2]))
    return (Path(base) / relative_path).as_posix()


def _install_palette(name):
    global _CURRENT_THEME, ACCENT, ACCENT_HOVER, ACCENT_SOFT, ACCENT_DIM
    global ACCENT_ON, ACCENT_TRANSITION, ACCENT_DEEP, INFO, INFO_SOFT
    global SUCCESS, WARNING, DANGER, DANGER_HOVER, DANGER_SOFT, DANGER_BORDER
    global BG_0, BG_1, BG_2, BG_3, BG_4, CANVAS_BG
    global BORDER, BORDER_STRONG, BORDER_HEX, TEXT_1, TEXT_2, TEXT_3
    global TITLE_HIGHLIGHT, SCROLLBAR
    if name not in PALETTES:
        raise ValueError(f"unknown theme: {name!r}")
    _CURRENT_THEME = name
    palette = PALETTES[name]
    globals().update(palette)
    QSS_TOKENS.update({
        "BG0": BG_0,
        "BG1": BG_1,
        "BG2": BG_2,
        "BG3": BG_3,
        "BG4": BG_4,
        "CANVAS": CANVAS_BG,
        "BORD": BORDER_HEX,
        "BORD_S": BORDER_STRONG,
        "BORD_RGBA": BORDER,
        "T1": TEXT_1,
        "T2": TEXT_2,
        "T3": TEXT_3,
        "ACCENT": ACCENT,
        "ACCENT_H": ACCENT_HOVER,
        "ACC_SOFT": ACCENT_SOFT,
        "ACC_DIM": ACCENT_DIM,
        "ACC_ON": ACCENT_ON,
        "ACC_ARGB": TITLE_HIGHLIGHT,
        "INFO": INFO,
        "INFO_SOFT": INFO_SOFT,
        "SUCCESS": SUCCESS,
        "WARNING": WARNING,
        "DANGER": DANGER,
        "DANGER_H": DANGER_HOVER,
        "DANGER_SOFT": DANGER_SOFT,
        "DANGER_BORD": DANGER_BORDER,
        "SCROLLBAR": SCROLLBAR,
        "RS": R_S,
        "RM": R_M,
        "RL": R_L,
        "FONT": FONT_FAMILY,
        "MONO": FONT_MONO,
    })
    CURVE_PALETTE[:] = [
        TEXT_1, "#7B818B", "#4CC9F0", "#F9C74F", "#F3722C", "#B388FF", "#43AA8B",
    ]


_install_palette(_CURRENT_THEME)


def token_color(name):
    """把主题令牌解析为 QColor；支持 #RRGGBB 与 rgba(...) 令牌。"""
    return _palette_qcolor(PALETTES[_CURRENT_THEME][name])


def accent_qcolor():
    return token_color("ACCENT")


def fluent_icon(name, size=16, color=None):
    from PyQt5.QtCore import QSize
    from PyQt5.QtGui import QIcon
    from siui.core import SiGlobal

    try:
        return SiGlobal.siui.iconpack.toIcon(name, QSize(size, size), color or TEXT_2)
    except Exception:
        return QIcon()


def set_button_icon(button, name, size=16, *, on_accent=False):
    button.setProperty("fluentIconName", name)
    button.setProperty("fluentIconSize", int(size))
    button.setProperty("fluentIconOnAccent", bool(on_accent))
    button.setIcon(fluent_icon(name, size, ACCENT_ON if on_accent else TEXT_2))


def apply_app_font():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "sans-serif"])
        font.setPixelSize(13)
        app.setFont(font)


def action_button_qss(kind="primary"):
    palette = {
        "primary": ("ACCENT", "ACCENT_ON", "ACCENT_HOVER", "ACCENT"),
        "secondary": ("BG_3", "TEXT_1", "BG_4", "BORDER_HEX"),
        "quiet": ("transparent", "TEXT_2", "BG_4", "transparent"),
        "danger": ("DANGER_SOFT", "DANGER", "BG_4", "DANGER_BORDER"),
    }
    bg, fg, hover, border = (
        value if value == "transparent" else _qss_color(value)
        for value in palette[kind]
    )
    hover_text = _qss_color("TEXT_1" if kind != "primary" else "ACCENT_ON")
    hover_border = _qss_color("BORDER_HEX" if kind != "primary" else "ACCENT_HOVER")
    return (
        f"QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border};"
        f" border-radius: {R_S}px; padding: 0 11px; font-size: 12px; font-weight: 500;"
        " min-height: 26px; min-width: 28px; }"
        f"QPushButton:hover {{ background: {hover}; color: {hover_text};"
        f" border-color: {hover_border}; }}"
        f"QPushButton:pressed {{ background: {_qss_color('BG_4')}; color: {_qss_color('TEXT_1')}; }}"
        f"QPushButton:checked {{ background: {_qss_color('ACCENT')}; color: {_qss_color('ACCENT_ON')};"
        f" border-color: {_qss_color('ACCENT')}; }}"
        f"QPushButton:focus {{ border: 1px solid {_qss_color('TEXT_2')}; }}"
        f"QPushButton:disabled {{ background: {_qss_color('BG_3')}; color: {_qss_color('TEXT_3')};"
        f" border: 1px solid {BORDER}; }}"
    )


def style_push_button(btn, kind="primary"):
    from siui.core import SiColor
    from PyQt5.QtWidgets import QPushButton

    btn.setProperty("buttonKind", kind)
    if isinstance(btn, QPushButton):
        fixed_width = btn.minimumWidth() if btn.minimumWidth() == btn.maximumWidth() and btn.minimumWidth() > 0 else None
        fixed_height = btn.minimumHeight() if btn.minimumHeight() == btn.maximumHeight() and btn.minimumHeight() > 0 else None
        style = action_button_qss(kind)
        if btn.property("compactIconButton"):
            style += "QPushButton { min-height: 24px; min-width: 26px; padding: 0; }"
        if btn.styleSheet() != style:
            btn.setStyleSheet(style)
        if btn.property("compactIconButton"):
            btn.setFixedSize(28, 26)
        if fixed_width is not None:
            btn.setFixedWidth(fixed_width)
        if fixed_height is not None:
            btn.setFixedHeight(fixed_height)
        return
    palette = {
        "primary": (ACCENT, ACCENT_ON),
        "secondary": (BG_3, TEXT_1),
        "quiet": ("transparent", TEXT_2),
        "danger": (DANGER_SOFT, DANGER),
    }
    if kind not in palette:
        raise ValueError(f"unknown button kind: {kind!r}")
    panel, text = palette[kind]
    btn.colorGroup().assign(SiColor.BUTTON_PANEL, panel)
    btn.colorGroup().assign(SiColor.TEXT_B, text)
    btn.reloadStyleSheet()


def style_accent_slider(slider):
    slider.setProperty("neutralSlider", True)
    sd = slider.style_data
    sd.track_color = QColor(TEXT_2)
    sd.background_color = QColor(BORDER_HEX)
    sd.thumb_idle_color = QColor(ACCENT)
    sd.thumb_hover_color = QColor(ACCENT_HOVER)
    sd.thumb_width = 10
    sd.thumb_height = 16
    sd.track_height = 3
    try:
        slider._thumb_color = QColor(ACCENT)
        thumb_ani = getattr(slider, "thumb_color_ani", None)
        if thumb_ani is not None:
            thumb_ani.setCurrentValue(QColor(ACCENT))
            thumb_ani.setEndValue(QColor(ACCENT))
    except Exception:
        pass
    slider.update()


def card_qss():
    return (
        "QFrame#CardGroup {"
        "background: transparent;"
        f"border: none; border-bottom: 1px solid {BORDER}; border-radius: 0;"
        "}"
    )


def card_title_qss():
    return f"color: {TEXT_1}; background: transparent; font-size: 13px; font-weight: 600;"


def field_label_qss():
    return f"color: {TEXT_2}; background: transparent; font-size: 12px;"


def slider_value_qss():
    return f"color: {TEXT_1}; font-size: 11px; font-family: {FONT_MONO};"


def _combo_text_color():
    return "#000000" if _CURRENT_THEME == "light" else TEXT_1


def _style_combo_menu_text(menu):
    text_color = QColor(_combo_text_color())
    disabled_color = QColor(TEXT_3)
    for item in getattr(menu, "_widgets", {}).values():
        style_data = getattr(item, "style_data", None)
        if style_data is not None:
            if hasattr(style_data, "label_text_color_enabled"):
                style_data.label_text_color_enabled = text_color
            if hasattr(style_data, "label_text_color_disabled"):
                style_data.label_text_color_disabled = disabled_color
        label = getattr(item, "_name_label", None)
        if label is not None:
            label.setStyleSheet(
                "QLabel { margin: 0px 8px 1px 0px;"
                f"color: {text_color.name()}; }}"
                "QLabel:disabled { margin: 0px 8px 1px 0px;"
                f"color: {disabled_color.name()}; }}"
            )


def raise_well_on_card(widget, *, adjust_layout=True):
    from siui.core import SiColor

    target = getattr(widget, "_line_edit", widget)
    widget.setProperty("neutralCombo", True)
    widget.setProperty("neutralComboAdjustLayout", bool(adjust_layout))
    if adjust_layout and hasattr(target, "_title_text_margins"):
        target._title_text_margins = QMargins(10, 0, 10, 0)
        target._updateTextMargins()
        target._updateContainerGeometry()
    menu = getattr(widget, "_menu", None)
    if menu is not None:
        menu.setMaximumHeight(360)
        menu.style_data.background_color = QColor(BG_3)
        menu.style_data.border_color = QColor(BORDER_HEX)
        menu._initStyle()
        _style_combo_menu_text(menu)
        if not menu.property("neutralComboTextThemeBound"):
            menu.aboutToShow.connect(lambda: _style_combo_menu_text(menu))
            menu.setProperty("neutralComboTextThemeBound", True)
    sd = getattr(target, "style_data", None)
    if sd is not None and hasattr(sd, "text_background_color"):
        text_color = QColor(_combo_text_color())
        sd.title_background_color = QColor(BG_1)
        sd.text_background_color = QColor(BG_1)
        sd.title_color_idle = QColor(TEXT_2)
        sd.title_color_focused = QColor(TEXT_1)
        sd.text_color = text_color
        sd.text_indicator_color_editing = text_color
        sd.text_indicator_color_error = QColor(DANGER)
        target.title_color_ani.setCurrentValue(QColor(TEXT_2))
        target.title_color_ani.setEndValue(QColor(TEXT_2))
        target._initStyleSheet()
        target.update()
        return
    try:
        target.colorGroup().assign(SiColor.INTERFACE_BG_B, BG_1)
        target.colorGroup().assign(SiColor.INTERFACE_BG_D, BORDER_HEX)
        target.reloadStyleSheet()
    except Exception:
        pass


def value_input_qss():
    return (
        "QLineEdit, QSpinBox, QDoubleSpinBox {"
        f"background-color: {BG_1}; color: {TEXT_1};"
        f"border: 1px solid {BORDER_HEX}; border-radius: 5px;"
        "padding-left: 8px; padding-right: 8px;"
        f"font-family: {FONT_MONO}; font-size: 11px; }}"
        "QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {"
        f"border: 1px solid {TEXT_2}; }}"
        f"QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{"
        f"color: {TEXT_3}; background: {BG_3}; }}"
        "QSpinBox::up-button, QSpinBox::down-button,"
        "QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {"
        "width: 0px; border: none; }"
    )


def section_label_qss():
    return f"color: {TEXT_1}; font-size: 11px; font-weight: 600;"


def nav_tab_qss():
    return (
        "QPushButton { background-color: transparent;"
        f"color: {TEXT_2}; border: 1px solid transparent; border-radius: 5px;"
        "padding: 0 10px; font-size: 12px; }"
        f"QPushButton:hover {{ color: {TEXT_1}; background-color: {BG_4}; }}"
        "QPushButton:checked {"
        f"background-color: {BG_3}; color: {TEXT_1};"
        f"border: 1px solid {BORDER_HEX}; font-weight: 500; }}"
    )


def context_menu_qss():
    return (
        "QMenu {"
        f"color: {TEXT_1}; background-color: {BG_3};"
        f"border: 1px solid {BORDER_HEX}; border-radius: {R_S}px; padding: 6px; }}"
        "QMenu::item {"
        "padding: 7px 24px 7px 12px; border-radius: 4px; background: transparent; }"
        f"QMenu::item:selected {{ background-color: {BG_4}; }}"
        f"QMenu::item:disabled {{ color: {TEXT_3}; }}"
        f"QMenu::separator {{ height: 1px; background: {BORDER_HEX}; margin: 4px 8px; }}"
    )


def scrollbar_qss():
    scrollbar = _qss_color("SCROLLBAR")
    hover = _qss_color("TEXT_3")
    return (
        "QScrollBar:vertical { background: transparent; width: 6px; margin: 1px; }"
        "QScrollBar:horizontal { background: transparent; height: 6px; margin: 1px; }"
        "QScrollBar::handle {"
        f"background: {scrollbar}; border-radius: 3px; min-height: 24px; min-width: 24px; }}"
        f"QScrollBar::handle:hover {{ background: {hover}; }}"
        "QScrollBar::add-line, QScrollBar::sub-line {"
        "height: 0px; width: 0px; border: none; background: none; }"
        "QScrollBar::add-page, QScrollBar::sub-page { background: none; }"
    )


def application_qss():
    BG_0 = _qss_color("BG_0")
    BG_1 = _qss_color("BG_1")
    BG_2 = _qss_color("BG_2")
    BG_3 = _qss_color("BG_3")
    BG_4 = _qss_color("BG_4")
    BORDER_HEX = _qss_color("BORDER_HEX")
    TEXT_1 = _qss_color("TEXT_1")
    TEXT_2 = _qss_color("TEXT_2")
    TEXT_3 = _qss_color("TEXT_3")
    ACCENT = _qss_color("ACCENT")
    ACCENT_HOVER = _qss_color("ACCENT_HOVER")
    ACCENT_ON = _qss_color("ACCENT_ON")
    ACCENT_SOFT = _qss_color("ACCENT_SOFT")
    COMBO_TEXT = "palette(text)"
    return f"""
        QWidget {{ color: {TEXT_1}; font-family: {FONT_FAMILY}; font-size: 13px; }}
        QDialog, QMessageBox, QProgressDialog {{ background-color: {BG_0}; }}
        QLabel {{ background: transparent; }}
        QToolTip {{ color: {TEXT_1}; background-color: {BG_3};
            border: 1px solid {BORDER_HEX}; padding: 6px 8px; }}
        QPushButton, QToolButton {{
            color: {TEXT_1}; background-color: {BG_3};
            border: 1px solid {BORDER_HEX}; border-radius: {R_S}px;
            min-height: 30px; padding: 0 10px; font-size: 12px;
        }}
        QPushButton:hover, QToolButton:hover {{ background-color: {BG_4}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {TEXT_3}; }}
        QPushButton[buttonKind="primary"] {{ background-color: {ACCENT}; color: {ACCENT_ON};
            border-color: {ACCENT}; }}
        QPushButton[buttonKind="primary"]:hover {{ background-color: {ACCENT_HOVER}; }}
        QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            color: {TEXT_1}; background-color: {BG_1};
            border: 1px solid {BORDER_HEX}; border-radius: 5px;
            min-height: 28px; padding: 0 8px; selection-background-color: {BG_4};
            selection-color: {TEXT_1};
        }}
        QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
        QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {TEXT_2}; }}
        QComboBox {{ color: {COMBO_TEXT}; selection-color: {COMBO_TEXT}; }}
        QComboBox::drop-down {{ width: 24px; border: none; }}
        QComboBox QAbstractItemView {{
            color: {COMBO_TEXT}; background-color: {BG_3}; border: 1px solid {BORDER_HEX};
            selection-background-color: {BG_4}; selection-color: {COMBO_TEXT};
        }}
        QGroupBox {{
            color: {TEXT_1}; background: transparent; border: 1px solid {BORDER_HEX};
            border-radius: 7px; margin-top: 13px; padding: 14px 11px 11px;
            font-size: 11px; font-weight: 600;
        }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px;
            background-color: {BG_0}; }}
        QTabWidget::pane {{ border: none; background: transparent; }}
        QTabBar::tab {{
            color: {TEXT_3}; background: transparent; padding: 8px 13px;
            margin-right: 5px; border: none; border-bottom: 2px solid transparent;
            font-size: 11px;
        }}
        QTabBar::tab:hover {{ color: {TEXT_1}; }}
        QTabBar::tab:selected {{ color: {TEXT_1}; border-bottom-color: {TEXT_1}; }}
        QTreeWidget, QTableWidget, QTreeView, QTableView, QListWidget {{
            color: {TEXT_1}; background-color: {BG_2}; alternate-background-color: {BG_2};
            border: 1px solid {BORDER_HEX}; border-radius: 7px;
            gridline-color: {BORDER_HEX}; selection-background-color: {ACCENT_SOFT};
            selection-color: {TEXT_1};
        }}
        QTreeWidget::item, QTableWidget::item {{ padding: 5px; border: none; }}
        QTreeWidget::item:hover, QTableWidget::item:hover {{ background-color: {BG_3}; }}
        QTreeWidget::item:selected, QTableWidget::item:selected {{
            color: {TEXT_1}; background-color: {ACCENT_SOFT};
        }}
        QHeaderView::section {{
            color: {TEXT_2}; background-color: {BG_3}; border: none;
            border-bottom: 1px solid {BORDER_HEX}; padding: 8px; font-size: 11px;
        }}
        QScrollArea {{ background: transparent; border: none; }}
        QCheckBox, QRadioButton {{ color: {TEXT_2}; spacing: 7px; font-size: 12px; }}
        QCheckBox::indicator, QRadioButton::indicator {{ width: 13px; height: 13px; }}
        QCheckBox::indicator:unchecked {{ background: {BG_1}; border: 1px solid {BORDER_HEX};
            border-radius: 3px; }}
        QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT};
            border-radius: 3px; image: url({asset_path('assets/ui/check.svg')}); }}
        QSlider::groove:horizontal {{ height: 3px; background: {BORDER_HEX}; border-radius: 1px; }}
        QSlider::sub-page:horizontal {{ background: {TEXT_2}; border-radius: 1px; }}
        QSlider::handle:horizontal {{ width: 9px; margin: -6px 0; background: {ACCENT};
            border: 1px solid {ACCENT}; border-radius: 3px; }}
        QSlider::groove:vertical {{ width: 3px; background: {BORDER_HEX}; }}
        QSlider::handle:vertical {{ height: 9px; margin: 0 -3px; background: {ACCENT}; }}
        QProgressBar {{ color: {TEXT_2}; background: {BG_1}; border: none;
            border-radius: 2px; max-height: 4px; font-size: 9px; }}
        QProgressBar::chunk {{ background: {TEXT_2}; border-radius: 2px; }}
        QMenu {{ color: {TEXT_1}; background-color: {BG_3};
            border: 1px solid {BORDER_HEX}; border-radius: {R_S}px; padding: 6px; }}
        QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: 4px; }}
        QMenu::item:selected {{ background-color: {BG_4}; }}
        QMenu::item:disabled {{ color: {TEXT_3}; }}
        QMenu::separator {{ height: 1px; background: {BORDER_HEX}; margin: 4px 8px; }}
        QSplitter::handle {{ background: {BG_0}; }}
        QSplitter::handle:hover {{ background: {BORDER_HEX}; }}
        {scrollbar_qss()}
    """


def apply_siui_palette():
    from siui.core import SiColor, SiGlobal

    mapping = {
        SiColor.INTERFACE_BG_A: BG_0,
        SiColor.INTERFACE_BG_B: BG_1,
        SiColor.INTERFACE_BG_C: BG_2,
        SiColor.INTERFACE_BG_D: BG_3,
        SiColor.INTERFACE_BG_E: BG_4,
        SiColor.TEXT_A: TEXT_1,
        SiColor.TEXT_B: TEXT_1,
        SiColor.TEXT_C: TEXT_2,
        SiColor.TEXT_D: TEXT_2,
        SiColor.TEXT_E: TEXT_3,
        SiColor.TEXT_THEME: TEXT_1,
        SiColor.THEME: ACCENT,
        SiColor.THEME_TRANSITION_A: ACCENT,
        SiColor.THEME_TRANSITION_B: ACCENT_TRANSITION,
        SiColor.TITLE_INDICATOR: TEXT_2,
        SiColor.TITLE_HIGHLIGHT: TITLE_HIGHLIGHT,
        SiColor.SIDE_MSG_THEME_NORMAL: BG_4,
        SiColor.SIDE_MSG_THEME_SUCCESS: SUCCESS,
        SiColor.SIDE_MSG_THEME_INFO: INFO,
        SiColor.SIDE_MSG_THEME_WARNING: WARNING,
        SiColor.SIDE_MSG_THEME_ERROR: DANGER,
        SiColor.MENU_BG: BG_3,
        SiColor.TOOLTIP_BG: BG_3,
        SiColor.BUTTON_PANEL: BG_3,
        SiColor.BUTTON_SHADOW: BG_0,
        SiColor.BUTTON_ON: ACCENT_DEEP,
        SiColor.BUTTON_OFF: BG_4,
        SiColor.BUTTON_THEMED_BG_A: ACCENT,
        SiColor.BUTTON_THEMED_BG_B: ACCENT,
        SiColor.BUTTON_THEMED_SHADOW_A: ACCENT_DEEP,
        SiColor.BUTTON_THEMED_SHADOW_B: ACCENT_DEEP,
        SiColor.BUTTON_TEXT_BUTTON_IDLE: TEXT_1,
        SiColor.BUTTON_TEXT_BUTTON_FLASH: ACCENT_HOVER,
        SiColor.BUTTON_TEXT_BUTTON_HOVER: ACCENT_HOVER,
        SiColor.BUTTON_LONG_PRESS_PANEL: DANGER,
        SiColor.BUTTON_LONG_PRESS_SHADOW: BG_0,
        SiColor.BUTTON_LONG_PRESS_PROGRESS: DANGER_HOVER,
        SiColor.RADIO_BUTTON_UNCHECKED: BG_1,
        SiColor.RADIO_BUTTON_CHECKED: ACCENT,
        SiColor.CHECKBOX_UNCHECKED: TEXT_3,
        SiColor.CHECKBOX_CHECKED: ACCENT,
        SiColor.CHECKBOX_SVG: ACCENT_ON,
        SiColor.SWITCH_DEACTIVATE: BG_2,
        SiColor.SWITCH_ACTIVATE: ACCENT_ON,
        SiColor.SVG_NORMAL: TEXT_2,
        SiColor.SVG_THEME: TEXT_1,
        SiColor.PROGRESS_BAR_TRACK: BG_4,
        SiColor.PROGRESS_BAR_PROCESSING: TEXT_2,
        SiColor.PROGRESS_BAR_COMPLETING: TEXT_1,
        SiColor.PROGRESS_BAR_PAUSED: TEXT_3,
    }
    for token, code in mapping.items():
        SiGlobal.siui.colors.assign(token, code)
    apply_application_style()


def apply_application_style():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    palette = app.palette()
    _apply_palette_colors(palette)
    app.setPalette(palette)
    style = application_qss()
    if app.styleSheet() != style:
        app.setStyleSheet(style)


def _build_style_color_maps():
    replacements = {}
    value_keys = {}
    for palette in PALETTES.values():
        for key, value in palette.items():
            if not isinstance(value, str) or not (value.startswith("#") or value.startswith("rgba")):
                continue
            normalized = value.lower().replace(" ", "")
            value_keys.setdefault(normalized, set()).add(key)
            if normalized not in replacements or key.startswith("BG_"):
                replacements[normalized] = key
    roles = {}
    for normalized, keys in value_keys.items():
        selected = replacements[normalized]
        equivalent = all(
            all(palette[key] == palette[selected] for palette in PALETTES.values())
            for key in keys
        )
        if equivalent and selected in _QSS_PALETTE_ROLES:
            roles[normalized] = _QSS_PALETTE_ROLES[selected]
    return replacements, roles


_STYLE_REPLACEMENTS, _STYLE_ROLES = _build_style_color_maps()
_STYLE_PATTERN = re.compile(r"#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|rgba?\([^)]*\)")


def _retranslate_style(style):
    if not style:
        return style

    def replace(match):
        normalized = match.group(0).lower().replace(" ", "")
        role = _STYLE_ROLES.get(normalized)
        if role:
            return f"palette({role})"
        key = _STYLE_REPLACEMENTS.get(normalized)
        return str(PALETTES[_CURRENT_THEME][key]) if key else match.group(0)

    return _STYLE_PATTERN.sub(replace, style)


def apply_widget_theme(root):
    from PyQt5.QtWidgets import QPushButton, QWidget

    widgets = [root, *root.findChildren(QObject)]
    for widget in widgets:
        if not hasattr(widget, "setStyleSheet"):
            continue
        original_palette = widget.palette()
        palette = QPalette(original_palette)
        _apply_palette_colors(palette)
        if palette != original_palette:
            widget.setPalette(palette)
        style = widget.styleSheet()
        translated = _retranslate_style(style)
        if translated != style:
            widget.setStyleSheet(translated)
        if isinstance(widget, QPushButton) and widget.property("buttonKind"):
            style_push_button(widget, widget.property("buttonKind"))
        if widget.property("neutralSlider") and hasattr(widget, "style_data"):
            style_accent_slider(widget)
        if widget.property("neutralCombo"):
            raise_well_on_card(
                widget,
                adjust_layout=widget.property("neutralComboAdjustLayout") is not False,
            )
        apply_theme = getattr(widget, "apply_theme", None)
        if callable(apply_theme):
            apply_theme()
        icon_name = widget.property("fluentIconName") if hasattr(widget, "property") else None
        if icon_name and hasattr(widget, "setIcon"):
            widget.setIcon(fluent_icon(
                icon_name,
                int(widget.property("fluentIconSize") or 16),
                ACCENT_ON if widget.property("fluentIconOnAccent") else TEXT_2,
            ))
        qt_style = QWidget.style(widget)
        qt_style.unpolish(widget)
        qt_style.polish(widget)
        widget.update()


def set_theme(name, *, notify=True):
    if name not in PALETTES:
        raise ValueError(f"unknown theme: {name!r}")
    changed_theme = name != _CURRENT_THEME
    _install_palette(name)
    apply_siui_palette()
    if changed_theme and notify:
        from PyQt5.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            top_level = set(app.topLevelWidgets())
            for widget in top_level:
                ancestor = widget.parentWidget()
                nested = False
                while ancestor is not None:
                    if ancestor in top_level:
                        nested = True
                        break
                    ancestor = ancestor.parentWidget()
                if not nested:
                    apply_widget_theme(widget)
        changed.emit(name)


def initialize_theme(name):
    _install_palette(name)
