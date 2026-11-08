"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from src.database.models.application_setting import ApplicationSetting
from src.database.models.automation_session import AutomationSession, Screenshot, SessionEvent
from src.database.models.browser_profile import BrowserProfile
from src.database.models.keyword import Keyword
from src.database.models.proxy_profile import ProxyProfile
from src.database.models.scenario import Scenario
from src.database.models.search_observation import ObservedResult, SearchObservation
from src.database.models.target_site import TargetSite

__all__ = [
    "ApplicationSetting",
    "AutomationSession",
    "BrowserProfile",
    "Keyword",
    "ObservedResult",
    "ProxyProfile",
    "Scenario",
    "Screenshot",
    "SearchObservation",
    "SessionEvent",
    "TargetSite",
]
