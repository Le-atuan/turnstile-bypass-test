#!/usr/bin/env python3
"""
Full end-to-end UI test using undetected-chromedriver (the only backend that
got a real Turnstile token, per the run_test.py comparison). Drives the real
page: load -> wait for the page's own Turnstile widget to resolve -> fill
MST + lookup code -> click "Tra cứu" -> wait for result -> click "Tải XML".

Only run against infrastructure you own or are authorized to test.
"""
import json
import logging
import os
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

import undetected_chromedriver as uc  # noqa: E402
from selenium.webdriver.common.action_chains import ActionChains  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402
from solver_uc import _installed_chrome_major  # noqa: E402

SITEURL = os.environ.get("TARGET_SITEURL", "https://tracuu.einvoice365.com/").strip()
TEST_CASES = json.loads(os.environ.get("TEST_CASES", "[]") or "[]")
TOKEN_WAIT_TIMEOUT = int(os.environ.get("TOKEN_WAIT_TIMEOUT", "20"))
RESULT_WAIT_TIMEOUT = int(os.environ.get("RESULT_WAIT_TIMEOUT", "20"))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))

NOT_FOUND_KEYWORDS = [
    kw.strip() for kw in os.environ.get(
        "NOT_FOUND_KEYWORDS",
        "không tìm thấy,không có dữ liệu,không tồn tại,not found,no data,no record",
    ).split(",")
    if kw.strip()
]

OUTCOME_SUCCESS = "success"
OUTCOME_NOT_FOUND = "not_found"
OUTCOME_ERROR = "error"

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
RUN_STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
LOG_FILE = os.path.join(LOG_DIR, f"ui_test_uc_{RUN_STAMP}.log")
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("ui_test_uc")
logger.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(message)s"))

file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))

logger.addHandler(console_handler)
logger.addHandler(file_handler)


def require_own_site_ack():
    logger.info("=" * 70)
    logger.info("This will drive a real browser (undetected-chromedriver) against:\n    %s", SITEURL)
    logger.info("Running %s test case(s), retrying up to %s time(s) each on technical failure.",
                len(TEST_CASES), MAX_ATTEMPTS)
    logger.info("Only run this against a site you own or are authorized to test.")
    logger.info("=" * 70)
    ans = input("Confirm you are authorized to test this target [y/N]: ").strip().lower()
    logger.debug("Authorization prompt answered: %r", ans)
    if ans != "y":
        logger.info("Aborted.")
        sys.exit(1)


def start_driver():
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    return uc.Chrome(options=options, headless=False, version_main=_installed_chrome_major())


def human_type(element, text: str):
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(0.05, 0.18))


def wait_for_token(driver, timeout: int) -> bool:
    deadline = time.time() + timeout
    poll_count = 0
    while time.time() < deadline:
        poll_count += 1
        val = driver.execute_script(
            "return document.querySelector('[name=\"cf-turnstile-response\"]')?.value || '';"
        )
        if val:
            logger.debug("Token appeared after %d poll(s): %s...", poll_count, val[:24])
            return True
        time.sleep(0.5)
    logger.debug("No token after %d poll(s) / %ds timeout", poll_count, timeout)
    return False


def find_button_rect(driver, view_id: str, text: str):
    raw = driver.execute_script("""
        return JSON.stringify((() => {
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
            return {x: r.x, y: r.y, w: r.width, h: r.height, tag: el.tagName, id: el.id, cls: el.className};
        })());
    """ % (json.dumps(view_id), json.dumps(text)))
    return json.loads(raw) if raw and raw != "null" else None


def click_rect(driver, rect):
    cx = rect["x"] + rect["w"] / 2 + random.uniform(-3, 3)
    cy = rect["y"] + rect["h"] / 2 + random.uniform(-3, 3)
    ActionChains(driver).move_by_offset(cx - 40, cy - 10).perform()
    time.sleep(random.uniform(0.15, 0.3))
    ActionChains(driver).move_by_offset(40, 10).perform()
    time.sleep(random.uniform(0.08, 0.2))
    ActionChains(driver).click().perform()
    ActionChains(driver).move_by_offset(-cx, -cy).perform()  # reset origin


def wait_for_outcome(driver, timeout: int):
    deadline = time.time() + timeout
    poll_count = 0
    while time.time() < deadline:
        poll_count += 1
        body_text = driver.execute_script("return document.body.innerText || '';")
        lowered = (body_text or "").lower()
        matched_keyword = next((kw for kw in NOT_FOUND_KEYWORDS if kw.lower() in lowered), None)
        if matched_keyword:
            logger.debug("Result detected (not-found keyword: %s) after %d poll(s)", matched_keyword, poll_count)
            return OUTCOME_NOT_FOUND
        rect = find_button_rect(driver, "search:btnxml", "Tải XML")
        if rect:
            logger.debug("Result detected ('Tải XML' button present) after %d poll(s)", poll_count)
            return OUTCOME_SUCCESS
        time.sleep(0.5)
    logger.debug("No conclusive result after %d poll(s) / %ds", poll_count, timeout)
    return None


