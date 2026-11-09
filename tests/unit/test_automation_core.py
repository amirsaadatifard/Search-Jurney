"""State machine, run control and the dwell / interaction schedule (no browser)."""

from __future__ import annotations

import asyncio
import random
import time

import pytest

from src.automation.control import RunControl
from src.automation.interaction_service import (
    EARLIEST_ACTION_FRACTION,
    INTERNAL_VISIT_EVERY,
    LATEST_ACTION_FRACTION,
    ActionKind,
    build_schedule,
    choose_dwell,
)
from src.automation.models import ProxySettings, ScreenshotPolicy
from src.automation.screenshot_service import screenshot_filename, slugify
from src.automation.state_machine import SessionStateMachine
from src.domain.enums import ScreenshotKind
from src.domain.enums import SessionState as S
from src.domain.exceptions import InvalidStateTransitionError, SessionCancelledError

# What a journey does today: the browser opens Google, types the keyword, reads each
# result page until the target is numbered, and clicks that result to arrive.
HAPPY_PATH = [
    S.INITIALIZING,  # 0
    S.OPENING_SEARCH,  # 1
    S.ENTERING_KEYWORD,  # 2
    S.SEARCHING,  # 3
    S.ANALYZING_RESULTS,  # 4
    S.TARGET_FOUND,  # 5
    S.CLICKING_RESULT,  # 6
    S.OPENING_TARGET,  # 7
    S.DWELLING,  # 8
    S.INTERACTING,  # 9
    S.DWELLING,  # 10
    S.CAPTURING_RESULT,  # 11
    S.COMPLETED,  # 12
]

# What a session recorded while the ranking came from a search provider's JSON API:
# no search page at all, and the browser only started once the answer was in hand.
# Those rows are still readable, so the edges that allow them stay.
LEGACY_PATH = [
    S.INITIALIZING,
    S.SEARCHING,
    S.ANALYZING_RESULTS,
    S.TARGET_FOUND,
    S.STARTING_BROWSER,
    S.OPENING_TARGET,
    S.DWELLING,
    S.CAPTURING_RESULT,
    S.COMPLETED,
]


def advance(machine: SessionStateMachine, *states: S) -> None:
    for state in states:
        machine.transition(state)


# -- state machine ----------------------------------------------------------------- #
def test_happy_path_and_history() -> None:
    changes: list[tuple[S, S]] = []
    machine = SessionStateMachine(lambda old, new: changes.append((old, new)))
    assert machine.state is S.IDLE
    advance(machine, *HAPPY_PATH)
    assert machine.state is S.COMPLETED and machine.is_terminal
    assert [state for state, _ in machine.history] == [S.IDLE, *HAPPY_PATH]
    assert changes[0] == (S.IDLE, S.INITIALIZING) and len(changes) == len(HAPPY_PATH)


def test_target_not_found_path() -> None:
    """A site the real results do not list is a finished session, not a failure.

    The walk ends where the reading ends: no browser step is taken for a site that
    was never listed, because there is nothing to click.
    """
    machine = SessionStateMachine()
    not_found = [*HAPPY_PATH[:5], S.TARGET_NOT_FOUND, S.CAPTURING_RESULT, S.COMPLETED]
    advance(machine, *not_found)
    assert machine.is_terminal
    assert [state for state, _ in machine.history] == [S.IDLE, *not_found]


def test_legacy_api_path_is_still_readable() -> None:
    """Old sessions keep their states; the machine does not reject history it wrote."""
    machine = SessionStateMachine()
    advance(machine, *LEGACY_PATH)
    assert machine.state is S.COMPLETED and machine.is_terminal


