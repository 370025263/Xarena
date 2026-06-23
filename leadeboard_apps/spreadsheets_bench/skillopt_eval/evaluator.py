# -*- coding: utf-8 -*-
"""
evaluator.py — SpreadsheetBench × Skill 评测主脚本（榜单镜像 / 评测容器）

由 RAG 版 evaluator.py 改写为 SkillOpt 评测管线（保留"黄金"回传契约）。
原 RAG 版备份于 evaluator_rag_original.py.bak。

保留（与指北针后端的契约，逐字沿用原 evaluator 的行为）:
  - 读 SUBMISSION_ID / LEADERBOARD_ID / API_INTERNAL_URL
  - _post_metrics(): POST {metrics, eval_details, status:Succeeded} 到
        {API_INTERNAL_URL}/api/internal/submission/{SUBMISSION_ID}/score
  - _post_failure(): 失败回传 {status:Failed, metrics:{score:0,error}}
  - evaluator 不改字段结构，只 POST 数据由后端落库
  - http 代理"黄金逻辑"（内网用，外网用不到但尊重保留）

改写（核心）:
  - 不再调用 algo /qa + RAGAS；改为：
      1) 等待"打榜算法"把 skill 包写入共享卷（SKILL_DIR + DONE 标记），
         对应 readme："训练阶段完毕就会发送开始信号给榜单镜像，榜单镜像就会请求 skill 包"
      2) skill 两种约定：单 skill.md（skillopt/trace2skill）或多 skill 文件夹（xskill，anthropic 格式）
      3) 以 TARGET_MODE ∈ {single, multi, react} 跑 SkillOpt eval_only.py：
         - single：TARGET_BACKEND=claude_code_exec（with-harness，需显式指定）
         - multi ：openai_chat direct-chat（codegen，mode=multi）
         - react ：openai_chat react loop（mode=react）
      4) 解析 results.jsonl + predictions/<id>/ → 每题 input.xlsx/output.xlsx/task.md/
         chatmessage.log/通过 Yes-No，组装 metrics + eval_details 回传
"""

import os
os.environ["GIT_PYTHON_REFRESH"] = "quiet"

import json
import time
import math
import glob
import shutil
import logging
import subprocess
from typing import Dict, Any, List, Optional, Tuple

import requests


# -----------------------
# 0) 工具：避免空字符串覆盖默认值（沿用黄金逻辑）
# -----------------------
def getenv_nonempty(key: str, default: str) -> str:
    v = os.environ.get(key)
    if v is None:
        return default
    v = v.strip()
    return v if v else default


# -----------------------
# 1) 环境配置
# -----------------------
API_INTERNAL_URL = getenv_nonempty("API_INTERNAL_URL", "http://leaderboard-api-svc:80")
SUBMISSION_ID = os.environ.get("SUBMISSION_ID")
LEADERBOARD_ID = os.environ.get("LEADERBOARD_ID")

# 代理默认值（保持原有黄金逻辑；外网部署不会用到，但尊重保留）
HTTP_PROXY_DEFAULT = "http://50.67.89.228:10086"
HTTPS_PROXY_DEFAULT = "http://50.67.89.228:10086"
EVAL_FORCE_PROXY = getenv_nonempty("EVAL_FORCE_PROXY", "0")  # 默认关闭（外网直连）

# 评测模式（三种榜单镜像各跑一种）
TARGET_MODE = getenv_nonempty("TARGET_MODE", "single").lower()   # single | multi | react

# 打榜算法交付的 skill 包所在共享卷（与算法容器约定）
SKILL_DIR = getenv_nonempty("SKILL_DIR", "/shared/skill")
SKILL_DONE_MARKER = getenv_nonempty("SKILL_DONE_MARKER", os.path.join(SKILL_DIR, "DONE"))
SKILL_WAIT_TIMEOUT = int(getenv_nonempty("SKILL_WAIT_TIMEOUT", str(60 * 60 * 6)))  # 等训练最多 6h

