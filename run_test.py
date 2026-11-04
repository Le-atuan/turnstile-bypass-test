#!/usr/bin/env python3
"""
Turnstile defensive test harness (ezsolver branch).

For each TEST_CASES entry: get a Turnstile token via EzSolver, then send
a raw HTTP POST to the backend endpoint to see if it accepts a token with
no real browser session behind it. For a test that drives the real
form/button, see run_ui_test.py.

Only run against infrastructure you own or are authorized to test.

Usage:
    cp .env.example .env
    pip install -r requirements.txt
    python run_test.py

Requires Python 3.8+, real Chrome (Xvfb on headless Linux), the EzSolver/ checkout.
"""
import argparse
import json
import logging
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "EzSolver"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests  # noqa: E402

SITEURL = os.environ.get("TARGET_SITEURL", "").strip()
SITEKEY = os.environ.get("TARGET_SITEKEY", "").strip()
VERIFY_ENDPOINT = os.environ.get("VERIFY_ENDPOINT", "").strip()
VERIFY_METHOD = os.environ.get("VERIFY_METHOD", "POST").strip().upper()
VERIFY_TOKEN_FIELD = os.environ.get("VERIFY_TOKEN_FIELD", "cf-turnstile-response").strip()
VERIFY_EXTRA_FIELDS = json.loads(os.environ.get("VERIFY_EXTRA_FIELDS", "{}") or "{}")

# Test cases come from a JSON file, not .env — default path below, or pass
# --cases <path> to use a different file. Format: a JSON list of
# {"tax_code": "...", "lookup_code": "..."} objects; extra fields
# (invoice_no, document_id, ...) are ignored if present.
DEFAULT_TEST_CASES_FILE = "test_cases.json"

_cli = argparse.ArgumentParser()
_cli.add_argument("--cases", default=DEFAULT_TEST_CASES_FILE,
                   help=f"Path to the JSON file of test cases (default: {DEFAULT_TEST_CASES_FILE})")
TEST_CASES_FILE = _cli.parse_args().cases

if os.path.isfile(TEST_CASES_FILE):
    with open(TEST_CASES_FILE, "r", encoding="utf-8") as f:
        TEST_CASES = json.load(f)
else:
    TEST_CASES = []

MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))  # retries on technical failure only
SOLVER_BACKEND = os.environ.get("SOLVER_BACKEND", "uc").strip()  # nodriver | uc
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "1"))  # >1 runs test cases concurrently, each its own browser

# Phrases meaning "no record found" — guessed; adjust once a real one is seen.
NOT_FOUND_KEYWORDS = [
    kw.strip() for kw in os.environ.get(
        "NOT_FOUND_KEYWORDS",
        "không tìm thấy,không có dữ liệu,không tồn tại,not found,no data,no record",
    ).split(",")
    if kw.strip()
]

# Only "error" is retried; the rest are final.
OUTCOME_ACCEPTED = "accepted"    # backend 2xx
OUTCOME_REJECTED = "rejected"    # backend non-2xx
OUTCOME_NOT_FOUND = "not_found"  # matched a "not found" phrase
OUTCOME_ERROR = "error"          # token acquisition or HTTP request itself failed
OUTCOME_SKIPPED = "skipped"      # XML already downloaded in a previous run

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
RUN_STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
LOG_FILE = os.path.join(LOG_DIR, f"run_test_{RUN_STAMP}.log")

os.makedirs(LOG_DIR, exist_ok=True)

# Tracks pairs whose XML was already downloaded in any past run — survives
# `clear_xmls.sh` deleting the actual XML files, since it's a separate record.
DOWNLOADED_FILE = os.path.join(LOG_DIR, "downloaded.json")
_downloaded_lock = threading.Lock()


def _pair_key(tax_code: str, lookup_code: str) -> str:
    return f"{tax_code}|{lookup_code}"


def load_downloaded() -> set:
    try:
        with open(DOWNLOADED_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, ValueError):
        return set()


def mark_downloaded(tax_code: str, lookup_code: str) -> None:
    with _downloaded_lock:
        downloaded = load_downloaded()
        downloaded.add(_pair_key(tax_code, lookup_code))
        with open(DOWNLOADED_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(downloaded), f, ensure_ascii=False, indent=2)

logger = logging.getLogger("run_test")
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
    logger.debug("=== Environment ===")
    logger.debug("Python: %s", sys.version.replace("\n", " "))
    logger.debug("TARGET_SITEURL=%s", SITEURL)
    logger.debug("TARGET_SITEKEY=%s", SITEKEY)
    logger.debug("VERIFY_ENDPOINT=%s", VERIFY_ENDPOINT)
    logger.debug("VERIFY_METHOD=%s", VERIFY_METHOD)
    logger.debug("MAX_ATTEMPTS=%s", MAX_ATTEMPTS)
    logger.debug("NOT_FOUND_KEYWORDS=%s", NOT_FOUND_KEYWORDS)
    logger.debug("TEST_CASES count=%s (source: %s)", len(TEST_CASES), TEST_CASES_FILE)
    logger.debug("===================")


