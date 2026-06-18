"""
utils/traj_analysis.py — 从轨迹文本片段做分析式指标（不靠埋点）
================================================================

看板要每个版本 atom 的"平均工具调用次数"和"token 消耗量"。这些**不另埋点**，
直接从轨迹文本算：bridged 轨迹里每次工具调用都渲染成 ``## Tool Call: <tool>``
段（CC / opencode / ngagent 同构），数一下即可；token 用字符数估算。

纯函数、无副作用、给定输入必出同结果——确定性，方便单测断言。
"""
from __future__ import annotations

import re

# 行首 ``## Tool Call``（允许后跟 ``: <tool>`` 或 ` [ERROR]`）。各生态渲染一致。
_TOOL_CALL_RE = re.compile(r"^##\s+Tool Call\b", re.MULTILINE)

# 粗略 token 估算系数：英文/代码约 4 字符/token，中文更密。取 4 作通用近似——
# 看板要的是"版本间相对趋势"，绝对值不必精确。
_CHARS_PER_TOKEN = 4


def count_tool_calls(segment: str) -> int:
    """数一段轨迹文本里的工具调用次数（``## Tool Call`` 段计数）。"""
    if not segment:
        return 0
    return len(_TOOL_CALL_RE.findall(segment))


def estimate_tokens(segment: str) -> int:
    """按字符数粗估 token 消耗量（``len // 4``）。"""
    if not segment:
        return 0
    return len(segment) // _CHARS_PER_TOKEN


def analyze_segment(segment: str) -> dict:
    """一段轨迹文本 → ``{"tool_calls": N, "est_tokens": M, "chars": C}``。"""
    return {
        "tool_calls": count_tool_calls(segment),
        "est_tokens": estimate_tokens(segment),
        "chars": len(segment or ""),
    }
