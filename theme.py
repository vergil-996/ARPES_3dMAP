# -*- coding: utf-8 -*-
"""BandScope UI 设计令牌 —— 全项目颜色 / 间距 / 圆角 / 字体的唯一来源。

所有界面代码必须从这里取色，禁止再出现硬编码的 ``#RRGGBB`` 字面量。
调整配色只需改这一个文件。

层级约定（深色实验室风格，由深到浅）：

- ``BG_0`` 窗口底色（最深）
- ``BG_1`` 绘图区 / 输入框底
- ``BG_2`` 面板 / 侧边栏 / 工具栏
- ``BG_3`` 卡片 / 抬升面
- ``BG_4`` 悬停 / 滑条轨道（最浅）

强调色只有一个：``ACCENT``（品牌粉）。语义色各司其职，``DANGER``
仅用于删除等不可逆操作。
"""

from PyQt5.QtGui import QColor, QFont

# ---------------------------------------------------------------------------
# 背景层级
# ---------------------------------------------------------------------------
BG_0 = "#0D0D13"
BG_1 = "#12121A"
BG_2 = "#181822"
BG_3 = "#20202C"
BG_4 = "#2A2A38"

# 边框（rgba 形式供 QSS 使用；BORDER_HEX 供 matplotlib 等只接受 hex 的场景）
BORDER = "rgba(255, 255, 255, 0.08)"
BORDER_STRONG = "rgba(255, 255, 255, 0.14)"
BORDER_HEX = "#2E2E3C"

# ---------------------------------------------------------------------------
# 文本
# ---------------------------------------------------------------------------
TEXT_1 = "#F2F2F7"
TEXT_2 = "#A3A3B5"
TEXT_3 = "#858599"

# ---------------------------------------------------------------------------
# 强调色与语义色
# ---------------------------------------------------------------------------
ACCENT = "#FF7EB6"                       # 唯一主强调色（品牌粉，柔化自 #FF69B4）
ACCENT_HOVER = "#FF9AC6"
ACCENT_SOFT = "rgba(255, 126, 182, 0.16)"
ACCENT_DIM = "rgba(255, 126, 182, 0.45)"
ACCENT_ON = "#1A0B14"                    # 主色上的文字（深底字）
ACCENT_TRANSITION = "#B85CFF"            # 过渡渐变的另一端（logo / 主题过渡动画）
ACCENT_DEEP = "#7F3F5B"                  # 主色压暗版（阴影 / 按下态）

INFO = "#5B8DEF"
INFO_SOFT = "rgba(91, 141, 239, 0.12)"
SUCCESS = "#34D399"
WARNING = "#FBBF24"
DANGER = "#F0506E"
DANGER_HOVER = "#F5738D"
DANGER_SOFT = "rgba(240, 80, 110, 0.12)"
DANGER_BORDER = "rgba(240, 80, 110, 0.4)"

# 白色透明叠层（SiUI 按钮的开关态染色，非调色板颜色）
TINT_CLEAR = "#00FFFFFF"
TINT_HOVER = "#12FFFFFF"

# matplotlib 曲线配色环（第一条永远是主色）
CURVE_PALETTE = [
    ACCENT, "#4CC9F0", "#F9C74F", "#90BE6D", "#F3722C", "#B388FF", "#43AA8B",
]

# ---------------------------------------------------------------------------
# 间距（4pt 网格）与圆角
# ---------------------------------------------------------------------------
SP_1 = 4
SP_2 = 8
SP_3 = 12
SP_4 = 16
SP_5 = 24
SP_6 = 32

R_S = 6
R_M = 10
R_L = 14

# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------
FONT_FAMILY = '"Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif'
FONT_MONO = '"Consolas", "Courier New", monospace'

# 供 QSS 大块样式表做 ``%`` 格式化用的令牌字典（避免 f-string 与 QSS 花括号冲突）
QSS_TOKENS = {
    "BG0": BG_0,
    "BG1": BG_1,
    "BG2": BG_2,
    "BG3": BG_3,
    "BG4": BG_4,
    "BORD": BORDER_HEX,
    "BORD_S": BORDER_STRONG,
    "T1": TEXT_1,
    "T2": TEXT_2,
    "T3": TEXT_3,
    "ACCENT": ACCENT,
    "ACCENT_H": ACCENT_HOVER,
    "ACC_SOFT": ACCENT_SOFT,
    "ACC_DIM": ACCENT_DIM,
    "ACC_ON": ACCENT_ON,
    "INFO": INFO,
    "INFO_SOFT": INFO_SOFT,
    "SUCCESS": SUCCESS,
    "WARNING": WARNING,
    "DANGER": DANGER,
    "DANGER_H": DANGER_HOVER,
    "DANGER_SOFT": DANGER_SOFT,
    "DANGER_BORD": DANGER_BORDER,
    "RS": R_S,
    "RM": R_M,
    "RL": R_L,
    "FONT": FONT_FAMILY,
    "MONO": FONT_MONO,
}