def require_own_site_ack():
    logger.info("=" * 70)
    logger.info("This harness will drive a real browser + raw HTTP requests against:")
    logger.info("    %s", SITEURL)
    logger.info(
        "It will run %s test case(s), retrying up to %s time(s) per case on "
        "TECHNICAL failure only (not on 'not found' or a rejected response).",
        len(TEST_CASES), MAX_ATTEMPTS,
    )
    logger.info("Only run this against a site you own or are authorized to test.")
    logger.info("=" * 70)
    ans = input("Confirm you are authorized to test this target [y/N]: ").strip().lower()
    logger.debug("Authorization prompt answered: %r", ans)
    if ans != "y":
        logger.info("Aborted.")
        sys.exit(1)


def _load_solve_fn():
    if SOLVER_BACKEND == "uc":
        from solver_uc import solve
    else:
        from solver import solve  # EzSolver/solver.py (nodriver)
    return solve


def step1_solve_token(run_label: str):
    solve = _load_solve_fn()

    logger.info("[1/2] Requesting a Turnstile token (backend=%s) for sitekey=%r ...", SOLVER_BACKEND, SITEKEY)
    t0 = time.time()
    token = solve(SITEKEY, SITEURL, timeout=60)
    elapsed = round(time.time() - t0, 2)
    logger.info("      -> solver obtained a token in %ss", elapsed)
    logger.debug("Run %s: token (truncated): %s...", run_label, token[:24])
    return token


def step2_verify_with_backend(token: str, tax_code: str, lookup_code: str):
    logger.info("[2/2] Submitting token to your backend: %s %s", VERIFY_METHOD, VERIFY_ENDPOINT)
    payload = {
        "mst": tax_code,
        "sec": lookup_code,
        "action": "search",
        VERIFY_TOKEN_FIELD: token,
        **VERIFY_EXTRA_FIELDS,
    }

    if VERIFY_METHOD == "GET":
        resp = requests.get(VERIFY_ENDPOINT, params=payload, timeout=30)
    else:
        resp = requests.post(VERIFY_ENDPOINT, json=payload, timeout=30)

    logger.info("      -> HTTP %s", resp.status_code)
    snippet = resp.text[:300].replace("\n", " ")
    logger.info("      -> response (truncated): %s", snippet)
    return resp


def already_downloaded(tax_code: str, lookup_code: str) -> bool:
    """True if an XML for this pair was already saved by a previous run,
    per logs/downloaded.json (independent of whether the XML file still exists)."""
    return _pair_key(tax_code, lookup_code) in load_downloaded()