@pytest.mark.parametrize(
    ("path", "illegal"),
    [
        ([], S.SEARCHING),  # IDLE cannot jump ahead
        ([], S.COMPLETED),
        # the browser is opened before the search page, never after it
        (HAPPY_PATH[:2], S.STARTING_BROWSER),
        (HAPPY_PATH[:3], S.OPENING_TARGET),  # the keyword is typed, nothing is read yet
        (HAPPY_PATH[:4], S.CLICKING_RESULT),  # a page has to be read before a result is clicked
        (HAPPY_PATH[:9], S.COMPLETED),  # an interaction ends before the result is captured
        (HAPPY_PATH, S.DWELLING),  # nothing leaves a terminal state
        (HAPPY_PATH[:3], S.INITIALIZING),  # no going back
        (HAPPY_PATH[:3], S.PAUSED),  # PAUSED only via pause()
    ],
)
def test_illegal_transitions_are_rejected(path: list[S], illegal: S) -> None:
    machine = SessionStateMachine()
    advance(machine, *path)
    before = machine.state
    with pytest.raises(InvalidStateTransitionError):
        machine.transition(illegal)
    assert machine.state is before


@pytest.mark.parametrize("steps", range(1, len(HAPPY_PATH)))
def test_every_active_state_can_fail_or_stop(steps: int) -> None:
    for finish in (SessionStateMachine.fail, SessionStateMachine.stop):
        machine = SessionStateMachine()
        advance(machine, *HAPPY_PATH[:steps])
        finish(machine)
        assert machine.state in {S.FAILED, S.STOPPED} and machine.is_terminal
        finish(machine)  # finishing twice is harmless


def test_pause_resumes_into_the_interrupted_state() -> None:
    machine = SessionStateMachine()
    advance(machine, *HAPPY_PATH[:9])
    assert machine.state is S.DWELLING
    machine.pause()
    assert machine.is_paused and not machine.is_terminal
    with pytest.raises(InvalidStateTransitionError):
        machine.transition(S.INTERACTING)  # nothing happens while paused
    assert machine.resume() is S.DWELLING
    machine.transition(S.INTERACTING)


def test_paused_session_can_still_be_stopped_or_fail() -> None:
    for finish, expected in (
        (SessionStateMachine.stop, S.STOPPED),
        (SessionStateMachine.fail, S.FAILED),
    ):
        machine = SessionStateMachine()
        advance(machine, *HAPPY_PATH[:4])
        machine.pause()
        finish(machine)
        assert machine.state is expected
        with pytest.raises(InvalidStateTransitionError):
            machine.resume()


def test_pause_and_finish_require_a_started_session() -> None:
    machine = SessionStateMachine()
    for call in (machine.pause, machine.resume, machine.fail, machine.stop):
        with pytest.raises(InvalidStateTransitionError):
            call()
    advance(machine, *HAPPY_PATH)
    with pytest.raises(InvalidStateTransitionError):
        machine.pause()


def test_allowed_from_matches_documentation() -> None:
    """The sets docs/AUTOMATION_ENGINE.md describes as the three journeys, exactly."""
    assert SessionStateMachine.allowed_from(S.ANALYZING_RESULTS) == {
        S.TARGET_FOUND,
        S.TARGET_NOT_FOUND,
        S.FAILED,
        S.STOPPED,
    }
    assert SessionStateMachine.allowed_from(S.INITIALIZING) == {
        S.OPENING_SEARCH,  # a journey that searches: Google's page comes first
        S.SEARCHING,  # a session recorded when a provider answered instead
        S.TARGET_FOUND,  # direct visit: nothing had to be found
        S.STARTING_BROWSER,  # a direct visit starts its own browser
        S.FAILED,
        S.STOPPED,
    }
    assert SessionStateMachine.allowed_from(S.OPENING_SEARCH) == {
        S.SEARCHING,
        S.ENTERING_KEYWORD,
        S.FAILED,
        S.STOPPED,
    }
    assert SessionStateMachine.allowed_from(S.COMPLETED) == frozenset()


# -- run control ------------------------------------------------------------------- #
async def test_sleep_waits_and_stop_interrupts_it() -> None:
    control = RunControl()
    started = time.monotonic()
    await control.sleep(0.25)
    assert 0.2 <= time.monotonic() - started < 1.0

    async def stop_soon() -> None:
        await asyncio.sleep(0.2)
        control.stop()

    stopper = asyncio.create_task(stop_soon())
    started = time.monotonic()
    with pytest.raises(SessionCancelledError):
        await control.sleep(30)
    assert time.monotonic() - started < 1.5
    await stopper
    assert control.is_stopped


