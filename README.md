# Turnstile defensive test harness

Tests whether an off-the-shelf browser-automation solver can obtain a
Cloudflare Turnstile token for **your own site**, and whether your backend
accepts that token. Built to evaluate `tracuu.einvoice365.com` (FPT
eInvoice lookup portal) — only point it at infrastructure you own or are
authorized to test.

## Target site findings (from direct observation, 2026-11-04)

- **Widget:** Cloudflare Turnstile, **managed/invisible mode** — no
  checkbox or puzzle is shown; it resolves in the background in ~1-2s.
  The "Tra cứu" (lookup) button stays disabled until the token is ready.
- **Site key:** `0x4AAAAAACA6lZiDGKBtjbxg` (from `window.TURNSTILE_SITE_KEY`).
- **Token field:** hidden input `name="cf-turnstile-response"`.
- **Candidate lookup endpoints** seen in `portal.js` (exact request shape
  not yet confirmed — verify with DevTools before using):
  `/ptop/psearch`, `/ptopnew/psearch`, `/ptopnew/pcussearch`,
  `/api/inv/minutes/param`.
- **No other bot defense observed:** no reCAPTCHA/hCaptcha/image captcha,
  no full-page Cloudflare interstitial — Turnstile is the only visible
  layer on this page.
- `robots.txt` returns an Internal Server Error (not a valid robots.txt).
- No public terms-of-use or API documentation found on this subdomain;
  only a support hotline/email and a link to the parent company fpt-is.com.

**Implication:** because the widget is invisible/managed and auto-resolves
with no interaction, a solver only needs to load the page and wait for the
callback — it doesn't need to simulate a checkbox click at all. This is
*easier* for a solver than a visible "I'm not a robot" challenge would be.
Whatever stops abuse here has to come from elsewhere: your backend's
replay/rate-limit/anomaly logic, not the widget's interaction mode.

## Why this exists

Turnstile tokens prove "a real browser executed the widget," nothing more.
Any tool that drives a real browser (EzSolver, and similarly-shaped tools
like Theyka/Turnstile-Solver or odell0111/turnstile_solver) can obtain a
valid token for a page it's pointed at. The real question for a site owner
isn't "can some tool get a token" — it almost always can — it's **"does my
backend still have a layer of defense after the token is accepted?"** This
harness measures exactly that, end to end.

## What's included

- `EzSolver/` — upstream solver (MIT license), vendored as a git checkout.
  Drives a real Chrome via `nodriver`, waits for the Turnstile widget's
  callback, and returns the token. Chosen first because its README
  explicitly scopes itself to "pages you own or are authorized to test,"
  it's the simplest of the candidates, and it has no paid API dependency.
- `run_test.py` — the harness:
  1. Prompts you to confirm authorization before touching the target.
  2. Calls EzSolver to obtain a token for your configured sitekey/URL.
  3. Optionally submits that token to your real backend endpoint and
     reports whether it was accepted or rejected.
- `.env.example` — fill in your real sitekey, target URL, and (optionally)
  the backend endpoint that consumes the token.

## Requirements (must run where these are available)

- Python 3.8+
- **Real Google Chrome** installed (not just headless Chromium —
  `nodriver` talks to Chrome's DevTools protocol and the README recommends
  the real Chrome build)
- Linux without a display: `sudo apt install xvfb`
- Outbound network access to your target site and to
  `challenges.cloudflare.com`

This cloud session cannot run the live test itself: this container has no
real Chrome (only a headless-automation Chromium) and its network policy
does not currently allow `tracuu.einvoice365.com` to resolve. Run this
project on your own machine (or a VM you control), e.g. via
`claude remote-control` or just `git clone`-ing this folder and running it
locally where you have Chrome and network access to your own site.

## Setup

```bash
cd turnstile-defense-test
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:
- `TARGET_SITEURL` — the exact page with the widget.
- `TARGET_SITEKEY` — read from the page source: look for
  `data-sitekey="..."` on the `cf-turnstile` div, or the `sitekey` passed
  to `turnstile.render(...)`.
- `VERIFY_ENDPOINT` (optional) — your backend endpoint that receives the
  token. Leave blank to only test token acquisition.
- `VERIFY_EXTRA_FIELDS` (optional) — any other fields your endpoint needs
  to run a real lookup. **Use a known test invoice number, never a real
  customer's data.**

## Run

```bash
python run_test.py
```

You'll be asked to confirm you're authorized to test the target before
anything touches it.

## Reading the result

| Token acquisition | Backend acceptance | Meaning |
|---|---|---|
| Failed | — | Good sign — your widget config (e.g. Managed mode, domain binding) is holding up against this particular tool. Not proof against all tools. |
| Succeeded | Rejected | Turnstile alone didn't stop the solver, but your backend has a real second layer (replay check, siteverify re-validation, extra required state). This is the healthy configuration. |
| Succeeded | Accepted | Your backend treats "has a valid Turnstile token" as sufficient. Add: server-side `siteverify` re-check per submission with one-time-use enforcement, rate limiting per IP/session, and anomaly detection on request patterns (sequential invoice IDs, abnormal request rate, etc.). |

## Trying the other candidates later

To compare against `Theyka/Turnstile-Solver` or `odell0111/turnstile_solver`
instead, clone them alongside `EzSolver/` and swap the import in
`step1_solve_token()` for their respective client call — both expose a
similar HTTP API (`POST` with `sitekey`/`site_key` + `siteurl`/`site_url`,
returning a token). Keep the rest of the harness (the authorization prompt
and the backend-acceptance check) unchanged.
