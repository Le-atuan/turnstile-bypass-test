#!/usr/bin/env python3
"""
One-off: hooks fetch/XHR on the real page to capture the exact lookup
request (url, method, headers, body, cookies) for run_test.py to replicate.
Only run against infrastructure you own or are authorized to test.
"""
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

import undetected_chromedriver as uc
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from solver_uc import _installed_chrome_major

SITEURL = os.environ.get("TARGET_SITEURL", "https://tracuu.einvoice365.com/").strip()
TEST_CASES = json.loads(os.environ.get("TEST_CASES", "[]") or "[]")
tax_code, lookup_code = TEST_CASES[0]["info"]["tax_code"], TEST_CASES[0]["info"]["lookup_code"]

HOOK_JS = """
window.__captured = [];
(function() {
    const origFetch = window.fetch;
    window.fetch = function(url, opts) {
        opts = opts || {};
        window.__captured.push({
            kind: 'fetch', url: String(url), method: opts.method || 'GET',
            headers: opts.headers ? (opts.headers instanceof Headers ? Object.fromEntries(opts.headers.entries()) : opts.headers) : {},
            body: opts.body ? String(opts.body) : null,
        });
        return origFetch.apply(window, arguments);
    };
    const OrigXHR = window.XMLHttpRequest;
    function PatchedXHR() {
        const xhr = new OrigXHR();
        const origOpen = xhr.open;
        const origSend = xhr.send;
        const origSetHeader = xhr.setRequestHeader;
        let rec = {kind: 'xhr', headers: {}};
        xhr.open = function(method, url) { rec.method = method; rec.url = url; return origOpen.apply(xhr, arguments); };
        xhr.setRequestHeader = function(k, v) { rec.headers[k] = v; return origSetHeader.apply(xhr, arguments); };
        xhr.send = function(body) { rec.body = body ? String(body) : null; window.__captured.push(rec); return origSend.apply(xhr, arguments); };
        return xhr;
    }
    window.XMLHttpRequest = PatchedXHR;
})();
"""


def human_type(element, text):
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(0.05, 0.18))


def find_button_rect(driver, view_id, text):
    raw = driver.execute_script("""
        return JSON.stringify((() => {
            const VIEW_ID = %s, TEXT = %s;
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
            return {x: r.x, y: r.y, w: r.width, h: r.height};
        })());
    """ % (json.dumps(view_id), json.dumps(text)))
    return json.loads(raw) if raw and raw != "null" else None


def click_rect(driver, rect):
    cx = rect["x"] + rect["w"] / 2
    cy = rect["y"] + rect["h"] / 2
    ActionChains(driver).move_by_offset(cx, cy).click().perform()
    ActionChains(driver).move_by_offset(-cx, -cy).perform()


options = uc.ChromeOptions()
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
driver = uc.Chrome(options=options, headless=False, version_main=_installed_chrome_major())

try:
    driver.get(SITEURL)
    driver.execute_script(HOOK_JS)

    deadline = time.time() + 20
    while time.time() < deadline:
        val = driver.execute_script("return document.querySelector('[name=\"cf-turnstile-response\"]')?.value || '';")
        if val:
            break
        time.sleep(0.5)

    tax_input = driver.find_element(By.CSS_SELECTOR, 'input[placeholder="MST bên bán"]')
    tax_input.click()
    human_type(tax_input, tax_code)
    lookup_input = driver.find_element(By.CSS_SELECTOR, 'input[placeholder="Mã tra cứu hóa đơn"]')
    lookup_input.click()
    human_type(lookup_input, lookup_code)

    rect = find_button_rect(driver, "search:btnsearch", "Tra cứu")
    click_rect(driver, rect)

    time.sleep(5)

    captured = json.loads(driver.execute_script("return JSON.stringify(window.__captured || []);"))
    cookies = {c["name"]: c["value"] for c in driver.get_cookies()}

    with open("captured_request.json", "w", encoding="utf-8") as f:
        json.dump({"requests": captured, "cookies": cookies}, f, indent=2, ensure_ascii=False)
    print(f"[capture] {len(captured)} request(s) saved to captured_request.json")

finally:
    driver.quit()
