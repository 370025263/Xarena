"""
config.py — 全局路径与配置加载
═════════════════════════════════════
统一从 ~/.xskill/ 读取；无 cwd fallback、无环境变量 fallback、无 ~/.aikey fallback。
缺失即抛异常。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("xskill.config")

# ─── 默认根路径 ─────────────────────────────────────────────────
XSKILL_HOME = Path.home() / ".xskill"
CONFIG_PATH = XSKILL_HOME / "config.yaml"
REGISTRY_DB = XSKILL_HOME / "registry.db"
CHAT_DB = XSKILL_HOME / "chat_sessions.db"
LOGS_DIR = XSKILL_HOME / "logs"

_config: dict = {}
_overrides: dict = {}


def set_overrides(**kwargs):
    """CLI flag 覆盖。仅 debug / quiet 两个保留。"""
    for k, v in kwargs.items():
        if v is not None:
            _overrides[k] = v


# 首次运行 auto-init 写出的配置模板。这是配置格式的**唯一真源**——
# 不再单独维护 examples/config.yaml.example，避免两份漂移。
CONFIG_TEMPLATE = """\
# xskill config — fill in the api keys below, then run `xskill serve` again.
#
# xskill does NOT read environment variables or any key file. Missing required
# fields (llm.api_key / embedding.api_key) raise loudly — no silent fallback.

# ===== Skill repository =====
skill_dir: ~/.xskill/skill            # the single global skill repo

# ===== LLM (generation / scoring / chat) =====
# Any OpenAI-compatible chat-completions endpoint works (DeepSeek, OpenAI,
# Qwen/DashScope, OpenRouter, a local Ollama, ...).
llm:
  base_url: https://api.deepseek.com
  model:    deepseek-v4-flash
  api_key:  PUT_YOUR_LLM_API_KEY_HERE
  max_tokens: 10000      # optional; a "thinking" model needs enough budget for
                         # reasoning_tokens + content, or meta extraction
                         # returns empty/truncated and falls back to rules.
  # temperature: 0.0     # optional; default 0 (deterministic)
  # rate_limit:          # optional; absent = unlimited (good for self-hosted)
  #   rpm: 60            # requests per minute; match your provider plan
  #   tpm: 100000        # tokens per minute (optional within rate_limit)
  #   burst: 10          # optional; default = ceil(rate/6)
  # See docs/adr/0001-rate-limit-diy-not-litellm.md for the design rationale.

# ===== Embedding (vector retrieval) =====
# Any OpenAI-compatible embeddings endpoint. dim: 0 auto-probes on first call.
#
# DeepSeek does NOT provide an embeddings API. Choose one of these:
#   • Alibaba DashScope:  base_url=https://dashscope.aliyuncs.com/compatible-mode/v1  model=text-embedding-v4
#   • OpenAI:             base_url=https://api.openai.com/v1                          model=text-embedding-3-small
#   • Ollama (local):     base_url=http://localhost:11434/v1                          model=nomic-embed-text
#   • Jina AI:            base_url=https://api.jina.ai/v1                             model=jina-embeddings-v3
embedding:
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
  model:    text-embedding-v4
  api_key:  PUT_YOUR_EMBEDDING_API_KEY_HERE
  dim:      0
  # api: openai | multimodal   # optional; default openai. "multimodal" for
                               # vision-style embedding endpoints.

# ===== Pricing (optional; for `xskill stats` cost estimation) =====
# Cost is ESTIMATED from response.usage tokens × price (USD per 1M tokens).
# Resolution order: this `pricing:` map  >  the vendored price table
# (src/xskill/data/model_prices.json, refreshed at build time)  >  `default`.
# Leave this out entirely to rely on the vendored table + default below.
# pricing:
#   default: { input_per_1m: 1.0, output_per_1m: 3.0, embed_per_1m: 0.05 }
#   deepseek-v4-flash: { input_per_1m: 0.14, output_per_1m: 0.28, cache_hit_per_1m: 0.014 }

# ===== Canary (gradual rollout) =====
canary:
  enabled:       true
  probability:   0.2            # on a retrieval hit, route to staging with prob p
  min_samples:   5              # need >= N UX scores on each side to decide
                                # (single-bucket / un-scoped path)
  max_days_hold: 14             # max staging lifetime; discarded on timeout
  rotate_interval: 300          # standalone canary time-window rotation (seconds)
  scope_top_n:   2              # model-scoped canary: only the top-N user models
                                # by usage take part (routing + scoring); unknown
                                # and non-top-N traffic stays on main
  total_samples: 20             # model-scoped path: total UX scores needed on
                                # each side before a weighted decision

# ===== Watcher (the directory poller inside `serve`) =====
watcher:
  poll_interval:  30            # seconds between scans of every watch_dir
  max_concurrent: 4             # parallel LLM calls per scan. Conservative
                                # placeholder that pairs with llm.rate_limit
                                # above. Raise to 20-30 for self-hosted vLLM
                                # or accounts with no concurrency cap. See
                                # docs/adr/0001-rate-limit-diy-not-litellm.md
  cold_start_threshold: 3       # defer process while >= N trajectories un-indexed

# ===== Team C/S mode (only read by `xskill serve --server`) =====
team:
  server:
    traj_root:    ~/.xskill/team_trajectories
    skill_slots:  100
    ranked_slots: 80

# ===== Dashboard (the built-in web console served by `xskill serve`) =====
dashboard:
  enabled:  false      # 设 true 才挂载控制台到 serve 的 /
  public:   false      # 默认仅本机可达；true 才放行公网（仅看板路由）
  password: ""         # 可选；非空时看板要求 HTTP Basic 登录（API 不受影响）
