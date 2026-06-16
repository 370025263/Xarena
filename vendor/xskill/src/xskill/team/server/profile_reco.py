"""profile_reco.py — team server 的「画像推荐」(SP3)

`build_manifest` 的 100 个 slot 里后 20 个是 ``recommended`` bucket。SP1 时
这 20 个只是按 ux 往下接着取的占位；SP3 把它们换成**基于该 client 画像的
推荐**。

画像 = 该 client 用过的 skill 的质心
─────────────────────────────────────
- server 自己算，不改协议、client 不上报。
- client 上传的轨迹落在 ``<traj_root>/clients/<client_id>/sessions/``，watcher
  把它们拆成 ``AtomTask``（每个有 ``used_skills``）。
- 该 client 用过的 skill 集合 = 其名下所有 atom 的 ``used_skills`` 并集。
- 质心 = 这些 skill 在 ``.skill_index.pkl`` 里 embedding 行的均值，再 L2 归一。

推荐
────
候选 = distributable skill 里**不在 ranked-80、且 client 没用过**的。候选按
与质心的 cosine 相似度降序，取前 N 个。

惰性缓存
────────
每个 client 缓存「质心向量 + 算它时的 atom 集指纹」。指纹便宜——该 client
名下 traj 子目录数 + 各 traj 的 atom 数。sync 时指纹不变则复用质心，变了
重算。这是 memoization 缓存（可随时丢弃重建），不是账本——``skill_manifest``
不存账本的约定不受影响。

冷启动
──────
client 没有任何带 ``used_skills`` 的 atom（没质心）→ 没有画像。这不是
fallback，是"画像不存在"这一情形的正确定义：调用方据此退回 ux 排序。
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np

from xskill.pipeline.atom import AtomTaskStore


class _ProfileCacheEntry:
    """一个 client 的画像缓存条目：质心向量 + 算它时的 atom 集指纹。"""

    __slots__ = ("fingerprint", "centroid", "used_skills")

    def __init__(self, fingerprint: tuple, centroid: np.ndarray | None,
                 used_skills: frozenset[str]):
        self.fingerprint = fingerprint
        self.centroid = centroid          # None = 冷启动（该 client 无画像）
        self.used_skills = used_skills


class ClientProfileRecommender:
    """为 team server 的 ``recommended`` bucket 算基于 client 画像的推荐。

    一个 server 进程持有一个实例（模块级单例 :data:`RECOMMENDER`）。内部维护
    ``client_id -> _ProfileCacheEntry`` 的 memoization 缓存。
    """

    def __init__(self) -> None:
        self._cache: dict[str, _ProfileCacheEntry] = {}

    # ── 公开 API ───────────────────────────────────────────────────

    def used_skills_for(self, *, client_id: str, traj_root: Path) -> frozenset[str]:
        """该 client 用过的 skill 集合（其名下所有 atom 的 used_skills 并集）。

        惰性：指纹不变则直接返回缓存值，不重新扫 atom store。
        """
        entry = self._entry(client_id=client_id, traj_root=traj_root,
                            skill_index=None)
        return entry.used_skills

    def recommend(
        self,
        *,
        client_id: str,
        traj_root: Path,
        skill_index_path: Path,
        candidate_names: list[str],
        limit: int,
    ) -> list[str] | None:
        """为 ``client_id`` 推荐 ≤ ``limit`` 个 skill 名。

        ``candidate_names``：调用方已筛好的候选（distributable 且不在
        ranked-80）。本方法在此基础上再排除该 client 已用过的，并按与质心的
        cosine 相似度降序取前 ``limit``。

        返回 ``None`` 表示**该 client 没有画像**（冷启动）——调用方据此退回
        ux 排序。返回 list（可能为空）表示有画像、已算出推荐。
        """
        skill_index = _load_skill_index(skill_index_path)
        entry = self._entry(client_id=client_id, traj_root=traj_root,
                            skill_index=skill_index)
        if entry.centroid is None:
            return None  # 冷启动：无画像

        names = skill_index["skill_names"]
        embeddings = skill_index["embeddings"]
        name_to_row = {n: i for i, n in enumerate(names)}

        scored: list[tuple[float, str]] = []
        for name in candidate_names:
            if name in entry.used_skills:
                continue  # client 已用过的不推荐
            row = name_to_row.get(name)
            if row is None:
                continue  # 该 skill 不在向量索引里，无法算相似度
            sim = float(embeddings[row] @ entry.centroid)
            scored.append((sim, name))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [name for _sim, name in scored[:limit]]

    # ── 内部 ───────────────────────────────────────────────────────

    def _entry(
        self,
        *,
        client_id: str,
        traj_root: Path,
        skill_index: dict | None,
    ) -> _ProfileCacheEntry:
        """取/建该 client 的缓存条目。指纹变了则重算质心。

        ``skill_index`` 为 None 时只算 used_skills（不需质心），此时若缓存
        缺质心也不强求——``recommend`` 会带着 index 再调一次补算。
        """
        store_root = self._client_store_root(traj_root, client_id)
        fp = _fingerprint(store_root)
        cached = self._cache.get(client_id)
        if cached is not None and cached.fingerprint == fp:
            if skill_index is None or cached.centroid is not None \
                    or not cached.used_skills:
                # 缓存有效：质心已算 / 无需质心 / 冷启动（used_skills 空）
                return cached

        used = _collect_used_skills(store_root)
        centroid = None
        if used and skill_index is not None:
            centroid = _centroid(used, skill_index)
        entry = _ProfileCacheEntry(fingerprint=fp, centroid=centroid,
                                   used_skills=used)
        self._cache[client_id] = entry
        return entry

    @staticmethod
    def _client_store_root(traj_root: Path, client_id: str) -> Path:
        """该 client 的 AtomTaskStore root。

        client 上传的轨迹落在 ``<traj_root>/clients/<client_id>/sessions/``
        （见 ``team/server/api.py`` 的 ``team_upload``），watcher 以该目录为
        watch_dir、把每条 traj 拆成 ``<sessions>/<traj_id>/tasks/atom_*.json``。
        """
        return traj_root / "clients" / client_id / "sessions"


# ── 模块级工具函数 ──────────────────────────────────────────────────


def _fingerprint(store_root: Path) -> tuple:
    """该 client 的 atom 集指纹——便宜、能反映 atom 集是否变化。

    指纹 = 每个 traj 子目录的 ``(traj_id, atom 文件数)`` 排序元组。新增 traj
    或某 traj 被拆出更多 atom 都会改变指纹，触发质心重算。
    """
    if not store_root.is_dir():
        return ()
    parts: list[tuple[str, int]] = []
    for traj_dir in sorted(store_root.iterdir()):
        if not traj_dir.is_dir():
            continue
        tasks_dir = traj_dir / "tasks"
        if not tasks_dir.is_dir():
            continue
        n_atoms = sum(1 for _ in tasks_dir.glob("atom_*.json"))
        parts.append((traj_dir.name, n_atoms))
    return tuple(parts)


def _collect_used_skills(store_root: Path) -> frozenset[str]:
    """该 client 名下所有 atom 的 ``used_skills`` 并集。"""
    store = AtomTaskStore(root=store_root)
    used: set[str] = set()
    for atom in store.all_atoms():
        used.update(atom.used_skills)
    return frozenset(used)


def _centroid(used_skills: frozenset[str], skill_index: dict) -> np.ndarray | None:
    """该 client 用过的 skill 的 embedding 行的均值，再 L2 归一。

    only count skill 名在 ``.skill_index.pkl`` 里有对应行的——用过但已删除/
    未索引的 skill 无 embedding，自然不进质心。全部不在索引里则返回 None。
    """
    names = skill_index["skill_names"]
    embeddings = skill_index["embeddings"]
    name_to_row = {n: i for i, n in enumerate(names)}
    rows = [name_to_row[n] for n in used_skills if n in name_to_row]
    if not rows:
        return None
    centroid = embeddings[rows].mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm == 0:
        return None
    return centroid / norm


def _load_skill_index(skill_index_path: Path) -> dict:
    """加载 ``.skill_index.pkl``。

    结构（由 ``agents/skill_tools.rebuild_skill_index`` 生成）：
    ``{"skill_names": list[str], "embeddings": np.ndarray(N, D) 已 L2 归一, ...}``。
    """
    if not skill_index_path.is_file():
        raise FileNotFoundError(f"skill index missing: {skill_index_path}")
    with open(skill_index_path, "rb") as f:
        return pickle.load(f)


# server 进程级单例：build_manifest 用它做画像推荐 + 跨 sync 复用缓存。
RECOMMENDER = ClientProfileRecommender()
