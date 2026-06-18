"""
pipeline/db_ingest.py — 从任意位置的 db 文件批量入库
=====================================================

``xskill read <LOCAL_PATH> --eco ngagent`` 与 HTTP 上传端口共用的核心编排：
把指定位置的 SQLite db 文件（ngagent / opencode 同 schema）桥成标准
``traj_*.md``，落到该生态的 bridge 目录，并把该目录注册成 watch_dir，让
watcher 后续按常规流水线拆分/聚类/出 skill。

与 daemon 常驻 ingester 的区别：常驻 ingester 扫**本机固定家目录路径**的 db；
本模块吃**任意路径**的 db 文件（``SqliteIngester(db_path=...)`` 覆盖）——用于
企业用户把自己机器上的 ngagent.db 上传到中心服务器后离线入库。

设计（遵守 CLAUDE.md：不兜底、出错抛）：
  - eco 必须是 sqlite-back 生态（ngagent / opencode），否则抛。
  - 给定路径下找不到 db 文件直接抛，不静默成功。
  - 同一 db 重复 read 幂等：``SqliteIngester._seen`` 按 bridge 目录里已存在的
    traj 去重，已桥过的 session 不会重复产出。
"""
from __future__ import annotations

import logging
from pathlib import Path

from xskill.ecosystems import SQLITE_SPEC_BY_ECO, SqliteIngester, bridge_dir_for

logger = logging.getLogger("xskill.process")


def _discover_db_files(path: Path, *, recursive: bool) -> list[Path]:
    """把入参路径解析成一组 db 文件。

    - 路径是文件 → 就这一个（不限定后缀，上传来的可能没 .db 后缀）。
    - 路径是目录 → 其下所有 ``*.db``（recursive 则递归）。
    """
    if path.is_file():
        return [path]
    if path.is_dir():
        globber = path.rglob if recursive else path.glob
        return sorted(globber("*.db"))
    raise FileNotFoundError(f"read path 不存在: {path}")


def read_db_files(
    path: Path | str,
    *,
    eco: str,
    home_root: Path | str | None = None,
    register: bool = True,
    recursive: bool = False,
    target_dir: Path | str | None = None,
    register_label: str | None = None,
) -> dict:
    """把 ``path`` 下的 db 文件批量桥接入库。

    Args:
        path: db 文件或包含 db 文件的目录。
        eco: sqlite-back 生态 id（``ngagent`` / ``opencode``）。
        home_root: bridge 目录的家目录根（默认 ``~``）；测试可指向 tmp。
        register: True 时把 bridge 目录注册成 watch_dir（让 watcher 捡起）。
        recursive: 目录模式下是否递归找 ``*.db``。
        target_dir: 显式落盘目录覆盖（团队服务器按 client 分桶时用，传
            ``clients/<client_id>/sessions``）；None 则用 ``bridge_dir_for(eco)``。
        register_label: 注册 watch_dir 的 label（团队服务器传 client_id 让
            watcher 能做 CS 归因）；None 则用 eco。

    Returns:
        ``{"eco", "target_dir", "db_files": [...], "bridged": N, "trajectories": [...]}``
    """
    spec = SQLITE_SPEC_BY_ECO.get(eco)
    if spec is None:
        known = ", ".join(SQLITE_SPEC_BY_ECO)
        raise ValueError(
            f"read 只支持 sqlite 生态（{known}），收到 eco={eco!r}"
        )

    src = Path(path)
    db_files = _discover_db_files(src, recursive=recursive)
    if not db_files:
        raise FileNotFoundError(
            f"{src} 下没有找到 db 文件（eco={eco}）——确认路径或上传是否成功"
        )

    out_dir = Path(target_dir) if target_dir else bridge_dir_for(eco, home_root)
    out_dir.mkdir(parents=True, exist_ok=True)

    bridged: list[dict] = []
    for dbf in db_files:
        ing = SqliteIngester(
            out_dir, home_root=home_root, spec=spec, db_path=dbf,
        )
        results = ing.run_once()
        logger.info(
            "read_db_files: %s → bridged %d session(s) into %s",
            dbf, len(results), out_dir,
        )
        bridged.extend(results)

    if register:
        # 函数内 import 避免 registry↔config↔ecosystems 成环
        from xskill.pipeline.registry import register_dir
        register_dir(str(out_dir), label=register_label or eco, ecosystem=eco)
    target_dir = out_dir

    return {
        "eco": eco,
        "target_dir": str(target_dir),
        "db_files": [str(p) for p in db_files],
        "bridged": len(bridged),
        "trajectories": [b.get("path") for b in bridged],
    }
