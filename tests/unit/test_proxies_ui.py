"""Proxy page and dialog."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest
from PySide6.QtWidgets import QApplication, QLineEdit
from pytestqt.qtbot import QtBot

from src.app.container import AppContainer
from src.domain.enums import ProxyProtocol, ProxyStatus
from src.networking.credentials import InMemoryCredentialStore, proxy_secret_key
from src.ui.dialogs.proxy_dialog import ProxyDialog
from src.ui.main_window import MainWindow
from src.ui.navigation import PageKey
from src.ui.pages.proxies_page import ProxiesPage
from src.ui.theme import ThemeManager, ThemeMode
from src.ui.widgets.confirm_dialog import ConfirmDialog
from tests.helpers.fake_proxy import FakeHttpProxy


@pytest.fixture
def window(container: AppContainer) -> Iterator[MainWindow]:
    app = cast(QApplication, QApplication.instance())
    main_window = MainWindow(container, ThemeManager(app, ThemeMode.LIGHT))
    main_window.show()
    yield main_window
    main_window.close()
    main_window.deleteLater()


def fill(dialog: ProxyDialog, **fields: object) -> None:
    dialog.name_input.setText(str(fields.get("name", "Lab proxy")))
    dialog.host_input.setText(str(fields.get("host", "10.0.0.5")))
    dialog.port_input.setValue(int(fields.get("port", 3128)))  # type: ignore[call-overload]
    dialog.username_input.setText(str(fields.get("username", "")))
    dialog.password_input.setText(str(fields.get("password", "")))


def make_dialog(container: AppContainer, qtbot: QtBot, profile=None):  # type: ignore[no-untyped-def]
    def submit(values: dict[str, object]) -> object:
        fields = {k: v for k, v in values.items() if not k.startswith("_")}
        if profile is None:
            return container.proxies.create(fields, cast("str | None", values["_password"]))
        password = cast("str | None", values["_password"])
        return container.proxies.update(
            profile.id, fields, password, bool(values["_clear_password"])
        )

    dialog = ProxyDialog(
        submit,
        profile,
        container.proxies.credentials_available,
        container.proxies.credentials_description,
        None,
    )
    qtbot.addWidget(dialog)
    return dialog


def test_dialog_validates_and_saves_with_a_secret(container: AppContainer, qtbot: QtBot) -> None:
    dialog = make_dialog(container, qtbot)
    assert dialog.password_input.echoMode() == QLineEdit.EchoMode.Password
    fill(dialog, host="http://bad", port=0)
    dialog.save()
    assert dialog.error_for("host") != "" and dialog.error_for("port") != ""
    assert container.proxies.list() == []

    fill(dialog, host="10.0.0.5", port=3128, username="user", password="s3cret")
    dialog.save()
    saved = container.proxies.list()[0]
    assert saved.protocol is ProxyProtocol.HTTP and saved.has_password
    assert container.proxies.get_password(saved.id) == "s3cret"


def test_editing_never_prefills_the_password(container: AppContainer, qtbot: QtBot) -> None:
    profile = container.proxies.create(
        {"name": "P", "host": "h.test", "port": 80, "username": "u"}, "s3cret"
    )
    dialog = make_dialog(container, qtbot, profile)
    assert (
        dialog.password_input.text() == ""
        and dialog.password_input.placeholderText() == "(unchanged)"
    )
    assert "s3cret" not in "".join(w.text() for w in dialog.findChildren(QLineEdit))
    assert (
        dialog.remove_password_input.isHidden() is False
        or dialog.remove_password_input.isVisibleTo(dialog)
    )

    dialog.name_input.setText("P renamed")
    dialog.save()  # empty password field keeps the stored one
    assert container.proxies.get_password(profile.id) == "s3cret"

    dialog2 = make_dialog(container, qtbot, container.proxies.list()[0])
    dialog2.remove_password_input.setChecked(True)
    dialog2.save()
    assert (
        container.proxies.get_password(profile.id) is None
        and not container.proxies.list()[0].has_password
    )


def test_dialog_explains_when_no_secure_store_exists(container: AppContainer, qtbot: QtBot) -> None:
    dialog = ProxyDialog(
        lambda v: v, None, False, "No secure credential storage is available.", None
    )
    qtbot.addWidget(dialog)
    assert not dialog.password_input.isEnabled()
    texts = " ".join(label.text() for label in dialog.findChildren(type(dialog.general_error)))
    assert "Passwords cannot be saved" in texts and "controlled network testing" in texts


def test_page_lists_profiles_and_tests_the_connection(
    window: MainWindow, container: AppContainer, qtbot: QtBot
) -> None:
    proxy = FakeHttpProxy()
    try:
        container.proxies.create({"name": "Local fake", "host": "127.0.0.1", "port": proxy.port})
        window.show_page(PageKey.PROXIES)
        page = cast(ProxiesPage, window.page(PageKey.PROXIES))
        assert page.table.row_count() == 1
        assert page.table.cell_text(0, 3) == "—" and page.table.cell_text(0, 4) == "Not tested"
        assert not page.test_button.isEnabled()

        page.table.selectRow(0)
        finished: list[bool] = []
        page.test_profile(page.table.object_at_position(0), lambda: finished.append(True))
        assert not page.test_button.isEnabled()  # busy while the test runs on a worker
        qtbot.waitUntil(lambda: bool(finished))
        assert page.table.cell_text(0, 4) == "Reachable" and page.table.cell_text(0, 5).endswith(
            " ms"
        )
        assert window.notifier.toast.tone() == "success" and page.test_button.isEnabled()
        assert container.proxies.list()[0].last_status is ProxyStatus.REACHABLE
    finally:
        proxy.stop()


def test_unreachable_proxy_is_reported_as_an_error(
    window: MainWindow, container: AppContainer, qtbot: QtBot
) -> None:
    container.proxies.create({"name": "Nowhere", "host": "no-such-host.invalid", "port": 3128})
    window.show_page(PageKey.PROXIES)
    page = cast(ProxiesPage, window.page(PageKey.PROXIES))
    finished: list[bool] = []
    page.test_profile(page.table.object_at_position(0), lambda: finished.append(True))
    qtbot.waitUntil(lambda: bool(finished), timeout=15_000)
    assert page.table.cell_text(0, 4) == "Unreachable" and window.notifier.toast.tone() == "danger"


def test_deleting_a_profile_removes_its_secret(
    window: MainWindow, container: AppContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = container.proxies.create(
        {"name": "Gone", "host": "h.test", "port": 80, "username": "u"}, "pw"
    )
    monkeypatch.setattr(ConfirmDialog, "ask", classmethod(lambda *_a, **_k: True))
    window.show_page(PageKey.PROXIES)
    page = cast(ProxiesPage, window.page(PageKey.PROXIES))
    page.table.selectRow(0)
    page.delete_button.click()
    assert page.table.row_count() == 0
    store = container.credentials
    assert (
        isinstance(store, InMemoryCredentialStore)
        and proxy_secret_key(profile.id) not in store._secrets
    )