# ---------------------------------------------------------------------------
# Qt 辅助
# ---------------------------------------------------------------------------
def accent_qcolor() -> QColor:
    return QColor(ACCENT)


def apply_app_font() -> None:
    """Set the application font before SiUI snapshots it in its custom controls."""
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "sans-serif"])
        font.setPixelSize(13)
        app.setFont(font)


def action_button_qss(kind="primary") -> str:
    palette = {
        "primary": (ACCENT, ACCENT_ON, ACCENT_HOVER, "transparent"),
        "secondary": (BG_4, TEXT_1, BORDER_HEX, BORDER),
        "danger": (DANGER_SOFT, DANGER, DANGER_BORDER, DANGER_BORDER),
    }
    bg, fg, hover, border = palette[kind]
    return (
        f"QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border};"
        f" border-radius: {R_S}px; padding: 0 10px; font-size: 13px; font-weight: 500; }}"
        f"QPushButton:hover {{ background: {hover}; }}"
        f"QPushButton:pressed {{ background: {BG_3}; color: {TEXT_1}; }}"
        f"QPushButton:focus {{ border: 1px solid {ACCENT}; }}"
        f"QPushButton:disabled {{ background: {BG_3}; color: {TEXT_3}; border: 1px solid {BORDER}; }}"
    )


def style_push_button(btn, kind: str = "primary") -> None:
    """按语义层级给 SiPushButton 上色。

    kind:
        ``"primary"``   —— 每组最多一个的主操作（应用 / 确定），主色底深字
        ``"secondary"`` —— 常规操作（保存 / 重置 / 返回），灰底
        ``"danger"``    —— 仅删除 / 清空等不可逆操作，红色
    """
    from siui.core import SiColor
    from PyQt5.QtWidgets import QPushButton

    if isinstance(btn, QPushButton):
        btn.setStyleSheet(action_button_qss(kind))
        return

    palette = {
        "primary": (ACCENT, ACCENT_ON),
        "secondary": (BG_4, TEXT_1),
        "danger": (DANGER, TEXT_1),
    }
    if kind not in palette:
        raise ValueError(f"unknown button kind: {kind!r}")
    panel, text = palette[kind]
    btn.colorGroup().assign(SiColor.BUTTON_PANEL, panel)
    btn.colorGroup().assign(SiColor.TEXT_B, text)
    btn.reloadStyleSheet()


def style_accent_slider(slider) -> None:
    """统一滑条外观：细轨道 + 白色小圆钮 + 主色填充（替代默认的 36×24 胖紫钮）。

    注意 ``SliderStyleData`` 的字段名是 ``track_color / thumb_idle_color``，
    ``_thumb_color`` 在构造时被快照并有动画驱动，所以除 style_data 外还要
    把动画的当前值/终值一并钉住，否则首次悬停前仍是旧色。
    """
    sd = slider.style_data
    sd.track_color = accent_qcolor()
    sd.background_color = QColor(BG_4)
    sd.thumb_idle_color = QColor(TEXT_1)
    sd.thumb_hover_color = QColor(ACCENT_HOVER)
    sd.thumb_width = 18
    sd.thumb_height = 18
    sd.track_height = 6

    try:
        slider._thumb_color = QColor(TEXT_1)
        thumb_ani = getattr(slider, "thumb_color_ani", None)
        if thumb_ani is not None:
            thumb_ani.setCurrentValue(QColor(TEXT_1))
            thumb_ani.setEndValue(QColor(TEXT_1))
    except Exception:
        pass
    slider.update()


def card_qss() -> str:
    """分组卡片的统一 QSS（抬升面 + 细边框 + 圆角）。"""
    return (
        "QFrame#CardGroup {"
        f"background-color: {BG_3};"
        f"border: 1px solid {BORDER};"
        f"border-radius: {R_M}px;"
        "}"
    )


