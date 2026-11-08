"""Safe editing of the ``.env`` file that holds the start-up configuration.

Only known ``SJAT_*`` keys can be written, values may not contain line breaks,
unknown lines and comments are kept untouched, and the file is replaced
atomically so a crash cannot leave a half-written configuration.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from src.app.constants import ENV_PREFIX
from src.domain.exceptions import InvalidConfigurationError

EDITABLE_KEYS: Final = frozenset(
    f"{ENV_PREFIX}{name}"
    for name in (
        "DATA_DIR",
        "DATABASE_FILE",
        "LOGS_DIR",
        "SCREENSHOTS_DIR",
        "SEARCH_PORT",
        "TARGET_PORT",
        "LOG_LEVEL",
    )
)


def update_env_file(path: Path, changes: Mapping[str, str | None]) -> bool:
    """Set (or, with ``None``/empty, remove) keys in ``path``. Returns ``True`` if it changed."""
    for key, value in changes.items():
        if key not in EDITABLE_KEYS:
            raise InvalidConfigurationError(f"'{key}' cannot be changed from the application.")
        if value is not None and any(ch in value for ch in "\r\n\0"):
            raise InvalidConfigurationError(f"The value for {key} contains invalid characters.")

    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    pending = {key: (value or "").strip() for key, value in changes.items()}
    result: list[str] = []
    for line in lines:
        key = line.partition("=")[0].strip()
        if key in pending and not line.lstrip().startswith("#"):
            value = pending.pop(key)
            if value:
                result.append(f"{key}={value}")
            continue
        result.append(line)
    result.extend(f"{key}={value}" for key, value in pending.items() if value)

    new_text = "\n".join(result) + ("\n" if result else "")
    if path.is_file() and path.read_text(encoding="utf-8") == new_text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(new_text)
        os.replace(temp_name, path)
    except OSError:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return True
