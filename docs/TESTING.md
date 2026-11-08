# Testing

The suite has three levels. Each level exists because the one below cannot prove what it proves.

| Level | Folder | What it uses | What it proves |
| --- | --- | --- | --- |
| Unit | `tests/unit/` | Pure code, in-memory or temporary SQLite, fake engine, Qt **offscreen** | Rules and logic: validation, safety, state machine, metrics, repositories, services, widgets and pages |
| Integration | `tests/integration/` | Real SQLite files, real Alembic, real local servers on loopback | Layers working together: migrations match the models, both servers behave over real sockets |
| End-to-end | `tests/e2e/` | **Real Chromium** (headless), both local servers, the real database, the real GUI | The product works: full journeys, pause/stop, safety blocking, proxy mode, the demo |

Qt tests use the `offscreen` platform (set in `tests/conftest.py`), so no window ever opens on your
desktop. All tests write to temporary folders; none touches your real data, `.env` or credentials
(a single round-trip with a throw-away key checks the real OS credential store and cleans up).

**Current state:** 489 tests, all passing (about six minutes in total on the development machine, most of
it the real-browser end-to-end tests).

## Running

From the project folder, with the virtual environment active:

```powershell
pytest                       # everything
pytest tests/unit            # fast: no browser
pytest -m e2e                # only the end-to-end tests (needs Chromium)
pytest tests/e2e/test_automation_e2e.py -k redirect
ruff check src tests ; black --check src tests ; mypy src
```

End-to-end tests need the browser once: `python -m playwright install chromium`.
If Chromium is missing they fail with that instruction rather than skipping silently.

## What is covered

The project brief required tests for the areas below; each maps to files here.

| Required area | Tests |
| --- | --- |
| URL validation | `unit/test_safety_service.py` (normalisation, 18 rejection cases) |
| Domain allowlist | `unit/test_safety_service.py`, `unit/test_management_services.py` |
| Scenario validation, dwell range | `unit/test_management_services.py` (parametrised per field), `unit/test_repositories.py` (database check constraints) |
| Repositories | `unit/test_repositories.py`, `integration/test_migrations.py` |
| Report calculations | `unit/test_reports.py` (metrics with hand-computed numbers, filters, exporters) |
| Local search simulator | `integration/test_local_servers.py` |
| Target discovery | `integration/test_local_servers.py`, `unit/test_session_runner.py`, `e2e/` (positions 1, 4, 7, 10 and not listed) |
| Session state machine | `unit/test_automation_core.py` (every legal and illegal transition), `unit/test_session_runner.py` |
| Configuration import/export | `unit/test_phase10_services.py` |
| End-to-end with both local sites | `e2e/test_automation_e2e.py`, `e2e/test_runner_ui_e2e.py`, `e2e/test_demo_e2e.py` |

Also covered: pause/resume/stop (unit and real browser), the network fence and redirect blocking,
proxy runs through a real local proxy, credential handling, backup/restore including failure and
roll-back, CSV/Excel/JSON export, all pages and dialogs, charts, the theme system, migrations.

## Techniques worth naming in a report

- **Test double for the browser.** `FakeEngine` implements `AutomationEngine`, records calls and can fail
  at any step, so ordering, error handling, stop and pause are checked deterministically.
- **Real fakes for the network.** `tests/helpers/fake_proxy.py` is a small loopback HTTP proxy and SOCKS5
  server, used to test proxy mode and the connection tester against real sockets.
- **Parametrised boundaries.** Every dwell, port, host and URL rule is tested at and beyond its limit.
- **Schema drift test.** The migrated database is compared with the ORM metadata.
- **Property-style checks.** Schedules and chart ticks are checked over many seeds/values for their
  invariants (bounds, ordering, coverage) rather than one example.
- **Qt event-loop discipline.** Cross-thread behaviour is tested by emitting from real threads and waiting
  on the GUI side with `qtbot`.

## Defects the tests found

The suite earned its keep. These were real problems, caught by tests rather than by users:

| Found by | Defect |
| --- | --- |
| End-to-end redirect test | Playwright does not call request handlers for redirect hops, so an approved site could redirect the browser anywhere before any check ran. Fixed with a network-level fence |
| End-to-end revocation test | Revoking a site's approval mid-session did not stop a session that was only dwelling. The allowlist is now re-checked every 0.25 s |
| Export test | Spreadsheet formula injection was not neutralised, because pandas 3 stores text in its own dtype, not `object` |
| Repository test | A scenario with "target not listed" was saved with a default position of 3 |
| Table test | Chronological tables were sorted alphabetically ("Apr" before "Mar") |
| UI test | A signal named `event` shadowed `QObject.event()` (caught by mypy) |
| UI test | A pydantic error text leaked into a user message |
| Proxy tests | Misleading messages for a wrong protocol or a proxy that needs credentials |

## Quality gates

`ruff` (lint), `black` (format), `mypy` (types, `disallow_untyped_defs`) run clean on `src`; the same
configuration lives in `pyproject.toml`.
