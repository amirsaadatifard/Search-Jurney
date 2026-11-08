"""CSV export of the sessions table."""

from __future__ import annotations

from pathlib import Path

from src.reports.common import safe_frame, validate_output_path
from src.services.report_service import ReportData


def export_csv(report: ReportData, path: Path) -> Path:
    """Write the sessions to ``path`` (UTF-8 with BOM so Excel opens it correctly)."""
    target = validate_output_path(path, ".csv")
    safe_frame(report.sessions).to_csv(target, index=False, encoding="utf-8-sig")
    return target
