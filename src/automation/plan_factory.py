"""Builds immutable :class:`SessionPlan` objects from stored scenarios."""

from __future__ import annotations

from dataclasses import fields

from src.app.constants import DEFAULT_TIMEOUT_SECONDS
from src.automation.models import BrowserSettings, ProxySettings, ScreenshotPolicy, SessionPlan
from src.database.models import BrowserProfile, ProxyProfile, Scenario, SearchObservation
from src.domain.exceptions import InvalidConfigurationError
from src.networking.credentials import CredentialStore, proxy_secret_key
from src.search_providers import result_domain
from src.services.settings_service import SettingKey, SettingsService

DIRECT_VISIT_SCROLLS = 5
_POLICY_FIELDS = {field.name for field in fields(ScreenshotPolicy)}


class PlanFactory:
    def __init__(
        self, settings: SettingsService, search_url: str, credentials: CredentialStore
    ) -> None:
        self._settings = settings
        self._search_url = search_url
        self._credentials = credentials

    def screenshot_policy(self) -> ScreenshotPolicy:
        stored = self._settings.get(SettingKey.SCREENSHOT_POLICY, {})
        if not isinstance(stored, dict):
            return ScreenshotPolicy()
        return ScreenshotPolicy(
            **{key: bool(value) for key, value in stored.items() if key in _POLICY_FIELDS}
        )

    @staticmethod
    def _browser_settings(browser: BrowserProfile) -> BrowserSettings:
        return BrowserSettings(
            name=browser.name,
            viewport_width=browser.viewport_width,
            viewport_height=browser.viewport_height,
            headless=browser.headless,
            locale=browser.locale,
            timezone=browser.timezone,
            downloads_enabled=browser.downloads_enabled,
            javascript_enabled=browser.javascript_enabled,
            clear_storage=browser.clear_storage,
            user_agent_mode=browser.user_agent_mode.value,
        )

    def direct_visit_plan(
        self,
        observation: SearchObservation,
        browser: BrowserProfile,
        quick_seconds: int | None = None,
    ) -> SessionPlan:
        """One session that opens the URL a real search observed, directly.

        ``quick_seconds`` makes a short look (no scrolling) instead of the default dwell.
        """
        if not observation.target_url:
            raise InvalidConfigurationError("The observation has no target URL.")
        defaults = self._settings.automation_defaults()
        quick = quick_seconds is not None
        dwell_min = quick_seconds if quick_seconds is not None else defaults.min_dwell_seconds
        dwell_max = quick_seconds if quick_seconds is not None else defaults.max_dwell_seconds
        return SessionPlan(
            scenario_id=None,
            scenario_name=f"Google Research: {observation.query}"[:120],
            keyword=observation.query,
            search_url="",
            target_url=observation.target_url,
            target_domain=result_domain(observation.target_url),
            simulated_position=None,
            min_dwell_seconds=dwell_min,
            max_dwell_seconds=dwell_max,
            iterations=1,  # one session per click: this mode never repeats visits
            delay_between_iterations=0,
            scroll_enabled=not quick,
            max_scroll_actions=0 if quick else DIRECT_VISIT_SCROLLS,
            clear_cookies=True,
            clear_storage=True,
            incognito=True,
            browser=self._browser_settings(browser),
            proxy=None,
            screenshots=self.screenshot_policy(),
            timeout_seconds=defaults.timeout_seconds,
            mode="direct",
            observation_id=observation.id,
            observed_position=observation.target_position,
            observed_result_count=observation.result_count,
            provider=observation.provider,
        )

    def from_scenario(self, scenario: Scenario) -> SessionPlan:
        browser = scenario.browser_profile
        proxy = scenario.proxy_profile
        if proxy is not None and not proxy.enabled:
            raise InvalidConfigurationError(
                f"Proxy profile '{proxy.name}' is disabled. Enable it or change the scenario."
            )
        site = scenario.target_site
        return SessionPlan(
            scenario_id=scenario.id,
            scenario_name=scenario.name,
            keyword=scenario.keyword,
            search_url=self._search_url,
            target_url=site.base_url,
            target_domain=site.domain,
            simulated_position=scenario.simulated_position,
            min_dwell_seconds=scenario.min_dwell_seconds,
            max_dwell_seconds=scenario.max_dwell_seconds,
            iterations=scenario.iterations,
            delay_between_iterations=scenario.delay_between_iterations,
            scroll_enabled=scenario.scroll_enabled,
            max_scroll_actions=scenario.max_scroll_actions,
            clear_cookies=scenario.clear_cookies_between_sessions,
            clear_storage=scenario.clear_storage_between_sessions,
            incognito=scenario.incognito,
            browser=BrowserSettings(
                name=browser.name,
                viewport_width=browser.viewport_width,
                viewport_height=browser.viewport_height,
                headless=browser.headless,
                locale=browser.locale,
                timezone=browser.timezone,
                downloads_enabled=browser.downloads_enabled,
                javascript_enabled=browser.javascript_enabled,
                clear_storage=browser.clear_storage,
                user_agent_mode=browser.user_agent_mode.value,
            ),
            proxy=(
                None
                if proxy is None
                else ProxySettings(
                    name=proxy.name,
                    protocol=proxy.protocol.value,
                    host=proxy.host,
                    port=proxy.port,
                    username=proxy.username,
                    password=self._password(proxy),
                )
            ),
            screenshots=self.screenshot_policy(),
            timeout_seconds=int(
                self._settings.get(SettingKey.DEFAULT_TIMEOUT, DEFAULT_TIMEOUT_SECONDS)
            ),
        )

    def _password(self, proxy: ProxyProfile) -> str | None:
        """The proxy password from the credential store (it is never in the database)."""
        if not (proxy.has_password and proxy.username):
            return None
        secret = self._credentials.get_secret(proxy_secret_key(proxy.id))
        if secret is None:
            raise InvalidConfigurationError(
                f"The password of proxy profile '{proxy.name}' is missing from the credential "
                "store. Edit the profile and enter it again."
            )
        return secret
