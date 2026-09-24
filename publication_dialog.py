# -*- coding: utf-8 -*-
"""图片导出：样式选择面板（卡片预览 / 草稿语义 / 视图族记忆）。

交互约定（plan v2 §4.1/§4.2）：

- 面板只打开一个实例；打开时冻结快照，顶部展示来源页/帧与结果类型。
- 卡片与大图预览由同一份快照、同一 renderer 生成，仅 dpi 不同。
- 点击卡片或微调只改草稿；“导出此预览”在写入成功后提交该视图族偏好；
  取消/保存失败不提交草稿、不改变左侧状态。
- “更新预览”重新冻结主页面科学状态；预览期间主页面变化只提示、不静默
  换数据。
- 2D/1D 预览在串行 worker 线程渲染（独立 Figure/Agg）；3D 预览在 GUI
  线程用独立 off-screen 场景渲染（Windows/VTK 安全）。
"""
from __future__ import annotations

import io
from typing import Any, Dict, Optional, Tuple

from PyQt5.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QCursor, QIcon, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import theme
from publication_export import (
    ExportError,
    capture_snapshot,
    commit_style,
    committed_style_for,
    default_filename,
    output_options_for,
    persist_output_options,
    render_and_save,
)
from publication_models import (
    FAMILY_FORMATS,
    FAMILY_LABELS,
    OutputOptions,
    overrides_signature,
    styles_for_family,
    validate_overrides,
)
from publication_renderers import (
    RENDER_LOCK,
    RenderError,
    default_colorbar_center,
    render_snapshot,
)

CARD_DPI = 80          # 卡片预览 dpi（完整数据、较低像素）
LARGE_DPI = 150        # 大图预览 dpi（与正式文件同一排版）
CACHE_LIMIT = 16


# ---------------------------------------------------------------------------
# 预览 worker（仅 2D/1D；3D 在 GUI 线程渲染）
# ---------------------------------------------------------------------------


class _PreviewSignals(QObject):
    done = pyqtSignal(object, int, bytes)          # key, revision, png bytes
    failed = pyqtSignal(object, int, str)


class _PreviewTask(QRunnable):
    def __init__(self, key, revision, snapshot, style, overrides, options, dpi):
        super().__init__()
        self.key = key
        self.revision = revision
        self.snapshot = snapshot
        self.style = style
        self.overrides = overrides
        self.options = options
        self.dpi = dpi
        self.signals = _PreviewSignals()

    @pyqtSlot()
    def run(self):
        try:
            fig = render_snapshot(
                self.snapshot, self.style, self.overrides, self.options, dpi=self.dpi
            )
            try:
                buffer = io.BytesIO()
                fig.savefig(buffer, format="png", dpi=int(fig.get_dpi()), facecolor="white")
                payload = buffer.getvalue()
            finally:
                fig.clear()
        except Exception as exc:  # noqa: BLE001 - 结果通过信号回 GUI 线程
            self.signals.failed.emit(self.key, self.revision, str(exc))
            return
        self.signals.done.emit(self.key, self.revision, payload)


# ---------------------------------------------------------------------------
# 样式卡片
# ---------------------------------------------------------------------------


class _WheelSpinBox(QSpinBox):
    """滚轮直接调节的百分比输入框：悬停滚动即改值，无需先点击聚焦。"""

    def wheelEvent(self, event):
        if not self.hasFocus():
            self.setFocus(Qt.MouseFocusReason)
        super().wheelEvent(event)


class _WheelDoubleSpinBox(QDoubleSpinBox):
    """滚轮直接调节的小数输入框：悬停滚动即改值，无需先点击聚焦。"""

    def wheelEvent(self, event):
        if not self.hasFocus():
            self.setFocus(Qt.MouseFocusReason)
        super().wheelEvent(event)


