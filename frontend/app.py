
# app.py (Streamlit frontend, non-blocking refresh via st_autorefresh + cache_data TTL)

import streamlit as st
import streamlit.components.v1 as components
st.set_page_config(page_title="NPU 榜单", layout="wide")

import requests
import jwt  # PyJWT
from datetime import datetime
import json, os, json
from html import escape as html_escape
from itertools import product
import math
import logging
# --- A. 非阻塞自动刷新工具 ---
try:
    from streamlit_autorefresh import st_autorefresh as _st_autorefresh
except Exception:
    _st_autorefresh = None

def st_auto(interval_ms: int | None, key: str, enable: bool = True, limit: int = 0) -> int:
    if not enable or not interval_ms or interval_ms <= 0:
        return 0
    if _st_autorefresh is None:
        return 0
    return _st_autorefresh(interval=interval_ms, key=key, limit=limit)

# --- B. 刷新间隔与缓存 TTL ---
QUEUE_REFRESH_MS   = int(os.getenv("LB_QUEUE_REFRESH_MS", "5000"))
RANK_REFRESH_MS    = int(os.getenv("LB_RANK_REFRESH_MS",  "5000"))
LIST_REFRESH_MS    = int(os.getenv("LB_LIST_REFRESH_MS",  "5000"))
LOG_REFRESH_MS     = int(os.getenv("LB_LOG_REFRESH_MS",   "2000"))
HOME_TOP5_REFRESH_MS = int(os.getenv("LB_HOME_TOP5_REFRESH_MS", "8000"))

TTL_DEFAULT_SEC = int(os.getenv("LB_CACHE_TTL", "2"))
TTL_LOG_SEC     = int(os.getenv("LB_CACHE_TTL_LOG", "1"))
TTL_TOP5_SEC    = int(os.getenv("LB_CACHE_TTL_TOP5", "3"))

MAX_BATCH = int(os.getenv("LB_MAX_BATCH", "50"))
# logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True # 强制覆盖 Streamlit 默认的日志配置
)
logger = logging.getLogger("AgentUI")


# ======== 工具 ========
API_BASE_URL = os.getenv("API_BASE_URL") or "http://8.46.50.72:30001"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL") or "http://8.46.50.72:30001"
JWT_ALGORITHM = "HS256"

GLOBAL_CSS = """
<style>
:root { --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }
.table-wrap { width: 100%; overflow-x: auto; }
.lb-table { border-collapse: collapse; width: 100%; font-size: 15px; }
.lb-table th, .lb-table td { padding: 10px 12px; border-bottom: 1px solid #EEE; text-align: left; vertical-align: middle; }
.lb-table th { font-weight: 600; color: #374151; background: #fafafa; }
.lb-table td.small { color: #6b7280; font-size: 13px; }
.lb-table td.mono { font-family: var(--mono); font-size: 13px; white-space: nowrap; }
.lb-table td.score { font-weight: 600; }
.badge { display: inline-block; min-width: 24px; height: 24px; line-height: 24px; text-align: center;
         border-radius: 6px; background: #f3f4f6; color: #374151; font-weight: 600; }
.badge.top1 { background: #fde68a; } .badge.top2 { background: #e5e7eb; } .badge.top3 { background: #fca5a5; }
.lb-h2 { margin: 0 0 6px 0; font-size: 20px; }
.lb-caption { margin-top: 2px; color: #6b7280; font-size: 13px; }
.linklike { color: #2563eb; text-decoration: none; cursor: pointer; font-weight: 600; }
.linklike:hover { text-decoration: underline; }
.link-button { border: none; background: transparent; padding: 0; margin: 0; color: #2563eb; cursor: pointer; font-weight: 600; }
.link-button:hover { text-decoration: underline; }
.card { border: 1px solid #EEE; border-radius: 10px; padding: 14px 16px; margin-bottom: 14px; background: #fff; }
.card .head { display: flex; align-items: baseline; justify-content: space-between; }
.card .head .title { font-size: 18px; margin: 0; }
.card .head .meta { color: #6b7280; font-size: 12px; }
.hr { border: 0; border-top: 1px solid #eee; margin: 12px 0; }
.kpi { display:flex; gap:16px; }
.kpi .item { padding:10px 14px; background:#fafafa; border:1px solid #eee; border-radius:8px; }
.kpi .item .val { font-size:20px; font-weight:700; }
.kpi .item .lab { color:#6b7280; font-size:12px; margin-top:2px; }
.small-note { color:#6b7280; font-size:12px; }
</style>
"""
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

default_session_state = {
    "token": None,
    "user": None,
    "page": "login",
    "page_params": {},
    "error_message": None,
    "success_message": None,
    "login_error": None,
    "leaderboards": None,
    "leaderboard_rankings": None,
    "my_submissions": None,
    "my_leaderboards": None,
    "all_users": None,
    "current_leaderboard_edit": None,
    "log_modal_submission_id": None,
    "logs": None,
    "queue_status": {"pending_tasks": 0, "running_tasks": 0},
    "show_create_user_modal": False,
    "editing_user_password": None,
    "points_me": None,
    "points_me_ym": None,
    "my_sub_page": 1,
    "auto_home_top5": True,
    "auto_rank": True,
    "auto_list": True,
    "auto_logs": True,
    # 存储最新版 data_editor 行（不需要先点“更新方案”）
    "grid_rows_latest": [],
}
for k, v in default_session_state.items():
    if k not in st.session_state:
        st.session_state[k] = v

def _do_get_json(api_base: str, token: str | None, endpoint: str, params: dict | None = None, is_internal: bool = False, timeout: int = 8):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{api_base}/api/{'internal/' if is_internal else ''}{endpoint}"
    s = requests.Session()
    s.trust_env = False
    try:
        resp = s.get(url, headers=headers, params=params, timeout=timeout)
        if resp.status_code == 401:
            return {"_error": "401", "_msg": "Unauthorized"}
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()
    except requests.exceptions.HTTPError as e:
        try:
            detail = e.response.json()
        except Exception:
            detail = {"text": e.response.text}
        return {"_error": "HTTP", "_status": getattr(e.response, "status_code", None), "_detail": detail}
    except requests.exceptions.RequestException as e:
        return {"_error": "NET", "_detail": str(e)}
    except Exception as e:
        return {"_error": "UNEXPECTED", "_detail": str(e)}

@st.cache_data(ttl=TTL_DEFAULT_SEC, show_spinner=False)
def cached_get_default(api_base, token, endpoint, params_tuple, is_internal):
    params = dict(params_tuple) if params_tuple else None
    return _do_get_json(api_base, token, endpoint, params, is_internal=is_internal)

@st.cache_data(ttl=TTL_LOG_SEC, show_spinner=False)
def cached_get_logfast(api_base, token, endpoint, params_tuple, is_internal):
    params = dict(params_tuple) if params_tuple else None
    return _do_get_json(api_base, token, endpoint, params, is_internal=is_internal)

@st.cache_data(ttl=TTL_TOP5_SEC, show_spinner=False)
def cached_get_top5(api_base, token, endpoint, params_tuple, is_internal):
    params = dict(params_tuple) if params_tuple else None
    return _do_get_json(api_base, token, endpoint, params, is_internal=is_internal)

def api_get(endpoint, params=None, is_internal=False, fast=False, top5=False):
    params_tuple = tuple(sorted((params or {}).items()))
    if top5:
        return cached_get_top5(API_BASE_URL, st.session_state.token, endpoint, params_tuple, is_internal)
    if fast:
        return cached_get_logfast(API_BASE_URL, st.session_state.token, endpoint, params_tuple, is_internal)
    return cached_get_default(API_BASE_URL, st.session_state.token, endpoint, params_tuple, is_internal)

def api_request(method, endpoint, data=None, params=None, is_internal=False, timeout=10, files=None):
    """
    通用 API 请求封装：
    - 默认走 JSON（Content-Type: application/json）
    - 若提供 files 参数，则走 multipart/form-data（用于文件上传）
    """
    st.session_state.error_message = None

    # ====== 组装 URL ======
    url = f"{API_BASE_URL}/api/{'internal/' if is_internal else ''}{endpoint}"

    # ====== 组装 Header ======
    headers = {}
    # 有文件时不要强行设 Content-Type，交给 requests 自己算 boundary
    if files is None:
        headers["Content-Type"] = "application/json"
    if st.session_state.token:
        headers["Authorization"] = f"Bearer {st.session_state.token}"

    try:
        with st.spinner(f"正在 {method} {endpoint}..."):
            s = requests.Session()
            s.trust_env = False

            # ====== 根据是否有文件选择不同的请求方式 ======
            if files is not None:
                # data 作为表单字段，files 作为文件字段
                resp = s.request(
                    method,
                    url,
                    headers=headers,
                    data=data,
                    files=files,
                    params=params,
                    timeout=timeout,
                )
            else:
                # 原有 JSON 请求逻辑
                resp = s.request(
                    method,
                    url,
                    headers=headers,
                    json=data,
                    params=params,
                    timeout=timeout,
                )

        if resp.status_code == 401:
            st.warning("会话已过期，请重新登录。")
            logout(navigate=False)
            st.session_state.page = "login"
            st.rerun()
            return None

        resp.raise_for_status()

        if resp.status_code == 204 or not resp.content:
            return {"success": True}

        # 文件上传接口也大概率返回 JSON，这里沿用原逻辑
        return resp.json()

    except requests.exceptions.HTTPError as e:
        try:
            error_json = e.response.json()
            detail = error_json.get("msg") or error_json.get("error", json.dumps(error_json))
        except Exception:
            detail = e.response.text
        st.session_state.error_message = f"API 请求失败 ({e.response.status_code}): {detail}"
        st.error(st.session_state.error_message)
        return None
    except requests.exceptions.RequestException as e:
        st.session_state.error_message = f"网络错误: 无法连接到 API 服务 ({e})"
        st.error(st.session_state.error_message)
        return None
    except Exception as e:
        st.session_state.error_message = f"发生意外错误: {e}"
        st.error(st.session_state.error_message)
        return None

def render_evalscope_perf_ui():
    """
    一把梭：侧边栏参数面板 + 调用 /api/v1/perf + 结果表格 + 可视化 + 单 run percentiles 详情
    直接把这个函数粘到 app.py 里，然后在 page_map 里挂上即可：
        page_map["evalscope_perf"] = render_evalscope_perf_ui
    （侧边栏按钮你自己加一行 navigate("evalscope_perf") 就行）
    """
    import os, json, re
    import streamlit as st
    import requests

    # --------- session state (无需改 default_session_state) ----------
    ss = st.session_state
    ss.setdefault("evalscope_perf_last_resp", None)
    ss.setdefault("evalscope_perf_last_rows", None)
    ss.setdefault("evalscope_perf_meta", None)
    ss.setdefault("evalscope_perf_selected_run", None)

    # --------- helpers (都内置在这个函数里) ----------
    def _parse_int_list(text: str, default: list[int]) -> list[int]:
        if text is None:
            return default
        s = str(text).strip()
        if not s:
            return default
        # JSON list
        if s.startswith("[") and s.endswith("]"):
            try:
                arr = json.loads(s)
                out = []
                for x in arr:
                    if x is None or str(x).strip() == "":
                        continue
                    out.append(int(x))
                return out or default
            except Exception:
                return default
        # comma/space/newline
        s2 = s.replace("\n", " ").replace(",", " ")
        parts = [p.strip() for p in s2.split(" ") if p.strip()]
        out = []
        for p in parts:
            try:
                out.append(int(p))
            except Exception:
                pass
        return out or default

    def _infer_parallel_number(run_key: str):
        try:
            m1 = re.search(r"parallel_(\d+)", run_key)
            m2 = re.search(r"number_(\d+)", run_key)
            p = int(m1.group(1)) if m1 else None
            n = int(m2.group(1)) if m2 else None
            return p, n
        except Exception:
            return None, None

    def _safe_float(v):
        try:
            if v is None:
                return None
            return float(v)
        except Exception:
            return None

    def _extract_rows(resp_json: dict, chip_type: str, card_count: int) -> list[dict]:
        results = (resp_json or {}).get("results") or {}
        rows = []
        for run_key, obj in results.items():
            metrics = (obj or {}).get("metrics") or {}
            percentiles = (obj or {}).get("percentiles") or {}
            p, n = _infer_parallel_number(run_key)

            row = {
                "run_key": run_key,
                "parallel": p,
                "number": n,
                "chip_type": chip_type,
                "card_count": card_count,
                "output_dir": (resp_json or {}).get("output_dir"),
                "status": (resp_json or {}).get("status"),
                "_percentiles": percentiles,
            }

            wanted = [
                "Number of concurrency",
                "Total requests",
                "Succeed requests",
                "Failed requests",
                "Time taken for tests (s)",
                "Average latency (s)",
                "Average time to first token (s)",
                "Average inter-token latency (s)",
                "Average output tokens per request",
                "Output token throughput (tok/s)",
                "Total token throughput (tok/s)",
                "Request throughput (req/s)",
            ]
            for k in wanted:
                row[k] = metrics.get(k, None)

            rows.append(row)

        rows.sort(key=lambda r: (
            r.get("parallel") if r.get("parallel") is not None else 10**9,
            r.get("number") if r.get("number") is not None else 10**9
        ))
        return rows

    def _render_percentiles_table(percentiles: dict):
        if not isinstance(percentiles, dict) or not percentiles:
            st.info("该 run 没有 percentiles 数据。")
            return
        labels = percentiles.get("Percentiles") or []
        if not labels:
            st.info("该 run 的 percentiles 缺少 Percentiles 列。")
            return

        cols = ["Percentiles"] + [k for k in percentiles.keys() if k != "Percentiles"]
        table = []
        for i, lab in enumerate(labels):
            row = {"Percentiles": lab}
            for k in cols[1:]:
                arr = percentiles.get(k) or []
                row[k] = arr[i] if i < len(arr) else None
            table.append(row)
        st.dataframe(table, use_container_width=True, hide_index=True)

    # --------- UI ----------
    st.title("⚡ EvalsScope 性能测试（Perf）")
    st.caption("侧边栏填写参数 -> 调用 /api/v1/perf -> 展示 metrics 表格 + 可视化 + percentiles 详情。")

    # Sidebar panel
    st.sidebar.markdown("---")
    with st.sidebar.expander("⚡ Perf 参数面板", expanded=True):
        evalscope_api_base = st.text_input(
            "EvalsScope API Base URL",
            value=os.getenv("EVALSCOPE_API_BASE_URL") or "http://evalscope-api-svc:80",
            help="集群内推荐：http://evalscope-api-svc:80",
            key="perf_evalscope_base",
        )

        chip_type = st.text_input(
            "芯片类型*（字符串）",
            value=(ss.get("evalscope_perf_meta") or {}).get("chip_type", ""),
            placeholder="例如：Ascend310P / Ascend910B / A800 ...",
            key="perf_chip_type",
        )
        card_count = st.number_input(
            "卡数量*（整数）",
            min_value=1, max_value=1024,
            value=int((ss.get("evalscope_perf_meta") or {}).get("card_count", 1) or 1),
            step=1,
            key="perf_card_count",
        )

        model = st.text_input("model（传给 perf 的 model 字段）", value="/models/Qwen/Qwen3-8B/", key="perf_model")
        url = st.text_input("url（OpenAI chat/completions）", value="http://localhost:16015/v1/chat/completions", key="perf_llm_url")
        api = st.selectbox("api", ["openai"], index=0, key="perf_api_type")
        api_key = st.text_input("api_key（可空）", value="", type="password", placeholder="sk-xxxx", key="perf_api_key")

        number_text = st.text_input(" 测试条数（支持 list/逗号/空格）", value="[10,20]", key="perf_number_text")
        parallel_text = st.text_input("并发（应和条数逐一对应）", value="[5,10]", key="perf_parallel_text")

        timeout_sec = st.number_input(
            "请求超时（秒）", min_value=10, max_value=66000, value=1800, step=10,
            help="Perf 可能跑很久，建议给大一点",
            key="perf_timeout_sec",
        )

        run_btn = st.button("🚀 开始 Perf 测试", type="primary", use_container_width=True)

    # Run
    if run_btn:
        chip_type2 = (chip_type or "").strip()
        if not chip_type2:
            st.error("芯片类型是必填项。")
            st.stop()
        if int(card_count) <= 0:
            st.error("卡数量必须 > 0。")
            st.stop()

        numbers = _parse_int_list(number_text, default=[10, 20])
        parallels = _parse_int_list(parallel_text, default=[5, 10])
        number_payload = numbers[0] if len(numbers) == 1 else numbers
        parallel_payload = parallels[0] if len(parallels) == 1 else parallels

        payload = {
            "model": model,
            "url": url,
            "api": api,
            "api_key": api_key,
            "number": number_payload,
            "parallel": parallel_payload,
        }

        ss["evalscope_perf_meta"] = {
            "chip_type": chip_type2,
            "card_count": int(card_count),
            "model": model,
            "url": url,
            "api_base": evalscope_api_base,
        }

        status_ctx = None
        try:
            status_ctx = st.status("正在提交 Perf 请求…", expanded=True)
            status_ctx.write(f"POST {evalscope_api_base.rstrip('/')}/api/v1/perf")
            status_ctx.write(f"number={number_payload}, parallel={parallel_payload}")
        except Exception:
            status_ctx = None

        try:
            with st.spinner("Perf 测试运行中…（请等待后端返回结果）"):
                s = requests.Session()
                s.trust_env = False
                resp = s.post(
                    f"{evalscope_api_base.rstrip('/')}/api/v1/perf",
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=(5, int(timeout_sec)),
                )
            resp.raise_for_status()
            data = resp.json()

            rows = _extract_rows(data, chip_type2, int(card_count))
            ss["evalscope_perf_last_resp"] = data
            ss["evalscope_perf_last_rows"] = rows

            if status_ctx is not None:
                try:
                    status_ctx.update(label="Perf 测试完成 ✅", state="complete", expanded=False)
                except Exception:
                    pass
            st.success("Perf 测试完成，已解析并展示结果。")

        except requests.exceptions.RequestException as e:
            if status_ctx is not None:
                try:
                    status_ctx.update(label="Perf 请求失败 ❌", state="error", expanded=True)
                except Exception:
                    pass
            st.error(f"Perf 请求失败：{e}")
        except Exception as e:
            if status_ctx is not None:
                try:
                    status_ctx.update(label="解析失败 ❌", state="error", expanded=True)
                except Exception:
                    pass
            st.error(f"发生意外错误：{e}")

    # Show last
    last = ss.get("evalscope_perf_last_resp")
    rows = ss.get("evalscope_perf_last_rows") or []
    meta = ss.get("evalscope_perf_meta") or {}

    if not last:
        st.info("还没有运行 Perf。请在侧边栏填写参数并点击「开始 Perf 测试」。")
        return

    c1, c2, c3 = st.columns([1.2, 1.2, 3])
    with c1:
        st.metric("芯片类型", meta.get("chip_type", "—"))
    with c2:
        st.metric("卡数量", str(meta.get("card_count", "—")))
    with c3:
        st.write(f"**output_dir**: `{(last or {}).get('output_dir','—')}`")

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.subheader("📋 结果表（metrics 汇总）")

    if not rows:
        st.warning("results 为空或解析失败。")
        return

    show_rows = []
    for r in rows:
        rr = dict(r)
        rr.pop("_percentiles", None)
        show_rows.append(rr)
    st.dataframe(show_rows, use_container_width=True, hide_index=True)

    st.subheader("📈 可视化（按 parallel / number）")
    try:
        import pandas as pd
        import altair as alt

        df = pd.DataFrame(rows)
        for col in [
            "Output token throughput (tok/s)",
            "Total token throughput (tok/s)",
            "Request throughput (req/s)",
            "Average latency (s)",
            "Average time to first token (s)",
        ]:
            if col in df.columns:
                df[col] = df[col].apply(_safe_float)

        metric = st.selectbox(
            "选择要画的指标",
            options=[
                "Output token throughput (tok/s)",
                "Total token throughput (tok/s)",
                "Request throughput (req/s)",
                "Average latency (s)",
                "Average time to first token (s)",
            ],
            index=0,
            key="perf_metric_select",
        )

        chart = (
            alt.Chart(df.dropna(subset=["parallel", "number"]))
            .mark_line(point=True)
            .encode(
                x=alt.X("parallel:Q", title="parallel（并发）"),
                y=alt.Y(f"{metric}:Q", title=metric),
                color=alt.Color("number:N", title="number（请求数）"),
                tooltip=[
                    alt.Tooltip("run_key:N", title="run"),
                    alt.Tooltip("parallel:Q"),
                    alt.Tooltip("number:Q"),
                    alt.Tooltip(f"{metric}:Q"),
                    alt.Tooltip("Average latency (s):Q"),
                    alt.Tooltip("Average time to first token (s):Q"),
                ],
            )
            .properties(height=320)
        )
        st.altair_chart(chart, use_container_width=True)

    except Exception as e:
        st.caption(f"可视化失败（可能缺少 pandas/altair 或数据异常）：{e}")

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.subheader("🔎 单个 Run 详情（percentiles）")

    run_keys = [r.get("run_key") for r in rows if r.get("run_key")]
    if not run_keys:
        st.info("没有可选的 run_key。")
        return

    default_run = ss.get("evalscope_perf_selected_run") or run_keys[0]
    if default_run not in run_keys:
        default_run = run_keys[0]

    sel = st.selectbox(
        "选择 run_key 查看 percentiles",
        options=run_keys,
        index=run_keys.index(default_run),
        key="perf_run_key_select",
    )
    ss["evalscope_perf_selected_run"] = sel

    target = next((r for r in rows if r.get("run_key") == sel), None)
    if target:
        with st.expander("percentiles 表", expanded=True):
            _render_percentiles_table(target.get("_percentiles") or {})


