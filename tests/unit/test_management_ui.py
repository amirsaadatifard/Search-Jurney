"""Management pages, dialogs and the reusable table / toast widgets."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog
from pytestqt.qtbot import QtBot

from src.app.container import AppContainer
from src.database.models import Scenario
from src.domain.enums import Environment
from src.services.first_run_service import DEMO_KEYWORDS, DEMO_SCENARIO_NAME, DEMO_SITE_NAME
from src.ui.dialogs.browser_profile_dialog import BrowserProfileDialog
from src.ui.dialogs.keyword_dialogs import BulkKeywordDialog, KeywordDialog, parse_tags
from src.ui.dialogs.scenario_dialog import ScenarioDialog
from src.ui.dialogs.target_site_dialog import TargetSiteDialog
from src.ui.main_window import MainWindow
from src.ui.navigation import PageKey
from src.ui.pages.browser_profiles_page import BrowserProfilesPage
from src.ui.pages.keywords_page import KeywordsPage
from src.ui.pages.scenarios_page import ScenariosPage
from src.ui.pages.targets_page import TargetsPage
from src.ui.theme import ThemeManager, ThemeMode
from src.ui.widgets.confirm_dialog import ConfirmDialog
from src.ui.widgets.data_table import Column, DataTable
from src.ui.widgets.form_dialog import select_combo_data
from src.ui.widgets.status_badge import BadgeTone


@pytest.fixture
def window(qtbot: QtBot, container: AppContainer) -> Iterator[MainWindow]:
    app = cast(QApplication, QApplication.instance())
    main_window = MainWindow(container, ThemeManager(app, ThemeMode.LIGHT))
    qtbot.addWidget(main_window)
    main_window.show()
    yield main_window
    main_window.close()


@pytest.fixture
def confirm_yes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Auto-confirm every ConfirmDialog and record the titles that were asked."""
    asked: list[str] = []

    def ask(_cls: Any, _parent: Any, title: str, *_args: Any, **_kwargs: Any) -> bool:
        asked.append(title)
        return True

    monkeypatch.setattr(ConfirmDialog, "ask", classmethod(ask))
    return asked


def open_page(window: MainWindow, key: PageKey) -> Any:
    window.show_page(key)
    return window.page(key)


# -- widgets -------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Row:
    id: int
    name: str
    count: int


def test_data_table_sorts_by_key_and_reports_selection(qtbot: QtBot) -> None:
    app = cast(QApplication, QApplication.instance())
    columns: list[Column[_Row]] = [
        Column("Name", lambda row: row.name),
        Column("Count", lambda row: str(row.count), sort_key=lambda row: row.count),
        Column("State", lambda row: "On", badge=lambda row: BadgeTone.SUCCESS),
    ]
    table: DataTable[_Row] = DataTable(ThemeManager(app, ThemeMode.LIGHT), columns)
    qtbot.addWidget(table)
    table.set_rows([_Row(1, "beta", 10), _Row(2, "Alpha", 9), _Row(3, "gamma", 100)])
    assert [table.cell_text(row, 0) for row in range(3)] == ["Alpha", "beta", "gamma"]

    table.sortByColumn(1, Qt.SortOrder.DescendingOrder)  # numeric, not "9" > "100"
    assert [table.cell_text(row, 1) for row in range(3)] == ["100", "10", "9"]

    with qtbot.waitSignal(table.selection_changed):
        table.selectRow(0)
    assert table.selected_objects() == [_Row(3, "gamma", 100)]
    table.set_rows([_Row(3, "gamma", 1), _Row(4, "z", 5)])
    assert [row.id for row in table.selected_objects()] == [3]  # selection survives reload
    table.grab()  # exercises the badge delegate's paint path


def test_toast_shows_message_and_tone(window: MainWindow) -> None:
    window.notifier.error("Something failed")
    assert window.notifier.toast.isVisible()
    assert window.notifier.toast.message() == "Something failed"
    assert window.notifier.toast.tone() == "danger"
    window.notifier.success("Saved")
    assert window.notifier.toast.tone() == "success"


def test_confirm_dialog_defaults_to_cancel(qtbot: QtBot) -> None:
    dialog = ConfirmDialog(None, "Delete?", "Gone forever.", "Delete", danger=True)
    qtbot.addWidget(dialog)
    assert dialog.cancel_button.isDefault()
    assert dialog.confirm_button.property("variant") == "danger"
    dialog.confirm_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted


