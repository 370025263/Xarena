"""xskill team — server 与 client 共享的部分。

protocol   : C/S 线协议的 pydantic 模型（HTTP body 单一事实源）
git_bundle : skill git 仓的 bundle 打包/落地/推送
reconcile  : skill side 调谐契约（client 与单机 watcher 共用步骤 2/3/4）
"""

from __future__ import annotations
