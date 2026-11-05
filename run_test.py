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
VERIFY_TIMEOUT = int(os.environ.get("VERIFY_TIMEOUT", "30"))  # backend response wait (xml lookup can be slow)

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

# Phrases meaning "no record found" — only trusted on a 2xx JSON response,
# so a block page containing similar text is NOT mislabeled as not_found.
NOT_FOUND_KEYWORDS = [
    kw.strip() for kw in os.environ.get(
        "NOT_FOUND_KEYWORDS",
        "không tìm thấy,không có dữ liệu,không tồn tại,not found,no data,no record",
    ).split(",")
    if kw.strip()
]

# HTTP statuses that mean "rate limited".
RATE_LIMIT_STATUSES = {
    int(s) for s in os.environ.get("RATE_LIMIT_STATUSES", "429").replace(" ", "").split(",") if s
}

# Body/markers that mean "bot challenge / WAF block" (Cloudflare etc.).
CHALLENGE_MARKERS = [
    m.strip().lower() for m in os.environ.get(
        "CHALLENGE_MARKERS",
        "just a moment,attention required,challenge-platform,__cf_chl,cf-chl,"
        "captcha,access denied,request blocked,error 1020,error 1015",
    ).split(",")
    if m.strip()
]

# Only "error" and "server_error" are retried; the rest are final.
OUTCOME_ACCEPTED = "accepted"          # 2xx and the XML was present
OUTCOME_NOT_FOUND = "not_found"        # 2xx, no xml, a genuine "no record" message
OUTCOME_RATE_LIMITED = "rate_limited"  # HTTP 429 / Retry-After — throttled
OUTCOME_BLOCKED = "blocked"            # bot challenge / WAF block (Cloudflare)
OUTCOME_SERVER_ERROR = "server_error"  # 5xx — transient, retried
OUTCOME_REJECTED = "rejected"          # other non-2xx, or 2xx with no xml and no clear reason
OUTCOME_ERROR = "error"                # token acquisition or the HTTP call itself failed
OUTCOME_SKIPPED = "skipped"            # XML already downloaded in a previous run

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

# Set when a system sleep/suspend (or long freeze) is detected, so the run stops.
SUSPENDED = threading.Event()


def _exc_chain_text(exc: BaseException) -> str:
    """Flatten an exception and its causes into one line (root causes are the useful part)."""
    parts, e, seen = [], exc, 0
    while e is not None and seen < 8:
        parts.append(f"{type(e).__name__}: {e}")
        e = e.__cause__ or e.__context__
        seen += 1
    return " <- ".join(parts)


def diagnose_exception(exc: BaseException) -> tuple:
    """Map an exception to (category, human reason) so the log says WHY, not just what."""
    text = _exc_chain_text(exc).lower()
    name = type(exc).__name__
    if SUSPENDED.is_set():
        return "MACHINE_SLEPT", "máy vừa sleep/suspend giữa chừng — không phải lỗi site"
    if any(s in text for s in (
        "invalid session id", "session deleted", "page crash", "chrome not reachable",
        "disconnected", "no such window", "target window already closed",
        "cannot connect to chrome", "connection refused", "max retries exceeded",
        "failed to establish a new connection",
    )):
        return "BROWSER_DEAD", "trình duyệt chết/mất kết nối (crash, hết RAM, hoặc máy đã sleep)"
    if "read timed out" in text or "readtimeout" in text:
        return "BROWSER_FROZEN", "trình duyệt treo, không phản hồi (có thể bị challenge, máy sleep, hoặc quá tải)"
    if name == "TimeoutException" or "timeoutexception" in text:
        return "PAGE_TIMEOUT", "trang/script quá thời gian (có thể bị challenge hoặc mạng chậm)"
    if "turnstile" in text or "cloudflare" in text:
        return "TURNSTILE", "không giải được Turnstile (bị chặn, hoặc sai sitekey/domain)"
    if any(s in text for s in ("name or service not known", "temporary failure in name resolution",
                                "connectionerror", "connecttimeout", "ssl", "certificate")):
        return "NETWORK", "lỗi mạng khi gọi backend (DNS/kết nối/SSL)"
    if "chrome not found" in text or "filenotfounderror" in text:
        return "CHROME_MISSING", "không tìm thấy Chrome (set CHROME_PATH trong .env)"
    return "UNKNOWN", f"lỗi chưa phân loại ({name})"


