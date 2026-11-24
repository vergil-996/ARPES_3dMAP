from copy import deepcopy
import math
import uuid
import weakref
from collections.abc import Mapping

from PyQt5 import sip
from PyQt5.QtCore import QEvent, QMimeData, QPoint, QRect, QRectF, QSize, Qt, QTimer, QSignalBlocker, pyqtSignal
from PyQt5.QtGui import QColor, QDrag, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bandscope.processing import denoise_config as config
import bandscope.ui.theme as theme


_MIME_TYPE = "application/x-bandscope-denoise-node"
_DEBOUNCE_MS = 600

_METHOD_ORDER = (
    config.GAUSSIAN_METHOD_NAME,
    config.MEDIAN_METHOD_NAME,
    config.SG_METHOD_NAME,
    config.WAVELET_METHOD_NAME,
    config.NLM_METHOD_NAME,
    config.LOW_RANK_METHOD_NAME,
)

_LEGACY_KEYS = {
    config.GAUSSIAN_METHOD_NAME: "gaussian",
    config.MEDIAN_METHOD_NAME: "median",
    config.SG_METHOD_NAME: "sg",
    config.WAVELET_METHOD_NAME: "wavelet",
    config.NLM_METHOD_NAME: "nlmeans",
    config.LOW_RANK_METHOD_NAME: "low_rank",
}

_METHOD_DETAILS = {
    config.GAUSSIAN_METHOD_NAME: ("G", "Gaussian", "按轴抑制随机噪声"),
    config.MEDIAN_METHOD_NAME: ("M", "Median", "去除脉冲与孤立点"),
    config.SG_METHOD_NAME: ("SG", "Savitzky–Golay", "平滑曲线并保留峰形"),
    config.WAVELET_METHOD_NAME: ("W", "Wavelet", "抑制多尺度噪声"),
    config.NLM_METHOD_NAME: ("NLM", "Non-local means", "利用相似结构保留细节"),
    config.LOW_RANK_METHOD_NAME: ("SVD", "Low-rank SVD", "提取数据的主要结构"),
}


def _canonical_name(name):
    if name is None:
        return None
    canonical = config.canonical_method_name(name)
    return None if canonical == "None" else canonical


def _default_params(name):
    canonical = _canonical_name(name)
    factory = getattr(config, "default_method_params", None)
    if callable(factory):
        return deepcopy(factory(canonical))
    defaults = {
        config.GAUSSIAN_METHOD_NAME: config.DEFAULT_GAUSSIAN_PARAMS,
        config.MEDIAN_METHOD_NAME: config.DEFAULT_MEDIAN_PARAMS,
        config.SG_METHOD_NAME: config.DEFAULT_SG_PARAMS,
        config.WAVELET_METHOD_NAME: config.DEFAULT_WAVELET_PARAMS,
        config.NLM_METHOD_NAME: config.DEFAULT_NLMEANS_PARAMS,
        config.LOW_RANK_METHOD_NAME: config.DEFAULT_LOW_RANK_PARAMS,
    }
    return deepcopy(defaults[canonical])


def _new_node(name, params=None, node_id=None, enabled=True):
    canonical = _canonical_name(name)
    factory = getattr(config, "make_denoise_node", None)
    if callable(factory):
        node = factory(canonical, params=deepcopy(params), node_id=node_id, enabled=enabled)
        return deepcopy(node)
    merged = _default_params(canonical)
    if isinstance(params, Mapping):
        merged.update(deepcopy(dict(params)))
    return {
        "id": str(node_id or uuid.uuid4().hex),
        "label": canonical,
        "params": merged,
        "enabled": bool(enabled),
    }


def _fallback_normalize(nodes, legacy_controls=None):
    legacy_controls = legacy_controls if isinstance(legacy_controls, Mapping) else {}
    normalized = []
    used_ids = set()
    for item in nodes or ():
        if item is None:
            continue
        supplied_id = None
        enabled = True
        params = None
        if isinstance(item, Mapping):
            name = item.get("label") or item.get("method") or item.get("name")
            supplied_id = item.get("id")
            enabled = bool(item.get("enabled", True))
            params = item.get("params")
        else:
            name = item
        if name is None:
            continue
        try:
            canonical = _canonical_name(name)
        except (TypeError, ValueError):
            continue
        if canonical is None:
            continue
        merged = _default_params(canonical)
        legacy = legacy_controls.get(_LEGACY_KEYS[canonical])
        if isinstance(legacy, Mapping):
            merged.update(deepcopy(dict(legacy)))
        if isinstance(params, Mapping):
            merged.update(deepcopy(dict(params)))
        node_id = str(supplied_id or uuid.uuid4().hex)
        if node_id in used_ids:
            node_id = uuid.uuid4().hex
        used_ids.add(node_id)
        normalized.append({
            "id": node_id,
            "label": canonical,
            "params": merged,
            "enabled": enabled,
        })
    return normalized


def _normalize_nodes(nodes, legacy_controls=None):
    normalizer = getattr(config, "normalize_denoise_nodes", None)
    if callable(normalizer):
        normalized = normalizer(nodes, legacy_controls=deepcopy(legacy_controls))
    else:
        normalized = _fallback_normalize(nodes, legacy_controls)
    result = []
    used_ids = set()
    for item in normalized or ():
        if item is None:
            continue
        canonical = _canonical_name(item.get("label") or item.get("method") or item.get("name"))
        if canonical is None:
            continue
        node_id = str(item.get("id") or uuid.uuid4().hex)
        if node_id in used_ids:
            node_id = uuid.uuid4().hex
        used_ids.add(node_id)
        params = _default_params(canonical)
        if isinstance(item.get("params"), Mapping):
            params.update(deepcopy(dict(item["params"])))
        result.append({
            "id": node_id,
            "label": canonical,
            "params": params,
            "enabled": bool(item.get("enabled", True)),
        })
    return result


