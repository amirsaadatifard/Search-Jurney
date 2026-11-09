"""Builds immutable :class:`SessionPlan` objects from stored scenarios.

The factory also decides *how* a journey reaches its target:

* a normal scenario searches Google in the browser (:data:`MODE_BROWSER`) -
  the keyword is typed into Google's own box, the result pages are walked and the
  target's link is clicked; this is the only way this application finds a ranking,
  and it needs no key;
* a target registered as a *local* site - the bundled sample website, which
  Google can never list - is opened directly (:data:`MODE_DIRECT`) and no
  ranking is claimed for it.
"""

from __future__ import annotations

from dataclasses import fields

from src.app.constants import DEFAULT_TIMEOUT_SECONDS
from src.automation.models import (
    MODE_BROWSER,
    MODE_DIRECT,
    BrowserSettings,
    ProxySettings,
    ScreenshotPolicy,
    SessionPlan,
)
from src.database.models import (
    BrowserProfile,
    ProxyProfile,
    Scenario,
    TargetSite,
)
from src.domain.enums import Environment
from src.domain.exceptions import InvalidConfigurationError
from src.networking.credentials import CredentialStore, proxy_secret_key
from src.services.settings_service import SettingKey, SettingsService

_POLICY_FIELDS = {field.name for field in fields(ScreenshotPolicy)}


def skips_rank_observation(site: TargetSite) -> bool:
    """Whether a journey may skip the ranking step.

    Only a site registered as a *local* environment: something on this computer
    is never in Google's index, so asking costs quota to learn what is already
    known and the bundled demo would need credentials to run at all. Any other
    environment is observed - guessing where a real site ranks would be worse
    than asking, and "not listed" is itself the finding.
    """
    return site.environment is Environment.LOCAL


class PlanFactory:
    """Turns a stored scenario (or an observation) into an immutable run plan."""

    def mode_for(self, site: TargetSite) -> str:
        """How the journey reaches its target: a search in the browser, or a visit."""
        return MODE_DIRECT if skips_rank_observation(site) else MODE_BROWSER

    def __init__(self, settings: SettingsService, credentials: CredentialStore) -> None:
        self._settings = settings
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

    def from_scenario(self, scenario: Scenario) -> SessionPlan:
        """Build the journey of one scenario: real ranking first, then the visit."""
        browser = scenario.browser_profile
        proxy = scenario.proxy_profile
        if proxy is not None and not proxy.enabled:
            raise InvalidConfigurationError(
                f"Proxy profile '{proxy.name}' is disabled. Enable it or change the scenario."
            )
        site = scenario.target_site
        defaults = self._settings.automation_defaults()
        return SessionPlan(
            scenario_id=scenario.id,
            scenario_name=scenario.name,
            keyword=scenario.keyword,
            target_url=site.base_url,
            target_domain=site.domain,
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
            mode=self.mode_for(site),
            search_language=defaults.search_language,
            search_country=defaults.search_country,
            requested_results=defaults.requested_results,
            max_search_pages=defaults.max_search_pages,
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
