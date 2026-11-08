"""API key and search-engine id for the Google search API.

The API key is a secret: it lives in the operating system's credential store,
never in the database, logs, exports or the GUI. The search-engine id is not
secret and is kept in the settings. For automated set-ups both can also come
from the environment (``SJAT_GOOGLE_API_KEY`` / ``SJAT_GOOGLE_ENGINE_ID``);
a value saved in the application wins over the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from src.domain.exceptions import InvalidConfigurationError
from src.logging_config import get_logger
from src.networking.credentials import CredentialStore
from src.services.settings_service import SettingsService

log = get_logger(__name__)

API_KEY_SECRET: Final = "google-api-key"
SETTING_ENGINE_ID: Final = "google.engine_id"
ENV_API_KEY: Final = "SJAT_GOOGLE_API_KEY"
ENV_ENGINE_ID: Final = "SJAT_GOOGLE_ENGINE_ID"
MIN_KEY_LENGTH: Final = 10
MAX_KEY_LENGTH: Final = 200
MAX_ENGINE_ID_LENGTH: Final = 100


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    """What is configured (never the key itself)."""

    api_key_set: bool
    engine_id: str
    api_key_source: str  # "stored", "environment" or "none"

    @property
    def complete(self) -> bool:
        return self.api_key_set and bool(self.engine_id)


class GoogleCredentialManager:
    def __init__(self, credentials: CredentialStore, settings: SettingsService) -> None:
        self._credentials = credentials
        self._settings = settings

    def pair(self) -> tuple[str | None, str | None]:
        """``(api_key, engine_id)`` as currently configured (either may be ``None``)."""
        stored = (
            self._credentials.get_secret(API_KEY_SECRET) if self._credentials.available else None
        )
        key = stored or os.environ.get(ENV_API_KEY) or None
        engine = str(self._settings.get(SETTING_ENGINE_ID, "") or "") or os.environ.get(
            ENV_ENGINE_ID, ""
        )
        return key, engine or None

    def status(self) -> CredentialStatus:
        stored = bool(self._credentials.available and self._credentials.get_secret(API_KEY_SECRET))
        from_env = bool(os.environ.get(ENV_API_KEY))
        _, engine = self.pair()
        return CredentialStatus(
            api_key_set=stored or from_env,
            engine_id=engine or "",
            api_key_source="stored" if stored else "environment" if from_env else "none",
        )

    def save(self, api_key: str | None, engine_id: str) -> None:
        """Save the engine id and, if given, the key. An empty key keeps the stored one."""
        errors: dict[str, str] = {}
        engine = engine_id.strip()
        if len(engine) > MAX_ENGINE_ID_LENGTH or any(ch.isspace() for ch in engine):
            errors["engine_id"] = "The search engine id has no spaces and at most 100 characters."
        key = (api_key or "").strip()
        if key and not MIN_KEY_LENGTH <= len(key) <= MAX_KEY_LENGTH:
            errors["api_key"] = "This does not look like an API key."
        if errors:
            raise InvalidConfigurationError("Some fields need attention.", field_errors=errors)
        if key:
            self._credentials.set_secret(API_KEY_SECRET, key)
        self._settings.set(SETTING_ENGINE_ID, engine)
        log.info("google_credentials_saved", engine_id_set=bool(engine), key_saved=bool(key))

    def clear_key(self) -> None:
        self._credentials.delete_secret(API_KEY_SECRET)
