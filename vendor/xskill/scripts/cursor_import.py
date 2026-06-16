#!/usr/bin/env python3
"""One-shot backfill: import Cursor agent-transcripts into xskill.

正常情况下 **不需要跑这个脚本** —— xskill daemon 启动时会 detect 到
``~/.cursor/projects/`` 并自动起 ``JsonlIngester(CURSOR_SPEC)`` 持续摄取新
session（同 CC/Codex/OpenCode/OpenClaw 4 家一样的体验）。

只在以下场景需要手动跑：
- 首次接入 xskill 想把**历史** Cursor transcripts 一次性灌进来（daemon 启动
  之后才出现的 session 会被自动接，但启动之前的老 session 不会主动回扫）
- 从非默认路径 ``--src`` 导入

可以直接 ``rm scripts/cursor_import.py`` 删掉这文件——所有功能都被 daemon
自动接管，这脚本只是个 historical-backfill 可选工具。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from xskill.adapters import submit_trajectory


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--src",
        type=Path,
        default=Path.home() / ".cursor" / "projects",
        help="Cursor projects root（递归扫 */agent-transcripts/*.jsonl）",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path.home() / ".xskill" / "cursor_sessions",
        help="xskill watch directory（traj_cursor_*.md 落盘位置；与 daemon 用同一个）",
    )
    args = p.parse_args()
    src = args.src.expanduser().resolve()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    jsonls = sorted(src.glob("*/agent-transcripts/*.jsonl"))
    if not jsonls:
        print(f"no */agent-transcripts/*.jsonl under {src}", file=sys.stderr)
        return 1

    for jsonl in jsonls:
        content = jsonl.read_text(encoding="utf-8", errors="ignore")
        sid = jsonl.stem
        result = submit_trajectory(
            content=content,
            format="cursor_transcripts_jsonl",  # 复用 adapter，跟 daemon 出的 md 一致
            metadata={"session_id": sid, "source_jsonl": str(jsonl)},
            traj_id=f"traj_cursor_unknown_{sid[:8]}",
            traj_dir=out,
        )
        print(f"imported {jsonl.name} -> {result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
