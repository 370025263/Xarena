"""
types.py — SDK dataclass 集中地
═══════════════════════════════════════
所有跨模块共享的 dataclass。**只放数据，不放行为。**
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:  # 避免循环 import
    from xskill.skill.skill import Skill
    from xskill.pipeline.trajectory import Trajectory


# ─── Registry / Watch ─────────────────────────────────────────────
@dataclass
class WatchDir:
    id: int
    path: Path
    label: str
    auto_index: bool
    traj_count: int
    indexed_count: int
    # 来源标签：``manual`` = 用户手动 ``xskill registry add``；其他如
    # ``claude_code`` = ``xskill serve`` 启动时自动 detect+register 的桥接
    # 目录。同时用 codex / opencode 时按这个字段区分聚合。
    ecosystem: str = "manual"


# ─── Search ───────────────────────────────────────────────────────
@dataclass
class SkillHit:
    skill: "Skill"
    similarity: float


@dataclass
class TrajectoryHit:
    trajectory: "Trajectory"
    similarity: float


# ─── Skill candidates ─────────────────────────────────────────────
@dataclass
class Candidate:
    pattern: str
    kind: Literal["step", "warning", "decision_branch"]
    attach_to: Optional[str]
    supporting_trajs: list[str]
    first_seen: Optional[date]
    promoted: bool


# ─── UX Score ─────────────────────────────────────────────────────
@dataclass
class UxScoreResult:
    scored: bool                      # False = 已存在（幂等跳过）
    score: Optional[int]              # 1-10
    reasons: str
    decision: dict                    # canary.check_and_decide 输出