# ======== 新增：下载文件用的 API（用于 Excel） ========
def api_get_file(endpoint, params=None, is_internal=False, timeout=30):
    """
    通过带 JWT 的 GET 请求拉取二进制文件（Excel），返回 (bytes, filename)。
    """
    st.session_state.error_message = None
    headers = {}
    if st.session_state.token:
        headers["Authorization"] = f"Bearer {st.session_state.token}"
    url = f"{API_BASE_URL}/api/{'internal/' if is_internal else ''}{endpoint}"
    try:
        with st.spinner(f"正在下载 {endpoint} ..."):
            s = requests.Session(); s.trust_env = False
            resp = s.get(url, headers=headers, params=params, timeout=timeout)
        if resp.status_code == 401:
            st.warning("会话已过期，请重新登录。")
            logout(navigate=False)
            st.session_state.page = "login"
            st.rerun()
            return None, None
        if resp.status_code >= 400:
            try:
                err = resp.json()
                msg = err.get("msg") or err.get("error") or resp.text
            except Exception:
                msg = resp.text
            st.error(f"文件下载失败 ({resp.status_code}): {msg}")
            return None, None

        content = resp.content
        cd = resp.headers.get("Content-Disposition", "")
        filename = None

        # 粗略解析文件名
        if "filename*=" in cd:
            # 形如：filename*=UTF-8''xxx.xlsx
            try:
                part = cd.split("filename*=")[1]
                part = part.strip().strip(";")
                if "''" in part:
                    part = part.split("''", 1)[1]
                filename = part.strip('"')
            except Exception:
                filename = None
        if not filename and "filename=" in cd:
            try:
                part = cd.split("filename=")[1]
                part = part.strip().strip(";")
                filename = part.strip('"')
            except Exception:
                filename = None

        if not filename:
            filename = "download.xlsx"

        return content, filename
    except requests.exceptions.RequestException as e:
        st.error(f"网络错误: 无法下载文件 ({e})")
        return None, None
    except Exception as e:
        st.error(f"下载文件时发生错误: {e}")
        return None, None

# ======== 认证 & 导航 ========
def parse_user_from_token(token: str):
    if not token:
        return None
    try:
        payload = jwt.decode(
            token, options={"verify_signature": False, "verify_exp": False}, algorithms=[JWT_ALGORITHM]
        )
        uid = payload.get("sub") or payload.get("identity")
        if not uid:
            return None
        return {
            "id": str(uid),
            "username": f"用户 {uid}",
            "role": payload.get("role", "participant"),
        }
    except Exception:
        return None

def attempt_login(username, password):
    st.session_state.token = None
    st.session_state.user = None
    st.session_state.login_error = None
    resp = api_request("POST", "login", data={"username": username, "password": password})
    if resp and "access_token" in resp:
        token = resp["access_token"]
        user = parse_user_from_token(token) or {}
        user["username"] = username or user.get("username", "")
        st.session_state.token = token
        st.session_state.user = user
        st.session_state.page = "public_leaderboards"
        st.success("登录成功！")
        st.rerun()
    else:
        if not st.session_state.error_message:
            st.session_state.login_error = "用户名或密码错误。"

def logout(navigate=True):
    keep_keys = ["queue_status"]
    for k in list(st.session_state.keys()):
        if k in keep_keys:
            continue
        if k in default_session_state:
            st.session_state[k] = default_session_state[k]
    st.success("您已成功登出。")
    if navigate:
        st.rerun()

def navigate(page_name, params=None):
    st.session_state.page = page_name
    st.session_state.page_params = params or {}
    st.session_state.error_message = None
    st.session_state.success_message = None
    st.rerun()

def check_role(allowed_roles):
    return bool(st.session_state.user and st.session_state.user.get("role") in allowed_roles)

# ======== 时间 & 表格 渲染 ========
def _fmt_ts(ts_iso):
    if not ts_iso:
        return "—"
    try:
        return datetime.fromisoformat(ts_iso).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts_iso

def build_rank_table_html(rows, limit=None):
    if not rows:
        return (
            '<div class="table-wrap"><table class="lb-table"><thead><tr>'
            '<th>排名</th><th>任务名称</th><th>镜像</th><th>分数</th><th>用户</th><th>时间</th>'
            '</tr></thead><tbody><tr><td colspan="6" class="small">暂无记录</td></tr></tbody></table></div>'
        )

    if limit is not None:
        rows = rows[:limit]

    def _flatten_metrics(data, parent_key=""):
        """
        通用 metrics 展开：
        - 任意层级 dict → a.b.c 形式的 key
        - 跳过整个 eval_details / evaldetails 分支
        - 顶层 score 不展开（已经有单独的“分数”列）
        """
        flat = {}
        if not isinstance(data, dict):
            return flat

        for k, v in data.items():
            k_str = str(k)
            k_lower = k_str.lower()

            # 跳过 eval_details 整个分支
            if "eval_details" in k_lower.replace("_", ""):
                continue

            # 顶层 score 不展开（parent_key 为空表示当前在顶层）
            if not parent_key and k_str == "score":
                continue

            new_key = f"{parent_key}.{k_str}" if parent_key else k_str

            if isinstance(v, dict):
                flat.update(_flatten_metrics(v, new_key))
            else:
                flat[new_key] = v
        return flat

    # 额外指标列（来自 metrics 的所有展开 key）
    metric_keys, seen = [], set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        m = r.get("metrics") or {}
        flat = _flatten_metrics(m)
        for k in flat.keys():
            if k not in seen:
                seen.add(k)
                metric_keys.append(k)

    ths = ["排名", "任务名称", "镜像", "分数", "用户", "时间"] + [html_escape(str(k)) for k in metric_keys]
    tr_html = []

    for r in rows:
        if not isinstance(r, dict):
            continue

        rank = int(r.get("rank", 0)) if r.get("rank") else 0
        badge_cls = (
            "badge top1" if rank == 1 else
            ("badge top2" if rank == 2 else
             ("badge top3" if rank == 3 else "badge"))
        )

        # 主显示：用户提交时填写的任务名称（submission_name）
        job_name = r.get("submission_name") or r.get("name") or "—"

        # 附属：K8s Job ID + submission_id
        jbid = r.get("job_name") or r.get("k8s_job_name") or ""
        sub_id = r.get("submission_id") or r.get("id") or ""

        if jbid or sub_id:
            job_cell = (
                f"<div class='mono'>{html_escape(str(job_name))}</div>"
                "<div class='small'>"
                + (f"<span class='mono'>JobID: {html_escape(str(jbid))}</span> " if jbid else "")
                + (f"<span class='mono'>(sub#{html_escape(str(sub_id))})</span>" if sub_id else "")
                + "</div>"
            )
        else:
            job_cell = f"<div class='mono'>{html_escape(str(job_name))}</div>"

        image = r.get("algorithm_image_url") or "—"
        score = r.get("score", "—")
        user = r.get("username", "—")
        ts = _fmt_ts(r.get("last_submitted"))

        # 当前行的 metrics 展开
        m_src = r.get("metrics") or {}
        flat_m = _flatten_metrics(m_src)

        metric_tds = []
        for k in metric_keys:
            v = flat_m.get(k, "")

            # 通用格式化：float / list / dict 都兼容
            if isinstance(v, float):
                v = f"{v:.6g}"
            elif isinstance(v, int):
                v = str(v)
            elif isinstance(v, list):
                # 简单处理：标量列表 → 逗号分隔；dict 列表 → N items
                if v and all(not isinstance(x, (dict, list)) for x in v):
                    v = ", ".join(str(x) for x in v)
                else:
                    v = f"{len(v)} items"
            elif isinstance(v, dict):
                try:
                    v = json.dumps(v, ensure_ascii=False)
                except Exception:
                    v = str(v)
            elif v is None:
                v = ""

            metric_tds.append(f"<td class='mono'>{html_escape(str(v))}</td>")

        tr_html.append(
            "<tr>"
            f"<td><span class='{badge_cls}'>{rank if rank else ''}</span></td>"
            f"<td>{job_cell}</td>"
            f"<td class='mono'>{html_escape(str(image))}</td>"
            f"<td class='score'>{html_escape(str(score))}</td>"
            f"<td>{html_escape(str(user))}</td>"
            f"<td class='small'>{html_escape(str(ts))}</td>"
            + "".join(metric_tds) +
            "</tr>"
        )

    table = (
        "<div class='table-wrap'>"
        "<table class='lb-table'>"
        "<thead><tr>"
        + "".join(f"<th>{h}</th>" for h in ths) +
        "</tr></thead>"
        f"<tbody>{''.join(tr_html)}</tbody>"
        "</table></div>"
    )
    return table

# ======== 侧边栏 ========
def render_sidebar():
    st.sidebar.title("导航")
    # ⚠️ 不要在 Agent 面板上跑队列自动刷新：它会在 Agent 流式输出（可能 >15s）中途
    # 触发整页 rerun，打断 _stream_and_render 的阻塞循环，导致工具调用与回答被丢弃、
    # 不写入 agent_chat（即“工具调用没打通”）。流式进行中也强制关闭。
    _agent_active = (st.session_state.get("page") == "agent_panel") or st.session_state.get("agent_streaming", False)
    st_auto(QUEUE_REFRESH_MS, key="auto_queue_sidebar", enable=not _agent_active)

    if st.sidebar.button("🏆 榜单广场", use_container_width=True,
                         type="primary" if st.session_state.page == "public_leaderboards" else "secondary"):
        navigate("public_leaderboards")

    if st.sidebar.button("⚡ 性能测试 (Perf)", use_container_width=True,
                         type="primary" if st.session_state.page == "evalscope_perf" else "secondary"):
        navigate("evalscope_perf")
    if st.sidebar.button("🤖 Agent 面板", use_container_width=True,
                         type="primary" if st.session_state.page == "agent_panel" else "secondary"):
        navigate("agent_panel")

    if st.session_state.token and st.session_state.user:
        st.sidebar.markdown("---")
        st.sidebar.subheader(f"欢迎, {st.session_state.user.get('username')} ({st.session_state.user.get('role')})")

        if st.sidebar.button("🚀 我的提交", use_container_width=True,
                             type="primary" if st.session_state.page == "my_submissions" else "secondary"):
            navigate("my_submissions")

        if st.sidebar.button("📈 积分中心", use_container_width=True,
                             type="primary" if st.session_state.page == "points_center" else "secondary"):
            navigate("points_center")

        if st.session_state.user.get("role") in ["creator", "admin"]:
            if st.sidebar.button("📋 管理榜单", use_container_width=True,
                                 type="primary" if st.session_state.page == "manage_leaderboards" else "secondary"):
                navigate("manage_leaderboards")

        if st.session_state.user.get("role") in ["admin"]:
            if st.sidebar.button("👥 用户管理", use_container_width=True,
                                 type="primary" if st.session_state.page == "admin_users" else "secondary"):
                navigate("admin_users")

        if st.sidebar.button("登出", on_click=logout, use_container_width=True):
            pass
    else:
        if st.sidebar.button("登录", use_container_width=True,
                             type="primary" if st.session_state.page == "login" else "secondary"):
            navigate("login")

    st.sidebar.markdown("---")
    q = api_get("queue/status")
    if isinstance(q, dict) and "_error" in q:
        st.sidebar.metric("⏳ 排队中任务", "N/A")
        st.sidebar.metric("⚙️ 运行中任务", "N/A")
        st.sidebar.caption(f"队列获取失败：{q.get('_error')}")
    else:
        st.sidebar.metric("⏳ 排队中任务", (q or {}).get("pending_tasks", "N/A"))
        st.sidebar.metric("⚙️ 运行中任务", (q or {}).get("running_tasks", "N/A"))

    if st.sidebar.button("🔄 刷新队列", use_container_width=True):
        cached_get_default.clear()
        st.rerun()

# ======== 登录页 ========
def render_login_page():
    st.title("🏆 NPU 算法榜单 - 请登录")
    with st.form(key="login_form"):
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录", type="primary")
        if submitted:
            attempt_login(username, password)
    if st.session_state.login_error:
        st.error(st.session_state.login_error)
        st.session_state.login_error = None

# ======== 首页（Top5） ========
# ======== 首页（Top5） ========
def render_public_leaderboards():
    st.title("🏆 榜单广场")
    st.caption("浏览公开榜单；按需展开榜单详情与 Top5（含 Job & 镜像）")
    st_auto(HOME_TOP5_REFRESH_MS, key="auto_home_top5_tick", enable=st.session_state.get("auto_home_top5", True))

    # ---------- 状态初始化 ----------
    if "home_search_kw" not in st.session_state:
        st.session_state.home_search_kw = ""  # 已生效过滤词
    if "home_search_kw_input" not in st.session_state:
        st.session_state.home_search_kw_input = st.session_state.home_search_kw  # 输入框编辑态

    # ---------- 控制区（紧凑） ----------
    colA, colB, colC, colD, colE = st.columns([1.2, 1.2, 5, 1.2, 1.2])
    with colA:
        if st.button("🔄 刷新", use_container_width=True):
            cached_get_default.clear()
            cached_get_top5.clear()
            st.session_state.leaderboards = None
            st.rerun()

    with colB:
        st.session_state.auto_home_top5 = st.checkbox(
            "⏱️ 自动",
            value=st.session_state.get("auto_home_top5", True),
            key="auto_home_top5_cb",
            help=f"每 {HOME_TOP5_REFRESH_MS} ms 自动刷新页面（仅对已展开的 Top5 触发请求）",
        )

    with colC:
        st.session_state.home_search_kw_input = st.text_input(
            "🔎 搜索榜单",
            value=st.session_state.home_search_kw_input,
            placeholder="名称 / 描述 / 发布者 / 版本 关键字（输入后点右侧“搜索”才生效）",
            label_visibility="collapsed",
        )

    with colD:
        if st.button("🔍 搜索", use_container_width=True):
            st.session_state.home_search_kw = (st.session_state.home_search_kw_input or "").strip()
            st.rerun()

    with colE:
        if st.button("↩️ 回退", use_container_width=True):
            st.session_state.home_search_kw_input = st.session_state.home_search_kw
            st.rerun()

    # ---------- 拉取榜单列表 ----------
    if st.session_state.leaderboards is None:
        data = api_get("leaderboards")
        if isinstance(data, list):
            st.session_state.leaderboards = data
        else:
            st.info("正在加载榜单列表...")
            return

    boards_all = st.session_state.leaderboards or []
    if not boards_all:
        st.info("目前还没有公开的榜单。")
        return

    # ---------- 过滤 + 展示统计 ----------
    kw = (st.session_state.get("home_search_kw") or "").strip()
    boards = boards_all
    if kw:
        kw_l = kw.lower()

        def _hit(b: dict) -> bool:
            parts = [
                str(b.get("name", "")),
                str(b.get("description", "")),
                str(b.get("owner_username", "")),
                str(b.get("version", "")),
            ]
            return kw_l in (" ".join(parts).lower())

        boards = [b for b in boards_all if _hit(b)]
        st.caption(f"🔎 当前搜索：**{kw}**（命中 {len(boards)}/{len(boards_all)}）")
    else:
        st.caption(f"共 **{len(boards_all)}** 个公开榜单")

    if not boards:
        st.info("没有匹配到榜单。")
        return

    # ---------- UI 小组件 ----------
    def _pill(text: str) -> str:
        return (
            "<span style='display:inline-block;padding:2px 8px;margin-right:6px;"
            "border:1px solid rgba(49,51,63,0.25);border-radius:999px;"
            "font-size:12px;line-height:18px;opacity:0.9;'>"
            f"{html_escape(text)}"
            "</span>"
        )

    def _fmt_float(v, nd=4) -> str:
        try:
            if v is None:
                return "—"
            return f"{float(v):.{nd}f}"
        except Exception:
            s = str(v).strip()
            return s if s else "—"

    def _fmt_int(v) -> str:
        try:
            if v is None:
                return "—"
            return str(int(v))
        except Exception:
            s = str(v).strip()
            return s if s else "—"

    # ---------- 渲染榜单卡片 ----------
    for board in boards:
        bid = board.get("id")
        name = str(board.get("name", "Unnamed"))
        version = str(board.get("version", ""))
        owner = str(board.get("owner_username", "N/A"))
        diff = _fmt_float(board.get("difficulty_factor", 1.0), nd=2)

        # 展示用 SOTA：优先用实时计算 current_sota_score（榜单当前最佳分）
        current_sota = _fmt_float(board.get("current_sota_score"), nd=4)

        # 配置/基准 SOTA：原字段（可能未维护）
        configured_sota = _fmt_float(board.get("sota_score"), nd=4)

        submit_cnt = _fmt_int(board.get("submission_count"))
        desc = (board.get("description") or "").strip()

        with st.container():
            st.markdown('<div class="card">', unsafe_allow_html=True)

            # === 顶部一行：榜单名（左） + 立即打榜（右，最右侧）===
            topL, topR = st.columns([6, 1.4])
            with topL:
                if st.button(f"🏷️ {name}", key=f"to_{bid}", help="查看榜单详情", type="secondary"):
                    navigate("leaderboard_detail", params={"id": bid})
            with topR:
                can_submit = bool(st.session_state.get("token"))
                if st.button(
                    "🚀 立即打榜",
                    key=f"go_submit_{bid}",
                    use_container_width=True,
                    type="primary",
                    disabled=not can_submit,
                    help="登录后即可提交你的算法" if not can_submit else "跳转到提交页面",
                ):
                    navigate("submit", params={"id": bid})

            # === 第二行：左侧信息 + 右侧 Top5（可折叠）===
            colL, colR = st.columns([3.2, 1.8])

            with colL:
                pills_html = (
                    "<div style='margin-top:6px;margin-bottom:2px;'>"
                    + _pill(f"SOTA {current_sota}")
                    + _pill(f"提交 {submit_cnt} 次")
                    + _pill(f"难度 {diff}")
                    + _pill(f"版本 {version}")
                    + _pill(f"发布者 {owner}")
                    + "</div>"
                )
                st.markdown(pills_html, unsafe_allow_html=True)

                if desc:
                    short = desc if len(desc) <= 90 else (desc[:90] + "…")
                    st.markdown(
                        f"<div style='opacity:0.85;font-size:13px;margin-top:6px;'>{html_escape(short)}</div>",
                        unsafe_allow_html=True,
                    )

                with st.expander("📌 榜单详情", expanded=False):
                    st.markdown(
                        (
                            "<div class='lb-caption'>"
                            f"版本: {html_escape(version)} | "
                            f"发布者: {html_escape(owner)} | "
                            f"难度系数: {html_escape(diff)} | "
                            f"提交次数: {html_escape(submit_cnt)}"
                            "</div>"
                            "<div class='lb-caption' style='margin-top:4px;'>"
                            f"当前最佳分 (current_sota_score): {html_escape(current_sota)} | "
                            f"配置/基准 SOTA (sota_score): {html_escape(configured_sota)}"
                            "</div>"
                        ),
                        unsafe_allow_html=True,
                    )
                    if desc:
                        st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
                        st.write(desc)

            with colR:
                show_top5 = st.toggle("📊 展开 Top5", value=False, key=f"top5_toggle_{bid}")
                if show_top5:
                    r = api_get(
                        f"leaderboard/{bid}/rankings",
                        params={"include_job": "1", "per_submission": "1", "limit": "5"},
                        top5=True,
                    )
                    top_rows = (r or {}).get("rankings", []) if isinstance(r, dict) else []

                    if top_rows and isinstance(top_rows[0], dict):
                        u = top_rows[0].get("username") or top_rows[0].get("user") or top_rows[0].get("owner") or "N/A"
                        s = top_rows[0].get("score") or top_rows[0].get("final_score") or top_rows[0].get("score_pct")
                        s_show = _fmt_float(s, nd=4) if s is not None else "—"
                        st.caption(f"🥇 Top1：{u} · {s_show}")

                    table_html = build_rank_table_html(top_rows, limit=5)
                    st.markdown(table_html, unsafe_allow_html=True)

            st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)


