"""Dummy search results generated from a local dataset.

Every fictitious site lives under the reserved ``.example`` top-level domain
(RFC 2606), so no generated link can ever resolve to a real website.

Results are deterministic: the same keyword always yields the same list, which
makes test sessions reproducible.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass
from typing import Final

from src.app.constants import MAX_RESULT_POSITION

RESULTS_PER_PAGE: Final = MAX_RESULT_POSITION
MAX_QUERY_LENGTH: Final = 200


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One entry on the results page."""

    position: int
    title: str
    url: str
    description: str
    is_target: bool = False


@dataclass(frozen=True, slots=True)
class _Site:
    domain: str
    name: str
    path: str


_SITES: Final[tuple[_Site, ...]] = (
    _Site("www.claycorner.example", "Clay Corner", "/shop"),
    _Site("www.northfieldpottery.example", "Northfield Pottery", "/collections"),
    _Site("guides.makersjournal.example", "Makers Journal", "/guides"),
    _Site("www.studio-ember.example", "Studio Ember", "/catalogue"),
    _Site("www.handmadehub.example", "Handmade Hub", "/browse"),
    _Site("blog.slowcraft.example", "Slow Craft Blog", "/posts"),
    _Site("www.riverbendceramics.example", "Riverbend Ceramics", "/store"),
    _Site("www.thekilnroom.example", "The Kiln Room", "/classes"),
    _Site("reviews.homegoodsweekly.example", "Home Goods Weekly", "/reviews"),
    _Site("www.artisanmarket.example", "Artisan Market", "/listings"),
    _Site("forum.wheelthrowers.example", "Wheel Throwers Forum", "/threads"),
    _Site("www.glazeandfire.example", "Glaze & Fire", "/products"),
    _Site("learn.craftschool.example", "Craft School Online", "/courses"),
    _Site("www.tablewaredirect.example", "Tableware Direct", "/range"),
    _Site("www.cityartscentre.example", "City Arts Centre", "/workshops"),
    _Site("wiki.materialsbase.example", "Materials Base", "/wiki"),
    _Site("www.greenleafgifts.example", "Greenleaf Gifts", "/gifts"),
    _Site("www.potterssupply.example", "Potters Supply Co.", "/supplies"),
)

_TITLE_PATTERNS: Final[tuple[str, ...]] = (
    "{keyword} | {site}",
    "{keyword}: A Practical Guide - {site}",
    "Best {keyword} Compared - {site}",
    "{site} - {keyword} and More",
    "Shop {keyword} Online | {site}",
    "{keyword} Explained for Beginners - {site}",
    "Top 10 {keyword} Ideas | {site}",
    "{keyword} - Reviews, Tips and Prices | {site}",
)

_DESCRIPTION_PATTERNS: Final[tuple[str, ...]] = (
    "Everything you need to know about {keyword}. {site} covers the essentials, "
    "common mistakes and what to look for before you decide.",
    "Browse a curated selection related to {keyword}. Updated regularly, with honest notes "
    "from the {site} team.",
    "Looking for {keyword}? Compare options, read independent opinions and find practical "
    "advice in one place.",
    "A step-by-step introduction to {keyword}, written for people who are just getting "
    "started. Includes a short checklist.",
    "{site} explains {keyword} in plain language: materials, care, pricing and frequently "
    "asked questions.",
    "Community discussion about {keyword}. Members share experience, photos and "
    "recommendations.",
)


def normalise_query(query: str) -> str:
    """Collapse whitespace and cap the length of a raw query string."""
    return re.sub(r"\s+", " ", query).strip()[:MAX_QUERY_LENGTH]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "results"


def generate_results(
    keyword: str,
    target: SearchResult | None = None,
    count: int = RESULTS_PER_PAGE,
) -> list[SearchResult]:
    """Build the results page for ``keyword``.

    Args:
        keyword: The (already normalised) search query.
        target: The configured target entry. Its ``position`` decides where it is
            inserted; ``None`` produces a page without the target.
        count: Number of results on the page.
    """
    seed = int.from_bytes(hashlib.sha256(keyword.lower().encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    sites = rng.sample(_SITES, k=min(count, len(_SITES)))
    display_keyword = keyword.title()
    slug = _slug(keyword)

    fillers = [
        SearchResult(
            position=0,
            title=rng.choice(_TITLE_PATTERNS).format(keyword=display_keyword, site=site.name),
            url=f"https://{site.domain}{site.path}/{slug}",
            description=rng.choice(_DESCRIPTION_PATTERNS).format(keyword=keyword, site=site.name),
        )
        for site in sites
    ]

    if target is not None:
        index = max(1, min(target.position, count)) - 1
        fillers.insert(index, target)
    page = fillers[:count]
    return [
        SearchResult(
            position=number,
            title=result.title,
            url=result.url,
            description=result.description,
            is_target=result.is_target,
        )
        for number, result in enumerate(page, start=1)
    ]
