*English | [Tiếng Việt](README.vi.md)*

# ezsolver branch — technical detail

Integrates [`ismoiloffS/EzSolver`](https://github.com/ismoiloffS/EzSolver)
(MIT) against our own `tracuu.einvoice365.com` lookup page. See the
[`main` branch](../../tree/main) README for the project overview and the
comparison against the `Turnstile-Solver` branch. This file goes deeper
into how EzSolver itself works internally and what that means for reading
the test result correctly.

For install/run commands, see [`SETUP.md`](SETUP.md) /
[`SETUP.vi.md`](SETUP.vi.md).

## Two separate tests in this branch

- **`run_test.py`** — token acquisition via EzSolver's own
  `solve_on_page()`, then (optionally) a raw HTTP POST of that token to a
  guessed backend endpoint. See the code walkthrough and the important
  DOM-wiping caveat below.
- **`run_ui_test.py`** — a different, more realistic test: loads the real
  page **unmodified**, waits for its own Turnstile widget to resolve in
  the background, then types a real tax code + lookup code into the real
  "MST bên bán" / "Mã tra cứu hóa đơn" form fields with human-like
  per-character delay, and clicks the real "Tra cứu" button — to answer
  "does the site detect a bot operating its actual UI," not just "can a
  token be obtained." It deliberately avoids `solve_on_page()` — see why
  in the next section.

## How EzSolver obtains a token — code walkthrough

EzSolver ships two entry points that share the same core logic in
`EzSolver/solver.py`:

- `solver.py` — standalone CLI, opens a fresh Chrome per run.
- `service.py` — keeps one Chrome instance alive across requests via a
  tiny built-in HTTP server (`POST /solve`), to cut cold-start cost.

Our `run_test.py` calls `solver.solve()` directly (no HTTP hop needed for
a single test). The sequence inside `solve_on_page()`:

1. **Navigate to the real target URL** — `await page.get(siteurl)`. Unlike
   the `Turnstile-Solver` branch, EzSolver does **not** intercept the
   network response — the real HTML initially loads. **But see the
   correction below: the very next step wipes it.**
2. **Hook `Element.prototype.attachShadow`** before Turnstile's own script
   runs, forcing every shadow root Cloudflare creates to be `mode: 'open'`
   instead of `'closed'`. Turnstile renders its iframe inside a closed
   shadow DOM specifically so page scripts can't easily reach into it;
   this hook defeats that isolation so the automation script can read the
   iframe's position and the widget's internal state.
3. **Inject and explicitly render the widget** into a small fixed-position
   div (`#_ts_box`), using `turnstile.render()` with our real `sitekey`,
   rather than relying on the page's own auto-render. The `callback` and
   `error-callback` options store the result on `window._tsToken` /
   `window._tsError` so the Python side can poll for it.
4. **Poll for state** every 0.5s via `page.evaluate()`, checking (in
   order) the error callback, the token callback, and the hidden
   `cf-turnstile-response` input's value as a fallback.
5. **If no token or error appears after ~5s**, locate the Cloudflare
   iframe's on-screen rectangle (through the opened shadow root, falling
   back to a plain `<iframe src*="challenges.cloudflare.com">` search) and
   **simulate a human-like click**: move the mouse in two steps with
   randomized jitter (`random.uniform(-3, 3)` on each coordinate,
   staggered `sleep`s between move/click), then click. This only matters
   for a *visible* checkbox widget — on this target, the widget is
   managed/invisible and should resolve from step 4 alone, without ever
   reaching the click logic.
6. **Error codes are mapped to human-readable reasons** (`110100` invalid
   sitekey, `110200` domain not authorized, `110600` challenge timeout,
   etc.) — useful for distinguishing "Turnstile actively rejected this
   browser" from "it just timed out."

## Correction: `solve_on_page()` wipes the real page's DOM

Step 2's hook code block, read closely, starts with
`window.stop(); document.body.innerHTML = '';` — **before** installing
the shadow-DOM hook and injecting its own widget div. This means EzSolver
does **not** preserve the real page's form, scripts, or any other DOM
content once solving begins — it erases the body immediately after
navigation, the same way the `Turnstile-Solver` branch's fake page does,
just one step later in the sequence (after a real navigation instead of
via route interception).

