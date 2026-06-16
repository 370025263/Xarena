import sys, time
from playwright.sync_api import sync_playwright

URL = "http://localhost:7799"
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1500, "height": 1000})
    pg.goto(URL, wait_until="networkidle", timeout=60000)
    time.sleep(4)
    pg.screenshot(path="/home/admin/leaderboard/run/shots/probe_landing.png", full_page=True)
    # dump text inputs / buttons
    print("=== TITLE ===", pg.title())
    print("=== TEXT INPUTS ===")
    for el in pg.query_selector_all("input"):
        print("  input type=", el.get_attribute("type"), "aria=", el.get_attribute("aria-label"), "ph=", el.get_attribute("placeholder"))
    print("=== BUTTONS ===")
    for el in pg.query_selector_all("button"):
        t = (el.inner_text() or "").strip().replace("\n", " ")
        if t:
            print("  button:", t[:40])
    print("=== visible text (first 1500) ===")
    print(pg.inner_text("body")[:1500])
    b.close()
