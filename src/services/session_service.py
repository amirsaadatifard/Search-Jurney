"""Session history: search, details and clean-up."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from src.app.paths import AppPaths
from src.database.models import AutomationSession
from src.database.repositories import SessionFilter, SessionRepository
from src.database.session import Database
from src.domain.enums import SessionStatus
from src.domain.exceptions import AppError, EntityNotFoundError
from src.logging_config import get_logger

log = get_logger(__name__)

DEFAULT_LIMIT = 2000


class SessionService:
    def __init__(self, database: Database, paths: AppPaths) -> None:
        self._database = database
        self._paths = paths

    def search(
        self,
        text: str = "",
        status: SessionStatus | None = None,
        scenario_id: int | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[AutomationSession]:
        """Newest sessions first."""
        criteria = SessionFilter(text=text, status=status, scenario_id=scenario_id)
        with self._database.session_scope() as session:
            return list(SessionRepository(session).search(criteria, limit=limit))

    def get_details(self, session_id: int) -> AutomationSession:
        """A session with its events and screenshots loaded."""
        with self._database.session_scope() as session:
            record = SessionRepository(session).get_with_details(session_id)
        if record is None:
            raise EntityNotFoundError(f"Session #{session_id} does not exist.")
        return record

    def delete(self, session_ids: Sequence[int]) -> int:
        """Delete sessions and their screenshot files. Running sessions are refused."""
        removed = 0
        files: list[Path] = []
        with self._database.session_scope() as session:
            repo = SessionRepository(session)
            for session_id in session_ids:
                record = repo.get_with_details(session_id)
                if record is None:
                    continue
                if record.status is SessionStatus.RUNNING:
                    raise AppError("A running session cannot be deleted. Stop the run first.")
                files.extend(Path(shot.file_path) for shot in record.screenshots)
                repo.delete(record)
                removed += 1
        for path in files:
            self._delete_screenshot(path)
        log.info("sessions_deleted", count=removed)
        return removed

    def _delete_screenshot(self, path: Path) -> None:
        """Remove a screenshot file, but only inside the screenshots folder."""
        try:
            resolved = path.resolve()
            if self._paths.screenshots_dir.resolve() in resolved.parents and resolved.is_file():
                resolved.unlink()
        except OSError:
            log.warning("screenshot_delete_failed", file=str(path))
