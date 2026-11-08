"""Google search data through the official Custom Search JSON API.

This uses Google's documented, key-authenticated REST endpoint
(``customsearch/v1``). It does **not** scrape ``google.com``, does not drive a
browser against Google, never retries in a loop (every call is billed against
the daily quota) and never falls back to other data when the call fails.

Notes the user must know (they are also shown in the interface):

* the API needs an API key and a *Programmable Search Engine* id (``cx``)
  configured to search the entire web;
* it returns at most 10 results per request and at most 100 per query;
* its ranking can differ from what ``google.com`` shows a person, so the
  "observed position" is the position within *this API's* results;
* Google controls availability and quota (a free daily allowance, then billing).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Final

import httpx

from src.domain.exceptions import (
    GoogleAuthenticationError,
    GoogleNetworkError,
    GoogleQuotaError,
    GoogleSearchError,
)
from src.logging_config import get_logger
from src.search_providers.base import ProviderCheck, ProviderResult
from src.search_providers.matching import result_domain

log = get_logger(__name__)

DEFAULT_ENDPOINT: Final = "https://www.googleapis.com/customsearch/v1"
PAGE_SIZE: Final = 10
MAX_RESULTS: Final = 100
TIMEOUT_SECONDS: Final = 15.0
_QUOTA_REASONS: Final = frozenset(
    {"dailylimitexceeded", "ratelimitexceeded", "quotaexceeded", "userratelimitexceeded"}
)
_AUTH_REASONS: Final = frozenset(
    {"keyinvalid", "accessnotconfigured", "forbidden", "iprefererblocked", "apikeyinvalid"}
)
Credentials = tuple[str | None, str | None]


class GoogleCustomSearchProvider:
    """Reads real results from Google's Custom Search JSON API."""

    name = "google-custom-search"

    def __init__(
        self,
        credentials: Callable[[], Credentials],
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_seconds: float = TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._credentials = credentials
        self._endpoint = endpoint
        self._timeout = timeout_seconds
        self._transport = transport

    # -- public API ------------------------------------------------------------------- #
    def search(
        self, query: str, language: str = "", country: str = "", count: int = 10
    ) -> list[ProviderResult]:
        """Return up to ``count`` real results, in the order the API ranks them."""
        text = query.strip()
        if not text:
            raise GoogleSearchError("Enter a keyword to search for.")
        wanted = max(1, min(int(count), MAX_RESULTS))
        api_key, engine_id = self._require_credentials()

        results: list[ProviderResult] = []
        start = 1
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            while len(results) < wanted:
                page = self._request(
                    client,
                    {
                        "key": api_key,
                        "cx": engine_id,
                        "q": text,
                        "num": min(PAGE_SIZE, wanted - len(results)),
                        "start": start,
                        **({"hl": language} if language else {}),
                        **({"gl": country} if country else {}),
                    },
                )
                items = page.get("items") or []
                if not items:
                    break  # the API has no more results for this query
                for item in items:
                    results.append(self._to_result(len(results) + 1, item))
                start += len(items)
                if start > MAX_RESULTS:
                    break
        log.info("search_completed", provider=self.name, results=len(results))
        return results[:wanted]

    def check(self) -> ProviderCheck:
        """Run a one-result query. Uses one unit of the daily quota."""
        started = time.perf_counter()
        try:
            self.search("connection test", count=1)
        except GoogleSearchError as exc:
            return ProviderCheck(False, exc.message)
        return ProviderCheck(
            True,
            "The search API answered.",
            round((time.perf_counter() - started) * 1000),
        )

    # -- internals ------------------------------------------------------------------------- #
    def _require_credentials(self) -> tuple[str, str]:
        api_key, engine_id = self._credentials()
        missing = [
            label
            for label, value in (("API key", api_key), ("Search engine ID", engine_id))
            if not (value or "").strip()
        ]
        if missing:
            raise GoogleAuthenticationError(
                f"Google is not configured: the {' and the '.join(missing)} "
                f"{'is' if len(missing) == 1 else 'are'} missing."
            )
        return str(api_key).strip(), str(engine_id).strip()

    def _request(self, client: httpx.Client, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = client.get(self._endpoint, params=params)
        except httpx.TimeoutException as exc:
            raise GoogleNetworkError("Google did not answer in time.") from exc
        except httpx.HTTPError as exc:
            raise GoogleNetworkError(
                "Google could not be reached. Check the internet connection."
            ) from exc
        if response.status_code != httpx.codes.OK:
            raise self._error_for(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise GoogleSearchError("Google returned an answer that is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise GoogleSearchError("Google returned an unexpected answer.")
        return payload

    @staticmethod
    def _error_for(response: httpx.Response) -> GoogleSearchError:
        """Map an HTTP error to the most useful application error."""
        try:
            error = response.json().get("error", {})
        except (ValueError, AttributeError):
            error = {}
        message = str(error.get("message") or response.reason_phrase or "error")
        reasons = {
            str(item.get("reason", "")).lower()
            for item in error.get("errors", [])
            if isinstance(item, dict)
        }
        status = response.status_code
        if status == httpx.codes.TOO_MANY_REQUESTS or reasons & _QUOTA_REASONS:
            return GoogleQuotaError(
                "The Google search quota is used up. Wait for the quota to reset or check "
                f"billing in Google Cloud. ({message})"
            )
        if status in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN) or reasons & _AUTH_REASONS:
            return GoogleAuthenticationError(
                f"Google rejected the credentials or the API is not enabled for them. ({message})"
            )
        if status == httpx.codes.BAD_REQUEST:
            if "api key" in message.lower() or "keyinvalid" in reasons:
                return GoogleAuthenticationError(f"The API key is not valid. ({message})")
            return GoogleSearchError(
                "Google refused the request; check the search engine id and the options. "
                f"({message})"
            )
        if status >= httpx.codes.INTERNAL_SERVER_ERROR:
            return GoogleSearchError(f"The Google service has a problem right now (HTTP {status}).")
        return GoogleSearchError(f"Google answered with HTTP {status}. ({message})")

    @staticmethod
    def _to_result(position: int, item: dict[str, Any]) -> ProviderResult:
        url = str(item.get("link") or "")
        if not url:
            raise GoogleSearchError("Google returned a result without a link.")
        return ProviderResult(
            position=position,
            title=str(item.get("title") or "").strip(),
            url=url,
            domain=result_domain(url) or str(item.get("displayLink") or "").lower(),
            snippet=" ".join(str(item.get("snippet") or "").split()),
        )
