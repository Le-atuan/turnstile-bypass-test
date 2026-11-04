#!/usr/bin/env python3
"""
UI-level bot-detection test — ezsolver branch
-------------------------------------------------
Goal: answer "does tracuu.einvoice365.com detect a bot that fills in the
REAL lookup form and clicks the REAL submit button, typing like a human?"
— a different, more realistic question than run_test.py's (which only
tests whether a token can be obtained and whether a raw HTTP POST to a
guessed endpoint is accepted).

IMPORTANT — why this is a separate script from run_test.py:
EzSolver's own `solve_on_page()` (used by run_test.py) runs
`document.body.innerHTML = ''` as its very first step, which WIPES the
real page's DOM — including the real "Mã số thuế" / "Mã tra cứu" inputs —
before injecting its own isolated widget. That makes it unsuitable for a
test that needs the real form to still exist. This script never calls
that function. It only reuses EzSolver's browser-launch helpers
(`start_browser`, `_find_chrome`, `_start_xvfb_if_needed`) and otherwise:

  1. Loads the real page completely unmodified — nothing injected, nothing
     wiped.
  2. Waits for the page's OWN Turnstile widget (managed/invisible mode —
     it auto-renders and resolves with no click needed, confirmed during
     recon) to produce a token in the background, by polling the real
     hidden `cf-turnstile-response` input the page's own JS already
     manages.
  3. Types the tax code and lookup code for ONE real test case into the
     real form fields (located by their placeholder text, confirmed from
     a screenshot of the real page), with randomized per-character delay
     to avoid an instant/robotic fill pattern. Repeats once per entry in
     TEST_CASES — a different real invoice each run.
  4. Clicks the real "Tra cứu" button.
  5. Captures and prints what the page shows afterward, so you can see
     whether the lookup succeeded, failed, or the page reacted as if it
     detected automation (CAPTCHA re-challenge, block page, error state).

NOT YET EXECUTED IN THIS SESSION: this cloud sandbox has no real Chrome
and no network path to the target, so this script has not been run
end-to-end. nodriver's exact method names (`page.select`, `page.find`,
`element.send_keys`) are written per its documented API but should be
verified against the installed nodriver version on your machine — if a
call fails, check `nodriver`'s current API (e.g. `python -c "import
nodriver; help(nodriver.Tab)"`) and adjust.

LOGGING: every run writes a timestamped log file under logs/ (full
detail, including tracebacks) in addition to concise console output. On
any step failure, this script also tries to save a screenshot and the
page's HTML into logs/ for offline debugging — see the "Logging &
troubleshooting" section in README.md for exactly what gets written and
where, and what to do with it.

Only run this against infrastructure you own, with explicit authorization.
"""
import asyncio
import json
import logging
import os
import platform
import random
import sys
import time
import traceback
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "EzSolver"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from solver import _find_chrome, _get_profile_dir, _start_xvfb_if_needed, start_browser  # noqa: E402

SITEURL = os.environ.get("TARGET_SITEURL", "https://tracuu.einvoice365.com/").strip()
TEST_CASES = json.loads(os.environ.get("TEST_CASES", "[]") or "[]")
TOKEN_WAIT_TIMEOUT = int(os.environ.get("TOKEN_WAIT_TIMEOUT", "20"))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))  # retries per test case on failure

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
RUN_STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
LOG_FILE = os.path.join(LOG_DIR, f"ui_test_{RUN_STAMP}.log")


# --- Logging setup --------------------------------------------------------
# Console: concise (INFO and up, no timestamps — matches the previous
#   print()-based output so it stays easy to read live).
# File (logs/ui_test_<timestamp>.log): full detail (DEBUG and up, with
#   timestamps, module, and level) — this is what you attach when
#   reporting an error, since it has the full traceback for any failure.
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("ui_test")
logger.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(message)s"))

file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
)

logger.addHandler(console_handler)
logger.addHandler(file_handler)


def log_environment():
    """Record enough context in the log file to debug a failure without re-asking the user."""
    logger.debug("=== Environment ===")
    logger.debug("Python: %s", sys.version.replace("\n", " "))
    logger.debug("Platform: %s", platform.platform())
    try:
        import nodriver
        logger.debug("nodriver version: %s", getattr(nodriver, "__version__", "unknown"))
    except Exception as exc:
        logger.debug("Could not read nodriver version: %s", exc)
    try:
        logger.debug("Chrome path resolved to: %s", _find_chrome())
    except Exception as exc:
        logger.debug("Could not resolve Chrome path: %s", exc)
    logger.debug("TARGET_SITEURL=%s", SITEURL)
    logger.debug("TOKEN_WAIT_TIMEOUT=%s", TOKEN_WAIT_TIMEOUT)
    logger.debug("MAX_ATTEMPTS=%s", MAX_ATTEMPTS)
    logger.debug("TEST_CASES count=%s", len(TEST_CASES))
    logger.debug("===================")