def _parameter_specs(name):
    common = {
        config.GAUSSIAN_METHOD_NAME: (
            {"key": "sigma_kx", "label": "Kx 标准差", "kind": "float", "min": 0.0, "max": 20.0, "step": 0.1, "hint": "σ，采样点；0 表示不平滑该轴"},
            {"key": "sigma_ky", "label": "Ky 标准差", "kind": "float", "min": 0.0, "max": 20.0, "step": 0.1, "hint": "σ，采样点；0 表示不平滑该轴"},
            {"key": "sigma_e", "label": "E 标准差", "kind": "float", "min": 0.0, "max": 20.0, "step": 0.1, "hint": "σ，采样点；0 表示不平滑该轴"},
        ),
        config.MEDIAN_METHOD_NAME: (
            {"key": "size_kx", "label": "Kx 窗口", "kind": "int", "min": 1, "max": config.MAX_MEDIAN_SIZE, "step": 2, "odd": True, "hint": "采样点，正奇数；1 表示不平滑该轴"},
            {"key": "size_ky", "label": "Ky 窗口", "kind": "int", "min": 1, "max": config.MAX_MEDIAN_SIZE, "step": 2, "odd": True, "hint": "采样点，正奇数；1 表示不平滑该轴"},
            {"key": "size_e", "label": "E 窗口", "kind": "int", "min": 1, "max": config.MAX_MEDIAN_SIZE, "step": 2, "odd": True, "hint": "采样点，正奇数；1 表示不平滑该轴"},
        ),
        config.SG_METHOD_NAME: (
            {"key": "window_length", "label": "滑动窗口", "kind": "int", "min": 3, "max": 9999, "step": 2, "odd": True, "hint": "采样点，不小于 3 的奇数"},
            {"key": "polyorder", "label": "多项式阶数", "kind": "int", "min": 0, "max": config.MAX_SG_POLYORDER, "step": 1, "hint": "无量纲，必须小于窗口长度"},
            {"key": "smoothing_axis", "label": "滑动轴", "kind": "choice", "options": (("E 轴", "e"), ("Kx 轴", "kx"), ("Ky 轴", "ky")), "hint": "沿所选采样轴执行局部多项式拟合"},
        ),
        config.WAVELET_METHOD_NAME: (
            {"key": "wavelet", "label": "小波基", "kind": "choice", "options": tuple((value, value) for value in config.WAVELET_OPTIONS), "hint": "PyWavelets 离散小波族"},
            {"key": "level", "label": "分解层数", "kind": "int", "min": 1, "max": 6, "step": 1, "hint": "无量纲；有效上限由数据形状决定"},
            {"key": "threshold_rule", "label": "阈值规则", "kind": "choice", "options": tuple((value, value) for value in config.THRESHOLD_RULE_OPTIONS), "hint": "通用阈值、SURE 或 Bayes 估计"},
            {"key": "threshold_mode", "label": "阈值模式", "kind": "choice", "options": tuple((value, value) for value in config.THRESHOLD_MODE_OPTIONS), "hint": "soft 软阈值或 hard 硬阈值"},
            {"key": "strength", "label": "阈值强度", "kind": "float", "min": 0.0, "max": 10.0, "step": 0.1, "hint": "无量纲；0 表示不收缩细节系数"},
            {"key": "noise_sigma", "label": "噪声 σ", "kind": "float", "min": 0.0, "max": 1.0e9, "step": 0.01, "hint": "数据强度单位；0 表示由 MAD 自动估计"},
        ),
        config.NLM_METHOD_NAME: (
            {"key": "patch_size", "label": "相似块边长", "kind": "int", "min": 1, "max": config.MAX_NLM_PATCH_SIZE, "step": 2, "odd": True, "hint": "采样点，正奇数"},
            {"key": "patch_distance", "label": "搜索半径", "kind": "int", "min": 1, "max": config.MAX_NLM_PATCH_DISTANCE, "step": 1, "hint": "采样点，正整数"},
            {"key": "h_scale", "label": "滤波倍率", "kind": "float", "min": 0.01, "max": 100.0, "step": 0.05, "hint": "无量纲；实际 h 为倍率乘以噪声 σ"},
            {"key": "noise_sigma", "label": "噪声 σ", "kind": "float", "min": 0.0, "max": 1.0e9, "step": 0.01, "hint": "数据强度单位；0 表示自动估计"},
        ),
        config.LOW_RANK_METHOD_NAME: (
            {"key": "rank", "label": "保留秩", "kind": "int", "min": 1, "max": 9999, "step": 1, "hint": "无量纲；有效上限由每个时间帧的数据形状决定"},
        ),
    }
    return deepcopy(common[name])


def _number_text(value):
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _parameter_summary(node):
    params = node["params"]
    name = node["label"]
    if name == config.GAUSSIAN_METHOD_NAME:
        return " · ".join(f"{axis} σ{_number_text(params[key])}" for axis, key in (("Kx", "sigma_kx"), ("Ky", "sigma_ky"), ("E", "sigma_e")))
    if name == config.MEDIAN_METHOD_NAME:
        return " · ".join(f"{axis} {_number_text(params[key])}" for axis, key in (("Kx", "size_kx"), ("Ky", "size_ky"), ("E", "size_e")))
    if name == config.SG_METHOD_NAME:
        axis = {"e": "E", "kx": "Kx", "ky": "Ky"}.get(params["smoothing_axis"], str(params["smoothing_axis"]))
        return f"窗口 {params['window_length']}  ·  {params['polyorder']} 阶  ·  {axis} 轴"
    if name == config.WAVELET_METHOD_NAME:
        noise = "自动噪声" if float(params["noise_sigma"]) == 0 else f"噪声 σ {_number_text(params['noise_sigma'])}"
        return f"{params['wavelet']}  ·  {params['level']} 层  ·  {params['threshold_rule']} / {params['threshold_mode']}\n强度 {_number_text(params['strength'])}  ·  {noise}"
    if name == config.NLM_METHOD_NAME:
        noise = "自动噪声" if float(params["noise_sigma"]) == 0 else f"噪声 σ {_number_text(params['noise_sigma'])}"
        return f"块 {params['patch_size']}  ·  半径 {params['patch_distance']}  ·  h × {_number_text(params['h_scale'])}\n{noise}"
    return f"保留秩 {params['rank']}  ·  每帧独立重构"


class _PrecisionDoubleSpinBox(QDoubleSpinBox):
    def textFromValue(self, value):
        return f"{value:.6f}".rstrip("0").rstrip(".")


