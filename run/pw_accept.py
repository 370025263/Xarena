"""
Playwright acceptance for the leaderboard frontend.
Logs in as each role (participant / creator-maintainer / admin), verifies the
role-specific navigation, opens the Spreadsheet Skill Bench, and captures the
rankings + per-task detail. Screenshots saved under run/shots/.
"""
import sys, time, os
from playwright.sync_api import sync_playwright

URL = os.environ.get("FRONTEND_URL", "http://localhost:7799")
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(SHOTS, exist_ok=True)

USERS = {
    "participant": ("p_user1", "user1pass"),
    "maintainer":  ("l_creator", "creatorpass"),
    "admin":       ("admin", "adminpass"),
}


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
    # click the form submit (last '登录' button)
    btns = [b for b in pg.query_selector_all("button") if (b.inner_text() or "").strip() == "登录"]
    (btns[-1] if btns else pg.get_by_role("button", name="登录")).click()
    settle(pg, 4)


def nav_text(pg):
    return pg.inner_text("body")


def click_button(pg, label, exact=False):
    for b in pg.query_selector_all("button"):
        t = (b.inner_text() or "").strip()
        if (t == label) if exact else (label in t):
            b.click()
            return True
    return False


def run_role(pg, role):
    user, pw = USERS[role]
    print(f"\n===== ROLE: {role} ({user}) =====")
    login(pg, user, pw)
    body = nav_text(pg)
    ok_login = ("退出" in body) or ("我的提交" in body) or ("榜单广场" in body and "请登录" not in body)
    print(f"  login ok={ok_login}")
    # role-specific nav presence
    nav_items = {
        "我的提交": "我的提交" in body,
        "积分中心": "积分中心" in body,
        "管理榜单(creator/admin)": "管理榜单" in body,
        "用户管理(admin)": "用户管理" in body,
    }
    print("  nav:", {k: v for k, v in nav_items.items()})
    shot(pg, f"role_{role}_dashboard")
    return nav_items


def open_board_and_rank(pg):
    print("\n===== Spreadsheet board rankings =====")
    click_button(pg, "🏆 榜单广场")
    settle(pg, 3)
    shot(pg, "board_square")
    # open the spreadsheet board detail
    opened = click_button(pg, "Spreadsheet Skill Bench") or click_button(pg, "立即打榜")
    settle(pg, 4)
    shot(pg, "board_detail_rankings")
    body = pg.inner_text("body")
    for kw in ["noskill", "skillopt", "trace2skill", "xskill"]:
        print(f"  rankings shows {kw}: {kw in body}")


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1500, "height": 1100})
        results = {}
        for role in ["participant", "maintainer", "admin"]:
            results[role] = run_role(pg, role)
        # use admin session to view board rankings
        open_board_and_rank(pg)
        b.close()
        print("\n===== SUMMARY =====")
        print("participant sees 用户管理? (should be False):", results["participant"]["用户管理(admin)"])
        print("admin sees 用户管理? (should be True):", results["admin"]["用户管理(admin)"])
        print("maintainer sees 管理榜单? (should be True):", results["maintainer"]["管理榜单(creator/admin)"])


if __name__ == "__main__":
    main()