def maybe_click_download_xml(driver, run_label: str) -> bool:
    """Returns True only if the click produced a real XML download (new blob)."""
    try:
        rect = find_button_rect(driver, "search:btnxml", "Tải XML")
    except Exception as exc:
        logger.warning("Run %s: could not locate 'Tải XML' (non-fatal): %s", run_label, exc)
        return False
    if not rect:
        logger.debug("Run %s: 'Tải XML' button not found — nothing to click.", run_label)
        return False

    logger.info("Run %s: 'Tải XML' found — clicking it (best-effort)...", run_label)
    try:
        driver.execute_script("""
            window.__xmlClickProbe = {opened: false, blobUrl: null};
            if (!window.__xmlClickHooked) {
                window.__xmlClickHooked = true;
                const origOpen = window.open;
                window.open = function(...args) { window.__xmlClickProbe.opened = true; return origOpen.apply(window, args); };
                const origCreateObjectURL = URL.createObjectURL;
                URL.createObjectURL = function(obj) {
                    const url = origCreateObjectURL.call(URL, obj);
                    window.__xmlClickProbe.blobUrl = url;
                    return url;
                };
            }
        """)
        click_rect(driver, rect)
        time.sleep(2)
        probe = json.loads(driver.execute_script("return JSON.stringify(window.__xmlClickProbe || {});") or "{}")
        logger.info("Run %s: 'Tải XML' click — opened new tab: %s, new blob: %s",
                    run_label, probe.get("opened"), bool(probe.get("blobUrl")))
        if probe.get("blobUrl"):
            logger.debug("Run %s: new blob URL: %s", run_label, probe.get("blobUrl"))
        return bool(probe.get("blobUrl"))
    except Exception as exc:
        logger.warning("Run %s: error clicking 'Tải XML' (non-fatal): %s", run_label, exc)
        logger.debug("Traceback:\n%s", traceback.format_exc())
        return False


_saved_artifacts = set()


def dump_failure_artifacts(driver, run_label: str, step: str):
    case_label = run_label.split(" (attempt")[0].strip().replace("/", "of")
    key = (case_label, step)
    if key in _saved_artifacts:
        return
    _saved_artifacts.add(key)
    path = os.path.join(LOG_DIR, f"failure_uc_{RUN_STAMP}_{case_label}_{step}.png")
    try:
        driver.save_screenshot(path)
        logger.info("Saved failure screenshot: %s", path)
    except Exception as exc:
        logger.warning("Could not save screenshot (non-fatal): %s", exc)