def _mem_hint() -> str:
    """Best-effort free-memory snapshot, to spot OOM as a cause (Linux /proc, else macOS vm_stat)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable"):
                    return "MemAvailable=" + line.split(":", 1)[1].strip()
    except OSError:
        pass
    try:
        import subprocess
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3).stdout
        free = next((l for l in out.splitlines() if "free" in l.lower()), "")
        return "vm_stat: " + free.strip() if free else "mem: n/a"
    except Exception:
        return "mem: n/a"


def _log_exception(run_label: str, stage: str, exc: BaseException, t0: float) -> None:
    """Uniform, root-cause-first error log for a failed stage."""
    cat, why = diagnose_exception(exc)
    logger.error("Run %s: %s FAILED [%s] — %s", run_label, stage, cat, why)
    logger.error("  nguyên nhân gốc: %s", _exc_chain_text(exc))
    logger.error("  ngữ cảnh: elapsed=%.1fs | backend=%s | workers=%s | %s",
                 time.time() - t0, SOLVER_BACKEND, MAX_WORKERS, _mem_hint())
    logger.debug("Run %s traceback:\n%s", run_label, traceback.format_exc())


def start_sleep_watchdog(interval: float = 5.0, threshold: float = 60.0):
    """Detect a system sleep/suspend (or long freeze) via wall-vs-monotonic drift; set SUSPENDED."""
    def loop():
        wall, mono = time.time(), time.monotonic()
        while not SUSPENDED.wait(interval):
            w, m = time.time(), time.monotonic()
            gap = (w - wall) - (m - mono)  # ~seconds the machine was suspended/frozen
            if gap > threshold:
                logger.warning("WATCHDOG: phát hiện máy sleep/treo ~%.0fs — lỗi sau đó sẽ được gắn "
                               "nhãn MACHINE_SLEPT (không dừng chạy).", gap)
                SUSPENDED.set()
                return
            wall, mono = w, m
    threading.Thread(target=loop, daemon=True).start()


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
        resp = requests.get(VERIFY_ENDPOINT, params=payload, timeout=VERIFY_TIMEOUT)
    else:
        resp = requests.post(VERIFY_ENDPOINT, json=payload, timeout=VERIFY_TIMEOUT)

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


# Distinct, greppable label + log level per outcome (for the console/log file).
OUTCOME_LABELS = {
    OUTCOME_NOT_FOUND: ("NOT_FOUND (hóa đơn không tồn tại — kết quả thật)", logging.WARNING),
    OUTCOME_RATE_LIMITED: ("RATE_LIMITED (bị giới hạn tần suất)", logging.ERROR),
    OUTCOME_BLOCKED: ("BLOCKED (bị chặn / challenge bot)", logging.ERROR),
    OUTCOME_SERVER_ERROR: ("SERVER_ERROR (lỗi phía server)", logging.ERROR),
    OUTCOME_REJECTED: ("REJECTED (2xx nhưng không có xml / lý do không rõ)", logging.WARNING),
}


def classify_response(resp) -> tuple:
    """Classify a backend response. Returns (outcome, detail). Checks block/limit
    BEFORE the not-found phrase so a challenge page can't masquerade as not_found."""
    status = resp.status_code
    lowered = (resp.text or "").lower()
    ctype = resp.headers.get("Content-Type", "").lower()
    retry_after = resp.headers.get("Retry-After")
    cf_mitigated = resp.headers.get("cf-mitigated")

    # 1. Rate limit — explicit status or a Retry-After header.
    if status in RATE_LIMIT_STATUSES or retry_after:
        return OUTCOME_RATE_LIMITED, f"HTTP {status}, Retry-After={retry_after}"

    # 2. Bot challenge / WAF block — CF header, a known marker, or HTML on a 403/503.
    marker = next((m for m in CHALLENGE_MARKERS if m in lowered), None)
    if cf_mitigated or marker or (status in (403, 503) and "text/html" in ctype):
        why = (f"cf-mitigated={cf_mitigated}" if cf_mitigated
               else f"marker '{marker}'" if marker else f"HTTP {status} HTML body")
        return OUTCOME_BLOCKED, f"HTTP {status}, {why}"

    # 3. Genuine "no record" message — the site may send this on any status
    # (e.g. HTTP 500 instead of 200/404), so check it before treating 5xx as transient.
    if any(kw.lower() in lowered for kw in NOT_FOUND_KEYWORDS):
        return OUTCOME_NOT_FOUND, f"HTTP {status}, matched a not-found phrase"

    # 4. Server error — transient, retried.
    if 500 <= status < 600:
        return OUTCOME_SERVER_ERROR, f"HTTP {status}"

    # 5. Other non-2xx.
    if not (200 <= status < 300):
        return OUTCOME_REJECTED, f"HTTP {status}"

    # 6. 2xx — inspect the JSON envelope.
    try:
        data = resp.json()
    except ValueError:
        return OUTCOME_REJECTED, f"HTTP {status}, non-JSON body"
    if isinstance(data, dict) and data.get("xml"):
        return OUTCOME_ACCEPTED, None
    return OUTCOME_REJECTED, f"HTTP {status}, 2xx but no xml and no not-found marker"


