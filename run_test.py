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

# JSON list of result_json-shaped {"info": {"tax_code", "lookup_code"}} objects, not read from .env.
DEFAULT_TEST_CASES_FILE = "test_cases.json"


def load_test_cases_file(path: str) -> list:
    """Loads a JSON list of result_json-shaped test cases from disk; [] if the file doesn't exist."""
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

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
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "").strip() or os.path.join(LOG_DIR, "xmls")
RUN_STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
LOG_FILE = os.path.join(LOG_DIR, f"run_test_{RUN_STAMP}.log")

os.makedirs(LOG_DIR, exist_ok=True)

# Survives clear_xmls.sh deleting the XML files themselves.
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


def require_own_site_ack(total_cases: int):
    logger.info(
        "Target: %s (%s test case(s), MAX_ATTEMPTS=%s). Authorized to test — proceeding.",
        SITEURL, total_cases, MAX_ATTEMPTS,
    )


def _load_solve_fn():
    if SOLVER_BACKEND == "uc":
        from solver_uc import solve
    else:
        from solver import solve  # EzSolver/solver.py (nodriver)
    return solve


def step1_solve_token(run_label: str):
    solve = _load_solve_fn()

    t0 = time.time()
    token = solve(SITEKEY, SITEURL, timeout=60)
    logger.debug("Run %s: token obtained in %ss", run_label, round(time.time() - t0, 2))
    return token


def step2_verify_with_backend(token: str, tax_code: str, lookup_code: str):
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

    logger.debug("      -> HTTP %s: %s", resp.status_code, resp.text[:300].replace("\n", " "))
    return resp


def already_downloaded(tax_code: str, lookup_code: str) -> bool:
    """True if this pair is recorded in logs/downloaded.json."""
    return _pair_key(tax_code, lookup_code) in load_downloaded()


def save_xml(run_label: str, tax_code: str, lookup_code: str, xml_content: str, out_dir: str = None) -> str:
    """Writes the response's `xml` field to disk and returns the file path."""
    xml_dir = out_dir or DOWNLOAD_DIR
    os.makedirs(xml_dir, exist_ok=True)
    safe_label = run_label.replace("/", "-").replace(" ", "_").replace("(", "").replace(")", "")
    path = os.path.join(xml_dir, f"invoice_{tax_code}_{lookup_code}_{safe_label}.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml_content)
    return path


def extract_pair(result_json: dict) -> tuple:
    """Pulls (tax_code, lookup_code) out of a result_json["info"]; raises KeyError/TypeError if malformed."""
    return result_json["info"]["tax_code"], result_json["info"]["lookup_code"]


def download_xml(run_label: str, tax_code: str, lookup_code: str, xml_content: str, download_dir: str = None) -> str:
    """Saves the XML and records the pair as downloaded; shared final step for get_file and run_once."""
    path = save_xml(run_label, tax_code, lookup_code, xml_content, out_dir=download_dir)
    mark_downloaded(tax_code, lookup_code)
    return path


def get_file(result_json: dict, download_dir: str = None) -> dict:
    """Drop-in replacement for GetFolderTraCuuEinvoice365.get_file; file_pdf is always None."""
    dict_result = {"file_pdf": None, "file_xml": None}
    try:
        tax_code, lookup_code = extract_pair(result_json)
    except (KeyError, TypeError):
        return dict_result

    run_label = f"{tax_code}/{lookup_code}"
    try:
        token = step1_solve_token(run_label)
        resp = step2_verify_with_backend(token, tax_code, lookup_code)
        xml_content = resp.json().get("xml") if 200 <= resp.status_code < 300 else None
    except Exception:
        return dict_result

    if xml_content:
        dict_result["file_xml"] = download_xml(run_label, tax_code, lookup_code, xml_content, download_dir)

    return dict_result


def run_once(run_label: str, tax_code: str, lookup_code: str) -> str:
    """Runs one test case; `accepted` only when the XML was actually saved."""
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

    xml_path = download_xml(run_label, tax_code, lookup_code, xml_content, DOWNLOAD_DIR)
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


def run_test_cases(test_cases: list) -> list:
    """Runs every result_json-shaped entry in test_cases and returns the list of outcomes.

    The entry point for integrating this harness into another project: build
    `test_cases` however you like (e.g. from your own API call) as a list of
    {"info": {"tax_code": "...", "lookup_code": "..."}} dicts, then call this
    directly — no CLI args, no test_cases.json file needed.
    """
    logger.debug("Log file for this run: %s", LOG_FILE)

    if not SITEURL or not SITEKEY:
        logger.error("Set TARGET_SITEURL and TARGET_SITEKEY in .env first (see .env.example).")
        return []
    if not test_cases:
        logger.error("No test cases given.")
        return []

    require_own_site_ack(len(test_cases))

    total = len(test_cases)
    jobs = []  # (index, tax_code, lookup_code) for well-formed, not-yet-downloaded cases
    results = [None] * total
    for i, case in enumerate(test_cases, start=1):
        try:
            tax_code, lookup_code = extract_pair(case)
        except (KeyError, TypeError):
            logger.error(
                "TEST_CASES entry %d is malformed (expected result_json shape "
                '{"info": {"tax_code", "lookup_code"}}): %r', i, case
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

    logger.info(
        "SUMMARY: %d/%d accepted | %d/%d rejected | %d/%d not found | %d/%d error | %d/%d skipped",
        accepted, total, rejected, total, not_found, total, errored, total, skipped, total,
    )
    if errored:
        logger.info("See %s for tracebacks on the %d error(s).", LOG_FILE, errored)

    return results


def main():
    """CLI entry point: --cases <path> file if given/present, else TEST_CASES from .env."""
    cli = argparse.ArgumentParser()
    cli.add_argument("--cases", default=DEFAULT_TEST_CASES_FILE,
                      help=f"Path to the JSON file of test cases (default: {DEFAULT_TEST_CASES_FILE}, "
                           "else falls back to TEST_CASES in .env)")
    cases_file = cli.parse_args().cases

    test_cases = load_test_cases_file(cases_file)
    if not test_cases:
        test_cases = json.loads(os.environ.get("TEST_CASES", "[]") or "[]")
    if not test_cases:
        logger.error(
            "No test cases found — create %s, pass --cases <path>, or set TEST_CASES in .env, "
            'as a JSON list of {"info": {"tax_code": "...", "lookup_code": "..."}} objects.',
            cases_file,
        )
        sys.exit(1)

    run_test_cases(test_cases)


if __name__ == "__main__":
    main()
