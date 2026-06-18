"""
agents/agent_trace.py — 每次 agent 调用的逐轮 CoT / 工具调用 trace，按 traj/atom/skill
落独立文件
================================================================================

单一 ``agno.log`` 把所有 agent 的请求糊在一起，没法按"这条 traj 拆分时模型想了啥、
look 了哪、submit 了什么"排查。这里给每次 ``agent.run()`` 开一个**上下文 sink**
（线程本地），由工厂在 ``model.invoke`` 外层每轮把 ``reasoning_content`` / ``content``
/ ``tool_calls`` 流式 append 进去。``tail -f`` 就能实时看某条 traj 的拆分推理。

路径由调用方决定（落在 ``get_logs_dir()/agents/`` 下）：
  task_agents/<traj_id>.log
  task_cluster_agents/<traj_id>/<atom_id>.log
  skill_edit_agents/skills/<skill>_<ts>.log

线程模型：watcher 每条 agent 跑在线程池各自线程，``threading.local`` 让各线程的
sink 互不串（同一次 run 内的多轮 invoke 都在同一线程 → 同一文件）。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

_STATE = threading.local()


def _sink() -> "Path | None":
    return getattr(_STATE, "sink", None)


def set_sink(path) -> None:
    """设当前线程的 trace sink；每次 run 开头清空同名文件（覆盖上一次）。"""
    if path is None:
        _STATE.sink = None
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    _STATE.sink = p
    _STATE.round = 0


def clear_sink() -> None:
    _STATE.sink = None


class trace_to:
    """``with trace_to(path): agent.run(...)`` —— 这次 run 的每轮 LLM 交互写进 path。

    path 为 None → no-op（不 trace，普通 agent 调用不受影响）。
    """

    def __init__(self, path):
        self.path = path

    def __enter__(self) -> "trace_to":
        if self.path is not None:
            set_sink(self.path)
        return self

    def __exit__(self, *exc) -> bool:
        clear_sink()
        return False


def _content_len(m: Any) -> int:
    c = getattr(m, "content", None)
    if c is None and isinstance(m, dict):
        c = m.get("content")
    return len(c if isinstance(c, str) else str(c or ""))


def _extract(resp: Any):
    """从 agno / OpenAI 响应抽 (reasoning, content, [(tool_name, args), ...])。

    防御式：兼容 ``resp.choices[0].message`` 结构与直接挂在 resp 上的属性、
    以及 tool_call 的 dict / 对象两种形态。
    """
    msg: Any
    try:
        msg = resp.choices[0].message
    except Exception:  # pylint: disable=broad-exception-caught
        msg = resp
    rc = getattr(msg, "reasoning_content", None)
    ct = getattr(msg, "content", None)
    tcs = []
    for tc in (getattr(msg, "tool_calls", None) or []):
        fn = getattr(tc, "function", None)
        if fn is None and isinstance(tc, dict):
            fn = tc.get("function")
        name = (getattr(fn, "name", None)
                or (fn.get("name") if isinstance(fn, dict) else "?"))
        args = (getattr(fn, "arguments", None)
                or (fn.get("arguments") if isinstance(fn, dict) else ""))
        tcs.append((name, str(args)[:400]))
    return rc, ct, tcs


def record(messages, resp) -> None:
    """工厂 invoke 包装层每轮调一次：把这轮 reasoning/content/tool_calls append 进 sink。

    没设 sink（普通调用）→ 直接返回，零开销。写盘失败吞掉，绝不影响主流程。
    """
    sink = _sink()
    if sink is None:
        return
    n = getattr(_STATE, "round", 0) + 1
    _STATE.round = n
    chars = sum(_content_len(m) for m in (messages or []))
    out = [f"\n=== round {n} | {len(messages or [])} msgs | ~{chars // 4} tokens ==="]
    rc, ct, tcs = _extract(resp)
    if rc and str(rc).strip():
        out.append("💭 think:\n" + str(rc).strip())
    if ct and str(ct).strip():
        out.append("💬 say: " + str(ct).strip())
    for name, args in tcs:
        out.append(f"🔧 {name}({args})")
    try:
        with open(sink, "a", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
    except OSError:
        pass
