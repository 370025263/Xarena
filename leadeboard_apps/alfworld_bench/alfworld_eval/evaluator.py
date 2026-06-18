# -*- coding: utf-8 -*-
"""
evaluator.py — ALFWorld × Skill 评测主脚本（榜单镜像 / 评测容器）

镜像 SpreadsheetBench 评测器（leadeboard_apps/spreadsheets_bench/skillopt_eval/
evaluator.py）的结构与「黄金回传契约」，把任务域从 SpreadsheetBench 换成
ALFWorld（SkillOpt 的 alfworld 环境，TextWorld ReAct agent，max_steps）。

保留（与指北针后端的契约，逐字沿用 SpreadsheetBench evaluator 的行为）:
  - 读 SUBMISSION_ID / LEADERBOARD_ID / API_INTERNAL_URL
  - _post_metrics(): POST {metrics, eval_details, result_view_html, status:Succeeded}
        到 {API_INTERNAL_URL}/api/internal/submission/{SUBMISSION_ID}/score
  - _post_failure(): 失败回传 {status:Failed, metrics:{score:0,error}}
  - evaluator 不改后端字段结构，只 POST 数据由后端落库
  - 通用产物落盘：把本次 run 目录拷到 $OUTPUT_DIR/eval（含 result_view.html）

ALFWorld 专属:
  1) 等「打榜算法」把 skill 包写入共享卷（SKILL_DIR + DONE 标记）。
     - 单 skill.md（skillopt / trace2skill / noskill）注入 agent system prompt（reference）。
     - 多 skill 文件夹（skills/<name>/SKILL.md，xskill 约定）合并为单文本注入。
  2) 准备 ALFWorld 运行期数据：把 split 的相对 gamefile 展开为 $ALFWORLD_DATA 下绝对路径，
     生成运行期 split_dir（train/val/test 的 items.json）。把 alfworld 自带 logic 文件
     （alfred.pddl / alfred.twl2）拷到 $ALFWORLD_DATA/logic/（config_tw.yaml 需要）。
  3) 跑 SkillOpt eval_only.py（env=alfworld，--split test，split_mode=split_dir），
     TextWorld ReAct rollout（openai_chat direct backend / deepseek-v4-flash）。
  4) 解析 results.jsonl + predictions/<id>/conversation.json → 每题 success/fail +
     动作/观测轨迹 → 组装 metrics + eval_details + result_view_html 回传。
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

# 打榜算法交付的 skill 包所在共享卷（与算法容器约定）
SKILL_DIR = getenv_nonempty("SKILL_DIR", "/shared/skill")
SKILL_DONE_MARKER = getenv_nonempty("SKILL_DONE_MARKER", os.path.join(SKILL_DIR, "DONE"))
SKILL_WAIT_TIMEOUT = int(getenv_nonempty("SKILL_WAIT_TIMEOUT", str(60 * 60 * 6)))  # 等训练最多 6h

# SkillOpt 评测管线
SKILLOPT_DIR = getenv_nonempty("SKILLOPT_DIR", "/app/SkillOpt")
EVAL_PY = getenv_nonempty("EVAL_PY", "/usr/local/bin/python")
# ALFWorld 数据根（json_2.1.1/ 与 logic/ 所在）。镜像里 baked 到 /alfworld_data，
# 或运行期由 fetch_data.sh 下载到挂载卷后用 ALFWORLD_DATA 指过去。
ALFWORLD_DATA = getenv_nonempty("ALFWORLD_DATA", "/alfworld_data")
# 评测 split 清单目录（含 train/val/test 的 items.json，gamefile 为相对 $ALFWORLD_DATA 路径）。
SPLIT_DIR = getenv_nonempty("SPLIT_DIR", "/bench/data/alfworld_path_split")
EVAL_SPLIT = getenv_nonempty("EVAL_SPLIT", "test")
OUT_DIR = getenv_nonempty("OUT_DIR", os.path.join("/shared", f"eval_{SUBMISSION_ID or 'x'}_alfworld"))
WORKERS = getenv_nonempty("WORKERS", "3")
MAX_STEPS = getenv_nonempty("MAX_STEPS", "50")
EVAL_MODEL = getenv_nonempty("EVAL_MODEL", "deepseek-v4-flash")

# openai 兼容端点 / key（ALFWorld 用 openai_chat direct backend 跑 ReAct rollout）
TARGET_ENDPOINT = getenv_nonempty("TARGET_ENDPOINT", "https://api.deepseek.com")
TARGET_API_KEY = getenv_nonempty("TARGET_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))

DEFAULT_TIMEOUT = 60

logging.basicConfig(level=logging.INFO)


def _log(msg: str):
    print(f"[alfworld-eval] {msg}", flush=True)


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
# 2) 回传契约（逐字沿用黄金 evaluator）
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
    _log(f"POST metrics => {url} : {json.dumps(safe_payload, ensure_ascii=False)[:1200]}...")
    resp = SESSION.post(url, headers={"Content-Type": "application/json"},
                        json=safe_payload, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    _log(f"POST metrics OK: {resp.text}")


# -----------------------
# 3) 等待打榜算法交付 skill 包
# -----------------------
def _resolve_skill() -> Tuple[str, str]:
    """返回 (skill_convention, skill_path)：
      - "multi" : SKILL_DIR/skills/<name>/SKILL.md（xskill 约定，多 skill）
      - "single": SKILL_DIR 下单个 *.md（skillopt / trace2skill / noskill）
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


