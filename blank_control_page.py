from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QSizePolicy, QVBoxLayout, QWidget
from siui.components.container import SiDenseContainer, SiTriSectionPanelCard
from siui.components.editbox import SiSpinBox
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.widgets import SiLabel, SiScrollArea
from siui.core import SiColor


class BlankControlPage(QWidget):
    SG_METHOD_NAME = "Savitzky-Golay滤波"
    GROUP_WIDTH = 560
    DEFAULT_SG_PARAMS = {
        "window_length": 5,
        "polyorder": 2,
    }
    MAX_SG_POLYORDER = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(0)

        self.scroll = SiScrollArea(self)
        self.container = QWidget(self)
        self.container.setFixedWidth(self.GROUP_WIDTH + 40)
        self.content_layout = QVBoxLayout(self.container)
        self.content_layout.setContentsMargins(20, 20, 20, 20)
        self.content_layout.setSpacing(18)
        self.content_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.group_savgol = self._create_method_group(self.SG_METHOD_NAME)
        self.content_layout.addWidget(self.group_savgol, 0, Qt.AlignTop | Qt.AlignLeft)
        self.content_layout.addStretch(1)
        self.container.adjustSize()

        self.scroll.setAttachment(self.container)
        root.addWidget(self.scroll)

    def _create_method_group(self, title):
        group = SiTitledWidgetGroup(self.container)
        group.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        group.setFixedWidth(self.GROUP_WIDTH)
        group.addTitle(title)

        card = SiTriSectionPanelCard(group)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        card.header().hide()
        card.footer().hide()
        card.body().layout().setSpacing(12)

        self.sg_window_box = self._create_spin_box(
            parent=card,
            title="滑动窗口长度",
            value=self.DEFAULT_SG_PARAMS["window_length"],
            minimum=3,
            maximum=9999,
            single_step=2,
        )
        self.sg_polyorder_box = self._create_spin_box(
            parent=card,
            title="多项式阶数",
            value=self.DEFAULT_SG_PARAMS["polyorder"],
            minimum=0,
            maximum=min(self.MAX_SG_POLYORDER, self.DEFAULT_SG_PARAMS["window_length"] - 1),
            single_step=1,
        )

        self.sg_window_box.editingFinished.connect(self._sync_savgol_constraints)
        self.sg_polyorder_box.editingFinished.connect(self._sync_savgol_constraints)

        card.body().addWidget(self.sg_window_box)
        card.body().addWidget(self.sg_polyorder_box)
        card.adjustSize()

        group.addWidget(card)
        self._apply_group_style(group)
        self._sync_savgol_constraints()
        return group

    def _create_spin_box(self, *, parent, title, value, minimum, maximum, single_step):
        spin_box = SiSpinBox(parent)
        spin_box.setTitle(title)
        spin_box.setMinimum(minimum)
        spin_box.setMaximum(maximum)
        spin_box.setSingleStep(single_step)
        spin_box.setValue(value)
        spin_box.resize(self.GROUP_WIDTH - 80, 58)
        spin_box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        return spin_box

    @staticmethod
    def _apply_group_style(group):
        for child in group.findChildren(SiLabel):
            try:
                child.colorGroup().assign(SiColor.TEXT_A, "#FFFFFF")
                child.reloadStyleSheet()
            except Exception:
                pass

    @staticmethod
    def _commit_spinbox_value(spin_box, fallback):
        text = spin_box.text().strip()
        if not text:
            spin_box.setValue(fallback)
            return spin_box.value()

        try:
            value = int(text)
        except ValueError:
            spin_box.setValue(fallback)
            return spin_box.value()

        spin_box.setValue(value)
        return spin_box.value()

    def reset_savgol_defaults(self):
        self.sg_window_box.setMaximum(9999)
        self.sg_window_box.setValue(self.DEFAULT_SG_PARAMS["window_length"])
        self.sg_polyorder_box.setMaximum(
            min(self.MAX_SG_POLYORDER, self.DEFAULT_SG_PARAMS["window_length"] - 1)
        )
        self.sg_polyorder_box.setValue(self.DEFAULT_SG_PARAMS["polyorder"])
        self._sync_savgol_constraints()

    def _sync_savgol_constraints(self):
        window_length = self._commit_spinbox_value(
            self.sg_window_box,
            self.DEFAULT_SG_PARAMS["window_length"],
        )
        if window_length < 3:
            window_length = 3
        if window_length % 2 == 0:
            window_length += 1
        self.sg_window_box.setValue(window_length)

        self.sg_polyorder_box.setMaximum(max(0, min(self.MAX_SG_POLYORDER, window_length - 1)))
        polyorder = self._commit_spinbox_value(
            self.sg_polyorder_box,
            self.DEFAULT_SG_PARAMS["polyorder"],
        )
        polyorder = max(0, min(polyorder, self.MAX_SG_POLYORDER, window_length - 1))
        self.sg_polyorder_box.setValue(polyorder)

    def get_savgol_params(self, energy_axis_size=None):
        self._sync_savgol_constraints()

        window_length = self.sg_window_box.value()
        polyorder = self.sg_polyorder_box.value()

        if energy_axis_size is not None and window_length > int(energy_axis_size):
            raise ValueError(
                f"Savitzky-Golay 的滑动窗口长度 {window_length} 不能大于能量轴长度 {energy_axis_size}。"
            )

        return {
            "window_length": int(window_length),
            "polyorder": int(polyorder),
        }

    def build_method_specs(self, selected_methods, data_shape=None):
        energy_axis_size = None
        if data_shape is not None and len(data_shape) >= 3:
            energy_axis_size = int(data_shape[2])

        resolved_methods = []
        for method in selected_methods:
            if method == self.SG_METHOD_NAME:
                resolved_methods.append(
                    {
                        "label": method,
                        "params": self.get_savgol_params(energy_axis_size),
                    }
                )
            else:
                resolved_methods.append(method)
        return resolved_methods
