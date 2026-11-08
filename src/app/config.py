"""Process-level configuration read from environment variables / ``.env``.

This covers what must be known *before* the database is available (where the
data directory is, which port the bundled sample website listens on). User
preferences that can be edited in the GUI are stored in the database instead.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.app.constants import DEFAULT_DATA_DIR_NAME, DEFAULT_TARGET_PORT, ENV_PREFIX

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

_MIN_USER_PORT = 1024
_MAX_PORT = 65535


class AppConfig(BaseModel):
    """Immutable startup configuration."""

    model_config = ConfigDict(frozen=True)

    data_dir: Path = Path(DEFAULT_DATA_DIR_NAME)
    target_port: int = Field(DEFAULT_TARGET_PORT, ge=_MIN_USER_PORT, le=_MAX_PORT)
    log_level: LogLevel = "INFO"
    # Optional overrides; by default everything lives inside ``data_dir``.
    database_file: Path | None = None
    logs_dir: Path | None = None
    screenshots_dir: Path | None = None

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None, env_file: Path | None = None
    ) -> AppConfig:
        """Build the configuration from ``environ``, falling back to ``env_file``.

        Real environment variables take precedence over values in the file.
        """
        values: dict[str, str] = {}
        if env_file is not None and env_file.is_file():
            values.update(_parse_env_file(env_file))
        values.update(os.environ if environ is None else environ)

        fields: dict[str, str] = {}
        for name in cls.model_fields:
            raw = values.get(f"{ENV_PREFIX}{name.upper()}")
            if raw is not None and raw.strip():
                fields[name] = raw.strip()
        if "log_level" in fields:
            fields["log_level"] = fields["log_level"].upper()
        return cls.model_validate(fields)


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` file; comments and blank lines are ignored."""
    parsed: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        parsed[key.strip()] = value.strip().strip("\"'")
    return parsed
