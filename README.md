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

## How EzSolver obtains a token — code walkthrough

EzSolver ships two entry points that share the same core logic in
`EzSolver/solver.py`:

- `solver.py` — standalone CLI, opens a fresh Chrome per run.
- `service.py` — keeps one Chrome instance alive across requests via a
  tiny built-in HTTP server (`POST /solve`), to cut cold-start cost.

Our `run_test.py` calls `solver.solve()` directly (no HTTP hop needed for
a single test). The sequence inside `solve_on_page()`:

1. **Navigate to the real target URL** — `await page.get(siteurl)`. Unlike
   the `Turnstile-Solver` branch, EzSolver does **not** intercept or
   replace the page response. Our actual HTML, CSS, and JS all load and
   run exactly as a normal visitor's browser would run them.
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

## What a result actually tells us, given this mechanism

- **Because step 1 loads our real page unmodified**, a successful token
  here means: *a scripted browser, with no human present, running our
  actual page's real JS, was able to get Turnstile to approve it.* This is
  as close as an automated test gets to "what would happen if a bot
  pointed a browser at our real lookup page right now."
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
