#!/usr/bin/env python3
"""
UI bot-detection test (ezsolver branch).

Drives the real page like a user would: waits for Turnstile, fills the
real form, clicks "Tra cứu", reads the result — to see if the site
detects a bot operating its UI. Runs once per TEST_CASES entry.

Unlike run_test.py, this never calls EzSolver's solve_on_page() (which
wipes the real DOM) — it only reuses EzSolver's browser-launch helpers.
Logs go to logs/ui_test_<timestamp>.log; failures also save a
screenshot + HTML there.

Only run against infrastructure you own or are authorized to test.
Not yet run end-to-end here (no real Chrome / no network to target) —
verify nodriver method names on first local run.
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
RESULT_WAIT_TIMEOUT = int(os.environ.get("RESULT_WAIT_TIMEOUT", "20"))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))

# Phrases meaning "no record found" — a valid result, never retried.
# Guessed; adjust once a real "not found" response is seen.
NOT_FOUND_KEYWORDS = [
    kw.strip() for kw in os.environ.get(
        "NOT_FOUND_KEYWORDS",
        "không tìm thấy,không có dữ liệu,không tồn tại,not found,no data,no record",
    ).split(",")
    if kw.strip()
]

# Only "error" is retried; the other two are final.
OUTCOME_SUCCESS = "success"
OUTCOME_NOT_FOUND = "not_found"
OUTCOME_ERROR = "error"

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
RUN_STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
LOG_FILE = os.path.join(LOG_DIR, f"ui_test_{RUN_STAMP}.log")

# Console: concise (INFO). File: full detail + tracebacks (DEBUG).
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
    """Log Python/platform/nodriver/Chrome context for debugging."""
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
    logger.debug("RESULT_WAIT_TIMEOUT=%s", RESULT_WAIT_TIMEOUT)
    logger.debug("MAX_ATTEMPTS=%s", MAX_ATTEMPTS)
    logger.debug("NOT_FOUND_KEYWORDS=%s", NOT_FOUND_KEYWORDS)
    logger.debug("TEST_CASES count=%s", len(TEST_CASES))
    logger.debug("===================")


def require_own_site_ack():
    logger.info("=" * 70)
    logger.info("This will drive a real browser to fill the REAL form on:\n    %s", SITEURL)
    logger.info("Running %s test case(s), retrying up to %s time(s) each on technical failure.",
                len(TEST_CASES), MAX_ATTEMPTS)
    logger.info("Only run this against a site you own or are authorized to test.")
    logger.info("=" * 70)
    ans = input("Confirm you are authorized to test this target [y/N]: ").strip().lower()
    logger.debug("Authorization prompt answered: %r", ans)
    if ans != "y":
        logger.info("Aborted.")
        sys.exit(1)


async def human_type(element, text: str):
    """Type one char at a time with a random delay, like a real user."""
    for ch in text:
        await element.send_keys(ch)
        await asyncio.sleep(random.uniform(0.05, 0.18))


async def wait_for_token(page, timeout: int) -> bool:
    """Poll the page's own hidden Turnstile input until it has a value."""
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


async def find_button_rect(page, view_id: str, text: str):
    """Locate a button's on-screen rect. Tries the Webix view-id first,
    then falls back to finding the smallest visible element whose text
    matches `text` — so it doesn't break if the view-id differs.
    Returns a dict with x/y/w/h (+ tag/id/cls for logging) or None.
    """
    return await page.evaluate("""
        (() => {
            const VIEW_ID = %s;
            const TEXT = %s;
            let el = null;

            if (typeof webix !== 'undefined' && webix.$$) {
                const v = webix.$$(VIEW_ID);
                if (v && v.$view) el = v.$view.querySelector('button') || v.$view;
            }

            if (!el) {
                const nodes = [...document.querySelectorAll('button, a, div, span')];
                const hits = nodes.filter(n => {
                    const t = (n.innerText || '').trim();
                    return t && t.includes(TEXT) && t.length < 30 && n.offsetParent !== null;
                });
                hits.sort((a, b) => {
                    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                    return (ra.width * ra.height) - (rb.width * rb.height);
                });
                el = hits[0] || null;
            }

            if (!el) return null;
            el.scrollIntoView({block: 'center', inline: 'center'});
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return null;
            return {x: r.x, y: r.y, w: r.width, h: r.height,
                    tag: el.tagName, id: el.id, cls: el.className};
        })()
    """ % (json.dumps(view_id), json.dumps(text)))


