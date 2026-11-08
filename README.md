# Search Journey Automation & Web Interaction Testing Platform

> Design and implementation of an automated system for simulating search journeys and
> controlled website interaction for web testing and behavioural analysis.

A desktop application (Python + PySide6) for defining, running and analysing **controlled** search-journey
test scenarios: look up a keyword on Google, see where your site actually ranks in those results, open it,
stay for a configured dwell time while scrolling, record every step, and report on the outcome. You can
watch each run live.

It is a university capstone project. Search data is **real**: it comes from Google's official
[Custom Search JSON API](https://developers.google.com/custom-search/v1/overview), which needs your own
API key and search-engine id (two minutes to set up — see [Google setup](#google-setup)). Pages are opened
only for sites you approve: your own production site, a development/staging site, or the **bundled local
sample website** that ships with the project for offline practice.

## Google setup

1. Enable *Custom Search API* in a Google Cloud project and create an **API key**
   (Cloud Console → APIs & Services → Credentials).
2. Create a [Programmable Search Engine](https://programmablesearchengine.google.com/controlpanel/create)
   and select **"Search the entire web"** — otherwise it only searches the sites you list and reports
   nothing about yours. Copy its **search engine id** (`cx`).
3. In the application open **Google Research** → *Google credentials and system checks*, enter both
   values and press *Save credentials*. The key goes into the operating system's credential store (never
   into the database); the engine id is a plain setting. *Test Google connection* then confirms them with
   a single query, and *Run system checks* reports key, quota reachability, Chromium and the allowlist.

What the API's limits mean for this tool: ten results per page and at most one hundred per query, a free
daily quota (Google bills beyond it), and a ranking that is *this API's* ranking — personalisation,
location and browser history are not applied, so it can differ from what you see on google.com.
The observed position is always labelled with the provider that produced it, and a session that cannot
reach Google **fails** rather than falling back to made-up data.

## University use case

Web-testing and behavioural-analysis coursework: a repeatable way to exercise a website along a user
journey, with a full audit trail (timeline, screenshots, settings snapshot, seed) and exportable results.
The project is designed to be demonstrated live ([Demo mode](#demo-mode)), reviewed as source code, and
described in a report ([docs/PROJECT_REPORT_NOTES.md](docs/PROJECT_REPORT_NOTES.md)).

## Safety scope

This tool is for QA testing and research in environments you control. By design it:

- opens only explicitly approved test sites (plus the bundled `localhost` sample website);
- reads Google through its documented API and never browses, clicks or scrolls inside a search engine, so
  it cannot influence rankings, CTR, impressions or analytics;
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
| Search data | Real Google results via the official Custom Search JSON API: rank, URL, snippet and result count per keyword, stored as an observation |
| Sample website | A five-page local site (FastAPI) to practise visits on, with long pages, scrollable content and safety fixtures |
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

On start-up the application verifies its folders, initialises logging, migrates the database, runs
environment checks (see **About**) and opens the window. The bundled sample website is started when the
application needs it — a journey against a real domain binds no local port at all.

**Like a normal program (no console window):** create a desktop shortcut once with

```powershell
python scripts/create_shortcut.py
```

and start it by double-clicking the *Search Journey Lab* icon.

Other entry points:

| Command | Purpose |
| --- | --- |
| `python -m src.main --demo` | Open the app and run the demo immediately |
| `python -m src.local_servers` | Start only the bundled sample website (`:8766`) to look at it in a browser |
| `python -m src.main --smoke-test` | Start, then exit (used for checks) |

## Demo mode

1. Start the application. The first launch creates a demo browser profile, the sample site as an approved
   target, five keywords and a demo scenario, so it works immediately — and with no Google key, because
   that demo journey visits the local site instead of asking about its ranking.
2. Press **Run demo** on the Dashboard (or start with `--demo`). It starts the sample website if needed,
   repairs the demo data if you changed it, and runs **one** safe journey.
3. The **Live Session View** opens: watch the step, URL, countdown, event timeline and the browser picture.
   Use Pause, Resume and Stop at any time.
4. When it finishes, open **Session History** (double-click the row) and **Reports**.

## How a session works

```
ask Google for the keyword → is the target site in the real results?
   ├─ no  → record "not listed" (a result, not an error) and stop: no page is loaded
   └─ yes → record the observed position → open the site directly
            → dwell (scroll, visit another page of the site) → screenshot → finish
```

Only the *visit* is a browser journey; the search itself is an API call, so no request ever reaches
google.com. A journey whose target Google cannot index (the local sample site, a private development
host) skips the ranking step and is recorded as a direct visit without a position.

Every step is a state of a validated state machine, is written to the database as an event, and is
shown live. Details: [docs/AUTOMATION_ENGINE.md](docs/AUTOMATION_ENGINE.md).

## Configuration

Everything works with defaults. To change locations or ports, use **Settings** (it writes `.env`; restart
afterwards) or copy `.env.example` to `.env`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SJAT_DATA_DIR` | `data` | Base folder for database, logs, screenshots, backups, exports |
| `SJAT_DATABASE_FILE` / `SJAT_LOGS_DIR` / `SJAT_SCREENSHOTS_DIR` | inside the data folder | Optional separate locations |
| `SJAT_TARGET_PORT` | `8766` | Port of the bundled sample website (1024-65535, bound to `127.0.0.1`) |
| `SJAT_GOOGLE_API_KEY` / `SJAT_GOOGLE_ENGINE_ID` | unset | Google search credentials for headless or development use; the stored key from **Google Research** wins |
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
pytest -m e2e          # real Chromium, the sample site and a local Google API double
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
  services/          business rules (scenarios, Google research, safety, reports, backup, …)
  search_providers/  the search API behind the ranking data (Google Custom Search JSON API)
  automation/        engine interface, state machine, runner, Playwright services, rank observer
  local_servers/     sample website, server manager
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
