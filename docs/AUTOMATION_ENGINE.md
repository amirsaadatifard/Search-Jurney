# Automation engine

The engine runs a scenario as a series of *sessions* (one per iteration). Each session follows the
same journey against the local, controlled environment:

```
open search page → type keyword → submit → read results → find target
   → open target → dwell (scroll / visit pages) → screenshot → finish
```

## Layers

| Module (`src/automation/`) | Responsibility |
| --- | --- |
| `models.py` | `SessionPlan` (immutable copy of a scenario), results, summaries |
| `plan_factory.py` | Builds a plan from a stored scenario and the application settings |
| `engine.py` | `AutomationEngine`: the abstract browser backend |
| `playwright_engine.py` | Chromium implementation, composed of the services below |
| `browser_manager.py` | Browser/context lifecycle, request guard, redirect watcher |
| `network_fence.py` | Chromium arguments that confine the browser to approved origins |
| `search_actions.py` / `target_actions.py` | Search-page and result-list operations |
| `interaction_service.py` | Dwell time choice, interaction schedule, scrolling, page visits |
| `screenshot_service.py` | Files under `data/screenshots/YYYY-MM-DD/` plus database rows |
| `state_machine.py` | Legal order of states, pause/resume, terminal states |
| `control.py` | Thread-safe pause / resume / stop signalling |
| `recorder.py` | Persists sessions and timeline events |
| `session_runner.py` | Orchestrates one iteration; owns the dwell clock and error handling |
| `run_service.py` | Background thread + asyncio loop used by the GUI; one run at a time |

The runner depends only on the abstract `AutomationEngine`. The unit tests drive the runner with a
fake engine, so every ordering, pause, stop and failure rule is verified without a browser; the
end-to-end tests then run the same runner against real Chromium.

## State machine

```
IDLE → INITIALIZING → STARTING_BROWSER → OPENING_SEARCH → ENTERING_KEYWORD → SEARCHING
     → ANALYZING_RESULTS ─┬→ TARGET_FOUND → OPENING_TARGET → DWELLING ⇄ INTERACTING
                          │                                     └→ CAPTURING_RESULT → COMPLETED
                          └→ TARGET_NOT_FOUND → CAPTURING_RESULT → COMPLETED
any active state → FAILED | STOPPED | PAUSED (resume returns to the interrupted state)
```

Illegal transitions raise `InvalidStateTransitionError`. Every transition updates the session row, so
the database always shows the current state; each timeline event also records the state it happened in.

A session that ends with `TARGET_NOT_FOUND` has state `COMPLETED` and status `TARGET_NOT_FOUND`: the
run worked, the answer was "not listed". That is a test result, not an error.

## Control: pause, resume, stop

`RunControl` holds two flags that the GUI thread sets and the automation thread polls:

- **Pause** takes effect at the next checkpoint (every state change and every 0.25 s of dwell). The
  machine enters `PAUSED`; resuming returns to the state it left. **Time spent paused does not count
  as dwell time.**
- **Stop** raises `SessionCancelledError` at the next checkpoint (or during any `control.sleep`). The
  session is closed as `STOPPED`, the browser context is closed normally, and remaining iterations
  are skipped. Nothing is killed. A stop also releases a paused session.
- On application exit `AutomationRunService.shutdown()` requests a stop and waits for the thread.

## Dwell time and interactions

- A dwell time is chosen uniformly in `[min, max]` (0.1 s resolution).
- Up to `max_scroll_actions` actions are scheduled across 10 %–85 % of the dwell: mostly scroll down,
  occasionally scroll up, and every fourth action opens another page of the *same* site, waits
  briefly and goes back.
- The browser works against a seeded random generator. The seed, the chosen dwell and the full
  schedule are stored in the session's `settings_snapshot`, so a session can be reproduced.

## Error handling

Every failure is caught per iteration, recorded (`ERROR` and `SESSION_FAILED` events, error type and
message on the session, an error screenshot if possible) and the run continues with the next
iteration. Exceptions: a browser that cannot start ends the run, because every further iteration
would fail the same way; a stop ends it by request.

| Situation | Exception | Result |
| --- | --- | --- |
| Chromium missing / cannot launch | `BrowserLaunchError` | Session failed, run aborted |
| Page does not load, element missing | `NavigationError`, `SearchError` | Session failed |
| Step exceeds the configured timeout | `AutomationTimeoutError` | Session failed |
| URL outside the approved scope | `SafetyViolationError` | Session failed, `SAFETY_BLOCKED` event |
| User pressed Stop | `SessionCancelledError` | Session `STOPPED` |
| Unexpected exception | any | Session failed, logged with traceback |

## Browser isolation and profiles

One Chromium instance is started per run. Each session gets its own browser context
(`incognito` on, the default); with it off, one context is reused and only cookies / local storage
are cleared according to the scenario. Viewport, locale, time zone, JavaScript, downloads and the
(truthful) user agent come from the browser profile; unset values use the browser's own defaults.

## Containing the browser

Two independent layers keep the browser inside the approved scope (see `SAFETY.md`):

1. **Request guard** - every request the page makes is checked against `DomainSafetyService` before it
   is sent; violations are aborted and recorded.
2. **Network fence** - Chromium itself is launched so that it can only connect to the approved origins.
   This is required because Playwright cannot intercept the *hops* of a redirect.

The allowlist is re-read at every checkpoint, so revoking a site's approval ends a running session
within a fraction of a second.

## GUI integration

The worker thread never touches widgets. `AutomationRunService` reports through a `RunObserver`;
`QtRunObserver` (in `src/ui/run_controller.py`) re-emits every callback as a Qt signal, which Qt
delivers on the GUI thread. `RunController` folds those signals into one model - current scenario,
iteration, state, URL, target position, dwell countdown, progress, timeline - that both the Runner
and the Live Session View render, so the two pages always agree. Pause and Stop are applied to the
model immediately so the buttons react at once; the automation thread confirms at its next checkpoint.

**Watching a run.** Playwright drives its own Chromium and cannot drive a `QWebEngineView`, so the
live view cannot embed the controlled browser itself. Instead the runner sends a small JPEG of the
page (about one frame per second while dwelling, and at each key step), which the Live Session View
shows; this also works for headless profiles. A browser profile with *headless* off additionally
opens the real Chromium window.

## Deliberate deviations from the original sketch

- `wait_dwell_time` is not an engine method. The dwell clock lives in the runner because it must be
  pausable, stoppable and report a live countdown, none of which is browser-specific.
- The interface adds `close()` (release the browser at the end of a run) and `drain_blocked()` (report
  requests the guard blocked), which the Playwright lifecycle and the timeline need.
- The runner records domain events (`SEARCH_SUBMITTED`, `TARGET_FOUND`, ...) rather than one event per
  state change; the state is stored on each event and on the session row.

## Known limitations

- Chromium only (Firefox and WebKit are not wired up).
- With a proxy profile the network fence cannot be applied (one proxy setting cannot do both jobs),
  so redirects are detected and recorded but not prevented at the network level. The session log
  says so. A PAC-based combination is planned for the proxy phase.
- One run at a time (`max concurrent sessions = 1`).