async def click_rect(page, rect):
    """Move the mouse and click the center of a rect, with small jitter."""
    cx = rect["x"] + rect["w"] / 2 + random.uniform(-3, 3)
    cy = rect["y"] + rect["h"] / 2 + random.uniform(-3, 3)
    await page.mouse_move(cx - 40, cy - 10)
    await asyncio.sleep(random.uniform(0.15, 0.3))
    await page.mouse_move(cx, cy)
    await asyncio.sleep(random.uniform(0.08, 0.2))
    await page.mouse_click(cx, cy)


async def click_submit_button(page):
    """Click the real "Tra cứu" button (by view-id, else by its text)."""
    rect = await find_button_rect(page, "search:btnsearch", "Tra cứu")
    if not rect:
        raise RuntimeError(
            "Could not locate the 'Tra cứu' button by view-id or by text — "
            "check the saved HTML snapshot."
        )
    logger.debug("Submit button found: tag=%s id=%s cls=%s",
                 rect.get("tag"), rect.get("id"), rect.get("cls"))
    await click_rect(page, rect)


async def maybe_click_download_xml(page, run_label: str):
    """If the "Tải XML" button is present, click it and report whether a
    new tab/blob resulted. Informational only — never affects the outcome,
    and any error here is logged as a warning. (Recon found no handler
    bound on this button, so a click likely does nothing.)
    """
    try:
        rect = await find_button_rect(page, "search:btnxml", "Tải XML")
    except Exception as exc:
        logger.warning("Run %s: could not locate 'Tải XML' (non-fatal): %s", run_label, exc)
        return

    if not rect:
        logger.debug("Run %s: 'Tải XML' button not found — nothing to click.", run_label)
        return

    logger.info("Run %s: 'Tải XML' found — clicking it (best-effort)...", run_label)

    try:
        # Hook window.open/createObjectURL to detect if THIS click does anything.
        await page.evaluate("""
            (() => {
                window.__xmlClickProbe = {opened: false, blobUrl: null};
                if (window.__xmlClickHooked) return;
                window.__xmlClickHooked = true;
                const origOpen = window.open;
                window.open = function(...args) {
                    window.__xmlClickProbe.opened = true;
                    return origOpen.apply(window, args);
                };
                const origCreateObjectURL = URL.createObjectURL;
                URL.createObjectURL = function(obj) {
                    const url = origCreateObjectURL.call(URL, obj);
                    window.__xmlClickProbe.blobUrl = url;
                    return url;
                };
            })()
        """)

        await click_rect(page, rect)
        await asyncio.sleep(2)  # let any download/navigation happen

        probe = await page.evaluate("window.__xmlClickProbe || {}")
        logger.info("Run %s: 'Tải XML' click — opened new tab: %s, new blob: %s",
                    run_label, probe.get("opened"), bool(probe.get("blobUrl")))
        if probe.get("blobUrl"):
            logger.debug("Run %s: new blob URL: %s", run_label, probe.get("blobUrl"))
    except Exception as exc:
        logger.warning("Run %s: error clicking 'Tải XML' (non-fatal): %s", run_label, exc)
        logger.debug("Traceback:\n%s", traceback.format_exc())


