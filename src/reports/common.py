"""Helpers shared by the exporters."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pandas as pd

from src.domain.exceptions import InvalidConfigurationError

# A cell starting with one of these is interpreted as a formula by spreadsheet programs.
FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")


def validate_output_path(path: Path, suffix: str) -> Path:
    """Return ``path`` with the right extension; its folder must already exist."""
    target = path if path.suffix.lower() == suffix else path.with_suffix(suffix)
    if not target.parent.is_dir():
        raise InvalidConfigurationError(f"The folder '{target.parent}' does not exist.")
    if target.exists() and target.is_dir():
        raise InvalidConfigurationError(f"'{target}' is a folder, not a file.")
    return target


def neutralise_formulas(value: Any) -> Any:
    """Prefix text that would be run as a spreadsheet formula (CSV/formula injection)."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def safe_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Copy of ``frame`` in which no text cell can act as a formula."""
    cleaned = frame.copy()
    for column in cleaned.columns:
        dtype = cleaned[column].dtype
        # pandas 3 stores text as its own string dtype; older versions use object.
        if pd.api.types.is_string_dtype(dtype) or pd.api.types.is_object_dtype(dtype):
            cleaned[column] = cleaned[column].map(neutralise_formulas)
    return cleaned
