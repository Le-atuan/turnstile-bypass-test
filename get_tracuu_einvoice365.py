#!/usr/bin/env python3
"""
Single-invoice fetch for tracuu.einvoice365.com (ezsolver-v2 branch).

Integration entry point. Takes ONE result_json object (not a list) and
returns one file, as a drop-in for the DrissionPage-based
GetFolderTraCuuEinvoice365.get_file. Two ways to call it:

    # 1) True drop-in — only the import line changes at your call sites:
    from get_tracuu_einvoice365 import GetFolderTraCuuEinvoice365
    result = GetFolderTraCuuEinvoice365().get_file(result_json, download_dir)

    # 2) Plain function:
    from get_tracuu_einvoice365 import get_file
    result = get_file(result_json, download_dir)
    # -> {"file_pdf": None, "file_xml": "<download_dir>/invoice_...xml"}  (file_xml=None on miss)

The XML is written into the SAME `download_dir` you pass in — identical to
the old class — so downstream that reads from that folder is unaffected.
This implementation fetches the XML over HTTP (via the uc Turnstile
backend) instead of driving the UI, so it never produces a PDF: file_pdf
is always None.

Run standalone to fetch one file (see --help). For the many-cases test
harness, use run_test.py instead.

Only run against infrastructure you own or are authorized to test.
"""
import argparse
import json
import logging
import os
import sys
import time
import traceback

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
VERIFY_TOKEN_FIELD = os.environ.get("VERIFY_TOKEN_FIELD", "token").strip()
VERIFY_EXTRA_FIELDS = json.loads(os.environ.get("VERIFY_EXTRA_FIELDS", "{}") or "{}")
SOLVER_BACKEND = os.environ.get("SOLVER_BACKEND", "uc").strip()  # uc | nodriver
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))          # retries on technical error only
SOLVE_TIMEOUT = int(os.environ.get("TOKEN_WAIT_TIMEOUT", "60"))

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "").strip() or os.path.join(LOG_DIR, "xmls")

# Library logger: no handlers added at import time, so importing this module
# into another project has no side effects. main() configures console output.
logger = logging.getLogger("get_tracuu_einvoice365")


def _load_solve_fn():
    """Return EzSolver's solve() for the configured backend (uc gets past this site)."""
    if SOLVER_BACKEND == "uc":
        from solver_uc import solve
    else:
        from solver import solve  # EzSolver/solver.py (nodriver — blocked on this site)
    return solve


def extract_pair(result_json: dict) -> tuple:
    """Pull (tax_code, lookup_code) out of result_json["info"]; raises KeyError/TypeError if malformed."""
    return result_json["info"]["tax_code"], result_json["info"]["lookup_code"]


def solve_token() -> str:
    """Open a browser via the configured backend and return a Turnstile token."""
    solve = _load_solve_fn()
    t0 = time.time()
    token = solve(SITEKEY, SITEURL, timeout=SOLVE_TIMEOUT)
    logger.debug("Token obtained in %ss via %s backend", round(time.time() - t0, 2), SOLVER_BACKEND)
    return token


def verify_with_backend(token: str, tax_code: str, lookup_code: str):
    """Replay the token + lookup pair to the backend endpoint; return the response."""
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
    logger.debug("HTTP %s: %s", resp.status_code, resp.text[:300].replace("\n", " "))
    return resp