def render_leaderboard_detail():
    import json
    import math
    import os
    import re
    from html import escape as html_escape

    import pandas as pd
    import streamlit as st

    leaderboard_id = st.session_state.page_params.get("id")
    if not leaderboard_id:
        st.error("缺少榜单 ID。请从首页进入。")
        return

    # 自动刷新（沿用现有逻辑）
    st_auto(RANK_REFRESH_MS, key=f"auto_rank_{leaderboard_id}", enable=st.session_state.get("auto_rank", True))

    # 取榜单基础信息（沿用现有逻辑）
    board_info = None
    if st.session_state.leaderboards:
        board_info = next((b for b in st.session_state.leaderboards if b["id"] == leaderboard_id), None)

    title_txt = f"🏆 {board_info['name']}" if board_info else f"榜单 #{leaderboard_id}"
    st.title(title_txt)
    if board_info:
        st.caption(
            f"版本: {board_info['version']} | 发布者: {board_info.get('owner_username', 'N/A')} | "
            f"难度系数: {board_info.get('difficulty_factor','1.0')} | SOTA: {board_info.get('sota_score','—')}"
        )
        if board_info.get("description"):
            st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
            st.write(board_info["description"])

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.subheader("实时排名（去重列名 + 可隐藏/排序 + Plotly 多边形图 + 按题正确率分析）")

    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button("🔄 刷新排名", use_container_width=True):
        cached_get_default.clear()
        st.session_state.leaderboard_rankings = None
        st.rerun()

    st.session_state.auto_rank = c2.checkbox(
        "⏱️ 自动刷新",
        value=st.session_state.get("auto_rank", True),
        help=f"每 {RANK_REFRESH_MS} ms 自动刷新当前榜单",
    )

    # --------------------------
    # 1) 拉取 rankings 数据（沿用现有逻辑）
    # --------------------------
    if (
        st.session_state.leaderboard_rankings is None
        or st.session_state.leaderboard_rankings.get("leaderboard_id") != leaderboard_id
    ):
        data = api_get(
            f"leaderboard/{leaderboard_id}/rankings",
            params={"include_job": "1", "per_submission": "1"},
        )
        if isinstance(data, dict):
            st.session_state.leaderboard_rankings = {"leaderboard_id": leaderboard_id, "data": data}

    rows = []
    if st.session_state.leaderboard_rankings:
        data = st.session_state.leaderboard_rankings["data"]
        rows = data.get("rankings", []) or []

    if not rows:
        st.info("正在加载排名...（或当前榜单暂无数据）")
        st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
        if st.session_state.token and st.button("🚀 我也要打榜", type="primary"):
            navigate("submit", params={"id": leaderboard_id})
        elif not st.session_state.token:
            st.info("登录后即可提交您的算法进行评测。")
        return

    # --------------------------
    # 2) 扁平化 + “属性A.属性B / 属性B” 按 B 去重（展示 B）
    # --------------------------
    def _flatten_any(obj, parent_key: str = "", out: dict | None = None) -> dict:
        if out is None:
            out = {}
        if isinstance(obj, dict):
            for k, v in obj.items():
                k = str(k)
                new_key = f"{parent_key}.{k}" if parent_key else k
                if isinstance(v, dict):
                    _flatten_any(v, new_key, out)
                elif isinstance(v, list):
                    try:
                        out[new_key] = json.dumps(v, ensure_ascii=False)
                    except Exception:
                        out[new_key] = str(v)
                else:
                    out[new_key] = v
        else:
            out[parent_key or "value"] = obj
        return out

    def _choose_key_by_suffix(keys: list[str], suffix: str) -> str:
        if suffix in keys:
            return suffix
        keys_sorted = sorted(keys, key=lambda x: (x.count("."), len(x)))
        return keys_sorted[0]

    def _dedupe_by_suffix(flat: dict, keep_keys: set[str] | None = None) -> dict:
        if keep_keys is None:
            keep_keys = set()

        buckets: dict[str, list[str]] = {}
        for k in flat.keys():
            if k in keep_keys:
                continue
            suffix = k.split(".")[-1]
            buckets.setdefault(suffix, []).append(k)

        suffix_to_key: dict[str, str] = {}
        for suffix, ks in buckets.items():
            suffix_to_key[suffix] = _choose_key_by_suffix(ks, suffix)

        out = {}
        for k in keep_keys:
            if k in flat:
                out[k] = flat[k]
        for suffix, chosen_k in suffix_to_key.items():
            out[suffix] = flat.get(chosen_k)
        return out

    CORE_KEYS = {
        "rank",
        "submission_id",
        "id",
        "submission_name",
        "name",
        "username",
        "score",
        "created_at",
        "time",
        "job_name",
        "algo_image",
        "algorithm_image",
        "evaluator_image",
        "image",
        "commitid",
        "commit_id",
    }

    deduped_rows = []
    for r in rows:
        flat = _flatten_any(r)
        disp = _dedupe_by_suffix(flat, keep_keys=CORE_KEYS)

        sid = r.get("submission_id") or r.get("id") or disp.get("submission_id") or disp.get("id")
        sname = r.get("submission_name") or r.get("name") or disp.get("submission_name") or disp.get("name") or ""
        uname = r.get("username") or disp.get("username") or "?"
        rank = r.get("rank") if r.get("rank") is not None else disp.get("rank")
        score = r.get("score") if r.get("score") is not None else disp.get("score")
        ts = r.get("time") or r.get("created_at") or disp.get("time") or disp.get("created_at") or ""
        commitid = r.get("commitid") or disp.get("commitid") or disp.get("commit_id")

        # 兜底：从 metrics / algo_env 推断 commitid（如果后端没给）
        if not commitid:
            m = r.get("metrics") or {}
            if isinstance(m, dict):
                for k in ("commitid", "commit_id", "git_commit", "git_sha", "sha", "revision"):
                    v = m.get(k)
                    if v:
                        commitid = str(v)
                        break
        if not commitid:
            env = r.get("algo_env") or {}
            if isinstance(env, dict):
                for k in ("COMMITID", "COMMIT_ID", "GIT_COMMIT", "GIT_SHA", "REVISION", "SHA"):
                    v = env.get(k)
                    if v:
                        commitid = str(v)
                        break

        disp["rank"] = rank
        disp["submission_id"] = str(sid) if sid is not None else ""
        disp["submission_name"] = sname
        disp["username"] = uname
        disp["score"] = score
        disp["time"] = ts
        disp["commitid"] = commitid or ""

        deduped_rows.append(disp)

    df = pd.DataFrame(deduped_rows)

    # 数值列尽量转 numeric（便于排序/雷达图）
    for c in df.columns:
        if c in ("submission_name", "username", "submission_id", "time", "commitid"):
            continue
        if df[c].dtype == object:
            df[c] = pd.to_numeric(df[c], errors="ignore")

    st.markdown(
        """
<style>
.block-container { padding-top: 1.0rem; }
div[data-testid="stDataFrame"] { border-radius: 12px; }
</style>
        """,
        unsafe_allow_html=True,
    )

    # --------------------------
    # 3) 交互：隐藏算法/属性 + 排序（降序） + 点击列头排序（st.dataframe 自带）
    # --------------------------
    df["_algo_label"] = df.apply(
        lambda x: f"#{x.get('rank','?')} | {x.get('submission_name','')} | 用户:{x.get('username','?')} | id:{x.get('submission_id','')}",
        axis=1,
    )
    algo_options = df["_algo_label"].tolist()

    preferred_defaults = [
        "rank",
        "submission_name",
        "username",
        "score",
        "commitid",
        "naive_context_recall",   # ★ 新增
        "statement_recall",       # ★ 新增
        "time",
    ]
    default_cols = [c for c in preferred_defaults if c in df.columns]
    if not default_cols:
        default_cols = [c for c in ["rank", "submission_name", "username", "score", "time"] if c in df.columns]

    with st.expander("🔧 自定义视图（隐藏算法/属性 + 排序）", expanded=True):
        a1, a2 = st.columns([2, 2])
        with a1:
            keep_algos = st.multiselect(
                "显示哪些算法（可直接 x 掉不关心的算法）",
                options=algo_options,
                default=algo_options,
                key=f"lb_keep_algos_{leaderboard_id}",
            )
        with a2:
            all_cols = [c for c in df.columns if c not in ("_algo_label",)]
            visible_cols = st.multiselect(
                "显示哪些属性列（可直接 x 掉不关心的属性）",
                options=all_cols,
                default=default_cols,
                key=f"lb_visible_cols_{leaderboard_id}",
            )

        b1, b2, b3 = st.columns([2, 1, 2])
        with b1:
            sort_cols = st.multiselect(
                "排序列（从左到右为优先级；默认降序）",
                options=[c for c in visible_cols],
                default=[c for c in ["score"] if c in visible_cols],
                key=f"lb_sort_cols_{leaderboard_id}",
            )
        with b2:
            sort_desc = st.checkbox("降序", value=True, key=f"lb_sort_desc_{leaderboard_id}")
        with b3:
            show_top_n = st.slider("最多展示多少行", 5, 500, 100, step=5, key=f"lb_topn_{leaderboard_id}")

    df_view = df[df["_algo_label"].isin(keep_algos)].copy()

    if sort_cols:
        sort_cols = [c for c in sort_cols if c in df_view.columns]
        if sort_cols:
            ascending = [not sort_desc] * len(sort_cols)
            try:
                df_view = df_view.sort_values(by=sort_cols, ascending=ascending, kind="mergesort")
            except Exception:
                pass

    if show_top_n and len(df_view) > show_top_n:
        df_view = df_view.head(show_top_n)

    cols_order = []
    if "rank" in visible_cols:
        cols_order.append("rank")
    for c in visible_cols:
        if c != "rank":
            cols_order.append(c)

    col_config = {}
    if "rank" in df_view.columns:
        col_config["rank"] = st.column_config.NumberColumn("排名", width="small")
    if "submission_name" in df_view.columns:
        col_config["submission_name"] = st.column_config.TextColumn("任务名称", width="large")
    if "username" in df_view.columns:
        col_config["username"] = st.column_config.TextColumn("用户", width="small")
    if "score" in df_view.columns:
        col_config["score"] = st.column_config.NumberColumn("分数", width="small", format="%.6f")
    if "time" in df_view.columns:
        col_config["time"] = st.column_config.TextColumn("时间", width="medium")
    if "submission_id" in df_view.columns:
        col_config["submission_id"] = st.column_config.TextColumn("submission_id", width="small")
    if "commitid" in df_view.columns:
        col_config["commitid"] = st.column_config.TextColumn("commitid", width="medium")

    for c in df_view.columns:
        if c in col_config or c in ("_algo_label",):
            continue
        if pd.api.types.is_numeric_dtype(df_view[c]):
            col_config[c] = st.column_config.NumberColumn(c, width="small")

    st.dataframe(
        df_view[cols_order] if cols_order else df_view,
        hide_index=True,
        use_container_width=True,
        column_config=col_config,
    )
    st.caption("提示：表格支持点击列头排序；上面“排序列”是固定排序（更可控）。")

    # --------------------------
    # ✅ 3.5) 管理员：点 ❌ 删除榜单记录（submission）
    # --------------------------
    def _get_api_base_url() -> str:
        for name in ["API_BASE_URL", "API_URL", "BACKEND_URL", "LEADERBOARD_API_URL"]:
            if name in globals() and globals()[name]:
                return str(globals()[name]).rstrip("/")
        for name in ["API_BASE_URL", "API_URL", "BACKEND_URL", "LEADERBOARD_API_URL"]:
            v = os.environ.get(name)
            if v:
                return v.rstrip("/")
        return ""

    def _api_delete(path: str) -> dict | None:
        import requests

        base = _get_api_base_url()
        if not base:
            st.error("未找到后端地址（API_BASE_URL/API_URL 等）。请检查前端配置。")
            return None

        url = f"{base.rstrip('/')}/api/{path.lstrip('/')}"
        headers = {}
        if st.session_state.get("token"):
            headers["Authorization"] = f"Bearer {st.session_state.token}"

        try:
            resp = requests.delete(url, headers=headers, timeout=30)
            if resp.status_code >= 400:
                st.error(f"删除失败：HTTP {resp.status_code} - {resp.text[:600]}")
                return None
            return resp.json()
        except Exception as e:
            st.error(f"调用后端删除接口失败：{e}")
            return None

    is_admin = bool(st.session_state.get("user") and st.session_state.user.get("role") == "admin")

    if is_admin:
        st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
        with st.expander("🛠️ 管理员：删除榜单记录（危险操作，点 ❌ 删除某条 submission 结果）", expanded=False):
            st.warning("删除会同时清理该 submission 的明细/日志/积分事件；不可恢复。")
            enable_del = st.checkbox("开启删除模式（开启后才可点 ❌）", value=False, key=f"enable_del_{leaderboard_id}")

            # 二次确认（全局）
            hard_confirm = st.checkbox("我已确认：删除不可恢复", value=False, key=f"hard_confirm_{leaderboard_id}")
            max_show = min(200, len(df_view))
            if max_show <= 0:
                    st.info("当前筛选结果为空（没有可删除的记录）。")
                    show_n = 0
            elif max_show <= 5:
                    show_n = max_show
                    st.caption(f"当前最多 {max_show} 条可显示（数量过少，不显示滑块）。")
            else:
                    show_n = st.slider(
                    "展示多少条用于删除",
                    5,
                    max_show,
                    min(20, max_show),
                    key=f"del_show_n_{leaderboard_id}",
                    )


            pending_key = f"pending_delete_sid_{leaderboard_id}"
            if pending_key not in st.session_state:
                st.session_state[pending_key] = ""

            st.caption("点击某行的 ❌ 会进入待删除状态，然后需要再点一次“确认删除”。")

            # 简表（手工列 + ❌）
            header = st.columns([1, 4, 2, 2, 2, 1])
            header[0].markdown("**Rank**")
            header[1].markdown("**Submission**")
            header[2].markdown("**User**")
            header[3].markdown("**Score**")
            header[4].markdown("**commitid**")
            header[5].markdown("**X**")

            for _, rr in df_view.head(show_n).iterrows():
                sid = str(rr.get("submission_id", "") or "").strip()
                if not sid:
                    continue
                cols = st.columns([1, 4, 2, 2, 2, 1])
                cols[0].write(f"{rr.get('rank','')}")
                cols[1].write(str(rr.get("submission_name", ""))[:120])
                cols[2].write(str(rr.get("username", ""))[:60])
                cols[3].write(rr.get("score", ""))
                cols[4].write(str(rr.get("commitid", ""))[:80])

                if cols[5].button("❌", key=f"del_pick_{leaderboard_id}_{sid}", disabled=(not enable_del)):
                    st.session_state[pending_key] = sid

            pending_sid = st.session_state.get(pending_key) or ""
            if pending_sid:
                st.error(f"待删除 submission_id = {pending_sid}")
                c_del1, c_del2 = st.columns([2, 2])
                if c_del1.button("✅ 确认删除", type="primary", use_container_width=True,
                                 disabled=(not (enable_del and hard_confirm)),
                                 key=f"del_confirm_{leaderboard_id}_{pending_sid}"):
                    resp_del = _api_delete(f"admin/submission/{pending_sid}")
                    if resp_del and resp_del.get("ok"):
                        st.success(f"已删除 submission_id={pending_sid}")
                        # 清缓存并刷新
                        cached_get_default.clear()
                        st.session_state.leaderboard_rankings = None
                        st.session_state[pending_key] = ""
                        st.rerun()
                if c_del2.button("取消", use_container_width=True, key=f"del_cancel_{leaderboard_id}"):
                    st.session_state[pending_key] = ""
                    st.rerun()

    with st.expander("🧾 原始详情表（含 Job/镜像/指标，便于核对）", expanded=False):
        try:
            st.markdown(build_rank_table_html(rows), unsafe_allow_html=True)
        except Exception as e:
            st.warning(f"原始表渲染失败：{e}")

    # --------------------------
    # 4) Plotly 多边形图（雷达图）
    # --------------------------
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.subheader("📐 多边形图对比（Plotly 雷达图）")

    try:
        import plotly.graph_objects as go
    except Exception:
        st.error("缺少 plotly：请在镜像里安装 `pip install plotly` 后再使用多边形图。")
        st.stop()

    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    numeric_cols = [c for c in numeric_cols if c not in ("rank",)]

    zero_one_cols = []
    for c in numeric_cols:
        s = pd.to_numeric(df[c], errors="coerce").dropna()
        if s.empty:
            continue
        in01 = ((s >= -0.05) & (s <= 1.05)).mean()
        if in01 >= 0.90 and s.min() >= -0.20 and s.max() <= 1.20:
            zero_one_cols.append(c)

    metric_candidates = []
    for c in visible_cols:
        if c in zero_one_cols:
            metric_candidates.append(c)
    for c in visible_cols:
        if c in numeric_cols and c not in metric_candidates:
            metric_candidates.append(c)
    for c in zero_one_cols:
        if c not in metric_candidates:
            metric_candidates.append(c)
    for c in numeric_cols:
        if c not in metric_candidates:
            metric_candidates.append(c)
    metric_candidates = list(dict.fromkeys(metric_candidates))

    prefer_names = [
        "naive_context_recall",   # ★ 第一优先
        "statement_recall",       # ★ 第二优先
    ]
    # 优先取这两个；若不在数据中则从 zero_one_cols 兜底补充
    radar_default = [c for c in prefer_names if c in metric_candidates]
    if len(radar_default) < 2:
        for c in metric_candidates:
            if c in zero_one_cols and c not in radar_default:
                radar_default.append(c)
            if len(radar_default) >= 6:
                break
    if not radar_default:
        radar_default = metric_candidates[:6]

    df_rankpick = df[df["_algo_label"].isin(keep_algos)].copy()
    df_rankpick["_rank_num"] = pd.to_numeric(df_rankpick.get("rank"), errors="coerce")
    df_rankpick = df_rankpick.sort_values(by=["_rank_num"], ascending=True, kind="mergesort")
    default_top5 = df_rankpick["_algo_label"].head(5).tolist()
    if not default_top5:
        default_top5 = keep_algos[: min(5, len(keep_algos))]

    r1, r2, r3 = st.columns([2, 2, 2])
    with r1:
        radar_algos = st.multiselect(
            "选择要对比的算法（默认 Top5，可 x 掉）",
            options=keep_algos,
            default=default_top5,
            key=f"radar_algos_{leaderboard_id}",
        )
    with r2:
        radar_metrics = st.multiselect(
            "选择要对比的属性（默认优先 0-1 指标，可 x 掉）",
            options=metric_candidates,
            default=radar_default,
            key=f"radar_metrics_{leaderboard_id}",
        )
    with r3:
        normalize_mode = st.selectbox(
            "指标归一化方式",
            options=["不归一化（原值）", "按选中算法 min-max 归一化"],
            index=0,
            key=f"radar_norm_{leaderboard_id}",
        )

    s1, s2, s3, s4 = st.columns([1, 1, 1, 1])
    with s1:
        radar_height = st.slider("高度", 320, 780, 420, step=20, key=f"radar_height_{leaderboard_id}")
    with s2:
        width_pct = st.slider("宽度(%)", 35, 95, 55, step=5, key=f"radar_width_pct_{leaderboard_id}")
    with s3:
        show_markers = st.checkbox("线+点", value=True, key=f"radar_markers_{leaderboard_id}")
    with s4:
        fill_poly = st.checkbox("填充色块", value=False, key=f"radar_fill_{leaderboard_id}")

    legend_layout = st.selectbox(
        "图例位置",
        options=["右侧(纵向)", "底部(横向)"],
        index=0,
        key=f"radar_legend_pos_{leaderboard_id}",
    )

    if radar_algos and radar_metrics:
        sub = df[df["_algo_label"].isin(radar_algos)].copy()

        base_cols = [c for c in ["rank", "submission_name", "username", "score", "commitid", "submission_id", "time"] if c in sub.columns]
        show_cols = list(dict.fromkeys(base_cols + [c for c in radar_metrics if c in sub.columns]))
        sub_list = sub[show_cols].copy()
        radar_df = sub_list.copy()

        if normalize_mode.startswith("按选中算法"):
            for m in radar_metrics:
                if m not in radar_df.columns:
                    continue
                col = radar_df[m]
                if isinstance(col, pd.DataFrame):
                    col = col.iloc[:, 0]
                col = pd.to_numeric(col, errors="coerce")
                mn = col.min(skipna=True)
                mx = col.max(skipna=True)
                if mn is None or mx is None or (isinstance(mn, float) and math.isnan(mn)) or (isinstance(mx, float) and math.isnan(mx)):
                    radar_df[m] = 0.0
                elif mx == mn:
                    radar_df[m] = 1.0
                else:
                    radar_df[m] = (col - mn) / (mx - mn)

        theta = list(radar_metrics)
        theta_closed = theta + [theta[0]]

        fig = go.Figure()
        for _, row in radar_df.iterrows():
            name = f"#{row.get('rank','?')} {row.get('submission_name','')}".strip()
            r_vals = []
            for t in theta:
                v = row.get(t, 0.0)
                try:
                    v = float(v) if v is not None else 0.0
                except Exception:
                    v = 0.0
                r_vals.append(v)
            r_closed = r_vals + [r_vals[0] if r_vals else 0.0]

            fig.add_trace(
                go.Scatterpolar(
                    r=r_closed,
                    theta=theta_closed,
                    mode=("lines+markers" if show_markers else "lines"),
                    fill=("toself" if fill_poly else "none"),
                    name=name,
                    hovertemplate="%{theta}: %{r:.6f}<extra>" + name + "</extra>",
                )
            )

        radialaxis = dict(visible=True)
        if normalize_mode.startswith("按选中算法"):
            radialaxis["range"] = [0, 1]

        if legend_layout.startswith("底部"):
            legend = dict(orientation="h", yanchor="top", y=-0.18, xanchor="left", x=0)
            margin = dict(l=12, r=12, t=40, b=90)
        else:
            legend = dict(orientation="v", yanchor="top", y=1, xanchor="right", x=0.985)
            margin = dict(l=12, r=6, t=40, b=12)

        fig.update_layout(
            height=radar_height,
            margin=margin,
            showlegend=True,
            legend=legend,
            polar=dict(radialaxis=radialaxis, angularaxis=dict(direction="clockwise")),
        )

        left_w = int(width_pct)
        right_w = max(1, 100 - left_w)
        chart_col, _ = st.columns([left_w, right_w])
        with chart_col:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": True, "scrollZoom": True, "responsive": True})

        st.markdown("##### 子列表（用于精确对比）")
        st.dataframe(sub_list.sort_values(by=["rank"] if "rank" in sub_list.columns else None), hide_index=True, use_container_width=True)
    else:
        st.info("请至少选择 1 个算法与 1 个属性，才能生成多边形图。")

    # --------------------------
    # 5) 按题正确率分析（保留柱状图 + 逐题答案对比；移除热力图、移除答案分桶）
    # --------------------------
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.subheader("🧪 按题正确率分析（选中算法 → 难题柱状图 + 逐题答案对比）")

    def _api_post_json(path: str, body: dict) -> dict | None:
        import requests

        base = _get_api_base_url()
        if not base:
            st.error("未找到后端地址（API_BASE_URL/API_URL 等）。请检查前端配置。")
            return None

        url = f"{base.rstrip('/')}/api/{path.lstrip('/')}"
        headers = {"Content-Type": "application/json"}
        if st.session_state.get("token"):
            headers["Authorization"] = f"Bearer {st.session_state.token}"

        try:
            resp = requests.post(url, headers=headers, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), timeout=30)
            if resp.status_code >= 400:
                st.error(f"后端返回错误：HTTP {resp.status_code} - {resp.text[:600]}")
                return None
            return resp.json()
        except Exception as e:
            st.error(f"调用后端失败：{e}")
            return None

    # label -> submission_id 映射
    label_to_sid = {}
    sid_to_label = {}
    for _, r in df.iterrows():
        sid = str(r.get("submission_id", "") or "").strip()
        label = str(r.get("_algo_label", "") or "").strip()
        if not sid or not label:
            continue
        label_to_sid[label] = sid
        sid_to_label[sid] = label

    analysis_default_labels = default_top5  # 默认 Top5（与雷达一致体验）

    with st.expander("📊 打开分析面板（难题柱状图 + 逐题答案对比）", expanded=True):
        if not st.session_state.get("token"):
            st.info("登录后可使用按题正确率分析（需要调用后端接口）。")
        else:
            aa1, aa2, aa3 = st.columns([2, 2, 2])
            with aa1:
                analysis_algos = st.multiselect(
                    "选择要分析的算法（可 x 掉）",
                    options=[x for x in keep_algos if x in label_to_sid],
                    default=[x for x in analysis_default_labels if x in label_to_sid],
                    key=f"qa_analysis_algos_{leaderboard_id}",
                )
            with aa2:
                analysis_sort = st.selectbox(
                    "题目排序",
                    options=[
                        ("q_rate_asc", "按题正确率（低→高，最难优先）"),
                        ("q_rate_desc", "按题正确率（高→低）"),
                        ("disagreement_desc", "按分歧度（高→低）"),
                        ("missing_desc", "按缺失数（多→少）"),
                        ("qid_asc", "按题号（数值顺序）"),
                    ],
                    index=0,
                    format_func=lambda x: x[1],
                    key=f"qa_analysis_sort_{leaderboard_id}",
                )
            with aa3:
                answer_trunc = st.slider(
                    "答案截断长度（防止过大）",
                    min_value=80,
                    max_value=1500,
                    value=400,
                    step=40,
                    key=f"qa_analysis_trunc_{leaderboard_id}",
                )

            bb1, bb2, bb3 = st.columns([1, 1, 2])
            with bb1:
                include_answers = st.checkbox("包含答案文本", value=True, key=f"qa_inc_ans_{leaderboard_id}")
            with bb2:
                include_prompts = st.checkbox("包含 prompt（谨慎，很大）", value=False, key=f"qa_inc_pr_{leaderboard_id}")
            with bb3:
                bar_height = st.slider("柱状图高度", 260, 720, 380, step=20, key=f"qa_bar_h_{leaderboard_id}")

            do_fetch = st.button("🔍 生成/刷新分析结果", use_container_width=True, key=f"qa_fetch_{leaderboard_id}")

            cache_key = (
                leaderboard_id,
                tuple(label_to_sid.get(x) for x in analysis_algos),
                analysis_sort[0],
                include_answers,
                include_prompts,
                int(answer_trunc),
            )

            if "qa_analysis_cache" not in st.session_state:
                st.session_state.qa_analysis_cache = {}

            if do_fetch or cache_key not in st.session_state.qa_analysis_cache:
                sub_ids = []
                for lab in analysis_algos:
                    sid = label_to_sid.get(lab)
                    if sid:
                        try:
                            sub_ids.append(int(sid))
                        except Exception:
                            pass

                if len(sub_ids) < 1:
                    st.warning("请至少选择 1 个算法。")
                else:
                    resp = _api_post_json(
                        f"leaderboard/{leaderboard_id}/question_analysis",
                        {
                            "submission_ids": sub_ids,
                            "include_answers": bool(include_answers),
                            "include_prompts": bool(include_prompts),
                            "include_answer_groups": False,  # ✅ 不要答案分桶
                            "answer_trunc": int(answer_trunc),
                            "sort_by": analysis_sort[0],
                        },
                    )
                    if resp and resp.get("ok"):
                        st.session_state.qa_analysis_cache[cache_key] = resp

            resp = st.session_state.qa_analysis_cache.get(cache_key)
            if not resp:
                st.info("点击上方按钮生成分析结果。")
            else:
                submissions = resp.get("submissions", []) or []
                questions = resp.get("questions", []) or []
                if not submissions or not questions:
                    st.warning("分析结果为空（可能后端无明细数据）。")
                else:
                    # -------- 难题柱状图（保留；色彩调整；不省略标号）--------
                    bar_df = pd.DataFrame(
                        [
                            {
                                "question_id": (q.get("question_id") or q.get("question_key") or ""),
                                "q_rate": (q.get("stats", {}) or {}).get("q_rate"),
                                "correct_cnt": (q.get("stats", {}) or {}).get("correct_cnt"),
                                "wrong_cnt": (q.get("stats", {}) or {}).get("wrong_cnt"),
                                "missing_cnt": (q.get("stats", {}) or {}).get("missing_cnt"),
                                "disagreement": (q.get("stats", {}) or {}).get("disagreement"),
                                "question": (q.get("question") or ""),
                                "gold": (q.get("gold_answer") or ""),
                            }
                            for q in questions
                        ]
                    )
                    bar_df["q_rate_num"] = pd.to_numeric(bar_df["q_rate"], errors="coerce")

                    import plotly.graph_objects as go

                    x_ids = bar_df["question_id"].astype(str).tolist()
                    y_rates = bar_df["q_rate_num"].fillna(0.0).tolist()

                    bar = go.Bar(
                        x=x_ids,
                        y=y_rates,
                        marker=dict(
                            color="rgba(37, 99, 235, 0.75)",
                            line=dict(color="rgba(37, 99, 235, 1.0)", width=1),
                        ),
                        hovertext=[
                            f"QID: {row['question_id']}<br>"
                            f"q_rate: {row['q_rate'] if row['q_rate'] is not None else '—'}<br>"
                            f"correct={row['correct_cnt']} wrong={row['wrong_cnt']} missing={row['missing_cnt']}<br>"
                            f"disagreement={row['disagreement']}<br>"
                            f"Q: {html_escape((row['question'] or '')[:220])}"
                            for _, row in bar_df.iterrows()
                        ],
                        hoverinfo="text",
                        text=[str(qid) for qid in x_ids],
                        textposition="outside",
                        cliponaxis=False,
                    )

                    bar_fig = go.Figure(data=[bar])
                    bar_fig.update_layout(
                        height=int(bar_height),
                        margin=dict(l=10, r=10, t=30, b=90),
                        xaxis=dict(
                            type="category",          # QID 是离散类目，避免被当成数值轴（dtick=1 → 上万刻度）
                            categoryorder="array",
                            categoryarray=x_ids,       # 保持给定顺序（按 sort_by 排序后的顺序）
                            tickangle=-35,
                            tickfont=dict(size=12),
                            automargin=True,
                            title="Question ID",
                        ),
                        yaxis=dict(
                            title="Correct Rate",
                            range=[0, 1] if (bar_df["q_rate_num"].max(skipna=True) <= 1.2) else None,
                            tickformat=".0%",
                        ),
                        uniformtext_minsize=10,
                        uniformtext_mode="show",
                    )

                    st.markdown("##### 🧱 难题排行：按题正确率（QID 不省略）")
                    st.plotly_chart(bar_fig, use_container_width=True, config={"displayModeBar": True, "responsive": True})

                    # -------- 逐题详情（答案对比）--------
                    st.markdown("##### 🔎 逐题答案对比（点选某题）")

                    q_options = [
                        f"{row['question_id']} | q_rate={row['q_rate'] if row['q_rate'] is not None else '—'} | {str(row['question'])[:70]}"
                        for _, row in bar_df.iterrows()
                    ]

                    if q_options:
                        pick = st.selectbox("选择题目：", options=q_options, key=f"qa_pick_{leaderboard_id}")
                        pick_qid = pick.split("|", 1)[0].strip()
                        q_obj = next(
                            (q for q in questions if str(q.get("question_id") or q.get("question_key") or "") == pick_qid),
                            None,
                        )
                        if q_obj:
                            st.write(f"**Question:** {q_obj.get('question','')}")
                            st.write(f"**Gold:** {q_obj.get('gold_answer','')}")
                            stt = q_obj.get("stats", {}) or {}
                            st.caption(
                                f"denom={stt.get('denom')} | correct={stt.get('correct_cnt')} | wrong={stt.get('wrong_cnt')} | "
                                f"missing={stt.get('missing_cnt')} | unknown={stt.get('unknown_cnt')} | "
                                f"q_rate={stt.get('q_rate')} | disagreement={stt.get('disagreement')}"
                            )

                            by_sub = q_obj.get("by_submission", {}) or {}
                            detail_rows = []
                            for s in submissions:
                                sid = str(s.get("submission_id"))
                                meta = f"{s.get('submission_name','')} ({s.get('username','')})"
                                it = by_sub.get(sid, {}) or {}
                                ic = it.get("is_correct", None)
                                state = "MISSING" if it.get("missing") else ("CORRECT" if ic == 1 else "WRONG" if ic == 0 else "UNKNOWN")
                                detail_rows.append(
                                    {
                                        "algo": meta,
                                        "state": state,
                                        "answer": it.get("answer", "") if include_answers else "",
                                        "prompt": it.get("prompt", "") if include_prompts else "",
                                    }
                                )
                            det_df = pd.DataFrame(detail_rows)
                            st.dataframe(
                                det_df[["algo", "state", "answer"] + (["prompt"] if include_prompts else [])],
                                hide_index=True,
                                use_container_width=True,
                            )

    # ==========================
    # 5.5) 榜单自定义结果视图（沙箱纯渲染器）
    # ----------------------------------------
    # 榜单若自带 result_view_html（由 evaluator 回传缓存），则在打开某条 submission
    # 明细时，用「嵌套沙箱 iframe」渲染榜单提供的 HTML/JS，并注入 DATA（= 该 submission
    # 的 /extra JSON）。榜单 JS 仅读 DATA 绘制 DOM——不持 token、不发网络请求。
    # 没有自定义视图的榜单完全走原有默认渲染，行为不变。
    # ==========================
    if st.session_state.token:
        _rv = api_get(f"leaderboard/{leaderboard_id}/result-view")
        if isinstance(_rv, dict) and _rv.get("has_custom") and _rv.get("html"):
            st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
            st.subheader("🧩 自定义结果视图（榜单自带面板）")

            _rv_opts = {}
            for _r in rows:
                _sid = _r.get("submission_id") or _r.get("id")
                if not _sid:
                    continue
                _lbl = (
                    f"#{_sid} · {_r.get('submission_name') or _r.get('name') or ''} "
                    f"({_r.get('username','')})"
                )
                _rv_opts[_lbl] = _sid

            if not _rv_opts:
                st.info("当前榜单暂无可展示的提交明细。")
            else:
                _pick = st.selectbox(
                    "选择要查看明细的提交：",
                    options=list(_rv_opts.keys()),
                    key=f"rv_pick_{leaderboard_id}",
                )
                _pick_sid = _rv_opts[_pick]
                _extra = api_get(f"submission/{_pick_sid}/extra")
                if not isinstance(_extra, dict):
                    st.warning("无法获取该提交的明细数据。")
                else:
                    _inner = (
                        "<script>const DATA = "
                        + json.dumps(_extra)
                        + ";</script>\n"
                        + _rv["html"]
                    )
                    _outer = (
                        '<iframe sandbox="allow-scripts" srcdoc="'
                        + html_escape(_inner, quote=True)
                        + '" style="width:100%;height:760px;border:0;background:#fff">'
                        + "</iframe>"
                    )
                    components.html(_outer, height=780, scrolling=True)

    # ==========================
    # 6) Excel 下载入口（完整保留 + ✅ 单算法表新增 commitid 列）
    # ==========================
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.subheader("📥 Excel 导出")

    if not st.session_state.token:
        st.info("登录后可以下载本榜单及单算法的 Excel 报表。")
    else:
        # 整个榜单 Excel
        col_dl_all, _ = st.columns([1, 3])
        with col_dl_all:
            if st.button("⬇️ 下载本榜单 Excel", use_container_width=True, key=f"dl_lb_btn_{leaderboard_id}"):
                content, fname = api_get_file(f"leaderboard/{leaderboard_id}/excel")
                if content:
                    st.download_button(
                        "点击下载榜单 Excel",
                        data=content,
                        file_name=fname,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"dl_lb_real_{leaderboard_id}",
                    )

        # 单算法 Excel：条纹表格 + 下拉选择，避免逐行点错
        if rows:
            st.markdown("#### 单算法 Excel 下载")

            dl_rows = rows[:50]

            st.markdown(
                """
<style>
.excel-dl-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
.excel-dl-table thead tr { background-color: #f3f4f6; }
.excel-dl-table th, .excel-dl-table td {
    padding: 0.35rem 0.75rem; text-align: left; border-bottom: 1px solid #e5e7eb;
}
.excel-dl-table tbody tr:nth-child(odd) { background-color: #f9fafb; }
.excel-dl-table tbody tr:nth-child(even) { background-color: #ffffff; }
.excel-dl-table tbody tr:hover { background-color: #e5f0ff; }
</style>
                """,
                unsafe_allow_html=True,
            )

            def _guess_commitid(r: dict) -> str:
                # 1) top-level
                for k in ("commitid", "commit_id"):
                    v = r.get(k)
                    if v:
                        return str(v)
                # 2) metrics
                m = r.get("metrics") or {}
                if isinstance(m, dict):
                    for k in ("commitid", "commit_id", "git_commit", "git_sha", "sha", "revision"):
                        v = m.get(k)
                        if v:
                            return str(v)
                # 3) algo_env
                env = r.get("algo_env") or {}
                if isinstance(env, dict):
                    for k in ("COMMITID", "COMMIT_ID", "GIT_COMMIT", "GIT_SHA", "REVISION", "SHA"):
                        v = env.get(k)
                        if v:
                            return str(v)
                return ""

            table_rows_html = []
            label_to_sid_excel: dict[str, str] = {}

            for idx, r in enumerate(dl_rows, start=1):
                sid = r.get("submission_id") or r.get("id")
                if not sid:
                    continue

                rank = r.get("rank", "?")
                name = r.get("submission_name") or r.get("name") or ""
                username = r.get("username", "?")
                score = r.get("score", "?")
                commitid = _guess_commitid(r)

                table_rows_html.append(
                    "<tr>"
                    f"<td>{idx}</td>"
                    f"<td>#{html_escape(str(rank))}</td>"
                    f"<td>{html_escape(str(name))}</td>"
                    f"<td>{html_escape(str(username))}</td>"
                    f"<td>{html_escape(str(score))}</td>"
                    f"<td>{html_escape(str(commitid))}</td>"
                    "</tr>"
                )

                label = f"{idx}. #{rank} {name} | 用户: {username} | 分数: {score} | commitid: {commitid}"
                label_to_sid_excel[label] = str(sid)

            if table_rows_html:
                table_html = (
                    "<table class='excel-dl-table'>"
                    "<thead><tr>"
                    "<th>序号</th><th>排名</th><th>提交名称</th><th>用户</th><th>分数</th><th>commitid</th>"
                    "</tr></thead>"
                    "<tbody>" + "".join(table_rows_html) + "</tbody>"
                    "</table>"
                )
                st.markdown(table_html, unsafe_allow_html=True)
            else:
                st.info("当前榜单暂无可导出的算法记录。")

            if label_to_sid_excel:
                selected_label = st.selectbox(
                    "选择你要下载 Excel 的算法：",
                    options=list(label_to_sid_excel.keys()),
                    key=f"select_excel_sub_{leaderboard_id}",
                )
                selected_sid = label_to_sid_excel[selected_label]

                if st.button("⬇️ 下载选中算法 Excel", key=f"dl_sub_btn_select_{leaderboard_id}"):
                    content, fname = api_get_file(f"submission/{selected_sid}/excel")
                    if content:
                        st.download_button(
                            "点击下载选中算法 Excel",
                            data=content,
                            file_name=fname,
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key=f"dl_sub_real_select_{leaderboard_id}",
                        )
            else:
                st.info("当前榜单暂无可导出的算法记录。")

    # --------------------------
    # 7) CTA
    # --------------------------
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    if st.session_state.token:
        if st.button("🚀 我也要打榜", type="primary"):
            navigate("submit", params={"id": leaderboard_id})
    else:
        st.info("登录后即可提交您的算法进行评测。")



