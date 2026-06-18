"""context_budget.py —— TaskAgent 弃窗单趟的上下文自管理（spec §4.5）
================================================================================

弃窗单趟拆分把"全轨迹 User 地图"喂一次,agent 用 ``look`` 工具按需读 assistant
正文。一条怪物轨迹（数万行）下,反复 ``look`` 的返回会把对话历史撑大。这里实现
**纯剪裁**的上下文自管理（不调模型压缩,见 ADR：我们选纯剪裁不用 agno 自带 LLM
压缩）：

1. **分母 max_context**：无统一查询 API。**配置优先**（``llm.max_context``）；
   缺省 **200K** 并打一条 warning（提醒用户按自己模型上限反注释配置）。
2. **主动剪裁**：每次发请求前,若历史估算 token 到 max_context 的 **85%**,把旧的
   ``look`` / ``readfile`` 工具返回 message 的 content **纯截断**（look 结果可
   重读,丢了安全）。从最旧的开始剪,剪到回落 85% 以下为止。
3. **最底层兜底（唯一）**：抓住后端抛的"上下文超长"报错 → 再剪一轮历史 →
   **重发一次**。就这一条,不做解析上限学分母、不做多触发统一。
4. **真实已用 token**：每次请求拿到 ``usage.prompt_tokens`` 写进 thread-local,
   供 TaskAgent 的 ``context_budget()`` 工具读"后端真实已用"。

线程模型：watcher 并发拆多条 traj,每条在各自线程跑一个 agent.run()。用
``threading.local`` 让每个线程的"已用 token / 上限"互不串。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger("xskill.context_budget")

DEFAULT_MAX_CONTEXT = 200_000          # 配置缺省时的兜底上限（spec §4.5）
TRIM_TRIGGER_RATIO = 0.85              # 到上限 85% 触发主动剪裁
CHARS_PER_TOKEN = 4                    # 4 字符/token 粗估（仅估未发出去那部分）
_TRIMMABLE_TOOLS = ("look", "readfile")
_TRIM_MARK = "[…look 旧结果已剪裁,需要可重新 look…]"

# 上下文超长报错的特征关键词（不同后端措辞不一,只做子串命中即兜底重发）。
_OVERLONG_HINTS = (
    "context length", "context_length", "maximum context",
    "too long", "exceeds", "context window", "reduce the length",
    "string too long", "tokens. however",
)

# thread-local：当前线程最近一次请求后端真实 prompt_tokens + 该线程的上限。
_STATE = threading.local()


def set_max_context(max_tokens: int) -> None:
    """记本线程的上下文 token 上限（invoke 包装层每次请求前写）。"""
    _STATE.max_context = int(max_tokens)


def get_max_context() -> int:
    """读本线程的上下文 token 上限；未设过则返回缺省 200K。"""
    return int(getattr(_STATE, "max_context", DEFAULT_MAX_CONTEXT))


def set_used_tokens(used: int) -> None:
    """记本线程最近一次请求后端真实 prompt_tokens。"""
    _STATE.used_tokens = int(used)


def get_used_tokens() -> int:
    """读本线程最近一次请求后端真实 prompt_tokens；未发过请求则 0。"""
    return int(getattr(_STATE, "used_tokens", 0))


def resolve_max_context(llm_cfg: dict) -> int:
    """配置优先解析 max_context；缺省 200K 并打 warning（spec §4.5）。"""
    raw = (llm_cfg or {}).get("max_context")
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    logger.warning(
        "llm.max_context 未配置,弃窗单趟拆分按缺省 %d token 估上下文上限；"
        "请在 config.yaml 的 llm 段反注释 max_context 改成你模型的真实上限。",
        DEFAULT_MAX_CONTEXT)
    return DEFAULT_MAX_CONTEXT


def _msg_content_str(msg: Any) -> str:
    c = getattr(msg, "content", None)
    if isinstance(c, str):
        return c
    if c is None:
        return ""
    return str(c)


def _estimate_history_tokens(messages: list) -> int:
    """字符粗估整段历史 token（4 字符/token）。仅作"未发出去那部分"的估值。"""
    chars = 0
    for m in messages or []:
        chars += len(_msg_content_str(m))
    return chars // CHARS_PER_TOKEN


def _is_trimmable_tool_msg(msg: Any) -> bool:
    if getattr(msg, "role", None) != "tool":
        return False
    name = (getattr(msg, "tool_name", None) or getattr(msg, "name", None) or "")
    return str(name) in _TRIMMABLE_TOOLS


def _trim_old_look_results(messages: list, target_tokens: int,
                           *, force_all: bool = False) -> int:
    """从最旧的 look/readfile 工具返回开始纯截断,直到估算回落 target 以下。

    ``force_all=True`` 时无视估算,把所有可剪的 look/readfile 返回一律剪掉
    （超长兜底场景：后端已明确报超长,狠剪一轮再重发）。
    返回被剪裁的 message 数。已是剪裁标记的不重复剪。
    """
    trimmed = 0
    for m in messages or []:
        if not force_all and _estimate_history_tokens(messages) <= target_tokens:
            break
        if not _is_trimmable_tool_msg(m):
            continue
        if _msg_content_str(m) == _TRIM_MARK:
            continue
        try:
            m.content = _TRIM_MARK
            trimmed += 1
        except (AttributeError, TypeError):
            continue
    return trimmed


class ContextManager:
    """把 model.invoke 包成"发请求前主动剪裁 + 超长兜底重发 + 记真实已用"。

    构造时拿 ``max_context``（已 resolve）。``wrap(original_invoke)`` 返回新的
    invoke,生产/测试都能套。剪裁直接改传进来的 ``messages`` 列表里
    look/readfile 工具返回的 content（纯截断,不调模型）。
    """

    def __init__(self, max_context: int):
        self.max_context = int(max_context)
        self.trigger = int(self.max_context * TRIM_TRIGGER_RATIO)

    @staticmethod
    def _is_overlong_error(exc: Exception) -> bool:
        text = f"{exc}".lower()
        return any(h in text for h in _OVERLONG_HINTS)

    def wrap(self, original_invoke):
        """返回包好上下文自管理的 invoke。"""
        def managed_invoke(messages, **kwargs):
            set_max_context(self.max_context)
            # 1) 主动剪裁：到 85% 就剪旧 look 结果（纯截断,不调模型）。
            est = _estimate_history_tokens(messages)
            if est >= self.trigger:
                n = _trim_old_look_results(messages, self.trigger)
                if n:
                    logger.info("上下文到 %d/%d token,主动剪裁 %d 条旧 look 结果",
                                est, self.max_context, n)
            try:
                resp = original_invoke(messages, **kwargs)
            except Exception as exc:  # noqa: BLE001 — 唯一底层兜底
                if not self._is_overlong_error(exc):
                    raise
                # 2) 超长兜底（唯一）：再狠剪一轮 → 重发一次。
                logger.warning("后端报上下文超长,剪裁历史后重发一次：%s", exc)
                _trim_old_look_results(messages, self.max_context // 2,
                                       force_all=True)
                resp = original_invoke(messages, **kwargs)
            # 3) 记后端真实 prompt_tokens（context_budget 工具读这个）。
            self._record_prompt_tokens(resp)
            return resp

        return managed_invoke

    @staticmethod
    def _record_prompt_tokens(resp: Any) -> None:
        from xskill.usage import extract_usage
        usage = extract_usage(resp)
        if usage.prompt:
            set_used_tokens(usage.prompt)
