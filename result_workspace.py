from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QToolButton, QVBoxLayout, QWidget

from siui.components.widgets import SiDenseVContainer
from siui.core import SiColor, SiGlobal
from siui.templates.application.components.page_view.page_view import PageButton


@dataclass
class AnalysisPageSpec:
    page_id: str
    title: str
    page_kind: str
    source_module: str
    params: Dict[str, Any] = field(default_factory=dict)
    activation_seq: int = 0
    closeable: bool = True
    source_page_id: Optional[str] = None


class ResultPageButton(PageButton):
    activated_with_id = pyqtSignal(str)

    ICON_MAP = {
        "home": "ic_fluent_home_filled",
        "control_panel": "ic_fluent_wrench_screwdriver_filled",
        "time_integral": "ic_fluent_history_filled",
        "axis_integral": "ic_fluent_data_trending_filled",
        "slice_dos": "ic_fluent_table_stack_right_filled",
        "energy_dos": "ic_fluent_document_data_filled",
        "waterfall_edc": "ic_fluent_document_data_filled",
        "second_derivative": "ic_fluent_document_data_filled",
    }

    def __init__(self, spec: AnalysisPageSpec, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.resize(40, 40)
        self.setHint(spec.title)
        self.attachment().setSvgSize(20, 20)
        self.attachment().load(SiGlobal.siui.iconpack.get(self.ICON_MAP.get(spec.page_kind, "ic_fluent_document_data_filled")))
        self.colorGroup().assign(SiColor.BUTTON_OFF, "#00FFFFFF")
        self.colorGroup().assign(SiColor.BUTTON_ON, "#12FFFFFF")
        self.reloadStyleSheet()
        self.activated.connect(self._emit_page_activated)

    def _emit_page_activated(self):
        self.activated_with_id.emit(self.spec.page_id)

    def set_active(self, active: bool):
        self.setChecked(active)
        self.active_indicator.setOpacityTo(1 if active else 0)


class ResultWorkspace(QWidget):
    page_activated = pyqtSignal(str)
    page_closed = pyqtSignal(str)

    def __init__(self, display_widget: QWidget, parent=None):
        super().__init__(parent)
        self.display_widget = display_widget
        self.home_page_id: Optional[str] = None
        self.current_page_id: Optional[str] = None
        self.activation_counter = 0
        self.page_specs: Dict[str, AnalysisPageSpec] = {}
        self.title_to_page_id: Dict[str, str] = {}
        self.page_buttons: Dict[str, ResultPageButton] = {}
        self.activation_history = []

        self.setObjectName("result_workspace")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = QFrame(self)
        self.sidebar.setObjectName("result_sidebar")
        self.sidebar.setFixedWidth(56)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(8, 12, 8, 12)
        sidebar_layout.setSpacing(0)

        self.nav_scroll = QScrollArea(self.sidebar)
        self.nav_scroll.setWidgetResizable(True)
        self.nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav_scroll.setFrameShape(QFrame.NoFrame)

        self.nav_buttons = SiDenseVContainer(self.nav_scroll)
        self.nav_buttons.setSpacing(8)
        self.nav_buttons.setShrinking(False)
        self.nav_buttons.setAlignment(Qt.AlignHCenter)
        self.nav_scroll.setWidget(self.nav_buttons)
        sidebar_layout.addWidget(self.nav_scroll)

        self.content_frame = QFrame(self)
        self.content_frame.setObjectName("result_content_frame")
        content_layout = QVBoxLayout(self.content_frame)
        content_layout.setContentsMargins(14, 14, 14, 14)
        content_layout.setSpacing(12)

        self.header = QFrame(self.content_frame)
        self.header.setObjectName("result_header")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)

        header_layout.addStretch(1)

        self.close_button = QToolButton(self.header)
        self.close_button.setText("X")
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setFixedSize(28, 28)
        self.close_button.clicked.connect(self.close_current_page)
        header_layout.addWidget(self.close_button)

        content_layout.addWidget(self.header)
        content_layout.addWidget(self.display_widget, stretch=1)

        root.addWidget(self.sidebar)
        root.addWidget(self.content_frame, stretch=1)

        self.setStyleSheet(
            """
            QWidget#result_workspace {
                background-color: #1A1A2E;
                border-radius: 12px;
            }
            QFrame#result_sidebar {
                background-color: #171728;
                border-top-left-radius: 12px;
                border-bottom-left-radius: 12px;
                border-right: 1px solid #2A2A3A;
            }
            QFrame#result_content_frame {
                background-color: #1A1A2E;
                border-top-right-radius: 12px;
                border-bottom-right-radius: 12px;
            }
            QFrame#result_header {
                background-color: transparent;
                border: none;
            }
            QToolButton {
                background-color: #E81123;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                font-weight: 700;
            }
            QToolButton:hover {
                background-color: #F33A4A;
            }
            QScrollArea {
                background: transparent;
            }
            """
        )

    def set_home_page(self, spec: AnalysisPageSpec):
        spec.closeable = False
        self.home_page_id = spec.page_id
        self._add_page(spec)
        self.activate_page(spec.page_id)

    def add_pinned_page(self, spec: AnalysisPageSpec, activate: bool = False) -> AnalysisPageSpec:
        spec.closeable = False
        if spec.page_id in self.page_specs:
            self.page_specs[spec.page_id].closeable = False
            if activate:
                self.activate_page(spec.page_id)
            return self.page_specs[spec.page_id]

        self._add_page(spec)
        if activate:
            self.activate_page(spec.page_id)
        return spec

    def ensure_page(self, spec: AnalysisPageSpec) -> AnalysisPageSpec:
        existing_id = self.title_to_page_id.get(spec.title)
        if existing_id is not None:
            self.activate_page(existing_id)
            return self.page_specs[existing_id]

        self._add_page(spec)
        self.activate_page(spec.page_id)
        return spec

    def add_page(self, spec: AnalysisPageSpec) -> AnalysisPageSpec:
        self._add_page(spec)
        self.activate_page(spec.page_id)
        return spec

    def _refresh_navigation_layout(self):
        self.nav_buttons.adjustSize()
        self.nav_buttons.arrangeWidget()
        self.nav_scroll.widget().adjustSize()
        self.nav_scroll.viewport().update()
        self.nav_scroll.update()

    def _rebuild_title_index(self):
        self.title_to_page_id = {}
        for page_id, spec in self.page_specs.items():
            self.title_to_page_id[spec.title] = page_id

    def _add_page(self, spec: AnalysisPageSpec):
        self.page_specs[spec.page_id] = spec
        self._rebuild_title_index()

        button = ResultPageButton(spec, self.nav_buttons)
        button.setIndex(len(self.page_buttons))
        button.activated_with_id.connect(self.activate_page)
        self.page_buttons[spec.page_id] = button
        self.nav_buttons.addWidget(button, side="top")
        button.show()
        self._refresh_navigation_layout()

    def _refresh_header(self):
        spec = self.current_spec()
        if spec is None:
            self.close_button.hide()
            return

        self.close_button.setVisible(spec.closeable)

    def activate_page(self, page_id: str):
        if page_id not in self.page_specs:
            return

        self.current_page_id = page_id
        self.activation_counter += 1

        spec = self.page_specs[page_id]
        spec.activation_seq = self.activation_counter

        self.activation_history = [pid for pid in self.activation_history if pid != page_id]
        self.activation_history.append(page_id)

        for current_id, button in self.page_buttons.items():
            button.set_active(current_id == page_id)

        self._refresh_header()
        self.page_activated.emit(page_id)

    def close_current_page(self):
        if self.current_page_id is not None:
            self.close_page(self.current_page_id)

    def close_page(self, page_id: str):
        spec = self.page_specs.get(page_id)
        if spec is None or not spec.closeable:
            return

        was_current = page_id == self.current_page_id

        self.activation_history = [pid for pid in self.activation_history if pid != page_id]
        self.page_specs.pop(page_id, None)
        self._rebuild_title_index()

        button = self.page_buttons.pop(page_id, None)
        if button is not None:
            self.nav_buttons.removeWidget(button)
            button.deleteLater()
            for index, current_button in enumerate(self.page_buttons.values()):
                current_button.setIndex(index)
            self._refresh_navigation_layout()

        self.page_closed.emit(page_id)

        if not was_current:
            return

        fallback_id = self.activation_history[-1] if self.activation_history else self.home_page_id
        if fallback_id is not None:
            self.activate_page(fallback_id)
        else:
            self._refresh_header()

    def reset_to_home(self):
        for page_id in list(self.page_specs.keys()):
            if page_id != self.home_page_id:
                self.close_page(page_id)

        if self.home_page_id is not None:
            self.activate_page(self.home_page_id)

    def current_spec(self) -> Optional[AnalysisPageSpec]:
        if self.current_page_id is None:
            return None
        return self.page_specs.get(self.current_page_id)

    def update_page(self, page_id: str, *, title: Optional[str] = None, params: Optional[Dict[str, Any]] = None, source_page_id: Optional[str] = None):
        spec = self.page_specs.get(page_id)
        if spec is None:
            return

        if title is not None:
            spec.title = title

        if params is not None:
            spec.params = dict(params)

        spec.source_page_id = source_page_id
        self._rebuild_title_index()

        button = self.page_buttons.get(page_id)
        if button is not None:
            button.spec = spec
            button.setHint(spec.title)

    def page_by_id(self, page_id: Optional[str]) -> Optional[AnalysisPageSpec]:
        if page_id is None:
            return None
        return self.page_specs.get(page_id)

    def home_spec(self) -> Optional[AnalysisPageSpec]:
        return self.page_by_id(self.home_page_id)
