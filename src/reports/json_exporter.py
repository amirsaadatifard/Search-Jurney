"""JSON export: metrics, filter and all three tables."""

from __future__ import annotations

import json
from pathlib import Path

from src.reports.common import validate_output_path
from src.services.report_service import ReportData


def export_json(report: ReportData, path: Path) -> Path:
    target = validate_output_path(path, ".json")
    target.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return target
