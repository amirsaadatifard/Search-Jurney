# Search Journey Automation & Web Interaction Testing Platform

> Design and implementation of an automated system for simulating search journeys and
> controlled website interaction for web testing and behavioural analysis.

A desktop application (Python + PySide6) for defining, running and analysing **controlled** search-journey
test scenarios: open a search page, type a keyword, locate a target site in the results, open it, stay
for a configured dwell time while scrolling, record every step, and report on the outcome. You can watch
each run live.

It is a university capstone project. Everything runs against a **local search-engine simulator** and a
**local sample website** that ship with the project, or against development/staging sites you explicitly
approve.

## University use case

Web-testing and behavioural-analysis coursework: a repeatable way to exercise a website along a user
journey, with a full audit trail (timeline, screenshots, settings snapshot, seed) and exportable results.
The project is designed to be demonstrated live ([Demo mode](#demo-mode)), reviewed as source code, and
described in a report ([docs/PROJECT_REPORT_NOTES.md](docs/PROJECT_REPORT_NOTES.md)).

## Safety scope

This tool is for QA testing and research in environments you control. By design it:

- runs only against `localhost`, the bundled servers and explicitly approved test sites;
- refuses public search engines and cannot be used to influence rankings, CTR, impressions or analytics;
- implements **no** CAPTCHA solving, anti-bot evasion, fingerprint spoofing, IP rotation, proxy scraping
  or ad clicking;
- treats proxy profiles purely as a network-testing setting for controlled environments.

How this is enforced in code: [docs/SAFETY.md](docs/SAFETY.md).

## Features

| Area | What you get |
| --- | --- |
| Scenarios | Create, edit, duplicate, delete, enable, search, filter, sort; validation on every field |
| Keywords | Library with tags and notes, bulk add, TXT/CSV import, CSV export |
| Target sites | Approved-sites allowlist with environments; approval needs an explicit confirmation |
| Profiles | Browser profiles (viewport, locale, time zone, headless …); proxy profiles with secure password storage and a connection test |
| Local test environment | FastAPI search simulator (target at rank 1-10 or absent) and a five-page sample website |
| Automation | Playwright/Chromium journeys, 17-state state machine, pause / resume / stop, safe scrolling and page visits |
| Live view | Current step, URL, target position, session clock, dwell countdown, live browser picture, event timeline |
| Records | Session history with timeline, settings snapshot, errors and screenshots; reproducible (seed stored) |
| Insight | Dashboard (KPIs + 4 charts), logs viewer, filtered reports exported as CSV, Excel (4 sheets) or JSON |
| Operations | Settings, database backup/restore with confirmation, configuration export/import, light/dark/system theme |
| One click | **Run demo** on the dashboard, or `--demo` on the command line |

## Technology stack

| Area | Technology |
| --- | --- |
| Language | Python 3.12+ (developed on 3.14) |
| GUI | PySide6 (Qt Widgets, Qt SVG) |
| Browser automation | Playwright (Chromium) |
| Local servers | FastAPI + Uvicorn + Jinja2 |
| Persistence | SQLite, SQLAlchemy 2, Alembic |
| Validation | Pydantic 2 |
| Logging | structlog, rotating JSON-lines files |
| Reports | pandas, openpyxl |
| Secrets | `keyring` (Windows Credential Manager, macOS Keychain, Secret Service) |
| Quality | pytest, pytest-qt, pytest-asyncio, ruff, black, mypy |

## Installation (Windows 10 / 11)

You need Python 3.12 or newer. From the project folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
```

If PowerShell refuses to run the activation script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
once, or activate with `.venv\Scripts\activate.bat` from `cmd.exe`.

macOS / Linux: `source .venv/bin/activate` instead of the activation line; the rest is identical.

> **Windows long paths.** PySide6 installs deeply nested files. If the project is in a very deep folder
> and `pip` fails with path errors, create the environment at a short path
> (`python -m venv C:\venvs\sjat`) or enable Win32 long paths. Keeping the project itself at a short path
> such as `C:\Search Journey` also avoids trouble.

## Running

```powershell
python -m src.main
```

On start-up the application verifies its folders, initialises logging, migrates the database, starts the
two local servers, runs environment checks (see **About**) and opens the window.

**Like a normal program (no console window):** create a desktop shortcut once with

```powershell
python scripts/create_shortcut.py
```

and start it by double-clicking the *Search Journey Lab* icon.

Other entry points:

| Command | Purpose |
| --- | --- |
| `python -m src.main --demo` | Open the app and run the demo immediately |
| `python -m src.local_servers` | Start only the simulator (`:8765`) and sample site (`:8766`) to look at them in a browser |
| `python -m src.main --smoke-test` | Start, then exit (used for checks) |

## Demo mode

1. Start the application. The first launch creates a demo browser profile, a local target site, five
   keywords and a demo scenario, so it works immediately.
2. Press **Run demo** on the Dashboard (or start with `--demo`). It starts the local servers if needed,
   repairs the demo data if you changed it, and runs **one** safe journey.
3. The **Live Session View** opens: watch the step, URL, countdown, event timeline and the browser picture.
   Use Pause, Resume and Stop at any time.
4. When it finishes, open **Session History** (double-click the row) and **Reports**.

## How a session works

```
open search page → type keyword → submit → parse ten results → find target
   → click it → dwell (scroll, visit another page of the site) → screenshot → finish
```

Every step is a state of a validated state machine, is written to the database as an event, and is
shown live. Details: [docs/AUTOMATION_ENGINE.md](docs/AUTOMATION_ENGINE.md).

## Configuration

Everything works with defaults. To change locations or ports, use **Settings** (it writes `.env`; restart
afterwards) or copy `.env.example` to `.env`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SJAT_DATA_DIR` | `data` | Base folder for database, logs, screenshots, backups, exports |
| `SJAT_DATABASE_FILE` / `SJAT_LOGS_DIR` / `SJAT_SCREENSHOTS_DIR` | inside the data folder | Optional separate locations |
| `SJAT_SEARCH_PORT` / `SJAT_TARGET_PORT` | `8765` / `8766` | Ports of the two local servers (1024-65535, different) |
| `SJAT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## Data locations

| Content | Location |
| --- | --- |
| Database | `data/sjat.db` |
| Logs (rotating JSON lines) | `data/logs/application.log` |
| **Screenshots** | `data/screenshots/YYYY-MM-DD/session_<id>_keyword_<keyword>_<moment>.png` |
| Backups | `data/backups/` |
| Exports | `data/exports/` |

## Database architecture

SQLite with SQLAlchemy 2 and Alembic migrations (applied automatically). Nine tables: `target_sites`,
`browser_profiles`, `proxy_profiles`, `scenarios`, `automation_sessions`, `session_events`, `screenshots`,
`keywords`, `application_settings`. A session stores a snapshot of its scenario so history keeps its
meaning after edits. Diagram and rules: [docs/DATABASE.md](docs/DATABASE.md).

## Testing

```powershell
pytest                 # all tests (Qt runs offscreen; no windows open)
pytest tests/unit      # fast, no browser
pytest -m e2e          # real Chromium + both local servers
ruff check src tests
black --check src tests
mypy src
```

Unit, integration and end-to-end levels, what each proves and the defects the tests found:
[docs/TESTING.md](docs/TESTING.md).

## Project structure

```
src/
  main.py            entry point
  app/               bootstrap, container, config, paths, health checks
  domain/            enums, exceptions, validation schemas
  database/          engine, ORM models, repositories, migrator
  services/          business rules (scenarios, safety, reports, backup, …)
  automation/        engine interface, state machine, runner, Playwright services
  local_servers/     search simulator, sample website, server manager
  networking/        credential store, proxy tester
  reports/           CSV / Excel / JSON exporters
  logging_config/    structured logging
  ui/                main window, theme, pages, dialogs, widgets
migrations/  tests/  docs/  data/  scripts/
```

Architecture overview: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Documentation

| Document | Content |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, threads, composition root, decisions |
| [docs/DATABASE.md](docs/DATABASE.md) | Tables, relationships, constraints, migrations |
| [docs/AUTOMATION_ENGINE.md](docs/AUTOMATION_ENGINE.md) | State machine, runner, dwell, pause/stop, live view |
| [docs/SAFETY.md](docs/SAFETY.md) | Safety rules, allowlist, network fence |
| [docs/TESTING.md](docs/TESTING.md) | Test levels, coverage map, defects found |
| [docs/PROJECT_REPORT_NOTES.md](docs/PROJECT_REPORT_NOTES.md) | Material for the university report |

## Known limitations

- Chromium only; one automation run at a time.
- With a proxy profile the network fence cannot be applied: redirects leaving the approved sites are
  detected and stop the session, but the proxy may already have received the request.
- The live view is a picture refreshed about once a second, not video (Playwright cannot be embedded in a
  `QWebEngineView`).
- The public-search-engine blocklist covers well-known engines; the allowlist is the primary control.
- English-only interface; tested on Windows 11 with offscreen rendering and a few real launches.

## Future improvements

- A guard proxy inside the application that fences proxy mode too.
- More browsers and parallel sessions (the run service is isolated for this).
- Form-filling interactions on the sample site, scheduled runs, run comparison.
- Persian localisation and a packaged installer (PyInstaller).

## Licence

Academic project. MIT licence declared in `pyproject.toml`.
