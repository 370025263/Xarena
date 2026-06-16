"""
skill/repo.py — SkillRepo 集合 + 集合级 git 操作
═══════════════════════════════════════════════════
管理 ~/.xskill/skill/ 下所有 Skill 子目录。dict-like + iterable。
顶层 .git 已废弃，所有 git 操作走 <skill>/.git 子仓。

模块函数 ``list_skills`` / ``import_skill`` 是对整个 skill 仓库（集合）的
操作（原 skill_manager.py 的集合部分）。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from xskill.skill.skill import Skill, _load_skill
from xskill.skill.git import commit_changes

if TYPE_CHECKING:
    from xskill.pipeline.registry import Registry

logger = logging.getLogger("skill_manager")


class SkillRepo:
    """skill_dir 顶层视图。

    接口：
      repo["foo"]            → Skill | KeyError
      "foo" in repo          → bool
      for s in repo: ...     → 迭代所有 Skill
      len(repo)              → int
      repo.get("foo")        → Skill | None
      repo.rebuild_index()   → 重建 .skill_index.pkl
    """

    def __init__(self, root: Path, registry: Optional["Registry"] = None):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self._registry = registry

    # ─── dict-like ─────────────────────────────────────────────
    def __getitem__(self, name: str) -> Skill:
        skill_path = self.root / name
        if not (skill_path / "SKILL.md").is_file():
            raise KeyError(f"skill not found: {name}")
        return Skill(path=skill_path, registry=self._registry)

    def get(self, name: str) -> Optional[Skill]:
        try:
            return self[name]
        except KeyError:
            return None

    def __contains__(self, name: str) -> bool:
        return (self.root / name / "SKILL.md").is_file()

    def __iter__(self) -> Iterator[Skill]:
        if not self.root.is_dir():
            return iter([])
        for sub in sorted(self.root.iterdir()):
            if not sub.is_dir():
                continue
            if sub.name.startswith(".") or sub.name == "references":
                continue
            if not (sub / "SKILL.md").is_file():
                continue
            yield Skill(path=sub, registry=self._registry)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    # ─── 索引 ──────────────────────────────────────────────────
    def rebuild_index(self) -> None:
        """重建 .skill_index.pkl（向量检索用）。

        显式给 ``rebuild_skill_index`` 传参，**不**走 ``init_context``——后者
        会要求 ``data_dir`` / ``llm_client`` 等本路径用不到的字段（早先版本
        传 ``None`` 进去会触发 ``Path(None)`` TypeError，rebuild 直接挂）。
        """
        from xskill.agents.skill_tools import rebuild_skill_index
        from xskill.config import get_config
        from xskill.utils.llm import create_embed_client
        embed = create_embed_client(get_config())
        rebuild_skill_index(skill_dir=self.root, embed_client=embed)

    def __repr__(self) -> str:
        return f"SkillRepo({self.root}, n={len(self)})"


# ═══════════════════════════════════════════════════════════════════
# 集合级 git 操作（原 skill_manager.py 集合部分）
# ═══════════════════════════════════════════════════════════════════


def list_skills(skill_dir: Path) -> list[dict]:
    """List all skills with v2 metadata. Legacy skills are surfaced via the
    synthesized frontmatter in _load_skill."""
    results = []
    if not skill_dir.exists():
        return results

    for d in sorted(skill_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        # Skip scaffold dirs without SKILL.md (gate-rejected, only have .candidates.yml)
        if not (d / "SKILL.md").is_file() and not (d / "skill.md").is_file():
            continue

        fm, _body, _p = _load_skill(d)
        meta = fm.get("metadata", {}) or {}
        eval_block = meta.get("eval", {}) or {}
        entry = {
            "name": d.name,
            "version": int(meta.get("version", 0) or 0),
            "eval_score": eval_block.get("eval_score") or eval_block.get("score"),
            "tags": meta.get("tags", []) or [],
            "frozen": bool(meta.get("frozen", False)),
        }
        results.append(entry)

    return results


def import_skill(skill_dir: Path, source_path: Path) -> str:
    """Copy a skill directory into ./skill/ and commit."""
    source = Path(source_path)
    if not source.is_dir():
        raise FileNotFoundError(f"source not found: {source}")

    name = source.name
    target = skill_dir / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)

    commit_changes(str(skill_dir), f"import skill: {name}")
    logger.info(f"imported: {name}")
    return name
