"""
Playwright acceptance for the board-supplied custom result-view panel.
Logs in as p_user1, opens the test board "Spreadsheet Mini Custom-View (test)",
finds the "自定义结果视图" section, and confirms the nested sandboxed iframe
renders the PASS/FAIL cards from the injected DATA.
Screenshot -> run/shots/custom_view_test.png
"""
import os
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get("FRONTEND_URL", "http://localhost:7799")
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(SHOTS, exist_ok=True)
BOARD_NAME = "Spreadsheet Mini Custom-View (test)"


def settle(pg, t=3.0):
    try:
        pg.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    time.sleep(t)


def login(pg, user, pw):
    pg.goto(URL, wait_until="domcontentloaded", timeout=60000)
    settle(pg, 3)
    pg.fill("input[aria-label='用户名']", user)
    pg.fill("input[aria-label='密码']", pw)
    btns = [b for b in pg.query_selector_all("button") if (b.inner_text() or "").strip() == "登录"]
    (btns[-1] if btns else pg.get_by_role("button", name="登录")).click()
    settle(pg, 4)


def click_button(pg, label, exact=False):
    for b in pg.query_selector_all("button"):
        t = (b.inner_text() or "").strip()
        if (t == label) if exact else (label in t):
            b.click()
            return True
    return False


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1500, "height": 1400})
        login(pg, "p_user1", "user1pass")
        print("login body has 退出:", "退出" in pg.inner_text("body"))

        click_button(pg, "🏆 榜单广场")
        settle(pg, 3)

        opened = click_button(pg, BOARD_NAME)
        print("opened test board:", opened)
        settle(pg, 5)

        body = pg.inner_text("body")
        has_section = "自定义结果视图" in body
        print("custom-view section present:", has_section)

        # scroll down so the iframe renders / lazy components mount
        for _ in range(6):
            pg.mouse.wheel(0, 1400)
            time.sleep(1.0)
        settle(pg, 3)

        # The Streamlit components.html host iframe -> inside it our nested
        # sandbox iframe (srcdoc). Walk frames to find the PASS/FAIL cards.
        found_cards = False
        frame_texts = []
        for fr in pg.frames:
            try:
                txt = fr.inner_text("body")
            except Exception:
                continue
            if "PASS" in txt or "FAIL" in txt or "题目" in txt or "task.md" in txt:
                frame_texts.append(txt[:400])
                if ("PASS" in txt) or ("FAIL" in txt):
                    found_cards = True
        print("nested-frame PASS/FAIL cards found:", found_cards)
        for ft in frame_texts:
            print("  frame snippet:", ft.replace("\n", " ")[:200])

        path = os.path.join(SHOTS, "custom_view_test.png")
        pg.screenshot(path=path, full_page=True)
        print("shot:", path)

        b.close()
        print("RESULT has_section=%s found_cards=%s" % (has_section, found_cards))


if __name__ == "__main__":
    main()
