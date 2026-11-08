"""Secret storage abstraction.

Proxy passwords are never written to the database, the logs, exports or
snapshots. They live in the operating system's credential store (Windows
Credential Manager, macOS Keychain, Secret Service on Linux) through the
``keyring`` library, behind this small interface so tests can use memory.

If no secure backend exists the store reports itself unavailable and saving a
password is refused; there is deliberately no plain-text fallback.
"""

from __future__ import annotations

from typing import Final, Protocol

import keyring
from keyring.backends import fail
from keyring.errors import KeyringError

from src.domain.exceptions import AppError

SERVICE_NAME: Final = "SearchJourneyLab"
MAX_SECRET_LENGTH: Final = 512


class CredentialStoreError(AppError):
    """The secret could not be stored, read or removed."""


class CredentialStore(Protocol):
    """Minimal secure key/value storage for secrets."""

    @property
    def available(self) -> bool: ...

    @property
    def description(self) -> str: ...

    def set_secret(self, key: str, value: str) -> None: ...

    def get_secret(self, key: str) -> str | None: ...

    def delete_secret(self, key: str) -> None: ...


class KeyringCredentialStore:
    """Credential store backed by the operating system through ``keyring``."""

    @property
    def available(self) -> bool:
        backend = keyring.get_keyring()
        return not isinstance(backend, fail.Keyring) and getattr(backend, "priority", 1) > 0

    @property
    def description(self) -> str:
        if not self.available:
            return "No secure credential storage is available on this system."
        return f"Operating-system credential store ({type(keyring.get_keyring()).__name__})"

    def set_secret(self, key: str, value: str) -> None:
        self._require_available()
        if len(value) > MAX_SECRET_LENGTH:
            raise CredentialStoreError("The password is too long.")
        try:
            keyring.set_password(SERVICE_NAME, key, value)
        except KeyringError as exc:
            raise CredentialStoreError(f"The password could not be stored securely: {exc}") from exc

    def get_secret(self, key: str) -> str | None:
        self._require_available()
        try:
            return keyring.get_password(SERVICE_NAME, key)
        except KeyringError as exc:
            raise CredentialStoreError(f"The stored password could not be read: {exc}") from exc

    def delete_secret(self, key: str) -> None:
        if not self.available:
            return
        try:
            keyring.delete_password(SERVICE_NAME, key)
        except keyring.errors.PasswordDeleteError:
            return  # nothing stored under that key
        except KeyringError as exc:
            raise CredentialStoreError(f"The stored password could not be removed: {exc}") from exc

    def _require_available(self) -> None:
        if not self.available:
            raise CredentialStoreError(
                "Passwords cannot be saved because this system has no secure credential store."
            )


class InMemoryCredentialStore:
    """Non-persistent store for tests and for systems you explicitly accept that on."""

    def __init__(self, available: bool = True) -> None:
        self._available = available
        self._secrets: dict[str, str] = {}

    @property
    def available(self) -> bool:
        return self._available

    @property
    def description(self) -> str:
        return "In-memory store (not persistent)"

    def set_secret(self, key: str, value: str) -> None:
        if not self._available:
            raise CredentialStoreError("Passwords cannot be saved: no credential store.")
        self._secrets[key] = value

    def get_secret(self, key: str) -> str | None:
        return self._secrets.get(key)

    def delete_secret(self, key: str) -> None:
        self._secrets.pop(key, None)

    def keys(self) -> set[str]:
        return set(self._secrets)


def proxy_secret_key(profile_id: int) -> str:
    """Key under which a proxy profile's password is stored."""
    return f"proxy-{profile_id}"