# ======== 提交评测（含网格搜索） ========

def _fmt_dur(sec: float | int | None) -> str:
    try:
        if sec is None or sec != sec or sec < 0:
            return "未知"
        sec = int(round(float(sec)))
        h = sec // 3600
        m = (sec % 3600) // 60
        s = sec % 60
        if h > 0:
            return f"{h}小时{m}分{s}秒"
        if m > 0:
            return f"{m}分{s}秒"
        return f"{s}秒"
    except Exception:
        return "未知"

def _get_avg_duration_sec(leaderboard_id: str) -> float | None:
    try:
        stats = api_get(f"leaderboard/{leaderboard_id}/stats")
        if isinstance(stats, dict):
            for k in ["avg_duration_sec", "avg_eval_time_sec", "avg_time_sec"]:
                if k in stats and stats[k] is not None:
                    return float(stats[k])
    except Exception:
        pass
    try:
        r = api_get(f"leaderboard/{leaderboard_id}/rankings",
                    params={"include_job": "1", "per_submission": "1", "limit": "20"},
                    top5=True)
        rows = (r or {}).get("rankings", []) if isinstance(r, dict) else []
        vals = []
        for item in rows:
            m = item.get("metrics") or {}
            for key in ["Avg Time (s)", "avg_time_s", "avg_time", "duration_s"]:
                if key in m and m[key] is not None:
                    try:
                        vals.append(float(m[key]))
                        break
                    except Exception:
                        continue
        if vals:
            return float(sum(vals) / max(1, len(vals)))
    except Exception:
        pass
    return None

