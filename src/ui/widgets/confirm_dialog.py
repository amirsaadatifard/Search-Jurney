"""Modal confirmation for destructive or consequential actions."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class ConfirmDialog(QDialog):
    """Asks the user to confirm. The safe choice (cancel) is the default button."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        confirm_text: str = "Confirm",
        danger: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)

        heading = QLabel(title)
        heading.setObjectName("DialogTitle")
        body = QLabel(message)
        body.setObjectName("Muted")
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.PlainText)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button = QPushButton(confirm_text)
        self.confirm_button.setProperty("variant", "danger" if danger else "primary")
        self.confirm_button.setAutoDefault(False)
        self.confirm_button.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.confirm_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(heading)
        layout.addWidget(body)
        layout.addSpacing(6)
        layout.addLayout(buttons)

    @classmethod
    def ask(
        cls,
        parent: QWidget | None,
        title: str,
        message: str,
        confirm_text: str = "Confirm",
        danger: bool = False,
    ) -> bool:
        """Show the dialog and return ``True`` only if the user confirmed."""
        return cls(parent, title, message, confirm_text, danger).exec() == cls.DialogCode.Accepted
