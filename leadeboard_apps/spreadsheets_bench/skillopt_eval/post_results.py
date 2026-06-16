#!/usr/bin/env python3
"""
Spreadsheet leaderboard — results poster.

Parses a SkillOpt single-mode eval output dir (results.jsonl + eval_summary.json
+ predictions/<id>/...) and POSTs a score + per-task eval_details back to the
compass/指北针 backend via the internal score endpoint.

Per the bench readme, each task detail carries: task.md, input/output xlsx info,
the chat-message log, the produced solution, and a pass Yes/No.

Env:
  SUBMISSION_ID      (required) target submission id
  API_INTERNAL_URL   backend base url (default http://localhost:7789)
  OUT_DIR            (required) eval output dir
  SPLIT_DIR          reduced split dir (for gold answer_position lookup)
  SKILL_ALGO         label (skillopt|trace2skill|xskill|noskill)
  EVAL_MODE          label (default "single")
  EVAL_MODEL         label (default "deepseek-v4-flash")
"""
import os, json, sys, urllib.request

SUBMISSION_ID = os.environ.get("SUBMISSION_ID")
API = os.environ.get("API_INTERNAL_URL", "http://localhost:7789").rstrip("/")
OUT_DIR = os.environ.get("OUT_DIR") or (sys.argv[1] if len(sys.argv) > 1 else None)
SPLIT_DIR = os.environ.get("SPLIT_DIR", "")
SKILL_ALGO = os.environ.get("SKILL_ALGO", "unknown")
EVAL_MODE = os.environ.get("EVAL_MODE", "single")
EVAL_MODEL = os.environ.get("EVAL_MODEL", "deepseek-v4-flash")

if not SUBMISSION_ID or not OUT_DIR:
    print("ERROR: SUBMISSION_ID and OUT_DIR required", file=sys.stderr)
    sys.exit(2)


def _read(path, limit=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
        return data[:limit] if limit else data
    except Exception:
        return ""


def _gold_positions():
    pos = {}
    try:
        items = json.load(open(os.path.join(SPLIT_DIR, "test", "items.json")))
        for it in items:
            pos[str(it["id"])] = it.get("answer_position", "")
    except Exception:
        pass
    return pos


def main():
    summ_path = os.path.join(OUT_DIR, "eval_summary.json")
    res_path = os.path.join(OUT_DIR, "results.jsonl")
    summary = json.load(open(summ_path)) if os.path.exists(summ_path) else {}
    rows = []
    if os.path.exists(res_path):
        for line in open(res_path):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass

    gold = _gold_positions()
    n = len(rows)
    n_pass = sum(1 for r in rows if int(r.get("hard", 0)) == 1)
    hard = summary.get("hard", (n_pass / n if n else 0.0))
    soft = summary.get("soft", 0.0)
    score = round(float(hard) * 100.0, 2)

    eval_details = []
    for r in rows:
        tid = str(r.get("id"))
        pdir = os.path.join(OUT_DIR, "predictions", tid)
        cdir = os.path.join(pdir, "codex_single")
        passed = int(r.get("hard", 0)) == 1
        task_md = _read(os.path.join(cdir, "task.md"), 6000)
        solution = _read(os.path.join(cdir, "solution.py"), 8000)
        chat = _read(os.path.join(pdir, "conversation.json"), 8000) \
            or _read(os.path.join(pdir, "claude_raw.txt"), 8000)
        extra = {
            "instruction_type": r.get("instruction_type"),
            "n_turns": r.get("n_turns"),
            "n_pass": r.get("n_pass"),
            "n_cases": r.get("n_cases"),
            "fail_reason": r.get("fail_reason") or r.get("error") or "",
            "skill_algo": SKILL_ALGO,
            "mode": EVAL_MODE,
            "model": EVAL_MODEL,
            "task_md": task_md,
            "chatmessage_log": chat,
            "solution_py": solution,
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
                           + (f" | {extra['fail_reason']}" if not passed and extra["fail_reason"] else ""),
            "is_correct": passed,
            "latency_ms": 0.0,
            "used_tokens": 0,
            "retrieved": int(r.get("n_turns") or 0),
            "eval_prompt": "single-mode claude_code_exec / SpreadsheetBench hard-match",
            "extra": extra,
        })

    payload = {
        "status": "Succeeded",
        "score": score,
        "metrics": {
            "score": score,
            "pass_rate_pct": score,
            "hard": float(hard),
            "soft": float(soft),
            "n_tasks": n,
            "n_pass": n_pass,
            "n_fail": n - n_pass,
            "mode": EVAL_MODE,
            "model": EVAL_MODEL,
            "skill_algo": SKILL_ALGO,
            "split": "test_10pct (28 tasks, 1/10 of SpreadsheetBench test280)",
        },
        "eval_details": eval_details,
    }

    url = f"{API}/api/internal/submission/{SUBMISSION_ID}/score"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    print(f"POST {url}  score={score}  n={n} pass={n_pass}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print("backend response:", resp.status, resp.read(300).decode("utf-8", "replace"))
    except Exception as e:
        print("POST failed:", e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
