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
import json
import logging
import os
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

import requests  # noqa: E402

SITEURL = os.environ.get("TARGET_SITEURL", "").strip()
SITEKEY = os.environ.get("TARGET_SITEKEY", "").strip()
VERIFY_ENDPOINT = os.environ.get("VERIFY_ENDPOINT", "").strip()
VERIFY_METHOD = os.environ.get("VERIFY_METHOD", "POST").strip().upper()
VERIFY_TOKEN_FIELD = os.environ.get("VERIFY_TOKEN_FIELD", "cf-turnstile-response").strip()
VERIFY_EXTRA_FIELDS = json.loads(os.environ.get("VERIFY_EXTRA_FIELDS", "{}") or "{}")
TEST_CASES = json.loads(os.environ.get("TEST_CASES", "[]") or "[]")
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))  # retries on technical failure only

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

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
RUN_STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
LOG_FILE = os.path.join(LOG_DIR, f"run_test_{RUN_STAMP}.log")

os.makedirs(LOG_DIR, exist_ok=True)

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
    logger.debug("TEST_CASES count=%s", len(TEST_CASES))
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


def step1_solve_token(run_label: str):
    from solver import solve  # EzSolver/solver.py

    logger.info("[1/2] Requesting a Turnstile token for sitekey=%r ...", SITEKEY)
    t0 = time.time()
    token = solve(SITEKEY, SITEURL, timeout=60)
    elapsed = round(time.time() - t0, 2)
    logger.info("      -> solver obtained a token in %ss", elapsed)
    logger.debug("Run %s: token (truncated): %s...", run_label, token[:24])
    return token


def step2_verify_with_backend(token: str, tax_code: str, lookup_code: str):
    logger.info("[2/2] Submitting token to your backend: %s %s", VERIFY_METHOD, VERIFY_ENDPOINT)
    payload = {
        VERIFY_TOKEN_FIELD: token,
        "tax_code": tax_code,
        "lookup_code": lookup_code,
        **VERIFY_EXTRA_FIELDS,
    }

    if VERIFY_METHOD == "GET":
        resp = requests.get(VERIFY_ENDPOINT, params=payload, timeout=30)
    else:
        resp = requests.post(VERIFY_ENDPOINT, data=payload, timeout=30)

    logger.info("      -> HTTP %s", resp.status_code)
    snippet = resp.text[:300].replace("\n", " ")
    logger.info("      -> response (truncated): %s", snippet)
    return resp


def run_once(run_label: str, tax_code: str, lookup_code: str) -> str:
    """Run one test case. Returns accepted / rejected / not_found / error."""
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

    accepted = 200 <= resp.status_code < 300
    outcome = OUTCOME_ACCEPTED if accepted else OUTCOME_REJECTED
    logger.info(
        "Run %s RESULT: %s (HTTP %s, %ss). Final — not retried.",
        run_label, outcome.upper(), resp.status_code, elapsed,
    )
    return outcome


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
            "Set TEST_CASES in .env first (see .env.example) — a JSON list of "
            '{"tax_code": "...", "lookup_code": "..."} objects, one per run.'
        )
        sys.exit(1)

    require_own_site_ack()

    results = []
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
            results.append(OUTCOME_ERROR)
            continue

        outcome = run_with_retries(f"{i}/{total}", tax_code, lookup_code)
        results.append(outcome)

    accepted = results.count(OUTCOME_ACCEPTED)
    rejected = results.count(OUTCOME_REJECTED)
    not_found = results.count(OUTCOME_NOT_FOUND)
    errored = results.count(OUTCOME_ERROR)

    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info(
        "%d/%d accepted | %d/%d rejected | %d/%d not found | %d/%d technical error",
        accepted, total, rejected, total, not_found, total, errored, total,
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
