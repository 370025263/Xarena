"""redact.py — 上传前的轨迹内容脱敏 hook

team 模式下 client 把本机 code-agent 轨迹上传给 server 之前先过一遍脱敏，
别让明文凭证（API key / token / 密码 / 私钥…）裸奔到 server。

主体复用开源的 `detect-secrets`（Yelp）的检测规则，而非自研——它带 23 个
厂商/格式专用检测器（AWS / Azure / GitHub / GitLab / Slack / Stripe / JWT /
PrivateKey / OpenAI / npm / Twilio / SendGrid / …）。

几个实测踩到的坑、对应的处理：

1. **不直接用 `scan_line`**：detect-secrets 的 `RegexBasedDetector` 在
   `analyze_string` 里用 `regex.findall()` 抽 secret —— 对**带捕获组**的正则
   （如 GitHubTokenDetector 的 ``(ghp|...)_[A-Za-z0-9_]{36}``），`findall`
   只回捕获组（``ghp``）不是完整 token，拿去替换会**漏掉密钥体**。故改取各
   检测器的 `denylist` 编译正则，用 `re.sub` 做**全匹配替换**（`re.sub`
   替换整个 match，不受捕获组影响）。

2. **排除 `IPPublicDetector`**：它抓公网 IP（PII 类、非凭证）。实测在真实
   轨迹里命中数是其余所有检测器之和的 ~10 倍——IP 多是运维信息（绑定地址
   0.0.0.0 / 服务器地址），大面积换掉反而毁轨迹可用性。

3. **高熵插件天然排除**：Base64/Hex HighEntropyString 不是
   `RegexBasedDetector`，不在 denylist 取值范围内——正合需要：高熵检测对
   自然语言/代码误报极高，自动脱敏（无人 review）容不下。

4. **私钥多行块**：detect-secrets 逐行扫描跨不了 PEM armor 块，另补一条
   PEM 块正则。

5. **不带引号的 env 赋值**：detect-secrets 的 `KeywordDetector` 只可靠命中
   *带引号* 的赋值（``api_key: "..."``）；``export OPENAI_API_KEY=sk-...``
   这类**裸值 env 赋值**——agent 轨迹里最常见的密钥泄漏形态——它一律不抓
   （实测确认）。故补一条 env 赋值正则：只匹配 **大写 env 风格** 的 keyword
   （`OPENAI_API_KEY=` / `DB_PASSWORD=`），避开代码里小写的 `token = ...`
   这类变量名误伤。

设计：纯函数 + 幂等。命中整体替换为 `[REDACTED]`。
"""
from __future__ import annotations

import re

from detect_secrets.core.plugins.util import get_mapping_from_secret_type_to_class
from detect_secrets.core.scan import scan_line
from detect_secrets.plugins.base import RegexBasedDetector
from detect_secrets.settings import transient_settings

_REDACTED = "[REDACTED]"

# 私钥多行块（detect-secrets 逐行扫描覆盖不到块体）。
_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*?PRIVATE KEY-----.*?-----END [A-Z0-9 ]*?PRIVATE KEY-----",
    re.DOTALL,
)

# 通用 `sk-` 前缀密钥。detect-secrets 只在 OpenAI 精确格式下抓 `sk-`；但
# `sk-` 是多厂商共用前缀（OpenAI / DeepSeek / 各 OpenAI-兼容服务），本项目
# 自己就用 DeepSeek 的 `sk-` key——轨迹里极常见，必须无条件覆盖。
_SK_PREFIX_TOKEN = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{15,}")

# 大写 env 风格的裸值密钥赋值（detect-secrets 的 KeywordDetector 不抓裸值）。
# keyword 须作大写标识符的**后缀**——`GITHUB_TOKEN=` 命中，`MAX_TOKENS=`
# （后缀是 TOKENS）不命中。值取无引号、无空白、≥6 字符。带引号的赋值由
# detect-secrets KeywordDetector 覆盖，这里只补裸值这一档。
_ENV_SECRET_ASSIGN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"([A-Z][A-Z0-9_]*?(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|ACCESS_?KEY))"
    r"(\s*[:=]\s*)"
    r"([^\s\"';]{6,})"
)

# 排除非凭证类检测器（理由见模块 docstring 第 2 点）。
_EXCLUDED_DETECTORS = {"IPPublicDetector"}


def _collect_regex_detectors() -> list[re.Pattern]:
    """收集 detect-secrets 凭证类 `RegexBasedDetector` 的 denylist 编译正则。

    动态派生（非硬编码名单）——detect-secrets 升级新增厂商检测器自动受益。
    """
    regexes: list[re.Pattern] = []
    for cls in get_mapping_from_secret_type_to_class().values():
        if issubclass(cls, RegexBasedDetector) \
                and cls.__name__ not in _EXCLUDED_DETECTORS:
            regexes.extend(cls.denylist)
    if not regexes:
        raise RuntimeError("detect-secrets: no RegexBasedDetector denylist found")
    return regexes


# 模块加载时算一次。
_REGEX_DETECTORS = _collect_regex_detectors()
# KeywordDetector 不是 RegexBasedDetector，单独走 scan_line：它专抓带引号的
# 赋值（`api_key: "sk-..."`），且实测 secret_value 是完整值、可靠。
_KEYWORD_CONFIG = {"plugins_used": [{"name": "KeywordDetector"}]}


def redact_text(text: str) -> str:
    """对一段轨迹文本脱敏：命中的凭证全部替换为 `[REDACTED]`。

    幂等：`[REDACTED]` 不匹配任何检测器/正则，重复调用结果稳定。
    """
    # ① PEM 私钥块
    text = _PEM_BLOCK.sub(_REDACTED, text)
    # ② detect-secrets 23 个厂商/格式检测器的 denylist 正则，全匹配替换
    for regex in _REGEX_DETECTORS:
        text = regex.sub(_REDACTED, text)
    # ②b 通用 sk- 前缀密钥（detect-secrets 不覆盖多厂商 sk-）
    text = _SK_PREFIX_TOKEN.sub(_REDACTED, text)
    # ③ KeywordDetector：带引号的赋值（`api_key: "sk-..."` 这类）
    found: set[str] = set()
    with transient_settings(_KEYWORD_CONFIG):
        for line in text.splitlines():
            for secret in scan_line(line):
                if secret.secret_value:
                    found.add(secret.secret_value)
    # 长的先替换：短 secret 若是长 secret 的子串，先换短的会毁掉长的。
    for value in sorted(found, key=len, reverse=True):
        text = text.replace(value, _REDACTED)
    # ④ 大写 env 风格裸值赋值：保留 keyword 前缀，只替换值
    text = _ENV_SECRET_ASSIGN.sub(lambda m: f"{m[1]}{m[2]}{_REDACTED}", text)
    return text
