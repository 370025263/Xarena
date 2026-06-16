"""xskill team — server 侧（``xskill serve --server`` 才跑）。

职责：身份/鉴权（join token + client 注册表）、收 client 上传的轨迹、
给每个 client 现算 skill manifest、发 skill bundle、收手改进隔离分支。
agent 流水线复用既有 DirectoryWatcher（server_mode=True）。
"""

from __future__ import annotations