async def test_pause_holds_sleep_until_resume() -> None:
    control = RunControl()
    control.pause()
    assert control.is_paused

    async def resume_later() -> None:
        await asyncio.sleep(0.4)
        control.resume()

    resumer = asyncio.create_task(resume_later())
    started = time.monotonic()
    await control.sleep(0.2)
    assert time.monotonic() - started >= 0.55  # waited for the resume, then slept
    await resumer
    assert not control.is_paused


async def test_stop_releases_a_paused_wait() -> None:
    control = RunControl()
    control.pause()
    asyncio.get_running_loop().call_later(0.2, control.stop)
    with pytest.raises(SessionCancelledError):
        await control.wait_while_paused()
    control.pause()  # cannot pause after stop
    assert not control.is_paused


# -- dwell and schedule -------------------------------------------------------------- #
def test_choose_dwell_stays_inside_the_range() -> None:
    rng = random.Random(1)
    values = [choose_dwell(5, 15, rng) for _ in range(500)]
    assert all(5 <= value <= 15 for value in values)
    assert max(values) - min(values) > 5  # actually varies
    assert choose_dwell(7, 7, rng) == 7
    with pytest.raises(ValueError, match="exceeds"):
        choose_dwell(10, 5, rng)


def test_dwell_choice_is_reproducible_from_the_seed() -> None:
    first = [choose_dwell(1, 60, random.Random(42)) for _ in range(3)]
    assert first[0] == first[1] == first[2]
    assert choose_dwell(1, 60, random.Random(43)) != first[0]


