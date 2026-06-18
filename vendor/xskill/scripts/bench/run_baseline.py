#!/usr/bin/env python3.11
"""第一遍基线 —— 现行窗机制(window mechanism)在合成集 + 真实集上的拆分质量。

不调用任何 LLM:基线全部用现存产物算。

合成集(15 条)
==============
窗机制预测已离线跑好,存放在 synthetic 旁的 v2_window.json(每条
{boundaries, covered_eof})。直接对 synthetic/ground_truth.json 算指标。
v2_window.json 不入库时缺失则报错(不写 fallback)。

真实集(21 条直采 + 3 条 low 退化样本单列)
==========================================
窗机制在真实轨迹上的"产物"就是现存 atom 库。对 real/annotations.json 里的
每条 traj,去本机 ~/.xskill 找它已落盘的 atom:
  <root>/<traj_id>/tasks/atom_*.json,字段 offset_start / offset_end。
注意:offset_* 是**字符偏移**,不是行号;annotations 的 boundaries 是**行号**。
所以必须读源 .md 把字符偏移换算成 1-based 行号:
  boundaries = sorted(line_of(offset_start))[1:]
  covered_eof = max(line_of(offset_end)) >= total_lines
真实轨迹原文绝不入库(隐私),只在运行时从本机读。

low 退化样本(confidence==low)不进主指标,单独列出。

用法:python3.11 run_baseline.py [--json OUT.json]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path

import evaluate as E

HERE = Path(__file__).resolve().parent
SYNTH_GT = HERE / "synthetic" / "ground_truth.json"
# 合成集窗机制离线预测(随 bench 入库,见 README "数据来源")。
SYNTH_PRED = HERE / "synthetic" / "v2_window.json"
REAL_ANN = HERE / "real" / "annotations.json"
XSKILL_ROOT = Path(os.path.expanduser("~/.xskill"))
# 不同客户端的 traj 落盘子目录(源 .md 与 atom 目录同放于此)。
SESSION_SUBDIRS = ("cc_sessions", "opencode_sessions",
                   "codex_sessions", "openclaw_sessions")


def _find_traj_md(traj_id: str) -> Path:
    for sub in SESSION_SUBDIRS:
        md = XSKILL_ROOT / sub / f"{traj_id}.md"
        if md.exists():
            return md
    raise FileNotFoundError(
        f"找不到 traj 源 .md: {traj_id}(查过 {SESSION_SUBDIRS})")


def _char_to_line(text: str, off: int) -> int:
    """字符偏移 -> 含该偏移的 1-based 行号。"""
    return text.count("\n", 0, off) + 1


def real_window_prediction(traj_id: str) -> dict:
    """从本机 atom 库还原窗机制对一条真实 traj 的拆分(边界 + EOF 覆盖)。"""
    md = _find_traj_md(traj_id)
    text = md.read_text(encoding="utf-8")
    total_lines = text.count("\n") + 1
    tasks_dir = md.parent / traj_id / "tasks"
    atoms = sorted(glob.glob(str(tasks_dir / "atom_*.json")))
    if not atoms:
        raise FileNotFoundError(f"无 atom 落盘: {tasks_dir}")
    starts, ends = [], []
    for a in atoms:
        j = json.loads(Path(a).read_text(encoding="utf-8"))
        starts.append(_char_to_line(text, j["offset_start"]))
        ends.append(_char_to_line(text, j["offset_end"]))
    boundaries = sorted(starts)[1:]
    covered_eof = max(ends) >= total_lines
    return {"boundaries": boundaries, "covered_eof": covered_eof,
            "n_atoms": len(atoms), "error": None}


def build_real_ground_and_preds() -> tuple[dict, dict, dict, dict]:
    """返回 (main_gt, main_preds, low_gt, low_preds)。

    main = confidence != low 的 21 条;low = 3 条退化样本。
    ground_truth 转成 evaluate.aggregate 期望的 schema
    (boundaries / scenario / total_lines)。
    """
    ann = json.loads(REAL_ANN.read_text(encoding="utf-8"))
    main_gt, main_preds, low_gt, low_preds = {}, {}, {}, {}
    for fname, meta in ann.items():
        traj_id = fname[:-3] if fname.endswith(".md") else fname
        scen = (meta.get("scenarios") or ["UNKNOWN"])[0]
        gt = {"boundaries": meta["boundaries"],
              "scenario": scen,
              "total_lines": meta["lines"]}
        pred = real_window_prediction(traj_id)
        if meta.get("confidence") == "low":
            low_gt[traj_id] = gt
            low_preds[traj_id] = pred
        else:
            main_gt[traj_id] = gt
            main_preds[traj_id] = pred
    return main_gt, main_preds, low_gt, low_preds


def _atom_totals(ann: dict, only_main: bool) -> tuple[int, int]:
    """返回 (标注理想 atom 数之和, 现存 atom 数之和),用于"漏拆"对照。"""
    ideal = current = 0
    for meta in ann.values():
        is_low = meta.get("confidence") == "low"
        if only_main and is_low:
            continue
        ideal += meta["atom_count"]
        current += meta["current_atoms"]
    return ideal, current


def main() -> None:
    """跑合成集 + 真实集第一遍基线,打印并可选写 JSON。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=None,
                    help="把机器可读基线结果写到此路径")
    args = ap.parse_args()

    # ---- 合成集 ----
    if not SYNTH_PRED.exists():
        raise FileNotFoundError(
            f"缺合成集窗机制预测 {SYNTH_PRED};见 README 数据来源说明")
    synth_gt = json.loads(SYNTH_GT.read_text(encoding="utf-8"))
    synth_pred = json.loads(SYNTH_PRED.read_text(encoding="utf-8"))
    synth_strict = E.aggregate(synth_pred, synth_gt, tol=0)
    synth_tol = E.aggregate(synth_pred, synth_gt, tol=2)

    # ---- 真实集 ----
    main_gt, main_preds, low_gt, low_preds = build_real_ground_and_preds()
    # 真实集人工标注边界 ±几行不确定,主报 tol=5 的宽容 F1(精确 tol=0 并列)。
    real_strict = E.aggregate(main_preds, main_gt, tol=0)
    real_tol = E.aggregate(main_preds, main_gt, tol=5)
    low_tol = E.aggregate(low_preds, low_gt, tol=5)

    ann = json.loads(REAL_ANN.read_text(encoding="utf-8"))
    ideal_main, cur_main = _atom_totals(ann, only_main=True)

    result = {
        "mechanism": "window (current production splitter)",
        "llm_used": False,
        "synthetic": {"n": synth_strict["overall"]["n"],
                      "strict_tol0": synth_strict["overall"],
                      "tolerant_tol2": synth_tol["overall"],
                      "by_scenario_strict": synth_strict["by_scenario"]},
        "real_main": {"n": real_strict["overall"]["n"],
                      "strict_tol0": real_strict["overall"],
                      "tolerant_tol5": real_tol["overall"],
                      "atoms_ideal_vs_current": [ideal_main, cur_main],
                      "by_scenario_tolerant": real_tol["by_scenario"]},
        "real_low_excluded": {"n": low_tol["overall"]["n"],
                              "tolerant_tol5": low_tol["overall"]},
        "per_traj_real": dict(main_preds.items()),
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"\n[written] {args.json}")


if __name__ == "__main__":
    main()
