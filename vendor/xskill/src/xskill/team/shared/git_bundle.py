"""git_bundle.py — skill git 仓的 bundle 传输封装（SP1）

skill 分发本质是 git 分布式。SP1 不跑独立 git http daemon，而是把每个
skill 子仓打成 git bundle 走普通 HTTP body 传输：

- server → client：``make_repo_bundle`` 打全分支 → client ``apply_repo_bundle``
  克隆/fetch 落到本地 working copy。
- client → server：client ``make_branch_bundle`` 打 ``_useredit`` 分支 →
  server ``fetch_branch_from_bundle`` 收进 ``user-staging/<client_id>``。

底层走 dulwich 的 ``create_bundle_from_repo`` / ``read_bundle``——纯 Python
git，不依赖系统 git 二进制（容器/受限环境也能跑）。SP1 每次传全量
bundle（skill 仓很小：SKILL.md ≤400 行 + 几个 script）。
"""
from __future__ import annotations

import io
from pathlib import Path

from dulwich import porcelain
from dulwich.bundle import create_bundle_from_repo, read_bundle, write_bundle
from dulwich.repo import Repo

from xskill.skill.git import _write_repo_identity  # 复用身份初始化


def make_repo_bundle(repo_dir: Path | str) -> bytes:
    """把一个 skill git 仓的所有本地分支打成 bundle 字节。"""
    repo_dir = Path(repo_dir)
    if not (repo_dir / ".git").is_dir():
        raise NotADirectoryError(f"not a git repo: {repo_dir}")
    with Repo(str(repo_dir)) as repo:
        # 只挑 refs/heads/* 分支
        head_refs = [r for r in repo.refs.keys() if r.startswith(b"refs/heads/")]
        bundle = create_bundle_from_repo(repo, refs=head_refs)
        buf = io.BytesIO()
        write_bundle(buf, bundle)
        return buf.getvalue()


def apply_repo_bundle(bundle_bytes: bytes, dest_dir: Path | str) -> None:
    """用 bundle 在本地物化/刷新一个 skill working copy。

    dest_dir 不是 git 仓 → ``init`` 一个 HEAD 指向 ``refs/heads/_scratch``
    的空仓（``_scratch`` 不会被 bundle 创建），让 main/staging 永远不是
    "当前 checked-out 分支"——避免后续覆盖当前分支的 ref 时被拒绝。

    覆盖语义：把 bundle 的 ``refs/heads/*`` 强制覆盖本地同名分支（main/
    staging/baby）。工作树留给 reconcile 去 checkout ``_active``。
    """
    dest_dir = Path(dest_dir)
    if not (dest_dir / ".git").is_dir():
        dest_dir.mkdir(parents=True, exist_ok=True)
        repo = porcelain.init(str(dest_dir), bare=False)
        try:
            _write_repo_identity(repo)
            # HEAD → refs/heads/_scratch（symbolic ref；_scratch 不存在 OK）
            repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/_scratch")
        finally:
            repo.close()

    with Repo(str(dest_dir)) as repo:
        with io.BytesIO(bundle_bytes) as buf:
            bundle = read_bundle(buf)
            # 存所有对象
            bundle.store_objects(repo.object_store)
            # 把 bundle.references 强制写入本地 refs
            for ref, sha in bundle.references.items():
                if ref.startswith(b"refs/heads/"):
                    repo.refs[ref] = sha


def make_branch_bundle(repo_dir: Path | str, branch: str) -> bytes:
    """把一个分支（含完整历史）打成 bundle 字节。client 推手改用。"""
    repo_dir = Path(repo_dir)
    if not (repo_dir / ".git").is_dir():
        raise NotADirectoryError(f"not a git repo: {repo_dir}")
    ref = f"refs/heads/{branch}".encode("utf-8")
    with Repo(str(repo_dir)) as repo:
        if ref not in repo.refs:
            raise RuntimeError(f"branch not found: {branch}")
        bundle = create_bundle_from_repo(repo, refs=[ref])
        buf = io.BytesIO()
        write_bundle(buf, bundle)
        return buf.getvalue()


def fetch_branch_from_bundle(
    bundle_bytes: bytes, dest_repo: Path | str, src_branch: str, dest_ref: str,
) -> str:
    """把 bundle 里的 ``src_branch`` fetch 进 ``dest_repo`` 的 ``dest_ref``。

    返回 ``dest_ref`` 的新 sha。server 收 client 手改时用——dest_ref 形如
    ``refs/heads/user-staging/<client_id>``，永远不碰 main。
    """
    dest_repo = Path(dest_repo)
    if not (dest_repo / ".git").is_dir():
        raise NotADirectoryError(f"not a git repo: {dest_repo}")
    src_ref = f"refs/heads/{src_branch}".encode("utf-8")
    dest_ref_b = dest_ref.encode("utf-8") if isinstance(dest_ref, str) else dest_ref

    with Repo(str(dest_repo)) as repo:
        with io.BytesIO(bundle_bytes) as buf:
            bundle = read_bundle(buf)
            bundle.store_objects(repo.object_store)
            if src_ref not in bundle.references:
                raise RuntimeError(
                    f"bundle missing branch {src_branch}: "
                    f"have {list(bundle.references.keys())}",
                )
            sha = bundle.references[src_ref]
            repo.refs[dest_ref_b] = sha
            return sha.decode("ascii")
