"""
utils/sanitize.py — 入库前清洗轨迹文本（去控制字符 / ANSI 转义）
================================================================

桥接进来的轨迹 ``.md``（尤其 ngagent/opencode 这种含终端、tool 原始输出的）
常掺入：

* **ANSI 终端转义序列**（``ESC[...m`` 之类的颜色/光标控制码）——渲染时是垃圾，
  还会污染喂给模型的内容。
* **C0/C1 控制字符**（``0x00``–``0x1f``、``0x7f``、``0x85`` 等）——其中
  ``0x0b 0x0c 0x1c 0x1d 0x1e 0x85 u2028 u2029`` 是 Python ``str.splitlines()``
  会**当成换行**的字符，但 ``\\n`` 计数 / vi / wc 不认 → 拆分器按 splitlines 数行、
  人按 ``\\n`` 数行，**行坐标错位**，atom 的 offset 跟人看的行号对不上。

本模块在**落盘前**把这些清掉，保证：① splitlines 行数 == ``\\n`` 行数（offset
与人类视角一致）；② 不把 ANSI/控制垃圾喂给模型。已损坏的替换符 ``U+FFFD``（上游
坏字节被替换成的）保留——那是已丢数据的标记，删不删都救不回，留着提示哪里丢了。
"""
from __future__ import annotations

import re

# ANSI 转义序列：CSI（``ESC[...``）+ 双字符 escape（``ESC@`` ~ ``ESC_``）。
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# 要删的控制字符集（显式构造，避免源码里塞字面不可见字符）：
#   - C0 控制符 0x00–0x08、0x0b–0x1f（**保留** \t=0x09、\n=0x0a）
#   - DEL 0x7f、C1 NEL 0x85、unicode 行/段分隔符 U+2028 / U+2029
# 关键是把"splitlines 会切但 \n 不切"的字符清掉，让两套行计数一致。
_CTRL_CHARS = (
    "".join(chr(c) for c in range(0x00, 0x09))    # 0x00–0x08
    + "".join(chr(c) for c in range(0x0B, 0x20))  # 0x0b–0x1f（跳过 \t \n）
    + "\x7f\x85  "
)
_CTRL_RE = re.compile("[" + re.escape(_CTRL_CHARS) + "]")


def sanitize_trajectory_text(text: str) -> str:
    """清洗一段轨迹文本：去 ANSI 转义 + 控制字符，归一换行。

    保证返回值 ``splitlines()`` 行数 == ``count('\\n')+1``（无隐藏换行），
    且不含 ANSI/控制垃圾。``\\t`` ``\\n`` 保留，``\\r\\n`` / ``\\r`` 归一为 ``\\n``。
    """
    if not text:
        return text
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CTRL_RE.sub("", text)
    return text


# ─────────────────────────────────────────────────────────────────
# 去壳掩码（mask_patterns）—— 入库转换阶段剥掉评测 harness 的固定外壳
# ─────────────────────────────────────────────────────────────────

# 命中段统一替换成的占位符。聚类/拆分看到的是这个 token 而非外壳原文，
# 不会被每题相同的 harness turn-0 提示词吸成一簇。
MASK_PLACEHOLDER = "[MASKED_HARNESS_PROMPT]"


def apply_mask_patterns(text: str, patterns: list[str]) -> str:
    """对命中 ``patterns`` 任一正则的文本段替换为 ``MASK_PLACEHOLDER``。

    在**入库转换写 md 之前**调用（不是拆分阶段）——保证落盘的轨迹文本
    本身已去壳，下游拆分/聚类/embedding 一律看不到外壳原文。

    ``patterns`` 为空列表时原样返回（默认行为，现网用户零影响）。
    跨行匹配由调用方在正则里写内联 flag（如 ``(?s)``）。坏正则直接抛
    ``re.error``——配置层 ``ingest_config`` 已先行编译校验，这里不重复吞错。
    """
    if not patterns:
        return text
    for pat in patterns:
        text = re.sub(pat, MASK_PLACEHOLDER, text)
    return text
