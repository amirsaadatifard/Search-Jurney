# Project report notes

Working notes to help you write the university report. Everything here is true of the delivered
software; where a number or behaviour matters, the section names the file that proves it.

**Title:** *Search Journey Automation & Web Interaction Testing Platform*
**Academic description:** Design and implementation of an automated system for simulating search
journeys and controlled website interaction for web testing and behavioural analysis.

## 1. Problem definition

Web teams need to test how a site behaves along a *user journey*: someone searches, scans results,
opens a page, reads and scrolls. Doing this by hand is slow and unrepeatable; scripting it ad hoc yields
throw-away code with no record of what happened. A tool is needed that (a) runs such journeys
automatically and repeatably, (b) records every step so results can be analysed, and (c) cannot be
turned against sites the tester does not control.

The last point shapes the whole design. Search-journey automation is dual-use: pointed at a public
search engine it could manipulate rankings or traffic. This project therefore reads search data **only**
through Google's official, key-authenticated API and opens pages **only** on the bundled sample website
and sites the user explicitly approves — enforced in code, not by policy. The automated browser never
visits a search engine at all.

## 2. Goals and non-goals

**Goals**

1. Define test scenarios (keyword, target, dwell time, iterations, browser and proxy settings).
2. Run each scenario as a visible, controllable browser journey against a controlled environment.
3. Record a structured event timeline, metrics, screenshots and a reproducible snapshot per session.
4. Report: dashboard, history, filtered reports with CSV/Excel/JSON export.
5. Be safe by construction (allowlist, no public search engines, redirect blocking).
6. Be a maintainable, tested, documented desktop application suitable for review and demonstration.

**Non-goals (deliberately excluded)**: manipulating public search rankings, clicks or analytics;
CAPTCHA or bot-protection bypass; fingerprint spoofing; IP rotation; proxy scraping; ad clicking.

## 3. Functional requirements

| ID | Requirement | Where |
| --- | --- | --- |
| FR1 | Manage scenarios (create, edit, duplicate, delete, enable, search, filter, sort) with per-field validation | `services/scenario_service.py`, `ui/pages/scenarios_page.py` |
| FR2 | Manage a reusable keyword library (bulk add, TXT/CSV import, CSV export, tags, notes) | `services/keyword_service.py` |
| FR3 | Manage approved target sites with environments; automation never starts on an unapproved site | `services/target_site_service.py`, `safety_service.py` |
| FR4 | Manage browser profiles and proxy profiles (secure password storage, connection test with latency) | `services/proxy_service.py`, `networking/` |
| FR5 | Real ranking data: the observed position of the target site, read from Google's Custom Search JSON API and stored as an auditable observation | `search_providers/`, `services/google_research_service.py`, `automation/rank_observer.py` |
| FR6 | Local sample website with several long pages | `local_servers/target_site/` |
| FR7 | Run a journey: query the provider, find the target in its real results, open the site, dwell, scroll, record, repeat | `automation/` |
| FR8 | Watch execution live (step, URL, countdown, preview); Start / Pause / Resume / Stop | `ui/pages/runner_page.py`, `live_view_page.py` |
| FR9 | State machine with persisted transitions; event timeline in UI, file and database | `automation/state_machine.py`, `recorder.py` |
| FR10 | Screenshots by moment, stored by date with metadata | `automation/screenshot_service.py` |
| FR11 | Session history with details (timeline, settings, errors, screenshots) | `ui/pages/sessions_page.py` |
| FR12 | Dashboard with KPIs and four charts | `services/dashboard_service.py`, `ui/widgets/charts.py` |
| FR13 | Reports with filters and CSV, Excel (4 sheets), JSON export | `services/report_service.py`, `reports/` |
| FR14 | Settings, database backup and restore, configuration export/import | `services/backup_service.py`, `config_transfer_service.py` |
| FR15 | First-run demo data and one-click demo mode | `services/first_run_service.py`, `demo_service.py` |
| FR16 | Light, dark and system themes, remembered | `ui/theme.py` |