class _ElidedLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._full_text = str(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def set_full_text(self, text):
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self._elide()

    def _elide(self):
        metrics = QFontMetrics(self.font())
        super().setText(metrics.elidedText(self._full_text, Qt.ElideRight, max(0, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class _StatusIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "idle"
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._advance)
        self.setFixedSize(16, 16)

    def set_state(self, state):
        self._state = state
        if state == "running" and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _advance(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if self._state == "running":
            self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        if self._state == "running":
            painter.setPen(QPen(QColor(theme.ACCENT), 1.8, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(rect, -self._angle * 16, 285 * 16)
            return
        color = QColor(theme.DANGER if self._state == "error" else theme.TEXT_3 if self._state == "idle" else theme.ACCENT)
        painter.setPen(QPen(color, 1.4))
        painter.setBrush(color if self._state in ("applied", "error") else Qt.NoBrush)
        painter.drawEllipse(rect)
        if self._state == "pending":
            painter.setBrush(color)
            painter.drawEllipse(rect.center(), 1.4, 1.4)


class _StateDot(QWidget):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "idle"
        self._enabled_node = True
        self.setFixedSize(20, 20)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("启停去噪节点")

    def set_state(self, state, enabled_node):
        self._state = state
        self._enabled_node = bool(enabled_node)
        self.setToolTip("点击停用此节点" if enabled_node else "点击启用此节点")
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        if not self._enabled_node:
            color = QColor(theme.TEXT_3)
            painter.setBrush(Qt.NoBrush)
        elif self._state == "error":
            color = QColor(theme.DANGER)
            painter.setBrush(color)
        else:
            color = QColor(theme.ACCENT if self._state in ("running", "applied") else theme.TEXT_2)
            painter.setBrush(color if self._state == "applied" else Qt.NoBrush)
        painter.setPen(QPen(color, 1.4))
        painter.drawEllipse(rect)
        if self._enabled_node and self._state == "running":
            painter.setBrush(color)
            painter.drawEllipse(rect.adjusted(2, 2, -2, -2))


class _NodeRail(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.first = False
        self.last = False
        self.setFixedWidth(24)
        self.dot = _StateDot(self)
        self.dot.move(2, 21)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        x = self.width() / 2.0
        start = 31.0 if self.first else 0.0
        end = 31.0 if self.last else float(self.height())
        if end > start:
            painter.setPen(QPen(QColor(theme.BORDER_HEX), 1.2))
            painter.drawLine(int(round(x)), int(start), int(round(x)), int(end))


class _DragGrip(QWidget):
    def __init__(self, node_id, pipeline, parent=None):
        super().__init__(parent)
        self.node_id = node_id
        self.pipeline = pipeline
        self._press_position = None
        self.setFixedSize(16, 28)
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip("拖动调整执行顺序")
        self.setAccessibleName("拖动节点排序")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_position = event.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_position is None or not event.buttons() & Qt.LeftButton:
            return
        if (event.pos() - self._press_position).manhattanLength() < QApplication.startDragDistance():
            return
        self._press_position = None
        mime = QMimeData()
        mime.setData(_MIME_TYPE, self.node_id.encode("utf-8"))
        mime.setText(self.node_id)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec_(Qt.MoveAction)
        self.pipeline._clear_drop_indicator()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._press_position = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(theme.TEXT_3), 1.25, Qt.SolidLine, Qt.RoundCap))
        for y in (9, 14, 19):
            painter.drawLine(QPoint(5, y), QPoint(11, y))


class _NodeCard(QFrame):
    activated = pyqtSignal(str)
    more_requested = pyqtSignal(str, object)

    def __init__(self, node, position, pipeline, parent=None):
        super().__init__(parent)
        self.node_id = node["id"]
        self.pipeline = pipeline
        self._press_position = None
        self._drop_edge = None
        self.setObjectName("denoise_node")
        self.setProperty("nodeEnabled", bool(node["enabled"]))
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(f"去噪节点 {position + 1}：{node['label']}")
        self.setAccessibleDescription("点击打开参数设置")
        self.setToolTip(f"{node['label']}\n点击打开参数设置；拖动左侧手柄调整执行顺序。")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 11, 10, 11)
        layout.setSpacing(7)
        header = QHBoxLayout()
        header.setSpacing(7)
        self.grip = _DragGrip(self.node_id, pipeline, self)
        header.addWidget(self.grip)
        index = QLabel(f"{position + 1:02d}")
        index.setObjectName("denoise_node_index")
        header.addWidget(index)
        self.title = _ElidedLabel(node["label"])
        self.title.setObjectName("denoise_node_title")
        header.addWidget(self.title, 1)
        self.more = QToolButton()
        self.more.setObjectName("denoise_node_more")
        self.more.setText("⋯")
        self.more.setCursor(Qt.PointingHandCursor)
        self.more.setFixedSize(28, 26)
        self.more.setToolTip("节点操作")
        self.more.setAccessibleName(f"{node['label']} 更多操作")
        self.more.clicked.connect(lambda: self.more_requested.emit(self.node_id, self.more))
        header.addWidget(self.more, 0, Qt.AlignTop)
        layout.addLayout(header)

        self.summary = QLabel(_parameter_summary(node))
        self.summary.setObjectName("denoise_node_summary")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.NoTextInteraction)
        self.summary.setCursor(Qt.PointingHandCursor)
        self.summary.setAccessibleName("参数摘要，点击打开参数设置")
        layout.addWidget(self.summary)
        self.hint = QLabel("" if node["enabled"] else "已停用 · 仍可点击调参")
        self.hint.setObjectName("denoise_node_hint")
        self.hint.setVisible(not node["enabled"])
        layout.addWidget(self.hint)

    def set_drop_edge(self, edge):
        if self._drop_edge == edge:
            return
        self._drop_edge = edge
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_position = event.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press_position is not None:
            moved = (event.pos() - self._press_position).manhattanLength()
            self._press_position = None
            if moved < QApplication.startDragDistance() and self.rect().contains(event.pos()):
                self.activated.emit(self.node_id)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activated.emit(self.node_id)
            event.accept()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(_MIME_TYPE):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        if not event.mimeData().hasFormat(_MIME_TYPE):
            event.ignore()
            return
        edge = "bottom" if event.pos().y() >= self.height() / 2 else "top"
        self.pipeline._show_drop_indicator(self, edge)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.set_drop_edge(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(_MIME_TYPE):
            event.ignore()
            return
        source = bytes(event.mimeData().data(_MIME_TYPE)).decode("utf-8")
        after = event.pos().y() >= self.height() / 2
        self.pipeline._clear_drop_indicator()
        self.pipeline._drop_node(source, self.node_id, after=after)
        event.acceptProposedAction()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_edge is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(theme.ACCENT), 2.0, Qt.SolidLine, Qt.RoundCap))
        y = 2 if self._drop_edge == "top" else self.height() - 3
        painter.drawLine(QPoint(10, y), QPoint(self.width() - 10, y))


class _NodeRow(QWidget):
    def __init__(self, node, position, count, pipeline, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0 if position == count - 1 else 8)
        layout.setSpacing(3)
        self.rail = _NodeRail(self)
        self.rail.first = position == 0
        self.rail.last = position == count - 1
        self.card = _NodeCard(node, position, pipeline, self)
        layout.addWidget(self.rail)
        layout.addWidget(self.card, 1)
        self.rail.dot.clicked.connect(lambda: pipeline._toggle_node(node["id"]))


class _EmptyGlyph(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(52, 44)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(theme.TEXT_3), 1.2))
        painter.setBrush(Qt.NoBrush)
        for offset in (12, 6, 0):
            rect = QRectF(8 + offset / 2, 4 + offset, 36 - offset, 12)
            painter.drawRoundedRect(rect, 4, 4)
        painter.setBrush(QColor(theme.BG_2))
        painter.drawEllipse(QPoint(26, 38), 3, 3)


class _EmptyState(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 22, 12, 20)
        layout.setSpacing(7)
        glyph = _EmptyGlyph(self)
        layout.addWidget(glyph, 0, Qt.AlignHCenter)
        title = QLabel("从第一个去噪步骤开始")
        title.setObjectName("denoise_empty_title")
        title.setAlignment(Qt.AlignCenter)
        title.setWordWrap(True)
        layout.addWidget(title)
        body = QLabel("自由组合算法，按顺序处理\n修改参数后自动更新")
        body.setObjectName("denoise_empty_body")
        body.setAlignment(Qt.AlignCenter)
        layout.addWidget(body)


class _MethodChoice(QFrame):
    activated = pyqtSignal(str)

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name
        self._pressed = False
        self.setObjectName("denoise_method_choice")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(f"添加 {name}")
        badge, _english, description = _METHOD_DETAILS[name]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(10)
        badge_label = QLabel(badge)
        badge_label.setObjectName("denoise_method_badge")
        badge_label.setAlignment(Qt.AlignCenter)
        badge_label.setFixedSize(36, 36)
        layout.addWidget(badge_label, 0, Qt.AlignVCenter)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)
        title = QLabel(name)
        title.setObjectName("denoise_method_title")
        text_layout.addWidget(title)
        subtitle = QLabel(description)
        subtitle.setObjectName("denoise_method_description")
        subtitle.setWordWrap(True)
        text_layout.addWidget(subtitle)
        layout.addLayout(text_layout, 1)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            if self.rect().contains(event.pos()):
                self.activated.emit(self.name)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activated.emit(self.name)
            event.accept()
            return
        super().keyPressEvent(event)