def _normalize_editor_rows(rows):
    """把 data_editor 返回的 DataFrame / list[dict] / list[str] / str 统一转成 list[dict]."""
    try:
        import pandas as pd  # 仅在存在时使用
        if hasattr(rows, "to_dict"):  # DataFrame
            # DataFrame -> list[dict]
            rows = rows.fillna("").to_dict(orient="records")
    except Exception:
        pass

    if rows is None:
        return []

    # 单条字符串（不应出现，但防御）
    if isinstance(rows, str):
        # 可能是复制进来的 JSON 行
        try:
            obj = json.loads(rows)
            if isinstance(obj, list):
                return _normalize_editor_rows(obj)
            if isinstance(obj, dict):
                return [obj]
        except Exception:
            return []
    # 列表
    if isinstance(rows, list):
        out = []
        for r in rows:
            if r is None:
                continue
            if isinstance(r, dict):
                out.append(r)
            elif isinstance(r, str):
                try:
                    obj = json.loads(r)
                    if isinstance(obj, dict):
                        out.append(obj)
                except Exception:
                    # 允许简单的 "k=v,k2=v2" 形式
                    parts = [x.strip() for x in r.split(",") if x.strip()]
                    kv = {}
                    for p in parts:
                        if "=" in p:
                            k, v = p.split("=", 1)
                            kv[k.strip()] = v.strip()
                    if kv:
                        out.append(kv)
        return out
    # 其他不可识别
    return []

def _parse_grid_rows(rows):
    """
    解析 data_editor 行 -> {param: [values...]}
    - 兼容 DataFrame/列表/字符串
    - 兼容列名："参与"/"participate"，"参数名"/"param"，"候选值(逗号分隔)"/"values"
    """
    grid = {}
    norm = _normalize_editor_rows(rows)
    for r in (norm or []):
        # 若 r 不是 dict（例如纯字符串），跳过
        if not isinstance(r, dict):
            continue
        participate = bool(r.get("参与") or r.get("participate") or False)
        if not participate:
            continue
        key = (r.get("参数名") or r.get("param") or "")
        if key is None:
            key = ""
        key = str(key).strip()

        raw = r.get("候选值(逗号分隔)") or r.get("values") or ""
        if raw is None:
            raw = ""
        raw = str(raw).strip()
        if not key or not raw:
            continue

        parts = []
        raw_norm = raw.replace("\n", ",").replace(" ", ",")
        for seg in raw_norm.split(","):
            s = seg.strip()
            if s:
                parts.append(s)

        seen = set()
        uniq = []
        for p in parts:
            if p not in seen:
                seen.add(p); uniq.append(p)

        if uniq:
            grid[key] = uniq
    return grid

def _cartesian_count(grid_dict):
    if not grid_dict:
        return 0
    nums = [max(0, len(v)) for v in grid_dict.values()]
    if not nums or any(n == 0 for n in nums):
        return 0
    total = 1
    for n in nums:
        total *= n
        if total > 10**9:
            return 10**9
    return total

