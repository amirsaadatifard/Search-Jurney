"""Proxy profiles: CRUD, secrets and connection tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.database.base import utcnow
from src.database.models import ProxyProfile
from src.database.repositories import ProxyProfileRepository, ScenarioRepository
from src.database.session import Database
from src.domain.exceptions import EntityInUseError, InvalidConfigurationError
from src.domain.schemas import MAX_PASSWORD_LENGTH, ProxyProfileInput, validate_input
from src.logging_config import get_logger
from src.networking.credentials import CredentialStore, proxy_secret_key
from src.networking.proxy_manager import ProxyTester, ProxyTestResult

log = get_logger(__name__)


class ProxyService:
    def __init__(
        self, database: Database, credentials: CredentialStore, tester: ProxyTester | None = None
    ) -> None:
        self._database = database
        self._credentials = credentials
        self._tester = tester or ProxyTester()

    @property
    def credentials_available(self) -> bool:
        return self._credentials.available

    @property
    def credentials_description(self) -> str:
        return self._credentials.description

    # -- queries ---------------------------------------------------------------- #
    def list(self, text: str = "", enabled_only: bool = False) -> list[ProxyProfile]:
        needle = text.strip().lower()
        with self._database.session_scope() as session:
            profiles = list(ProxyProfileRepository(session).list_sorted(enabled_only))
        return [
            p for p in profiles if not needle or needle in f"{p.name} {p.host} {p.notes}".lower()
        ]

    def get_password(self, profile_id: int) -> str | None:
        """The stored password, for building a run. Never shown in the GUI."""
        return self._credentials.get_secret(proxy_secret_key(profile_id))

    # -- commands ----------------------------------------------------------------- #
    def create(self, data: Mapping[str, Any], password: str | None = None) -> ProxyProfile:
        values = validate_input(ProxyProfileInput, data)
        _check_password(password)
        with self._database.session_scope() as session:
            repo = ProxyProfileRepository(session)
            if repo.get_by_name(values.name) is not None:
                raise _duplicate(values.name)
            profile = repo.add(ProxyProfile(**values.model_dump()))
            if password:
                # Inside the transaction: if the secret cannot be stored, nothing is saved.
                self._credentials.set_secret(proxy_secret_key(profile.id), password)
                profile.has_password = True
        log.info("proxy_profile_created", name=values.name)
        return profile

    def update(
        self,
        profile_id: int,
        data: Mapping[str, Any],
        password: str | None = None,
        clear_password: bool = False,
    ) -> ProxyProfile:
        """Update a profile. ``password=None`` keeps the stored password."""
        values = validate_input(ProxyProfileInput, data)
        _check_password(password)
        with self._database.session_scope() as session:
            repo = ProxyProfileRepository(session)
            profile = repo.require(profile_id)
            existing = repo.get_by_name(values.name)
            if existing is not None and existing.id != profile_id:
                raise _duplicate(values.name)
            moved = (values.host, values.port, values.protocol) != (
                profile.host,
                profile.port,
                profile.protocol,
            )
            for name, value in values.model_dump().items():
                setattr(profile, name, value)
            if moved:
                _forget_test(profile)
            if clear_password:
                self._credentials.delete_secret(proxy_secret_key(profile_id))
                profile.has_password = False
            if password:
                self._credentials.set_secret(proxy_secret_key(profile_id), password)
                profile.has_password = True
            return profile

    def set_enabled(self, profile_id: int, enabled: bool) -> None:
        with self._database.session_scope() as session:
            ProxyProfileRepository(session).require(profile_id).enabled = enabled

    def delete(self, profile_id: int) -> None:
        with self._database.session_scope() as session:
            repo = ProxyProfileRepository(session)
            profile = repo.require(profile_id)
            in_use = ScenarioRepository(session).count_using_proxy_profile(profile_id)
            if in_use:
                raise EntityInUseError(
                    f"'{profile.name}' is used by {in_use} scenario(s). "
                    "Change those scenarios first so none switches to a direct connection."
                )
            repo.delete(profile)
        self._credentials.delete_secret(proxy_secret_key(profile_id))

    # -- connection test ----------------------------------------------------------------- #
    def test_connection(self, profile_id: int) -> ProxyTestResult:
        """Test the proxy and store the outcome. Runs a short network operation."""
        with self._database.session_scope() as session:
            profile = ProxyProfileRepository(session).require(profile_id)
            protocol, host, port, username = (
                profile.protocol,
                profile.host,
                profile.port,
                profile.username,
            )
        password = self.get_password(profile_id) if username else None
        result = self._tester.test(protocol, host, port, username, password)
        with self._database.session_scope() as session:
            stored = ProxyProfileRepository(session).get(profile_id)
            if stored is not None:
                stored.last_status = result.status
                stored.last_latency_ms = result.latency_ms
                stored.last_checked_at = utcnow()
        log.info("proxy_tested", name=host, status=result.status.value, latency=result.latency_ms)
        return result


def _forget_test(profile: ProxyProfile) -> None:
    """An edited address invalidates the last test result."""
    from src.domain.enums import ProxyStatus

    profile.last_status = ProxyStatus.UNKNOWN
    profile.last_latency_ms = None
    profile.last_checked_at = None


def _check_password(password: str | None) -> None:
    if password is not None and len(password) > MAX_PASSWORD_LENGTH:
        raise InvalidConfigurationError(
            "Some fields need attention.",
            field_errors={"password": f"Use at most {MAX_PASSWORD_LENGTH} characters."},
        )


def _duplicate(name: str) -> InvalidConfigurationError:
    return InvalidConfigurationError(
        "Duplicate name.", field_errors={"name": f"A proxy profile named '{name}' already exists."}
    )
