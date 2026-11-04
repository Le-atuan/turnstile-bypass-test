#!/usr/bin/env python3
"""
Turnstile defensive test harness
---------------------------------
Purpose: as the operator of TARGET_SITEURL, measure whether an
off-the-shelf browser-automation solver (EzSolver) can obtain a Turnstile
token for your own page, and whether your backend's verification logic
actually accepts that token.

This is NOT a generic bypass tool against third-party sites. Point it only
at infrastructure you own or are explicitly authorized to test. Running it
against a site you don't control or lack permission for is out of scope.

Usage:
    cp .env.example .env   # fill in your real values
    pip install -r requirements.txt
    python run_test.py

Requires: Python 3.8+, real Chrome installed, (Linux headless: Xvfb),
this directory's EzSolver/ checkout.
"""
import json
import os
import sys
import time

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


def require_own_site_ack():
    print("=" * 70)
    print("This harness will drive a real browser against:")
    print(f"    {SITEURL}")
    print("Only run this against a site you own or are authorized to test.")
    print("=" * 70)
    ans = input("Confirm you are authorized to test this target [y/N]: ").strip().lower()
    if ans != "y":
        print("Aborted.")
        sys.exit(1)


def step1_solve_token():
    from solver import solve  # EzSolver/solver.py

    print(f"\n[1/2] Requesting a Turnstile token for sitekey={SITEKEY!r} ...")
    t0 = time.time()
    token = solve(SITEKEY, SITEURL, timeout=60)
    elapsed = round(time.time() - t0, 2)
    print(f"      -> solver obtained a token in {elapsed}s")
    print(f"      -> token (truncated): {token[:24]}...")
    return token


def step2_verify_with_backend(token: str):
    if not VERIFY_ENDPOINT:
        print("\n[2/2] VERIFY_ENDPOINT not set — skipping backend acceptance check.")
        print("      (Token acquisition alone does not mean your server accepts it.)")
        return None

    print(f"\n[2/2] Submitting token to your backend: {VERIFY_METHOD} {VERIFY_ENDPOINT}")
    payload = {VERIFY_TOKEN_FIELD: token, **VERIFY_EXTRA_FIELDS}

    if VERIFY_METHOD == "GET":
        resp = requests.get(VERIFY_ENDPOINT, params=payload, timeout=30)
    else:
        resp = requests.post(VERIFY_ENDPOINT, data=payload, timeout=30)

    print(f"      -> HTTP {resp.status_code}")
    snippet = resp.text[:300].replace("\n", " ")
    print(f"      -> response (truncated): {snippet}")
    return resp


def main():
    if not SITEURL or not SITEKEY:
        print("Set TARGET_SITEURL and TARGET_SITEKEY in .env first (see .env.example).")
        sys.exit(1)

    require_own_site_ack()

    try:
        token = step1_solve_token()
    except Exception as exc:
        print(f"\n[RESULT] Token acquisition FAILED: {exc}")
        print("This is a GOOD outcome defensively: EzSolver could not get a token")
        print("for your widget configuration (e.g. Managed mode is holding, or the")
        print("widget/challenge is rejecting the automated browser).")
        sys.exit(0)

    resp = step2_verify_with_backend(token)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("Token acquisition: SUCCEEDED (solver got a browser-signed token)")
    if resp is not None:
        accepted = 200 <= resp.status_code < 300
        print(f"Backend acceptance: {'ACCEPTED' if accepted else 'REJECTED'} (HTTP {resp.status_code})")
        if accepted:
            print("\n-> Your server-side logic currently accepts tokens obtained this way.")
            print("   Treat Turnstile as one layer only. Add server-side defenses:")
            print("   rate limiting, anomaly detection on request patterns, and")
            print("   re-verifying the token with Cloudflare's siteverify API per request.")
        else:
            print("\n-> Your backend rejected the token/request. Good — investigate why")
            print("   (token replay check, extra required fields, session binding, etc.)")
            print("   so you understand which control is actually doing the work.")
    else:
        print("Backend acceptance: NOT TESTED (set VERIFY_ENDPOINT to test this)")


if __name__ == "__main__":
    main()