# -- scenarios -------------------------------------------------------------------- #
def test_scenarios_page_lists_filters_and_toggles(window: MainWindow) -> None:
    page: ScenariosPage = open_page(window, PageKey.SCENARIOS)
    assert page.table.row_count() == 1
    assert page.table.cell_text(0, 0) == DEMO_SCENARIO_NAME
    assert page.table.cell_text(0, 3) == "#4"
    assert page.footer.text() == "1 scenario"
    assert not page.edit_button.isEnabled()

    page.table.selectRow(0)
    assert page.edit_button.isEnabled() and page.toggle_button.text() == "Disable"
    page.toggle_button.click()
    assert page.table.cell_text(0, 6) == "Disabled"
    assert page.toggle_button.text() == "Enable"

    select_combo_data(page.status_filter, True)
    assert page.table.row_count() == 0
    assert page.footer.text() == "0 scenarios match the current filters"
    select_combo_data(page.status_filter, None)
    assert page.table.row_count() == 1


def test_scenario_duplicate_and_delete_from_page(
    window: MainWindow, confirm_yes: list[str]
) -> None:
    page: ScenariosPage = open_page(window, PageKey.SCENARIOS)
    page.table.selectRow(0)
    page.duplicate_button.click()
    assert page.table.row_count() == 2
    assert "Duplicated" in window.notifier.toast.message()

    page.table.selectAll()
    page.delete_button.click()
    assert confirm_yes == ["Delete scenarios?"]
    assert page.table.row_count() == 0
    assert page._stack.currentIndex() == 1  # empty state


def test_scenario_dialog_shows_field_errors_then_saves(
    qtbot: QtBot, container: AppContainer
) -> None:
    dialog = ScenarioDialog(
        container.scenarios.create, container.scenarios.form_options(), None, None
    )
    qtbot.addWidget(dialog)
    dialog.min_dwell_input.setValue(30)
    dialog.max_dwell_input.setValue(10)
    dialog.iterations_input.setValue(0)
    dialog.save()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.error_for("name") == "This field is required."
    assert dialog.error_for("keyword") == "This field is required."
    assert "must not be less" in dialog.error_for("max_dwell_seconds")
    assert dialog.error_for("iterations") != ""
    assert dialog.name_input.property("invalid") == "true"

    dialog.name_input.setText("Negative test")
    dialog.keyword_input.setCurrentText("unknown thing")
    dialog.max_dwell_input.setValue(40)
    dialog.iterations_input.setValue(2)
    dialog.save()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.error_for("name") == ""
    saved = cast(Scenario, dialog.saved)
    assert (saved.name, saved.iterations) == ("Negative test", 2)


def test_scenario_dialog_prefills_when_editing(qtbot: QtBot, container: AppContainer) -> None:
    scenario = container.scenarios.search()[0]
    dialog = ScenarioDialog(
        lambda values: values, container.scenarios.form_options(), scenario, None
    )
    qtbot.addWidget(dialog)
    values = dialog.values()
    assert values["name"] == DEMO_SCENARIO_NAME
    assert values["keyword"] == scenario.keyword
    assert values["target_site_id"] == scenario.target_site_id
    assert values["min_dwell_seconds"] == 8  # the demo scenario's own dwell
    assert not hasattr(dialog, "position_input")  # no invented rank can be configured any more
    dialog.scroll_input.setChecked(False)
    assert not dialog.max_scroll_input.isEnabled()