def _cartesian_combos(grid_dict):
    if not grid_dict:
        return []
    keys = list(grid_dict.keys())
    vals = [grid_dict[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in product(*vals)]


# ================================================================
# 完整替换 frontend_app.py 第 66 行 ~ 第 340 行
# ================================================================
# ==============================================================
# 完整替换 frontend_app.py 第 66 行 ~ 第 340 行
# ==============================================================

def render_submit_page():
    if not check_role(["participant", "creator", "admin"]):
        st.error("您没有访问此页面的权限。")
        return

    leaderboard_id = st.session_state.page_params.get("id")
    if not leaderboard_id:
        st.error("缺少榜单 ID。")
        return

    ss = st.session_state
    lb_name = f"榜单 #{leaderboard_id}"
    lb_info = None
    if ss.leaderboards:
        lb_info = next((b for b in ss.leaderboards if b["id"] == leaderboard_id), None)
        if lb_info:
            lb_name = lb_info["name"]

    st.markdown("""
    <style>
    .sec-label { font-size: 12px; font-weight: 700; color: #64748b;
        letter-spacing: .5px; text-transform: uppercase; margin: 0 0 4px; }
    .kv-key { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 13px; font-weight: 600; color: #1e293b;
        line-height: 38px; white-space: nowrap; }
    .kv-key .req { color: #ef4444; font-size: 10px; vertical-align: super; margin-left: 2px; }
    .badge-new { display: inline-block; padding: 0 6px; border-radius: 8px;
        font-size: 10px; font-weight: 700; color: #c2410c;
        background: #fff7ed; border: 1px solid #fed7aa; margin-left: 4px; vertical-align: middle; }
    .badge-known { display: inline-block; padding: 0 6px; border-radius: 8px;
        font-size: 10px; font-weight: 600; color: #15803d;
        background: #f0fdf4; border: 1px solid #bbf7d0; margin-left: 4px; vertical-align: middle; }
    .badge-req-top { display: inline-block; padding: 1px 6px; border-radius: 3px;
        font-size: 11px; font-weight: 700; color: #dc2626;
        background: #fef2f2; border: 1px solid #fecaca; margin-right: 4px; }
    </style>
    """, unsafe_allow_html=True)

    st.title(f"🚀 提交评测 · {lb_name}")

    def _now_ts_ms():
        return datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]

    required_keys = set()
    if lb_info and lb_info.get("required_algo_env_keys"):
        try:
            rk = lb_info["required_algo_env_keys"]
            if isinstance(rk, str): rk = json.loads(rk)
            required_keys = set(rk) if isinstance(rk, list) else set()
        except Exception: pass

    # ── 工具函数 ──
    def _parse_to_dict(text):
        """KEY=VALUE 文本 → dict (保留首次出现)"""
        d = {}
        for line in (text or "").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k = k.strip()
            if k and k not in d:
                d[k] = v.strip()
        return d

    def _store_to_text(store):
        """dict → 按 key 排序的多行文本"""
        return "\n".join(f"{k}={v}" for k, v in sorted(store.items()))

    # ────────────────────────────────────────────────────────────
    # ★ 同步引擎 — 在所有 widget 渲染之前执行
    # ────────────────────────────────────────────────────────────
    if "_env_store" not in ss:
        ss["_env_store"] = {}

    # 消费 dirty 标记 (由上一轮 on_change 回调设置)
    src = ss.pop("_env_src", None)

    if src == "text":
        # 用户编辑了文本框 → store 已更新 → 同步 store → 所有 KV input
        # (text_area 自己的值不需要动, Streamlit 保持了用户输入)
        _sync_store_to_kv_keys(ss)

    elif src == "kv":
        # 用户编辑了某个 KV input → store 已更新 → 同步 store → text_area
        # (KV input 自己的值不需要动)
        ss["env_text_area"] = _store_to_text(ss["_env_store"])

    # 镜像 pending
    if "_img_pending" in ss:
        ss["algo_image_input"] = ss.pop("_img_pending")

    # ── on_change 回调 ──
    def _on_text_change():
        """用户编辑 text_area → 解析 → 更新 store"""
        ss["_env_store"] = _parse_to_dict(ss.get("env_text_area", ""))
        ss["_env_src"] = "text"

    def _make_kv_cb(key):
        """用户编辑 KV text_input → 更新 store 对应 key"""
        def cb():
            ss["_env_store"][key] = ss.get(f"_ev_{key}", "")
            ss["_env_src"] = "kv"
        return cb

    def _make_hist_cb(key):
        """用户从历史 selectbox 选了一个值 → 写入 store + KV input"""
        def cb():
            hk = f"_eh_{key}"
            chosen = ss.get(hk, "")
            if chosen:
                ss["_env_store"][key] = chosen
                ss[f"_ev_{key}"] = chosen   # 直接设 KV input (它还没渲染, 安全)
                ss["_env_src"] = "kv"       # 需要同步到 text_area
            ss.pop(hk, None)                # 重置 selectbox
        return cb

    def _apply_store_to_all_widgets(store):
        """预设/历史导入: 直接设所有 widget key (在 text_area 和 KV 渲染之前调用, 安全)"""
        ss["_env_store"] = dict(store)
        ss["env_text_area"] = _store_to_text(store)
        # 清理不存在的 KV keys
        for k in [k for k in ss if k.startswith("_ev_")]:
            if k[4:] not in store:
                del ss[k]
        for k in [k for k in ss if k.startswith("_eh_")]:
            if k[4:] not in store:
                del ss[k]
        # 设置 KV keys
        for k, v in store.items():
            ss[f"_ev_{k}"] = v

    # ================================================================
    tab_image, tab_upload = st.tabs(["🐳 镜像打榜", "📤 上传评测结果"])

    with tab_image:

        # ═══ A: 算法镜像 ═══
        with st.container(border=True):
            st.markdown('<p class="sec-label">🔗 算法镜像</p>', unsafe_allow_html=True)
            img_hist_raw = api_request("GET", "env-presets/image-history",
                params={"leaderboard_id": leaderboard_id})
            img_hist = img_hist_raw if isinstance(img_hist_raw, list) else []

            c_pick, c_input = st.columns([1, 2])
            with c_pick:
                img_opts = ["── 手动输入 ──"] + [
                    f"{h['image_url']}  ({'本榜' + str(h['board_count']) + '·' if h.get('board_count') else ''}全局{h['global_count']})"
                    for h in img_hist]
                img_sel = st.selectbox("历史镜像", img_opts, key="_img_sel", label_visibility="collapsed")
                if img_sel != "── 手动输入 ──":
                    chosen_url = img_sel.split("  (")[0].strip()
                    if ss.get("algo_image_input", "") != chosen_url:
                        ss["_img_pending"] = chosen_url
                        st.rerun()
            with c_input:
                algorithm_image_url = st.text_input(
                    "地址", placeholder="registry.local/p_user1/my-algo:latest",
                    key="algo_image_input", label_visibility="collapsed")

        image_filled = bool(algorithm_image_url and algorithm_image_url.strip())

        # ═══ B: 环境变量 ═══
        with st.container(border=True):
            st.markdown('<p class="sec-label">⚙️ 环境变量</p>', unsafe_allow_html=True)

            # ── 查询预设/历史/key历史 ──
            presets, history_subs, key_history = [], [], {}
            preset_map, hist_map = {}, {}

            if image_filled:
                _p = api_request("GET", "env-presets", params={"image_url": algorithm_image_url.strip()})
                presets = _p if isinstance(_p, list) else []
                _h = api_request("GET", "env-presets/history-submissions",
                    params={"image_url": algorithm_image_url.strip()})
                history_subs = _h if isinstance(_h, list) else []
                _kh = api_request("GET", "env-presets/key-history",
                    params={"leaderboard_id": leaderboard_id})
                key_history = _kh if isinstance(_kh, dict) and "_error" not in _kh else {}

            # ── 预设 / 历史导入 selectbox ──
            # ⚠️ 这两个在 text_area 之前渲染, 所以检测到选择后可以安全写 env_text_area
            cp, ch = st.columns(2)

            with cp:
                popts = ["─"]
                for p in presets:
                    n = len(p.get("env", {}))
                    lbl = f"{'⭐ ' if p.get('is_default') else ''}{'📋' if p.get('source')=='history' else '📌'} {p['name']} ({n}项)"
                    popts.append(lbl); preset_map[lbl] = p
                sel_p = st.selectbox("加载预设", popts, key="preset_sel",
                    help="选中后填入文本框" if presets else "输入镜像后加载")

                # 检测预设选择变化 (只在选择发生变化时应用, 避免覆盖用户编辑)
                if sel_p in preset_map and ss.get("_applied_preset") != sel_p:
                    ss["_applied_preset"] = sel_p
                    _apply_store_to_all_widgets(preset_map[sel_p].get("env", {}))
                    st.rerun()
                elif sel_p not in preset_map:
                    ss.pop("_applied_preset", None)

            with ch:
                hopts = ["─"]
                for h in history_subs:
                    sc = f"{h['score']:.3f}" if h.get("score") is not None else "—"
                    em = {"Succeeded":"✅","Failed":"❌","Running":"🔄"}.get(h.get("status",""),"⏳")
                    lbl = f"{em} #{h['submission_id']} {h['submission_name'][:18]} {sc} ({h['env_count']}项)"
                    hopts.append(lbl); hist_map[lbl] = h
                sel_h = st.selectbox("从历史导入", hopts, key="hist_sel",
                    help="导入固定 ENV" if history_subs else "无历史")

                if sel_h in hist_map and ss.get("_applied_hist") != sel_h:
                    ss["_applied_hist"] = sel_h
                    _apply_store_to_all_widgets(hist_map[sel_h].get("env_preview", {}))
                    st.rerun()
                elif sel_h not in hist_map:
                    ss.pop("_applied_hist", None)

            # ── 必填提示 ──
            if required_keys:
                st.markdown(" ".join(f'<span class="badge-req-top">必填 {k}</span>'
                    for k in sorted(required_keys)), unsafe_allow_html=True)

            # ── text_area (主编辑区) ──
            env_text = st.text_area(
                "KEY=VALUE（每行一条，# 注释）",
                placeholder="HTTP_PROXY=http://xxx:7890\nWORKERS=4",
                height=120, key="env_text_area",
                on_change=_on_text_change,
                help="直接编辑或从预设/历史填入。下方面板实时同步。")

            # ── 必填校验 ──
            store = ss["_env_store"]
            if required_keys:
                miss = required_keys - {k for k, v in store.items() if v}
                if miss:
                    st.warning(f"缺少必填：{', '.join(sorted(miss))}")

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # ★ KV 面板 (从 store 读, on_change 写回 store)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            sorted_keys = sorted(store.keys())

            if sorted_keys:
                st.caption(f"📋 {len(sorted_keys)} 项（Key 排序）— 可直接编辑值 · 有历史记录时可下拉选择")

                for key in sorted_keys:
                    value = store.get(key, "")
                    hvs = key_history.get(key, [])
                    is_req = key in required_keys

                    if hvs:
                        c_k, c_v, c_h, c_x = st.columns([2, 4, 2.5, 0.5])
                    else:
                        c_k, c_v, c_x = st.columns([2, 6.5, 0.5])
                        c_h = None

                    # ── Key 列 ──
                    with c_k:
                        req_html = '<span class="req">*</span>' if is_req else ''
                        st.markdown(f'<div class="kv-key">{key}{req_html} =</div>', unsafe_allow_html=True)

                    # ── Value 列 (可编辑 text_input) ──
                    with c_v:
                        # 确保 widget 有初始值 (首次出现时)
                        if f"_ev_{key}" not in ss:
                            ss[f"_ev_{key}"] = value

                        st.text_input(
                            key, key=f"_ev_{key}",
                            on_change=_make_kv_cb(key),
                            label_visibility="collapsed")

                        # badge: 新值 / 已知
                        cur_val = ss.get(f"_ev_{key}", value)
                        if hvs and cur_val:
                            if cur_val not in hvs:
                                st.markdown('<span class="badge-new">🆕 新值</span>', unsafe_allow_html=True)
                            else:
                                st.markdown('<span class="badge-known">✓ 已知</span>', unsafe_allow_html=True)

                    # ── 历史值下拉 (有历史时才显示) ──
                    if c_h is not None:
                        with c_h:
                            st.selectbox(
                                f"h_{key}",
                                [""] + hvs,
                                key=f"_eh_{key}",
                                format_func=lambda x, _k=key: "↑ 历史值" if x == "" else (x if len(x) <= 28 else x[:25] + "…"),
                                on_change=_make_hist_cb(key),
                                label_visibility="collapsed")

                    # ── 删除按钮 ──
                    with c_x:
                        if st.button("✕", key=f"_d_{key}", help=f"删除 {key}"):
                            ss["_env_store"].pop(key, None)
                            ss.pop(f"_ev_{key}", None)
                            ss.pop(f"_eh_{key}", None)
                            # 同步到 text_area (text_area 已经渲染过了, 不能直接写)
                            ss["_env_src"] = "kv"
                            st.rerun()

            # ── 添加新变量 ──
            ak, av, ab = st.columns([2, 6.5, 0.5])
            with ak:
                nk = st.text_input("新变量", placeholder="KEY", key="_ak", label_visibility="collapsed")
            with av:
                ch_hist = key_history.get(nk.strip(), []) if nk.strip() else []
                if ch_hist:
                    nv_raw = st.selectbox("值", [""] + ch_hist, key="_av_sel",
                        format_func=lambda x: "手动输入或选择…" if x == "" else x,
                        label_visibility="collapsed")
                    nv = nv_raw if nv_raw else ""
                else:
                    nv = st.text_input("值", placeholder="VALUE", key="_av", label_visibility="collapsed")
            with ab:
                if st.button("➕", key="_ab", use_container_width=True):
                    k = nk.strip()
                    if k:
                        ss["_env_store"][k] = nv
                        ss["_env_src"] = "kv"   # 需要同步到 text_area
                        for ck in ["_ak", "_av", "_av_sel"]:
                            ss.pop(ck, None)
                        st.rerun()

            # ── 保存预设 ──
            st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
            sn, sb, sd, sx = st.columns([3, 1.2, 0.8, 0.7])
            with sn:
                pname = st.text_input("预设名", placeholder="RAG-v3-proxy", key="pname", label_visibility="collapsed")
            with sb:
                save_click = st.button("💾 保存", use_container_width=True, type="primary")
            with sd:
                as_def = st.checkbox("默认", key="pdef")
            with sx:
                del_click = False
                if sel_p in preset_map:
                    del_click = st.button("🗑️", key="_px", use_container_width=True)

            if save_click:
                if not pname or not pname.strip(): st.error("请输入预设名")
                elif not image_filled: st.error("请先填写镜像")
                elif not ss["_env_store"]: st.error("ENV 为空")
                else:
                    r = api_request("POST", "env-presets", data={
                        "image_url": algorithm_image_url.strip(),
                        "name": pname.strip(), "env": dict(ss["_env_store"]), "is_default": as_def})
                    if r and isinstance(r, dict) and r.get("ok"):
                        st.success(f"✅ 已保存「{pname}」（{len(ss['_env_store'])}项）"); st.rerun()
                    else:
                        st.error(f"失败：{r.get('msg',r) if isinstance(r,dict) else r}")

            if del_click and sel_p in preset_map:
                r = api_request("DELETE", f"env-presets/{preset_map[sel_p]['id']}")
                if r and isinstance(r, dict) and r.get("ok"):
                    ss.pop("preset_sel", None); ss.pop("_applied_preset", None); st.rerun()

        # ═══ C: 提交配置 ═══
        with st.container(border=True):
            st.markdown('<p class="sec-label">🚀 提交配置</p>', unsafe_allow_html=True)
            with st.form("submission_form"):
                submission_name = st.text_input("任务名称", placeholder="my-resnet-v2（自动追加时间戳）")
                enable_grid = st.checkbox("启用网格搜索批量提交", value=False)
                if enable_grid: st.caption("编辑下表，勾选「参与」行即可。")
                default_rows = [
                    {"参与": False, "参数名": "lr", "候选值(逗号分隔)": "1e-4, 3e-4, 1e-3"},
                    {"参与": False, "参数名": "batch_size", "候选值(逗号分隔)": "8, 16"},
                ]
                grid_rows_local = st.data_editor(default_rows, num_rows="dynamic", use_container_width=True,
                    key="grid_editor", column_config={
                        "参与": st.column_config.CheckboxColumn("参与"),
                        "参数名": st.column_config.TextColumn("参数名", width="medium"),
                        "候选值(逗号分隔)": st.column_config.TextColumn("候选值", width="large"),
                    }, disabled=not enable_grid)
                ss["grid_rows_latest"] = grid_rows_local
                gd = _parse_grid_rows(grid_rows_local) if enable_grid else {}
                tc = _cartesian_count(gd)
                if enable_grid:
                    g1, g2, g3, g4 = st.columns([1, 1, 1, 2])
                    with g1: st.metric("组合数", f"{tc}")
                    with g2: max_batch = st.number_input("上限", 1, 1000, MAX_BATCH, 1)
                    with g3: name_suffix = st.text_input("后缀", value="{idx}/{N}")
                    with g4: update_plan = st.form_submit_button("🧪 预览", type="secondary")
                    if update_plan and tc > 0:
                        avg_sec = _get_avg_duration_sec(str(leaderboard_id))
                        est = None if avg_sec is None else float(avg_sec) * float(tc)
                        e1, e2, e3 = st.columns(3)
                        with e1: st.metric("组合", f"{tc}")
                        with e2: st.metric("单次", _fmt_dur(avg_sec))
                        with e3: st.metric("总耗时", _fmt_dur(est))
                        pn = st.slider("预览", 1, min(50, tc), min(10, tc))
                        st.dataframe([{"#": i, **c} for i, c in enumerate(_cartesian_combos(gd)[:pn], 1)],
                            use_container_width=True, hide_index=True)
                else:
                    max_batch = MAX_BATCH; name_suffix = "{idx}/{N}"
                st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
                b1, b2 = st.columns(2)
                with b1: submitted_single = st.form_submit_button("▶️ 提交", type="primary", use_container_width=True)
                with b2: submitted_batch = st.form_submit_button("⚡ 批量提交", use_container_width=True, disabled=not enable_grid)

        # ── 提交时从 store 生成 env_text ──
        final_env_text = _store_to_text(ss["_env_store"])

        if submitted_single:
            if not submission_name or not algorithm_image_url:
                st.warning("请填写任务名称和镜像。")
            else:
                r = api_request("POST", f"leaderboard/{leaderboard_id}/submit", data={
                    "submission_name": f"{submission_name.strip()}_{_now_ts_ms()}",
                    "algorithm_image_url": algorithm_image_url.strip(),
                    "env_text": final_env_text, "params": {}})
                if r:
                    st.success(f"✅ ID: {r.get('submission_id','?')} · Job: {r.get('job_name','?')}")
                    ss.my_submissions = None; navigate("my_submissions")

        if submitted_batch:
            if not submission_name or not algorithm_image_url:
                st.warning("请填写任务名称和镜像。")
            elif not enable_grid: st.warning("启用网格搜索。")
            else:
                bn = f"{submission_name.strip()}_{_now_ts_ms()}"
                gd2 = _parse_grid_rows(ss.get("grid_rows_latest", []))
                tc2 = _cartesian_count(gd2)
                if tc2 <= 0: st.warning("无有效组合。")
                else:
                    lim = int(max_batch or MAX_BATCH); nr = min(tc2, lim)
                    if tc2 > lim: st.warning(f"共{tc2}，仅提交前{nr}个。")
                    ac = _cartesian_combos(gd2)[:nr]
                    ok, fail, res = 0, 0, []
                    with st.spinner(f"批量提交 {nr} 个..."):
                        for i, combo in enumerate(ac, 1):
                            sf = name_suffix or ""
                            sf = sf.format(idx=i, N=nr) if ("{idx}" in sf or "{N}" in sf) else (sf or f"{i}/{nr}")
                            ps = ";".join(f"{k}={combo[k]}" for k in sorted(combo)) if combo else ""
                            sn = f"{bn} [{sf}]" + (f" {ps}" if ps else "")
                            r = api_request("POST", f"leaderboard/{leaderboard_id}/submit", data={
                                "submission_name": sn, "algorithm_image_url": algorithm_image_url.strip(),
                                "env_text": final_env_text, "params": combo, "batch": True})
                            if r: ok += 1; res.append({"name": sn, "id": r.get("submission_id"), "job": r.get("job_name")})
                            else: fail += 1
                    st.success(f"✅{ok}成功 ❌{fail}失败")
                    if res: st.dataframe(res[:50], use_container_width=True, hide_index=True)
                    ss.my_submissions = None; navigate("my_submissions")

    # ════ TAB 2 ════
    with tab_upload:
        st.caption("上传 JSON/JSONL 直接评测。")
        with st.form("upload_eval_form"):
            upload_name = st.text_input("任务名称*", placeholder="my-rag-output-eval")
            upload_file = st.file_uploader("评测结果文件*", type=["json", "jsonl"])
            upload_note = st.text_area("备注（可选）", placeholder="模型版本、策略等", height=80)
            eval_img = st.text_input("评测镜像（可选）", placeholder="留空默认")
            upload_submit = st.form_submit_button("📤 上传并评测", type="primary")
        if upload_submit:
            if not upload_name: st.warning("填写名称。")
            elif not upload_file: st.warning("上传文件。")
            else:
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                title = f"{upload_name.strip()}_{ts}"
                files = {"file": (upload_file.name, upload_file.getvalue())}
                data = {"leaderboard_id": str(leaderboard_id), "title": title}
                if upload_note: data["note"] = upload_note
                if eval_img: data["evaluator_image"] = eval_img.strip()
                with st.spinner("上传中..."):
                    resp = api_request("POST", "submission/upload", data=data, files=files)
                if not resp: st.error("上传失败。")
                else:
                    st.success(f"✅ ID={resp.get('submission_id','?')} · Job={resp.get('job_name','?')}")
                    ss.my_submissions = None; navigate("my_submissions")


def _sync_store_to_kv_keys(ss):
    """同步 _env_store → 所有 _ev_* widget keys"""
    store = ss.get("_env_store", {})
    # 删掉 store 中已不存在的 key
    for k in [k for k in ss if k.startswith("_ev_")]:
        if k[4:] not in store:
            del ss[k]
    # 删掉对应的 history selectbox key
    for k in [k for k in ss if k.startswith("_eh_")]:
        if k[4:] not in store:
            del ss[k]
    # 写入当前值
    for k, v in store.items():
        ss[f"_ev_{k}"] = v

# ======== 我的提交 ========
def _log_viewer_html(sid: int, api_base: str, token: str) -> str:
    # f-string 内不能有反斜杠，提前定义
    newline = "\\n"
    return f"""<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{
  font-family:'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace;
  font-size:12px;background:#f8f9fa;color:#1f2328;
  height:100vh;overflow:hidden;display:flex;flex-direction:column;
}}
.toolbar{{
  display:flex;align-items:center;gap:10px;
  padding:8px 14px;background:#ffffff;
  border-bottom:1px solid #d0d7de;flex-shrink:0;
}}
.indicator{{
  display:flex;align-items:center;gap:6px;padding:3px 10px;
  border-radius:20px;background:#ddf4ff;border:1px solid #54aeff;
  font-size:11px;color:#0969da;font-family:inherit;
}}
.indicator.done{{background:#dafbe1;border-color:#4ac26b;color:#1a7f37}}
.indicator.error{{background:#ffebe9;border-color:#ff8182;color:#cf222e}}
.dot{{width:7px;height:7px;border-radius:50%;background:#0969da;animation:pulse 1.2s ease-in-out infinite;}}
.dot.done{{background:#1a7f37;animation:none}}
.dot.error{{background:#cf222e;animation:none}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.25}}}}
.status{{font-size:11px;color:#57606a;flex:1;font-family:-apple-system,sans-serif}}
.btn{{
  background:#f6f8fa;border:1px solid #d0d7de;color:#24292f;
  padding:4px 12px;border-radius:6px;cursor:pointer;font-size:11px;
  font-family:-apple-system,sans-serif;transition:background .12s,border-color .12s;white-space:nowrap;
}}
.btn:hover{{background:#f3f4f6;border-color:#b1bac4}}
.btn.active{{background:#ddf4ff;border-color:#0969da;color:#0969da}}
.panels{{flex:1;display:flex;flex-direction:column;overflow:hidden;min-height:0;}}
.panel{{display:flex;flex-direction:column;flex:1;min-height:0;overflow:hidden;border-bottom:1px solid #d0d7de;}}
.panel:last-child{{border-bottom:none}}
.phdr{{
  display:flex;align-items:center;gap:8px;padding:6px 14px;
  background:#f6f8fa;border-bottom:1px solid #d0d7de;
  flex-shrink:0;user-select:none;
}}
.phdr-left{{display:flex;align-items:center;gap:8px;flex:1;cursor:pointer;}}
.chev{{font-size:9px;color:#6e7781;transition:transform .15s;display:inline-block;width:10px;}}
.chev.collapsed{{transform:rotate(-90deg)}}
.pname{{font-size:11px;font-weight:600;color:#24292f;font-family:-apple-system,sans-serif;}}
.pcnt{{
  font-size:10px;color:#6e7781;font-family:-apple-system,sans-serif;
  background:#eaeef2;padding:1px 7px;border-radius:10px;
}}
.dl-btn{{
  background:#f6f8fa;border:1px solid #d0d7de;color:#0969da;
  padding:3px 10px;border-radius:5px;cursor:pointer;font-size:10px;
  font-family:-apple-system,sans-serif;transition:background .12s;white-space:nowrap;flex-shrink:0;
}}
.dl-btn:hover{{background:#ddf4ff;border-color:#0969da;}}
.dl-btn:disabled{{color:#b1bac4;border-color:#eaeef2;cursor:not-allowed;background:#f6f8fa;}}
.truncate-bar{{
  padding:3px 14px;background:#fff8c5;border-bottom:1px solid #d4a72c;
  font-size:10px;color:#9a6700;font-family:-apple-system,sans-serif;flex-shrink:0;
}}
.logbody{{flex:1;overflow-y:auto;overflow-x:auto;min-height:0;}}
.logbody.hidden{{display:none}}
table{{border-collapse:collapse;width:100%;min-width:100%}}
tr:hover td{{background:#f6f8fa}}
td{{padding:0;vertical-align:top;line-height:1.6}}
.ln{{
  color:#b1bac4;text-align:right;padding:0 10px 0 14px;
  min-width:44px;width:44px;user-select:none;white-space:nowrap;
  border-right:2px solid #eaeef2;font-size:11px;
}}
.lt{{color:#1f2328;padding:0 16px 0 10px;white-space:pre;font-size:12px;}}
.ts-px{{color:#8c959f;margin-right:8px}}
.kw-err{{color:#cf222e;font-weight:600}}
.kw-warn{{color:#9a6700}}
.kw-ok{{color:#1a7f37;font-weight:600}}
.empty{{
  display:flex;align-items:center;justify-content:center;
  height:60px;color:#8c959f;font-size:11px;font-family:-apple-system,sans-serif;
}}
</style>

<div class="toolbar">
  <div class="indicator" id="indicator">
    <div class="dot" id="dot"></div>
    <span id="ind-text">连接中</span>
  </div>
  <span class="status" id="status">正在拉取日志…</span>
  <button class="btn active" id="sbtn" onclick="toggleScroll()">↓ 自动滚动</button>
</div>

<div class="panels">
  <div class="panel">
    <div class="phdr">
      <div class="phdr-left" onclick="togglePanel('eval')">
        <span class="chev" id="eval-chev">▾</span>
        <span class="pname">evaluator-container</span>
        <span class="pcnt" id="eval-cnt">0 行</span>
      </div>
      <button class="dl-btn" id="eval-dl"
        onclick="downloadLog('evaluator-container','eval-dl')">↓ 下载全量日志</button>
    </div>
    <div class="truncate-bar" id="eval-trunc" style="display:none"></div>
    <div class="logbody" id="eval-body">
      <div class="empty" id="eval-empty">等待日志…</div>
      <table id="eval-tbl"></table>
    </div>
  </div>

  <div class="panel">
    <div class="phdr">
      <div class="phdr-left" onclick="togglePanel('algo')">
        <span class="chev" id="algo-chev">▾</span>
        <span class="pname">submitter-container (algorithm)</span>
        <span class="pcnt" id="algo-cnt">0 行</span>
      </div>
      <button class="dl-btn" id="algo-dl"
        onclick="downloadLog('submitter-container','algo-dl')">↓ 下载全量日志</button>
    </div>
    <div class="truncate-bar" id="algo-trunc" style="display:none"></div>
    <div class="logbody" id="algo-body">
      <div class="empty" id="algo-empty">等待日志…</div>
      <table id="algo-tbl"></table>
    </div>
  </div>
</div>

<script>
const API={repr(api_base)}, TOK={repr(token or '')}, SID={sid}, INTERVAL=1500;
const MAX_DISPLAY=1500;
let evalOff=0, algoOff=0;
let done=false, legacyDone=false, autoScroll=true, timer=null;
let graceLeft=0;

const $=id=>document.getElementById(id);

function toggleScroll(){{
  autoScroll=!autoScroll;
  $('sbtn').textContent=autoScroll?'↓ 自动滚动':'⊘ 已锁定';
  $('sbtn').className='btn'+(autoScroll?' active':'');
}}

function togglePanel(name){{
  var b=$(name+'-body'), c=$(name+'-chev');
  var hidden=b.classList.toggle('hidden');
  c.textContent=hidden?'▸':'▾';
  c.className='chev'+(hidden?' collapsed':'');
}}

function esc(s){{
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

function highlight(s){{
  if(/error|exception|traceback|fatal|critical/i.test(s))
    return '<span class="kw-err">'+esc(s)+'</span>';
  if(/warn(ing)?/i.test(s))
    return '<span class="kw-warn">'+esc(s)+'</span>';
  if(/success|succeed|complete|done|finished|passed/i.test(s))
    return '<span class="kw-ok">'+esc(s)+'</span>';
  return esc(s);
}}

function appendLines(tblId, bodyId, emptyId, truncId, lines, nRef, cntId){{
  if(!lines||!lines.length) return;
  $(emptyId).style.display='none';
  var tbl=$(tblId), frag=document.createDocumentFragment();
  lines.forEach(function(raw){{
    var ts='', text=raw;
    var m=raw.match(/^(\\d{{4}}-\\d{{2}}-\\d{{2}}T[\\d:.]+Z)\\s/);
    if(m){{ ts=m[1].slice(0,19).replace('T',' '); text=raw.slice(m[0].length); }}
    var tr=document.createElement('tr');
    tr.innerHTML='<td class="ln">'+nRef.n+'</td>'
      +'<td class="lt">'+(ts?'<span class="ts-px">'+ts+'</span>':'')
      +highlight(text)+'</td>';
    frag.appendChild(tr);
    nRef.n++;
  }});
  tbl.appendChild(frag);

  // ✅ 超出 MAX_DISPLAY 行时，从头部删除，只保留最新的
  var overCount = tbl.rows.length - MAX_DISPLAY;
  if(overCount > 0){{
    for(var i=0;i<overCount;i++) tbl.deleteRow(0);
  }}

  var totalReceived = nRef.n - 1;
  var truncated = totalReceived > MAX_DISPLAY;
  $(cntId).textContent = totalReceived + ' 行';
  var trunc = $(truncId);
  if(truncated){{
    trunc.style.display='block';
    trunc.textContent = '仅显示最新 '+MAX_DISPLAY+' 行，共 '+totalReceived+' 行 — 点击「下载全量日志」获取完整内容';
  }} else {{
    trunc.style.display='none';
  }}

  if(autoScroll){{
    var b=$(bodyId);
    b.scrollTop=b.scrollHeight;
  }}
}}

const eRef={{n:1}}, aRef={{n:1}};

function setStatus(state, indText, detail){{
  var ind=$('indicator'), dot=$('dot'), it=$('ind-text');
  ind.className='indicator'+(state==='done'?' done':state==='error'?' error':'');
  dot.className='dot'+(state==='done'?' done':state==='error'?' error':'');
  it.textContent=state==='done'?'完成':state==='error'?'错误':'采集中';
  $('status').textContent=detail||indText;
}}

async function downloadLog(container, btnId){{
  var btn=$(btnId);
  btn.disabled=true;
  btn.textContent='下载中…';
  try{{
    var url=API+'/api/submission/'+SID+'/logs/download?container='+encodeURIComponent(container);
    var r=await fetch(url,{{headers:{{Authorization:'Bearer '+TOK}}}});
    if(!r.ok){{ alert('下载失败: HTTP '+r.status); return; }}
    var fname=r.headers.get('X-Filename')||(container+'-sub'+SID+'.log');
    var blob=await r.blob();
    var a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download=fname;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  }}catch(e){{
    alert('下载失败: '+e.message);
  }}finally{{
    btn.disabled=false;
    btn.textContent='↓ 下载全量日志';
  }}
}}

async function poll(){{
  if(done||legacyDone) return;
  try{{
    var url=API+'/api/submission/'+SID+'/logs?eval_offset='+evalOff+'&algo_offset='+algoOff+'&limit=300';
    var r=await fetch(url,{{headers:{{Authorization:'Bearer '+TOK}}}});
    if(!r.ok){{ setStatus('error','错误','HTTP '+r.status); return; }}
    var d=await r.json();

    if(d.mode==='incremental'){{
      var hasNew=(d.evaluator_lines&&d.evaluator_lines.length>0)
               ||(d.algorithm_lines&&d.algorithm_lines.length>0);
      appendLines('eval-tbl','eval-body','eval-empty','eval-trunc', d.evaluator_lines||[], eRef,'eval-cnt');
      appendLines('algo-tbl','algo-body','algo-empty','algo-trunc', d.algorithm_lines||[], aRef,'algo-cnt');
      evalOff=d.eval_next_offset!=null?d.eval_next_offset:evalOff;
      algoOff=d.algo_next_offset!=null?d.algo_next_offset:algoOff;

      if(d.finalized){{
        if(hasNew){{
          graceLeft=5;
          setStatus('running','收尾中','任务已结束，正在等待最后日志写入…');
        }}else{{
          if(graceLeft===0) graceLeft=5;
          graceLeft--;
          if(graceLeft<=0){{
            done=true; clearInterval(timer);
            setStatus('done','完成','任务结束 — evaluator '+(eRef.n-1)+' 行，algorithm '+(aRef.n-1)+' 行');
          }}else{{
            setStatus('running','收尾中','确认日志完整中… ('+graceLeft+')');
          }}
        }}
      }}else{{
        graceLeft=0;
        setStatus('running','采集中','evaluator '+(eRef.n-1)+' 行  ·  algorithm '+(aRef.n-1)+' 行');
      }}

    }}else if(d.mode==='legacy'){{
      var el=(d.evaluator_log||'').split('{newline}').filter(Boolean);
      var al=(d.algorithm_log||'').split('{newline}').filter(Boolean);
      appendLines('eval-tbl','eval-body','eval-empty','eval-trunc', el, eRef,'eval-cnt');
      appendLines('algo-tbl','algo-body','algo-empty','algo-trunc', al, aRef,'algo-cnt');
      legacyDone=true; clearInterval(timer);
      setStatus('done','完成','历史固化日志（全量）');
    }}else{{
      setStatus('running','等待','采集器启动中，稍候…');
    }}
  }}catch(e){{
    setStatus('error','错误','请求失败: '+e.message);
  }}
}}

poll();
timer=setInterval(poll, INTERVAL);
</script>"""

