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

Only run this against infrastructure you own, with explicit authorization.
"""
import asyncio
import json
import os
import random
import sys
import time

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


def require_own_site_ack():
    print("=" * 70)
    print(f"This will drive a real browser to fill the REAL form on:\n    {SITEURL}")
    print(f"It will run {len(TEST_CASES)} time(s), one distinct real "
          "tax_code/lookup_code pair per run.")
    print("Only run this against a site you own or are authorized to test.")
    print("=" * 70)
    ans = input("Confirm you are authorized to test this target [y/N]: ").strip().lower()
    if ans != "y":
        print("Aborted.")
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
    while time.time() < deadline:
        val = await page.evaluate(
            "document.querySelector('[name=\"cf-turnstile-response\"]')?.value || ''"
        )
        if val:
            return True
        await asyncio.sleep(0.5)
    return False


async def run_once(run_label: str, tax_code: str, lookup_code: str):
    print(f"\n--- Run {run_label} (tax_code={tax_code}, lookup_code={lookup_code}) ---")
    browser = await start_browser()
    try:
        page = await browser.get(SITEURL)

        print("Waiting for the page's own Turnstile widget to resolve in the background...")
        if not await wait_for_token(page, timeout=TOKEN_WAIT_TIMEOUT):
            print(f"RESULT: no token appeared within {TOKEN_WAIT_TIMEOUT}s — "
                  "the widget itself may be flagging this browser session.")
            return

        print("Token ready. Filling the real form like a human would...")
        tax_input = await page.select('input[placeholder="MST bên bán"]')
        await tax_input.click()
        await human_type(tax_input, tax_code)
        await asyncio.sleep(random.uniform(0.3, 0.9))

        lookup_input = await page.select('input[placeholder="Mã tra cứu hóa đơn"]')
        await lookup_input.click()
        await human_type(lookup_input, lookup_code)
        await asyncio.sleep(random.uniform(0.3, 0.9))

        submit_btn = await page.find("Tra cứu", best_match=True)
        await submit_btn.click()

        print("Submitted. Waiting for the page to respond...")
        await asyncio.sleep(3)

        body_text = await page.evaluate("document.body.innerText.slice(0, 600)")
        print("Page content after submit (first 600 chars):")
        print(body_text)
    finally:
        browser.stop()


async def main():
    if not TEST_CASES:
        print("Set TEST_CASES in .env first (see .env.example) — a JSON list of "
              '{"tax_code": "...", "lookup_code": "..."} objects, one per run.')
        sys.exit(1)

    require_own_site_ack()

    xvfb = _start_xvfb_if_needed()
    try:
        total = len(TEST_CASES)
        for i, case in enumerate(TEST_CASES, start=1):
            await run_once(f"{i}/{total}", case["tax_code"], case["lookup_code"])
            if i < total:
                await asyncio.sleep(random.uniform(2, 5))
    finally:
        if xvfb:
            xvfb.terminate()


if __name__ == "__main__":
    asyncio.run(main())
