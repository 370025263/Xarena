#!/usr/bin/env python3.11
"""生产 TaskAgent 评测 runner —— 端到端驱动真实拆分器,喂评测器。

跟 ``run_baseline.py`` 的根本区别
==================================
``run_baseline.py`` **不调 LLM**:合成集吃离线预测 ``v2_window.json``、真实集
吃本机已落盘 atom 库,纯复算指标,做"第一遍基线"。

本 runner **真的把生产拆分器跑起来**:对每条 case 构造一个真实
``TaskAgent``(``src/xskill/agents/task_agent.py``,不改它),用 deepseek-v4-flash
现跑 ``run()`` 产新 atom,再把 ``boundaries`` / ``covered_eof`` 喂进
``evaluate.aggregate``。这样证明 harness 能端到端驱动**生产代码**而非离线产物——
将来换拆分机制(window → user-turn split)只要改 ``task_agent.py``,本 runner
原样复用,指标可直接对比。

坐标系
======
- 合成 atom 的 ``offset_start`` 是 **1-based 行号**(``TaskAgent`` 的坐标系)。
- 评测器吃的也是行号边界。所以本 runner 直接用新 atom 的 ``offset_start``:
    ``boundaries = sorted(offset_start)[1:]``  (去掉被迫的首起点)
    ``covered_eof = max(offset_end) >= total_lines + 1``  (末 atom 半开区间到 EOF+1)
- **绝不复用存量 atom**:存量真实 atom 可能是字符偏移坐标系(旧机制),本 runner
  对每条 case 用 fresh store 现跑拆分器产新 atom,只信新代码的坐标系。

数据源
======
① 合成(``--dataset synthetic``):读 ``synthetic/data/c*.md``(+ sidecar .json),
  每条 case 拷进各自的 fresh 临时 store 后跑 ``TaskAgent.run()``。
② 真实(``--dataset real``):从本机 ``~/.xskill/<sub>/<traj_id>.md`` 读原文
  (运行时读,绝不入库),同样 fresh store 现跑。

反假实现
========
本 runner **不硬编码任何期望边界**。预测全部来自 ``TaskAgent.run()`` 的真实
落盘 atom;指标全部走 ``evaluate.aggregate``。runner 里没有任何 ground-truth
行号常量。

成本控制
========
- ``--limit N``:只跑前 N 条 case。
- ``--cases id1,id2``:只跑指定 case(合成用 case_id,真实用 traj_id)。
- ``--dataset``:synthetic / real,默认 synthetic。
- 默认 deepseek-v4-flash;agno Agent 开 ``retries=3, exponential_backoff=True``
  防限流静默空。
- 每条 case 跑完检查 ``run()`` 有没有抛错;抛了就在该 case 记 ``error`` 字段
  (评测器把 error 计入 errors 计数),不静默吞。

用法
====
    # 合成集全 15 条(真的调 deepseek,会花钱)
    python3.11 scripts/bench/run_splitter.py --dataset synthetic

    # 先 smoke 2 条控成本
    python3.11 scripts/bench/run_splitter.py --dataset synthetic --limit 2

    # 真实集 smoke(只跑指定的小 traj;真实标注与原文仅本机,不入库)
    python3.11 scripts/bench/run_splitter.py --dataset real \\
        --cases <traj_id_1>,<traj_id_2>

    # 写机器可读结果
    python3.11 scripts/bench/run_splitter.py --dataset synthetic --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

# bench 目录自身在 sys.path 上(evaluate 是同目录平铺模块)。
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import evaluate as E  # noqa: E402

# 把 src/ 挂上去,直接 import 生产代码(不装包也能跑)。
REPO_ROOT = HERE.parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xskill.agents.agno_factory import build_chat_model  # noqa: E402
from xskill.agents.task_agent import TaskAgent  # noqa: E402
from xskill.pipeline.atom import AtomTaskStore  # noqa: E402

SYNTH_GT = HERE / "synthetic" / "ground_truth.json"
SYNTH_DATA = HERE / "synthetic" / "data"
REAL_ANN = HERE / "real" / "annotations.json"
XSKILL_ROOT = Path(os.path.expanduser("~/.xskill"))
SESSION_SUBDIRS = ("cc_sessions", "opencode_sessions",
                   "codex_sessions", "openclaw_sessions")
AIKEY_FILE = Path(os.path.expanduser("~/.aikey"))

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"


# --------------------------------------------------------------------------- #
# deepseek 工厂                                                                 #
# --------------------------------------------------------------------------- #
def _read_deepseek_key() -> str:
    """从 ``~/.aikey`` 正则抠 ``DEEPSEEK_API_KEY``;缺则报错(不写 fallback)。"""
    if not AIKEY_FILE.is_file():
        raise FileNotFoundError(f"缺 {AIKEY_FILE};需含 DEEPSEEK_API_KEY")
    text = AIKEY_FILE.read_text(encoding="utf-8")
    m = re.search(r"^DEEPSEEK_API_KEY\s*=\s*(\S+)", text, re.MULTILINE)
    if not m:
        raise ValueError(f"{AIKEY_FILE} 里没找到 DEEPSEEK_API_KEY")
    return m.group(1).strip()


def make_deepseek_factory():
    """造一个 agno Agent 工厂,签名 ``(*, instructions, tools) -> Agent``。

    用 ``build_chat_model`` 走 DeepSeek 子类(reasoning_content round-trip 必需),
    base_url=api.deepseek.com,model=deepseek-v4-flash。Agent 开 retries=3 +
    exponential_backoff,免得限流时工具调用静默返回空 submitted。
    """
    from agno.agent import Agent

    llm_cfg = {
        "base_url": DEEPSEEK_BASE_URL,
        "model": DEEPSEEK_MODEL,
        "api_key": _read_deepseek_key(),
    }

    def factory(*, instructions, tools, **kwargs):
        model = build_chat_model(llm_cfg)
        return Agent(
            model=model,
            instructions=instructions,
            tools=tools,
            system_message_role="system",
            markdown=True,
            retries=3,
            exponential_backoff=True,
            **kwargs,
        )

    return factory


# --------------------------------------------------------------------------- #
# 单条 case 跑拆分器 -> 预测                                                     #
# --------------------------------------------------------------------------- #
def _atoms_to_prediction(atoms: list, total_lines: int) -> dict:
    """把一条 case 新产 atom 列折成评测器吃的预测 dict。

    ``boundaries = sorted(offset_start)[1:]``(去掉被迫首起点);
    ``covered_eof`` 用末 atom 半开区间是否摸到 EOF 判定:末 atom 的
    ``offset_end`` 是末行号+1,故覆盖判据是 ``max(offset_end) >= total_lines+1``。
    无 atom 时 boundaries 空、covered_eof False。
    """
    if not atoms:
        return {"boundaries": [], "covered_eof": False, "n_atoms": 0,
                "error": None}
    starts = sorted(a.offset_start for a in atoms)
    max_end = max(a.offset_end for a in atoms)
    return {
        "boundaries": starts[1:],
        "covered_eof": max_end >= total_lines + 1,
        "n_atoms": len(atoms),
        "error": None,
    }


def run_one_case(factory, *, traj_id: str, src_md: Path,
                 total_lines: int) -> dict:
    """对一条轨迹现跑生产 TaskAgent,返回评测器预测 dict。

    每条 case 用**独立临时 store**(fresh,不污染真实 ~/.xskill,不复用存量
    atom)。把源 .md(+ 同名 sidecar .json,若有)拷进临时根作为
    ``<traj_id>.md``,构造 TaskAgent 后 ``run()``,从 store 收新 atom 折预测。

    ``run()`` 抛错时返回带 ``error`` 字段的预测(评测器计入 errors),不吞错。
    """
    tmp_root = Path(tempfile.mkdtemp(prefix=f"splitter_{traj_id}_"))
    try:
        staged_md = tmp_root / f"{traj_id}.md"
        shutil.copyfile(src_md, staged_md)
        sidecar = src_md.with_suffix(".json")
        if sidecar.is_file():
            shutil.copyfile(sidecar, staged_md.with_suffix(".json"))

        store = AtomTaskStore(root=tmp_root)
        # skill_dir 给个临时空目录满足白名单契约(本评测不读 skill)。
        skill_dir = tmp_root / "_skills"
        skill_dir.mkdir(exist_ok=True)
        agent = TaskAgent(
            agno_agent_factory=factory,
            store=store,
            traj_root=tmp_root,
            skill_dir=skill_dir,
        )
        try:
            agent.run(traj_id=traj_id, traj_path=staged_md)
        except Exception as exc:  # noqa: BLE001 — 把真实错误透传给评测器
            return {"boundaries": [], "covered_eof": False, "n_atoms": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc()}
        atoms = store.list_by_traj(traj_id)
        return _atoms_to_prediction(atoms, total_lines)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 合成集                                                                        #
# --------------------------------------------------------------------------- #
def _synth_cases() -> dict:
    """{case_id: {"src_md": Path, "total_lines": int}}——读 ground_truth + data。"""
    gt = json.loads(SYNTH_GT.read_text(encoding="utf-8"))
    cases = {}
    for case_id, meta in gt.items():
        md = SYNTH_DATA / f"{case_id}.md"
        if not md.is_file():
            raise FileNotFoundError(f"缺合成轨迹 {md}")
        cases[case_id] = {"src_md": md, "total_lines": meta["total_lines"]}
    return cases


def run_synthetic(factory, *, limit: int | None,
                  only: set[str] | None) -> tuple[dict, dict]:
    """跑合成集,返回 (preds, ground)。"""
    cases = _synth_cases()
    ground = json.loads(SYNTH_GT.read_text(encoding="utf-8"))
    ids = _select_ids(sorted(cases), limit=limit, only=only)
    preds = {}
    used_ground = {}
    for case_id in ids:
        c = cases[case_id]
        print(f"[synthetic] running {case_id} ...", flush=True)
        preds[case_id] = run_one_case(
            factory, traj_id=case_id, src_md=c["src_md"],
            total_lines=c["total_lines"])
        used_ground[case_id] = ground[case_id]
        _print_case_line(case_id, preds[case_id])
    return preds, used_ground


# --------------------------------------------------------------------------- #
# 真实集                                                                        #
# --------------------------------------------------------------------------- #
def _find_real_md(traj_id: str) -> Path:
    for sub in SESSION_SUBDIRS:
        md = XSKILL_ROOT / sub / f"{traj_id}.md"
        if md.is_file():
            return md
    raise FileNotFoundError(
        f"找不到真实 traj 源 .md: {traj_id}(查过 {SESSION_SUBDIRS})")


def run_real(factory, *, limit: int | None,
             only: set[str] | None) -> tuple[dict, dict]:
    """跑真实集(从 ~/.xskill 现读现拆),返回 (preds, ground)。

    ground 从 annotations.json 转成评测器 schema(boundaries/scenario/total_lines)。
    真实原文绝不入库:只在运行时从本机读,跑完临时 store 即删。
    """
    ann = json.loads(REAL_ANN.read_text(encoding="utf-8"))
    # annotations key 带 .md 后缀;归一成 traj_id。
    norm = {}
    for fname, meta in ann.items():
        tid = fname[:-3] if fname.endswith(".md") else fname
        norm[tid] = meta
    ids = _select_ids(sorted(norm), limit=limit, only=only)
    preds, ground = {}, {}
    for traj_id in ids:
        meta = norm[traj_id]
        md = _find_real_md(traj_id)
        total_lines = meta["lines"]
        print(f"[real] running {traj_id} (lines={total_lines}) ...", flush=True)
        preds[traj_id] = run_one_case(
            factory, traj_id=traj_id, src_md=md, total_lines=total_lines)
        scen = (meta.get("scenarios") or ["UNKNOWN"])[0]
        ground[traj_id] = {"boundaries": meta["boundaries"],
                           "scenario": scen, "total_lines": total_lines}
        _print_case_line(traj_id, preds[traj_id])
    return preds, ground


# --------------------------------------------------------------------------- #
# 选择 / 打印                                                                    #
# --------------------------------------------------------------------------- #
def _select_ids(all_ids: list[str], *, limit: int | None,
                only: set[str] | None) -> list[str]:
    if only:
        missing = only - set(all_ids)
        if missing:
            raise KeyError(f"--cases 里有未知 id: {sorted(missing)}")
        ids = [i for i in all_ids if i in only]
    else:
        ids = list(all_ids)
    if limit is not None:
        ids = ids[:limit]
    return ids


def _print_case_line(case_id: str, pred: dict) -> None:
    if pred.get("error"):
        print(f"    {case_id}: ERROR {pred['error']}", flush=True)
    else:
        print(f"    {case_id}: n_atoms={pred['n_atoms']} "
              f"boundaries={pred['boundaries']} "
              f"covered_eof={pred['covered_eof']}", flush=True)


# --------------------------------------------------------------------------- #
# main                                                                          #
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", choices=("synthetic", "real"),
                    default="synthetic")
    ap.add_argument("--limit", type=int, default=None,
                    help="只跑前 N 条 case(控成本)")
    ap.add_argument("--cases", type=str, default=None,
                    help="逗号分隔的 case id(合成用 case_id,真实用 traj_id)")
    ap.add_argument("--tol", type=int, default=None,
                    help="边界匹配容差;默认合成 0、真实 5")
    ap.add_argument("--json", type=Path, default=None,
                    help="把机器可读结果写到此路径")
    args = ap.parse_args()

    only = (set(s.strip() for s in args.cases.split(",") if s.strip())
            if args.cases else None)

    factory = make_deepseek_factory()

    if args.dataset == "synthetic":
        preds, ground = run_synthetic(factory, limit=args.limit, only=only)
        tol = 0 if args.tol is None else args.tol
    else:
        preds, ground = run_real(factory, limit=args.limit, only=only)
        tol = 5 if args.tol is None else args.tol

    agg_strict = E.aggregate(preds, ground, tol=0)
    agg_tol = E.aggregate(preds, ground, tol=tol)

    result = {
        "mechanism": "PRODUCTION TaskAgent (live deepseek-v4-flash)",
        "dataset": args.dataset,
        "model": DEEPSEEK_MODEL,
        "llm_used": True,
        "n_cases": len(preds),
        "tol": tol,
        "strict_tol0": agg_strict["overall"],
        f"tolerant_tol{tol}": agg_tol["overall"],
        "by_scenario_strict": agg_strict["by_scenario"],
        "per_case": preds,
    }
    print("\n==== aggregate ====")
    print(json.dumps({k: v for k, v in result.items() if k != "per_case"},
                     ensure_ascii=False, indent=2))
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"\n[written] {args.json}")


if __name__ == "__main__":
    main()
