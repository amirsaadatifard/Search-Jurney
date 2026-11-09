# Architecture

The application is a layered desktop program. The rule that shapes everything: **the GUI contains no
business rules, and the business rules know nothing about Qt.** That is what makes the services and
the automation runner testable without a window, and the GUI testable without a browser.

## Layers and dependencies

```
src/ui              PySide6 windows, pages, dialogs, live run view
  ├─▶ src/services      called from the GUI
  └─▶ src/automation    observed through Qt signals

src/services        business rules: scenarios, keywords, target sites, browser and
  │                   proxy profiles, safety, reports, dashboard,
  │                   backup, settings, demo
  ├─▶ src/database      models, repositories, Alembic
  ├─▶ src/networking    OS credential store, proxy connection test
  └─▶ src/domain        enums, exceptions, validation schemas (depends on nothing)

src/automation        session runner, state machine, engine interface, Playwright
                      engine, run service, the Google search step (page knowledge,
                      page actions, the walk over result pages, the journal)
```

Allowed dependency direction is **downwards only**. `src/domain` imports nothing from the project;
`src/database` imports only `domain`; services import database, domain, networking; automation imports
services and domain. No package below `ui` opens a browser: the ranking arrives as a read of Google's own
results page, and the browser is driven exclusively by `src/automation` - which is the only package that
imports Playwright, and the only one allowed to open a search engine. `src/app` is the *composition root*: the only place that knows
every concrete class.

| Package | Responsibility |
| --- | --- |
| `app/` | `AppConfig` (environment/`.env`), `AppPaths`, constants, health checks, `bootstrap()` → `AppContainer` |
| `domain/` | Enums, exception hierarchy, Pydantic input schemas |
| `database/` | Engine and sessions, ORM models, repositories, Alembic runner |
| `services/` | All business rules (see below) |
| `automation/` | Browser automation: engine interface, state machine, runner, run service, the Google search step |
| `local_servers/` | Bundled sample website and `LocalServerManager` - a page to visit, never a search source |
| `networking/` | Credential store (OS keyring), proxy connection tester |
| `reports/` | CSV / Excel / JSON exporters |
| `logging_config/` | structlog setup, rotating JSON file, in-memory buffer for the GUI |
| `ui/` | PySide6 interface |

## Composition root and dependency injection

`bootstrap()` creates directories, logging, the database (migrating it), the safety service, every other
service and the run service, and returns an `AppContainer`. Nothing uses module-level singletons:

- the GUI receives the container and hands individual services to pages through `PageContext`;
- tests call `bootstrap()` with temporary paths and an in-memory credential store;
- the automation runner receives its engine, recorder, safety service and observer through the
  constructor, which is how it is tested with a fake engine.

## Threads and data flow

| Thread | Runs | Talks to the GUI through |
| --- | --- | --- |
| GUI (main) | Qt event loop, all widgets | - |
| Automation worker | one `asyncio` loop per run: Playwright, state machine, dwell clock | `RunObserver` → `QtRunObserver` signals |
| Server threads | uvicorn for the bundled sample website | the search API needs no server of its own |
| Qt thread pool | report generation, exports, backups, proxy tests | `TaskRunner` signals |

The automation thread never touches a widget. It calls `RunObserver` methods; `QtRunObserver` turns
each call into a Qt signal, and because its receivers live in the GUI thread, Qt delivers them through
the event queue. `RunController` folds the signals into one model that the Runner page, the Live
Session View and the Dashboard all render, so the pages always agree. Pause/Stop travel the other way
through a small thread-safe `RunControl` object that the worker polls at safe boundaries.

## The automation core

See [AUTOMATION_ENGINE.md](AUTOMATION_ENGINE.md). In short: `SessionRunner` drives one session through
a validated state machine against the abstract `AutomationEngine`. Each iteration first *searches*: the
browser opens Google's result page for the keyword, and the target's position is read out of the pages
until it turns up — then the journey reaches the site by clicking that result. The Playwright implementation is
assembled from single-purpose services (browser manager, target actions, interaction service, screenshot
service). Everything the browser may reach is decided by the central `DomainSafetyService`
([SAFETY.md](SAFETY.md)).

## Error handling

All expected failures derive from `AppError` and carry a user-readable message; forms get per-field
messages (`InvalidConfigurationError.field_errors`). A failing iteration is recorded and the run
continues; unexpected exceptions are logged with a traceback and shown as a generic message. Background
tasks and GUI actions convert `AppError` into a toast instead of letting it escape.

## Persistence

SQLite through SQLAlchemy 2, migrations through Alembic (applied automatically at start-up). Repositories
are the only code that queries; services open short transactions with `Database.session_scope()`.
Details in [DATABASE.md](DATABASE.md).

## Configuration

Two levels, on purpose:

- **Start-up configuration** (`.env`, environment): data/database/log/screenshot locations, the port of
  the bundled sample website, log level. Needed before the database exists.
- **User settings** (database, `application_settings`): theme, automation defaults, screenshot policy,
  first-run flag. Included in configuration export.

## Notable design decisions

| Decision | Reason |
| --- | --- |
| Runner written against an abstract engine | Every ordering, pause, stop and failure rule is tested in milliseconds without a browser; the same runner is then run end to end against Chromium |
| Network fence in addition to a request guard | Playwright cannot intercept redirect hops; found by testing, fixed below the application (see SAFETY.md) |
| Plans are immutable snapshots | Editing a scenario mid-run cannot change that run; the snapshot doubles as the audit record |
| Timestamps stored as naive UTC | SQLite has no time zone type; conversion happens only for display |
| Live view = periodic screenshots | Playwright cannot drive a `QWebEngineView`; screenshots work for headless profiles too |
| Painter-based charts | Full control over mark specs and theming, no extra chart dependency |
| Secrets only in the OS credential store | Never in the database, logs, snapshots or exports |
| Search data read from the results page in the browser, not from an API | An API answer is a *different* ranking (no personalisation, no location) and needs a key, a quota and a second code path; the page the test browser was shown is the ranking the journey actually followed. The cost is honesty about terms of service: automated access is against them, so the exception is kept narrow — one query page at a time, no evasion, and a refusal ends the session |
| Every read is stored as an observation | A result page is not reproducible the way an API answer is, so the exact links that were seen are saved with the session; a claim of rank can be audited against them |
| A local target skips the search step | The bundled sample site and private development hosts are not in Google's index; asking would only burn the pacing budget, and inventing a position would corrupt the record |

## Directory map

```
src/main.py            entry point (python -m src.main)
src/app/               bootstrap, container, config, paths, health, constants
src/domain/            enums, exceptions, schemas
src/database/          base, session, migrator, models/, repositories/
src/services/          business rules
src/automation/        engine, state machine, runner, search walk, Playwright services
src/local_servers/     target_site/, server_manager
src/networking/        credentials, proxy_manager
src/reports/           csv / excel / json exporters
src/logging_config/    structured logging
src/ui/                main_window, theme, icons, run_controller, pages/, dialogs/, widgets/
migrations/            Alembic environment and revisions
tests/                 unit/, integration/, e2e/, helpers/
docs/                  this documentation
```