async def wait_for_result(page, timeout: int, baseline_len: int) -> bool:
    """Poll for the lookup result, using several signals so it doesn't
    depend on one view-id: the Webix status banner becoming visible, a
    PDF viewer / iframe appearing, or the page text growing noticeably
    from baseline_len (captured just before the submit click). This can
    take 5-8s, so we poll rather than sleep a fixed amount.
    """
    deadline = time.time() + timeout
    poll_count = 0
    while time.time() < deadline:
        poll_count += 1
        signal = await page.evaluate("""
            (() => {
                const BASE = %d;
                if (typeof webix !== 'undefined' && webix.$$) {
                    const v = webix.$$('search:status');
                    if (v && v.isVisible && v.isVisible()) return 'status-banner';
                }
                if (document.querySelector('iframe, embed, canvas')) return 'pdf-viewer';
                const len = (document.body.innerText || '').length;
                if (len > BASE + 40) return 'text-grew';
                return '';
            })()
        """ % baseline_len)
        if signal:
            logger.debug("Result detected (%s) after %d poll(s)", signal, poll_count)
            return True
        await asyncio.sleep(0.5)
    logger.debug("No result signal after %d poll(s) / %ds", poll_count, timeout)
    return False


async def read_result_text(page) -> str:
    """Return the result banner's text, falling back to the page body."""
    try:
        status_text = await page.evaluate("""
            (() => {
                if (typeof webix === 'undefined') return '';
                const v = webix.$$('search:status');
                return (v && v.$view) ? v.$view.innerText : '';
            })()
        """)
    except Exception:
        status_text = ""

    if status_text:
        return status_text

    return await page.evaluate("document.body.innerText.slice(0, 600)")


async def dump_failure_artifacts(page, run_label: str, step: str):
    """Best-effort screenshot + page HTML on failure. Never crashes the run."""
    safe_label = run_label.replace("/", "-")
    base = os.path.join(LOG_DIR, f"failure_{RUN_STAMP}_run{safe_label}_{step}")

    try:
        screenshot_path = base + ".png"
        # Method name varies across nodriver versions.
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


