"""Proxy profiles for controlled network testing."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from src.database.models import ProxyProfile
from src.domain.enums import ProxyStatus
from src.networking.proxy_manager import ProxyTestResult
from src.ui.dialogs.proxy_dialog import ProxyDialog
from src.ui.formatting import PLACEHOLDER, format_datetime
from src.ui.pages.base_page import PageContext
from src.ui.pages.crud_page import CrudPage
from src.ui.widgets.data_table import Column
from src.ui.widgets.status_badge import BadgeTone
from src.ui.workers import TaskRunner

_STATUS: dict[ProxyStatus, tuple[str, BadgeTone]] = {
    ProxyStatus.UNKNOWN: ("Not tested", BadgeTone.NEUTRAL),
    ProxyStatus.REACHABLE: ("Reachable", BadgeTone.SUCCESS),
    ProxyStatus.UNREACHABLE: ("Unreachable", BadgeTone.DANGER),
}
_SECRET_KEYS = ("_password", "_clear_password")


class ProxiesPage(CrudPage[ProxyProfile]):
    entity_name = "proxy profile"
    entity_plural = "proxy profiles"
    empty_icon = "proxies"
    empty_description = (
        "A proxy profile routes the test browser through a proxy you control. "
        "It is optional; scenarios use a direct connection by default."
    )
    supports_toggle = True

    def __init__(self, context: PageContext) -> None:
        super().__init__(
            context,
            "Proxy Profiles",
            "Optional network profiles for controlled test environments. Passwords are kept in the "
            "operating system's credential store, never in the database.",
        )
        self._service = context.container.proxies
        self._tasks = TaskRunner(self)
        self.setup(
            [
                Column("Name", lambda p: p.name, stretch=True),
                Column(
                    "Protocol",
                    lambda p: p.protocol.value,
                    badge=lambda p: BadgeTone.INFO,
                    width=110,
                ),
                Column("Address", lambda p: f"{p.host}:{p.port}", width=200),
                Column("Password", lambda p: "Stored" if p.has_password else PLACEHOLDER, width=90),
                Column(
                    "Status",
                    lambda p: _STATUS[p.last_status][0],
                    badge=lambda p: _STATUS[p.last_status][1],
                    width=120,
                ),
                Column(
                    "Latency",
                    lambda p: (
                        PLACEHOLDER if p.last_latency_ms is None else f"{p.last_latency_ms} ms"
                    ),
                    lambda p: p.last_latency_ms if p.last_latency_ms is not None else -1,
                    width=90,
                ),
                Column(
                    "Last test",
                    lambda p: format_datetime(p.last_checked_at),
                    lambda p: p.last_checked_at,
                    width=140,
                ),
                Column(
                    "Enabled",
                    lambda p: "Yes" if p.enabled else "No",
                    badge=lambda p: BadgeTone.SUCCESS if p.enabled else BadgeTone.NEUTRAL,
                    width=90,
                ),
            ],
            "Search name, host or notes…",
        )
        self.test_button = self.add_row_button("Test connection", self._test_selected)

    # -- hooks ------------------------------------------------------------------ #
    def fetch(self, text: str) -> Sequence[ProxyProfile]:
        return self._service.list(text)

    def edit(self, entity: ProxyProfile | None) -> bool:
        def submit(values: dict[str, Any]) -> ProxyProfile:
            fields = {k: v for k, v in values.items() if k not in _SECRET_KEYS}
            password, clear = values["_password"], values["_clear_password"]
            if entity is None:
                return self._service.create(fields, password)
            return self._service.update(entity.id, fields, password, clear)

        dialog = ProxyDialog(
            submit,
            entity,
            self._service.credentials_available,
            self._service.credentials_description,
            self.window(),
        )
        if not dialog.exec():
            return False
        self.context.notifier.success("Proxy profile saved.")
        return True

    def describe(self, entity: ProxyProfile) -> str:
        return entity.name

    def remove(self, entity: ProxyProfile) -> None:
        self._service.delete(entity.id)

    def is_enabled(self, entity: ProxyProfile) -> bool:
        return entity.enabled

    def set_enabled(self, entity: ProxyProfile, enabled: bool) -> None:
        self._service.set_enabled(entity.id, enabled)

    # -- connection test -------------------------------------------------------------- #
    def _test_selected(self) -> None:
        selected = self.selected()
        if len(selected) == 1:
            self.test_profile(selected[0])

    def test_profile(
        self, profile: ProxyProfile, on_finished: Callable[[], None] | None = None
    ) -> None:
        """Test on a worker thread (a dead host can take seconds) and refresh the table."""
        self.test_button.setEnabled(False)
        self.context.notifier.info(f"Testing '{profile.name}'…")

        def done(result: object) -> None:
            assert isinstance(result, ProxyTestResult)
            self.test_button.setEnabled(True)
            message = f"{profile.name}: {result.detail}"
            if result.ok:
                latency = f" ({result.latency_ms} ms)" if result.latency_ms is not None else ""
                self.context.notifier.success(message + latency)
            else:
                self.context.notifier.error(message)
            self.refresh()
            if on_finished:
                on_finished()

        def failed(message: str) -> None:
            self.test_button.setEnabled(True)
            self.context.notifier.error(message)
            if on_finished:
                on_finished()

        self._tasks.submit(lambda: self._service.test_connection(profile.id), done, failed)
