"""Keyword library: CRUD, bulk add, TXT/CSV import and CSV export."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from src.database.models import Keyword
from src.database.repositories import KeywordRepository
from src.database.session import Database
from src.domain.exceptions import InvalidConfigurationError
from src.domain.schemas import KeywordInput, validate_input
from src.logging_config import get_logger

log = get_logger(__name__)

MAX_IMPORT_BYTES: Final = 5 * 1024 * 1024
IMPORT_SUFFIXES: Final = frozenset({".txt", ".csv"})
CSV_COLUMNS: Final = ("keyword", "tags", "notes", "enabled")
TAG_SEPARATOR: Final = ";"
_FALSE_WORDS: Final = frozenset({"0", "false", "no", "n", "off", "disabled"})


@dataclass(slots=True)
class BulkAddResult:
    """Outcome of adding many keywords at once."""

    added: int = 0
    duplicates: int = 0
    invalid: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.added} added"]
        if self.duplicates:
            parts.append(f"{self.duplicates} already existed")
        if self.invalid:
            parts.append(f"{len(self.invalid)} invalid")
        return ", ".join(parts)


class KeywordService:
    def __init__(self, database: Database) -> None:
        self._database = database

    # -- queries ---------------------------------------------------------------- #
    def search(
        self, text: str = "", enabled: bool | None = None, tag: str | None = None
    ) -> list[Keyword]:
        with self._database.session_scope() as session:
            return list(KeywordRepository(session).search(text, enabled, tag))

    def all_tags(self) -> list[str]:
        with self._database.session_scope() as session:
            return KeywordRepository(session).all_tags()

    # -- single-item commands -------------------------------------------------------- #
    def create(self, data: Mapping[str, Any]) -> Keyword:
        values = validate_input(KeywordInput, data)
        with self._database.session_scope() as session:
            repo = KeywordRepository(session)
            if repo.get_by_text(values.text) is not None:
                raise _duplicate(values.text)
            return repo.add(Keyword(**values.model_dump()))

    def update(self, keyword_id: int, data: Mapping[str, Any]) -> Keyword:
        values = validate_input(KeywordInput, data)
        with self._database.session_scope() as session:
            repo = KeywordRepository(session)
            keyword = repo.require(keyword_id)
            existing = repo.get_by_text(values.text)
            if existing is not None and existing.id != keyword_id:
                raise _duplicate(values.text)
            for name, value in values.model_dump().items():
                setattr(keyword, name, value)
            return keyword

    def set_enabled(self, keyword_id: int, enabled: bool) -> None:
        with self._database.session_scope() as session:
            KeywordRepository(session).require(keyword_id).enabled = enabled

    def delete(self, keyword_id: int) -> None:
        with self._database.session_scope() as session:
            repo = KeywordRepository(session)
            repo.delete(repo.require(keyword_id))

    # -- bulk commands ------------------------------------------------------------------- #
    def bulk_add(self, texts: Iterable[str], tags: Sequence[str] = ()) -> BulkAddResult:
        """Add one keyword per non-empty entry, all with the same ``tags``."""
        return self._add_many({"text": text, "tags": list(tags)} for text in texts if text.strip())

    def import_file(self, path: Path) -> BulkAddResult:
        """Import keywords from a ``.txt`` (one per line) or ``.csv`` file."""
        suffix = path.suffix.lower()
        if suffix not in IMPORT_SUFFIXES:
            raise InvalidConfigurationError("Only .txt and .csv files can be imported.")
        if not path.is_file():
            raise InvalidConfigurationError(f"File not found: {path.name}")
        if path.stat().st_size > MAX_IMPORT_BYTES:
            raise InvalidConfigurationError("The file is larger than 5 MB.")
        try:
            content = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InvalidConfigurationError("The file must be UTF-8 encoded text.") from exc

        rows = _parse_csv(content) if suffix == ".csv" else _parse_txt(content)
        result = self._add_many(rows)
        log.info("keywords_imported", file=path.name, added=result.added)
        return result

    def export_csv(self, path: Path) -> int:
        """Write every keyword to ``path``; returns the number of rows written."""
        keywords = self.search()
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(CSV_COLUMNS)
            for keyword in keywords:
                writer.writerow(
                    [
                        keyword.text,
                        TAG_SEPARATOR.join(keyword.tags),
                        keyword.notes,
                        "true" if keyword.enabled else "false",
                    ]
                )
        log.info("keywords_exported", file=path.name, count=len(keywords))
        return len(keywords)

    def _add_many(self, rows: Iterable[Mapping[str, Any]]) -> BulkAddResult:
        result = BulkAddResult()
        with self._database.session_scope() as session:
            repo = KeywordRepository(session)
            seen: set[str] = set()
            for row in rows:
                try:
                    values = validate_input(KeywordInput, row)
                except InvalidConfigurationError:
                    result.invalid.append(str(row.get("text", ""))[:60])
                    continue
                key = values.text.lower()
                if key in seen or repo.get_by_text(values.text) is not None:
                    result.duplicates += 1
                    continue
                seen.add(key)
                repo.add(Keyword(**values.model_dump()))
                result.added += 1
        return result


def _duplicate(text: str) -> InvalidConfigurationError:
    return InvalidConfigurationError(
        "Duplicate keyword.", field_errors={"text": f"The keyword '{text}' already exists."}
    )


def _parse_txt(content: str) -> list[dict[str, Any]]:
    return [{"text": line} for line in content.splitlines() if line.strip()]


def _parse_csv(content: str) -> list[dict[str, Any]]:
    """Accept either our export format (with header) or a plain first-column list."""
    rows = [row for row in csv.reader(content.splitlines()) if row and any(row)]
    if not rows:
        return []
    header = [cell.strip().lower() for cell in rows[0]]
    if "keyword" not in header:
        return [{"text": row[0]} for row in rows]

    index: dict[str, int] = {name: header.index(name) for name in CSV_COLUMNS if name in header}

    def cell(row: list[str], name: str) -> str:
        position = index.get(name)
        return row[position].strip() if position is not None and position < len(row) else ""

    return [
        {
            "text": cell(row, "keyword"),
            "tags": [tag for tag in cell(row, "tags").split(TAG_SEPARATOR) if tag.strip()],
            "notes": cell(row, "notes"),
            "enabled": cell(row, "enabled").lower() not in _FALSE_WORDS,
        }
        for row in rows[1:]
    ]
