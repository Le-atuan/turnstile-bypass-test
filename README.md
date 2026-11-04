*English | [Tiếng Việt](README.vi.md)*

# turnstile-bypass-test

Defensive-testing project overview: do open-source Turnstile-solving
tools obtain a valid token for our own invoice-lookup page, and more
importantly — **does our backend still enforce anything once that token
has already been accepted.**

**Only run this against `tracuu.einvoice365.com` (or other infrastructure
we own) — never against a third-party site.**

## Target context

- **Page:** FPT eInvoice — `https://tracuu.einvoice365.com/`
- **Widget:** Cloudflare Turnstile, **managed/invisible mode** — no
  checkbox/puzzle shown, resolves in the background (~1-2s). The "Tra
  cứu" (lookup) button stays disabled until the token is ready.
- **Site key:** `0x4AAAAAACA6lZiDGKBtjbxg`
- **Token field:** hidden input `name="cf-turnstile-response"`
- **Candidate lookup endpoints** (read from `portal.js`, exact request
  shape not yet confirmed): `/ptop/psearch`, `/ptopnew/psearch`,
  `/ptopnew/pcussearch`, `/api/inv/minutes/param`
- No other bot defense observed (no reCAPTCHA/hCaptcha/image captcha, no
  full-page Cloudflare interstitial). Turnstile is the only visible
  layer.

**Implication:** since the widget is invisible and auto-resolves with no
interaction, any solver that loads the page and waits for the callback
gets a token — no click simulation required. The real line of defense
has to be the backend (replay protection, rate limiting, re-verifying
with Cloudflare), not the widget's interaction mode. That's exactly what
each branch below measures.

## Repos integrated & comparison

This started from a broader survey of "Cloudflare Turnstile bypass"
tools on GitHub. Two were chosen for an actual integration, because they
represent two different approaches:

| | [`ezsolver`](../../tree/ezsolver) branch | [`Turnstile-Solver`](../../tree/Turnstile-Solver) branch |
|---|---|---|
| **Upstream repo** | [ismoiloffS/EzSolver](https://github.com/ismoiloffS/EzSolver) | [Theyka/Turnstile-Solver](https://github.com/Theyka/Turnstile-Solver) |
| **Language / engine** | Python + `nodriver` (raw CDP, no Playwright) | Python + `patchright` (Playwright patched to reduce fingerprinting) |
| **How it gets a token** | Loads our **real page**, waits for the widget's own JS callback, clicks the iframe if needed | Navigates to the **real URL** (so the sitekey's domain-binding check still passes) but **intercepts the response and serves its own minimal HTML** — never loads our real page |
| **Fidelity to the real page** | High — every script, DOM element, and side logic on the real page actually runs | Lower — only proves the sitekey itself can be solved in isolation; says nothing about our real page's JS |
| **License** | MIT | No clear LICENSE file in the repo — be cautious reusing it beyond this test |
| **System requirements** | Real Chrome, Xvfb on headless Linux | Browser managed by `patchright` (chromium/chrome/msedge/camoufox) — no separately-installed Chrome needed |
| **Speed / resources** | Ships a `service.py` that keeps one Chrome warm (fewer cold starts) — author's README claims ~2-2.2s per solve once warm | Each call opens a fresh Chrome via `sync_solver` — slower, but simpler, no background process to manage |
| **Self-hosting risk** | `service.py` binds `0.0.0.0` with **no authentication** — the README itself warns to keep it on a trusted network only | No open-port service is used here (we call `sync_solver` directly as a Python function); the repo's own API server (`main.py`/`api_solver.py`) also ships with no authentication if used instead — not exercised in this test |
| **What it actually measures** | **End-to-end** defense of the real page — the closest match to what a real user or bot would encounter | Defense of the **sitekey/domain-binding alone**, isolated from our frontend's own logic |
| **Code-review flags** | Nothing beyond the usual "use at your own risk" disclaimer | Similarly nothing alarming, but the missing license is a reason for extra caution if reused elsewhere |

### How to read the two results together

- **Run `ezsolver` first** — it's the closest match to a real user/bot
  hitting the actual page.
- **Run `Turnstile-Solver` second** to see whether the sitekey alone is
  easy to solve in isolation — if both end up "accepted" by the backend,
  that's a stronger signal the backend genuinely has no second layer.
- If one succeeds and the other fails, that's a useful signal on its
  own: it tells you whether the defense lives in the real frontend's JS
  or in the widget/domain-binding itself.

Other repos surveyed but not integrated: `odell0111/turnstile_solver`,
`Iruko233/Turnstile-Slip`, `Lucrehulk/cloudflare-turnstile-solver-2026`.
A branch can be added for any of these later if needed.

## Layout of each solver branch

```
<branch>/
├── <Solver>/          # upstream repo, vendored as-is
├── run_test.py        # harness: confirm authorization → get token → submit to backend → report accepted/rejected
├── .env.example        # sitekey/URL/endpoint pre-filled with the values recon already confirmed
├── README.md / README.vi.md   # that solver's technical detail (EN/VI)
└── SETUP.md / SETUP.vi.md     # step-by-step install & run guide, commands annotated (EN/VI)
```

Switch to the branch you want and read its `SETUP.md` to actually run it
— `main` is overview only, nothing here runs directly.

## Reading a result (applies to both branches)

| Token acquisition | Backend acceptance | Meaning |
|---|---|---|
| Failed | — | This solver couldn't get a token against the current widget config. Not proof against every solver. |
| Succeeded | Rejected | Turnstile alone didn't stop the solver, but the backend has a real second layer. This is the healthy outcome. |
| Succeeded | Accepted | The backend treats "has a valid token" as sufficient. Add: a server-side `siteverify` re-check with one-time-use enforcement, per-IP/session rate limiting, and anomaly detection on request patterns. |