"""


def ensure_config_exists(path: Optional[Path] = None) -> bool:
    """首次运行 auto-init：config.yaml 不存在时写出 CONFIG_TEMPLATE。

    返回值：
        True  —— 配置已存在（什么都没做）
        False —— 刚刚创建了模板（调用方应提示用户填 key 后重跑）
    """
    cfg_path = Path(path) if path else CONFIG_PATH
    if cfg_path.exists():
        return True
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return False


def load_config(path: Optional[Path] = None) -> dict:
    """加载 ~/.xskill/config.yaml；不存在直接抛 FileNotFoundError。

    正常路径下 CLI 会先调 ``ensure_config_exists`` auto-init，不会走到这个
    FileNotFoundError；保留它作为 SDK 直接调用时的 fail-loud 兜底。
    """
    global _config
    cfg_path = Path(path) if path else CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"xskill config not found: {cfg_path}\n"
            f"Run `xskill serve` once to auto-create a template, "
            f"or call config.ensure_config_exists()."
        )
    with open(cfg_path, encoding="utf-8") as f:
        _config = yaml.safe_load(f) or {}
    if not _config.get("llm", {}).get("api_key"):
        raise KeyError(f"llm.api_key missing in {cfg_path}")
    if not _config.get("embedding", {}).get("api_key"):
        raise KeyError(f"embedding.api_key missing in {cfg_path}")
    return _config


def get_config() -> dict:
    if not _config:
        load_config()
    return _config


def dashboard_config(cfg: dict) -> dict:
    """从已加载 config 取 dashboard 段，缺字段用显式默认（非 fallback 兼容）。"""
    d = cfg.get("dashboard") or {}
    return {
        "enabled": bool(d.get("enabled", False)),
        "public": bool(d.get("public", False)),
        "password": str(d.get("password", "") or ""),
    }


def get_skill_dir() -> Path:
    """skill_dir: config.yaml 字段；默认 ~/.xskill/skill/"""
    cfg = get_config()
    raw = cfg.get("skill_dir") or str(XSKILL_HOME / "skill")
    return Path(raw).expanduser()


def get_logs_dir() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR


def get_traj_dir() -> Path:
    """默认轨迹目录 = 第一个已注册的 watch dir。

    轨迹来源的真源是 Registry——dataset 通过 ``xskill registry add <abs-path>``
    注册，daemon 启动时也会自动探测并注册各生态的 session 目录。本函数仅给
    "无显式路径" 的内部调用取一个默认目录用；新代码优先走 Registry / 显式 path。

    一个 watch dir 都没注册时直接抛错——不兜底到某个魔术目录（CLAUDE.md：
    遇到问题 throw error，不写 fallback）。
    """
    # 函数内 import：registry 反过来依赖 config，模块级 import 会成环。
    from xskill.pipeline.registry import list_watch_dirs
    dirs = list_watch_dirs()
    if not dirs:
        raise RuntimeError(
            "没有已注册的 watch dir——先 `xskill registry add <abs-path>`，"
            "或让 daemon 启动时自动探测生态目录后再调用 get_traj_dir()。"
        )
    return Path(dirs[0]["path"])


def get_registry_db_path() -> Path:
    return REGISTRY_DB


def get_chat_db_path() -> Path:
    return CHAT_DB


# ─── team (C/S 模式) 路径 ───────────────────────────────────────
# 纯路径运算，不读 config.yaml——client 瘦客户端无 llm.api_key，
# get_config() 会抛 KeyError。get_team_trajectories_dir() 是唯一例外
# （只 server 调，server 一定有 key）。

def get_team_server_state_path() -> Path:
    """server join token 落盘位置（~/.xskill/team_server.json，0600）。"""
    return XSKILL_HOME / "team_server.json"


def get_team_clients_db_path() -> Path:
    """server 端 client 注册表 SQLite。"""
    return XSKILL_HOME / "team_clients.db"


def get_team_client_state_path() -> Path:
    """client 端连接信息（server_url / client_id / join_token）。"""
    return XSKILL_HOME / "team_client.json"


# 注：team client 不另开 team_skills/ / team_outbox/ 目录——
#  - skill working copies 复用标准 skill_dir（~/.xskill/skill/），与
#    standalone 模式同位置；
#  - 采集的轨迹复用标准 bridge 目录（~/.xskill/<eco>_sessions/），即
#    detect_known_ecosystems 返回的 bridge 路径。


def get_team_trajectories_dir() -> Path:
    """server 端收下的 client 上传轨迹根目录。

    读 config.yaml ``team.server.traj_root``，缺省 ~/.xskill/team_trajectories。
    仅 server 调用。
    """
    cfg = get_config()
    raw = (cfg.get("team", {}).get("server", {}).get("traj_root")
           or str(XSKILL_HOME / "team_trajectories"))
    p = Path(raw).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


# ─── 调试 flag ──────────────────────────────────────────────────
def is_debug() -> bool:
    return _overrides.get("debug", False)


def is_quiet() -> bool:
    return _overrides.get("quiet", False)


# ─── 兼容旧 API（仅为过渡期保留，下期清掉）───────────────────────
def get_registry_dir() -> Path:
    """旧 API。返回 XSKILL_HOME。新代码请用 get_registry_db_path。"""
    return XSKILL_HOME




def get_output_dir() -> Path:
    """旧 API → 转 logs_dir"""
    return get_logs_dir()


def resolve_traj_path(path_or_dataset: str) -> Path:
    """旧 API。新代码：直接用绝对路径。"""
    p = Path(path_or_dataset).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"trajectory path not found: {p}")
    return p
