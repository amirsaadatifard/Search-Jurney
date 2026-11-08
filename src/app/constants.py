"""Application-wide constants.

Only values that are genuinely fixed belong here. Anything a user may change
lives in :mod:`src.app.config` (process level) or in the ``application_settings``
table (user level).
"""

from __future__ import annotations

from typing import Final

APP_NAME: Final = "Search Journey Automation & Web Interaction Testing Platform"
APP_SHORT_NAME: Final = "Search Journey Lab"
APP_SLUG: Final = "sjat"
APP_VERSION: Final = "0.1.0"
APP_ORGANIZATION: Final = "University Capstone Project"
APP_DESCRIPTION: Final = (
    "Design and implementation of an automated system for simulating search journeys "
    "and controlled website interaction for web testing and behavioral analysis."
)

ENV_PREFIX: Final = "SJAT_"

# Local controlled servers ------------------------------------------------- #
LOOPBACK_HOST: Final = "127.0.0.1"
DEFAULT_TARGET_PORT: Final = 8766

# Storage ------------------------------------------------------------------ #
DEFAULT_DATA_DIR_NAME: Final = "data"
DATABASE_FILENAME: Final = "sjat.db"
LOG_FILENAME: Final = "application.log"
LOG_MAX_BYTES: Final = 2 * 1024 * 1024
LOG_BACKUP_COUNT: Final = 5
UI_LOG_BUFFER_SIZE: Final = 2000

# Automation defaults ------------------------------------------------------ #
DEFAULT_MIN_DWELL_SECONDS: Final = 5
DEFAULT_MAX_DWELL_SECONDS: Final = 15
DEFAULT_TIMEOUT_SECONDS: Final = 30
MAX_CONCURRENT_SESSIONS: Final = 1

# Search provider (Google Custom Search JSON API) --------------------------- #
DEFAULT_SEARCH_RESULTS: Final = 10  # the API returns at most ten results per page
MAX_SEARCH_RESULTS: Final = 100  # and never more than one hundred for one query

# Main window -------------------------------------------------------------- #
WINDOW_MIN_WIDTH: Final = 1180
WINDOW_MIN_HEIGHT: Final = 720
WINDOW_DEFAULT_WIDTH: Final = 1360
WINDOW_DEFAULT_HEIGHT: Final = 840
SIDEBAR_WIDTH: Final = 244