def save_xml(run_label: str, tax_code: str, lookup_code: str, xml_content: str) -> str:
    """Saves the XML extracted from the api/search response's own `xml` field
    (the real page never makes a second HTTP call for it — see portal.js:
    the "Tải XML" button just Blob()s this same field). Returns the file path."""
    xml_dir = os.path.join(LOG_DIR, "xmls")
    os.makedirs(xml_dir, exist_ok=True)
    safe_label = run_label.replace("/", "-").replace(" ", "_").replace("(", "").replace(")", "")
    path = os.path.join(xml_dir, f"invoice_{tax_code}_{lookup_code}_{safe_label}.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml_content)
    return path


def run_once(run_label: str, tax_code: str, lookup_code: str) -> str:
    """Run one test case. Returns accepted / rejected / not_found / error.
    `accepted` only when the XML was actually extracted and saved to disk —
    a bypass that gets a 2xx with no usable XML is not a successful test."""
    logger.info("\n--- Run %s (tax_code=%s, lookup_code=%s) ---", run_label, tax_code, lookup_code)
    t0 = time.time()

    try:
        token = step1_solve_token(run_label)
    except Exception as exc:
        logger.error("Run %s: token acquisition FAILED: %s", run_label, exc)
        logger.debug("Traceback:\n%s", traceback.format_exc())
        return OUTCOME_ERROR

    if not VERIFY_ENDPOINT:
        logger.info("VERIFY_ENDPOINT not set — skipping backend check (got a token only).")
        return OUTCOME_ACCEPTED  # nothing left to check

    try:
        resp = step2_verify_with_backend(token, tax_code, lookup_code)
    except Exception as exc:
        logger.error("Run %s: backend request FAILED: %s", run_label, exc)
        logger.debug("Traceback:\n%s", traceback.format_exc())
        return OUTCOME_ERROR

    elapsed = round(time.time() - t0, 2)
    lowered = (resp.text or "").lower()
    matched_keyword = next((kw for kw in NOT_FOUND_KEYWORDS if kw.lower() in lowered), None)

    if matched_keyword:
        logger.warning("Run %s RESULT: NOT FOUND — matched \"%s\" (%ss). Final, not retried.",
                       run_label, matched_keyword, elapsed)
        return OUTCOME_NOT_FOUND

    if not (200 <= resp.status_code < 300):
        logger.info("Run %s RESULT: REJECTED (HTTP %s, %ss). Final — not retried.",
                    run_label, resp.status_code, elapsed)
        return OUTCOME_REJECTED

    try:
        xml_content = resp.json().get("xml")
    except ValueError:
        xml_content = None

    if not xml_content:
        logger.warning("Run %s RESULT: REJECTED — HTTP 2xx but no 'xml' field in the response "
                       "(%ss). Bypass alone isn't a successful test.", run_label, elapsed)
        return OUTCOME_REJECTED

    xml_path = save_xml(run_label, tax_code, lookup_code, xml_content)
    mark_downloaded(tax_code, lookup_code)
    logger.info("Run %s RESULT: ACCEPTED — XML saved to %s (%ss). Final — not retried.",
                run_label, xml_path, elapsed)
    return OUTCOME_ACCEPTED


def run_with_retries(run_label: str, tax_code: str, lookup_code: str) -> str:
    """Retry a case up to MAX_ATTEMPTS times, but only on a technical error."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempt_label = f"{run_label} (attempt {attempt}/{MAX_ATTEMPTS})"
        outcome = run_once(attempt_label, tax_code, lookup_code)

        if outcome != OUTCOME_ERROR:
            return outcome

        if attempt < MAX_ATTEMPTS:
            logger.warning(
                "Run %s: technical error — retrying (attempt %d/%d)...",
                run_label, attempt + 1, MAX_ATTEMPTS,
            )
        else:
            logger.error(
                "Run %s: all %d attempt(s) failed with technical errors. "
                "See the log file for details.", run_label, MAX_ATTEMPTS,
            )

    return OUTCOME_ERROR


def main():
    logger.debug("Log file for this run: %s", LOG_FILE)
    log_environment()

    if not SITEURL or not SITEKEY:
        logger.error("Set TARGET_SITEURL and TARGET_SITEKEY in .env first (see .env.example).")
        sys.exit(1)
    if not TEST_CASES:
        logger.error(
            "No test cases loaded from %s — create it (or pass --cases <path>) "
            'as a JSON list of {"tax_code": "...", "lookup_code": "..."} objects, one per run.',
            TEST_CASES_FILE,
        )
        sys.exit(1)

    require_own_site_ack()

    total = len(TEST_CASES)
    jobs = []  # (index, tax_code, lookup_code) for well-formed, not-yet-downloaded cases
    results = [None] * total
    for i, case in enumerate(TEST_CASES, start=1):
        try:
            tax_code, lookup_code = case["tax_code"], case["lookup_code"]
        except (KeyError, TypeError):
            logger.error(
                "TEST_CASES entry %d is malformed (expected keys 'tax_code' "
                "and 'lookup_code'): %r", i, case
            )
            results[i - 1] = OUTCOME_ERROR
            continue
        if already_downloaded(tax_code, lookup_code):
            logger.info(
                "Run %d/%d: SKIPPED — XML already in logs/xmls/ for tax_code=%s, lookup_code=%s.",
                i, total, tax_code, lookup_code,
            )
            results[i - 1] = OUTCOME_SKIPPED
            continue
        jobs.append((i, tax_code, lookup_code))

    if MAX_WORKERS > 1:
        logger.info("Running %d test case(s) with up to %d concurrent browsers...", len(jobs), MAX_WORKERS)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(run_with_retries, f"{i}/{total}", tax_code, lookup_code): i
                for i, tax_code, lookup_code in jobs
            }
            for future in as_completed(futures):
                i = futures[future]
                results[i - 1] = future.result()
    else:
        for i, tax_code, lookup_code in jobs:
            results[i - 1] = run_with_retries(f"{i}/{total}", tax_code, lookup_code)

    accepted = results.count(OUTCOME_ACCEPTED)
    rejected = results.count(OUTCOME_REJECTED)
    not_found = results.count(OUTCOME_NOT_FOUND)
    errored = results.count(OUTCOME_ERROR)
    skipped = results.count(OUTCOME_SKIPPED)

    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info(
        "%d/%d accepted | %d/%d rejected | %d/%d not found | %d/%d technical error | %d/%d skipped (already downloaded)",
        accepted, total, rejected, total, not_found, total, errored, total, skipped, total,
    )
    if accepted:
        logger.info("\n-> Some tokens obtained this way WERE accepted by your backend.")
        logger.info("   Treat Turnstile as one layer only. Add server-side defenses:")
        logger.info("   rate limiting, anomaly detection on request patterns, and")
        logger.info("   re-verifying the token with Cloudflare's siteverify API per request.")
    if rejected:
        logger.info("\n-> Some requests were rejected by your backend. Good — investigate why")
        logger.info("   (token replay check, extra required fields, session binding, etc.)")
        logger.info("   so you understand which control is actually doing the work.")
    if errored:
        logger.info(
            "\nSome cases hit technical errors even after retries — see the full "
            "log for tracebacks:\n    %s", LOG_FILE
        )


if __name__ == "__main__":
    main()
