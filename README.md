# turnstile-bypass-test

Defensive testing: can off-the-shelf browser-automation Turnstile solvers
get a token for our own eInvoice lookup page, and does our backend still
reject abuse after the token is accepted?

**Only point any of this at `tracuu.einvoice365.com` (or other
infrastructure we own) — never at third-party sites.**

## Target findings (shared across all branches)

- **Page:** FPT eInvoice lookup — `https://tracuu.einvoice365.com/`
- **Widget:** Cloudflare Turnstile, **managed/invisible mode** — no
  checkbox/puzzle, resolves in the background in ~1-2s. The "Tra cứu"
  button stays disabled until the token is ready.
- **Site key:** `0x4AAAAAACA6lZiDGKBtjbxg`
- **Token field:** hidden input `name="cf-turnstile-response"`
- **Candidate lookup endpoints** (from `portal.js`, shape unconfirmed):
  `/ptop/psearch`, `/ptopnew/psearch`, `/ptopnew/pcussearch`,
  `/api/inv/minutes/param`
- No other bot defense observed (no reCAPTCHA/hCaptcha/image captcha, no
  full-page Cloudflare interstitial). Turnstile is the only visible layer.

**Implication:** an invisible/managed widget auto-resolves with no
interaction, so any solver that loads the page and waits for the callback
gets a token — it doesn't need to simulate a click. Whatever actually
stops abuse has to live in the backend (replay checks, rate limiting,
re-verification via Cloudflare's `siteverify`), not in the widget's
interaction mode. That's what each branch's harness measures.

## Branch layout

One branch per candidate solver, so each integration and its
solver-specific quirks stay isolated:

| Branch | Solver repo | Approach | Status |
|---|---|---|---|
| [`ezsolver`](../../tree/ezsolver) | [ismoiloffS/EzSolver](https://github.com/ismoiloffS/EzSolver) | Real Chrome + `nodriver`, reads Turnstile JS callback | done |
| `Turnstile-Solver` | [Theyka/Turnstile-Solver](https://github.com/Theyka/Turnstile-Solver) | `patchright`, multi-browser, local API w/ `action`/`cdata` support | in progress |
| `odell0111-solver` | [odell0111/turnstile_solver](https://github.com/odell0111/turnstile_solver) | `patchright` + Chrome, HTTP server, secret-header auth | planned |
| `turnstile-slip` | [Iruko233/Turnstile-Slip](https://github.com/Iruko233/Turnstile-Slip) | Node.js, returns `cf_clearance` cookie directly | planned |
| `lucrehulk-solver` | [Lucrehulk/cloudflare-turnstile-solver-2026](https://github.com/Lucrehulk/cloudflare-turnstile-solver-2026) | Rust token server + browser extension, Windows-only GUI | planned — requires code audit first (debugger-based response interception) |

Each branch keeps the same shape: `<solver>/` (vendored upstream repo),
`run_test.py` (harness: get token → optionally submit to our backend →
report accepted/rejected), `.env.example`, `README.md`.

## Running a branch's harness

```bash
git checkout <branch-name>
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values, see that branch's README
python run_test.py
```

Requires real Chrome (or the solver's required browser) and network
access to the target site — run locally, not from a sandboxed cloud
session with no browser/network access to the target.

## Reading a result

| Token acquisition | Backend acceptance | Meaning |
|---|---|---|
| Failed | — | This particular solver couldn't get a token against our widget config. Not proof against all solvers. |
| Succeeded | Rejected | Turnstile alone didn't stop it, but our backend has a real second layer. This is the healthy outcome. |
| Succeeded | Accepted | Our backend treats "valid token" as sufficient — add server-side `siteverify` re-check with one-time-use enforcement, per-IP/session rate limiting, and anomaly detection. |
