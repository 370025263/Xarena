"""
Final Playwright acceptance: captures the 3-skill (+noskill) comparison on the
Spreadsheet Skill Bench, per-task details (input/output/task.md/chat/pass-fail
via the per-question analysis), the radar chart, and role-specific admin views.
"""
import time, os
from playwright.sync_api import sync_playwright

URL = os.environ.get("FRONTEND_URL", "http://localhost:7799")
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(SHOTS, exist_ok=True)


def slp(t=3.0): time.sleep(t)


def snap(pg, name, full=False):
    path = f"{SHOTS}/{name}.png"
    for _ in range(5):
        try:
            pg.screenshot(path=path, full_page=full, animations="disabled", timeout=10000)
            print("  shot:", path); return True
        except Exception:
            time.sleep(2)
    print("  shot FAILED:", path); return False


def login(pg, user, pw):
    pg.goto(URL, wait_until="domcontentloaded", timeout=60000); slp(5)
    pg.fill("input[aria-label='用户名']", user)
    pg.fill("input[aria-label='密码']", pw)
    [x for x in pg.query_selector_all("button") if (x.inner_text() or '').strip() == "登录"][-1].click()
    slp(5)


def click_contains(pg, text):
    for x in pg.query_selector_all("button"):
        if text in (x.inner_text() or ''):
            x.click(); return True
    return False


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": 1600, "height": 2200})
        pg = ctx.new_page()

        # ---- participant: board detail + comparison + per-task analysis ----
        login(pg, "p_user1", "user1pass")
        click_contains(pg, "榜单广场"); slp(4)
        click_contains(pg, "Spreadsheet Skill Bench"); slp(5)
        snap(pg,"final_rankings")
        body = pg.inner_text("body")
        print("rankings contains:")
        for kw in ["noskill", "skillopt", "trace2skill", "xskill"]:
            print(f"   {kw}: {kw in body}")

        # generate per-question analysis (per-task input/output/gold/pred/correct)
        if click_contains(pg, "生成/刷新分析结果"):
            slp(8)
            snap(pg,"final_question_analysis")
            print("captured per-question analysis")

        # ---- admin: user management ----
        login(pg, "admin", "adminpass")
        if click_contains(pg, "用户管理"):
            slp(4); snap(pg,"final_admin_users")
            print("captured admin user management")

        # ---- maintainer: manage leaderboards ----
        login(pg, "l_creator", "creatorpass")
        if click_contains(pg, "管理榜单"):
            slp(4); snap(pg,"final_maintainer_manage")
            print("captured maintainer manage")

        b.close()
        print("DONE")


if __name__ == "__main__":
    main()