# SkillOpt 评测管线
REPRO_ROOT = getenv_nonempty("REPRO_ROOT", "/app")
SKILLOPT_DIR = getenv_nonempty("SKILLOPT_DIR", "/app/SkillOpt")
EVAL_PY = getenv_nonempty("EVAL_PY", "/usr/local/bin/python")
DATA_ROOT = getenv_nonempty("DATA_ROOT", "/data")
SPLIT_DIR = getenv_nonempty("SPLIT_DIR", "/bench/data/test_10pct_split")
OUT_DIR = getenv_nonempty("OUT_DIR", os.path.join("/shared", f"eval_{SUBMISSION_ID or 'x'}_{TARGET_MODE}"))
WORKERS = getenv_nonempty("WORKERS", "3")
EVAL_MODEL = getenv_nonempty("EVAL_MODEL", "deepseek-v4-flash")
EXEC_TIMEOUT = getenv_nonempty("EXEC_TIMEOUT", "600")

# direct-chat（multi/react）所需的 OpenAI 兼容端点 / key
TARGET_ENDPOINT = getenv_nonempty("TARGET_ENDPOINT", "https://api.deepseek.com")
TARGET_API_KEY = getenv_nonempty("TARGET_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))

# single（claude_code_exec / harness）所需
ANTHROPIC_BASE_URL = getenv_nonempty("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
ANTHROPIC_AUTH_TOKEN = getenv_nonempty("ANTHROPIC_AUTH_TOKEN", os.environ.get("DEEPSEEK_API_KEY", ""))
CLAUDE_BIN = getenv_nonempty("CLAUDE_BIN", "")

DEFAULT_TIMEOUT = 60

logging.basicConfig(level=logging.INFO)


def _log(msg: str):
    print(f"[evaluator] {msg}", flush=True)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _sanitize_for_json(obj: Any):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else 0.0
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_sanitize_for_json(v) for v in obj)
    return obj


def _read(path: str, limit: Optional[int] = None) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
        return data[:limit] if limit else data
    except Exception:
        return ""


# 明确不走系统代理的会话（回传后端走集群内网）
SESSION = requests.Session()
SESSION.trust_env = False


# -----------------------
# 2) 代理黄金逻辑（保留）
# -----------------------
def _enable_eval_proxy_if_needed():
    if EVAL_FORCE_PROXY == "1":
        hp = os.environ.get("http_proxy", HTTP_PROXY_DEFAULT).strip()
        sp = os.environ.get("https_proxy", HTTPS_PROXY_DEFAULT).strip()
        if hp:
            os.environ["HTTP_PROXY"] = hp
        if sp:
            os.environ["HTTPS_PROXY"] = sp
        os.environ["NO_PROXY"] = os.environ.get(
            "no_proxy",
            "localhost,127.0.0.1,*.local,leaderboard-api-svc,*.svc,*.cluster.local",
        )


_enable_eval_proxy_if_needed()


# -----------------------
# 3) 回传契约（逐字沿用黄金 evaluator）
# -----------------------
def _post_failure(reason: str):
    _log(f"FATAL: {reason}")
    if not SUBMISSION_ID:
        _log("SUBMISSION_ID 未设置, 无法回传失败状态。")
        return
    try:
        url = f"{API_INTERNAL_URL}/api/internal/submission/{SUBMISSION_ID}/score"
        payload = {"status": "Failed", "metrics": {"score": 0.0, "error": reason}}
        _log(f"POST failure => {url}")
        resp = SESSION.post(url, headers={"Content-Type": "application/json"},
                            json=payload, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        _log(f"CRITICAL: 回传失败状态时出错: {e}")


def _post_metrics(metrics: Dict[str, Any], eval_details: Optional[List[Dict[str, Any]]] = None,
                  result_view_html: Optional[str] = None):
    assert SUBMISSION_ID, "SUBMISSION_ID env is required"
    url = f"{API_INTERNAL_URL}/api/internal/submission/{SUBMISSION_ID}/score"
    payload: Dict[str, Any] = {"metrics": metrics, "status": "Succeeded"}
    if eval_details is not None:
        payload["eval_details"] = eval_details
        metrics["eval_details"] = eval_details
    if result_view_html:
        payload["result_view_html"] = result_view_html
    safe_payload = _sanitize_for_json(payload)
    _log(f"POST metrics => {url} : {json.dumps(safe_payload, ensure_ascii=False)[:1500]}...")
    resp = SESSION.post(url, headers={"Content-Type": "application/json"},
                        json=safe_payload, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    _log(f"POST metrics OK: {resp.text}")


# -----------------------
# 4) 等待打榜算法交付 skill 包（类比黄金 _algo_ready_or_raise）
# -----------------------
def _resolve_skill() -> Tuple[str, str]:
    """
    返回 (skill_convention, skill_path):
      - "single": SKILL_DIR 下的单个 *.md（skillopt/trace2skill）
      - "multi" : SKILL_DIR/skills/ 下的多 skill 文件夹（xskill，anthropic 格式）
    约定优先级：存在 skills/ 子目录且含 SKILL.md → multi；否则取单个 .md → single。
    """
    skills_dir = os.path.join(SKILL_DIR, "skills")
    if os.path.isdir(skills_dir) and glob.glob(os.path.join(skills_dir, "*", "SKILL.md")):
        return "multi", skills_dir
    mds = sorted(glob.glob(os.path.join(SKILL_DIR, "*.md")))
    mds = [m for m in mds if os.path.basename(m).lower() not in ("readme.md",)]
    if mds:
        return "single", mds[0]
    raise RuntimeError(f"skill 包未找到：{SKILL_DIR}（既无 skills/*/SKILL.md，也无 *.md）")


def _wait_for_skill() -> Tuple[str, str]:
    _log(f"Waiting for 打榜算法 skill 包 at {SKILL_DIR} (marker={SKILL_DONE_MARKER}) ...")
    start = time.time()
    while time.time() - start < SKILL_WAIT_TIMEOUT:
        if os.path.exists(SKILL_DONE_MARKER):
            conv, path = _resolve_skill()
            _log(f"skill ready: convention={conv} path={path}")
            return conv, path
        time.sleep(5)
    raise RuntimeError(f"等待 skill 包超时（{SKILL_WAIT_TIMEOUT}s）：{SKILL_DONE_MARKER} 未出现")


# -----------------------
# 5) 跑 SkillOpt eval_only.py（类比黄金 _run_external_ragas 的 subprocess 思路）
# -----------------------
def _run_eval(skill_convention: str, skill_path: str) -> str:
    """以 TARGET_MODE 跑 eval_only.py，返回 out_dir。"""
    data_root = DATA_ROOT or _read(os.path.join(REPRO_ROOT, "repro/data/DATA_ROOT.txt")).strip()
    if not data_root:
        raise RuntimeError("DATA_ROOT 未设置且 DATA_ROOT.txt 不可读")

    os.makedirs(OUT_DIR, exist_ok=True)

    # 隔离 claude home（single/xskill 用），多 skill 注入到 .claude/skills
    home_dir = os.path.join(OUT_DIR, "_home")
    if os.path.isdir(home_dir):
        shutil.rmtree(home_dir)
    os.makedirs(os.path.join(home_dir, ".claude", "skills"), exist_ok=True)
    os.makedirs(os.path.join(home_dir, ".claude", "projects"), exist_ok=True)
    with open(os.path.join(home_dir, ".claude", "settings.json"), "w") as f:
        json.dump({"skillListingBudgetFraction": 0.1,
                   "permissions": {"defaultMode": "bypassPermissions"}}, f)

    env = os.environ.copy()
    env["XSKILL_CLAUDE_HOME"] = os.path.join(home_dir, ".claude")

    empty_skill = os.path.join(OUT_DIR, "empty_skill.md")
    open(empty_skill, "w").close()

    # 组装 skill 注入
    if skill_convention == "multi":
        # xskill 多 skill 约定：拷入 .claude/skills，native 模式，--skill 用空文件
        for d in glob.glob(os.path.join(skill_path, "*")):
            if os.path.isfile(os.path.join(d, "SKILL.md")):
                name = os.path.basename(d)
                dst = os.path.join(home_dir, ".claude", "skills", name)
                shutil.copytree(d, dst, dirs_exist_ok=True)
        env["XSKILL_SKILL_MODE"] = "native"
        skill_file = empty_skill
        if TARGET_MODE != "single":
            raise RuntimeError("多 skill（xskill）约定仅支持 single（with-harness）模式")
    else:
        # 单 skill.md 约定：注入 system prompt（reference 模式），适配三种模式
        skill_file = skill_path
        env.pop("XSKILL_SKILL_MODE", None)

    mode_for_cfg = "single" if TARGET_MODE == "single" else TARGET_MODE
    cfg_opts = ["env.mode=" + mode_for_cfg, "env.exec_timeout=" + EXEC_TIMEOUT]

    cmd = [EVAL_PY, "scripts/eval_only.py",
           "--config", "configs/spreadsheetbench/default.yaml",
           "--skill", skill_file,
           "--split", "test", "--split_mode", "split_dir",
           "--split_dir", SPLIT_DIR, "--data_root", data_root,
           "--workers", WORKERS, "--seed", "42", "--out_root", OUT_DIR,
           "--mode", mode_for_cfg,
           "--cfg-options", *cfg_opts]

    if TARGET_MODE == "single":
        # with-harness：claude_code_exec + deepseek-v4-flash（Anthropic 兼容端点）
        claude_abs = CLAUDE_BIN or shutil.which("claude") or ""
        if not claude_abs:
            raise RuntimeError("single 模式需要可用的 claude 二进制（CLAUDE_BIN 或 PATH）")
        env["CLAUDE_CODE_EXEC_USE_SDK"] = "cli"
        env["ANTHROPIC_BASE_URL"] = ANTHROPIC_BASE_URL
        env["ANTHROPIC_AUTH_TOKEN"] = ANTHROPIC_AUTH_TOKEN
        cmd += ["--target_backend", "claude_code_exec", "--target_model", EVAL_MODEL,
                "--claude_code_exec_use_sdk", "cli", "--claude_code_exec_path", claude_abs]
    else:
        # multi / react：openai_chat direct backend
        cmd += ["--target_backend", "openai_chat", "--target_model", EVAL_MODEL,
                "--target_azure_openai_endpoint", TARGET_ENDPOINT,
                "--target_azure_openai_api_key", TARGET_API_KEY,
                "--target_azure_openai_auth_mode", "openai_compatible"]

    _log(f"Running eval_only.py (mode={TARGET_MODE}): {' '.join(cmd)} (cwd={SKILLOPT_DIR})")
    proc = subprocess.Popen(cmd, env=env, cwd=SKILLOPT_DIR, text=True)
    proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"eval_only.py 退出码非 0：{proc.returncode}")
    return OUT_DIR


# -----------------------
# 6) 解析结果 → metrics + eval_details（每题 input/output/task.md/聊天日志/通过 Yes-No）
# -----------------------
def _gold_positions() -> Dict[str, str]:
    pos = {}
    try:
        items = json.load(open(os.path.join(SPLIT_DIR, "test", "items.json")))
        for it in items:
            pos[str(it["id"])] = it.get("answer_position", "")
    except Exception:
        pass
    return pos


def _result_summary(content: Any, limit: Optional[int] = None) -> str:
    """tool_result.content → 字符串。limit=None（默认）全量保留，不截断。

    全量存储是安全的：Flask 无 MAX_CONTENT_LENGTH、backend extra_json 是 sqlite TEXT
    （无大小上限）、评测 POST 走 k8s 内网 leaderboard-api-svc:80 不过 nginx。因此工具
    结果默认完整记录，前端 result-view 能看到真值（如算法读到的完整注入 skill 正文）。"""
    if isinstance(content, str):
        s = content
    elif isinstance(content, list):
        s = " ".join(
            p for p in (
                (str(blk.get("text") or blk.get("content") or "")
                 if isinstance(blk, dict) else str(blk))
                for blk in content
            ) if p
        )
    elif content is None:
        return ""
    else:
        s = str(content)
    return s if limit is None else s[:limit]


def _input_repr(inp: Any, limit: Optional[int] = None) -> str:
    """tool_use.input 的紧凑表示（key=val）。limit=None 全量。"""
    if isinstance(inp, dict):
        try:
            s = json.dumps(inp, ensure_ascii=False)
        except Exception:
            s = str(inp)
    elif inp is not None:
        s = str(inp)
    else:
        return ""
    return s if limit is None else s[:limit]


def _tool_trajectory_for_task(out_dir: str, sid: Optional[str], task_id: str,
                              max_steps: int = 500) -> List[Dict[str, Any]]:
    """
    解析 claude 会话 jsonl，抽取按时序排列的工具调用轨迹（single 模式才有）。

    会话 jsonl 落在隔离 home 下：
      {out_dir}/_home/.claude/projects/*predictions-<task_id>-codex-single*/*.jsonl
    每行 message.content 是一个 block 列表，逐块产出紧凑 step：
      {"kind":"tool_use","name":..,"input":..}
      {"kind":"tool_result","summary":..}
      {"kind":"text","text":..}
    task_id 可能含连字符（如 188-39），用通配稳健匹配该目录。仅单模式存在该文件，
    否则返回空列表。最多 max_steps 步。
    """
    proj_root = os.path.join(out_dir, "_home", ".claude", "projects")
    if not os.path.isdir(proj_root):
        return []
    patterns = [
        os.path.join(proj_root, f"*predictions-{task_id}-codex-single*", "*.jsonl"),
        os.path.join(proj_root, f"*predictions-{task_id}-*", "*.jsonl"),
    ]
    files: List[str] = []
    for pat in patterns:
        files = sorted(glob.glob(pat))
        if files:
            break
    if not files:
        return []

    steps: List[Dict[str, Any]] = []
    for fp in files:
        try:
            fh = open(fp, "r", encoding="utf-8", errors="replace")
        except Exception:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for blk in content:
                    if not isinstance(blk, dict):
                        continue
                    bt = blk.get("type")
                    if bt == "tool_use":
                        steps.append({
                            "kind": "tool_use",
                            "name": str(blk.get("name") or ""),
                            "input": _input_repr(blk.get("input")),
                        })
                    elif bt == "tool_result":
                        steps.append({
                            "kind": "tool_result",
                            "summary": _result_summary(blk.get("content")),
                        })
                    elif bt == "text":
                        txt = str(blk.get("text") or "").strip()
                        if txt:
                            steps.append({"kind": "text", "text": txt})
                    if len(steps) >= max_steps:
                        return steps
        if steps:
            break
    return steps[:max_steps]


def _build_metrics_and_details(out_dir: str, skill_convention: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    summ = {}
    sp = os.path.join(out_dir, "eval_summary.json")
    if os.path.exists(sp):
        summ = json.load(open(sp))
    rows = []
    rp = os.path.join(out_dir, "results.jsonl")
    if os.path.exists(rp):
        for line in open(rp):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass

    gold = _gold_positions()
    n = len(rows)
    n_pass = sum(1 for r in rows if int(r.get("hard", 0)) == 1)
    hard = float(summ.get("hard", (n_pass / n if n else 0.0)))
    soft = float(summ.get("soft", 0.0))
    score = round(_clamp(hard * 100.0, 0.0, 100.0), 4)

    eval_details: List[Dict[str, Any]] = []
    for r in rows:
        tid = str(r.get("id"))
        pdir = os.path.join(out_dir, "predictions", tid)
        cdir = os.path.join(pdir, "codex_single")
        passed = int(r.get("hard", 0)) == 1
        fail_reason = r.get("fail_reason") or r.get("error") or ""
        extra = {
            "instruction_type": r.get("instruction_type"),
            "n_turns": r.get("n_turns"),
            "n_pass": r.get("n_pass"),
            "n_cases": r.get("n_cases"),
            "fail_reason": fail_reason,
            "mode": TARGET_MODE,
            "model": EVAL_MODEL,
            "skill_convention": skill_convention,
            "task_md": _read(os.path.join(cdir, "task.md"), 6000),
            "solution_py": _read(os.path.join(cdir, "solution.py"), 8000),
            "chatmessage_log": (_read(os.path.join(pdir, "conversation.json"), 8000)
                                or _read(os.path.join(pdir, "claude_raw.txt"), 8000)),
            # 新增：完整工具调用轨迹（single 模式从 claude 会话 jsonl 提取；其它模式为空）。
            "tool_trajectory": (_tool_trajectory_for_task(out_dir, SUBMISSION_ID, tid)
                                if TARGET_MODE == "single" else []),
            "input_xlsx": os.path.join(cdir, "input.xlsx"),
            "output_xlsx": os.path.join(cdir, "output.xlsx"),
            "spreadsheet_preview": (r.get("spreadsheet_preview") or "")[:2000],
        }
        eval_details.append({
            "question_id": tid,
            "question": (r.get("task_description") or "")[:4000],
            "gold_answer": f"answer_position={gold.get(tid, '')} ({r.get('instruction_type')})",
            "pred_answer": ("PASS" if passed else "FAIL")
                           + f" | cases {r.get('n_pass', 0)}/{r.get('n_cases', 0)}"
                           + (f" | {fail_reason}" if not passed and fail_reason else ""),
            "is_correct": bool(passed),
            "latency_ms": 0.0,
            "used_tokens": 0,
            "retrieved": int(r.get("n_turns") or 0),
            "eval_prompt": f"SpreadsheetBench hard-match / mode={TARGET_MODE}",
            "extra": extra,
        })

    metrics = {
        "score": score,
        "pass_rate_pct": score,
        "hard": hard,
        "soft": soft,
        "n_tasks": n,
        "n_pass": n_pass,
        "n_fail": n - n_pass,
        "mode": TARGET_MODE,
        "model": EVAL_MODEL,
        "skill_convention": skill_convention,
        "split": os.path.basename(SPLIT_DIR.rstrip("/")),
        "dataset": "SpreadsheetBench (10pct test)",
    }
    return metrics, eval_details


# -----------------------
# 7) 主函数
# -----------------------
def main():
    if not SUBMISSION_ID:
        raise RuntimeError("Missing SUBMISSION_ID env")
    _log(f"Start SpreadsheetBench eval: submission={SUBMISSION_ID}, leaderboard={LEADERBOARD_ID}")
    _log(f"TARGET_MODE={TARGET_MODE}  EVAL_MODEL={EVAL_MODEL}  SPLIT_DIR={SPLIT_DIR}")

    conv, skill_path = _wait_for_skill()
    out_dir = _run_eval(conv, skill_path)
    metrics, eval_details = _build_metrics_and_details(out_dir, conv)

    # 榜单自定义结果视图（可选）：镜像里若烘焙了 /app/result_view.html 就随 /score 回传。
    # 生产单评测镜像不含该文件时为 no-op，行为不变。
    _result_view_html = None
    _rv_path = "/app/result_view.html"
    if os.path.isfile(_rv_path):
        try:
            with open(_rv_path, "r", encoding="utf-8") as _f:
                _result_view_html = _f.read()
            _log(f"loaded custom result view ({len(_result_view_html)} bytes)")
        except Exception as e:
            _log(f"WARN: read {_rv_path} failed: {e}")

    _post_metrics(metrics, eval_details=eval_details, result_view_html=_result_view_html)

    # 通用产物落盘：把本次评测 run 目录拷到 $OUTPUT_DIR/eval（不耦合 phase）。
    _out_base = os.environ.get("OUTPUT_DIR", "").strip()
    if _out_base:
        _dst = os.path.join(_out_base, "eval")
        try:
            os.makedirs(_dst, exist_ok=True)
            subprocess.run(["cp", "-a", out_dir + "/.", _dst], check=False)
            _log(f"copied eval artifacts -> {_dst}")
            if os.path.isfile(_rv_path):
                subprocess.run(["cp", _rv_path, _dst + "/"], check=False)
                _log(f"copied custom result view -> {_dst}")
        except Exception as e:
            _log(f"WARN: copy eval artifacts failed: {e}")

    _log("=== SUMMARY ===")
    _log(json.dumps(_sanitize_for_json(metrics), ensure_ascii=False, indent=2))
    _log("Evaluation done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        _post_failure(f"Evaluator main() failed: {e}")
        raise
