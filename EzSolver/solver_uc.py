"""
Turnstile solve attempt using undetected-chromedriver (patched ChromeDriver
binary, keeps the Selenium WebDriver protocol) instead of nodriver's raw CDP.
Same render+click strategy as solver.py so results are comparable.
"""
import asyncio
import json
import random
import time
from typing import Optional

import undetected_chromedriver as uc


def _installed_chrome_major() -> Optional[int]:
    """undetected-chromedriver auto-downloads a matching driver, but only if
    told the installed Chrome's major version — otherwise it may grab a
    driver newer than the local browser and fail to connect."""
    import re
    import subprocess

    paths = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
    ]
    for path in paths:
        try:
            out = subprocess.check_output([path, "--version"], text=True)
            m = re.search(r"(\d+)\.", out)
            if m:
                return int(m.group(1))
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _inject_widget(driver, sitekey: str):
    driver.execute_script(f"""
        window._tsToken = null;
        window._tsError = null;
        const wrap = document.createElement('div');
        wrap.id = '_ts_box';
        wrap.style = 'position:fixed;top:20px;left:20px;z-index:2147483647;';
        document.body.appendChild(wrap);
        window._tsLoad = function () {{
            turnstile.render('#_ts_box', {{
                sitekey: '{sitekey}',
                callback: function(token) {{ window._tsToken = token; }},
                'error-callback': function(code) {{ window._tsError = code; }}
            }});
        }};
        const s = document.createElement('script');
        s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?onload=_tsLoad&render=explicit';
        s.async = true;
        document.head.appendChild(s);
    """)


def _get_state(driver) -> dict:
    raw = driver.execute_script("""
        return JSON.stringify((() => {
            if (window._tsError) return {error: window._tsError};
            if (window._tsToken) return {token: window._tsToken};
            return {};
        })());
    """)
    return json.loads(raw) if raw else {}


def _get_iframe_rect(driver) -> Optional[dict]:
    raw = driver.execute_script("""
        return JSON.stringify((() => {
            for (const el of document.querySelectorAll('iframe')) {
                const src = el.src || '';
                if (src.includes('challenges.cloudflare.com')) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 50 && r.height > 20) return {x: r.x, y: r.y, w: r.width, h: r.height};
                }
            }
            return null;
        })());
    """)
    return json.loads(raw) if raw and raw != "null" else None


def solve(sitekey: str, siteurl: str, timeout: int = 60) -> str:
    """Synchronous — undetected-chromedriver uses classic Selenium, no asyncio."""
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = uc.Chrome(options=options, headless=False, version_main=_installed_chrome_major())
    try:
        driver.get(siteurl)
        _inject_widget(driver, sitekey)

        start = time.time()
        rect = None
        for i in range(10):
            time.sleep(0.5)
            st = _get_state(driver)
            if st.get("error"):
                raise RuntimeError(f"Cloudflare Turnstile error {st['error']}")
            if st.get("token"):
                return st["token"]
            rect = _get_iframe_rect(driver)
            if rect:
                print(f"[solver_uc] widget iframe found at {rect}")
                break
        if not rect:
            print("[solver_uc] WARNING: no Cloudflare iframe found after 5s initial wait")

        from selenium.webdriver.common.action_chains import ActionChains

        deadline = start + timeout
        click_count = 0
        last_click = 0.0
        last_status = 0.0
        while time.time() < deadline:
            st = _get_state(driver)
            if st.get("error"):
                raise RuntimeError(f"Cloudflare Turnstile error {st['error']}")
            if st.get("token"):
                return st["token"]

            now = time.time()
            if now - last_status > 5:
                print(f"[solver_uc] still waiting ({now - start:.1f}s elapsed, {click_count} click(s), rect={'found' if rect else 'none'})")
                last_status = now

            if click_count < 4 and (click_count == 0 or now - last_click > 6):
                rect = _get_iframe_rect(driver) or rect
                cx = (rect["x"] + 28) if rect else 48
                cy = (rect["y"] + rect["h"] / 2) if rect else 52
                cx += random.uniform(-3, 3)
                cy += random.uniform(-3, 3)
                print(f"[solver_uc] clicking at ({cx:.0f}, {cy:.0f})")
                ActionChains(driver).move_by_offset(cx, cy).click().perform()
                ActionChains(driver).move_by_offset(-cx, -cy).perform()  # reset origin
                last_click = time.time()
                click_count += 1
                time.sleep(0.8)
                continue
            time.sleep(0.3)

        st = _get_state(driver)
        if st.get("token"):
            return st["token"]
        raise TimeoutError(f"Turnstile token not obtained within {timeout}s")
    finally:
        driver.quit()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python solver_uc.py <sitekey> <siteurl> [timeout]")
        sys.exit(1)
    timeout = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    print(solve(sys.argv[1], sys.argv[2], timeout=timeout))
