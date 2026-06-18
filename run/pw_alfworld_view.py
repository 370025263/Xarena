#!/usr/bin/env python3
"""Render the ALFWorld board's custom result-view exactly as the platform does:
fetch the board's result_view_html + the submission's /extra JSON, inject
`const DATA = <extra>` ahead of the board HTML inside a sandbox="allow-scripts"
iframe, and screenshot it headless. Proves the custom panel renders real
ALFWorld trajectory data.

Usage: python run/pw_alfworld_view.py <board_id> <submission_id>
"""
import sys, json, time, html, urllib.request

API = "http://localhost:30001"


def login(u, p):
    req = urllib.request.Request(API + "/api/login",
                                 data=json.dumps({"username": u, "password": p}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=15))["access_token"]


def get(path, tok):
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + tok})
    return json.load(urllib.request.urlopen(req, timeout=15))


def main():
    bid = sys.argv[1] if len(sys.argv) > 1 else "4"
    sid = sys.argv[2] if len(sys.argv) > 2 else "10"
    tok = login("l_creator", "creatorpass")

    rv = get(f"/api/leaderboard/{bid}/result-view", tok)
    extra = get(f"/api/submission/{sid}/extra", tok)
    print("has_custom:", rv.get("has_custom"), "| html bytes:", len(rv.get("html") or ""))
    print("extra items:", extra.get("count"))
    if not rv.get("has_custom"):
        print("ERROR: board has no custom result-view html (evaluator did not POST it).")
        sys.exit(2)

    board_html = rv["html"]
    # Same assembly as frontend a0af7da: DATA injected before the board HTML.
    inner = "<script>const DATA = " + json.dumps(extra) + ";</script>\n" + board_html
    srcdoc = html.escape(inner, quote=True)
    page = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>body{margin:0;background:#eceff3}</style></head><body>"
        f'<iframe sandbox="allow-scripts" srcdoc="{srcdoc}" '
        'style="width:1180px;height:1400px;border:0"></iframe>'
        "</body></html>"
    )

    from playwright.sync_api import sync_playwright
    out = "/home/admin/leaderboard/run/shots/alfworld_view.png"
    import os
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1220, "height": 1450})
        pg.set_content(page, wait_until="load")
        time.sleep(2)
        # expand all task panels inside the iframe so trajectories show
        fr = pg.frames[1] if len(pg.frames) > 1 else None
        if fr:
            try:
                fr.eval_on_selector_all(".task", "els => els.forEach(e => e.classList.add('open'))")
            except Exception as e:
                print("expand warn:", e)
            time.sleep(1)
            txt = fr.inner_text("body")
            print("=== rendered panel text (head) ===")
            print(txt[:900])
        pg.screenshot(path=out, full_page=True)
        b.close()
    print("SCREENSHOT ->", out)


if __name__ == "__main__":
    main()