## 4. Non-functional requirements

| Quality | How it is met |
| --- | --- |
| Safety | Central safety service, request guard, network fence, re-validation every 0.25 s, no public engines |
| Reliability | One failed iteration never stops a run; crash recovery closes orphaned sessions; restore rolls back on failure |
| Responsiveness | Browser, report, export and backup work run off the GUI thread |
| Reproducibility | Each session stores the plan, random seed, chosen dwell and schedule |
| Security | Secrets in the OS credential store only; formula-injection-safe exports; validated paths and JSON; no dynamic code or shell execution |
| Maintainability | Layered architecture, dependency injection, typed code, ruff/black/mypy clean, small modules |
| Testability | Runner written against an interface; pure functions for scheduling, metrics and safety rules |
| Portability | Windows 10/11 first; pathlib, no Windows-only code except the optional shortcut script |
| Usability | Consistent pages, inline validation messages, confirmation for destructive actions, empty states, themes |

## 5. Architecture

Layered (UI → services → repositories/models → domain) with a composition root; see
[ARCHITECTURE.md](ARCHITECTURE.md). Suggested figures: the layer diagram; the thread/signal diagram
(automation thread → `QtRunObserver` → GUI); the component diagram of the automation package.

## 6. Database design

Nine tables, relationships and rules in [DATABASE.md](DATABASE.md). Points to discuss: database-level
check constraints as a second line of defence; `RESTRICT` versus `SET NULL` versus `CASCADE` choices;
the denormalised session snapshot (a deliberate trade-off for auditability); no password column.

## 7. Algorithms

**Journey (session runner).** For each iteration: check safety → start browser context → open search
page → type keyword → submit → parse ten results → find target → (not found: record and finish)
→ click the result → dwell → screenshot → finish. Every step passes through the state machine.

**State machine.** 17 states. Forward path is a straight line; `DWELLING ⇄ INTERACTING`; any active state
can `FAIL`, be `STOPPED` or `PAUSED` (resume returns to the interrupted state). Illegal transitions raise
`InvalidStateTransitionError`. A table of all transitions is in `automation/state_machine.py`.

**Target discovery.** Parse `li.result` items, normalise each URL, compare host and port with the target's
origin; the first match by position wins. Matching by origin means any page of the target site counts.

**Dwell and interaction schedule.** Dwell is uniform in `[min, max]`. Between ⌈max/2⌉ and `max` actions are
placed across 10-85 % of the dwell with jitter; mostly scroll-down, occasional scroll-up, every fourth a
visit to another page of the same site and back. A seeded generator makes the whole schedule reproducible.
Pause time is excluded from the dwell clock; interaction time is included.

**Safety check (`DomainSafetyService`).** Normalise the URL (scheme, credentials, IDNA host, port) →
refuse public search engines (brand label under any TLD) → require an allowlist match (exact host, exact
or standard ports). Redirects use the same rules. Details and the reasoning in [SAFETY.md](SAFETY.md).

**Network fence.** Chromium starts with a non-existent proxy and a bypass list holding only the approved
origins, so redirect hops, other ports on the computer and raw IPs fail before any connection.

**Report metrics.** `success rate = completed / (completed + failed + not found)`; stopped and running
sessions are excluded; averages ignore sessions without a value. Defined in `services/report_service.py`.

**Nice axis ticks (charts).** Round the step to 1, 2, 2.5, 5 or 10 × 10ⁿ so the maximum is covered.

## 8. Testing methodology

Three levels (unit, integration, end-to-end), a fake engine for the runner, real fakes for the network,
parametrised boundary tests and a schema-drift test; see [TESTING.md](TESTING.md). A strong report
section is the list of defects the tests found (redirect hops bypassing the guard, formula injection, ...).

## 9. Security considerations

- **Scope control** is enforced in code at three points: when a target is registered, when a session
  starts, and on every request/redirect while it runs.
