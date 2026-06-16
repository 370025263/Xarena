"""state.py — team server 的 join token（SP1）

join token 是 client 接入的唯一门槛（单一共享 token，见设计决策）。
client 完全信任 server；token 只用来挡住组织外的随机接入。真正的防呆
是"client 永远只能写 user-staging/<client_id>，碰不到 main"。

token 落 ~/.xskill/team_server.json，0600 权限。切勿回显进日志。
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path


def ensure_join_token(path: Path | str) -> str:
    """读取已有 join token；不存在则生成一个并以 0600 落盘。返回 token。"""
    path = Path(path)
    existing = load_join_token(path)
    if existing:
        return existing
    token = secrets.token_hex(16)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"join_token": token}), encoding="utf-8")
    path.chmod(0o600)
    return token


def load_join_token(path: Path | str) -> str | None:
    """读 join token；文件不存在或损坏返回 None。"""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    tok = data.get("join_token")
    return tok if isinstance(tok, str) and tok else None
