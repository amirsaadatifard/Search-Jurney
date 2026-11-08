"""Saves screenshots under ``data/screenshots/YYYY-MM-DD/`` and records their metadata."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from src.app.paths import AppPaths
from src.automation.models import ScreenshotPolicy
from src.automation.recorder import SessionRecorder
from src.domain.enums import ScreenshotKind
from src.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

log = get_logger(__name__)

MAX_SLUG_LENGTH: Final = 40
_LABELS: Final[dict[ScreenshotKind, str]] = {
    ScreenshotKind.SEARCH_RESULTS: "search_results",
    ScreenshotKind.TARGET_OPENED: "target_opened",
    ScreenshotKind.SESSION_END: "session_end",
    ScreenshotKind.ERROR: "error",
}


def slugify(text: str) -> str:
    """Lower-case ``text`` into a short, filesystem-safe fragment."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:MAX_SLUG_LENGTH].strip("-") or "keyword"


def screenshot_filename(session_id: int, keyword: str, kind: ScreenshotKind) -> str:
    """e.g. ``session_102_keyword_gold-ring_search_results.png``."""
    return f"session_{session_id}_keyword_{slugify(keyword)}_{_LABELS[kind]}.png"


class ScreenshotService:
    def __init__(
        self, paths: AppPaths, recorder: SessionRecorder, policy: ScreenshotPolicy
    ) -> None:
        self._paths = paths
        self._recorder = recorder
        self._policy = policy

    def destination(self, session_id: int, keyword: str, kind: ScreenshotKind, day: date) -> Path:
        """Path of the file; the name is built only from sanitised parts."""
        folder = self._paths.screenshots_dir_for(day)
        return folder / screenshot_filename(session_id, keyword, kind)

    async def capture(
        self, page: Page, session_id: int, keyword: str, kind: ScreenshotKind
    ) -> dict[str, Any] | None:
        """Take a screenshot if the policy allows it. Never raises for capture problems."""
        if not self._policy.allows(kind):
            return None
        path = self.destination(session_id, keyword, kind, date.today())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = await page.screenshot(type="png")
            path.write_bytes(data)
            size = page.viewport_size
            screenshot_id = self._recorder.add_screenshot(
                session_id,
                kind,
                str(path),
                page.url,
                size["width"] if size else None,
                size["height"] if size else None,
                len(data),
            )
        except Exception:  # a failed screenshot must never fail the session
            log.exception("screenshot_failed", session_id=session_id, kind=kind.value)
            return None
        return {
            "id": screenshot_id,
            "kind": kind.value,
            "path": str(path),
            "file": path.name,
            "size_bytes": len(data),
        }
