"""
Playwright acceptance for the tool-call trajectory in the custom result-view.
Logs in as p_user1, opens the test board, finds submission 11's custom result
view, expands the "🔧 工具调用过程" sections and confirms tool-call steps render.
Screenshot -> run/shots/tooltraj_test.png
"""
import os
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get("FRONTEND_URL", "http://localhost:7799")
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(SHOTS, exist_ok=True)
BOARD_NAME = "Spreadsheet Mini Custom-View (test)"
SUB_NAME_HINT = "noskill-tooltraj"


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
        pg = b.new_page(viewport={"width": 1500, "height": 1600})
        login(pg, "p_user1", "user1pass")
        print("login ok:", "退出" in pg.inner_text("body"))

        # Navigate: my submissions / the board with the test plugin.
        click_button(pg, "🏆 榜单广场")
        settle(pg, 3)
        print("opened board:", click_button(pg, BOARD_NAME))
        settle(pg, 5)

        # Scroll so the custom-view section + selectbox mount.
        for _ in range(8):
            pg.mouse.wheel(0, 1500)
            time.sleep(0.8)
        settle(pg, 3)

        # Pick submission #11 in the result-view selectbox so the iframe shows it.
        # The result-view selectbox is the one whose current value starts with "#".
        for attempt in range(3):
            try:
                target = None
                for sb in pg.query_selector_all("div[data-baseweb='select']"):
                    if "#" in (sb.inner_text() or ""):
                        target = sb
                        break
                if not target:
                    break
                target.click()
                time.sleep(1.2)
                opts = pg.query_selector_all("li[role='option'], div[role='option']")
                hit = None
                for o in opts:
                    t = o.inner_text() or ""
                    if "#11" in t or SUB_NAME_HINT in t:
                        hit = o
                        break
                if hit:
                    hit.click()
                    print("selected submission #11 in result-view selectbox")
                    settle(pg, 5)
                    break
                else:
                    pg.keyboard.press("Escape")
                    time.sleep(0.5)
            except Exception as e:
                print("selectbox attempt %d:" % attempt, str(e)[:80])
                time.sleep(1.0)

        for _ in range(6):
            pg.mouse.wheel(0, 1500)
            time.sleep(0.6)
        settle(pg, 3)

        # Find the nested sandbox iframe and expand the trajectory <details>.
        found_traj = False
        steps_seen = 0
        badges_seen = 0
        for fr in pg.frames:
            try:
                txt = fr.inner_text("body")
            except Exception:
                continue
            if "工具调用过程" in txt:
                found_traj = True
                # expand every <details> summary that mentions the trajectory
                for det in fr.query_selector_all("details"):
                    try:
                        s = det.query_selector("summary")
                        if s and "工具调用过程" in (s.inner_text() or ""):
                            det.evaluate("d => d.open = true")
                    except Exception:
                        pass
                time.sleep(1.0)
                steps_seen = len(fr.query_selector_all(".rv-step"))
                badges_seen = len(fr.query_selector_all(".rv-tool-badge"))
                print("trajectory frame: rv-step=%d rv-tool-badge=%d" % (steps_seen, badges_seen))
                # sample a few badge texts
                names = [el.inner_text() for el in fr.query_selector_all(".rv-tool-badge")[:8]]
                print("  tool badges:", names)

        settle(pg, 2)
        path = os.path.join(SHOTS, "tooltraj_test.png")
        pg.screenshot(path=path, full_page=True)
        print("shot:", path)
        b.close()
        print("RESULT found_traj=%s steps=%d badges=%d" % (found_traj, steps_seen, badges_seen))


if __name__ == "__main__":
    main()
