*English | [Tiếng Việt](README.vi.md)*

# ezsolver branch

Integrates [`ismoiloffS/EzSolver`](https://github.com/ismoiloffS/EzSolver)
(MIT) to test our own `tracuu.einvoice365.com` lookup page. See the
[`main` branch](../../tree/main) for the project overview. Install/run
steps: [`SETUP.md`](SETUP.md) / [`SETUP.vi.md`](SETUP.vi.md).

## Backend

Only `undetected-chromedriver` (`SOLVER_BACKEND=uc`, `EzSolver/solver_uc.py`)
actually gets past this site's Turnstile — it's the default and only
backend used here. `nodriver` and `playwright-stealth` were tried and
blocked.

## Tests

| Script | What it does | Answers |
|---|---|---|
| `run_test.py` | Gets a token via EzSolver (`uc`), then sends a raw HTTP POST straight to `POST /api/search` (endpoint/payload shape reverse-engineered with `capture_request.py`). Runs in the background, no visible browser needed for the HTTP step. Supports `MAX_WORKERS>1` to fire several test cases concurrently, each with its own browser. | "Does the backend accept a token with no real browser session behind it, and does it rate-limit concurrent requests?" |
| `capture_request.py` | One-off: drives the real UI with `uc`, hooks `fetch`/`XMLHttpRequest`, and dumps the exact request the page sends to `captured_request.json`. Used once to discover `run_test.py`'s endpoint/payload. | — |

`run_test.py` loops over the pairs in `test_cases.json` (or `--cases <path>`).

## Commands

```bash
cp .env.example .env            # one-time setup: set TARGET_SITEURL/SITEKEY
pip install -r requirements.txt
```

### Primary test — background raw-HTTP bypass + XML download (preferred, no UI needed)

```bash
python run_test.py
```

Solves Turnstile, replays the token straight to `POST /api/search`, and
saves the real invoice XML to `logs/xmls/` (or `DOWNLOAD_DIR` if set in
`.env`) for every test case — this is the one to run by default. Runs
unattended, with no authorization prompt. Test cases come from
`test_cases.json` if that file exists, else from `TEST_CASES` in `.env`
— either way a JSON list of result_json-shaped
`{"info": {"tax_code": "...", "lookup_code": "..."}}` objects; pairs
already in `logs/downloaded.json` are skipped. Use a different file (e.g.
a batch of 1000) with:

```bash
python run_test.py --cases /path/to/your_file.json
```

(replace `/path/to/your_file.json` with the real path to your own file)

### Same, but N test cases at once (to probe for missing rate-limiting)

```bash
MAX_WORKERS=4 python run_test.py
```

Runs up to 4 test cases concurrently, each with its own browser, to see
if the backend throttles/flags simultaneous identical requests.

### One-off: capture the exact request the live page sends

```bash
python capture_request.py
```

Already run once to reverse-engineer `run_test.py`'s endpoint/payload;
re-run only if the site's frontend changes.

## How `run_test.py` works

```mermaid
flowchart TD
    A[For each {info: {tax_code, lookup_code}}<br/>entry in --cases file] --> B{Pair already in<br/>logs/downloaded.json?}
    B -->|yes| S[skipped — final, no browser opened]
    B -->|no| C[Open Chrome via uc backend,<br/>solve Turnstile → get token]
    C -->|exception| E1[error]
    C -->|token| D[POST token + tax_code + lookup_code<br/>to VERIFY_ENDPOINT]
    D -->|exception| E1
    D --> F{Response text matches<br/>NOT_FOUND_KEYWORDS?}
    F -->|yes| N[not_found — final]
    F -->|no| G{HTTP 2xx?}
    G -->|no| R[rejected — final]
    G -->|yes| H{response JSON has<br/>non-empty 'xml' field?}
    H -->|no| R
    H -->|yes| I[download_xml: save to DOWNLOAD_DIR,<br/>record pair in downloaded.json]
    I --> ACC[accepted — final]
    E1 --> J{Attempts left<br/>under MAX_ATTEMPTS?}
    J -->|yes| C
    J -->|no| EF[error — final]
```

`get_file(result_json, download_dir)` is a separate, standalone entry point
(drop-in for `GetFolderTraCuuEinvoice365.get_file`) — it is not called by
the flow above. It runs its own solve+verify, then reuses the same
`download_xml` save step.

Jobs for different test cases run concurrently when `MAX_WORKERS>1` (each
its own browser, via `ThreadPoolExecutor`); only `error` retries — `skipped`,
`not_found`, `rejected` and `accepted` are always final.

## Outcomes & retries

Only a **technical error** retries (up to `MAX_ATTEMPTS`). A real answer
from the site is always final.

| Outcome | Meaning | Retried? |
|---|---|---|
| `accepted` | HTTP 2xx and the XML was actually saved | No |
| `rejected` | Backend returned non-2xx | No |
| `not_found` | Response matched `NOT_FOUND_KEYWORDS` (valid "no record") | No |
| `error` | Token acquisition or the HTTP request itself failed | **Yes** |
| `skipped` | XML already downloaded in a previous run | No |

## Running several in parallel

`run_test.py` with `MAX_WORKERS>1` in `.env` runs that many test cases
concurrently, each in its own browser instance — used to check whether
the backend rate-limits/flags concurrent identical requests from one
machine (as of this testing, it does not).

`NOT_FOUND_KEYWORDS` is a **guessed, unconfirmed** list — adjust it once
a real "not found" response is observed.

## Logging

Each run writes `logs/<script>_<timestamp>.log` (full detail +
tracebacks) alongside concise console output. On a step failure it also
saves a screenshot + page HTML to `logs/`. XML files downloaded via
`run_test.py` land in `logs/xmls/` (or `DOWNLOAD_DIR` if set). **`logs/`
is gitignored** — it can contain real invoice data.

## Requirements

Real Google Chrome (not just Chromium — `undetected-chromedriver` drives
it); Xvfb on headless Linux; `CHROME_PATH` if Chrome isn't auto-detected.
