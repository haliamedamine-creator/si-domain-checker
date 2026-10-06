"""End-to-end browser test: filtered CSV download must only contain filtered rows."""
import csv
import io
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:5055"
DOMAINS = [
    "abc.si",            # registered
    "google.si",         # registered
    "MeritBeauty.si",    # available
    "moja-novadomena.si",# available
    "si.si",             # reserved
    "112.si",            # reserved
    "a.si",              # invalid
    "-bad.si",           # invalid
]

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def read_csv(download):
    raw = download.path()
    with open(raw, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


server = subprocess.Popen(
    [sys.executable, "app.py"],
    cwd=ROOT,
    env={**os.environ, "PORT": "5055"},
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
try:
    for _ in range(40):
        try:
            urllib.request.urlopen(BASE, timeout=1)
            break
        except Exception:
            time.sleep(0.25)
    else:
        raise SystemExit("server did not start")

    errors = []
    export_calls = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 950})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("request", lambda r: export_calls.append(r.url) if "/export" in r.url else None)

        page.goto(BASE)
        print("\n== page loads ==")
        check(page.title() == "SI Domain Checker", f"title = {page.title()!r}")
        check(page.locator("#startBtn").is_visible(), "start button visible")
        src = page.locator("script[src*='app.js']").get_attribute("src") or ""
        check("?v=" in src, f"cache-busted asset url = {src!r}")

        print("\n== run a check ==")
        page.fill("#input", "\n".join(DOMAINS))
        page.click(".switch .track")          # same target a user clicks
        page.wait_for_function("document.getElementById('details').checked === true")
        page.click("#startBtn")
        page.wait_for_selector("text=Finished", timeout=60000)

        check(page.locator("#c-available").inner_text() == "2", "available count = 2")
        check(page.locator("#c-registered").inner_text() == "2", "registered count = 2")
        check(page.locator("#c-reserved").inner_text() == "2", "reserved count = 2")
        check(page.locator("#c-invalid").inner_text() == "2", "invalid count = 2")
        check(page.locator("#tbody tr").count() == 8, "8 rows rendered")
        check("Download CSV" == page.locator("#exportBtn").inner_text(),
              f'unfiltered label = {page.locator("#exportBtn").inner_text()!r}')

        print("\n== unfiltered download ==")
        with page.expect_download() as dl:
            page.click("#exportBtn")
        d = dl.value
        rows = read_csv(d)
        check(len(rows) == 8, f"CSV has {len(rows)} rows (want 8)")
        check(d.suggested_filename == "domains-si.csv", f"filename = {d.suggested_filename}")
        check("expires" in rows[0] and rows[0]["expires"] == "2031-06-06",
              "detail column 'expires' present")

        print("\n== filter: available ==")
        page.click('.chip[data-filter="available"]')
        page.wait_for_timeout(200)
        visible = page.locator("#tbody tr").count()
        label = page.locator("#exportBtn").inner_text()
        check(visible == 2, f"{visible} rows shown (want 2)")
        check(label == "Download CSV (2)", f"label = {label!r}")

        with page.expect_download() as dl:
            page.click("#exportBtn")
        rows = read_csv(dl.value)
        check(len(rows) == 2, f"CSV has {len(rows)} rows (want 2)")
        check(all(r["status"] == "available" for r in rows),
              f"all rows available: {[r['status'] for r in rows]}")
        check({r["domain"] for r in rows} == {"MeritBeauty.si", "moja-novadomena.si"},
              f"domains = {sorted(r['domain'] for r in rows)}")
        check(dl.value.suggested_filename == "domains-si-available.csv",
              f"filename = {dl.value.suggested_filename}")

        print("\n== filter: registered ==")
        page.click('.chip[data-filter="registered"]')
        page.wait_for_timeout(200)
        with page.expect_download() as dl:
            page.click("#exportBtn")
        rows = read_csv(dl.value)
        check(len(rows) == 2 and all(r["status"] == "registered" for r in rows),
              f"registered CSV rows = {[r['domain'] for r in rows]}")

        print("\n== filter: reserved + search box ==")
        page.click('.chip[data-filter="reserved"]')
        page.wait_for_timeout(150)
        check(page.locator("#exportBtn").inner_text() == "Download CSV (2)",
              f'label = {page.locator("#exportBtn").inner_text()!r}')
        page.fill("#search", "112")
        page.wait_for_timeout(350)
        check(page.locator("#tbody tr").count() == 1, "search narrows to 1 row")
        check(page.locator("#exportBtn").inner_text() == "Download CSV (1)",
              f'label = {page.locator("#exportBtn").inner_text()!r}')
        with page.expect_download() as dl:
            page.click("#exportBtn")
        rows = read_csv(dl.value)
        check(len(rows) == 1 and rows[0]["domain"] == "112.si",
              f"CSV = {[r['domain'] for r in rows]}")

        print("\n== back to all ==")
        page.fill("#search", "")
        page.click('.chip[data-filter="all"]')
        page.wait_for_timeout(300)
        check(page.locator("#tbody tr").count() == 8, "8 rows again")
        check(page.locator("#exportBtn").inner_text() == "Download CSV",
              f'label = {page.locator("#exportBtn").inner_text()!r}')

        print("\n== zero-match filter ==")
        page.fill("#search", "zzzzz")
        page.wait_for_timeout(350)
        check(page.locator("#tbody tr").count() == 0, "0 rows for no-match search")
        check(page.locator("#empty").inner_text() == "No domains match this filter.",
              "empty-state message shown")
        check(page.locator("#exportBtn").is_disabled(), "export disabled at 0 rows")

        print("\n== console/page errors ==")
        check(not errors, f"errors: {errors[:3]}")
        check(not export_calls,
              f"no server-side /export calls (old cached path): {export_calls}")

        page.fill("#search", "")
        page.click('.chip[data-filter="all"]')
        page.wait_for_timeout(300)
        page.screenshot(path=str(ROOT / "_ui.png"), full_page=True)
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()

print("\n" + "=" * 46)
print(f"{'ALL PASSED' if not fails else 'FAILURES:'}")
for f in fails:
    print("  -", f)
sys.exit(1 if fails else 0)
