"""Dialog for creating/editing a proxy profile."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QCheckBox, QLabel, QLineEdit, QPlainTextEdit, QSpinBox, QWidget

from src.database.models import ProxyProfile
from src.domain.enums import ProxyProtocol
from src.ui.widgets.form_dialog import FormDialog, Submit, make_combo

CONTROLLED_USE_NOTICE = (
    "Proxy profiles are for controlled network testing only. With a proxy, the browser can no "
    "longer be fenced at the network level: redirects that leave the approved sites are "
    "detected and stop the session, but the proxy may already have received that request."
)


class ProxyDialog(FormDialog):
    """Password handling: the field is never pre-filled and never shows stored secrets.

    Leaving it empty keeps the stored password; *Remove stored password* deletes it.
    """

    def __init__(
        self,
        submit: Submit,
        profile: ProxyProfile | None,
        credentials_available: bool,
        credentials_description: str,
        parent: QWidget | None,
    ) -> None:
        super().__init__("Edit proxy profile" if profile else "New proxy profile", submit, parent)
        notice = QLabel(CONTROLLED_USE_NOTICE)
        notice.setObjectName("FieldHint")
        notice.setWordWrap(True)
        self._form.addWidget(notice)

        self.name_input = QLineEdit(profile.name if profile else "")
        self.protocol_input = make_combo(
            [(p.value, p) for p in ProxyProtocol],
            profile.protocol if profile else ProxyProtocol.HTTP,
        )
        self.host_input = QLineEdit(profile.host if profile else "")
        self.host_input.setPlaceholderText("proxy.example.test or 10.0.0.5")
        self.port_input = QSpinBox()
        self.port_input.setRange(0, 99999)  # wider than valid so the service explains the rule
        self.port_input.setValue(profile.port if profile else 8080)
        self.username_input = QLineEdit(profile.username if profile else "")
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText(
            "(unchanged)" if profile and profile.has_password else "Optional"
        )
        self.password_input.setEnabled(credentials_available)
        self.remove_password_input = QCheckBox("Remove the stored password")
        self.remove_password_input.setVisible(bool(profile and profile.has_password))
        self.enabled_input = QCheckBox("Enabled")
        self.enabled_input.setChecked(profile.enabled if profile else True)
        self.notes_input = QPlainTextEdit(profile.notes if profile else "")
        self.notes_input.setFixedHeight(64)

        self.add_field("name", "Name", self.name_input)
        self.add_row(
            [
                ("protocol", "Protocol", self.protocol_input),
                ("port", "Port", self.port_input),
            ]
        )
        self.add_field("host", "Host", self.host_input)
        self.add_row(
            [
                ("username", "User name (optional)", self.username_input),
                ("password", "Password (optional)", self.password_input),
            ]
        )
        storage = QLabel(
            credentials_description
            if credentials_available
            else "Passwords cannot be saved: " + credentials_description
        )
        storage.setObjectName("FieldHint")
        storage.setWordWrap(True)
        self._form.addWidget(storage)
        self._form.addWidget(self.remove_password_input)
        self.add_field("notes", "Notes", self.notes_input)
        self.add_checkbox("enabled", self.enabled_input)
        self.finish()

    def values(self) -> dict[str, Any]:
        """Profile fields plus the secret handling flags consumed by the page's submit function."""
        return {
            "name": self.name_input.text(),
            "protocol": self.protocol_input.currentData(),
            "host": self.host_input.text(),
            "port": self.port_input.value(),
            "username": self.username_input.text(),
            "enabled": self.enabled_input.isChecked(),
            "notes": self.notes_input.toPlainText(),
            "_password": self.password_input.text() or None,
            "_clear_password": self.remove_password_input.isChecked(),
        }