def run_once(run_label: str, tax_code: str, lookup_code: str):
    """Returns (outcome, elapsed_seconds). elapsed_seconds is the time from
    step 1 (opening the browser) to a confirmed XML download, and is None
    for any outcome other than a confirmed download."""
    logger.info("\n--- Run %s (tax_code=%s, lookup_code=%s) ---", run_label, tax_code, lookup_code)
    t0 = time.time()
    driver = None

    try:
        driver = start_driver()
    except Exception:
        logger.error("Run %s: FAILED to start the browser.", run_label)
        logger.debug("Traceback:\n%s", traceback.format_exc())
        return OUTCOME_ERROR, None

    try:
        try:
            driver.get(SITEURL)
        except Exception:
            logger.error("Run %s: FAILED to navigate to %s", run_label, SITEURL)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            return OUTCOME_ERROR, None

        logger.info("Waiting for Turnstile to resolve in the background...")
        try:
            got_token = wait_for_token(driver, timeout=TOKEN_WAIT_TIMEOUT)
        except Exception:
            logger.error("Run %s: FAILED while polling for the Turnstile token.", run_label)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            dump_failure_artifacts(driver, run_label, "token_poll_error")
            return OUTCOME_ERROR, None

        if not got_token:
            logger.warning("Run %s: no token within %ds — the widget may be flagging this session.",
                           run_label, TOKEN_WAIT_TIMEOUT)
            dump_failure_artifacts(driver, run_label, "no_token")
            return OUTCOME_ERROR, None

        logger.info("Token ready. Filling the real form...")
        try:
            tax_input = driver.find_element(By.CSS_SELECTOR, 'input[placeholder="MST bên bán"]')
            tax_input.click()
            human_type(tax_input, tax_code)
            time.sleep(random.uniform(0.3, 0.9))
        except Exception:
            logger.error("Run %s: FAILED to fill the MST field (selector may not match).", run_label)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            dump_failure_artifacts(driver, run_label, "fill_mst_error")
            return OUTCOME_ERROR, None

        try:
            lookup_input = driver.find_element(By.CSS_SELECTOR, 'input[placeholder="Mã tra cứu hóa đơn"]')
            lookup_input.click()
            human_type(lookup_input, lookup_code)
            time.sleep(random.uniform(0.3, 0.9))
        except Exception:
            logger.error("Run %s: FAILED to fill the lookup-code field (selector may not match).", run_label)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            dump_failure_artifacts(driver, run_label, "fill_lookup_error")
            return OUTCOME_ERROR, None

        try:
            rect = find_button_rect(driver, "search:btnsearch", "Tra cứu")
            if not rect:
                raise RuntimeError("Could not locate the 'Tra cứu' button by view-id or by text.")
            click_rect(driver, rect)
        except Exception:
            logger.error("Run %s: FAILED to click the 'Tra cứu' button.", run_label)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            dump_failure_artifacts(driver, run_label, "click_submit_error")
            return OUTCOME_ERROR, None

        logger.info("Submitted. Waiting for the result (up to %ds)...", RESULT_WAIT_TIMEOUT)
        try:
            outcome = wait_for_outcome(driver, timeout=RESULT_WAIT_TIMEOUT)
        except Exception:
            logger.error("Run %s: FAILED while polling for the result.", run_label)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            dump_failure_artifacts(driver, run_label, "result_poll_error")
            return OUTCOME_ERROR, None

        if outcome is None:
            logger.warning("Run %s: no 'Tải XML' button and no 'not found' phrase within %ds.",
                           run_label, RESULT_WAIT_TIMEOUT)
            dump_failure_artifacts(driver, run_label, "no_result")
            return OUTCOME_ERROR, None

        if outcome == OUTCOME_NOT_FOUND:
            elapsed = round(time.time() - t0, 2)
            logger.warning("Run %s RESULT: NOT FOUND (%ss). Final, not retried.", run_label, elapsed)
            return OUTCOME_NOT_FOUND, None

        xml_downloaded = maybe_click_download_xml(driver, run_label)
        elapsed = round(time.time() - t0, 2)
        if xml_downloaded:
            logger.info("Run %s RESULT: SUCCESS — XML downloaded, step1->XML in %ss.", run_label, elapsed)
            return OUTCOME_SUCCESS, elapsed
        logger.warning("Run %s RESULT: ERROR — 'Tải XML' button present but no download confirmed (%ss).",
                       run_label, elapsed)
        dump_failure_artifacts(driver, run_label, "xml_download_unconfirmed")
        return OUTCOME_ERROR, None

    except Exception:
        logger.error("Run %s: UNEXPECTED error.", run_label)
        logger.debug("Traceback:\n%s", traceback.format_exc())
        if driver is not None:
            dump_failure_artifacts(driver, run_label, "unexpected_error")
        return OUTCOME_ERROR, None

    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                logger.debug("driver.quit() raised (non-fatal):\n%s", traceback.format_exc())


def run_with_retries(run_label: str, tax_code: str, lookup_code: str):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempt_label = f"{run_label} (attempt {attempt}/{MAX_ATTEMPTS})"
        outcome, elapsed = run_once(attempt_label, tax_code, lookup_code)
        if outcome != OUTCOME_ERROR:
            return outcome, elapsed
        if attempt < MAX_ATTEMPTS:
            backoff = random.uniform(2, 5) * attempt
            logger.warning("Run %s: technical error — retrying in %.1fs (attempt %d/%d)...",
                           run_label, backoff, attempt + 1, MAX_ATTEMPTS)
            time.sleep(backoff)
        else:
            logger.error("Run %s: all %d attempt(s) failed. See the log for details.", run_label, MAX_ATTEMPTS)
    return OUTCOME_ERROR, None


def main():
    logger.debug("Log file for this run: %s", LOG_FILE)
    if not TEST_CASES:
        logger.error('Set TEST_CASES in .env first — a JSON list of '
                     '{"tax_code": "...", "lookup_code": "..."} objects.')
        sys.exit(1)

    require_own_site_ack()

    results = []
    xml_times = []  # step1 -> confirmed XML download, successful runs only
    total = len(TEST_CASES)
    for i, case in enumerate(TEST_CASES, start=1):
        try:
            tax_code = case["tax_code"]
            lookup_code = case["lookup_code"]
        except (KeyError, TypeError):
            logger.error("TEST_CASES entry %d is malformed: %r", i, case)
            results.append(OUTCOME_ERROR)
            continue
        outcome, elapsed = run_with_retries(f"{i}/{total}", tax_code, lookup_code)
        results.append(outcome)
        if elapsed is not None:
            xml_times.append(elapsed)
        if i < total:
            time.sleep(random.uniform(2, 5))

    succeeded = results.count(OUTCOME_SUCCESS)
    not_found = results.count(OUTCOME_NOT_FOUND)
    errored = results.count(OUTCOME_ERROR)
    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY: %d found | %d not found | %d technical error (of %d)",
                succeeded, not_found, errored, len(results))
    if xml_times:
        avg = round(sum(xml_times) / len(xml_times), 2)
        logger.info("Step1 -> XML downloaded: avg %ss over %d confirmed download(s) (min %ss, max %ss)",
                    avg, len(xml_times), min(xml_times), max(xml_times))
    if errored:
        logger.info("See the full log for tracebacks: %s", LOG_FILE)
        logger.info("Saved failure screenshots (if any): %s", LOG_DIR)
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