def save_xml(tax_code: str, lookup_code: str, xml_content: str, download_dir: str = None) -> str:
    """Write the response's xml to disk and return the absolute file path."""
    xml_dir = download_dir or DOWNLOAD_DIR
    os.makedirs(xml_dir, exist_ok=True)
    path = os.path.join(xml_dir, f"invoice_{tax_code}_{lookup_code}.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml_content)
    return os.path.abspath(path)


def _fetch_once(tax_code: str, lookup_code: str, download_dir: str = None) -> str:
    """One solve+verify+save attempt. Returns the XML path, or raises on a technical error."""
    token = solve_token()
    resp = verify_with_backend(token, tax_code, lookup_code)
    if not (200 <= resp.status_code < 300):
        logger.warning("Backend rejected the request (HTTP %s) for %s/%s.",
                       resp.status_code, tax_code, lookup_code)
        return None
    try:
        xml_content = resp.json().get("xml")
    except ValueError:
        xml_content = None
    if not xml_content:
        logger.warning("HTTP 2xx but no 'xml' field for %s/%s — nothing saved.", tax_code, lookup_code)
        return None
    return save_xml(tax_code, lookup_code, xml_content, download_dir)


def get_file(result_json: dict, download_dir: str = None) -> dict:
    """Fetch one invoice. Drop-in for GetFolderTraCuuEinvoice365.get_file; file_pdf is always None.

    Retries only on a technical error (token/HTTP failure), up to MAX_ATTEMPTS.
    A clean "no file" answer (rejected / no xml) is final and returns file_xml=None.
    """
    dict_result = {"file_pdf": None, "file_xml": None}
    try:
        tax_code, lookup_code = extract_pair(result_json)
    except (KeyError, TypeError):
        logger.error('Malformed result_json (expected {"info": {"tax_code", "lookup_code"}}): %r', result_json)
        return dict_result

    if not SITEURL or not SITEKEY or not VERIFY_ENDPOINT:
        logger.error("Set TARGET_SITEURL, TARGET_SITEKEY and VERIFY_ENDPOINT in .env first.")
        return dict_result

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            dict_result["file_xml"] = _fetch_once(tax_code, lookup_code, download_dir)
            return dict_result  # reached the backend — final, whatever the answer
        except Exception as exc:
            logger.error("Attempt %d/%d FAILED for %s/%s: %s",
                         attempt, MAX_ATTEMPTS, tax_code, lookup_code, exc)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            if attempt < MAX_ATTEMPTS:
                logger.warning("Technical error — retrying (%d/%d)...", attempt + 1, MAX_ATTEMPTS)

    return dict_result


class GetFolderTraCuuEinvoice365:
    """Drop-in replacement for the DrissionPage-based class of the same name.

    Same call site: `GetFolderTraCuuEinvoice365().get_file(result_json, download_dir)`.
    Swap only the import to migrate. The XML lands in the `download_dir` you
    pass (same as the old class); file_pdf is always None (XML-only over HTTP).
    """

    def get_file(self, result_json: dict, download_dir: str = None) -> dict:
        return get_file(result_json, download_dir=download_dir)


def _load_single_case(args) -> dict:
    """Read ONE result_json object from --case '<json>', --file <path>, or stdin."""
    if args.case:
        return json.loads(args.case)
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            return json.load(f)
    if not sys.stdin.isatty():
        return json.loads(sys.stdin.read())
    raise SystemExit("No input. Pass --case '<json>', --file <path>, or pipe JSON via stdin.")


def main():
    cli = argparse.ArgumentParser(description="Fetch one invoice from tracuu.einvoice365.com.")
    cli.add_argument("--case", help='One result_json object as a JSON string, '
                                     'e.g. \'{"info": {"tax_code": "...", "lookup_code": "..."}}\'')
    cli.add_argument("--file", help="Path to a JSON file holding ONE result_json object")
    cli.add_argument("--out", help="Directory to save the XML into (default: logs/xmls/)")
    args = cli.parse_args()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    try:
        result_json = _load_single_case(args)
    except (ValueError, OSError) as exc:
        raise SystemExit(f"Could not read the input object: {exc}")
    if not isinstance(result_json, dict):
        raise SystemExit('Input must be a single object, not a list. '
                         'Use run_test.py for many cases.')

    result = get_file(result_json, download_dir=args.out)
    if result["file_xml"]:
        logger.info("OK — XML saved to %s", result["file_xml"])
    else:
        logger.info("No file retrieved (see the messages above).")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
