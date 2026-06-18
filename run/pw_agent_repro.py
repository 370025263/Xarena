"""
Playwright repro for the Agent panel tool-calling bug.
Logs in as admin, opens the 🤖 Agent 面板, sends a tool-triggering message
(列出所有用户), and captures what the UI actually renders.
"""
import sys, time, os, re
from playwright.sync_api import sync_playwright

URL = os.environ.get("FRONTEND_URL", "http://localhost:7799")
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(SHOTS, exist_ok=True)
CHROME = os.path.expanduser(
    "~/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome"
)
TAG = os.environ.get("REPRO_TAG", "before")
MSG = os.environ.get("REPRO_MSG", "列出所有用户")


def shot(pg, name):
    path = f"{SHOTS}/{name}.png"
    pg.screenshot(path=path, full_page=True)
    print("  shot:", path)


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


def click_button(pg, label, exact=False):
    for b in pg.query_selector_all("button"):
        t = (b.inner_text() or "").strip()
        if (t == label) if exact else (label in t):
            b.click()
            return True
    return False


def main():
    console_msgs = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, executable_path=CHROME,
                              args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1500, "height": 1100})
        pg.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
        pg.on("pageerror", lambda e: console_msgs.append(f"[pageerror] {e}"))

        print("=== login admin ===")
        login(pg, "admin", "adminpass")
        print("  open agent panel")
        ok = click_button(pg, "🤖 Agent 面板")
        print("  agent panel button clicked:", ok)
        settle(pg, 4)
        shot(pg, f"agent_{TAG}_1_opened")

        body = pg.inner_text("body")
        print("  title present (Agent Chat):", "Agent Chat" in body)
        print("  SID caption present:", "SID" in body)

        # Send a tool-triggering message via the chat_input
        print(f"=== send message: {MSG} ===")
        ci = pg.query_selector("textarea[aria-label='输入指令...']") \
            or pg.query_selector("textarea")
        if not ci:
            # streamlit chat_input renders as textarea with placeholder
            ci = pg.query_selector("[data-testid='stChatInput'] textarea")
        if not ci:
            print("  !! could not locate chat input")
            shot(pg, f"agent_{TAG}_NOINPUT")
            b.close()
            return
        ci.click()
        ci.fill(MSG)
        ci.press("Enter")

        # Observe streaming: poll body for tool-call / answer markers over ~75s
        markers = {
            "calling_tool": False,   # "Calling list_user" or "🔄"
            "tool_done": False,      # "✅ ... 找到 3 条用户" / 👤
            "user_table_or_answer": False,
            "error_shown": False,
            "thinking_shown": False,
        }
        deadline = time.time() + 80
        last_dump = ""
        while time.time() < deadline:
            time.sleep(2.0)
            try:
                txt = pg.inner_text("body")
            except Exception:
                continue
            last_dump = txt
            if ("Calling" in txt) or ("list_user" in txt) or ("搜索用户" in txt) \
               or re.search(r"Step\s*\d+", txt):
                markers["calling_tool"] = True
            if ("找到" in txt and "用户" in txt) or ("步操作" in txt) \
               or ("步完成" in txt) or ("步操作完成" in txt):
                markers["tool_done"] = True
            if ("正在思考" in txt) or ("思考" in txt):
                markers["thinking_shown"] = True
            # a rendered answer: a markdown table or the usernames
            if ("admin" in txt and "p_user1" in txt and "l_creator" in txt):
                markers["user_table_or_answer"] = True
            if ("Agent 错误" in txt) or ("服务报错" in txt) or ("连接失败" in txt) \
               or ("连接断开" in txt):
                markers["error_shown"] = True
            if markers["user_table_or_answer"] and markers["tool_done"]:
                break

        shot(pg, f"agent_{TAG}_2_after_send")
        print("=== MARKERS ===")
        for k, v in markers.items():
            print(f"  {k}: {v}")
        # dump a slice of the body that likely contains the assistant area
        print("=== BODY TAIL (last 1200 chars) ===")
        print(last_dump[-1200:])
        print("=== CONSOLE (last 40) ===")
        for m in console_msgs[-40:]:
            print(" ", m[:300])
        b.close()


if __name__ == "__main__":
    main()
