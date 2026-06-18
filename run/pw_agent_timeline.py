"""
Timeline repro: send a slow (>15s) tool-triggering message and snapshot the
agent panel body every 2s to see whether the autorefresh reruns mid-stream and
wipes the streamed tool-call/answer.
"""
import time, os, re
from playwright.sync_api import sync_playwright

URL = os.environ.get("FRONTEND_URL", "http://localhost:7799")
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(SHOTS, exist_ok=True)
CHROME = os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
TAG = os.environ.get("REPRO_TAG", "timeline")
MSG = os.environ.get("REPRO_MSG", "列出所有用户并逐一详细说明其角色权限与职责范围")


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
    btns = [b for b in pg.query_selector_all("button")
            if (b.inner_text() or "").strip() == "登录"]
    (btns[-1] if btns else pg.get_by_role("button", name="登录")).click()
    settle(pg, 4)


def click_button(pg, label):
    for b in pg.query_selector_all("button"):
        if label in ((b.inner_text() or "").strip()):
            b.click()
            return True
    return False


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, executable_path=CHROME, args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1500, "height": 1100})
        login(pg, "admin", "adminpass")
        click_button(pg, "🤖 Agent 面板")
        settle(pg, 4)
        ci = pg.query_selector("[data-testid='stChatInput'] textarea") or pg.query_selector("textarea")
        ci.click(); ci.fill(MSG); ci.press("Enter")
        t0 = time.time()
        rows = []
        # snapshot every 1.5s for 40s
        while time.time() - t0 < 42:
            time.sleep(1.5)
            try:
                txt = pg.inner_text("body")
            except Exception:
                continue
            el = round(time.time() - t0, 1)
            on_agent = "Agent Chat" in txt
            thinking = ("正在思考" in txt) or ("思考" in txt)
            calling = ("Calling" in txt) or ("list_user" in txt) or bool(re.search(r"Step\s*\d+", txt))
            steps_done = ("步操作完成" in txt) or ("步完成" in txt) or ("步操作" in txt)
            answer = ("admin" in txt and "p_user1" in txt and "l_creator" in txt and ("角色" in txt or "权限" in txt))
            user_echo = MSG[:8] in txt
            rows.append((el, on_agent, user_echo, thinking, calling, steps_done, answer))
        print(f"{'t':>5} {'agent':>5} {'echo':>5} {'think':>5} {'call':>5} {'done':>5} {'answ':>5}")
        for r in rows:
            print(f"{r[0]:>5} {str(r[1]):>5} {str(r[2]):>5} {str(r[3]):>5} {str(r[4]):>5} {str(r[5]):>5} {str(r[6]):>5}")
        pg.screenshot(path=f"{SHOTS}/agent_{TAG}_final.png", full_page=True)
        print("final shot:", f"{SHOTS}/agent_{TAG}_final.png")
        b.close()


if __name__ == "__main__":
    main()