@pytest.mark.parametrize("max_actions", [1, 2, 5, 8, 20])
def test_schedule_respects_bounds(max_actions: int) -> None:
    dwell = 40.0
    for seed in range(60):
        schedule = build_schedule(dwell, max_actions, random.Random(seed))
        assert max(1, max_actions // 2) <= len(schedule) <= max_actions
        times = [action.at_seconds for action in schedule]
        assert times == sorted(times)
        assert all(
            dwell * EARLIEST_ACTION_FRACTION <= at <= dwell * LATEST_ACTION_FRACTION for at in times
        )


def test_schedule_is_empty_when_nothing_is_to_be_done() -> None:
    assert build_schedule(30, 0, random.Random(0)) == []
    assert build_schedule(0, 5, random.Random(0)) == []


def test_every_fourth_action_visits_another_page() -> None:
    schedule = build_schedule(60, 12, random.Random(3))
    by_position = sorted(schedule, key=lambda action: action.at_seconds)
    visits = [
        i + 1
        for i, action in enumerate(by_position)
        if action.kind is ActionKind.VISIT_INTERNAL_PAGE
    ]
    assert visits == [n for n in range(1, len(schedule) + 1) if n % INTERNAL_VISIT_EVERY == 0]
    kinds = {action.kind for action in build_schedule(60, 20, random.Random(5))}
    assert ActionKind.SCROLL_DOWN in kinds


def test_schedule_is_reproducible() -> None:
    assert build_schedule(30, 6, random.Random(9)) == build_schedule(30, 6, random.Random(9))


# -- small models --------------------------------------------------------------------- #
def test_screenshot_naming_is_safe_and_meaningful() -> None:
    assert slugify("Gold Ring!") == "gold-ring"
    assert slugify("../../etc/passwd") == "etc-passwd"
    assert slugify("???") == "keyword"
    assert len(slugify("x" * 200)) <= 40
    assert (
        screenshot_filename(102, "gold", ScreenshotKind.SEARCH_RESULTS)
        == "session_102_keyword_gold_search_results.png"
    )  # the kind of a session recorded years from now still names a file
    for kind in ScreenshotKind:
        name = screenshot_filename(1, 'a/b\\c:"d"', kind)
        assert "/" not in name and "\\" not in name and ":" not in name


def test_screenshot_policy_switches() -> None:
    policy = ScreenshotPolicy(target_opened=False, error=True)
    assert not policy.allows(ScreenshotKind.TARGET_OPENED)
    assert policy.allows(ScreenshotKind.ERROR)


def test_a_journey_never_photographs_a_results_page() -> None:
    """``SEARCH_RESULTS`` only exists for old sessions, so no policy can enable it."""
    for policy in (ScreenshotPolicy(), ScreenshotPolicy(True, True, True)):
        assert not policy.allows(ScreenshotKind.SEARCH_RESULTS)


def test_proxy_settings_never_reveal_the_password() -> None:
    proxy = ProxySettings("lab", "SOCKS5", "10.0.0.2", 1080, "user", "s3cret")
    assert proxy.server == "socks5://10.0.0.2:1080"
    assert "s3cret" not in repr(proxy)
    assert ProxySettings("p", "HTTP", "h", 3128).server == "http://h:3128"


# -- network fence ----------------------------------------------------------------------- #
def test_fence_lists_only_the_approved_origins() -> None:
    from src.automation.network_fence import UNREACHABLE_PROXY, bypass_entries, fence_arguments
    from src.services.safety_service import AllowedOrigin

    origins = [
        AllowedOrigin("127.0.0.1", 8765),
        AllowedOrigin("127.0.0.1", 8766),
        AllowedOrigin("127.0.0.1", 8766),  # duplicate
        AllowedOrigin("staging.shop.test"),  # standard ports: both 80 and 443
        AllowedOrigin("::1", 9000),
    ]
    assert bypass_entries(origins) == [
        "127.0.0.1:8765",
        "127.0.0.1:8766",
        "[::1]:9000",
        "staging.shop.test:443",
        "staging.shop.test:80",
    ]
    proxy, bypass = fence_arguments(origins)
    assert proxy == f"--proxy-server=http://{UNREACHABLE_PROXY}"
    assert bypass.startswith("--proxy-bypass-list=<-loopback>;")  # other local services stay fenced
    assert "*" not in bypass and "staging.shop.test:80" in bypass


def test_fence_without_origins_blocks_everything() -> None:
    from src.automation.network_fence import fence_arguments

    assert fence_arguments([])[1] == "--proxy-bypass-list=<-loopback>"


@pytest.mark.parametrize(
    "path",
    [
        # the browser channel: Chromium reads the results page, then the visit
        [S.INITIALIZING, S.OPENING_SEARCH, S.SEARCHING, S.ANALYZING_RESULTS, S.TARGET_FOUND],
        # ... and the same journey when the site is not on the page
        [
            S.INITIALIZING,
            S.OPENING_SEARCH,
            S.SEARCHING,
            S.ANALYZING_RESULTS,
            S.TARGET_NOT_FOUND,
            S.CAPTURING_RESULT,
            S.COMPLETED,
        ],
        # the API channel: the provider answers before a browser exists
        [S.INITIALIZING, S.SEARCHING, S.ANALYZING_RESULTS, S.TARGET_FOUND, S.STARTING_BROWSER],
        # a direct visit: the shortcut is INITIALIZING -> TARGET_FOUND, not a browser step
        [S.INITIALIZING, S.TARGET_FOUND, S.STARTING_BROWSER, S.OPENING_TARGET],
    ],
)
def test_each_channel_is_a_legal_path(path: list[S]) -> None:
    machine = SessionStateMachine()
    advance(machine, *path)
    assert machine.state is path[-1]


def test_direct_visit_shortcut_is_a_legal_transition() -> None:
    """A direct visit finds nothing, so "found" is not a step it has to take.

    The order that matters is the other way round: the browser exists before the tab
    is aimed at the target, and a session that is still on the search page cannot
    claim to be opening the target - that would let a journey visit a site it never
    saw listed.
    """
    for path in (
        [S.INITIALIZING, S.TARGET_FOUND, S.STARTING_BROWSER, S.OPENING_TARGET, S.DWELLING],
        [S.INITIALIZING, S.STARTING_BROWSER, S.OPENING_TARGET, S.DWELLING],
    ):
        machine = SessionStateMachine()
        advance(machine, *path)
        assert machine.state is S.DWELLING
    other = SessionStateMachine()
    advance(other, S.INITIALIZING, S.OPENING_SEARCH, S.ENTERING_KEYWORD)
    with pytest.raises(InvalidStateTransitionError):
        other.transition(S.OPENING_TARGET)