def _materialize_skill_file(skill_convention: str, skill_path: str) -> str:
    """把交付的 skill 整理成单个 .md 文件路径（eval_only.py 的 --skill 接收单文件）。
    ALFWorld rollout 把 skill 文本拼进 agent system prompt（reference），故多 skill 也合并成一个文本。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    if skill_convention == "multi":
        merged = os.path.join(OUT_DIR, "merged_skill.md")
        parts: List[str] = []
        for d in sorted(glob.glob(os.path.join(skill_path, "*"))):
            sk = os.path.join(d, "SKILL.md")
            if os.path.isfile(sk):
                parts.append(f"# Skill: {os.path.basename(d)}\n\n" + _read(sk))
        with open(merged, "w", encoding="utf-8") as f:
            f.write("\n\n---\n\n".join(parts))
        return merged
    return skill_path


# -----------------------
# 4) 准备 ALFWorld 运行期数据（展开 gamefile 为绝对路径 + 拷 logic 文件）
# -----------------------
def _prepare_alfworld_runtime() -> str:
    """生成运行期 split_dir：把 train/val/test 的 items.json 里相对 gamefile 展开为
    $ALFWORLD_DATA 下绝对路径。返回运行期 split_dir 路径。同时确保 logic 文件就位。"""
    if not os.path.isdir(ALFWORLD_DATA):
        raise RuntimeError(f"ALFWORLD_DATA 不存在：{ALFWORLD_DATA}（数据未下载/未挂载）")

    # 4a) logic 文件：config_tw.yaml 引用 $ALFWORLD_DATA/logic/{alfred.pddl,alfred.twl2}
    logic_dst = os.path.join(ALFWORLD_DATA, "logic")
    os.makedirs(logic_dst, exist_ok=True)
    for fn in ("alfred.pddl", "alfred.twl2"):
        dst = os.path.join(logic_dst, fn)
        if not os.path.isfile(dst):
            # 从 alfworld 包自带数据拷
            try:
                import alfworld.info as _info
                src = os.path.join(os.path.dirname(_info.__file__), "data", fn)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                    _log(f"copied logic {fn} -> {dst}")
            except Exception as e:
                _log(f"WARN: 无法拷 logic 文件 {fn}: {e}")

    # 4b) 运行期 split_dir：相对 gamefile -> 绝对。
    # SkillOpt 的 SplitDataLoader._load_all_splits 要求 train/ val/ test/ 三个子目录都在
    # （即使只评 test）。miniset 仅含 test/ 时，用 EVAL_SPLIT 的 items 作为占位填充缺失 split，
    # 保证 loader 能加载；实际只评 --split EVAL_SPLIT。
    run_split = os.path.join(OUT_DIR, "split_runtime")

    def _expand(items: List[dict]) -> List[dict]:
        out = []
        for it in items:
            row = dict(it)
            gf = str(row.get("gamefile") or "")
            if gf and not os.path.isabs(gf):
                row["gamefile"] = os.path.join(ALFWORLD_DATA, gf)
            out.append(row)
        return out

    loaded: Dict[str, List[dict]] = {}
    for sp in ("train", "val", "test"):
        src_items = os.path.join(SPLIT_DIR, sp, "items.json")
        if os.path.isfile(src_items):
            loaded[sp] = _expand(json.load(open(src_items)))

    if EVAL_SPLIT not in loaded or not loaded.get(EVAL_SPLIT):
        raise RuntimeError(f"split_dir 缺少要评测的 split '{EVAL_SPLIT}'：{SPLIT_DIR}")

    # 缺失的 split 用 EVAL_SPLIT 的 items 占位（loader 需要三目录都在）。
    placeholder = loaded[EVAL_SPLIT]
    n_total = 0
    for sp in ("train", "val", "test"):
        items = loaded.get(sp) or placeholder
        n_total += len(items) if sp == EVAL_SPLIT else 0
        dst_dir = os.path.join(run_split, sp)
        os.makedirs(dst_dir, exist_ok=True)
        json.dump(items, open(os.path.join(dst_dir, "items.json"), "w"), ensure_ascii=False, indent=2)
    _log(f"prepared runtime split_dir {run_split} (eval split '{EVAL_SPLIT}': {n_total} items; "
         f"missing splits stubbed with placeholder)")
    return run_split


# -----------------------
# 5) 跑 SkillOpt eval_only.py（env=alfworld，ReAct rollout）
# -----------------------
def _run_eval(skill_file: str, run_split_dir: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    eval_out = os.path.join(OUT_DIR, "run")
    os.makedirs(eval_out, exist_ok=True)

    env = os.environ.copy()
    env["ALFWORLD_DATA"] = ALFWORLD_DATA
    env["PYTHONPATH"] = SKILLOPT_DIR + ":" + env.get("PYTHONPATH", "")
    # 子进程式 env worker（textworld 单进程更稳）
    env.setdefault("ALFWORLD_WORKER_START_METHOD", "spawn")

    cfg_opts = [
        "env.name=alfworld",
        "env.max_steps=" + MAX_STEPS,
        "env.split_mode=split_dir",
        "env.workers=" + WORKERS,
        "env.max_api_workers=" + WORKERS,
    ]

    cmd = [EVAL_PY, "scripts/eval_only.py",
           "--config", "configs/alfworld/default.yaml",
           "--skill", skill_file,
           "--split", EVAL_SPLIT, "--split_mode", "split_dir",
           "--split_dir", run_split_dir,
           "--workers", WORKERS, "--seed", "42", "--out_root", eval_out,
           "--target_backend", "openai_chat", "--target_model", EVAL_MODEL,
           "--target_azure_openai_endpoint", TARGET_ENDPOINT,
           "--target_azure_openai_api_key", TARGET_API_KEY,
           "--target_azure_openai_auth_mode", "openai_compatible",
           "--cfg-options", *cfg_opts]

    _log(f"Running eval_only.py (alfworld): {' '.join(cmd)} (cwd={SKILLOPT_DIR})")
    proc = subprocess.Popen(cmd, env=env, cwd=SKILLOPT_DIR, text=True)
    proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"eval_only.py 退出码非 0：{proc.returncode}")
    return eval_out


# -----------------------
# 6) 解析结果 → metrics + eval_details（每题 success/fail + 动作/观测轨迹）
# -----------------------
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

    n = len(rows)
    n_pass = sum(1 for r in rows if int(r.get("hard", 0)) == 1)
    hard = float(summ.get("hard", (n_pass / n if n else 0.0)))
    soft = float(summ.get("soft", hard))
    score = round(_clamp(hard * 100.0, 0.0, 100.0), 4)

    eval_details: List[Dict[str, Any]] = []
    for r in rows:
        tid = str(r.get("id"))
        passed = int(r.get("hard", 0)) == 1
        fail_reason = r.get("fail_reason") or ""
        # 读轨迹（rollout 落盘的 conversation.json）
        conv_path = os.path.join(out_dir, "predictions", tid, "conversation.json")
        trajectory: List[Dict[str, Any]] = []
        try:
            conv = json.load(open(conv_path))
            for step in conv:
                trajectory.append({
                    "step": step.get("step"),
                    "action": step.get("action"),
                    "reasoning": (step.get("reasoning") or "")[:1500],
                    "observation": (step.get("env_feedback") or "")[:2000],
                    "reward": step.get("reward"),
                    "done": step.get("done"),
                })
        except Exception:
            pass

        extra = {
            "task_type": r.get("task_type") or r.get("instruction_type"),
            "gamefile": r.get("gamefile"),
            "n_turns": r.get("n_turns"),
            "max_steps": int(MAX_STEPS),
            "fail_reason": fail_reason,
            "won": passed,
            "model": EVAL_MODEL,
            "skill_convention": skill_convention,
            "task_description": (r.get("task_description") or "")[:1000],
            "trajectory": trajectory,
        }
        eval_details.append({
            "question_id": tid,
            "question": (r.get("task_description") or r.get("task_type") or "")[:2000],
            "gold_answer": f"task_type={r.get('task_type')} (ALFWorld goal completion)",
            "pred_answer": ("SUCCESS" if passed else "FAIL")
                           + f" | steps={r.get('n_turns', 0)}/{MAX_STEPS}"
                           + (f" | {fail_reason}" if not passed and fail_reason else ""),
            "is_correct": bool(passed),
            "latency_ms": 0.0,
            "used_tokens": 0,
            "retrieved": int(r.get("n_turns") or 0),
            "eval_prompt": "ALFWorld goal-completion (won flag) / ReAct rollout",
            "extra": extra,
        })

    metrics = {
        "score": score,
        "success_rate_pct": score,
        "hard": hard,
        "soft": soft,
        "n_tasks": n,
        "n_pass": n_pass,
        "n_fail": n - n_pass,
        "max_steps": int(MAX_STEPS),
        "model": EVAL_MODEL,
        "skill_convention": skill_convention,
        "split": EVAL_SPLIT,
        "dataset": "ALFWorld (TextWorld, path_split)",
    }
    return metrics, eval_details


# -----------------------
# 7) 主函数
# -----------------------
def main():
    if not SUBMISSION_ID:
        raise RuntimeError("Missing SUBMISSION_ID env")
    _log(f"Start ALFWorld eval: submission={SUBMISSION_ID}, leaderboard={LEADERBOARD_ID}")
    _log(f"EVAL_MODEL={EVAL_MODEL}  SPLIT_DIR={SPLIT_DIR}  ALFWORLD_DATA={ALFWORLD_DATA}  MAX_STEPS={MAX_STEPS}")

    conv, skill_path = _wait_for_skill()
    skill_file = _materialize_skill_file(conv, skill_path)
    run_split_dir = _prepare_alfworld_runtime()
    out_dir = _run_eval(skill_file, run_split_dir)
    metrics, eval_details = _build_metrics_and_details(out_dir, conv)

    # 榜单自定义结果视图：镜像里若 baked /app/result_view.html 就随 /score 回传。
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
