"""xskill team — client 侧（``xskill connect`` 才跑）。

职责：记连接信息、采集本机 code-agent 轨迹、上传前脱敏、静默增量上传、
拉 skill bundle 并对齐到 server 指定的灰度 side、推本地手改进隔离分支、
清理 manifest 外的本地 skill。瘦客户端——零 LLM、零 git 写 main。
"""

from __future__ import annotations
