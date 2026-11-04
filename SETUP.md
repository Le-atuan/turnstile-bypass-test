*English | [Tiếng Việt](SETUP.vi.md)*

# Setup & run — ezsolver branch

Step-by-step. Run this on your own machine or a VM you control — **not**
from a sandboxed cloud session with no real Chrome and no network access
to the target site.

## 0. Prerequisites

- Python 3.8 or newer
- Google Chrome installed (the real browser, not just Chromium) —
  download from https://www.google.com/chrome/ if you don't have it
- Linux **without** a graphical desktop: also install Xvfb (virtual
  display), e.g. `sudo apt install xvfb` on Debian/Ubuntu
- Network access from this machine to `tracuu.einvoice365.com` and to
  `challenges.cloudflare.com`

## 1. Get the code

```bash
# Clone the repo if you don't already have it locally
git clone https://github.com/Le-atuan/turnstile-bypass-test
cd turnstile-bypass-test

# Switch to this branch specifically — each solver lives on its own branch
git checkout ezsolver
```

## 2. Create an isolated Python environment

```bash
# A venv keeps this project's dependencies separate from your system Python
python3 -m venv venv

# Activate it — your shell prompt should now show (venv)
source venv/bin/activate        # Linux/macOS
# venv\Scripts\activate         # Windows (cmd/PowerShell instead)
```

## 3. Install dependencies

```bash
# Installs nodriver (browser automation), requests (HTTP client),
# and python-dotenv (reads the .env file below)
pip install -r requirements.txt
```

## 4. Configure the target

```bash
# Copy the template — .env itself is gitignored so your local edits
# (and any real endpoint details you fill in) never get committed
cp .env.example .env
```

Open `.env` in an editor and check/fill:

- `TARGET_SITEURL` — already set to `https://tracuu.einvoice365.com/`.
- `TARGET_SITEKEY` — already set to the real sitekey found during recon
  (`0x4AAAAAACA6lZiDGKBtjbxg`). Leave as-is unless the widget changes.
- `VERIFY_ENDPOINT` — a **candidate** endpoint (`/ptop/psearch`) guessed
  from reading `portal.js`. **Confirm it's correct** before trusting the
  backend-acceptance result:
  1. Open the real page in Chrome, press F12 (DevTools), go to the
     **Network** tab, and check "Preserve log".
  2. Fill in a real-looking MST + mã tra cứu and click "Tra cứu".
  3. Find the actual request that fired and copy its real URL, method,
     and field names into `.env` (`VERIFY_ENDPOINT`, `VERIFY_METHOD`,
     `VERIFY_EXTRA_FIELDS`).
  4. Use a **known test invoice**, never a real customer's data, for this
     step.
- Leave `VERIFY_ENDPOINT` blank if you only want to test whether a token
  can be obtained at all, without touching the lookup endpoint.

`run_ui_test_uc.py` / `run_ui_test.py` still read their test cases from
`TEST_CASES` in `.env`; `run_test.py` (step 5 below) does not.

## 5. List the test cases for `run_test.py`

`run_test.py` loads its tax_code/lookup_code pairs from a JSON file, not
`.env`. Create `test_cases.json` in the project root:

```json
[
  {"tax_code": "0314218920", "lookup_code": "j5nnm3lzo5"},
  {"tax_code": "0400495597", "lookup_code": "mr3pye3m65"}
]
```

Extra fields (`invoice_no`, `document_id`, ...) are ignored if present, so
you can paste a larger export as-is. Pairs already recorded in
`logs/downloaded.json` (from a past successful run) are skipped
automatically, even across different `--cases` files.

## 6. Run the test

```bash
python run_test.py                                # reads test_cases.json (default path)
python run_test.py --cases /path/to/your_file.json  # or a file of your own — replace with its real path
```

What happens:
1. The script prints the target URL and **asks you to type `y` to
   confirm you're authorized to test it** — nothing touches the network
   before you confirm.
2. It launches Chrome (you'll see a window open, unless you've modified
   the code to run headless) and drives it to the real target page.
3. It waits for EzSolver to pull a Turnstile token out of the page.
4. If `VERIFY_ENDPOINT` is set, it submits the token (and any extra
   fields) to that endpoint and prints the HTTP status + a snippet of
   the response.
5. It prints a plain-language summary of what the result means — see the
   table in `README.md` / the `main` branch README.

### Sequential vs. parallel runs

Both are controlled by `MAX_WORKERS` in `.env`:

```bash
MAX_WORKERS=1    # sequential — one browser at a time, test cases run one after another
MAX_WORKERS=4    # parallel — up to 4 browsers at once, each with its own test case
python run_test.py
```

Either way, pairs already recorded in `logs/downloaded.json` are skipped
the same way; if interrupted mid-run, re-running picks up where it left
off.

## 7. Clean up

```bash
# Leave the venv when done
deactivate
```

## Troubleshooting

- **"Chrome not found"** — set `CHROME_PATH` to your Chrome executable's
  full path before running, e.g.:
  ```bash
  export CHROME_PATH="/usr/bin/google-chrome-stable"   # Linux
  python run_test.py
  ```
- **Timeout / no token after a long wait** — the target's Turnstile risk
  scoring may be flagging the automated browser itself (common on
  headless setups, or if `headless=True` was set without a realistic
  user agent). See the "What a result actually tells us" section in
  `README.md` before assuming this is your backend's defense working.
- **Linux: "Xvfb not found"** — `sudo apt install xvfb` (Debian/Ubuntu)
  or the equivalent for your distro.