def card_title_qss() -> str:
    """卡片标题文字（配合左侧主色竖条使用）。"""
    return f"color: {TEXT_1}; background: transparent; font-size: 13px; font-weight: 600;"


def field_label_qss() -> str:
    """卡片内字段小标签（滑条名、输入框名）：弱化一级，避免满屏粗白字。"""
    return f"color: {TEXT_2}; background: transparent; font-size: 12px;"


def slider_value_qss() -> str:
    """滑条行尾的当前数值：等宽字体，与标签同行右对齐。"""
    return f"color: {TEXT_1}; font-size: 12px; font-family: {FONT_MONO};"


def raise_well_on_card(widget) -> None:
    """把 siui 输入类控件（输入框 / 下拉框 / 数字框）的井底色提亮到 BG_4。

    这些控件默认从 ``INTERFACE_BG_B``（BG_1）取井底色，放在 BG_3 卡片上
    会形成"黑洞"。对齐到比卡片亮一级的 BG_4 后，控件读起来是抬起的
    可操作面而不是凹陷。对 SiCapsuleComboBox 自动落到内部的 _line_edit。
    """
    from siui.core import SiColor

    target = getattr(widget, "_line_edit", widget)
    menu = getattr(widget, "_menu", None)
    if menu is not None:
        menu.setMaximumHeight(360)
        menu.style_data.background_color = QColor(BG_3)
        menu.style_data.border_color = QColor(BORDER_HEX)
        menu._initStyle()
    sd = getattr(target, "style_data", None)
    if sd is not None and hasattr(sd, "text_background_color"):
        sd.title_background_color = QColor(BG_4)
        sd.text_background_color = QColor(BG_4)
        sd.title_color_idle = QColor(TEXT_2)
        sd.title_color_focused = QColor(TEXT_1)
        sd.text_color = QColor(TEXT_1)
        sd.text_indicator_color_editing = QColor(ACCENT)
        sd.text_indicator_color_error = QColor(DANGER)
        target.title_color_ani.setCurrentValue(QColor(TEXT_2))
        target.title_color_ani.setEndValue(QColor(TEXT_2))
        target._initStyleSheet()
        target.update()
        return
    try:
        target.colorGroup().assign(SiColor.INTERFACE_BG_B, BG_4)
        target.colorGroup().assign(SiColor.INTERFACE_BG_D, BORDER_HEX)
        target.reloadStyleSheet()
    except Exception:
        pass


def value_input_qss() -> str:
    """数值输入框（QSpinBox / QDoubleSpinBox）的统一 QSS。"""
    return (
        "QLineEdit, QSpinBox, QDoubleSpinBox {"
        f"background-color: {BG_1};"
        f"color: {TEXT_1};"
        f"border: 1px solid {BORDER};"
        f"border-radius: {R_S}px;"
        "padding-left: 10px;"
        "padding-right: 10px;"
        f"font-family: {FONT_MONO}; font-size: 12px;"
        "}"
        "QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {"
        f"border: 1px solid {ACCENT};"
        "}"
        f"QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {TEXT_3}; background: {BG_3}; }}"
        "QSpinBox::up-button, QSpinBox::down-button,"
        "QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {"
        "width: 0px;"
        "border: none;"
        "}"
    )


def section_label_qss() -> str:
    """卡片/分组内小标签的统一 QSS（替代散落的 color: white; font-weight: bold）。"""
    return f"color: {TEXT_1}; font-weight: bold;"


def nav_tab_qss() -> str:
    """右栏页签（图像控制 / 渲染控制 / 处理分析）的胶囊式 QSS。

    替代 SiCapsuleButton：设计稿要求 inactive=抬升面灰、active=主色
    柔光底 + 主色字，无任何徽章动画。
    """
    return (
        "QPushButton {"
        f"background-color: {BG_3};"
        f"color: {TEXT_2};"
        "border: 1px solid transparent;"
        "border-radius: 6px;"
        "padding: 0 10px;"
        "font-size: 13px;"
        "}"
        "QPushButton:hover {"
        f"color: {TEXT_1};"
        "}"
        "QPushButton:checked {"
        f"background-color: {ACCENT_SOFT};"
        f"color: {ACCENT};"
        f"border: 1px solid {ACCENT_DIM};"
        "font-weight: 600;"
        "}"
    )