async def run_once(run_label: str, tax_code: str, lookup_code: str) -> str:
    """Run one lookup attempt. Returns success / not_found / error.
    Each step is wrapped so the log shows exactly which one failed.
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
        return OUTCOME_ERROR

    try:
        try:
            page = await browser.get(SITEURL)
        except Exception:
            logger.error("Run %s: FAILED to navigate to %s", run_label, SITEURL)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            return OUTCOME_ERROR

        logger.info("Waiting for Turnstile to resolve in the background...")
        try:
            got_token = await wait_for_token(page, timeout=TOKEN_WAIT_TIMEOUT)
        except Exception:
            logger.error("Run %s: FAILED while polling for the Turnstile token.", run_label)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            await dump_failure_artifacts(page, run_label, "token_poll_error")
            return OUTCOME_ERROR

        if not got_token:
            logger.warning("Run %s: no token within %ds — the widget may be flagging this session.",
                           run_label, TOKEN_WAIT_TIMEOUT)
            await dump_failure_artifacts(page, run_label, "no_token")
            return OUTCOME_ERROR

        logger.info("Token ready. Filling the real form...")
        try:
            tax_input = await page.select('input[placeholder="MST bên bán"]')
            await tax_input.click()
            await human_type(tax_input, tax_code)
            await asyncio.sleep(random.uniform(0.3, 0.9))
        except Exception:
            logger.error("Run %s: FAILED to fill the MST field (selector may not match). "
                         "Check the saved HTML.", run_label)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            await dump_failure_artifacts(page, run_label, "fill_mst_error")
            return OUTCOME_ERROR

        try:
            lookup_input = await page.select('input[placeholder="Mã tra cứu hóa đơn"]')
            await lookup_input.click()
            await human_type(lookup_input, lookup_code)
            await asyncio.sleep(random.uniform(0.3, 0.9))
        except Exception:
            logger.error("Run %s: FAILED to fill the lookup-code field (selector may not match). "
                         "Check the saved HTML.", run_label)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            await dump_failure_artifacts(page, run_label, "fill_lookup_error")
            return OUTCOME_ERROR

        try:
            baseline_len = await page.evaluate("(document.body.innerText || '').length")
        except Exception:
            baseline_len = 0

        try:
            await click_submit_button(page)
        except Exception:
            logger.error("Run %s: FAILED to click the 'Tra cứu' button.", run_label)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            await dump_failure_artifacts(page, run_label, "click_submit_error")
            return OUTCOME_ERROR

        logger.info("Submitted. Waiting for the result (up to %ds)...", RESULT_WAIT_TIMEOUT)
        try:
            got_result = await wait_for_result(page, timeout=RESULT_WAIT_TIMEOUT, baseline_len=baseline_len)
        except Exception:
            logger.error("Run %s: FAILED while polling for the result banner.", run_label)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            await dump_failure_artifacts(page, run_label, "result_poll_error")
            return OUTCOME_ERROR

        if not got_result:
            logger.warning("Run %s: no result banner within %ds — treated as a retryable error "
                           "(could be 'no match' or an unregistered click).", run_label, RESULT_WAIT_TIMEOUT)
            await dump_failure_artifacts(page, run_label, "no_result")
            return OUTCOME_ERROR

        try:
            body_text = await read_result_text(page)
        except Exception:
            logger.error("Run %s: FAILED to read the result content.", run_label)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            await dump_failure_artifacts(page, run_label, "read_result_error")
            return OUTCOME_ERROR

        logger.info("Result content (first 600 chars):")
        logger.info(body_text)

        await maybe_click_download_xml(page, run_label)

        elapsed = round(time.time() - t0, 2)
        lowered = (body_text or "").lower()
        matched_keyword = next((kw for kw in NOT_FOUND_KEYWORDS if kw.lower() in lowered), None)

        if matched_keyword:
            logger.warning("Run %s RESULT: NOT FOUND — matched \"%s\" (%ss). Final, not retried.",
                           run_label, matched_keyword, elapsed)
            return OUTCOME_NOT_FOUND

        logger.info("Run %s RESULT: SUCCESS — no 'not found' phrase, in %ss.", run_label, elapsed)
        return OUTCOME_SUCCESS

    except Exception:
        # Safety net: log anything uncaught instead of crashing.
        logger.error("Run %s: UNEXPECTED error.", run_label)
        logger.debug("Traceback:\n%s", traceback.format_exc())
        if page is not None:
            await dump_failure_artifacts(page, run_label, "unexpected_error")
        return OUTCOME_ERROR

    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                logger.debug("browser.stop() raised (non-fatal):\n%s", traceback.format_exc())


async def run_with_retries(run_label: str, tax_code: str, lookup_code: str) -> str:
    """Retry a case up to MAX_ATTEMPTS times, but only on a technical error.
    Each attempt uses a fresh browser, with a small backoff between tries.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempt_label = f"{run_label} (attempt {attempt}/{MAX_ATTEMPTS})"
        outcome = await run_once(attempt_label, tax_code, lookup_code)

        if outcome != OUTCOME_ERROR:
            return outcome  # success or not_found — final

        if attempt < MAX_ATTEMPTS:
            backoff = random.uniform(2, 5) * attempt
            logger.warning("Run %s: technical error — retrying in %.1fs (attempt %d/%d)...",
                           run_label, backoff, attempt + 1, MAX_ATTEMPTS)
            await asyncio.sleep(backoff)
        else:
            logger.error("Run %s: all %d attempt(s) failed. See the log for details.",
                         run_label, MAX_ATTEMPTS)

    return OUTCOME_ERROR


async def main():
    logger.debug("Log file for this run: %s", LOG_FILE)
    log_environment()

    if not TEST_CASES:
        logger.error('Set TEST_CASES in .env first — a JSON list of '
                     '{"tax_code": "...", "lookup_code": "..."} objects.')
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
                logger.error("TEST_CASES entry %d is malformed: %r", i, case)
                results.append(OUTCOME_ERROR)
                continue

            outcome = await run_with_retries(f"{i}/{total}", tax_code, lookup_code)
            results.append(outcome)
            if i < total:
                await asyncio.sleep(random.uniform(2, 5))
    finally:
        if xvfb:
            xvfb.terminate()

    succeeded = results.count(OUTCOME_SUCCESS)
    not_found = results.count(OUTCOME_NOT_FOUND)
    errored = results.count(OUTCOME_ERROR)
    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY: %d found | %d not found | %d technical error (of %d)",
                succeeded, not_found, errored, len(results))
    if errored:
        logger.info("See the full log for tracebacks: %s", LOG_FILE)
        logger.info("Saved screenshots/HTML (if any): %s", LOG_DIR)
    logger.info("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