class _MethodPopup(QFrame):
    method_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("denoise_method_popup")
        self.setAccessibleName("添加去噪步骤")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 13, 12, 12)
        outer.setSpacing(5)
        title = QLabel("添加去噪步骤")
        title.setObjectName("denoise_popup_title")
        outer.addWidget(title)
        subtitle = QLabel("同一算法可重复添加，每个节点保留独立参数")
        subtitle.setObjectName("denoise_popup_subtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("denoise_method_scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(0, 6, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(8)
        self.choices = []
        for name in _METHOD_ORDER:
            choice = _MethodChoice(name)
            choice.activated.connect(self._select)
            self.choices.append(choice)
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll, 1)
        self._columns = 0
        self._set_columns(2)
        self.apply_theme()

    def _set_columns(self, columns):
        if columns == self._columns:
            return
        self._columns = columns
        for choice in self.choices:
            self.grid.removeWidget(choice)
        for index, choice in enumerate(self.choices):
            self.grid.addWidget(choice, index // columns, index % columns)
        for column in range(2):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)

    def _select(self, name):
        self.close()
        self.method_selected.emit(name)

    def present(self, anchor):
        self.apply_theme()
        origin = anchor.mapToGlobal(QPoint(0, 0))
        screen = QApplication.screenAt(origin) or anchor.screen()
        available = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        width = min(462, max(300, available.width() - 16))
        columns = 2 if width >= 410 else 1
        self._set_columns(columns)
        rows = math.ceil(len(self.choices) / columns)
        content_height = rows * 72 + 102
        height = min(content_height, max(240, available.height() - 24), 430)
        x = max(available.left() + 8, min(origin.x(), available.right() - width - 7))
        below = origin.y() + anchor.height() + 6
        if below + height <= available.bottom() - 6:
            y = below
        else:
            y = max(available.top() + 8, origin.y() - height - 6)
        self.resize(width, height)
        self.move(x, y)
        self.show()
        self.raise_()
        self.setFocus(Qt.PopupFocusReason)

    def apply_theme(self):
        self.setStyleSheet(f"""
            QFrame#denoise_method_popup {{ background: {theme.BG_3}; border: 1px solid {theme.BORDER_HEX}; border-radius: 10px; }}
            QLabel#denoise_popup_title {{ color: {theme.TEXT_1}; font-size: 14px; font-weight: 600; }}
            QLabel#denoise_popup_subtitle {{ color: {theme.TEXT_2}; font-size: 11px; }}
            QScrollArea#denoise_method_scroll, QScrollArea#denoise_method_scroll > QWidget > QWidget {{ background: transparent; border: none; }}
            QFrame#denoise_method_choice {{ background: {theme.BG_2}; border: 1px solid {theme.BORDER_HEX}; border-radius: 7px; }}
            QFrame#denoise_method_choice:hover, QFrame#denoise_method_choice:focus {{ background: {theme.BG_4}; border-color: {theme.TEXT_3}; }}
            QLabel#denoise_method_badge {{ background: {theme.BG_4}; color: {theme.TEXT_1}; border: 1px solid {theme.BORDER_HEX}; border-radius: 6px; font-family: {theme.FONT_MONO}; font-size: 11px; font-weight: 600; }}
            QLabel#denoise_method_title {{ color: {theme.TEXT_1}; font-size: 12px; font-weight: 600; background: transparent; border: none; }}
            QLabel#denoise_method_description {{ color: {theme.TEXT_2}; font-size: 10px; background: transparent; border: none; }}
            {theme.scrollbar_qss()}
        """)


class _ParameterDialog(QDialog):
    edited = pyqtSignal(str)
    committed = pyqtSignal(str, object)

    def __init__(self, node, position, parent=None):
        super().__init__(parent, Qt.Window | Qt.WindowCloseButtonHint | Qt.WindowTitleHint)
        self.node_id = node["id"]
        self._loading = False
        self._dirty = False
        self._controls = {}
        self._specs = {}
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setMinimumSize(360, 210)
        self.resize(430, 240)
        self.setWindowTitle(f"{node['label']} · 去噪参数")
        self.setAccessibleName(f"{node['label']} 参数")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 14)
        outer.setSpacing(10)
        header = QHBoxLayout()
        self._header_layout = header
        header.setSpacing(10)
        badge, english, _description = _METHOD_DETAILS[node["label"]]
        badge_label = QLabel(badge)
        badge_label.setObjectName("denoise_dialog_badge")
        badge_label.setAlignment(Qt.AlignCenter)
        badge_label.setFixedSize(40, 40)
        header.addWidget(badge_label)
        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)
        self.title = QLabel(node["label"])
        self.title.setObjectName("denoise_dialog_title")
        title_layout.addWidget(self.title)
        self.subtitle = QLabel(f"节点 {position + 1:02d} · {english}")
        self.subtitle.setObjectName("denoise_dialog_subtitle")
        title_layout.addWidget(self.subtitle)
        header.addLayout(title_layout, 1)
        outer.addLayout(header)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("denoise_dialog_scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        self.form = QVBoxLayout(content)
        self.form.setContentsMargins(0, 3, 3, 3)
        self.form.setSpacing(12)
        for spec in _parameter_specs(node["label"]):
            self._add_parameter(spec, node["params"].get(spec["key"]))
        self.form.addStretch(1)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)

        footer = QHBoxLayout()
        self._footer_layout = footer
        footer.setSpacing(7)
        self.auto_dot = QLabel("●")
        self.auto_dot.setObjectName("denoise_dialog_auto_dot")
        footer.addWidget(self.auto_dot)
        self.auto_status = QLabel("修改即自动生效")
        self.auto_status.setObjectName("denoise_dialog_auto_status")
        self.auto_status.setWordWrap(True)
        footer.addWidget(self.auto_status, 1)
        outer.addLayout(footer)
        hint = QLabel("数值停顿后生效 · Enter 或离开输入框立即生效")
        self._input_hint = hint
        hint.setObjectName("denoise_dialog_hint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._commit_now)
        self._sync_constraints()
        self.apply_theme()
        self.resize(430, self.sizeHint().height())

    def _preferred_height(self):
        self.form.activate()
        margins = self.layout().contentsMargins()
        chrome_height = (
            margins.top() + margins.bottom()
            + 3 * self.layout().spacing()
            + self._header_layout.sizeHint().height()
            + self._footer_layout.sizeHint().height()
            + self._input_hint.sizeHint().height()
            + 2 * self.scroll.frameWidth()
        )
        return max(210, chrome_height + self.form.sizeHint().height())

    def sizeHint(self):
        return QSize(430, min(540, self._preferred_height()))

    def _add_parameter(self, spec, value):
        key = spec["key"]
        self._specs[key] = spec
        row = QWidget()
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(12)
        label = QLabel(spec["label"])
        label.setObjectName("denoise_field_label")
        top.addWidget(label, 1)
        if spec["kind"] == "choice":
            control = QComboBox()
            control.setObjectName("denoise_parameter_input")
            control.setMinimumHeight(32)
            control.setMinimumWidth(152)
            for display, option_value in spec["options"]:
                control.addItem(display, option_value)
            index = control.findData(value)
            if index < 0:
                index = control.findText(str(value))
            control.setCurrentIndex(max(0, index))
            control.currentIndexChanged.connect(self._option_edited)
        elif spec["kind"] == "float":
            control = _PrecisionDoubleSpinBox()
            control.setDecimals(6)
            control.setRange(float(spec["min"]), float(spec["max"]))
            control.setSingleStep(float(spec["step"]))
            control.setValue(float(value))
        else:
            control = QSpinBox()
            control.setRange(int(spec["min"]), int(spec["max"]))
            control.setSingleStep(int(spec["step"]))
            control.setValue(int(value))
        if spec["kind"] != "choice":
            control.setObjectName("denoise_parameter_input")
            control.setButtonSymbols(QAbstractSpinBox.NoButtons)
            control.setKeyboardTracking(True)
            control.setAccelerated(True)
            control.setAlignment(Qt.AlignRight)
            control.setFixedWidth(132)
            control.setMinimumHeight(32)
            control.setAccessibleName(spec["label"])
            control.valueChanged.connect(self._numeric_edited)
            control.lineEdit().textEdited.connect(self._numeric_edited)
            control.editingFinished.connect(self._commit_now)
        top.addWidget(control)
        row_layout.addLayout(top)
        hint = QLabel(spec["hint"])
        hint.setObjectName("denoise_field_hint")
        hint.setWordWrap(True)
        row_layout.addWidget(hint)
        self.form.addWidget(row)
        self._controls[key] = control
        control.setToolTip(spec["hint"])

    def _numeric_edited(self, *_args):
        if self._loading:
            return
        self._dirty = True
        self._sync_constraints()
        self._debounce.start()
        self.set_auto_status("pending", "正在等待输入完成…")
        self.edited.emit(self.node_id)

    def _option_edited(self, *_args):
        if self._loading:
            return
        self._dirty = True
        self._debounce.stop()
        self._commit_now()

    def _sync_constraints(self):
        if "window_length" not in self._controls:
            return
        window = self._controls["window_length"].value()
        polyorder = self._controls.get("polyorder")
        if polyorder is not None:
            maximum = min(config.MAX_SG_POLYORDER, max(0, int(window) - 1))
            with QSignalBlocker(polyorder):
                polyorder.setMaximum(maximum)

    def _values(self):
        values = {}
        for key, control in self._controls.items():
            spec = self._specs[key]
            if spec["kind"] == "choice":
                values[key] = control.currentData()
            elif spec["kind"] == "float":
                values[key] = float(control.value())
            else:
                value = int(control.value())
                if spec.get("odd") and value % 2 == 0:
                    value += 1
                maximum = int(control.maximum())
                if value > maximum:
                    value = maximum if maximum % 2 else maximum - 1
                values[key] = max(int(control.minimum()), value)
                if value != control.value():
                    with QSignalBlocker(control):
                        control.setValue(values[key])
        return values

    def _commit_now(self):
        if self._loading or not self._dirty:
            self._debounce.stop()
            return
        self._debounce.stop()
        self._sync_constraints()
        values = self._values()
        self._dirty = False
        self.set_auto_status("pending", "参数已更新 · 等待重新计算")
        self.committed.emit(self.node_id, deepcopy(values))

    def sync_node(self, node, position):
        self._debounce.stop()
        self._loading = True
        try:
            self.subtitle.setText(f"节点 {position + 1:02d} · {_METHOD_DETAILS[node['label']][1]}")
            for key, control in self._controls.items():
                value = node["params"].get(key)
                with QSignalBlocker(control):
                    if isinstance(control, QComboBox):
                        index = control.findData(value)
                        if index < 0:
                            index = control.findText(str(value))
                        control.setCurrentIndex(max(0, index))
                    else:
                        control.setValue(value)
            self._dirty = False
        finally:
            self._loading = False
        self._sync_constraints()

    def set_auto_status(self, state, message=""):
        messages = {
            "idle": "修改即自动生效",
            "pending": "配置已更改 · 等待更新",
            "running": "正在计算 · 可继续编辑",
            "applied": "已应用当前参数",
            "error": "计算失败 · 参数已保留",
        }
        self.auto_status.setText(message if state == "error" and message else messages[state])
        self.auto_dot.setStyleSheet(f"color: {theme.DANGER if state == 'error' else theme.TEXT_3 if state == 'idle' else theme.ACCENT};")

    def apply_theme(self):
        self.setStyleSheet(f"""
            QDialog {{ background: {theme.BG_2}; color: {theme.TEXT_1}; }}
            QLabel#denoise_dialog_badge {{ background: {theme.BG_4}; color: {theme.TEXT_1}; border: 1px solid {theme.BORDER_HEX}; border-radius: 7px; font-family: {theme.FONT_MONO}; font-size: 12px; font-weight: 600; }}
            QLabel#denoise_dialog_title {{ color: {theme.TEXT_1}; font-size: 16px; font-weight: 600; }}
            QLabel#denoise_dialog_subtitle, QLabel#denoise_dialog_hint {{ color: {theme.TEXT_3}; font-size: 10px; }}
            QScrollArea#denoise_dialog_scroll, QScrollArea#denoise_dialog_scroll > QWidget > QWidget {{ background: transparent; border: none; }}
            QLabel#denoise_field_label {{ color: {theme.TEXT_1}; font-size: 12px; }}
            QLabel#denoise_field_hint {{ color: {theme.TEXT_3}; font-size: 10px; }}
            QLabel#denoise_dialog_auto_status {{ color: {theme.TEXT_2}; font-size: 11px; }}
            QDoubleSpinBox#denoise_parameter_input, QSpinBox#denoise_parameter_input, QComboBox#denoise_parameter_input {{ background: {theme.BG_1}; color: {theme.TEXT_1}; border: 1px solid {theme.BORDER_HEX}; border-radius: 5px; padding: 4px 9px; font-family: {theme.FONT_MONO}; font-size: 11px; selection-background-color: {theme.ACCENT}; selection-color: {theme.ACCENT_ON}; }}
            QDoubleSpinBox#denoise_parameter_input:focus, QSpinBox#denoise_parameter_input:focus, QComboBox#denoise_parameter_input:focus {{ border-color: {theme.TEXT_2}; }}
            QComboBox#denoise_parameter_input::drop-down {{ border: none; width: 22px; }}
            QComboBox QAbstractItemView {{ background: {theme.BG_3}; color: {theme.TEXT_1}; border: 1px solid {theme.BORDER_HEX}; selection-background-color: {theme.BG_4}; selection-color: {theme.TEXT_1}; padding: 4px; }}
            {theme.scrollbar_qss()}
        """)
        self.auto_dot.setStyleSheet(f"color: {theme.ACCENT};")

    def closeEvent(self, event):
        self._commit_now()
        super().closeEvent(event)


class DenoisePipelineWidget(QWidget):
    pipeline_edited = pyqtSignal()
    pipeline_committed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("denoise_pipeline")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self.setAcceptDrops(True)
        self.setAccessibleName("去噪流水线")
        self._nodes = []
        self._node_cards = {}
        self._parameter_dialogs = {}
        self._execution_state = "idle"
        self._execution_message = ""
        self._drop_card = None
        self._suspend_signals = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        header = QHBoxLayout()
        header.setSpacing(8)
        caption = QLabel("按顺序处理 · 自动生效")
        caption.setObjectName("denoise_caption")
        header.addWidget(caption, 1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("denoise_count")
        header.addWidget(self.count_label)
        layout.addLayout(header)

        self.flow_host = QWidget()
        self.flow_host.setAcceptDrops(True)
        self.flow_host.installEventFilter(self)
        self.flow_layout = QVBoxLayout(self.flow_host)
        self.flow_layout.setContentsMargins(0, 0, 0, 0)
        self.flow_layout.setSpacing(0)
        layout.addWidget(self.flow_host)

        self.add_button = QPushButton("＋  添加去噪步骤")
        self.add_button.setObjectName("denoise_add")
        self.add_button.setMinimumHeight(38)
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.setAccessibleName("添加去噪步骤")
        self.add_button.clicked.connect(self._present_method_popup)
        layout.addWidget(self.add_button)

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self.status_indicator = _StatusIndicator(self)
        status_row.addWidget(self.status_indicator)
        self.status_label = _ElidedLabel("修改后自动更新")
        self.status_label.setObjectName("denoise_status")
        status_row.addWidget(self.status_label, 1)
        layout.addLayout(status_row)

        self.error_panel = QFrame()
        self.error_panel.setObjectName("denoise_error")
        self.error_panel.setVisible(False)
        error_layout = QHBoxLayout(self.error_panel)
        error_layout.setContentsMargins(10, 8, 10, 8)
        error_layout.setSpacing(8)
        error_badge = QLabel("!")
        error_badge.setObjectName("denoise_error_badge")
        error_badge.setAlignment(Qt.AlignCenter)
        error_badge.setFixedSize(18, 18)
        error_layout.addWidget(error_badge, 0, Qt.AlignTop)
        self.error_label = QLabel()
        self.error_label.setObjectName("denoise_error_label")
        self.error_label.setWordWrap(True)
        error_layout.addWidget(self.error_label, 1)
        layout.addWidget(self.error_panel)

        self.method_popup = _MethodPopup(self)
        self.method_popup.method_selected.connect(self._add_node)
        reference = weakref.ref(self)

        def on_theme_changed(name):
            widget = reference()
            if widget is not None and not sip.isdeleted(widget):
                widget.apply_theme(name)

        self._theme_listener = on_theme_changed
        theme.changed.connect(self._theme_listener)

        def on_destroyed():
            theme.changed.disconnect(on_theme_changed)

        on_theme_changed._destroy_listener = on_destroyed
        self._theme_destroy_listener = on_destroyed
        self.destroyed.connect(self._theme_destroy_listener)
        self._rebuild()
        self.apply_theme()

    def _disconnect_theme(self, *_args):
        theme.changed.disconnect(self._theme_listener)

    def get_denoise_settings(self):
        return [
            {"id": node["id"], "label": node["label"], "params": deepcopy(node["params"])}
            for node in self._nodes if node["enabled"]
        ]

    def export_state(self):
        return {"nodes": deepcopy(self._nodes)}

    def restore_state(self, state, block_signals=True):
        state = state if isinstance(state, Mapping) else {}
        legacy_controls = state.get("legacy_controls")
        if "nodes" in state:
            source = state.get("nodes") or []
        elif "methods" in state:
            source = state.get("methods") or []
        else:
            source = [state.get(f"combo_n{index}") for index in range(1, 4)]
        nodes = _normalize_nodes(source, legacy_controls=legacy_controls)
        self._suspend_signals = bool(block_signals)
        try:
            self._close_all_dialogs()
            self._nodes = nodes
            self._rebuild()
            self.set_execution_state("idle")
        finally:
            self._suspend_signals = False
        if not block_signals:
            self._mark_pending()
            self.pipeline_edited.emit()
            self.pipeline_committed.emit()

    def set_execution_state(self, state, message=""):
        state = str(state)
        if state not in ("pending", "running", "applied", "error", "idle"):
            raise ValueError(f"未知去噪执行状态：{state!r}")
        self._execution_state = state
        self._execution_message = str(message or "")
        labels = {
            "idle": "修改后自动更新",
            "pending": "配置已更改 · 等待更新",
            "running": "正在计算 · 可继续编辑",
            "applied": "已应用当前流水线",
            "error": "计算失败 · 配置已保留",
        }
        self.status_label.set_full_text(labels[state])
        self.status_indicator.set_state(state)
        self.error_panel.setVisible(state == "error")
        self.error_label.setText(self._execution_message or "去噪计算失败，请调整参数后重试。")
        self.error_label.setToolTip(self.error_label.text())
        for row in self._node_rows():
            row.rail.dot.set_state(state, self._node(row.card.node_id)["enabled"])
        for dialog in self._parameter_dialogs.values():
            dialog.set_auto_status(state, self._execution_message)

    def apply_theme(self, *_args):
        self.setStyleSheet(f"""
            QWidget#denoise_pipeline {{ background: transparent; }}
            QLabel#denoise_caption {{ color: {theme.TEXT_2}; font-size: 11px; }}
            QLabel#denoise_count {{ color: {theme.TEXT_3}; font-family: {theme.FONT_MONO}; font-size: 10px; }}
            QPushButton#denoise_add {{ color: {theme.TEXT_2}; background: transparent; border: 1px dashed {theme.BORDER_HEX}; border-radius: 7px; padding: 7px 10px; font-size: 11px; }}
            QPushButton#denoise_add:hover {{ color: {theme.TEXT_1}; background: {theme.ACCENT_SOFT}; border-color: {theme.TEXT_3}; }}
            QPushButton#denoise_add:focus {{ color: {theme.TEXT_1}; border: 1px solid {theme.TEXT_2}; }}
            QLabel#denoise_empty_title {{ color: {theme.TEXT_1}; font-size: 12px; font-weight: 600; }}
            QLabel#denoise_empty_body {{ color: {theme.TEXT_3}; font-size: 11px; line-height: 150%; }}
            QFrame#denoise_node {{ background: {theme.BG_2}; border: 1px solid {theme.BORDER_HEX}; border-radius: 8px; }}
            QFrame#denoise_node:hover, QFrame#denoise_node:focus {{ background: {theme.BG_3}; border-color: {theme.TEXT_3}; }}
            QFrame#denoise_node[nodeEnabled="false"] {{ background: {theme.BG_1}; border-style: dashed; }}
            QLabel#denoise_node_index {{ color: {theme.TEXT_3}; font-family: {theme.FONT_MONO}; font-size: 10px; background: transparent; border: none; }}
            QLabel#denoise_node_title {{ color: {theme.TEXT_1}; font-size: 12px; font-weight: 600; background: transparent; border: none; }}
            QLabel#denoise_node_summary {{ color: {theme.TEXT_2}; font-family: {theme.FONT_MONO}; font-size: 10px; background: transparent; border: none; }}
            QLabel#denoise_node_hint {{ color: {theme.TEXT_3}; font-size: 9px; background: transparent; border: none; }}
            QFrame#denoise_node[nodeEnabled="false"] QLabel {{ color: {theme.TEXT_3}; }}
            QToolButton#denoise_node_more {{ color: {theme.TEXT_2}; background: transparent; border: none; border-radius: 5px; font-size: 17px; padding-bottom: 3px; }}
            QToolButton#denoise_node_more:hover {{ color: {theme.TEXT_1}; background: {theme.BG_4}; }}
            QLabel#denoise_status {{ color: {theme.TEXT_3}; font-size: 10px; }}
            QFrame#denoise_error {{ background: {theme.DANGER_SOFT}; border: 1px solid {theme.DANGER_BORDER}; border-radius: 6px; }}
            QLabel#denoise_error_badge {{ color: {theme.DANGER}; border: 1px solid {theme.DANGER}; border-radius: 9px; font-size: 11px; font-weight: 600; }}
            QLabel#denoise_error_label {{ color: {theme.DANGER}; font-size: 11px; background: transparent; border: none; }}
        """)
        for dialog in self._parameter_dialogs.values():
            dialog.apply_theme()
        self.update()

    def sizeHint(self):
        return QSize(300, max(220, super().sizeHint().height()))

    def eventFilter(self, watched, event):
        if watched is self.flow_host:
            if event.type() in (QEvent.DragEnter, QEvent.DragMove):
                if event.mimeData().hasFormat(_MIME_TYPE):
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Drop and event.mimeData().hasFormat(_MIME_TYPE):
                source = bytes(event.mimeData().data(_MIME_TYPE)).decode("utf-8")
                self._clear_drop_indicator()
                self._drop_node(source, None, after=True)
                event.acceptProposedAction()
                return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(_MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat(_MIME_TYPE):
            source = bytes(event.mimeData().data(_MIME_TYPE)).decode("utf-8")
            self._drop_node(source, None, after=True)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _node_rows(self):
        return [self.flow_layout.itemAt(index).widget() for index in range(self.flow_layout.count()) if isinstance(self.flow_layout.itemAt(index).widget(), _NodeRow)]

    def _node(self, node_id):
        return next((node for node in self._nodes if node["id"] == node_id), None)

    def _position(self, node_id):
        return next((index for index, node in enumerate(self._nodes) if node["id"] == node_id), -1)

    def _rebuild(self):
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._node_cards = {}
        if not self._nodes:
            self.flow_layout.addWidget(_EmptyState(self.flow_host))
        for position, node in enumerate(self._nodes):
            row = _NodeRow(node, position, len(self._nodes), self, self.flow_host)
            row.card.activated.connect(self.open_parameter_dialog)
            row.card.more_requested.connect(self._show_node_menu)
            row.rail.dot.set_state(self._execution_state, node["enabled"])
            self.flow_layout.addWidget(row)
            self._node_cards[node["id"]] = row.card
        enabled_count = sum(bool(node["enabled"]) for node in self._nodes)
        if self._nodes:
            self.count_label.setText(f"{len(self._nodes)} 步 · {enabled_count} 启用")
        else:
            self.count_label.setText("0 步")
        self.flow_host.updateGeometry()
        self.updateGeometry()

    def _mark_pending(self):
        if self._execution_state != "pending":
            self.set_execution_state("pending")

    def _emit_structure_change(self):
        self._mark_pending()
        self.pipeline_edited.emit()
        if not self._suspend_signals:
            self.pipeline_committed.emit()

    def _present_method_popup(self):
        self.method_popup.present(self.add_button)

    def _add_node(self, name):
        node = _new_node(name)
        self._nodes.append(node)
        self._rebuild()
        self._emit_structure_change()
        self.open_parameter_dialog(node["id"])

    def _toggle_node(self, node_id):
        node = self._node(node_id)
        if node is None:
            return
        node["enabled"] = not node["enabled"]
        self._rebuild()
        self._emit_structure_change()

    def _duplicate_node(self, node_id):
        position = self._position(node_id)
        if position < 0:
            return
        source = self._nodes[position]
        duplicate = _new_node(source["label"], source["params"], enabled=source["enabled"])
        self._nodes.insert(position + 1, duplicate)
        self._rebuild()
        self._emit_structure_change()

    def _reset_node(self, node_id):
        node = self._node(node_id)
        if node is None:
            return
        node["params"] = _default_params(node["label"])
        self._sync_dialog(node)
        card = self._node_cards.get(node_id)
        if card is not None:
            card.summary.setText(_parameter_summary(node))
        self._emit_structure_change()

    def _remove_node(self, node_id):
        position = self._position(node_id)
        if position < 0:
            return
        self._nodes.pop(position)
        dialog = self._parameter_dialogs.pop(node_id, None)
        if dialog is not None:
            dialog._debounce.stop()
            dialog._dirty = False
            dialog.close()
            dialog.deleteLater()
        self._rebuild()
        self._emit_structure_change()

    def _move_to(self, node_id, new_index):
        old_index = self._position(node_id)
        if old_index < 0:
            return
        new_index = max(0, min(len(self._nodes) - 1, int(new_index)))
        if old_index == new_index:
            return
        node = self._nodes.pop(old_index)
        self._nodes.insert(new_index, node)
        self._rebuild()
        self._sync_dialog_positions()
        self._emit_structure_change()

    def _drop_node(self, source_id, target_id, after):
        source_index = self._position(source_id)
        if source_index < 0 or source_id == target_id and target_id is not None:
            return
        previous_order = [item["id"] for item in self._nodes]
        node = self._nodes.pop(source_index)
        if target_id is None:
            new_index = len(self._nodes)
        else:
            target_index = self._position(target_id)
            if target_index < 0:
                self._nodes.insert(source_index, node)
                return
            new_index = target_index + (1 if after else 0)
        self._nodes.insert(new_index, node)
        if [item["id"] for item in self._nodes] == previous_order:
            return
        self._rebuild()
        self._sync_dialog_positions()
        self._emit_structure_change()

    def _show_drop_indicator(self, card, edge):
        if self._drop_card is not None and self._drop_card is not card:
            self._drop_card.set_drop_edge(None)
        self._drop_card = card
        card.set_drop_edge(edge)

    def _clear_drop_indicator(self):
        if self._drop_card is not None:
            self._drop_card.set_drop_edge(None)
            self._drop_card = None

    def _show_node_menu(self, node_id, anchor):
        node = self._node(node_id)
        if node is None:
            return
        position = self._position(node_id)
        menu = QMenu(self)
        menu.setStyleSheet(theme.context_menu_qss())
        duplicate = menu.addAction("复制节点")
        duplicate.triggered.connect(lambda: self._duplicate_node(node_id))
        toggle = menu.addAction("停用节点" if node["enabled"] else "启用节点")
        toggle.triggered.connect(lambda: self._toggle_node(node_id))
        menu.addSeparator()
        earlier = menu.addAction("向前移动")
        earlier.setEnabled(position > 0)
        earlier.triggered.connect(lambda: self._move_to(node_id, position - 1))
        later = menu.addAction("向后移动")
        later.setEnabled(position < len(self._nodes) - 1)
        later.triggered.connect(lambda: self._move_to(node_id, position + 1))
        menu.addSeparator()
        reset = menu.addAction("重置为默认参数")
        reset.triggered.connect(lambda: self._reset_node(node_id))
        remove = menu.addAction("移除节点")
        remove.triggered.connect(lambda: self._remove_node(node_id))
        menu.exec_(anchor.mapToGlobal(QPoint(0, anchor.height())))

    def open_parameter_dialog(self, node_id):
        node = self._node(node_id)
        if node is None:
            return None
        dialog = self._parameter_dialogs.get(node_id)
        if dialog is None:
            dialog = _ParameterDialog(node, self._position(node_id), self)
            dialog.edited.connect(self._on_parameter_edited)
            dialog.committed.connect(self._on_parameter_committed)
            self._parameter_dialogs[node_id] = dialog
            dialog.set_auto_status(self._execution_state, self._execution_message)
            self._position_dialog(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def _position_dialog(self, dialog):
        card = self._node_cards.get(dialog.node_id)
        if card is None:
            return
        origin = card.mapToGlobal(QPoint(card.width() + 8, 0))
        screen = QApplication.screenAt(origin) or card.screen()
        available = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        available_height = max(dialog.minimumHeight(), available.height() - 16)
        if dialog.height() > available_height:
            dialog.resize(dialog.width(), available_height)
        offset = (len(self._parameter_dialogs) - 1) % 5 * 22
        x = origin.x() + offset
        if x + dialog.width() > available.right():
            x = card.mapToGlobal(QPoint(0, 0)).x() - dialog.width() - 8 - offset
        x = max(available.left() + 8, x)
        y = max(available.top() + 8, min(origin.y() + offset, available.bottom() - dialog.height() - 8))
        dialog.move(x, y)

    def _on_parameter_edited(self, node_id):
        if self._node(node_id) is None:
            return
        self._mark_pending()
        if not self._suspend_signals:
            self.pipeline_edited.emit()

    def _on_parameter_committed(self, node_id, params):
        node = self._node(node_id)
        if node is None:
            return
        node["params"] = deepcopy(params)
        card = self._node_cards.get(node_id)
        if card is not None:
            card.summary.setText(_parameter_summary(node))
        self._mark_pending()
        if not self._suspend_signals:
            self.pipeline_edited.emit()
            self.pipeline_committed.emit()

    def _sync_dialog(self, node):
        dialog = self._parameter_dialogs.get(node["id"])
        if dialog is not None:
            dialog.sync_node(node, self._position(node["id"]))

    def _sync_dialog_positions(self):
        for node_id, dialog in self._parameter_dialogs.items():
            position = self._position(node_id)
            if position >= 0:
                dialog.subtitle.setText(f"节点 {position + 1:02d} · {_METHOD_DETAILS[self._node(node_id)['label']][1]}")

    def _close_all_dialogs(self):
        dialogs = list(self._parameter_dialogs.values())
        self._parameter_dialogs.clear()
        for dialog in dialogs:
            dialog._debounce.stop()
            dialog._dirty = False
            dialog.close()
            dialog.deleteLater()

    def closeEvent(self, event):
        self._disconnect_theme()
        self._close_all_dialogs()
        self.method_popup.close()
        super().closeEvent(event)
