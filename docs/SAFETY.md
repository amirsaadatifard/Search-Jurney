# Safety design

The platform automates a browser. To keep that capability inside controlled test environments,
every URL passes through one component, `DomainSafetyService`
(`src/services/safety_service.py`), and the rules are enforced in code, not left to convention.

## Scope

| Allowed | Not implemented, by design |
| --- | --- |
| Reading search data through the **official search API** (`src/search_providers/`), or one read of Google's results page in the browser when the user chose that channel | Any browsing, clicking or scrolling inside a search engine beyond that single read |
| Target sites the user registered **and** approved, plus the bundled sample website | Ranking, CTR, impression or analytics manipulation |
| `http` / `https` only | CAPTCHA solving, anti-bot or rate-limit evasion |
| Proxy profiles as a network-testing setting | Fingerprint spoofing, IP rotation, proxy scraping, ad clicks |

A journey needs to know where a site ranks, and the user chooses the channel per scenario. Both are
allowed to reach Google, and only for that one question:

* **Google in the browser** (default, no key): Chromium navigates to `https://www.google.com/search?q=…`,
  reads the result links and the tab is left on a blank page. This is *not* the API route and Google's
  terms of service forbid it, so the exception around it is kept as small as possible — see below.
* **Google API**: the [Custom Search JSON API](https://developers.google.com/custom-search/v1/overview),
  Google's own documented, key-authenticated endpoint, reached through
  `src/search_providers/google_cse.py`. No browser is involved at all.

## What the browser channel may and may not do

The whole permission is one object (`SearchScope`: the entry URL plus the origins its page loads) that
three places consult, and nothing else:

| Step | Check |
| --- | --- |
| `SearchPageActions.read` | `check_search_url`: the exact origin and path of the entry, a query string, nothing else |
| the network fence at launch | `search_origins()`: those origins are added to the bypass list, so other hosts still fail before connecting |
| the request guard | the results page's own assets, and only while `BrowserSession.search_scope_open` is set by the read itself |

Outside that window nothing gains access: `check_url` still refuses every public search engine, a result
link is never opened (`normalize_result_link` unwraps it only to record the *destination* as the target
URL, which is then checked against the allowlist like any other address), the page is never scrolled,
re-queried, retried or photographed, and a captcha is reported as an error rather than worked around. One
read per iteration is spaced by `pace_query`, which is courtesy and not evasion. A target site may be
registered with `approved_for_automation` on `https://www.google.com` and the visit would still be refused.

Rules that keep the rest of the journey ordinary:

* the ranking is read, then the tab is blanked; the visit goes to the approved address;
* every scope check during the dwell (`_verify_scope`) uses `check_url`, so a session can never idle on a
  search engine;
* a browser-channel journey that finds nothing stores no screenshot of Google's page.

## Auditing the API channel

* on the API channel every query is billed against the user's own quota, so there is nothing to hide
  behind: no retry loop, no proxy rotation, no fallback source;
* results are **never invented**. A provider error fails the session with the API's own message; a
  missing key or engine id stops a run before it starts;
* each query is stored as an observation (`search_observations`, `observed_results`), so a session can
  be audited against the exact results the API returned at that moment;
* the position is labelled with its source (`google-browser` or the API's own name), because neither
  ranking is the personalised page a particular person happens to see.

`DomainSafetyService` has no exempt origins in its *allowlist*: an entry there is either an approved
target site or nothing. Its one narrow exception is the search scope above, held as data and readable only
by the three call sites listed, so nothing in the application can widen it behind the user's back. (The
allowlist used to accept an `internal_origins` argument for the bundled search simulator; that parameter
is gone.)

## Rules applied to every URL

`check_url(url)` either returns a normalised URL or raises `SafetyViolationError` carrying a
machine-readable `reason`.

1. **Well-formed** – parsed with `urllib.parse`; whitespace, control characters, backslashes,
   over-long URLs and invalid ports are rejected (`malformed_url`).
2. **Protocol** – only `http` and `https`. `file://`, `javascript:`, `data:`, `ftp://`,
   `chrome://`, `about:` are refused (`forbidden_scheme`).
3. **No credentials** – `user:password@host` URLs are refused (`credentials_in_url`).
4. **Public search engines** – refused unconditionally, *before* the allowlist is consulted, so an
   allowlist entry can never unlock one (`public_search_engine`). Brand labels are matched under
   any top-level domain (`google.com`, `google.co.uk`, …); `google.com.evil.test` is correctly
   treated as belonging to `evil.test`. `check_search_url` is the separate, purpose-specific entry
   point the browser channel uses (`search_scope`); it is not reachable from `check_url`.
5. **Allowlist** – the origin must match an enabled **and** approved target site (`not_allowlisted`).
   The application keeps no server of its own in the allowlist any more; the bundled sample website is a
   normal, approved target site and can be unapproved like any other.

### Allowlist matching

- Host comparison is **exact**. Approving `staging.shop.test` does not approve
  `www.staging.shop.test`, `shop.test` or `staging.shop.test.evil.test`.
- A site registered with an explicit port (e.g. `127.0.0.1:8766`) is allowed on that port only.
  A site on standard ports is allowed on 80 and 443, so an http→https upgrade is not a violation.
- The allowlist is read from the database on **every** check (no cache). Revoking approval or
  disabling a site takes effect immediately, including for a session that is already running.

### Redirects

`check_redirect(source, destination)` applies the same rules to navigations that happen during a
session and reports `redirect_blocked`, so the session timeline shows that automation was stopped
from *leaving* the approved scope.

### Browser-level enforcement

`DomainSafetyService` answers "may this URL be opened?". Two mechanisms apply the answer inside the
browser during a run:

1. **Request guard** - a Playwright route handler checks every request before it is sent and aborts
   violations (recorded as `SAFETY_BLOCKED` events). A fatal violation (a navigation) fails the session.
2. **Network fence** - found by testing: Playwright does **not** call route handlers for the hops of a
   redirect, so an approved site could redirect the browser anywhere and the request would be sent
   before any check ran. The fence closes this below the application. Chromium is launched with a
   proxy that does not exist and a bypass list naming only the origin of the run (the journey's approved
   target site), with the implicit "never proxy localhost" rule removed. Anything else - a redirect target,
   another port on this computer, a raw IP address - fails with `ERR_PROXY_CONNECTION_FAILED` before any
   connection is attempted. The failing hop is still detected through the page's `request` event and
   reported as a safety violation.

The end-to-end tests verify this against real Chromium, including a live service on another local port
that must not be reachable.

The runner also re-checks the current page against the live allowlist at every checkpoint (every
state change and every 0.25 s of dwell), so revoking a site's approval ends a running session at once.

## Registering a target site

`validate_target_candidate(base_url, environment)` guards the Target Sites form:

- rules 1–4 above apply;
- a site declared `LOCAL` must be `localhost`, `*.localhost`, a loopback address, or a name that
  resolves **only** to loopback addresses.

A new site is created *not approved*. Approval is a separate, explicit action with a confirmation
dialog stating that the user owns the site or is authorised to test it. A scenario can only be
saved or enabled while its target site is both enabled and approved.

## Proxy mode

A proxy profile routes the test browser through a proxy the user controls (HTTP, HTTPS or SOCKS5, with
optional credentials). It exists for controlled network testing only: there is no proxy list, scraping,
rotation or automatic selection.

The network fence needs Chromium's single proxy setting, so it cannot be combined with a user proxy.
A PAC-file approach was tried and **rejected after testing**: Chromium ignored the `data:` PAC script and
sent traffic directly, which would have given a false sense of safety. In proxy mode the protection is
therefore weaker and the application says so:

- ordinary requests are still checked by the request guard before they are sent;
- redirect hops cannot be prevented, but they are detected, recorded as `SAFETY_BLOCKED` events, and fail
  the session at once - the proxy may already have received that one request;
- the timeline's `BROWSER_STARTED` event states "proxy mode", the session's browser info records
  `network_fence: false`, and the scenario and proxy forms carry a warning.

A guard proxy inside the application (which would also fence proxy mode) is listed as future work.

## Secrets

Proxy passwords live only in the operating system's credential store (`keyring`). They never appear in
the database, logs, session snapshots, exports, error messages or the GUI (the password field is never
pre-filled). If the system has no secure store, saving a password is refused; there is no plain-text
fallback. Tests assert that a password is absent from every one of those places.

## Imports and exports

- A configuration file **can never grant approval**: imported target sites arrive not approved, and a
  scenario that points at an unapproved site is stored disabled.
- Target-site rules still apply to imported sites (no public search engines, `LOCAL` must be loopback).
- A file with any invalid entry is rejected as a whole before anything is written (5 MB limit, safe JSON
  parsing, only known keys).
- Exports neutralise spreadsheet formulas (a cell starting with `=`, `+`, `-` or `@` is prefixed with `'`).
- Restoring a backup validates the file first and keeps a safety copy; deleting backups or screenshots is
  confined to their own folders.

## Other safeguards

- Browser profiles offer the browser's own identity or that identity plus an explicit
  test-automation label. There is no option to impersonate another browser or device.
- Imported keyword files are restricted to `.txt` / `.csv`, 5 MB, UTF-8; nothing imported is executed.
- Sort columns are mapped through a whitelist; user text never reaches `ORDER BY`. `LIKE`
  wildcards in search text are escaped.
- Screenshot file names are built from sanitised parts only.

## Verification

`tests/unit/test_safety_service.py` covers URL normalisation, each rejection reason, search-engine
detection (including look-alike domains), port and sub-domain matching, live allowlist changes,
redirect blocking and host classification. `tests/unit/test_management_services.py` and
`tests/unit/test_management_ui.py` verify the same rules through the services and the forms.

## Known limitations

- The search-engine list is a built-in set of well-known engines, not an exhaustive registry. The
  allowlist is the primary control; the list is defence in depth.
- Host names are resolved when a `LOCAL` site is registered, not on every request. A DNS record
  that changes afterwards is not re-checked (the exact-host allowlist still applies).
- With a proxy profile the network fence cannot be applied (see *Proxy mode*): redirects are detected
  and stop the session but are not prevented at the network level.
- The fence confines the browser to the approved origins; it does not restrict what an approved
  site itself does server-side.