def require_own_site_ack():
    logger.info("=" * 70)
    logger.info("This will drive a real browser to fill the REAL form on:\n    %s", SITEURL)
    logger.info(
        "It will run %s test case(s), one distinct real tax_code/lookup_code pair each, "
        "retrying up to %s time(s) per case on failure.",
        len(TEST_CASES), MAX_ATTEMPTS,
    )
    logger.info("Only run this against a site you own or are authorized to test.")
    logger.info("=" * 70)
    ans = input("Confirm you are authorized to test this target [y/N]: ").strip().lower()
    logger.debug("Authorization prompt answered: %r", ans)
    if ans != "y":
        logger.info("Aborted.")
        sys.exit(1)


async def human_type(element, text: str):
    """Type one character at a time with a randomized delay, like a real user."""
    for ch in text:
        await element.send_keys(ch)
        await asyncio.sleep(random.uniform(0.05, 0.18))


async def wait_for_token(page, timeout: int) -> bool:
    """Poll the page's own hidden Turnstile input until it has a value.

    Deliberately does NOT inject or call turnstile.render() — the real
    page's own script already renders it in managed/invisible mode.
    """
    deadline = time.time() + timeout
    poll_count = 0
    while time.time() < deadline:
        poll_count += 1
        val = await page.evaluate(
            "document.querySelector('[name=\"cf-turnstile-response\"]')?.value || ''"
        )
        if val:
            logger.debug("Token appeared after %d poll(s): %s...", poll_count, val[:24])
            return True
        await asyncio.sleep(0.5)
    logger.debug("No token after %d poll(s) / %ds timeout", poll_count, timeout)
    return False


async def dump_failure_artifacts(page, run_label: str, step: str):
    """Best-effort capture of a screenshot + page HTML on failure, for offline debugging.

    Wrapped in its own try/except per artifact: a failure saving one artifact
    must never hide the original error or crash the run.
    """
    safe_label = run_label.replace("/", "-")
    base = os.path.join(LOG_DIR, f"failure_{RUN_STAMP}_run{safe_label}_{step}")

    try:
        screenshot_path = base + ".png"
        # nodriver's screenshot method name has varied across versions
        # (save_screenshot / screenshot). Try both; log whichever worked.
        if hasattr(page, "save_screenshot"):
            await page.save_screenshot(screenshot_path)
        elif hasattr(page, "screenshot"):
            await page.screenshot(screenshot_path)
        else:
            raise AttributeError("page has neither save_screenshot() nor screenshot()")
        logger.info("Saved failure screenshot: %s", screenshot_path)
    except Exception as exc:
        logger.warning("Could not save screenshot (non-fatal): %s", exc)

    try:
        html_path = base + ".html"
        html = await page.evaluate("document.documentElement.outerHTML")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html or "")
        logger.info("Saved failure page HTML: %s", html_path)
    except Exception as exc:
        logger.warning("Could not save page HTML (non-fatal): %s", exc)


