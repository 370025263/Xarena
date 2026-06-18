"""Verify the log viewer fetches logs SAME-ORIGIN (no hardcoded domain) after the fix.
Loads the UI via the public proxied origin, opens a submission's logs, and captures
the /api/submission/<id>/logs network request — it must be same-origin + 200."""
import os, time
from playwright.sync_api import sync_playwright

URL = os.environ.get("FRONTEND_URL", "https://algo.xskill.wiki")
SID = os.environ.get("SID", "5")
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(SHOTS, exist_ok=True)
log_reqs = []


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 1000})
        pg = ctx.new_page()
        pg.on("response", lambda r: (("/api/submission/" in r.url and "/logs" in r.url)
                                     and log_reqs.append((r.status, r.url))))
        pg.goto(URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        pg.fill("input[aria-label='用户名']", "p_user1")
        pg.fill("input[aria-label='密码']", "user1pass")
        btns = [x for x in pg.query_selector_all("button") if (x.inner_text() or "").strip() == "登录"]
        (btns[-1] if btns else pg.get_by_role("button", name="登录")).click()
        time.sleep(6)
        for x in pg.query_selector_all("button"):
            if "我的提交" in (x.inner_text() or ""):
                x.click(); break
        time.sleep(5)
        clicked = False
        for x in pg.query_selector_all("button"):
            if "查看日志" in (x.inner_text() or ""):
                x.click(); clicked = True; break
        time.sleep(9)  # let the in-iframe JS poll fire a few times
        pg.screenshot(path=f"{SHOTS}/logs_verify_after.png", full_page=True)
        print("page_origin =", pg.evaluate("location.origin"))
        print("clicked_log_button =", clicked)
        print("captured /logs requests (status, url):")
        for s, u in log_reqs:
            print(f"  {s}  {u}")
        ok = any(s == 200 and u.startswith(URL.rstrip('/')) for s, u in log_reqs)
        print("SAME-ORIGIN 200 LOG FETCH:", ok)
        b.close()


if __name__ == "__main__":
    main()