**Practical effect:** `run_test.py` (which uses `solve_on_page()`) is
still a meaningful test — the real page's sitekey, domain, and any
scripts that already executed before the wipe are exercised — but it is
**not** a test of the real form or the real submit flow, because that
form no longer exists in the DOM by the time a token comes back. For a
test of the real form and button, use `run_ui_test.py` instead (see
above), which never wipes anything.

## What a result actually tells us, given this mechanism

- **A successful token via `run_test.py` means**: *a scripted browser,
  with no human present, got Turnstile to approve it on our real domain* —
  but says nothing about whether our real form/button flow would also go
  through undetected. That's what `run_ui_test.py` is for.
- **The shadow-DOM hook (step 2) is a meaningful technique to be aware
  of**, independent of this specific tool: `closed` shadow roots are not a
  security boundary against a browser the automation itself controls,
  since CDP/Playwright-level access can always override `attachShadow`
  before any other script runs. Don't rely on shadow DOM encapsulation as
  a bot-defense mechanism — it only raises the bar for people copying someone else's JS tricks without controlling the browser itself, not for anyone driving the browser directly, which is exactly the situation any solver tool represents.
- **The explicit `turnstile.render()` call with our real sitekey means
  domain-binding is exercised exactly as it would be for a real visitor**
  — this isn't a synthetic/foreign-page test like the `Turnstile-Solver`
  branch's technique.
- A **failure** here, given managed/invisible mode doesn't require a
  click, would suggest Cloudflare's risk scoring itself flagged the
  automated browser (headless fingerprint, missing real user signals,
  etc.) rather than any click-simulation timing issue — worth noting
  before concluding our own backend/widget config is responsible for a
  block.

## Requirements specific to this solver

- **Real Google Chrome**, not just a headless-automation Chromium build —
  `nodriver` drives it over the DevTools Protocol directly (no Playwright
  layer), and the upstream README recommends the genuine Chrome binary.
- Linux without a display: `Xvfb` (the code auto-starts it on `:99` if
  `DISPLAY` isn't already set).
- `CHROME_PATH` env var if Chrome isn't in one of the auto-detected
  locations (see `EzSolver/solver.py:_find_chrome()`).

## Known rough edges in the upstream code (review before relying on it)

- `EzSolver/service.py`'s HTTP server binds `0.0.0.0` with **no
  authentication or rate limiting** — the upstream README itself warns
  about this. Our `run_test.py` doesn't use `service.py` at all (it calls
  `solver.solve()` in-process), so this doesn't apply to our test, but
  it's relevant if this repo is ever reused to run a persistent service.
- `clientsend.py` hardcodes `SERVICE_URL = "http://127.0.0.1:8191/solve"`
  — irrelevant to us since we bypass it, noted only for completeness.

## `run_ui_test.py` — real-form UI test

Written for this project, not part of upstream EzSolver. It only reuses
EzSolver's browser-launch helpers (`start_browser`, `_find_chrome`,
`_start_xvfb_if_needed`) and never calls `solve_on_page()`, so the real
page's DOM is never wiped:

1. Load the real page unmodified.
2. Poll the real hidden `cf-turnstile-response` input (the page's own
   script manages it — managed/invisible mode means no click is needed)
   until it has a value.
3. Type a real tax code and lookup code into the real
   `input[placeholder="MST bên bán"]` / `input[placeholder="Mã tra cứu hóa đơn"]`
   fields, one character at a time with a randomized delay
   (`random.uniform(0.05, 0.18)`s) to avoid an instantaneous/robotic fill.
4. Click the real button found by its visible text, `"Tra cứu"`.
5. Capture and print the page's body text after submission.

Configured via `.env`: `TEST_CASES`, a JSON list of
`{"tax_code": "...", "lookup_code": "..."}` objects — each a real,
already-issued invoice the site owner explicitly authorized for this
test (see `.env.example`'s comment). One run per entry, in order, each
against a genuinely different invoice — this is distinct from repeating
the same lookup, so it doesn't by itself test rate-limiting on identical
requests, but it does test whether the site behaves consistently across
multiple distinct bot-driven lookups in one session.

**Not yet executed end-to-end in this session** — written against
nodriver's documented API, but this sandboxed cloud environment has no
real Chrome and no network path to the target, so the exact method names
(`page.select`, `page.find`, `element.send_keys`) should be verified
against your installed nodriver version the first time you run it
locally.