def run_once(run_label: str, tax_code: str, lookup_code: str) -> str:
    """Runs one test case; `accepted` only when the XML was actually saved."""
    t0 = time.time()

    try:
        token = step1_solve_token(run_label)
    except Exception as exc:
        _log_exception(run_label, "token acquisition (solve Turnstile)", exc, t0)
        return OUTCOME_ERROR

    if not VERIFY_ENDPOINT:
        logger.info("VERIFY_ENDPOINT not set — skipping backend check (got a token only).")
        return OUTCOME_ACCEPTED  # nothing left to check

    try:
        resp = step2_verify_with_backend(token, tax_code, lookup_code)
    except Exception as exc:
        _log_exception(run_label, "backend request (POST verify)", exc, t0)
        return OUTCOME_ERROR

    elapsed = round(time.time() - t0, 2)
    outcome, detail = classify_response(resp)
    snippet = (resp.text or "")[:300].replace("\n", " ")

    if outcome == OUTCOME_ACCEPTED:
        xml_path = download_xml(run_label, tax_code, lookup_code, resp.json().get("xml"), DOWNLOAD_DIR)
        logger.info("Run %s RESULT: ACCEPTED — XML saved to %s (%ss).", run_label, xml_path, elapsed)
        return OUTCOME_ACCEPTED

    label, level = OUTCOME_LABELS.get(outcome, (outcome.upper(), logging.WARNING))
    retried = " — sẽ thử lại" if outcome == OUTCOME_SERVER_ERROR else " — kết quả cuối"
    logger.log(level, "Run %s RESULT: %s — %s (%ss)%s", run_label, label, detail, elapsed, retried)
    logger.debug("Run %s response: HTTP %s | headers=%s | body[:300]=%s",
                 run_label, resp.status_code, dict(resp.headers), snippet)
    return outcome


RETRYABLE = {OUTCOME_ERROR, OUTCOME_SERVER_ERROR}  # transient; rate_limited/blocked/not_found are final


def run_with_retries(run_label: str, tax_code: str, lookup_code: str) -> str:
    """Retry a case up to MAX_ATTEMPTS times, but only on a transient technical error."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempt_label = f"{run_label} (attempt {attempt}/{MAX_ATTEMPTS})"
        outcome = run_once(attempt_label, tax_code, lookup_code)

        if outcome not in RETRYABLE:
            return outcome

        if attempt < MAX_ATTEMPTS:
            logger.warning(
                "Run %s: transient error (%s) — retrying (attempt %d/%d)...",
                run_label, outcome, attempt + 1, MAX_ATTEMPTS,
            )
            time.sleep(3)  # let the previous Chrome/chromedriver fully release before relaunching
        else:
            logger.error(
                "Run %s: all %d attempt(s) failed (%s). See the log file for details.",
                run_label, MAX_ATTEMPTS, outcome,
            )

    return outcome  # last transient outcome (error or server_error)


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
    start_sleep_watchdog()

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
        for n, (i, tax_code, lookup_code) in enumerate(jobs):
            if n > 0:
                time.sleep(3)  # let the previous Chrome/chromedriver fully release before relaunching
            results[i - 1] = run_with_retries(f"{i}/{total}", tax_code, lookup_code)

    counts = {o: results.count(o) for o in (
        OUTCOME_ACCEPTED, OUTCOME_NOT_FOUND, OUTCOME_RATE_LIMITED, OUTCOME_BLOCKED,
        OUTCOME_SERVER_ERROR, OUTCOME_REJECTED, OUTCOME_ERROR, OUTCOME_SKIPPED,
    )}

    logger.info("SUMMARY (total %d):", total)
    logger.info("  accepted     = %d   (lấy được XML)", counts[OUTCOME_ACCEPTED])
    logger.info("  not_found    = %d   (hóa đơn không tồn tại — kết quả thật)", counts[OUTCOME_NOT_FOUND])
    logger.info("  rate_limited = %d   (bị giới hạn tần suất)", counts[OUTCOME_RATE_LIMITED])
    logger.info("  blocked      = %d   (bị chặn / challenge bot)", counts[OUTCOME_BLOCKED])
    logger.info("  server_error = %d   (lỗi phía server)", counts[OUTCOME_SERVER_ERROR])
    logger.info("  rejected     = %d   (2xx không xml / không rõ)", counts[OUTCOME_REJECTED])
    logger.info("  error        = %d   (lấy token / HTTP hỏng — xem [CATEGORY] ở log)", counts[OUTCOME_ERROR])
    logger.info("  skipped      = %d   (đã tải trước đó)", counts[OUTCOME_SKIPPED])

    if SUSPENDED.is_set():
        logger.info("-> Watchdog thấy MÁY SLEEP giữa chừng — các lỗi lúc đó đã gắn nhãn MACHINE_SLEPT "
                    "(không phải lỗi site). Chạy loạt dài nên dùng: systemd-inhibit --what=idle:sleep ...")
    if counts[OUTCOME_RATE_LIMITED] or counts[OUTCOME_BLOCKED]:
        logger.info("-> Có dấu hiệu phòng thủ tầng 2 (rate-limit / chặn bot). Xem body response "
                    "ở log DEBUG (%s) để xác nhận và lấy số liệu cho báo cáo.", LOG_FILE)
    if counts[OUTCOME_ERROR] or counts[OUTCOME_SERVER_ERROR]:
        logger.info("See %s for tracebacks / raw responses.", LOG_FILE)

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