# ---------------------------------------------------------------------------
# SiUI 全局色板对齐
# ---------------------------------------------------------------------------
def apply_siui_palette() -> None:
    """把 SiUI 全局色板整体对齐到本文件的设计令牌。

    SiUI 的自绘控件（分组标题指示条、下拉菜单、开关、滚动条等）绘制时从
    ``SiGlobal.siui.colors`` 取色，QSS 压不到。必须在**创建任何 SiUI 控件
    之前**调用一次（多数控件在初始化或 reloadStyleSheet 时快照色值）。
    """
    from siui.core import SiColor, SiGlobal

    mapping = {
        # 背景 5 级
        SiColor.INTERFACE_BG_A: BG_0,
        SiColor.INTERFACE_BG_B: BG_1,
        SiColor.INTERFACE_BG_C: BG_2,
        SiColor.INTERFACE_BG_D: BG_3,
        SiColor.INTERFACE_BG_E: BG_4,
        # 文本
        SiColor.TEXT_A: TEXT_1,
        SiColor.TEXT_B: TEXT_1,
        SiColor.TEXT_C: TEXT_2,
        SiColor.TEXT_D: TEXT_2,
        SiColor.TEXT_E: TEXT_3,
        SiColor.TEXT_THEME: ACCENT,
        # 主题色与过渡渐变
        SiColor.THEME: ACCENT,
        SiColor.THEME_TRANSITION_A: ACCENT,
        SiColor.THEME_TRANSITION_B: ACCENT_TRANSITION,
        SiColor.TITLE_INDICATOR: ACCENT,
        SiColor.TITLE_HIGHLIGHT: "#29FF7EB6",  # ACCENT 16% alpha（ARGB hex）
        # 侧边消息语义色
        SiColor.SIDE_MSG_THEME_NORMAL: BG_4,
        SiColor.SIDE_MSG_THEME_SUCCESS: SUCCESS,
        SiColor.SIDE_MSG_THEME_INFO: INFO,
        SiColor.SIDE_MSG_THEME_WARNING: WARNING,
        SiColor.SIDE_MSG_THEME_ERROR: DANGER,
        # 菜单 / 提示
        SiColor.MENU_BG: BG_3,
        SiColor.TOOLTIP_BG: BG_3,
        # 按钮体系（默认粉紫渐变消为单色主色）
        SiColor.BUTTON_PANEL: BG_4,
        SiColor.BUTTON_SHADOW: BG_0,
        SiColor.BUTTON_ON: ACCENT_DEEP,
        SiColor.BUTTON_OFF: BG_4,
        SiColor.BUTTON_THEMED_BG_A: ACCENT,
        SiColor.BUTTON_THEMED_BG_B: ACCENT,
        SiColor.BUTTON_THEMED_SHADOW_A: ACCENT_DEEP,
        SiColor.BUTTON_THEMED_SHADOW_B: ACCENT_DEEP,
        SiColor.BUTTON_TEXT_BUTTON_IDLE: ACCENT,
        SiColor.BUTTON_TEXT_BUTTON_FLASH: ACCENT_HOVER,
        SiColor.BUTTON_TEXT_BUTTON_HOVER: ACCENT_HOVER,
        SiColor.BUTTON_LONG_PRESS_PANEL: DANGER,
        SiColor.BUTTON_LONG_PRESS_SHADOW: "#5C2430",
        SiColor.BUTTON_LONG_PRESS_PROGRESS: DANGER_HOVER,
        # 选择类控件
        SiColor.RADIO_BUTTON_UNCHECKED: BG_1,
        SiColor.RADIO_BUTTON_CHECKED: ACCENT,
        SiColor.CHECKBOX_UNCHECKED: TEXT_3,
        SiColor.CHECKBOX_CHECKED: ACCENT,
        SiColor.CHECKBOX_SVG: ACCENT_ON,
        SiColor.SWITCH_DEACTIVATE: TEXT_1,
        SiColor.SWITCH_ACTIVATE: ACCENT_ON,
        # SVG 图标
        SiColor.SVG_NORMAL: TEXT_2,
        SiColor.SVG_THEME: ACCENT,
        # 进度条
        SiColor.PROGRESS_BAR_TRACK: BG_4,
        SiColor.PROGRESS_BAR_PROCESSING: ACCENT,
        SiColor.PROGRESS_BAR_COMPLETING: SUCCESS,
        SiColor.PROGRESS_BAR_PAUSED: TEXT_3,
    }
    colors = SiGlobal.siui.colors
    for token, code in mapping.items():
        colors.assign(token, code)