# ======== 我的提交 ========
def render_my_submissions():
    if not check_role(["participant", "creator", "admin"]):
        st.error("您没有访问此页面的权限。")
        return

    if "my_sub_page" not in st.session_state:
        st.session_state.my_sub_page = 1

    PER_PAGE = 10

    # 列表自动刷新（只刷新列表，不影响日志组件）
    list_rc = st_auto(LIST_REFRESH_MS, key="auto_my_list",
                      enable=st.session_state.get("auto_list", True))
    if list_rc and list_rc != st.session_state.get("_list_rc", 0):
        st.session_state["_list_rc"] = list_rc
        cached_get_default.clear()
        st.session_state.my_submissions = None

    st.title("🚀 我的提交记录")

    ctrl1, ctrl2, _ = st.columns([1, 1, 2])
    with ctrl1:
        if st.button("🔄 刷新列表", use_container_width=True):
            cached_get_default.clear()
            st.session_state.my_submissions = None
            st.session_state.my_sub_page = 1
            st.rerun()
    with ctrl2:
        st.session_state.auto_list = st.checkbox(
            "⏱️ 自动刷新列表", value=st.session_state.get("auto_list", True),
            help=f"每 {LIST_REFRESH_MS} ms 自动刷新"
        )

    if st.session_state.my_submissions is None:
        current_page = st.session_state.my_sub_page
        with st.spinner(f"正在加载第 {current_page} 页数据..."):
            data = api_get(f"my-submissions?page={current_page}&per_page={PER_PAGE}")
        st.session_state.my_submissions = data if data else {"items": [], "total": 0, "pages": 1}

    data_bundle = st.session_state.my_submissions
    if isinstance(data_bundle, list):
        data_bundle = {"items": data_bundle, "total": len(data_bundle), "pages": 1}

    if not data_bundle or "items" not in data_bundle:
        st.info("正在加载...")
        return

    items       = data_bundle["items"]
    total       = data_bundle["total"]
    total_pages = data_bundle.get("pages", 1)

    if not items and total == 0:
        st.info("您还没有任何提交记录。")
        return

    # 分页控件
    colP1, colP2, colP3 = st.columns([1, 2, 2])
    with colP1:
        st.write(f"共 {total} 条，当前 {st.session_state.my_sub_page}/{total_pages} 页")
    with colP2:
        if st.button("⬅️ 上一页", disabled=st.session_state.my_sub_page <= 1,
                     use_container_width=True):
            st.session_state.my_sub_page -= 1
            st.session_state.my_submissions = None
            st.rerun()
    with colP3:
        if st.button("下一页 ➡️", disabled=st.session_state.my_sub_page >= total_pages,
                     use_container_width=True):
            st.session_state.my_sub_page += 1
            st.session_state.my_submissions = None
            st.rerun()

    # 渲染表格
    display = []
    for srow in items:
        display.append({
            "ID":     srow["id"],
            "任务名称": srow["name"],
            "榜单":   srow.get("leaderboard_name", f"ID: {srow['leaderboard_id']}"),
            "状态":   srow["status"],
            "分数":   srow.get("score", "N/A"),
            "提交时间": _fmt_ts(srow.get("submitted_at")),
        })
    st.dataframe(display, use_container_width=True, hide_index=True)

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.subheader("操作")

    cols_per_row = 4
    row_cols = None
    for i, srow in enumerate(items):
        if i % cols_per_row == 0:
            row_cols = st.columns(cols_per_row)
        col = row_cols[i % cols_per_row]
        with col:
            sid = srow["id"]
            st.write(f"**任务 ID: {sid}** ({srow['status']})")

            if srow["status"] in ["Running", "Succeeded", "Failed", "Cancelled"]:
                if st.button("📄 查看日志", key=f"log_{sid}", use_container_width=True):
                    st.session_state.log_modal_submission_id = sid

            if srow["status"] == "Failed":
                if st.button("🔁 重新运行", key=f"rerun_{sid}", use_container_width=True):
                    r = api_request("POST", f"submission/{sid}/rerun")
                    if r:
                        st.success(f"已请求重新运行任务 {sid}。")
                        st.session_state.my_submissions = None
                        st.rerun()

            if srow["status"] in ["Pending", "Running", "Submitted"]:
                if st.button("❌ 取消任务", key=f"cancel_{sid}", use_container_width=True):
                    r = api_request("DELETE", f"submission/{sid}")
                    if r:
                        st.success(f"已请求取消任务 {sid}。")
                        st.session_state.my_submissions = None
                        st.rerun()

    # ──────────────── 日志面板 ────────────────
    if not st.session_state.log_modal_submission_id:
        return

    sid = st.session_state.log_modal_submission_id

    st.markdown(f"#### 📋 任务 {sid} 日志")
    if st.button("✖ 关闭日志", key=f"close_log_{sid}"):
        st.session_state.log_modal_submission_id = None
        st.rerun()

    # ✅ 核心：JS 组件完全独立于 Streamlit rerun，自己 poll、自己追加、自己滚动
    # 不需要 st_auto，不需要 cached_get_logfast，不需要任何 session_state buffer
    # 浏览器端日志拉取走同源相对路径（API='' → /api/...），不把 PUBLIC_BASE_URL 域名
    # 硬编码进页面；依赖前置代理在同源下代理 /api/（algo.xskill.wiki 的 nginx 即是）。
    # 这样无论从哪个域名/隧道访问，日志请求都打到当前页面同源，不再触发跨域预检。
    html = _log_viewer_html(sid, "", st.session_state.get("token") or "")
    components.html(html, height=700, scrolling=False)

# ======== 管理榜单 ========
def render_manage_leaderboards():
    if not check_role(["creator", "admin"]):
        st.error("您没有访问此页面的权限。")
        return

    st.title("📋 管理榜单")

    c1, _ = st.columns([1, 5])
    with c1:
        if st.button("➕ 发布新榜单", use_container_width=True, type="primary"):
            st.session_state.current_leaderboard_edit = None
            navigate("create_edit_leaderboard")
        if st.button("🔄 刷新列表", use_container_width=True):
            cached_get_default.clear()
            st.session_state.my_leaderboards = None
            st.rerun()

    if st.session_state.my_leaderboards is None:
        d = api_get("leaderboards/manage")
        if isinstance(d, list):
            st.session_state.my_leaderboards = d

    lbs = st.session_state.my_leaderboards
    if lbs is None:
        st.info("正在加载榜单列表...")
        return
    if not lbs:
        st.info("您还没有发布任何榜单。")
        return

    for b in lbs:
        with st.container():
            st.markdown('<div class="card">', unsafe_allow_html=True)
            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(
                    f"<h3 class='lb-h2'>{html_escape(b['name'])} (v{html_escape(b['version'])})</h3>",
                    unsafe_allow_html=True
                )
                st.caption(f"ID: {b['id']} | Owner: {b.get('owner_username','N/A')} | 难度系数: {b.get('difficulty_factor','1.0')} | SOTA: {b.get('sota_score','—')}")
                st.write(b.get("description") or "无描述")
            with col2:
                if st.button("✏️ 编辑", key=f"edit_{b['id']}", use_container_width=True):
                    st.session_state.current_leaderboard_edit = b
                    navigate("create_edit_leaderboard", params={"id": b["id"]})
            st.markdown('</div>', unsafe_allow_html=True)

# ======== 创建/编辑榜单 ========
# ======== 创建/编辑榜单 ========
def render_create_edit_leaderboard():
    if not check_role(["creator", "admin"]):
        st.error("您没有访问此页面的权限。")
        return

    # ========== 进入页面弹窗：BCG 数据规范提醒 ==========
    def _ensure_bcg_data_notice():
        ack_key = "bcg_data_notice_ack_create_edit"
        if st.session_state.get(ack_key):
            return

        dialog_fn = getattr(st, "dialog", None) or getattr(st, "experimental_dialog", None)

        if dialog_fn is not None:
            @dialog_fn("数据合规提醒")
            def _dlg():
                st.markdown("### ⚠️ 合规提醒")
                st.markdown("请遵守BCG数据规范，禁止建榜时上传公司保密信息，违者自负。")

                c1, c2 = st.columns([1, 1])
                with c1:
                    if st.button("我已知悉", type="primary"):
                        st.session_state[ack_key] = True
                        st.rerun()
                with c2:
                    if st.button("返回"):
                        navigate("manage_leaderboards")

            _dlg()
            st.stop()
        else:
            # 兜底：没有 dialog 能力时，用警告条 + 勾选确认
            st.warning("⚠️ 请遵守BCG数据规范，禁止建榜时上传公司保密信息，违者自负。")
            if not st.checkbox("我已知悉并遵守", key="bcg_data_notice_checkbox"):
                st.stop()
            st.session_state[ack_key] = True
            st.rerun()

    _ensure_bcg_data_notice()
    # ========== 弹窗结束 ==========

    edit_mode = "id" in st.session_state.page_params
    page_title = "编辑榜单" if edit_mode else "发布新榜单"
    leaderboard_id = st.session_state.page_params.get("id") if edit_mode else None

    st.title(f"{'✏️' if edit_mode else '➕'} {page_title}")

    default_values = {}
    if edit_mode:
        cached = st.session_state.current_leaderboard_edit
        if cached and cached["id"] == leaderboard_id:
            default_values = cached
            default_values["resource_spec_str"] = default_values.get("resource_spec_str", "")
            default_values["evaluator_image"] = default_values.get("evaluator_image", "")
            default_values["baseline_image"] = default_values.get("baseline_image", "")
        else:
            st.warning("无法加载编辑数据，请返回管理页面重试。")
            return

    with st.form("leaderboard_form"):
        name = st.text_input("榜单名称*", value=default_values.get("name", ""))
        version = st.text_input("版本号*", value=default_values.get("version", "v1.0"))
        description = st.text_area("描述 (支持 Markdown)", value=default_values.get("description", ""), height=150)
        evaluator_image = st.text_input(
            "评测镜像 URL*" if not edit_mode else "评测镜像 URL（不修改请留空）",
            value=default_values.get("evaluator_image", "") if not edit_mode else "",
            placeholder="例如: registry/eval-image:latest",
        )
        baseline_image = st.text_input(
            "Baseline 镜像 URL (可选，留空表示无/不变)",
            value=default_values.get("baseline_image", "") if not edit_mode else "",
            placeholder="例如: registry/baseline-image:latest",
        )
        resource_spec_str = st.text_area(
            "资源需求 (JSON)*" if not edit_mode else "资源需求 (JSON，留空表示不变)",
            value=default_values.get("resource_spec_str", "") if edit_mode else '{\n  "limits": {"huawei.com/Ascend310P": "1"},\n  "requests": {"huawei.com/Ascend310P": "1"}\n}',
            height=150,
            help='K8s 资源请求与限制，JSON 格式。',
        )
        difficulty_factor = st.text_input(
            "难度系数（默认 1.0）", value=str(default_values.get("difficulty_factor", "1.0"))
        )
        sota_score = st.text_input(
            "SOTA 分数（可空）", value="" if default_values.get("sota_score") in (None, "") else str(default_values.get("sota_score"))
        )

        submitted = st.form_submit_button("保存榜单" if edit_mode else "发布榜单", type="primary")

        if submitted:
            payload = {}
            if name: payload["name"] = name
            if version: payload["version"] = version
            payload["description"] = description

            if evaluator_image:
                payload["evaluator_image"] = evaluator_image
            payload["baseline_image"] = baseline_image

            if not edit_mode or resource_spec_str.strip():
                try:
                    resource_spec_dict = json.loads(resource_spec_str) if resource_spec_str.strip() else None
                    if not edit_mode and not isinstance(resource_spec_dict, dict):
                        st.error("资源需求必须是一个 JSON 对象。")
                        st.stop()
                    if resource_spec_dict is not None:
                        payload["resource_spec"] = resource_spec_dict
                except json.JSONDecodeError:
                    st.error("资源需求格式错误，请输入有效的 JSON。")
                    st.stop()

            try:
                if difficulty_factor.strip():
                    payload["difficulty_factor"] = float(difficulty_factor.strip())
            except Exception:
                st.error("难度系数应为数字。")
                st.stop()
            try:
                if sota_score.strip():
                    payload["sota_score"] = float(sota_score.strip())
                else:
                    payload["sota_score"] = None
            except Exception:
                st.error("SOTA 分数应为数字或留空。")
                st.stop()

            if edit_mode:
                if not payload:
                    st.warning("没有任何修改内容。")
                    return
                result = api_request("PUT", f"leaderboards/{leaderboard_id}", data=payload)
            else:
                if not evaluator_image:
                    st.warning("请填写评测镜像 URL。")
                    return
                if "resource_spec" not in payload:
                    st.warning("请填写资源需求 JSON。")
                    return
                result = api_request("POST", "leaderboards", data=payload)

            if result:
                st.success(f"榜单 {'更新' if edit_mode else '发布'} 成功！")
                cached_get_default.clear()
                st.session_state.my_leaderboards = None
                st.session_state.leaderboards = None
                navigate("manage_leaderboards")


