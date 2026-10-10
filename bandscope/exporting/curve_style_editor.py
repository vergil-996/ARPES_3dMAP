"""截图窗口中的 1D 编辑器：逐曲线覆盖、整图外观、预设与撤销。"""
from __future__ import annotations

import copy

import numpy as np
from PyQt5.QtCore import Qt, QSignalBlocker, pyqtSignal
from PyQt5.QtGui import QColor, QKeySequence, QIcon, QPixmap, QPainter, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QShortcut, QSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from bandscope.exporting.curve_presentation import (
    Figure1DPresentation, _color, _patch, CURVE_SCHEMA, FIGURE_SCHEMA,
    load_presets, save_presets, ordered_curves, snapshot_curves,
)
from bandscope.exporting.curve_renderer import curve_style
import bandscope.ui.theme as theme


class CurveStyleEditor(QWidget):
    changed = pyqtSignal()
    validityChanged = pyqtSignal(bool, str)
    presetRequested = pyqtSignal(object)
    externalRestored = pyqtSignal(str, object)
    resetAllRequested = pyqtSignal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.snapshot = None
        self.presentation = Figure1DPresentation()
        self.style = None
        self.legacy_overrides = lambda: {}
        self._syncing = False
        self._last = self.presentation.to_dict()
        self._undo, self._redo = [], []
        self._errors = {}
        self.setStyleSheet(
            f"QWidget#curve-style-page {{ background: {theme.BG_2}; }}"
            f"QTabWidget::pane {{ border: 1px solid {theme.BORDER_HEX}; background: {theme.BG_2}; }}"
            f"QTabBar::tab {{ background: {theme.BG_2}; color: {theme.TEXT_2}; padding: 5px 8px; }}"
            f"QTabBar::tab:selected {{ background: {theme.BG_3}; color: {theme.ACCENT}; }}"
            f"QScrollArea {{ border: none; background: {theme.BG_2}; }}"
            f"QTableWidget, QListWidget {{ background: {theme.BG_1}; color: {theme.TEXT_1};"
            f" border: 1px solid {theme.BORDER_HEX}; selection-background-color: {theme.ACCENT_SOFT};"
            f" selection-color: {theme.TEXT_1}; }}"
            f"QHeaderView::section {{ background: {theme.BG_3}; color: {theme.TEXT_1}; padding: 4px;"
            f" border: 1px solid {theme.BORDER_HEX}; }}"
            f"QPushButton {{ background: {theme.BG_3}; color: {theme.TEXT_1}; padding: 4px;"
            f" border: 1px solid {theme.BORDER_HEX}; border-radius: 4px; }}"
            f"QPushButton:hover {{ border-color: {theme.ACCENT}; }}"
            f"QPushButton:disabled {{ color: {theme.TEXT_3}; }}"
        )
        self.fields = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        actions = QHBoxLayout()
        self.btn_undo = QPushButton("撤销")
        self.btn_redo = QPushButton("重做")
        self.btn_undo.clicked.connect(self.undo)
        self.btn_redo.clicked.connect(self.redo)
        actions.addWidget(self.btn_undo)
        actions.addWidget(self.btn_redo)
        actions.addStretch()
        root.addLayout(actions)
        for key, callback in (("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.hint = QLabel("多选可批量设置；只覆盖修改的字段。重置曲线可恢复继承。")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)
        self._build_curves()
        self._build_legend()
        self._build_axes()
        self._build_layout()
        self._build_waterfall()
        self._build_annotations()
        self._build_presets()
        common = self._tab("标题 / 字体")
        self.common_form = QFormLayout()
        common.addLayout(self.common_form)
        self._common_controls = []
        self._common_visible = False

    def _tab(self, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        container.setObjectName("curve-style-page")
        layout = QVBoxLayout(container)
        scroll.setWidget(container)
        self.tabs.addTab(scroll, title)
        return layout

    def _field(self, form, target, key, label, default, choices=None, suffix=""):
        schema = CURVE_SCHEMA if target == "curve" else FIGURE_SCHEMA
        rule = schema[key]
        if choices:
            widget = QComboBox()
            for title, value in choices:
                widget.addItem(title, value)
            widget.currentIndexChanged.connect(lambda _=0, t=target, k=key, w=widget: self._field_changed(t, k, w.currentData()))
        elif rule == "bool":
            widget = QCheckBox()
            widget.setTristate(target == "curve")
            widget.stateChanged.connect(lambda v, t=target, k=key: self._field_changed(t, k, v == Qt.Checked) if v != Qt.PartiallyChecked else None)
        elif isinstance(rule, tuple) and rule[0] in ("int", "float"):
            widget = QSpinBox() if rule[0] == "int" else QDoubleSpinBox()
            widget.setRange(rule[1], rule[2])
            if target == "curve":
                widget.setMinimum(0)
                widget.setSpecialValueText("混合")
            if rule[0] == "float":
                widget.setDecimals(2)
                widget.setSingleStep(0.1)
            widget.setSuffix(suffix)
            widget.setKeyboardTracking(False)
            widget.valueChanged.connect(lambda v, t=target, k=key: self._field_changed(t, k, v))
        else:
            widget = QLineEdit()
            widget.setPlaceholderText("自动，或 下限,上限" if rule == "range" else "#RRGGBB" if rule == "color" else "留空不显示")
            widget.editingFinished.connect(lambda t=target, k=key, w=widget: self._text_changed(t, k, w))
        self.fields[target, key] = (widget, default)
        if rule == "color":
            row = QHBoxLayout()
            row.addWidget(widget)
            button = QPushButton("选色")
            button.clicked.connect(lambda _=False, t=target, k=key, w=widget: self._choose_color(t, k, w))
            row.addWidget(button)
            form.addRow(label, row)
        else:
            form.addRow(label, widget)
        return widget

    def _build_curves(self):
        layout = self._tab("曲线")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["显示", "曲线 / 基准", "来源", "样式"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFixedHeight(140)
        self.table.itemSelectionChanged.connect(self._sync_fields)
        self.table.itemChanged.connect(self._table_changed)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for text, callback in (("上移", lambda: self._move(-1)), ("下移", lambda: self._move(1)),
                               ("恢复所选曲线", self.reset_selected), ("恢复整图默认", self.reset_all)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        defaults = QPushButton("将所选曲线外观设为整图默认")
        defaults.clicked.connect(self._selected_as_defaults)
        layout.addWidget(defaults)
        form = QFormLayout()
        layout.addLayout(form)
        self._field(form, "curve", "label", "图例名称", "")
        self._field(form, "curve", "legend", "加入图例", True)
        self._field(form, "curve", "color", "颜色", "#000000")
        self._field(form, "curve", "linestyle", "线型", "-", [("实线", "-"), ("虚线", "--"), ("点划线", "-."), ("点线", ":"), ("仅标记", "none")])
        self._field(form, "curve", "linewidth", "线宽", 0.8, suffix=" pt")
        self._field(form, "curve", "alpha", "透明度", 1)
        self._field(form, "curve", "marker", "标记", "", [("无", ""), ("圆", "o"), ("方", "s"), ("上三角", "^"), ("下三角", "v"), ("菱形", "D"), ("十字", "+"), ("叉", "x"), ("点", ".")])
        self._field(form, "curve", "markersize", "标记大小", 3, suffix=" pt")
        self._field(form, "curve", "marker_filled", "实心标记", True)
        self._field(form, "curve", "markevery", "每 N 个样本显示标记", 1)

    def _build_legend(self):
        layout = self._tab("图例")
        form = QFormLayout()
        layout.addLayout(form)
        positions = [("跟随模板", "template"), ("自动避让", "auto"), ("不显示", "none"),
                     ("右上", "upper right"), ("左上", "upper left"), ("右下", "lower right"),
                     ("左下", "lower left"), ("居中", "center"), ("图外顶部", "outside_top"),
                     ("图外底部", "outside_bottom"), ("图外左侧", "outside_left"), ("图外右侧", "outside_right")]
        self._field(form, "figure", "legend_position", "位置", "template", positions)
        for key, label, value in (("legend_columns", "列数", 1), ("legend_size", "字号 (pt)", 6),
                                  ("legend_frame", "边框", False), ("legend_background", "背景色", "#FFFFFF"),
                                  ("legend_alpha", "背景不透明度", 0.8), ("legend_spacing", "条目间距", 0.5),
                                  ("legend_handlelength", "线段长度", 2), ("legend_wrap", "换行字符数", 32)):
            self._field(form, "figure", key, label, value)
        layout.addWidget(QLabel("拖动条目调整图例顺序；曲线绘制顺序保持独立。"))
        self.legend_list = QListWidget()
        self.legend_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.legend_list.model().rowsMoved.connect(self._legend_moved)
        layout.addWidget(self.legend_list)
        button = QPushButton("图例跟随曲线顺序")
        button.clicked.connect(self._reset_legend_order)
        layout.addWidget(button)

    def _build_axes(self):
        layout = self._tab("坐标")
        form = QFormLayout()
        layout.addLayout(form)
        for axis, title in (("x", "横轴"), ("y", "纵轴")):
            self._field(form, "figure", f"{axis}lim", f"{title}范围", None)
            self._field(form, "figure", f"{axis}_reverse", f"{title}反向", False)
            self._field(form, "figure", f"{axis}_ticks", f"{title}主刻度数量", 5)
            self._field(form, "figure", f"{axis}_minor", f"{title}次刻度", False)
            self._field(form, "figure", f"{axis}_ticklabels", f"{title}刻度文字", True)
        self._field(form, "figure", "tick_direction", "刻线方向", "out", [("朝内", "in"), ("朝外", "out"), ("双向", "inout")])
        for key, label, value in (("tick_color", "刻线颜色", "#000000"), ("tick_length", "主刻线长度 (pt)", 2),
                                  ("tick_width", "刻线宽度 (pt)", 0.6), ("major_grid", "主网格", False), ("minor_grid", "次网格", False)):
            self._field(form, "figure", key, label, value)

    def _build_layout(self):
        layout = self._tab("排版")
        form = QFormLayout()
        layout.addLayout(form)
        for key, label, value in (("axis_label_size", "轴名字号 (pt)", 7), ("tick_label_size", "刻度字号 (pt)", 6),
                                  ("title_size", "标题字号 (pt)", 7), ("panel_label_size", "面板编号字号 (pt)", 8),
                                  ("axes_linewidth", "坐标框线宽 (pt)", 0.6), ("labelpad", "轴名距离 (pt)", 2),
                                  ("ink_color", "文字 / 框线颜色", "#000000"), ("canvas_color", "画布颜色", "#FFFFFF"),
                                  ("margin_left_mm", "左留白 (mm)", 12), ("margin_right_mm", "右留白 (mm)", 7),
                                  ("margin_top_mm", "上留白 (mm)", 8), ("margin_bottom_mm", "下留白 (mm)", 9),
                                  ("base_emphasis", "默认基准加粗", True)):
            self._field(form, "figure", key, label, value)

    def _build_waterfall(self):
        layout = self._tab("配色 / 瀑布")
        form = QFormLayout()
        layout.addLayout(form)
        self._field(form, "figure", "palette", "整图配色", "legacy", [("原有配色", "legacy"), ("统一颜色", "uniform"),
                    ("分类配色", "categorical"), ("灰度线型", "gray"), ("序列渐变", "gradient")])
        self._field(form, "figure", "uniform_color", "统一颜色", "#000000")
        self._field(form, "figure", "waterfall_labels", "瀑布序列标签", "all", [("全部", "all"), ("关闭", "none"),
                    ("每 N 条", "every"), ("自动稀疏", "auto")])
        for key, label, value in (("waterfall_every", "每 N 条标签", 1), ("waterfall_label_size", "标签字号 (pt)", 6),
                                  ("waterfall_label_rotation", "旋转角度", 0), ("waterfall_label_gap", "标签距离 (pt)", 0),
                                  ("waterfall_label_color", "标签颜色", "#000000")):
            self._field(form, "figure", key, label, value)
        layout.addWidget(QLabel("逐曲线覆盖优先于整图配色。瀑布标签设置只影响瀑布图。"))

    def _build_annotations(self):
        layout = self._tab("注记")
        self.annotation_list = QListWidget()
        self.annotation_list.currentRowChanged.connect(self._sync_annotation)
        layout.addWidget(self.annotation_list)
        row = QHBoxLayout()
        self.annotation_kind = QComboBox()
        for title, value in (("文本", "text"), ("箭头", "arrow"), ("水平线", "hline"), ("垂直线", "vline"), ("水平区间", "hspan"), ("垂直区间", "vspan")):
            self.annotation_kind.addItem(title, value)
        row.addWidget(self.annotation_kind)
        add = QPushButton("添加")
        add.clicked.connect(self._add_annotation)
        remove = QPushButton("删除")
        remove.clicked.connect(self._remove_annotation)
        row.addWidget(add)
        row.addWidget(remove)
        layout.addLayout(row)
        form = QFormLayout()
        layout.addLayout(form)
        self.annotation_fields = {}
        for key, label, default in (("text", "文字", "注记"), ("coordinates", "坐标模式", "data"),
                                    ("x", "X / 区间起点", 0), ("y", "Y / 区间起点", 0),
                                    ("x2", "箭头文字 X / 区间终点", 1), ("y2", "箭头文字 Y / 区间终点", 1),
                                    ("color", "颜色 #RRGGBB", "#000000"), ("alpha", "不透明度", 1),
                                    ("size", "字号 (pt)", 7), ("linewidth", "线宽 (pt)", 0.6), ("linestyle", "线型", "--")):
            if key in ("coordinates", "linestyle"):
                widget = QComboBox()
                items = [("数据坐标", "data"), ("图内比例 (0–1)", "axes fraction")] if key == "coordinates" else [("实线", "-"), ("虚线", "--"), ("点划线", "-."), ("点线", ":")]
                for title, value in items:
                    widget.addItem(title, value)
                widget.currentIndexChanged.connect(self._annotation_changed)
            else:
                widget = QLineEdit()
                widget.editingFinished.connect(self._annotation_changed)
            self.annotation_fields[key] = (widget, default)
            form.addRow(label, widget)
        layout.addWidget(QLabel("箭头 X/Y 为指向位置，X2/Y2 为文字位置；参考线和区间使用数据坐标。"))

    def _build_presets(self):
        layout = self._tab("预设")
        self.preset_combo = QComboBox()
        layout.addWidget(self.preset_combo)
        for title, callback in (("应用预设", self._apply_preset), ("保存 / 覆盖预设", self._save_preset), ("删除预设", self._delete_preset)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            layout.addWidget(button)
        layout.addWidget(QLabel("保存模板、字体及通用外观。应用后保留当前曲线覆盖、轴范围和注记。"))
        layout.addStretch()
        self._reload_presets()

    def selected_ids(self):
        return [self.table.item(index.row(), 0).data(Qt.UserRole) for index in self.table.selectionModel().selectedRows()]

    def set_snapshot(self, snapshot, presentation, style):
        same_page = self.snapshot is not None and self.snapshot.source_page_id == snapshot.source_page_id
        self.snapshot, self.presentation, self.style = snapshot, presentation, style
        self._last = self._state()
        if not same_page:
            self._undo.clear()
            self._redo.clear()
        self._errors.clear()
        self.refresh()
        self.validityChanged.emit(True, "")

    def refresh(self):
        if self.snapshot is None:
            return
        self._syncing = True
        try:
            selected = set(self.selected_ids())
            blocker = QSignalBlocker(self.table)
            curves = ordered_curves(self.snapshot, self.presentation)
            self.table.setRowCount(len(curves))
            params = self.style.params
            for row, curve in enumerate(curves):
                config = curve_style(curve, self.presentation, params, self.snapshot.view == "waterfall",
                                     self.snapshot.payload.get("palette_span", len(curves)))
                checkbox = QTableWidgetItem()
                checkbox.setData(Qt.UserRole, curve.curve_id)
                checkbox.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                checkbox.setCheckState(Qt.Checked if config["visible"] else Qt.Unchecked)
                self.table.setItem(row, 0, checkbox)
                self.table.setItem(row, 1, QTableWidgetItem(("★ " if curve.is_base else "") + config["label"]))
                self.table.setItem(row, 2, QTableWidgetItem(curve.source))
                text = QTableWidgetItem(f"{config['linestyle']}  {config['linewidth']:g} pt  {config['marker']}")
                color = config["color"]
                if not isinstance(color, str):
                    color = QColor.fromRgbF(*color).name()
                swatch = QPixmap(28, 16)
                swatch.fill(Qt.white)
                painter = QPainter(swatch)
                painter.setPen(QPen(QColor(color), 3))
                painter.drawLine(2, 8, 26, 8)
                painter.end()
                text.setIcon(QIcon(swatch))
                self.table.setItem(row, 3, text)
            self.table.resizeColumnsToContents()
            self.table.clearSelection()
            selection_model = self.table.selectionModel()
            from PyQt5.QtCore import QItemSelectionModel
            for row, curve in enumerate(curves):
                if curve.curve_id in selected:
                    selection_model.select(self.table.model().index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
            if not self.selected_ids() and curves:
                self.table.selectRow(0)
            del blocker
            self.legend_list.blockSignals(True)
            self.legend_list.clear()
            rank = {v: i for i, v in enumerate(self.presentation.legend_order)}
            original_order = {c.curve_id: i for i, c in enumerate(curves)}
            for curve in sorted(curves, key=lambda c: rank.get(c.curve_id, len(rank) + original_order[c.curve_id])):
                item = QListWidgetItem(self.presentation.curves.get(curve.curve_id, {}).get("label", curve.label))
                item.setData(Qt.UserRole, curve.curve_id)
                self.legend_list.addItem(item)
            self.legend_list.blockSignals(False)
            old_row = self.annotation_list.currentRow()
            self.annotation_list.blockSignals(True)
            self.annotation_list.clear()
            for item in self.presentation.annotations:
                self.annotation_list.addItem(f"{item['kind']}  {item.get('text', '')}")
            self.annotation_list.setCurrentRow(min(max(0, old_row), len(self.presentation.annotations) - 1))
            self.annotation_list.blockSignals(False)
        finally:
            self._syncing = False
        self._sync_fields()
        self._sync_annotation()
        self.btn_undo.setEnabled(bool(self._undo))
        self.btn_redo.setEnabled(bool(self._redo))

    def _sync_fields(self):
        if self.snapshot is None or self._syncing:
            return
        self._syncing = True
        try:
            ids = set(self.selected_ids())
            curves = snapshot_curves(self.snapshot)
            selected = [curve_style(c, self.presentation, self.style.params, self.snapshot.view == "waterfall",
                                    self.snapshot.payload.get("palette_span", len(curves))) for c in curves if c.curve_id in ids]
            for (target, key), (widget, default) in self.fields.items():
                if (target, key) in self._errors:
                    continue
                widget.setStyleSheet("")
                blocker = QSignalBlocker(widget)
                if target == "curve":
                    values = [v[key] for v in selected]
                    value = values[0] if values and all(str(v) == str(values[0]) for v in values) else None
                    widget.setEnabled(bool(ids))
                else:
                    value = self.presentation.figure.get(key, self.style.params.get(key, default))
                if isinstance(widget, QComboBox):
                    widget.setCurrentIndex(widget.findData(value))
                elif isinstance(widget, QCheckBox):
                    widget.setCheckState(Qt.PartiallyChecked if value is None else Qt.Checked if value else Qt.Unchecked)
                elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    widget.setValue(value if isinstance(value, (float, int)) else widget.minimum())
                    widget.setToolTip("多选值混合；修改后统一应用" if value is None else "")
                elif isinstance(widget, QLineEdit):
                    if isinstance(value, (list, tuple)) and key.endswith("lim"):
                        value = f"{value[0]:g}, {value[1]:g}"
                    elif key == "color" and isinstance(value, (tuple, list)):
                        value = QColor.fromRgbF(*value).name()
                    elif not isinstance(value, (str, int, float)):
                        value = ""
                    widget.setText("" if value is None else str(value))
                    widget.setProperty("syncedText", widget.text())
                del blocker
        finally:
            self._syncing = False

    def _field_changed(self, target, key, value):
        if self._syncing or self.snapshot is None or value is None:
            return
        schema = CURVE_SCHEMA if target == "curve" else FIGURE_SCHEMA
        clean = _patch({key: value}, schema)
        if key not in clean:
            return
        if target == "curve":
            for curve_id in self.selected_ids():
                self.presentation.curves.setdefault(curve_id, {})[key] = clean[key]
        else:
            self.presentation.figure[key] = clean[key]
        self._emit_change()

    def _text_changed(self, target, key, widget):
        text = widget.text().strip()
        if text == widget.property("syncedText") and (target, key) not in self._errors:
            return
        rule = (CURVE_SCHEMA if target == "curve" else FIGURE_SCHEMA)[key]
        value = text
        if rule == "range":
            if not text:
                self.presentation.figure.pop(key, None)
                self._errors.pop((target, key), None)
                widget.setStyleSheet("")
                self._emit_change()
                self._validate()
                return
            try:
                value = [float(v.strip()) for v in text.replace("，", ",").split(",")]
            except ValueError:
                value = []
        if key not in _patch({key: value}, CURVE_SCHEMA if target == "curve" else FIGURE_SCHEMA):
            self._errors[target, key] = "颜色需为 #RRGGBB；范围需为有限的 下限,上限 且下限小于上限。"
            widget.setStyleSheet("border: 1px solid #D0552B;")
            self._validate()
            return
        self._errors.pop((target, key), None)
        widget.setStyleSheet("")
        self._field_changed(target, key, value)
        self._validate()

    def _choose_color(self, target, key, widget):
        color = QColorDialog.getColor(QColor(widget.text() or "#000000"), self, "曲线 / 排版颜色")
        if color.isValid():
            widget.setText(color.name())
            self._text_changed(target, key, widget)

    def _validate(self):
        error = next(iter(self._errors.values()), "")
        self.hint.setText(error or "多选可批量设置；只覆盖修改的字段。重置曲线可恢复继承。")
        self.validityChanged.emit(not self._errors, error)

    def _emit_change(self):
        current = self._state()
        if current == self._last:
            return
        self._undo.append(copy.deepcopy(self._last))
        self._undo = self._undo[-100:]
        self._redo.clear()
        self._last = copy.deepcopy(current)
        self.refresh()
        self.changed.emit()

    def _state(self):
        return {"presentation": self.presentation.to_dict(),
                "overrides": copy.deepcopy(self.legacy_overrides()),
                "style_id": self.style.style_id if self.style is not None else "1d_open"}

    def record_external(self):
        if self.snapshot is not None:
            self._emit_change()

    def _table_changed(self, item):
        if self._syncing or item.column() != 0:
            return
        self.presentation.curves.setdefault(item.data(Qt.UserRole), {})["visible"] = item.checkState() == Qt.Checked
        self._emit_change()

    def _move(self, direction):
        if self.snapshot is None:
            return
        ids = self.selected_ids()
        order = [c.curve_id for c in ordered_curves(self.snapshot, self.presentation)]
        positions = range(len(order)) if direction < 0 else range(len(order) - 1, -1, -1)
        for index in positions:
            target = index + direction
            if order[index] in ids and 0 <= target < len(order) and order[target] not in ids:
                order[index], order[target] = order[target], order[index]
        self.presentation.order = order
        self._emit_change()

    def _legend_moved(self, *_args):
        if self._syncing:
            return
        self.presentation.legend_order = [self.legend_list.item(i).data(Qt.UserRole) for i in range(self.legend_list.count())]
        self._emit_change()

    def _reset_legend_order(self):
        self.presentation.legend_order = []
        self._emit_change()

    def reset_selected(self):
        for key in self.selected_ids():
            self.presentation.curves.pop(key, None)
        self._emit_change()

    def _selected_as_defaults(self):
        selected = self.selected_ids()
        curves = snapshot_curves(self.snapshot) if self.snapshot is not None else []
        curve = next((c for c in curves if c.curve_id in selected), None)
        if curve is None:
            return
        effective = curve_style(curve, self.presentation, self.style.params,
                                self.snapshot.view == "waterfall", self.snapshot.payload.get("palette_span", len(curves)))
        raw = {k: v for k, v in effective.items() if k not in ("label", "legend", "visible")}
        self.presentation.defaults = Figure1DPresentation.from_dict({"defaults": raw}).defaults
        self._emit_change()

    def configure_common(self, controls):
        """复用宿主已有标题/字体控件，切换视图族时放回原布局。"""
        self._common_controls = [(label, widget, home, home.indexOf(widget))
                                 for label, widget, home in controls]

    def show_common(self, enabled):
        if enabled == self._common_visible:
            return
        self._common_visible = enabled
        if enabled:
            for label, widget, home, _ in self._common_controls:
                home.removeWidget(widget)
                if self.common_form.indexOf(widget) < 0:
                    self.common_form.addRow(label, widget)
        else:
            for label, widget, home, index in self._common_controls:
                position = self.common_form.indexOf(widget)
                if position >= 0:
                    row, _ = self.common_form.getItemPosition(position)
                    # takeRow removes ownership without deleting the shared widget.
                    taken = self.common_form.takeRow(row)
                    if taken.labelItem is not None:
                        taken.labelItem.widget().deleteLater()
                home.insertWidget(index, widget)

    def reset_all(self):
        empty = Figure1DPresentation()
        self.presentation.__dict__.update(empty.to_dict())
        self._errors.clear()
        self.resetAllRequested.emit()
        self._emit_change()
        self._validate()

    def _restore(self, state):
        from bandscope.exporting.publication_models import resolve_style
        self.presentation.__dict__.update(Figure1DPresentation.from_dict(state["presentation"]).to_dict())
        self.style = resolve_style("1d", state["style_id"])
        self.externalRestored.emit(self.style.style_id, state["overrides"])
        self._last = self._state()
        self._errors.clear()
        self.refresh()
        self._validate()
        self.changed.emit()

    def undo(self):
        if self._undo:
            self._redo.append(self._state())
            self._restore(self._undo.pop())

    def redo(self):
        if self._redo:
            self._undo.append(self._state())
            self._restore(self._redo.pop())

    def _sync_annotation(self, *_args):
        self._syncing = True
        row = self.annotation_list.currentRow()
        item = self.presentation.annotations[row] if 0 <= row < len(self.presentation.annotations) else None
        try:
            for key, (widget, default) in self.annotation_fields.items():
                blocker = QSignalBlocker(widget)
                widget.setEnabled(item is not None and (key != "coordinates" or item["kind"] in ("text", "arrow")))
                value = item.get(key, default) if item is not None else default
                if isinstance(widget, QComboBox):
                    widget.setCurrentIndex(widget.findData(value))
                else:
                    widget.setText(str(value))
                del blocker
        finally:
            self._syncing = False

    def _add_annotation(self):
        if self.snapshot is None:
            return
        curves = snapshot_curves(self.snapshot)
        def center(axis):
            values = np.concatenate([np.asarray(getattr(c, axis)).ravel() for c in curves])
            values = values[np.isfinite(values)]
            return float((np.min(values) + np.max(values)) / 2) if len(values) else 0.0
        self.presentation.annotations.append(dict(kind=self.annotation_kind.currentData(), text="注记",
                                                   x=center("x"), y=center("y"), x2=center("x"), y2=center("y"),
                                                   coordinates="data"))
        self._emit_change()
        self.annotation_list.setCurrentRow(len(self.presentation.annotations) - 1)

    def _remove_annotation(self):
        row = self.annotation_list.currentRow()
        if 0 <= row < len(self.presentation.annotations):
            self.presentation.annotations.pop(row)
            self._errors = {key: value for key, value in self._errors.items() if key[0] != "annotation"}
            self._emit_change()
            self._validate()

    def _annotation_changed(self, *_args):
        if self._syncing:
            return
        row = self.annotation_list.currentRow()
        if not 0 <= row < len(self.presentation.annotations):
            return
        from bandscope.exporting.curve_presentation import ANNOTATION_SCHEMA
        item = dict(self.presentation.annotations[row])
        for key, (widget, _) in self.annotation_fields.items():
            value = widget.currentData() if isinstance(widget, QComboBox) else widget.text()
            clean = _patch({key: value}, ANNOTATION_SCHEMA)
            if key not in clean:
                self._errors["annotation", key] = "注记坐标和样式必须为合法有限值，颜色需为 #RRGGBB。"
                self._validate()
                return
            self._errors.pop(("annotation", key), None)
            item[key] = clean[key]
        self.presentation.annotations[row] = item
        self._emit_change()
        self._validate()

    def _reload_presets(self):
        self.preset_combo.clear()
        self.preset_combo.addItems(sorted(load_presets(self.settings)))

    def _save_preset(self):
        name, ok = QInputDialog.getText(self, "保存 / 覆盖预设", "预设名称（同名将覆盖）", text=self.preset_combo.currentText())
        if ok and name.strip():
            presets = load_presets(self.settings)
            excluded = {"title_text", "xlabel_text", "ylabel_text", "zlabel_text", "panel_label"}
            presets[name.strip()[:80]] = dict(style_id=self.style.style_id, presentation=self.presentation.preset(),
                                              overrides={k: v for k, v in self.legacy_overrides().items() if k not in excluded})
            save_presets(self.settings, presets)
            self._reload_presets()
            self.preset_combo.setCurrentText(name.strip()[:80])

    def _apply_preset(self):
        preset = load_presets(self.settings).get(self.preset_combo.currentText())
        if isinstance(preset, dict):
            self.presentation.apply_preset(preset.get("presentation", {}))
            self.presetRequested.emit(preset)
            self._emit_change()

    def _delete_preset(self):
        presets = load_presets(self.settings)
        presets.pop(self.preset_combo.currentText(), None)
        save_presets(self.settings, presets)
        self._reload_presets()
