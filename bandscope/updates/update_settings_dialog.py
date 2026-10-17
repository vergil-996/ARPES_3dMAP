"""更新源与 MatElab 账号设置；所有编辑先保留为对话框草稿。"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from bandscope.ui import theme
from bandscope.updates.matelab_source import MatelabCredentials
from bandscope.updates.sources import (
    DEFAULT_SOURCE,
    SOURCE_LABELS,
    SOURCE_MATELAB,
    normalize_source,
)


def _style_dialog(dialog):
    dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    dialog.setMinimumWidth(460)
    dialog.setStyleSheet(
        f"QDialog {{ background-color: {theme.BG_0}; }}"
        f"QLabel {{ color: {theme.TEXT_1}; background: transparent; }}"
        f"QGroupBox {{ color: {theme.TEXT_2}; border: 1px solid {theme.BORDER_HEX};"
        " border-radius: 8px; margin-top: 12px; padding: 16px 12px 12px; }"
        f"QGroupBox::title {{ subcontrol-origin: margin; left: 12px;"
        f" padding: 0 4px; background: {theme.BG_0}; }}"
        f"QLineEdit, QComboBox {{ color: {theme.TEXT_1}; background: {theme.BG_1};"
        f" border: 1px solid {theme.BORDER_HEX}; border-radius: {theme.R_S}px;"
        " min-height: 30px; padding: 0 10px; }"
        f"QLineEdit:focus, QComboBox:focus {{ border-color: {theme.ACCENT}; }}"
        f"QComboBox QAbstractItemView {{ color: {theme.TEXT_1};"
        f" background: {theme.BG_2}; selection-background-color: {theme.ACCENT_SOFT};"
        f" selection-color: {theme.TEXT_1}; }}"
    )


def _style_buttons(buttons, primary):
    for button in buttons.buttons():
        theme.style_push_button(button, "primary" if button is primary else "secondary")
        button.setMinimumHeight(32)
        button.setAutoDefault(False)
    primary.setDefault(True)


class MatelabLoginDialog(QDialog):
    """编辑账号；不预填密码，不在此处联网或写入设置。"""

    def __init__(self, parent=None, username=""):
        super().__init__(parent)
        self.setWindowTitle("设置 MatElab 账号")
        _style_dialog(self)
        layout = QFormLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        hint = QLabel(
            "使用你的物质科学电子实验室账号。\n"
            "账号密码仅保存在本机，密码做基本混淆存储。\n"
            "检查更新时会使用此账号登录。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_2};")
        layout.addRow(hint)
        self.username_edit = QLineEdit(str(username or ""))
        self.username_edit.setPlaceholderText("中文姓名或电子邮箱")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("请输入密码")
        layout.addRow("用户名", self.username_edit)
        layout.addRow("密码", self.password_edit)
        self.error_label = QLabel("用户名和密码不能为空。")
        self.error_label.setStyleSheet(f"color: {theme.WARNING};")
        self.error_label.hide()
        layout.addRow(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("确定")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        _style_buttons(self.buttons, self.buttons.button(QDialogButtonBox.Ok))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)

    def credentials(self):
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username or not password:
            return None
        return MatelabCredentials(username=username, password=password)

    def accept(self):
        if self.credentials() is None:
            self.error_label.show()
            field = self.username_edit if not self.username_edit.text().strip() else self.password_edit
            field.setFocus()
            return
        super().accept()


class UpdateSettingsDialog(QDialog):
    """只维护更新设置草稿，由控制器在接受后统一持久化。"""

    def __init__(self, parent=None, *, source=DEFAULT_SOURCE, credentials=None, username=""):
        super().__init__(parent)
        self.setWindowTitle("更新设置")
        _style_dialog(self)
        self._credentials = credentials
        self._initial_credentials = credentials
        self._username = credentials.username if credentials is not None else str(username or "")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(16)
        form = QFormLayout()
        self.source_combo = QComboBox()
        for source_id, label in SOURCE_LABELS.items():
            self.source_combo.addItem(label, source_id)
        self.source_combo.setCurrentIndex(self.source_combo.findData(normalize_source(source)))
        form.addRow("更新源", self.source_combo)
        layout.addLayout(form)
        self.source_hint = QLabel()
        self.source_hint.setWordWrap(True)
        self.source_hint.setStyleSheet(f"color: {theme.TEXT_2};")
        layout.addWidget(self.source_hint)

        self.account_group = QGroupBox("MatElab 账号")
        account_layout = QVBoxLayout(self.account_group)
        account_layout.setSpacing(12)
        self.account_label = QLabel()
        self.account_label.setTextFormat(Qt.PlainText)
        self.account_label.setWordWrap(True)
        self.account_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        account_layout.addWidget(self.account_label)
        account_buttons = QHBoxLayout()
        self.edit_account_button = QPushButton()
        self.clear_account_button = QPushButton("清除本机账号")
        for button in (self.edit_account_button, self.clear_account_button):
            theme.style_push_button(button, "secondary")
            button.setMinimumHeight(32)
            button.setAutoDefault(False)
            account_buttons.addWidget(button)
        account_buttons.addStretch()
        self.edit_account_button.clicked.connect(self._edit_account)
        self.clear_account_button.clicked.connect(self._clear_account)
        account_layout.addLayout(account_buttons)
        layout.addWidget(self.account_group)

        hint = QLabel("点击「保存」后生效；取消会保留原设置。")
        hint.setStyleSheet(f"color: {theme.TEXT_3};")
        layout.addWidget(hint)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText("保存")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        _style_buttons(self.buttons, self.buttons.button(QDialogButtonBox.Save))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.source_combo.currentIndexChanged.connect(self._refresh_source)
        self._refresh_source()
        self._refresh_account()

    def selected_source(self):
        return self.source_combo.currentData()

    def credentials(self):
        return self._credentials

    def _refresh_source(self):
        matelab = self.selected_source() == SOURCE_MATELAB
        self.account_group.setVisible(matelab)
        self.source_hint.setText(
            "MatElab 需要所内账号。未设置账号时，手动检查更新会提示填写。"
            if matelab else "GitHub 无需账号。已保存的 MatElab 账号会保留。"
        )

    def _refresh_account(self):
        credentials = self._credentials
        if credentials is None:
            text = "未设置账号" if self._initial_credentials is None else "保存后将清除本机账号"
        else:
            prefix = "已保存账号" if credentials == self._initial_credentials else "待保存账号"
            text = f"{prefix}：{credentials.username}"
        self.account_label.setText(text)
        self.edit_account_button.setText("设置账号…" if credentials is None else "更换账号…")
        self.clear_account_button.setEnabled(credentials is not None or bool(self._username))

    def _edit_account(self):
        dialog = MatelabLoginDialog(self, username=self._username)
        try:
            if dialog.exec_() != QDialog.Accepted:
                return
            credentials = dialog.credentials()
            if credentials is not None:
                self._credentials = credentials
                self._username = credentials.username
                self._refresh_account()
        finally:
            dialog.deleteLater()

    def _clear_account(self):
        self._credentials = None
        self._username = ""
        self._refresh_account()