class _StyleCard(QToolButton):
    def __init__(self, style, parent=None):
        super().__init__(parent)
        self.style = style
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setFixedSize(216, 224)
        self.setCursor(Qt.PointingHandCursor)
        self._base_text = style.name
        self.setText(self._base_text)
        self._apply_qss(False)
        self.toggled.connect(self._apply_qss)

    def _apply_qss(self, checked):
        border = theme.ACCENT if checked else theme.BORDER_HEX
        self.setStyleSheet(
            "QToolButton {"
            f" background-color: {theme.BG_1}; color: {theme.TEXT_1};"
            f" border: 1.5px solid {border}; border-radius: 8px;"
            " padding: 8px; font-size: 12px; font-weight: 600; }"
            f"QToolButton:hover {{ border: 1.5px solid {theme.ACCENT_DIM}; }}"
        )

    def set_tuned(self, tuned: bool):
        suffix = " · 已微调" if tuned else ""
        self.setText(self._base_text + suffix)

    def set_preview(self, pixmap: Optional[QPixmap]):
        if pixmap is None or pixmap.isNull():
            return
        scaled = pixmap.scaled(196, 168, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setIconSize(scaled.size())
        self.setIcon(QIcon(scaled))


# ---------------------------------------------------------------------------
# 主面板
# ---------------------------------------------------------------------------


class PublicationExportDialog(QDialog):
    """单实例样式选择面板。由主窗口持有并按需重建快照。"""

    def __init__(self, window):
        super().__init__(window)
        self.main_window = window
        self.setWindowTitle("图片导出 · 截图样式")
        self.setModal(False)
        self.resize(880, 780)
        self.setStyleSheet(
            f"QDialog {{ background-color: {theme.BG_0}; }}"
            f"QLabel {{ color: {theme.TEXT_1}; background: transparent; }}"
            f"QCheckBox {{ color: {theme.TEXT_1}; spacing: 6px; }}"
            f"QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{"
            f" color: {theme.TEXT_1}; background-color: {theme.BG_3};"
            f" border: 1px solid {theme.BORDER_HEX}; border-radius: 5px; padding: 2px 6px; }}"
            f"QComboBox QAbstractItemView {{ color: {theme.TEXT_1}; background-color: {theme.BG_2}; }}"
        )

        self.snapshot = None
        self._draft_style_id: Optional[str] = None
        self._draft_overrides: Dict[str, Any] = {}
        self._committed_overrides: Dict[str, Dict[str, Any]] = {}
        self._preview_cache: Dict[Tuple, bytes] = {}
        self._preview_revision = 0
        self._big_ready = False
        self._busy_3d = False
        self._pending_regenerate = False
        self._fit_to_window = True
        # 用户是否手动动过色带中心；未动过时中心值随布局默认自动刷新
        self._center_dirty = False
        # 色带几何参数按位置（right/left/top/bottom）分别记忆：切换位置时
        # 收存旧位置的一套、换入新位置的一套；导出/提交/换样式/换快照/关闭时清空
        self._cbar_pos_memory: Dict[str, Dict[str, Any]] = {}
        self._cbar_pos_current = "right"

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self._regenerate_previews)

        self._stale_timer = QTimer(self)
        self._stale_timer.setInterval(600)
        self._stale_timer.timeout.connect(self._check_stale_source)

        self._build_ui()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self.title_label = QLabel("图片导出")
        self.title_label.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {theme.TEXT_1};")
        header.addWidget(self.title_label)
        header.addStretch(1)
        self.family_badge = QLabel("")
        self.family_badge.setStyleSheet(
            f"color: {theme.ACCENT}; background-color: {theme.ACCENT_SOFT};"
            f" border: 1px solid {theme.ACCENT_DIM}; border-radius: 6px; padding: 2px 10px; font-size: 11px;"
        )
        header.addWidget(self.family_badge)
        layout.addLayout(header)

        self.source_label = QLabel("")
        self.source_label.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")
        layout.addWidget(self.source_label)
        note = QLabel("仅影响截图输出，不改变左侧视图、数据、相机与色阶锁定。")
        note.setStyleSheet(f"color: {theme.TEXT_3}; font-size: 11px;")
        layout.addWidget(note)

        # 样式卡片
        self.cards_row = QHBoxLayout()
        self.cards_row.setSpacing(10)
        self.card_group = QButtonGroup(self)
        self.card_group.setExclusive(True)
        self.cards: Dict[str, _StyleCard] = {}
        layout.addLayout(self.cards_row)

        # 大图预览
        preview_header = QHBoxLayout()
        self.preview_title = QLabel("预览")
        self.preview_title.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {theme.TEXT_1};")
        preview_header.addWidget(self.preview_title)
        preview_header.addStretch(1)
        self.fit_toggle = QPushButton("100% 像素")
        self.fit_toggle.setFixedHeight(24)
        theme.style_push_button(self.fit_toggle, "secondary")
        self.fit_toggle.setCursor(Qt.PointingHandCursor)
        self.fit_toggle.clicked.connect(self._toggle_fit)
        preview_header.addWidget(self.fit_toggle)
        layout.addLayout(preview_header)

        self.preview_scroll = QScrollArea(self)
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setMinimumHeight(240)
        self.preview_scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {theme.BG_1}; border: 1px solid {theme.BORDER_HEX}; border-radius: 8px; }}"
        )
        self.preview_label = QLabel("正在生成预览…")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet(f"color: {theme.TEXT_2}; background: transparent;")
        self.preview_scroll.setWidget(self.preview_label)
        layout.addWidget(self.preview_scroll, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {theme.TEXT_2}; font-size: 11px;")
        layout.addWidget(self.status_label)

        # 微调
        self.tune_toggle = QPushButton("微调（选中样式） ▾")
        self.tune_toggle.setCheckable(True)
        self.tune_toggle.setFixedHeight(26)
        theme.style_push_button(self.tune_toggle, "secondary")
        self.tune_toggle.setCursor(Qt.PointingHandCursor)
        self.tune_toggle.toggled.connect(self._toggle_tune_panel)
        layout.addWidget(self.tune_toggle)

        self.tune_panel = QFrame(self)
        self.tune_panel.setStyleSheet(
            f"QFrame {{ background-color: {theme.BG_2}; border: 1px solid {theme.BORDER_HEX}; border-radius: 8px; }}"
        )
        tune_layout = QVBoxLayout(self.tune_panel)
        tune_layout.setContentsMargins(10, 8, 10, 8)
        tune_layout.setSpacing(6)

        # 色条行
        self.cbar_row = QHBoxLayout()
        self.chk_cbar = QCheckBox("显示色条")
        self.chk_cbar.setChecked(True)
        self.combo_cbar_pos = QComboBox()
        self.combo_cbar_pos.addItems(["右侧", "左侧", "顶部", "底部"])
        self.combo_cbar_tick = QComboBox()
        self.combo_cbar_tick.addItems(["真实数值", "仅端点", "Low/High", "无文字"])
        self.spin_cbar_nticks = QSpinBox()
        self.spin_cbar_nticks.setRange(2, 8)
        self.spin_cbar_nticks.setValue(3)
        self.spin_cbar_len = _WheelSpinBox()
        self.spin_cbar_len.setRange(30, 100)
        self.spin_cbar_len.setValue(100)
        self.spin_cbar_len.setSuffix(" %")
        self.spin_cbar_len.setFixedWidth(84)
        self.spin_cbar_len.setToolTip(
            "色带沿长轴的长度（100% 为占满可用空间）；可直接输入或用鼠标滚轮调节"
        )
        self.chk_cbar_outline = QCheckBox("色条轮廓")
        for w, text in (
            (self.chk_cbar, ""), (QLabel("位置"), None), (self.combo_cbar_pos, None),
            (QLabel("刻度"), None), (self.combo_cbar_tick, None),
            (QLabel("数量"), None), (self.spin_cbar_nticks, None),
            (QLabel("长度"), None), (self.spin_cbar_len, None),
            (self.chk_cbar_outline, ""),
        ):
            self.cbar_row.addWidget(w)
        self.cbar_row.addStretch(1)
        tune_layout.addLayout(self.cbar_row)

        # 色条行 2：厚度与中心位置
        self.cbar_row2 = QHBoxLayout()
        self.spin_cbar_thick = _WheelDoubleSpinBox()
        self.spin_cbar_thick.setRange(0.5, 10.0)
        self.spin_cbar_thick.setSingleStep(0.1)
        self.spin_cbar_thick.setDecimals(1)
        self.spin_cbar_thick.setValue(2.2)
        self.spin_cbar_thick.setSuffix(" mm")
        self.spin_cbar_thick.setFixedWidth(84)
        self.spin_cbar_thick.setToolTip("色带厚度；可直接输入或用鼠标滚轮调节")
        self.spin_cbar_cx = _WheelSpinBox()
        self.spin_cbar_cx.setRange(0, 100)
        self.spin_cbar_cx.setValue(50)
        self.spin_cbar_cx.setSuffix(" %")
        self.spin_cbar_cx.setFixedWidth(84)
        self.spin_cbar_cx.setToolTip(
            "色带中心在画布横向的位置（初始为自动居中值）；可直接输入或用鼠标滚轮调节"
        )
        self.spin_cbar_cy = _WheelSpinBox()
        self.spin_cbar_cy.setRange(0, 100)
        self.spin_cbar_cy.setValue(50)
        self.spin_cbar_cy.setSuffix(" %")
        self.spin_cbar_cy.setFixedWidth(84)
        self.spin_cbar_cy.setToolTip(
            "色带中心在画布纵向的位置（初始为自动居中值）；可直接输入或用鼠标滚轮调节"
        )
        for w in (
            QLabel("厚度"), self.spin_cbar_thick,
            QLabel("中心 X"), self.spin_cbar_cx,
            QLabel("中心 Y"), self.spin_cbar_cy,
        ):
            self.cbar_row2.addWidget(w)
        self.cbar_row2.addStretch(1)
        tune_layout.addLayout(self.cbar_row2)

        # 边框行
        self.frame_row = QHBoxLayout()
        self.combo_frame = QComboBox()
        self.combo_frame.addItems(["四边框线", "开放轴线"])
        self.chk_box = QCheckBox("完整包围盒")
        self.chk_grid = QCheckBox("背部网格")
        self.chk_title = QCheckBox("显示标题")
        self.chk_title.setChecked(True)
        self.frame_row.addWidget(QLabel("坐标边框"))
        self.frame_row.addWidget(self.combo_frame)
        self.frame_row.addWidget(self.chk_box)
        self.frame_row.addWidget(self.chk_grid)
        self.label_body_size = QLabel("数据体大小")
        self.spin_body_size = _WheelSpinBox()
        self.spin_body_size.setRange(0, 250)
        self.spin_body_size.setValue(100)
        self.spin_body_size.setSuffix(" %")
        self.spin_body_size.setFixedWidth(84)
        self.spin_body_size.setToolTip(
            "数据体在画面中的大小（100% 为充满内容区，超过 100% 可向页边扩展，"
            "最大 250%）；可直接输入或用鼠标滚轮调节"
        )
        self.frame_row.addSpacing(12)
        self.frame_row.addWidget(self.label_body_size)
        self.frame_row.addWidget(self.spin_body_size)
        self.frame_row.addSpacing(12)
        self.frame_row.addWidget(self.chk_title)
        self.frame_row.addWidget(QLabel("面板编号"))
        self.edit_panel_label = QLineEdit()
        self.edit_panel_label.setMaxLength(8)
        self.edit_panel_label.setFixedWidth(64)
        self.edit_panel_label.setPlaceholderText("如 a")
        self.frame_row.addWidget(self.edit_panel_label)
        self.frame_row.addStretch(1)
        tune_layout.addLayout(self.frame_row)

        reset_row = QHBoxLayout()
        self.btn_reset_style = QPushButton("恢复该样式默认值")
        self.btn_reset_style.setFixedHeight(26)
        theme.style_push_button(self.btn_reset_style, "secondary")
        self.btn_reset_style.setCursor(Qt.PointingHandCursor)
        self.btn_reset_style.clicked.connect(self._reset_style_overrides)
        reset_row.addWidget(self.btn_reset_style)
        reset_row.addStretch(1)
        tune_layout.addLayout(reset_row)
        self.tune_panel.setVisible(False)
        layout.addWidget(self.tune_panel)

        # 输出
        output_frame = QFrame(self)
        output_frame.setStyleSheet(
            f"QFrame {{ background-color: {theme.BG_2}; border: 1px solid {theme.BORDER_HEX}; border-radius: 8px; }}"
        )
        output_row = QHBoxLayout(output_frame)
        output_row.setContentsMargins(10, 8, 10, 8)
        output_row.setSpacing(6)
        output_row.addWidget(QLabel("宽"))
        self.combo_width = QComboBox()
        self.combo_width.addItems(["89 mm（单栏）", "183 mm（双栏）", "自定义"])
        output_row.addWidget(self.combo_width)
        self.spin_width = QDoubleSpinBox()
        self.spin_width.setRange(20.0, 300.0)
        self.spin_width.setSuffix(" mm")
        self.spin_width.setValue(89.0)
        self.spin_width.setVisible(False)
        output_row.addWidget(self.spin_width)
        output_row.addWidget(QLabel("高"))
        self.spin_height = QDoubleSpinBox()
        self.spin_height.setRange(20.0, 300.0)
        self.spin_height.setSuffix(" mm")
        output_row.addWidget(self.spin_height)
        output_row.addWidget(QLabel("DPI"))
        self.combo_dpi = QComboBox()
        self.combo_dpi.addItems(["300", "600", "自定义"])
        output_row.addWidget(self.combo_dpi)
        self.spin_dpi = QSpinBox()
        self.spin_dpi.setRange(72, 2400)
        self.spin_dpi.setValue(600)
        self.spin_dpi.setVisible(False)
        output_row.addWidget(self.spin_dpi)
        output_row.addWidget(QLabel("格式"))
        self.combo_format = QComboBox()
        output_row.addWidget(self.combo_format)
        output_row.addStretch(1)
        layout.addWidget(output_frame)

        # 底部按钮
        footer = QHBoxLayout()
        self.btn_update = QPushButton("更新预览")
        self.btn_export = QPushButton("导出此预览")
        self.btn_cancel = QPushButton("取消")
        for btn, kind in (
            (self.btn_update, "secondary"),
            (self.btn_export, "primary"),
            (self.btn_cancel, "secondary"),
        ):
            btn.setFixedHeight(30)
            theme.style_push_button(btn, kind)
            btn.setCursor(Qt.PointingHandCursor)
        self.btn_update.clicked.connect(self._on_update_preview)
        self.btn_export.clicked.connect(self._on_export)
        self.btn_cancel.clicked.connect(self.reject)
        footer.addWidget(self.btn_update)
        footer.addStretch(1)
        footer.addWidget(self.btn_export)
        footer.addWidget(self.btn_cancel)
        layout.addLayout(footer)

        # 微调信号
        self.chk_cbar.toggled.connect(self._on_tune_changed)
        self.combo_cbar_pos.currentIndexChanged.connect(self._on_cbar_position_changed)
        self.combo_cbar_tick.currentIndexChanged.connect(self._on_tune_changed)
        self.spin_cbar_nticks.valueChanged.connect(self._on_tune_changed)
        self.spin_cbar_len.valueChanged.connect(self._on_tune_changed)
        self.chk_cbar_outline.toggled.connect(self._on_tune_changed)
        self.spin_cbar_thick.valueChanged.connect(self._on_tune_changed)
        self.spin_cbar_cx.valueChanged.connect(self._on_center_changed)
        self.spin_cbar_cy.valueChanged.connect(self._on_center_changed)
        self.combo_frame.currentIndexChanged.connect(self._on_tune_changed)
        self.chk_box.toggled.connect(self._on_tune_changed)
        self.chk_grid.toggled.connect(self._on_tune_changed)
        self.chk_title.toggled.connect(self._on_tune_changed)
        self.edit_panel_label.textChanged.connect(self._on_tune_changed)
        self.spin_body_size.valueChanged.connect(self._on_tune_changed)

        # 输出信号
        self.combo_width.currentIndexChanged.connect(self._on_output_changed)
        self.spin_width.valueChanged.connect(self._on_output_changed)
        self.spin_height.valueChanged.connect(self._on_output_changed)
        self.combo_dpi.currentIndexChanged.connect(self._on_output_changed)
        self.spin_dpi.valueChanged.connect(self._on_output_changed)
        self.combo_format.currentIndexChanged.connect(self._on_output_changed)

    # ------------------------------------------------------------- 打开/快照

    def open_for_current_view(self) -> bool:
        """捕获当前视图快照并打开面板。失败时提示并返回 False。"""
        # PyQt5 中未捕获的槽函数异常会 qFatal 终止进程：此处兜底一切异常。
        try:
            snapshot = capture_snapshot(self.main_window)
        except ExportError as exc:
            self.main_window._show_message("无法导出图片", str(exc))
            return False
        except Exception as exc:  # noqa: BLE001
            self.main_window._show_message(
                "无法导出图片", f"{type(exc).__name__}: {exc}"
            )
            return False
        try:
            self._adopt_snapshot(snapshot)
        except Exception as exc:  # noqa: BLE001
            self.main_window._show_message(
                "无法生成预览", f"{type(exc).__name__}: {exc}"
            )
            return False
        self.show()
        self.raise_()
        self.activateWindow()
        self._stale_timer.start()
        # 预览图生成于 show 之前，viewport 尺寸尚不可用；布局完成后重排大图
        QTimer.singleShot(0, self._refresh_big_preview)
        return True

    def _adopt_snapshot(self, snapshot):
        self.snapshot = snapshot
        self._preview_cache.clear()
        self._big_ready = False
        family = snapshot.view_family

        self.family_badge.setText(f"当前：{FAMILY_LABELS.get(family, family)}")
        self.source_label.setText(f"来源：{snapshot.source_desc}（{snapshot.view} 结果）")

        style, committed = committed_style_for(self.main_window.settings, family)
        self._committed_overrides = {
            s.style_id: committed if s.style_id == style.style_id else self._load_committed(s.style_id)
            for s in styles_for_family(family)
        }
        self._draft_style_id = style.style_id
        self._draft_overrides = dict(self._committed_overrides.get(style.style_id, {}))

        self._rebuild_cards()
        self._load_output_options_ui()
        self._sync_tune_ui_from_draft()
        self._regenerate_previews()

    def _load_committed(self, style_id: str) -> Dict[str, Any]:
        from publication_models import load_overrides

        return load_overrides(self.main_window.settings, self.snapshot.view_family, style_id)

    def _rebuild_cards(self):
        for card in self.cards.values():
            self.card_group.removeButton(card)
            card.setParent(None)
            card.deleteLater()
        self.cards = {}
        while self.cards_row.count():
            item = self.cards_row.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for style in styles_for_family(self.snapshot.view_family):
            card = _StyleCard(style, self)
            card.set_tuned(bool(self._committed_overrides.get(style.style_id)))
            card.clicked.connect(lambda _=False, sid=style.style_id: self._on_card_clicked(sid))
            self.card_group.addButton(card)
            self.cards[style.style_id] = card
            self.cards_row.addWidget(card)
        self.cards_row.addStretch(1)
        self.cards[self._draft_style_id].setChecked(True)
        self._update_tune_availability()

    # ------------------------------------------------------------- 微调草稿

    def _update_tune_availability(self):
        family = self.snapshot.view_family
        has_cbar = family in ("2d", "3d")
        for row in (self.cbar_row, self.cbar_row2):
            for i in range(row.count()):
                w = row.itemAt(i).widget()
                if w is not None:
                    w.setEnabled(has_cbar)
        self.combo_frame.setVisible(family in ("1d", "2d"))
        self.frame_row.itemAt(0).widget().setVisible(family in ("1d", "2d"))
        self.chk_box.setVisible(family == "3d")
        self.chk_grid.setVisible(family == "3d")
        self.label_body_size.setVisible(family == "3d")
        self.spin_body_size.setVisible(family == "3d")
        self.chk_title.setVisible(family in ("1d", "2d"))

    def _sync_tune_ui_from_draft(self):
        ov = self._draft_overrides
        style = self._current_style()
        params = style.params
        blockers = [
            (self.chk_cbar,), (self.combo_cbar_pos,), (self.combo_cbar_tick,),
            (self.spin_cbar_nticks,), (self.spin_cbar_len,), (self.chk_cbar_outline,),
            (self.combo_frame,),
            (self.chk_box,), (self.chk_grid,), (self.chk_title,), (self.edit_panel_label,),
            (self.spin_body_size,),
            (self.spin_cbar_thick,), (self.spin_cbar_cx,), (self.spin_cbar_cy,),
        ]
        for (w,) in blockers:
            w.blockSignals(True)
        try:
            self.chk_cbar.setChecked(bool(ov.get("colorbar_visible", True)))
            pos = ov.get("colorbar_position", params.get("colorbar_position", "right"))
            self.combo_cbar_pos.setCurrentIndex(
                {"right": 0, "left": 1, "top": 2, "bottom": 3}.get(pos, 0)
            )
            mode = ov.get("colorbar_tick_mode", "values")
            self.combo_cbar_tick.setCurrentIndex(
                {"values": 0, "endpoints": 1, "lowhigh": 2, "none": 3}.get(mode, 0)
            )
            self.spin_cbar_nticks.setValue(
                int(ov.get("colorbar_nticks", params.get("colorbar_ticks", 3)))
            )
            self.chk_cbar_outline.setChecked(
                bool(ov.get("colorbar_outline", params.get("colorbar_outline", False)))
            )
            self.spin_cbar_len.setValue(int(ov.get("colorbar_length", 100)))
            self.spin_cbar_thick.setValue(float(ov.get(
                "colorbar_thickness", params.get("colorbar_thickness_mm", 2.2)
            )))
            # 中心位置：覆盖优先；否则以当前布局下色带的默认中心为初始值
            self._center_dirty = "colorbar_cx" in ov or "colorbar_cy" in ov
            center = self._compute_cbar_default_center(ov)
            if center is not None:
                self.spin_cbar_cx.setValue(
                    int(round(float(ov.get("colorbar_cx", center[0]))))
                )
                self.spin_cbar_cy.setValue(
                    int(round(float(ov.get("colorbar_cy", center[1]))))
                )
            frame = ov.get("frame_mode", params.get("frame_mode", "open"))
            self.combo_frame.setCurrentIndex(0 if frame == "box" else 1)
            self.chk_box.setChecked(bool(ov.get("show_box", params.get("show_box", False))))
            self.chk_grid.setChecked(bool(ov.get("show_grid", params.get("show_grid", False))))
            self.chk_title.setChecked(bool(ov.get("show_title", True)))
            self.edit_panel_label.setText(str(ov.get("panel_label", "")))
            self.spin_body_size.setValue(int(ov.get("body_size", 100)))
        finally:
            for (w,) in blockers:
                w.blockSignals(False)
        # 草稿重建（换快照/换样式/恢复默认）时清空各位置的几何记忆
        self._cbar_pos_memory.clear()
        self._cbar_pos_current = self._current_cbar_position()

    def _collect_overrides_from_ui(self) -> Dict[str, Any]:
        family = self.snapshot.view_family
        ov: Dict[str, Any] = {}
        if family in ("2d", "3d"):
            if not self.chk_cbar.isChecked():
                ov["colorbar_visible"] = False
            pos = {0: "right", 1: "left", 2: "top", 3: "bottom"}[self.combo_cbar_pos.currentIndex()]
            default_pos = self._current_style().params.get("colorbar_position", "right")
            if pos != default_pos:
                ov["colorbar_position"] = pos
            mode = {0: "values", 1: "endpoints", 2: "lowhigh", 3: "none"}[self.combo_cbar_tick.currentIndex()]
            if mode != "values":
                ov["colorbar_tick_mode"] = mode
            nticks = int(self.spin_cbar_nticks.value())
            if nticks != int(self._current_style().params.get("colorbar_ticks", 3)):
                ov["colorbar_nticks"] = nticks
            outline = self.chk_cbar_outline.isChecked()
            if outline != bool(self._current_style().params.get("colorbar_outline", False)):
                ov["colorbar_outline"] = outline
            cbar_len = int(self.spin_cbar_len.value())
            if cbar_len != 100:
                ov["colorbar_length"] = cbar_len
            thick = float(self.spin_cbar_thick.value())
            default_thick = float(self._current_style().params.get("colorbar_thickness_mm", 2.2))
            if abs(thick - default_thick) > 1e-6:
                ov["colorbar_thickness"] = thick
            # 中心位置：与当前布局默认中心不同的轴才存为覆盖
            center = self._compute_cbar_default_center(ov)
            if center is not None:
                if int(self.spin_cbar_cx.value()) != int(round(center[0])):
                    ov["colorbar_cx"] = int(self.spin_cbar_cx.value())
                if int(self.spin_cbar_cy.value()) != int(round(center[1])):
                    ov["colorbar_cy"] = int(self.spin_cbar_cy.value())
        if family in ("1d", "2d"):
            frame = "box" if self.combo_frame.currentIndex() == 0 else "open"
            if frame != self._current_style().params.get("frame_mode", "open"):
                ov["frame_mode"] = frame
        if family == "3d":
            if self.chk_box.isChecked() != bool(self._current_style().params.get("show_box", False)):
                ov["show_box"] = self.chk_box.isChecked()
            if self.chk_grid.isChecked() != bool(self._current_style().params.get("show_grid", False)):
                ov["show_grid"] = self.chk_grid.isChecked()
            body_size = int(self.spin_body_size.value())
            if body_size != 100:
                ov["body_size"] = body_size
        if family in ("1d", "2d") and not self.chk_title.isChecked():
            ov["show_title"] = False
        panel = self.edit_panel_label.text().strip()
        if panel:
            ov["panel_label"] = panel
        return validate_overrides(family, ov)

    def _current_style(self):
        from publication_models import resolve_style

        return resolve_style(self.snapshot.view_family, self._draft_style_id)

    def _compute_cbar_default_center(self, overrides):
        """按当前样式与输出尺寸计算色带默认中心（画布 %）；无色带返回 None。"""
        if self.snapshot is None:
            return None
        family = self.snapshot.view_family
        if family not in ("2d", "3d"):
            return None
        clean = {
            k: v for k, v in (overrides or {}).items()
            if k not in ("colorbar_cx", "colorbar_cy")
        }
        try:
            options = self._collect_output_options()
            return default_colorbar_center(
                self._current_style().params, clean, family,
                float(options.width_mm), options.resolved_height_mm(family),
            )
        except Exception:  # noqa: BLE001 - 布局失败时不刷新中心初值
            return None

    def _refresh_center_defaults_if_untouched(self):
        """用户未手动指定中心时，X/Y 初值跟随布局默认（位置/长度/画布变化）。"""
        if self._center_dirty or self.snapshot is None:
            return
        ov = {
            k: v for k, v in self._collect_overrides_from_ui().items()
            if k not in ("colorbar_cx", "colorbar_cy")
        }
        center = self._compute_cbar_default_center(ov)
        if center is None:
            return
        self.spin_cbar_cx.blockSignals(True)
        self.spin_cbar_cy.blockSignals(True)
        try:
            self.spin_cbar_cx.setValue(int(round(center[0])))
            self.spin_cbar_cy.setValue(int(round(center[1])))
        finally:
            self.spin_cbar_cx.blockSignals(False)
            self.spin_cbar_cy.blockSignals(False)

    def _on_center_changed(self, *_args):
        self._center_dirty = True
        self._on_tune_changed()

    # ------------------------------------------------- 色带几何：按位置记忆

    def _current_cbar_position(self) -> str:
        return {0: "right", 1: "left", 2: "top", 3: "bottom"}[
            self.combo_cbar_pos.currentIndex()
        ]

    def _default_center_for_position(self, position: str, geometry_overrides):
        """指定位置 + 几何参数下的色带默认中心（画布 %）。"""
        ov = dict(geometry_overrides or {})
        ov["colorbar_position"] = position
        return self._compute_cbar_default_center(ov)

    def _stash_cbar_geometry(self, position: str):
        """把当前 UI 上的色带几何参数（长度/厚度/中心）收存到该位置名下。"""
        if self.snapshot is None:
            return
        entry: Dict[str, Any] = {}
        length = int(self.spin_cbar_len.value())
        if length != 100:
            entry["colorbar_length"] = length
        thick = float(self.spin_cbar_thick.value())
        default_thick = float(
            self._current_style().params.get("colorbar_thickness_mm", 2.2)
        )
        if abs(thick - default_thick) > 1e-6:
            entry["colorbar_thickness"] = thick
        center = self._default_center_for_position(position, entry)
        if center is not None:
            cxv = int(self.spin_cbar_cx.value())
            cyv = int(self.spin_cbar_cy.value())
            if cxv != int(round(center[0])):
                entry["colorbar_cx"] = cxv
            if cyv != int(round(center[1])):
                entry["colorbar_cy"] = cyv
        self._cbar_pos_memory[position] = entry

    def _apply_cbar_geometry(self, position: str):
        """把该位置记忆的几何参数（或默认值）写入 UI。"""
        entry = self._cbar_pos_memory.get(position, {})
        params = self._current_style().params
        spins = (
            self.spin_cbar_len, self.spin_cbar_thick,
            self.spin_cbar_cx, self.spin_cbar_cy,
        )
        for w in spins:
            w.blockSignals(True)
        try:
            self.spin_cbar_len.setValue(int(entry.get("colorbar_length", 100)))
            self.spin_cbar_thick.setValue(float(entry.get(
                "colorbar_thickness", params.get("colorbar_thickness_mm", 2.2)
            )))
            center = self._default_center_for_position(position, entry)
            if center is not None:
                self.spin_cbar_cx.setValue(
                    int(round(float(entry.get("colorbar_cx", center[0]))))
                )
                self.spin_cbar_cy.setValue(
                    int(round(float(entry.get("colorbar_cy", center[1]))))
                )
            self._center_dirty = "colorbar_cx" in entry or "colorbar_cy" in entry
        finally:
            for w in spins:
                w.blockSignals(False)

    def _on_cbar_position_changed(self, *_args):
        """切换色带位置：收存旧位置的一套几何参数，换入新位置的一套。"""
        if self.snapshot is None:
            return
        old = self._cbar_pos_current
        new = self._current_cbar_position()
        if old != new:
            self._stash_cbar_geometry(old)
            self._apply_cbar_geometry(new)
            self._cbar_pos_current = new
        self._on_tune_changed()

    def _on_card_clicked(self, style_id: str):
        if style_id == self._draft_style_id:
            return
        # 切换样式不带入上一样式的覆盖参数（plan §4.1）
        self._draft_style_id = style_id
        self._draft_overrides = dict(self._committed_overrides.get(style_id, {}))
        self._sync_tune_ui_from_draft()
        self._regenerate_previews()

    def _on_tune_changed(self, *_args):
        if self.snapshot is None:
            return
        self._refresh_center_defaults_if_untouched()
        self._draft_overrides = self._collect_overrides_from_ui()
        self._debounce.start()

    def _reset_style_overrides(self):
        self._draft_overrides = {}
        self._sync_tune_ui_from_draft()
        self._debounce.start()

    def _toggle_tune_panel(self, checked):
        self.tune_panel.setVisible(checked)
        self.tune_toggle.setText("微调（选中样式） ▴" if checked else "微调（选中样式） ▾")

    # ------------------------------------------------------------- 输出偏好

    def _load_output_options_ui(self):
        options = output_options_for(self.main_window.settings, self.snapshot.view_family)
        widgets = [
            self.combo_width, self.spin_width, self.spin_height,
            self.combo_dpi, self.spin_dpi, self.combo_format,
        ]
        for w in widgets:
            w.blockSignals(True)
        try:
            width = float(options.width_mm)
            if abs(width - 89.0) < 1e-6:
                self.combo_width.setCurrentIndex(0)
                self.spin_width.setVisible(False)
            elif abs(width - 183.0) < 1e-6:
                self.combo_width.setCurrentIndex(1)
                self.spin_width.setVisible(False)
            else:
                self.combo_width.setCurrentIndex(2)
                self.spin_width.setVisible(True)
            self.spin_width.setValue(width)
            self.spin_height.setValue(options.resolved_height_mm(self.snapshot.view_family))
            if int(options.dpi) == 300:
                self.combo_dpi.setCurrentIndex(0)
                self.spin_dpi.setVisible(False)
            elif int(options.dpi) == 600:
                self.combo_dpi.setCurrentIndex(1)
                self.spin_dpi.setVisible(False)
            else:
                self.combo_dpi.setCurrentIndex(2)
                self.spin_dpi.setVisible(True)
            self.spin_dpi.setValue(int(options.dpi))

            self.combo_format.clear()
            formats = FAMILY_FORMATS.get(self.snapshot.view_family, ("png",))
            self.combo_format.addItem("PNG")
            pdf_index = self.combo_format.count()
            self.combo_format.addItem("PDF")
            model = self.combo_format.model()
            item = model.item(pdf_index)
            if "pdf" not in formats:
                item.setEnabled(False)
                item.setToolTip("3D 视图首版仅支持 PNG（TIFF/混合 PDF 属后续增强）")
            self.combo_format.setCurrentIndex(1 if options.fmt == "pdf" and "pdf" in formats else 0)
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _collect_output_options(self) -> OutputOptions:
        width_idx = self.combo_width.currentIndex()
        width = 89.0 if width_idx == 0 else (183.0 if width_idx == 1 else float(self.spin_width.value()))
        dpi_idx = self.combo_dpi.currentIndex()
        dpi = 300 if dpi_idx == 0 else (600 if dpi_idx == 1 else int(self.spin_dpi.value()))
        fmt = "pdf" if self.combo_format.currentIndex() == 1 else "png"
        formats = FAMILY_FORMATS.get(self.snapshot.view_family, ("png",))
        if fmt not in formats:
            fmt = "png"
        return OutputOptions(
            width_mm=float(width),
            height_mm=float(self.spin_height.value()),
            dpi=int(dpi),
            fmt=fmt,
        )

    def _on_output_changed(self, *_args):
        if self.snapshot is None:
            return
        # 画布尺寸变化会移动色带默认中心：未手动指定时刷新 X/Y 初值
        self._refresh_center_defaults_if_untouched()
        custom_width = self.combo_width.currentIndex() == 2
        custom_dpi = self.combo_dpi.currentIndex() == 2
        self.spin_width.setVisible(custom_width)
        self.spin_dpi.setVisible(custom_dpi)
        if self.combo_width.currentIndex() == 0:
            self.spin_width.setValue(89.0)
        elif self.combo_width.currentIndex() == 1:
            self.spin_width.setValue(183.0)
        if self.combo_dpi.currentIndex() == 0:
            self.spin_dpi.setValue(300)
        elif self.combo_dpi.currentIndex() == 1:
            self.spin_dpi.setValue(600)
        # 尺寸/DPI/格式是独立输出偏好，立即持久化；恢复样式默认不重置它们
        persist_output_options(self.main_window.settings, self._collect_output_options())
        self._debounce.start()

    # ------------------------------------------------------------- 预览生成

    def _preview_key(self, style_id: str, dpi: int, overrides=None) -> Tuple:
        ov = self._draft_overrides if overrides is None else overrides
        options = self._collect_output_options()
        return (
            self.snapshot.snapshot_id,
            style_id,
            overrides_signature(ov),
            options.signature(self.snapshot.view_family),
            int(dpi),
        )

    def _cache_put(self, key, payload: bytes):
        if len(self._preview_cache) >= CACHE_LIMIT:
            self._preview_cache.pop(next(iter(self._preview_cache)))
        self._preview_cache[key] = payload

    def _regenerate_previews(self):
        if self.snapshot is None:
            return
        if self._busy_3d:
            self._pending_regenerate = True
            return
        self._preview_revision += 1
        self._big_ready = False
        self.btn_export.setEnabled(False)
        options = self._collect_output_options()
        family = self.snapshot.view_family
        styles = styles_for_family(family)
        order = [self._draft_style_id] + [
            s.style_id for s in styles if s.style_id != self._draft_style_id
        ]
        self.status_label.setText("正在生成预览…")

        if family == "3d":
            self._render_3d_preview_batch(order, styles, options)
            return

        for style_id in order:
            style = next(s for s in styles if s.style_id == style_id)
            overrides = (
                dict(self._draft_overrides)
                if style_id == self._draft_style_id
                else dict(self._committed_overrides.get(style_id, {}))
            )
            for tier_dpi, is_large in ((CARD_DPI, False), (LARGE_DPI, True)):
                if is_large and style_id != self._draft_style_id:
                    continue  # 大图只生成选中样式
                key = self._preview_key(style_id, tier_dpi, overrides)
                if key in self._preview_cache:
                    self._apply_preview(style_id, tier_dpi, self._preview_cache[key])
                    continue
                task = _PreviewTask(
                    key, self._preview_revision, self.snapshot, style,
                    overrides, options, tier_dpi,
                )
                task.signals.done.connect(self._on_preview_done)
                task.signals.failed.connect(self._on_preview_failed)
                self._pool.start(task)

    def _render_3d_preview_batch(self, order, styles, options):
        """3D 预览在 GUI 线程串行渲染（独立 off-screen 场景）。"""
        self._busy_3d = True
        self._pending_regenerate = False
        revision = self._preview_revision
        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        try:
            for style_id in order:
                if revision != self._preview_revision:
                    break  # 期间参数已变化，放弃旧批次
                style = next(s for s in styles if s.style_id == style_id)
                overrides = (
                    dict(self._draft_overrides)
                    if style_id == self._draft_style_id
                    else dict(self._committed_overrides.get(style_id, {}))
                )
                for tier_dpi, is_large in ((CARD_DPI, False), (LARGE_DPI, True)):
                    if is_large and style_id != self._draft_style_id:
                        continue
                    key = self._preview_key(style_id, tier_dpi, overrides)
                    if key in self._preview_cache:
                        self._apply_preview(style_id, tier_dpi, self._preview_cache[key])
                        continue
                    try:
                        fig = render_snapshot(
                            self.snapshot, style, overrides, options, dpi=tier_dpi
                        )
                        try:
                            buffer = io.BytesIO()
                            fig.savefig(
                                buffer, format="png",
                                dpi=int(fig.get_dpi()), facecolor="white",
                            )
                            payload = buffer.getvalue()
                        finally:
                            fig.clear()
                        self._cache_put(key, payload)
                        if revision == self._preview_revision:
                            self._apply_preview(style_id, tier_dpi, payload)
                    except Exception as exc:  # noqa: BLE001
                        if revision == self._preview_revision:
                            self.status_label.setText(f"预览失败：{exc}")
                QApplication.processEvents()
        finally:
            QApplication.restoreOverrideCursor()
            self._busy_3d = False
        if self._pending_regenerate:
            self._pending_regenerate = False
            self._regenerate_previews()

    @pyqtSlot(object, int, bytes)
    def _on_preview_done(self, key, revision, payload):
        if self.snapshot is None or key[0] != self.snapshot.snapshot_id:
            return  # 过期快照结果丢弃
        if revision != self._preview_revision:
            return  # 过期预览结果丢弃
        self._cache_put(key, payload)
        self._apply_preview(key[1], key[4], payload)

    @pyqtSlot(object, int, str)
    def _on_preview_failed(self, key, revision, message):
        if self.snapshot is None or key[0] != self.snapshot.snapshot_id:
            return
        if revision != self._preview_revision:
            return
        self.status_label.setText(f"预览失败：{message}")

    def _apply_preview(self, style_id: str, dpi: int, payload: bytes):
        pixmap = QPixmap()
        pixmap.loadFromData(payload)
        if pixmap.isNull():
            return
        if dpi == CARD_DPI and style_id in self.cards:
            card_w = self.cards[style_id].width() - 20
            scaled = pixmap.scaledToWidth(card_w, Qt.SmoothTransformation)
            self.cards[style_id].set_preview(scaled)
        if dpi == LARGE_DPI and style_id == self._draft_style_id:
            self._set_big_preview(pixmap)
            self._big_ready = True
            self.btn_export.setEnabled(True)
            style_name = self._current_style().full_name
            self.status_label.setText(f"预览就绪：{style_name}（与导出文件同一排版）")

    def _set_big_preview(self, pixmap: QPixmap):
        self._big_pixmap = pixmap
        self._refresh_big_preview()

    def _refresh_big_preview(self):
        pixmap = getattr(self, "_big_pixmap", None)
        if pixmap is None:
            return
        if self._fit_to_window:
            view = self.preview_scroll.viewport().size()
            if view.width() < 60 or view.height() < 60:
                # 布局尚未完成（面板未 show 时 viewport 极小）：延迟重试
                if self.isVisible():
                    QTimer.singleShot(60, self._refresh_big_preview)
                return
            scaled = pixmap.scaled(
                view, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.preview_label.setPixmap(scaled)
        else:
            self.preview_label.setPixmap(pixmap)
        self.preview_label.adjustSize()

    def _toggle_fit(self):
        self._fit_to_window = not self._fit_to_window
        self.fit_toggle.setText("适应窗口" if not self._fit_to_window else "100% 像素")
        self._refresh_big_preview()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_to_window:
            self._refresh_big_preview()

    # ------------------------------------------------------------- 过期提示

    def _current_main_state_token(self):
        window = self.main_window
        spec = window.left_workspace.current_spec()
        page_id = spec.page_id if spec is not None else None
        t_idx = None
        if window.core.raw_data is not None and window.core.has_time_axis:
            try:
                t_idx = int(window.timeline_bar.slider_time.value())
            except Exception:
                t_idx = None
        return page_id, t_idx

    def _snapshot_state_token(self):
        if self.snapshot is None:
            return None, None
        return self.snapshot.source_page_id, self.snapshot.home_frame_index

    def _check_stale_source(self):
        try:
            if self.snapshot is None or not self.isVisible():
                return
            current = self._current_main_state_token()
            frozen = self._snapshot_state_token()
            if current != frozen:
                self.source_label.setText(
                    f"来源：{self.snapshot.source_desc}（预览来自之前的页面/帧，"
                    "导出仍输出该快照；点击“更新预览”换取当前状态）"
                )
            else:
                self.source_label.setText(
                    f"来源：{self.snapshot.source_desc}（{self.snapshot.view} 结果）"
                )
        except Exception:  # noqa: BLE001 - 定时器槽绝不抛出
            return

    # ------------------------------------------------------------- 动作

    def _on_update_preview(self):
        """重新冻结当前主页面科学状态，并按新视图族加载样式库。"""
        try:
            snapshot = capture_snapshot(self.main_window)
        except ExportError as exc:
            self.main_window._show_message("无法导出图片", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - 槽函数绝不允许异常逃逸
            self.main_window._show_message(
                "无法导出图片", f"{type(exc).__name__}: {exc}"
            )
            return
        try:
            self._adopt_snapshot(snapshot)
        except Exception as exc:  # noqa: BLE001
            self.main_window._show_message(
                "无法生成预览", f"{type(exc).__name__}: {exc}"
            )

    def _on_export(self):
        """导出面板冻结的快照；写入成功后提交该视图族偏好。"""
        if self.snapshot is None or not self._big_ready:
            return  # 正式预览未就绪时不可导出
        style = self._current_style()
        options = self._collect_output_options()
        filters = "PNG (*.png)" if options.fmt == "png" else "PDF (*.pdf)"
        default_name = default_filename(self.snapshot, style, options)
        path, selected = QFileDialog.getSaveFileName(
            self, "导出图片", default_name, filters
        )
        if not path:
            return  # 取消路径选择：保留草稿，不提交偏好
        path = self.main_window._sanitize_save_path(path, selected)

        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        self.btn_export.setEnabled(False)
        self.status_label.setText("正在渲染并写入文件…")
        QApplication.processEvents()
        try:
            render_and_save(
                self.snapshot, style, self._draft_overrides, options, path
            )
        except (ExportError, RenderError, OSError, ValueError) as exc:
            self.status_label.setText(f"导出失败：{exc}")
            self.main_window._show_message("导出失败", str(exc))
            return  # 不提交草稿，不提示成功
        except Exception as exc:  # noqa: BLE001 - 槽函数绝不允许异常逃逸
            self.status_label.setText(f"导出失败：{type(exc).__name__}: {exc}")
            self.main_window._show_message(
                "导出失败", f"{type(exc).__name__}: {exc}"
            )
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.btn_export.setEnabled(self._big_ready)

        commit_style(
            self.main_window.settings,
            self.snapshot.view_family,
            style.style_id,
            self._draft_overrides,
        )
        self._cbar_pos_memory.clear()  # 导出成功后清空各位置几何记忆
        self._committed_overrides[style.style_id] = dict(self._draft_overrides)
        self.cards[style.style_id].set_tuned(bool(self._draft_overrides))
        persist_output_options(self.main_window.settings, options)
        self.main_window._update_screenshot_tooltip()
        self.status_label.setText(f"已导出：{path}")
        self.main_window._toast_success("图片已导出", path)

    # ------------------------------------------------------------- 关闭

    def closeEvent(self, event):
        self._stale_timer.stop()
        self._preview_revision += 1  # 使在途结果失效（协作式取消）
        self._pool.clear()
        self._cbar_pos_memory.clear()  # 面板关闭即清空各位置几何记忆
        super().closeEvent(event)
