"""KPI card used on the dashboard and report pages."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

_PLACEHOLDER = "—"


class MetricCard(QFrame):
    """Shows one headline number with a label and an optional hint line."""

    def __init__(
        self, label: str, value: str = _PLACEHOLDER, hint: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MetricCard")
        self.setMinimumHeight(96)

        self._label = QLabel(label)
        self._label.setObjectName("MetricLabel")
        self._value = QLabel(value)
        self._value.setObjectName("MetricValue")
        self._value.setWordWrap(True)
        self._hint = QLabel(hint)
        self._hint.setObjectName("MetricHint")
        self._hint.setVisible(bool(hint))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(2)
        layout.addWidget(self._label)
        layout.addWidget(self._value)
        layout.addWidget(self._hint)
        layout.addStretch(1)

    def set_value(self, value: str | None, hint: str | None = None) -> None:
        """Update the number; ``None`` shows a neutral placeholder."""
        self._value.setText(_PLACEHOLDER if value is None else value)
        if hint is not None:
            self._hint.setText(hint)
            self._hint.setVisible(bool(hint))

    def value_text(self) -> str:
        return self._value.text()