- **Secrets**: proxy passwords only in the OS credential store; never logged, exported, snapshotted or
  shown. If no secure store exists, saving a password is refused (there is no plain-text fallback).
- **Configuration files can never grant approval**: imported target sites arrive unapproved.
- **Input handling**: all user input validated by Pydantic schemas; file imports limited by type and size;
  `LIKE` wildcards escaped; sort columns whitelisted; screenshot file names built from sanitised parts;
  deletions confined to their own folders.
- **Exports** neutralise cells that start with `=`, `+`, `-` or `@` (CSV/formula injection).
- **No dynamic execution**: no `eval`, no user-supplied code, no shell commands from the GUI.
- **Browser identity**: truthful. Profiles offer the browser's own agent or that agent plus a test label;
  there is no impersonation.

## 10. Limitations (state these honestly)

- Only Chromium. One run at a time.
- With a proxy profile the network fence cannot be applied (one browser proxy setting); redirects are
  detected and stop the session, but the proxy may already have received the request.
- The live view is a screenshot every second or so, not video; the controlled browser cannot be embedded
  in the window because Playwright cannot drive `QWebEngineView`.
- The search-engine blocklist is a list of well-known engines; the allowlist is the primary control.
- Charts are simple (no zoom); there is no multi-user or remote mode; the interface is English only.
- Visual checks were made on offscreen renders and a few real launches; high-DPI scaling on other
  machines was not tested.

## 11. Future development

- A guard proxy inside the application that also fences proxy mode (and gives one enforcement point).
- Additional browsers; parallel sessions (the run service is already isolated for this).
- Richer interactions (form filling on the sample site), scheduled runs, comparison of two runs.
- Localisation (Persian/English), packaging as a signed installer (PyInstaller), crash reporting.
- Statistical analysis in reports (distributions, confidence intervals) and chart export.

## 12. Suggested report outline

1. Introduction (problem, motivation, ethics and scope)  2. Requirements (FR/NFR above)
3. Related work / tools (Selenium/Playwright-based journey recorders, SEO rank trackers, load-testing
   tools; why a search *API* rather than scraping a results page)
4. System design (architecture, database, state machine, threading)  5. Implementation highlights
(safety, automation core, live view, reports)  6. Testing and results  7. Security and ethics
8. Limitations and future work  9. Conclusion  Appendices: user guide, test list, class overview.

**Screenshots to include** (the app can produce all of them with the demo data): dashboard (light and
dark), scenario editor with a validation error, runner, live view during dwell, session details timeline,
reports page and the Excel workbook, settings (backup), proxy dialog.

## 13. Five-minute demonstration script

1. Start the application (shortcut). Show the Dashboard and the About page's safety scope.
2. Press **Run demo**: the Live Session View shows the browser, step, countdown, timeline.
3. Pause, resume; show that the countdown holds still while paused.
4. Open Session History → the finished session → Timeline, Settings snapshot, Screenshots.
5. Target Sites: try to add `https://www.google.com`; show the refusal.
6. Reports: generate, export Excel, open the four sheets.
7. Settings: back up, show restore confirmation.
8. Mention the test suite and the defects it found.

## 14. Likely examiner questions

- *Can it be used to inflate traffic of a real site?* No: it refuses everything except the local
  search API and approved sites you control; public search engines cannot be opened by the browser; and
  there is no CAPTCHA,
  fingerprint or IP-rotation code.
- *How do you know the redirect protection works?* An end-to-end test redirects from an approved page to
  an unapproved host; this exposed that Playwright skips redirect hops, which is why the network fence exists.
- *Why not embed the browser?* Playwright and `QWebEngineView` are separate engines; the preview approach
  works for headless runs and keeps the GUI independent of the browser.
- *What if the application crashes mid-run?* Sessions left `RUNNING` are closed as failed at the next start.
- *How is a result reproducible?* The session stores the plan, seed, dwell and schedule.
