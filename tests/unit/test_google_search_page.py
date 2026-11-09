"""The pure half of the browser channel: the URL, the page's meaning, the numbering."""

from __future__ import annotations

import time

import pytest

from src.automation.google_search_page import (
    MAX_RESULTS,
    PageKind,
    classify,
    find_target,
    looks_blocked,
    normalize_result_link,
    pace_query,
    parse_results,
    ranking,
    search_url,
)
from src.domain.exceptions import SearchError

SEARCH = "https://www.google.com/search"


def item(position: int, url: str, title: str = "") -> tuple[str, str]:
    return (title or f"Result {position}", url)


# -- what the channel asks for -------------------------------------------------- #
def test_the_query_url_is_one_page_on_google_with_the_plan_settings() -> None:
    assert search_url("handmade ceramic mugs", "en", "us", 10) == (
        f"{SEARCH}?gl=us&hl=en&q=handmade+ceramic+mugs"
    )


def test_blank_settings_are_left_out_rather_than_sent_empty() -> None:
    """A parameter is sent when it asks for something, not to restate the default.

    Google's page size is 10 whether or not ``num`` is in the URL, and no ``hl``
    or ``gl`` means "the browser's own", which a bare parameter would override.
    """
    url = search_url("gold ring")
    assert url == f"{SEARCH}?q=gold+ring"
    assert "hl=" not in url and "gl=" not in url and "num=" not in url
    assert search_url("gold ring", count=20) == f"{SEARCH}?num=20&q=gold+ring"


def test_the_keyword_is_trimmed_and_a_page_never_asked_for_more_than_google_shows() -> None:
    assert "q=one+two" in search_url("  one   two  ")
    assert f"num={MAX_RESULTS}" in search_url("x", count=MAX_RESULTS * 3)
    assert "num=1" in search_url("x", count=0)


def test_an_empty_keyword_is_refused_before_any_navigation() -> None:
    with pytest.raises(SearchError):
        search_url("   ")


# -- what the page means -------------------------------------------------------- #
def test_a_results_url_is_a_results_page_only_without_a_refusal_on_it() -> None:
    assert classify(f"{SEARCH}?q=cups") is PageKind.SEARCH
    assert classify(f"{SEARCH}?q=cups", "Our systems have detected unusual traffic") is (
        PageKind.BLOCKED
    )


def test_the_consent_screen_and_the_apology_page_are_told_apart_from_results() -> None:
    assert classify("https://consent.google.com/#/details") is PageKind.CONSENT
    assert classify("https://www.google.com/sorry/index?continue=x") is PageKind.BLOCKED


@pytest.mark.parametrize(
    "url",
    [
        "",
        "about:blank",
        "https://shop.example/search?q=cups",  # an approved site is not a search page
        "http://www.google.com/search?q=cups",  # not https
        "https://www.google.com/maps?q=cups",  # not the results page
        "https://evil.example/search?q=cups",
    ],
)
def test_anything_else_is_nothing_the_channel_should_be_looking_at(url: str) -> None:
    assert classify(url) is PageKind.OTHER


@pytest.mark.parametrize(
    "text",
    [
        "not a robot",
        "UNUSUAL TRAFFIC FROM YOUR NETWORK",
        "Please enable JavaScript and cookies to continue",
    ],
)
def test_the_refusals_are_recognised_from_their_own_words(text: str) -> None:
    assert looks_blocked(text) is True


def test_an_ordinary_page_is_not_read_as_a_refusal() -> None:
    assert looks_blocked("12 results for ceramic mugs - our systems are fast") is False


# -- the ranking the page shows ------------------------------------------------- #
def test_a_wrapped_result_link_is_unwrapped_because_the_destination_is_what_matters() -> None:
    assert normalize_result_link("https://www.google.com/url?q=https://shop.example/a&sa=D") == (
        "https://shop.example/a"
    )
    assert normalize_result_link("https://shop.example/a") == "https://shop.example/a"


@pytest.mark.parametrize(
    "href",
    ["", "javascript:void(0)", "/search?q=more", "https://www.google.com/url?q=", "#anchor"],
)
def test_a_link_that_is_not_a_page_is_not_a_result(href: str) -> None:
    assert normalize_result_link(href) == ""


def test_results_are_numbered_as_a_visitor_counts_them() -> None:
    results = parse_results(
        [
            ("Ad", "https://www.google.com/aclk?ad=1"),  # not a result, not counted
            ("Second", "https://a.example/x"),
            ("Wrapped third", "https://www.google.com/url?q=https://b.example/y"),
            ("Same page again", "https://a.example/x"),  # duplicate: dropped, not counted
            ("Fourth", "https://c.example/z"),
        ]
    )
    assert [(r.position, r.url) for r in results] == [
        (1, "https://a.example/x"),
        (2, "https://b.example/y"),
        (3, "https://c.example/z"),
    ]


def test_titles_are_cleaned_and_the_limit_holds() -> None:
    results = parse_results([("  Weird\n   title  ", f"https://s{n}.example/") for n in range(50)])
    assert results[0].title == "Weird title"
    assert len(parse_results(_many(), limit=7)) == 7


def _many() -> list[tuple[str, str]]:
    return [(f"t{n}", f"https://site{n}.example/") for n in range(40)]


def test_a_site_that_merely_ends_with_the_engine_name_is_still_a_result() -> None:
    """ "seagoogle.com" is someone else's domain and must not be filtered away."""
    results = parse_results(
        [
            ("Sea Google", "https://www.seagoogle.com/mugs"),
            ("Fake", "https://google.com.evil.test/mugs"),
            ("Theirs", "https://seagoogle.com/x"),
        ]
    )
    assert [r.url for r in results] == [
        "https://www.seagoogle.com/mugs",
        "https://google.com.evil.test/mugs",
        "https://seagoogle.com/x",
    ]


def test_the_target_is_found_on_its_subdomains_and_counts_as_first_ranked() -> None:
    results = parse_results(
        [("Other", "https://elsewhere.example/"), ("Shop", "https://www.shop.example/mugs")]
    )
    listed = find_target(results, "shop.example")
    assert listed is not None and listed.position == 2
    assert find_target(results, "nowhere.example") is None


def test_a_blank_domain_never_matches() -> None:
    assert find_target(parse_results([("t", "https://shop.example/")]), "") is None


def test_the_snapshot_rows_are_the_ranking_itself() -> None:
    results = parse_results([("Shop title", "https://shop.example/mugs")])
    assert ranking(results) == [(1, "shop.example", "Shop title")]


# -- courtesy ------------------------------------------------------------------- #
async def test_two_reads_of_the_page_are_spaced_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("src.automation.google_search_page.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "src.automation.google_search_page._pacing.next_allowed", time.monotonic() + 5.0
    )
    await pace_query(gap_seconds=5.0)
    assert slept and 0 < slept[0] <= 5.0
