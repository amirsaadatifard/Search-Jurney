"""Validation schemas for user input.

Forms hand a plain dictionary to a service; the service validates it with one
of these models. Failures become an :class:`InvalidConfigurationError` whose
``field_errors`` the form shows next to each input.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from src.domain.enums import Environment, ProxyProtocol, UserAgentMode
from src.domain.exceptions import InvalidConfigurationError

NAME_MAX = 120
KEYWORD_MAX = 200
NOTES_MAX = 2000
TAG_MAX = 30
MAX_TAGS = 10
MAX_DWELL_SECONDS = 3600
MAX_ITERATIONS = 1000
MAX_DELAY_SECONDS = 3600
MAX_SCROLL_ACTIONS = 50
MAX_PASSWORD_LENGTH = 256
MAX_PORT = 65535

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=NOTES_MAX)]

_LOCALE_PATTERN = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")
_TIMEZONE_PATTERN = re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+\-]+){0,2}$")

_FRIENDLY_MESSAGES: dict[str, str] = {
    "missing": "This field is required.",
    "string_too_short": "This field is required.",
    "int_parsing": "Enter a whole number.",
    "int_type": "Enter a whole number.",
    "bool_parsing": "Choose yes or no.",
    "enum": "Choose one of the available options.",
}


class _Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _clean_single_line(value: str, label: str) -> str:
    if "\n" in value or "\r" in value or "\t" in value:
        raise ValueError(f"{label} must be a single line of text.")
    return re.sub(r" {2,}", " ", value)


class KeywordInput(_Schema):
    text: Annotated[str, StringConstraints(min_length=1, max_length=KEYWORD_MAX)]
    tags: list[str] = Field(default_factory=list)
    notes: Notes = ""
    enabled: bool = True

    @field_validator("text")
    @classmethod
    def _single_line(cls, value: str) -> str:
        return _clean_single_line(value, "A keyword")

    @field_validator("tags")
    @classmethod
    def _normalise_tags(cls, value: list[str]) -> list[str]:
        return normalise_tags(value)


def normalise_tags(tags: list[str]) -> list[str]:
    """Trim, drop blanks and case-insensitive duplicates; enforce the limits."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for tag in tags:
        candidate = str(tag).strip()
        if not candidate or candidate.lower() in seen:
            continue
        if len(candidate) > TAG_MAX or "," in candidate or ";" in candidate:
            raise ValueError(
                f"Tags may be up to {TAG_MAX} characters and must not contain ',' or ';'."
            )
        seen.add(candidate.lower())
        cleaned.append(candidate)
    if len(cleaned) > MAX_TAGS:
        raise ValueError(f"A keyword can have at most {MAX_TAGS} tags.")
    return cleaned


class TargetSiteInput(_Schema):
    """``base_url`` is checked further by the safety service (scheme, host, scope)."""

    display_name: Name
    base_url: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    environment: Environment = Environment.LOCAL
    notes: Notes = ""
    enabled: bool = True
    approved_for_automation: bool = False


class BrowserProfileInput(_Schema):
    name: Name
    viewport_width: int = Field(1280, ge=320, le=3840)
    viewport_height: int = Field(800, ge=240, le=2160)
    headless: bool = False
    locale: str = ""
    timezone: str = ""
    downloads_enabled: bool = False
    javascript_enabled: bool = True
    clear_storage: bool = True
    user_agent_mode: UserAgentMode = UserAgentMode.BROWSER_DEFAULT

    @field_validator("locale")
    @classmethod
    def _valid_locale(cls, value: str) -> str:
        if value and not _LOCALE_PATTERN.fullmatch(value):
            raise ValueError("Use a locale such as 'en-US' or 'de', or leave it empty.")
        return value

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        if not value:
            return value
        message = "Use an IANA time zone such as 'Europe/Berlin', or leave it empty."
        if not _TIMEZONE_PATTERN.fullmatch(value):
            raise ValueError(message)
        if not available_timezones():
            return value  # no tz database on this system; the pattern check must do
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(message) from exc
        return value


_HOST_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
_HOST_PATTERN = re.compile(rf"^{_HOST_LABEL}(?:\.{_HOST_LABEL})*$")


class ProxyProfileInput(_Schema):
    """Connection details of a proxy. The password is handled separately (never in a schema)."""

    name: Name
    protocol: ProxyProtocol = ProxyProtocol.HTTP
    host: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    port: int = Field(ge=1, le=MAX_PORT)
    username: Annotated[str, StringConstraints(max_length=NAME_MAX)] = ""
    enabled: bool = True
    notes: Notes = ""

    @field_validator("host")
    @classmethod
    def _valid_host(cls, value: str) -> str:
        host = value.strip().rstrip(".")
        if "://" in host or "/" in host or " " in host or "@" in host:
            raise ValueError("Enter only the host name or IP address, without http:// or a path.")
        bare = host.strip("[]")
        try:
            ipaddress.ip_address(bare)
        except ValueError:
            if not _HOST_PATTERN.fullmatch(host):
                raise ValueError("This is not a valid host name or IP address.") from None
            return host.lower()
        return bare


class ScenarioInput(_Schema):
    name: Name
    description: Notes = ""
    keyword: Annotated[str, StringConstraints(min_length=1, max_length=KEYWORD_MAX)]
    target_site_id: int = Field(gt=0)
    browser_profile_id: int = Field(gt=0)
    proxy_profile_id: int | None = Field(default=None, gt=0)
    min_dwell_seconds: int = Field(ge=1, le=MAX_DWELL_SECONDS)
    max_dwell_seconds: int = Field(ge=1, le=MAX_DWELL_SECONDS)
    iterations: int = Field(1, ge=1, le=MAX_ITERATIONS)
    delay_between_iterations: int = Field(2, ge=0, le=MAX_DELAY_SECONDS)
    scroll_enabled: bool = True
    max_scroll_actions: int = Field(5, ge=0, le=MAX_SCROLL_ACTIONS)
    clear_cookies_between_sessions: bool = True
    clear_storage_between_sessions: bool = True
    incognito: bool = True
    enabled: bool = True

    @field_validator("keyword")
    @classmethod
    def _single_line(cls, value: str) -> str:
        return _clean_single_line(value, "The keyword")

    @field_validator("max_dwell_seconds")
    @classmethod
    def _dwell_range_ordered(cls, value: int, info: ValidationInfo) -> int:
        minimum = info.data.get("min_dwell_seconds")
        if minimum is not None and value < minimum:
            raise ValueError("Maximum dwell time must not be less than the minimum.")
        return value


def validate_input[SchemaT: BaseModel](schema: type[SchemaT], data: Mapping[str, Any]) -> SchemaT:
    """Validate ``data`` or raise :class:`InvalidConfigurationError` with field errors."""
    try:
        return schema.model_validate(dict(data))
    except ValidationError as exc:
        raise InvalidConfigurationError(
            "Some fields need attention.", field_errors=to_field_errors(exc)
        ) from exc


def to_field_errors(error: ValidationError) -> dict[str, str]:
    """Flatten a Pydantic error into ``{field: message}`` (first message per field)."""
    errors: dict[str, str] = {}
    for item in error.errors():
        field = str(item["loc"][0]) if item["loc"] else "__all__"
        if field in errors:
            continue
        message = _FRIENDLY_MESSAGES.get(item["type"], item["msg"])
        errors[field] = message.removeprefix("Value error, ")
    return errors