def test_scenario_page_edit_flow(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_exec(dialog: ScenarioDialog) -> int:
        dialog.name_input.setText("Renamed scenario")
        dialog.save()
        return dialog.result()

    monkeypatch.setattr(ScenarioDialog, "exec", fake_exec)
    page: ScenariosPage = open_page(window, PageKey.SCENARIOS)
    page.table.selectRow(0)
    page.edit_button.click()
    assert page.table.cell_text(0, 0) == "Renamed scenario"
    assert "Renamed scenario" in window.notifier.toast.message()


# -- keywords -------------------------------------------------------------------- #
def test_keywords_page_search_tag_filter_and_delete(
    window: MainWindow, confirm_yes: list[str]
) -> None:
    page: KeywordsPage = open_page(window, PageKey.KEYWORDS)
    assert page.table.row_count() == len(DEMO_KEYWORDS)
    assert page.tag_filter.count() > 1

    select_combo_data(page.tag_filter, "article")
    assert page.table.row_count() == 2
    select_combo_data(page.tag_filter, None)

    page.search_box.setText("ceramic")
    page.refresh()
    assert page.table.row_count() == 2

    page.table.selectRow(0)
    page.delete_button.click()
    assert confirm_yes == ["Delete keyword?"]
    assert page.table.row_count() == 1


def test_keyword_dialogs(qtbot: QtBot, container: AppContainer) -> None:
    assert parse_tags(" a, b ,,c ") == ["a", "b", "c"]
    dialog = KeywordDialog(container.keywords.create, None, None)
    qtbot.addWidget(dialog)
    dialog.text_input.setText(DEMO_KEYWORDS[0][0].upper())
    dialog.save()
    assert "already exists" in dialog.error_for("text")
    dialog.text_input.setText("brand new keyword")
    dialog.tags_input.setText("x, y")
    dialog.save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert container.keywords.search(text="brand new")[0].tags == ["x", "y"]

    bulk = BulkKeywordDialog(container.keywords.bulk_add, None)
    qtbot.addWidget(bulk)
    bulk.lines_input.setPlainText("one\ntwo\n\none\n")
    bulk.tags_input.setText("bulk")
    bulk.save()
    assert bulk.result_summary is not None
    assert (bulk.result_summary.added, bulk.result_summary.duplicates) == (2, 1)


def test_keyword_import_and_export_from_page(window: MainWindow, tmp_path: Path) -> None:
    page: KeywordsPage = open_page(window, PageKey.KEYWORDS)
    source = tmp_path / "more.txt"
    source.write_text("imported one\nimported two\n", encoding="utf-8")
    page.import_from(source)
    assert page.table.row_count() == len(DEMO_KEYWORDS) + 2
    assert "2 added" in window.notifier.toast.message()

    page.export_to(tmp_path / "out.csv")
    assert (tmp_path / "out.csv").read_text(encoding="utf-8-sig").startswith("keyword,tags,notes")

    page.import_from(tmp_path / "missing.txt")
    assert window.notifier.toast.tone() == "danger"


# -- target sites -------------------------------------------------------------------- #
def test_target_dialog_blocks_search_engines_and_non_local_hosts(
    qtbot: QtBot, container: AppContainer
) -> None:
    dialog = TargetSiteDialog(container.target_sites.create, None, None)
    qtbot.addWidget(dialog)
    dialog.name_input.setText("Search engine")
    dialog.url_input.setText("https://www.google.com")
    select_combo_data(dialog.environment_input, Environment.STAGING)
    dialog.save()
    assert "public search engine" in dialog.error_for("base_url")

    dialog.url_input.setText("https://staging.shop.test")
    select_combo_data(dialog.environment_input, Environment.LOCAL)
    dialog.save()
    assert "loopback" in dialog.error_for("base_url")

    select_combo_data(dialog.environment_input, Environment.STAGING)
    dialog.save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert not container.safety.is_allowed("https://staging.shop.test/")  # not approved yet


def test_targets_page_approval_requires_confirmation(
    window: MainWindow, container: AppContainer, confirm_yes: list[str]
) -> None:
    container.target_sites.create(
        {
            "display_name": "A staging",
            "base_url": "https://staging.shop.test",
            "environment": "STAGING",
        }
    )
    page: TargetsPage = open_page(window, PageKey.TARGETS)
    assert page.table.row_count() == 2
    page.table.selectRow(0)
    assert page.approve_button.text() == "Approve"
    page.approve_button.click()
    assert confirm_yes == ["Approve for automation?"]
    assert container.safety.is_allowed("https://staging.shop.test/")
    assert page.approve_button.text() == "Revoke approval"

    page.approve_button.click()  # revoking needs no confirmation
    assert confirm_yes == ["Approve for automation?"]
    assert not container.safety.is_allowed("https://staging.shop.test/")


def test_deleting_a_site_in_use_reports_an_error(
    window: MainWindow, confirm_yes: list[str]
) -> None:
    page: TargetsPage = open_page(window, PageKey.TARGETS)
    assert page.table.cell_text(0, 0) == DEMO_SITE_NAME
    page.table.selectRow(0)
    page.delete_button.click()
    assert page.table.row_count() == 1
    assert window.notifier.toast.tone() == "danger"
    assert "used by 1 scenario" in window.notifier.toast.message()


# -- browser profiles -------------------------------------------------------------------- #
def test_browser_profiles_page_and_dialog(
    window: MainWindow, qtbot: QtBot, container: AppContainer
) -> None:
    dialog = BrowserProfileDialog(container.browser_profiles.create, None, None)
    qtbot.addWidget(dialog)
    dialog.name_input.setText("Tiny")
    dialog.width_input.setValue(100)
    dialog.timezone_input.setText("Nowhere/Land")
    dialog.save()
    assert dialog.error_for("viewport_width") != ""
    assert dialog.error_for("timezone") != ""
    dialog.width_input.setValue(1024)
    dialog.timezone_input.setText("")
    dialog.headless_input.setChecked(True)
    dialog.save()
    assert dialog.result() == QDialog.DialogCode.Accepted

    page: BrowserProfilesPage = open_page(window, PageKey.BROWSER_PROFILES)
    assert page.table.row_count() == 2
    page.search_box.setText("tiny")
    page.refresh()
    assert (page.table.cell_text(0, 1), page.table.cell_text(0, 2)) == ("1024 × 800", "Headless")
