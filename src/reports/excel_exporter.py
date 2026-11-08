"""Excel export with the sheets Summary, Sessions, Events and Errors."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from src.app.constants import APP_NAME
from src.reports.common import safe_frame, validate_output_path
from src.services.report_service import ReportData

SHEETS: Final = ("Summary", "Sessions", "Events", "Errors")
HEADER_FILL: Final = PatternFill("solid", fgColor="2F62E0")
HEADER_FONT: Final = Font(bold=True, color="FFFFFF")
MAX_COLUMN_WIDTH: Final = 60
MIN_COLUMN_WIDTH: Final = 10


def summary_frame(report: ReportData) -> pd.DataFrame:
    rows: list[tuple[str, str]] = [
        ("Report", APP_NAME),
        ("Generated", report.generated_at.strftime("%Y-%m-%d %H:%M:%S")),
        ("", ""),
        ("FILTERS", ""),
        *[
            (key.replace("_", " ").capitalize(), value)
            for key, value in report.filter.describe().items()
        ],
        ("", ""),
        ("METRICS", ""),
        *report.metrics.as_rows(),
    ]
    return pd.DataFrame(rows, columns=["Item", "Value"])


def export_excel(report: ReportData, path: Path) -> Path:
    """Write a workbook with four sheets and basic formatting."""
    target = validate_output_path(path, ".xlsx")
    frames = {
        "Summary": summary_frame(report),
        "Sessions": safe_frame(report.sessions),
        "Events": safe_frame(report.events),
        "Errors": safe_frame(report.errors),
    }
    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        for name, frame in frames.items():
            frame.to_excel(writer, sheet_name=name, index=False)
            _format_sheet(writer.sheets[name], frame, is_summary=name == "Summary")
    return target


def _format_sheet(sheet: Worksheet, frame: pd.DataFrame, is_summary: bool) -> None:
    for cell in sheet[1]:
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    sheet.freeze_panes = "A2"
    for index, column in enumerate(frame.columns, start=1):
        longest = max([len(str(column))] + [len(str(v)) for v in frame[column].head(200)])
        width = min(max(longest + 2, MIN_COLUMN_WIDTH), MAX_COLUMN_WIDTH)
        sheet.column_dimensions[get_column_letter(index)].width = width
    if is_summary:
        for row in sheet.iter_rows(min_row=2):
            if row[0].value in {"FILTERS", "METRICS"}:
                row[0].font = Font(bold=True)
    elif frame.shape[0]:
        sheet.auto_filter.ref = sheet.dimensions