async def run_once(run_label: str, tax_code: str, lookup_code: str) -> bool:
    """Run one lookup attempt. Returns True on a clean completion, False on failure.

    Every step is wrapped individually so the log file shows exactly which
    step failed and why, with a full traceback — and so one failing run
    doesn't abort the remaining TEST_CASES.
    """
    logger.info("\n--- Run %s (tax_code=%s, lookup_code=%s) ---", run_label, tax_code, lookup_code)
    t0 = time.time()
    browser = None
    page = None

    try:
        browser = await start_browser()
    except Exception:
        logger.error("Run %s: FAILED to start the browser.", run_label)
        logger.debug("Traceback:\n%s", traceback.format_exc())
        return False

    try:
        try:
            page = await browser.get(SITEURL)
        except Exception:
            logger.error("Run %s: FAILED to navigate to %s", run_label, SITEURL)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            return False

        logger.info("Waiting for the page's own Turnstile widget to resolve in the background...")
        try:
            got_token = await wait_for_token(page, timeout=TOKEN_WAIT_TIMEOUT)
        except Exception:
            logger.error("Run %s: FAILED while polling for the Turnstile token.", run_label)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            await dump_failure_artifacts(page, run_label, "token_poll_error")
            return False

        if not got_token:
            logger.warning(
                "Run %s RESULT: no token appeared within %ds — the widget itself "
                "may be flagging this browser session.", run_label, TOKEN_WAIT_TIMEOUT
            )
            await dump_failure_artifacts(page, run_label, "no_token")
            return False

        logger.info("Token ready. Filling the real form like a human would...")
        try:
            tax_input = await page.select('input[placeholder="MST bên bán"]')
            await tax_input.click()
            await human_type(tax_input, tax_code)
            await asyncio.sleep(random.uniform(0.3, 0.9))
        except Exception:
            logger.error(
                "Run %s: FAILED to fill the MST field — selector "
                "'input[placeholder=\"MST bên bán\"]' may not match the live page "
                "(placeholder text or markup may have changed). Check the saved "
                "HTML snapshot.", run_label
            )
            logger.debug("Traceback:\n%s", traceback.format_exc())
            await dump_failure_artifacts(page, run_label, "fill_mst_error")
            return False

        try:
            lookup_input = await page.select('input[placeholder="Mã tra cứu hóa đơn"]')
            await lookup_input.click()
            await human_type(lookup_input, lookup_code)
            await asyncio.sleep(random.uniform(0.3, 0.9))
        except Exception:
            logger.error(
                "Run %s: FAILED to fill the lookup-code field — selector "
                "'input[placeholder=\"Mã tra cứu hóa đơn\"]' may not match the "
                "live page. Check the saved HTML snapshot.", run_label
            )
            logger.debug("Traceback:\n%s", traceback.format_exc())
            await dump_failure_artifacts(page, run_label, "fill_lookup_error")
            return False

        try:
            submit_btn = await page.find("Tra cứu", best_match=True)
            await submit_btn.click()
        except Exception:
            logger.error(
                "Run %s: FAILED to find/click the 'Tra cứu' button.", run_label
            )
            logger.debug("Traceback:\n%s", traceback.format_exc())
            await dump_failure_artifacts(page, run_label, "click_submit_error")
            return False

        logger.info("Submitted. Waiting for the page to respond...")
        await asyncio.sleep(3)

        try:
            body_text = await page.evaluate("document.body.innerText.slice(0, 600)")
            logger.info("Page content after submit (first 600 chars):")
            logger.info(body_text)
        except Exception:
            logger.error("Run %s: FAILED to read the page's response content.", run_label)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            await dump_failure_artifacts(page, run_label, "read_result_error")
            return False

        elapsed = round(time.time() - t0, 2)
        logger.info("Run %s completed in %ss.", run_label, elapsed)
        return True

    except Exception:
        # Catch-all safety net: anything not already caught above still gets
        # logged with a full traceback instead of crashing the whole script.
        logger.error("Run %s: UNEXPECTED error outside the normal step handling.", run_label)
        logger.debug("Traceback:\n%s", traceback.format_exc())
        if page is not None:
            await dump_failure_artifacts(page, run_label, "unexpected_error")
        return False

    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                logger.debug("browser.stop() raised (non-fatal):\n%s", traceback.format_exc())


async def run_with_retries(run_label: str, tax_code: str, lookup_code: str) -> bool:
    """Run one test case, retrying up to MAX_ATTEMPTS times on failure.

    Each attempt opens a fresh browser (run_once's own finally block closes
    it), so a retry starts clean rather than reusing a possibly-broken
    session. A short randomized backoff separates attempts so retries don't
    hammer the site back-to-back.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempt_label = f"{run_label} (attempt {attempt}/{MAX_ATTEMPTS})"
        ok = await run_once(attempt_label, tax_code, lookup_code)
        if ok:
            return True

        if attempt < MAX_ATTEMPTS:
            backoff = random.uniform(2, 5) * attempt
            logger.warning(
                "Run %s failed — retrying in %.1fs (attempt %d/%d)...",
                run_label, backoff, attempt + 1, MAX_ATTEMPTS,
            )
            await asyncio.sleep(backoff)
        else:
            logger.error(
                "Run %s: all %d attempt(s) failed. See the log file for details.",
                run_label, MAX_ATTEMPTS,
            )

    return False


async def main():
    logger.debug("Log file for this run: %s", LOG_FILE)
    log_environment()

    if not TEST_CASES:
        logger.error(
            "Set TEST_CASES in .env first (see .env.example) — a JSON list of "
            '{"tax_code": "...", "lookup_code": "..."} objects, one per run.'
        )
        sys.exit(1)

    require_own_site_ack()

    xvfb = _start_xvfb_if_needed()
    results = []
    try:
        total = len(TEST_CASES)
        for i, case in enumerate(TEST_CASES, start=1):
            try:
                tax_code = case["tax_code"]
                lookup_code = case["lookup_code"]
            except (KeyError, TypeError):
                logger.error(
                    "TEST_CASES entry %d is malformed (expected keys 'tax_code' "
                    "and 'lookup_code'): %r", i, case
                )
                results.append(False)
                continue

            ok = await run_with_retries(f"{i}/{total}", tax_code, lookup_code)
            results.append(ok)
            if i < total:
                await asyncio.sleep(random.uniform(2, 5))
    finally:
        if xvfb:
            xvfb.terminate()

    succeeded = sum(1 for r in results if r)
    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY: %d/%d run(s) completed without error.", succeeded, len(results))
    if succeeded < len(results):
        logger.info(
            "Some runs failed — see this run's full log for tracebacks and the "
            "exact step that failed:\n    %s", LOG_FILE
        )
        logger.info(
            "Any saved screenshots/HTML snapshots for failed steps are in:\n    %s",
            LOG_DIR,
        )
    logger.info("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
