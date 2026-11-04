*English | [Tiếng Việt](README.vi.md)*

# turnstile-bypass-test

Defensive testing: do open-source Turnstile solvers get a valid token
for our own invoice-lookup page, and — more importantly — **does our
backend still enforce anything once that token is accepted?**

**Only run against `tracuu.einvoice365.com` (or other infrastructure we
own) — never a third-party site.**

## Target context

- **Page:** FPT eInvoice — `https://tracuu.einvoice365.com/`
- **Widget:** Cloudflare Turnstile, **managed/invisible mode** — no
  checkbox/puzzle, resolves in the background (~1-2s). The "Tra cứu"
  button stays disabled until the token is ready.
- **Site key:** `0x4AAAAAACA6lZiDGKBtjbxg`
- **Token field:** hidden input `name="cf-turnstile-response"`
- **Candidate lookup endpoints** (from `portal.js`, unconfirmed):
  `/ptop/psearch`, `/ptopnew/psearch`, `/ptopnew/pcussearch`,
  `/api/inv/minutes/param`
- **UI framework:** Webix (buttons are `<div>`s with stable view-ids;
  only the two inputs are real `<input>`s).
- No other bot defense seen (no reCAPTCHA/hCaptcha/image captcha, no
  full-page Cloudflare interstitial).

**Implication:** an invisible widget auto-resolves with no interaction,
so any solver that loads the page and waits gets a token. The real
defense has to live in the backend (replay protection, rate limiting,
re-verifying with Cloudflare) — that's what this project measures.

## Integrated solver: `ezsolver`

The [`ezsolver` branch](../../tree/ezsolver) integrates
[`ismoiloffS/EzSolver`](https://github.com/ismoiloffS/EzSolver) (MIT,
built on `nodriver` + real Chrome). It's the only one of the surveyed
tools able to drive the **real page** — load it, fill the real form,
click the real button, reach the result. Two scripts there:

- `run_test.py` — get a token, then raw HTTP POST to the backend
  endpoint ("does the backend accept a token with no browser session?").
- `run_ui_test.py` — fill the real form + click "Tra cứu" like a user
  ("does the site detect a bot operating its UI?").

## Other solvers surveyed (not integrated)

| Repo | Why not |
|---|---|
| Theyka/Turnstile-Solver | Serves a synthetic widget-only page — never loads the real page, so it only gets a token, can't drive the UI. |
| odell0111/turnstile_solver | Same synthetic-page technique (confirmed by reading its code) — token only. Most polished codebase of the group, but no real-UI capability. |
| Iruko233/Turnstile-Slip | Node.js, returns a `cf_clearance` cookie — different language, token/cookie focused. |
| Lucrehulk/cloudflare-turnstile-solver-2026 | Rust + browser extension, Windows GUI — not a clean browser driver. |

Takeaway: only tools built on a general browser-automation library
(`nodriver`, Playwright) that actually load the real page can test the
UI. EzSolver fits; the rest only harvest tokens.

## Reading a result

| Token | Backend | Meaning |
|---|---|---|
| Failed | — | This solver couldn't get a token against the current widget config. |
| Got one | Rejected | Turnstile alone didn't stop it, but the backend has a real second layer. Healthy. |
| Got one | Accepted | Backend treats "valid token" as enough. Add server-side `siteverify` re-check (one-time-use), rate limiting per IP/session, and anomaly detection. |

See the [`ezsolver` branch](../../tree/ezsolver) for setup, the full
flow diagram, and how to read each outcome.
