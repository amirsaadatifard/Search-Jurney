"""Search-data providers (real search results from an official API)."""

from src.search_providers.base import ProviderCheck, ProviderResult, SearchProvider
from src.search_providers.google_cse import GoogleCustomSearchProvider
from src.search_providers.matching import (
    domain_matches,
    find_target,
    normalize_target_domain,
    result_domain,
)

__all__ = [
    "GoogleCustomSearchProvider",
    "ProviderCheck",
    "ProviderResult",
    "SearchProvider",
    "domain_matches",
    "find_target",
    "normalize_target_domain",
    "result_domain",
]
