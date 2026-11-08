"""Display names and badge colours for session statuses."""

from __future__ import annotations

from typing import Final

from src.domain.enums import SessionStatus
from src.ui.widgets.status_badge import BadgeTone

STATUS_LABELS: Final[dict[SessionStatus, str]] = {
    SessionStatus.RUNNING: "Running",
    SessionStatus.COMPLETED: "Completed",
    SessionStatus.TARGET_NOT_FOUND: "Not found",
    SessionStatus.FAILED: "Failed",
    SessionStatus.STOPPED: "Stopped",
}

STATUS_TONES: Final[dict[SessionStatus, BadgeTone]] = {
    SessionStatus.RUNNING: BadgeTone.INFO,
    SessionStatus.COMPLETED: BadgeTone.SUCCESS,
    SessionStatus.TARGET_NOT_FOUND: BadgeTone.WARNING,
    SessionStatus.FAILED: BadgeTone.DANGER,
    SessionStatus.STOPPED: BadgeTone.NEUTRAL,
}