# ======== 14) 页面：管理员-用户管理 ========
def render_admin_users():
    if not check_role(["admin"]):
        st.error("您没有访问此页面的权限。")
        return

    st.title("👥 用户管理")

    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("➕ 创建新用户", use_container_width=True, type="primary"):
            st.session_state.show_create_user_modal = True
            st.rerun()
        if st.button("🔄 刷新列表", use_container_width=True):
            cached_get_default.clear()
            st.session_state.all_users = None
            st.rerun()

    if st.session_state.show_create_user_modal:
        with st.expander("创建新用户", expanded=True):
            with st.form("create_user_form"):
                new_username = st.text_input("用户名*")
                new_password = st.text_input("初始密码*", type="password")
                new_role = st.selectbox("角色*", ["participant", "creator", "admin"])
                c = st.columns(2)
                create_submitted = c[0].form_submit_button("创建用户", type="primary")
                cancel_create = c[1].form_submit_button("取消")

                if create_submitted:
                    if not new_username or not new_password:
                        st.warning("请填写用户名和密码。")
                    else:
                        payload = {"username": new_username, "password": new_password, "role": new_role}
                        result = api_request("POST", "admin/users", data=payload)
                        if result:
                            st.success(f"用户 '{new_username}' 创建成功。")
                            cached_get_default.clear()
                            st.session_state.all_users = None
                            st.session_state.show_create_user_modal = False
                            st.rerun()
                if cancel_create:
                    st.session_state.show_create_user_modal = False
                    st.rerun()

    if st.session_state.all_users is None:
        data = api_get("admin/users")
        if isinstance(data, list):
            st.session_state.all_users = data

    users = st.session_state.all_users
    if users is None:
        st.info("正在加载用户列表...")
        return
    if not users:
        st.info("系统中还没有用户。")
        return

    display_users = []
    for u in users:
        display_users.append(
            {
                "ID": u["id"],
                "用户名": u["username"],
                "角色": u["role"],
                "创建时间": _fmt_ts(u.get("created_at")),
            }
        )
    st.dataframe(display_users, use_container_width=True, hide_index=True)

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.subheader("用户操作")

    cols_per_row = 4
    row_cols = None
    for i, u in enumerate(users):
        if i % cols_per_row == 0:
            row_cols = st.columns(cols_per_row)
        col = row_cols[i % cols_per_row]
        with col:
            user_id = u["id"]; username = u["username"]; role = u["role"]
            st.write(f"**{username}** (ID: {user_id}, {role})")

            if st.button("修改密码", key=f"pwd_{user_id}", use_container_width=True):
                st.session_state.editing_user_password = u
                st.rerun()

            state_key = f"del_{user_id}"
            if state_key not in st.session_state:
                st.session_state[state_key] = False

            if not st.session_state[state_key]:
                if st.button("删除用户", key=f"req_del_{user_id}", use_container_width=True):
                    st.session_state[state_key] = True
                    st.rerun()
            else:
                st.warning(f"确认删除用户 '{username}'?")
                c1, c2 = st.columns(2)
                if c1.button("确认删除", key=f"confirm_del_{user_id}", type="primary", use_container_width=True):
                    result = api_request("DELETE", f"admin/users/{user_id}")
                    if result:
                        st.success(f"用户 '{username}' 已删除。")
                        cached_get_default.clear()
                        st.session_state.all_users = None
                        st.session_state[state_key] = False
                        st.rerun()
                if c2.button("取消删除", key=f"cancel_del_{user_id}", use_container_width=True):
                    st.session_state[state_key] = False
                    st.rerun()


# ======== 积分中心 ========
def render_points_center():
    if not st.session_state.token:
        st.error("请先登录。")
        return

    st.title("📈 积分中心（我的积分）")

    colA, colB, colC = st.columns([1.2, 1.2, 3])
    with colA:
        if st.button("🔄 刷新", use_container_width=True):
            cached_get_default.clear()
            st.session_state.points_me = None
            st.rerun()
    with colB:
        ym_input = st.text_input("筛选月份 (YYYY-MM，可留空)", value=st.session_state.points_me_ym or "")
        if st.button("应用筛选"):
            st.session_state.points_me_ym = ym_input.strip() or None
            cached_get_default.clear()
            st.session_state.points_me = None
            st.rerun()

    if st.session_state.points_me is None:
        params = {}
        if st.session_state.points_me_ym:
            params["year_month"] = st.session_state.points_me_ym
        data = api_get("points/me", params=params)
        if isinstance(data, dict):
            st.session_state.points_me = data

    points = st.session_state.points_me
    if not points:
        st.info("暂无积分数据。")
        return

    monthly = points.get("monthly", []) or []
    events = points.get("events", []) or []

    total_recent = sum(float(m.get("total_points", 0.0) or 0.0) for m in monthly)
    this_month = datetime.utcnow().strftime("%Y-%m")
    this_month_total = 0.0
    for m in monthly:
        if m.get("year_month") == this_month:
            this_month_total = float(m.get("total_points", 0.0) or 0.0)
            break

    st.markdown(
        "<div class='kpi'>"
        f"<div class='item'><div class='val'>{this_month_total:.4f}</div><div class='lab'>本月积分</div></div>"
        f"<div class='item'><div class='val'>{total_recent:.4f}</div><div class='lab'>近 12 个月总积分</div></div>"
        "</div>",
        unsafe_allow_html=True
    )

    if monthly:
        try:
            import pandas as pd
            import altair as alt
            months_sorted = sorted(monthly, key=lambda x: x["year_month"])
            months = [m["year_month"] for m in months_sorted]
            values = [float(m.get("total_points", 0.0) or 0.0) for m in months_sorted]
            df = pd.DataFrame({"year_month": months, "points": values})
            bar_size = 12 if len(df) == 1 else 24
            chart = (
                alt.Chart(df)
                .mark_bar(size=bar_size)
                .encode(
                    x=alt.X("year_month:N", title="月份"),
                    y=alt.Y("points:Q", title="积分"),
                    tooltip=[alt.Tooltip("year_month:N", title="月份"),
                             alt.Tooltip("points:Q", title="积分", format=".4f")],
                ).properties(height=240)
            )
            st.altair_chart(chart, use_container_width=True)
        except Exception:
            st.write(dict((m["year_month"], m.get("total_points", 0.0)) for m in monthly))

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.subheader("🧾 最近事件明细")
    if not events:
        st.info("暂无事件。超越 SOTA 的成功提交将会在此出现。")
    else:
        head = ["时间", "月份", "榜单ID", "提交ID", "积分", "Δ(score-SOTA)", "难度系数", "倍数", "得分", "SOTA"]
        trs = []
        for e in events:
            created_str = html_escape(_fmt_ts(e.get("created_at")))
            ym_str = html_escape(str(e.get("year_month", "")))
            lb_id_str = html_escape(str(e.get("leaderboard_id", "")))
            sub_id_str = html_escape(str(e.get("submission_id", "")))
            points_str = html_escape(f"{float(e.get('points', 0.0) or 0.0):.4f}")
            delta_str = html_escape(f"{float(e.get('delta', 0.0) or 0.0):.6f}")
            diff_str = html_escape(f"{float(e.get('difficulty_factor', 1.0) or 1.0):.2f}")
            mult_str = html_escape(f"{float(e.get('multiplier', 1.0) or 1.0):.2f}")
            score_str = html_escape(f"{float(e.get('score', 0.0) or 0.0):.6f}")
            sota_raw = e.get("sota_score", "")
            sota_str = html_escape("" if sota_raw is None else str(sota_raw))
            trs.append(
                "<tr>"
                f"<td class='small'>{created_str}</td>"
                f"<td class='mono'>{ym_str}</td>"
                f"<td class='mono'>{lb_id_str}</td>"
                f"<td class='mono'>{sub_id_str}</td>"
                f"<td class='score'>{points_str}</td>"
                f"<td class='mono'>{delta_str}</td>"
                f"<td class='mono'>{diff_str}</td>"
                f"<td class='mono'>{mult_str}</td>"
                f"<td class='mono'>{score_str}</td>"
                f"<td class='mono'>{sota_str}</td>"
                "</tr>"
            )
        html_table = (
            "<div class='table-wrap'><table class='lb-table'>"
            "<thead><tr>"
            + "".join(f"<th>{h}</th>" for h in head) +
            "</tr></thead>"
            f"<tbody>{''.join(trs)}</tbody>"
            "</table></div>"
        )
        st.markdown(html_table, unsafe_allow_html=True)



# ========== AGENT ================
def render_agent_ui():
    """
    Agent Chat v3.3 (Agno Backend Compatible):
    - 适配 POST /api/agent/chat
    - 适配 text_delta 流式事件
    - 修复参数名 agent_session_id
    """
    ss = st.session_state

    # ---- state ----
    ss.setdefault("agent_session_id", None)
    ss.setdefault("agent_chat", [])
    ss.setdefault("agent_chat_keep", 100)
    ss.setdefault("agent_shortcuts", [])
    ss.setdefault("agent_shortcuts_loaded", False)
    ss.setdefault("agent_streaming", False)

    st.title("🤖 Agent Chat")

    # ---- helpers ----
    def _base():
        # ⚠️ 确保这里指向正确的后端地址
        default = "http://leaderboard-api-svc:80"
        return (os.getenv("API_BASE_URL") or os.getenv("BACKEND_API_BASE_URL") or default).rstrip("/")

    def _headers():
        tok = ss.get("token") or ""
        return {"Authorization": f"Bearer {tok}"} if tok else {}

    def _url(path):
        return f"{_base()}/api/{path.lstrip('/')}"

    _NAMES = {
        "diagnose_task": "🔍 一键诊断", "batch_rerun_failed": "🔄 批量重跑",
        "get_task": "📋 查看任务", "get_task_logs": "📜 拉取日志",
        "list_curr_task": "📋 任务列表", "run_task": "🚀 提交任务",
        "cancel_task": "🛑 取消任务", "queue_status": "📡 队列状态",
        "note_add": "📝 添加备忘", "note_list": "📝 查看备忘",
        "wait": "⏳ 等待", "bash": "💻 执行命令",
        "search_user": "👤 搜索用户", "search_ladder": "🔍 搜索榜单"
    }
    def _friendly(n):
        return _NAMES.get(n or "", f"`{n}`")

    # ---- session ----
    def _ensure_session():
        if not ss.get("token"): return False
        if ss.get("agent_session_id"): return True
        try:
            s = requests.Session(); s.trust_env = False
            # 这里的 endpoint 是 agent/session，和后端代码一致
            r = s.get(_url("agent/session"), headers=_headers(), timeout=(3, 10))
            r.raise_for_status()
            d = r.json()
            if d.get("ok"):
                ss["agent_session_id"] = d["agent_session_id"]
                return True
        except Exception as e:
            logger.error(f"Session Error: {e}")
            st.error("无法连接到 Agent 服务，请检查网络或登录状态。")
        return False

    # ---- shortcuts ----
    def _shortcut_panel():
        if not ss.get("agent_shortcuts_loaded"):
            try:
                s = requests.Session(); s.trust_env = False
                r = s.get(_url("agent/shortcuts"), headers=_headers(), timeout=(2, 5))
                if r.ok: ss["agent_shortcuts"] = r.json().get("shortcuts", [])
                ss["agent_shortcuts_loaded"] = True
            except: pass

        shortcuts = ss.get("agent_shortcuts") or []
        if not shortcuts: return None

        cols = st.columns(min(len(shortcuts), 4))
        for i, cmd in enumerate(shortcuts):
            with cols[i % len(cols)]:
                if st.button(f"{cmd.get('icon','⚡')} {cmd['label']}", key=f"sc_{cmd['id']}", use_container_width=True):
                    return cmd.get("message", "")
        return None

    # ============================================================
    # ★ 核心流式处理逻辑 (适配 Agno runner.py)
    # ============================================================
    def _stream_and_render(message: str):
        sid = ss.get("agent_session_id")
        tool_steps = []
        response_text = ""
        step_count = 0

        # 1. 修正 URL 为 /api/agent/chat
        target_url = _url("agent/chat")

        # 2. 修正 Payload key 为 agent_session_id
        payload = {
            "agent_session_id": sid,
            "message": message
        }

        logger.info(f"🚀 POST {target_url} | SID: {sid}")

        try:
            s = requests.Session(); s.trust_env = False
            resp = s.post(
                target_url,
                headers={**_headers(), "Content-Type": "application/json"},
                json=payload,
                stream=True,
                timeout=(5, 300),
            )

            if resp.status_code != 200:
                logger.error(f"HTTP {resp.status_code}: {resp.text}")
                st.error(f"服务报错 ({resp.status_code})：{resp.text[:200]}")
                return None, []

        except Exception as e:
            logger.error(f"Connection Error: {e}", exc_info=True)
            st.error(f"连接失败: {e}")
            return None, []

        # UI 容器
        status_ui = st.status("🤖 正在思考...", expanded=True)
        text_placeholder = st.empty()

        # 事件循环
        try:
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line: continue
                line = raw_line.strip()
                if not line.startswith("data:"): continue

                try:
                    evt = json.loads(line[5:].strip())
                except: continue

                etype = evt.get("type", "") # 注意：后端用的是 "type" 不是 "event"

                # 忽略心跳和开始事件
                if etype in ("ping", "run_start"):
                    continue

                # --- 文本流 (Agno 使用 text_delta) ---
                if etype == "text_delta":
                    # 收到文本说明工具调用可能结束了，折叠状态栏
                    if not response_text and tool_steps:
                        status_ui.update(label=f"✅ {len(tool_steps)} 步操作完成", state="complete", expanded=False)

                    chunk = evt.get("content", "")
                    response_text += chunk
                    text_placeholder.markdown(response_text + " ▌")

                # --- 完整回答 (非流式回退) ---
                elif etype == "answer":
                    response_text = evt.get("content", "")
                    text_placeholder.markdown(response_text)

                # --- 工具开始 ---
                elif etype == "tool_start":
                    name = evt.get("name", "")
                    friendly = _friendly(name)
                    step_count += 1
                    tool_steps.append({
                        "name": name, "friendly": friendly,
                        "summary": "", "status": "running"
                    })
                    status_ui.update(label=f"🔄 Step {step_count}: {friendly}…")
                    with status_ui:
                        st.markdown(f"🔄 **Calling** `{name}`...")

                # --- 工具结束 ---
                elif etype == "tool_end":
                    name = evt.get("name", "")
                    summary = evt.get("summary", "完成")
                    # 更新对应的 step
                    for t in reversed(tool_steps):
                        if t["name"] == name and t["status"] == "running":
                            t["status"] = "done"
                            t["summary"] = summary
                            break
                    # UI 更新
                    with status_ui:
                        st.markdown(f"✅ **{_friendly(name)}** — {summary}")

                    done_cnt = sum(1 for t in tool_steps if t["status"] == "done")
                    status_ui.update(label=f"⚙️ 已执行 {done_cnt}/{len(tool_steps)} 步…")

                # --- 工具错误 ---
                elif etype == "tool_error":
                    name = evt.get("name", "")
                    err = evt.get("error", "Error")
                    for t in reversed(tool_steps):
                        if t["name"] == name and t["status"] == "running":
                            t["status"] = "error"
                            t["summary"] = err
                            break
                    with status_ui:
                        st.error(f"❌ **{name}** — {err}")

                # --- 全局错误 ---
                elif etype == "error":
                    content = evt.get("content", "Unknown Error")
                    logger.error(f"Agent Error: {content}")
                    st.error(f"Agent 错误: {content}")

                # --- 结束 ---
                elif etype == "done":
                    break

            resp.close()

        except Exception as e:
            logger.error(f"Stream Error: {e}", exc_info=True)
            st.warning("连接断开，显示已接收内容。")
            return response_text, tool_steps

        # 最终渲染清理（去光标）
        if response_text:
            text_placeholder.markdown(response_text)

        # 最终状态栏清理
        if tool_steps:
            has_err = any(t["status"] == "error" for t in tool_steps)
            status_ui.update(
                label=f"{'⚠️' if has_err else '✅'} {len(tool_steps)} 步完成",
                state="error" if has_err else "complete",
                expanded=False
            )
        else:
            # 没调工具直接回答
            status_ui.update(label="✅", state="complete", expanded=False)

        return response_text, tool_steps

    # ============================================================
    # 页面主逻辑
    # ============================================================
    if not ss.get("token"):
        st.info("请先登录。")
        return

    if not _ensure_session():
        st.stop()

    # Sidebar
    st.sidebar.markdown("---")
    with st.sidebar.expander("🤖 设置", expanded=False):
        ss["agent_chat_keep"] = st.number_input("保留对话数", 20, 500, ss["agent_chat_keep"], 20)
        if st.button("🧹 清空对话", use_container_width=True):
            ss["agent_chat"] = []
            st.rerun()
        st.caption(f"SID: `{ss.get('agent_session_id','?')[:8]}…`")

    # History
    for msg in ss["agent_chat"]:
        role = msg["role"]
        if role == "user":
            with st.chat_message("user"): st.markdown(msg["content"])
        elif role == "assistant":
            with st.chat_message("assistant"):
                # 渲染折叠的工具调用
                tools = msg.get("tools", [])
                if tools:
                    has_err = any(t["status"] == "error" for t in tools)
                    with st.status(f"{'⚠️' if has_err else '✅'} {len(tools)} 步操作", expanded=False, state="error" if has_err else "complete"):
                        for t in tools:
                            icon = "✅" if t["status"]=="done" else "❌"
                            st.markdown(f"{icon} **{t.get('friendly', t['name'])}** — {t.get('summary','')}")
                st.markdown(msg["content"])

    # Input
    sc_text = _shortcut_panel()
    prompt = st.chat_input("输入指令...")
    user_text = sc_text or (prompt.strip() if prompt else None)

    if user_text:
        # User Msg
        ss["agent_chat"].append({"role": "user", "content": user_text})
        with st.chat_message("user"): st.markdown(user_text)

        # Assistant Msg
        # 标记流式进行中，确保侧边栏队列自动刷新在本次回合内不会触发整页 rerun
        ss["agent_streaming"] = True
        try:
            with st.chat_message("assistant"):
                resp_txt, tools = _stream_and_render(user_text)
        finally:
            ss["agent_streaming"] = False

        if resp_txt is None:
            st.stop() # 报错停止

        ss["agent_chat"].append({"role": "assistant", "content": resp_txt, "tools": tools})

        # Limit History
        keep = ss["agent_chat_keep"]
        if len(ss["agent_chat"]) > keep:
            ss["agent_chat"] = ss["agent_chat"][-keep:]

        st.rerun()
# ======== 路由 ========
render_sidebar()
page_map = {
    "login": render_login_page,
    "public_leaderboards": render_public_leaderboards,
    "leaderboard_detail": render_leaderboard_detail,
    "submit": render_submit_page,
    "my_submissions": render_my_submissions,
    "manage_leaderboards": render_manage_leaderboards,
    "create_edit_leaderboard": render_create_edit_leaderboard,
    "admin_users": render_admin_users,
    "points_center": render_points_center,
    "evalscope_perf": render_evalscope_perf_ui,
    "agent_panel": render_agent_ui,
}
current = st.session_state.page
fn = page_map.get(current)
if fn:
    protected = ["submit", "my_submissions", "manage_leaderboards", "create_edit_leaderboard", "admin_users", "points_center"]
    if current in protected and not st.session_state.token:
        st.warning("请先登录。")
        render_login_page()
    else:
        fn()
else:
    st.error(f"页面 '{current}' 未找到。将导航回首页。")
    navigate("public_leaderboards")

