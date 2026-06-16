# app.py
# -----------------------------------------------------------------------------
# 极简评测服务（兼容 evaluator.py）：
#   - /healthz : 进程健康
#   - /ready   : 建库 + LLM 就绪；必须返回 llm_ok，兼容 evaluator.py
#   - /stats   : 统计（index_build_time_ms / total_questions）
#   - /qa      : 提问；返回 answer/elapsed_ms/used_tokens/retrieved/contexts
# 朴素 RAG 实现在 rag.py
# -----------------------------------------------------------------------------

from __future__ import annotations
import os
import time
import threading
import traceback
from typing import Optional, Any, Dict

from flask import Flask, request, jsonify
import requests
import sys


def _getenv_any(*keys: str, default: str = "") -> str:
    """从多个 key 中读取环境变量（容错：允许 key 名后带空格的情况）。"""
    for k in keys:
        v = os.environ.get(k)
        if v is not None:
            return v.strip()
    return default


DATA_DIR = os.environ.get("DATA_DIR", "/models/datasets/shenjiaosuo")
WORK_DIR = os.environ.get("WORK_DIR", "/app/rag_storage")
EXIT_ON_QA_ERROR = os.environ.get("EXIT_ON_QA_ERROR", "1") == "1"  # 保留但不再实际退出

CONFIG: Dict[str, Any] = {
    "data_dir": DATA_DIR,
    "work_dir": WORK_DIR,

    # 基础模式控制（保持兼容）
    "mode": os.environ.get("LIGHTRAG_MODE", "naive").lower(),
    "prompt_only": os.environ.get("PROMPT_ONLY", "0") == "1",

    # LLM（OpenAI/DashScope 兼容）
    "llm_base_url": _getenv_any("LLM_BASE_URL", "LLM_BASE_URL ", default="http://127.0.0.1:8888/v1"),
    "llm_api_key": _getenv_any("LLM_API_KEY", "LLM_API_KEY "),
    "llm_model_name": _getenv_any("LLM_MODEL", "LLM_MODEL_NAME", default="/models/Qwen3-4B-Instruct-2507"),

    # Embedding 服务（OpenAI embeddings 兼容）
    "embedding_endpoint": os.environ.get("EMBEDDING_API_ENDPOINT", "http://127.0.0.1:8889/v1"),
    "embedding_model": os.environ.get("EMBEDDING_MODEL", "/models/Qwen3-Embedding-0.6B"),
    "embedding_dim": int(os.environ.get("EMBEDDING_DIM", "1024")),

    # 文档解析引擎
    "document_loading_engine": os.environ.get("DOCUMENT_LOADING_ENGINE", "DEFAULT").upper(),

    # 朴素 RAG 参数（可通过环境变量调参）
    "chunk_size_tokens": int(os.environ.get("CHUNK_SIZE_TOKENS", "400")),
    "chunk_overlap_tokens": int(os.environ.get("CHUNK_OVERLAP_TOKENS", "80")),
    "retrieve_top_k": int(os.environ.get("RETRIEVE_TOP_K", "6")),
    "context_max_tokens": int(os.environ.get("CONTEXT_MAX_TOKENS", "2000")),
    "embed_batch": int(os.environ.get("EMBED_BATCH", "16")),
    "max_pdf_pages": int(os.environ.get("MAX_PDF_PAGES", "1000")),  # 防止超大 PDF
}

# -------- RAG 引擎加载 --------
try:
    import rag  # 由本目录 rag.py 提供
except Exception as e:
    print("FATAL: 缺少 rag.py 或导入失败：请实现 load_engine(config) -> Engine", file=sys.stderr)
    traceback.print_exc()
    os._exit(2)

try:
    ENGINE = rag.load_engine(CONFIG)  # 返回 Engine 实例
except Exception:
    print("FATAL: 调用 rag.load_engine(config) 失败，请检查实现。", file=sys.stderr)
    traceback.print_exc()
    os._exit(2)

# -------- 进程状态 --------
app = Flask(__name__)
_index_ready: bool = False
_index_error: Optional[str] = None
_index_cost_ms: int = 0
_total_questions: int = 0
_lock = threading.Lock()
_fatal_error: Optional[str] = None  # 新增：记录运行期“致命错误”，由 /healthz /ready 暴露给 evaluator

# -------- LLM 健康探针（给 /ready 的 llm_ok 用）--------
_LLM_BASE_URL = CONFIG["llm_base_url"]
_LLM_API_KEY = CONFIG["llm_api_key"]
_LLM_MODEL = CONFIG["llm_model_name"]


def _is_local_llm(url: str) -> bool:
    return url.startswith("http://127.0.0.1:") or url.startswith("http://localhost:")


def _llm_ok() -> bool:
    try:
        base = _LLM_BASE_URL.rstrip("/")
        if _is_local_llm(_LLM_BASE_URL):
            # 本地 vLLM
            plain_base = base[:-3] if base.endswith("/v1") else base
            for path in ("/health", "/healthz", "/"):
                url = plain_base.rstrip("/") + path
                # 修改点 1: 添加 verify=False
                r = requests.get(url, timeout=2, verify=False)
                if r.status_code == 200:
                    return True
            return False

        # 远程（DashScope/OpenAI 兼容）
        headers = {}
        if _LLM_API_KEY:
            headers["Authorization"] = f"Bearer {_LLM_API_KEY}"

        try:
            # 修改点 2: 添加 verify=False
            r = requests.get(base + "/models", headers=headers, timeout=3, verify=False)
            if r.ok:
                return True
        except Exception:
            pass

        payload = {
            "model": _LLM_MODEL,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "temperature": 0,
        }
        # 修改点 3: 添加 verify=False
        r = requests.post(
            base + "/chat/completions",
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
            timeout=5,
            verify=False  # <--- 关键修改：关闭 SSL 验证
        )
        return r.ok
    except Exception as e:
        # 调试建议：如果还是失败，可以在这里 print(e) 看看具体报错
        print(f"DEBUG: _llm_ok check failed: {e}", file=sys.stderr)
        return False


# -------- 后台建库（失败不再直接退出，错误通过 health 暴露） --------
def _build_index_background() -> None:
    global _index_ready, _index_error, _index_cost_ms, _fatal_error
    t0 = time.time()
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(WORK_DIR, exist_ok=True)
        ENGINE.build_index(DATA_DIR, WORK_DIR)
        _index_ready = True
        _index_error = None
        # 成功建库则清理之前可能残留的 fatal_error
        _fatal_error = None
    except Exception as e:
        _index_ready = False
        _index_error = f"{e}"
        _fatal_error = f"{e}"
        print("[indexing] failed:", e, file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        # 不再直接退出进程：由 /ready 与 /healthz 暴露错误，让 evaluator 自行决定是否结束 Job
    finally:
        _index_cost_ms = int((time.time() - t0) * 1000)


threading.Thread(target=_build_index_background, daemon=True).start()


# -------- 路由 --------
@app.get("/healthz")
def healthz():
    """
    总体健康：
    - 默认 healthy
    - 若建库失败（存在 index_error 且 index 未 ready）或运行期记录了 fatal_error，则为 error
    """
    status = "healthy"
    if _fatal_error is not None or (_index_error is not None and not _index_ready):
        status = "error"
    return jsonify(
        {
            "status": status,
            "index_ready": _index_ready,
            "index_error": _index_error,
            "fatal_error": _fatal_error,
        }
    ), 200


@app.get("/ready")
def ready():
    """
    evaluator.py 需要: status=ready 且 index_ready=True 且 llm_ok=True
    这里额外在 status = "error" 时，把 fatal_error / index_error 暴露出来，
    让 _algo_ready_or_raise 能立刻失败，而不是傻等。
    """
    llm_ok = _llm_ok()
    status = "ready" if _index_ready and llm_ok and _fatal_error is None else "initializing"
    if _fatal_error is not None or (_index_error is not None and not _index_ready):
        status = "error"

    return jsonify(
        {
            "status": status,
            "index_ready": _index_ready,
            "index_error": _index_error,
            "llm_ok": llm_ok,
            "fatal_error": _fatal_error,
        }
    ), 200


@app.get("/stats")
def stats():
    with _lock:
        return jsonify(
            {
                "index_build_time_ms": _index_cost_ms,
                "total_questions": _total_questions,
            }
        ), 200


@app.post("/qa")
def qa():
    global _total_questions, _fatal_error

    if not _index_ready:
        return jsonify({"msg": "Index not ready", "index_error": _index_error}), 503

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"msg": "missing 'question'"}), 400

    t0 = time.time()
    try:
        result = ENGINE.query(question)  # 约定：返回 dict
        elapsed = int((time.time() - t0) * 1000)

        # 构造 evaluator/raGas 期望的 payload
        payload = {
            "answer": result.get("answer", ""),
            "elapsed_ms": elapsed,
            "used_tokens": int(result.get("used_tokens", 0)),
            "retrieved": int(result.get("retrieved", 0)),
            "contexts": result.get("contexts", []),  # 关键：RAGAS 需要文本列表
        }

        with _lock:
            _total_questions += 1

        return jsonify(payload), 200

    except Exception as e:
        # 出现异常时不再直接退出，而是记录为 fatal_error，通过 /healthz & /ready 暴露：
        _fatal_error = str(e)
        print("[qa] failed:", e, file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        # EXIT_ON_QA_ERROR 已废弃为“退出信号”，仅保留环境变量以兼容旧配置
        # if EXIT_ON_QA_ERROR:
        #     os._exit(6)
        return jsonify({"msg": "query failed", "error": str(e)[:800]}), 500


if __name__ == "__main__":
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", "5000"))
    print(f"[startup] binding {host}:{port}")
    print(f"[startup] data_dir={DATA_DIR}  work_dir={WORK_DIR}")
    print(f"[startup] DOCUMENT_LOADING_ENGINE={CONFIG['document_loading_engine']}")
    print(
        f"[startup] RAG: chunk={CONFIG['chunk_size_tokens']} "
        f"overlap={CONFIG['chunk_overlap_tokens']} "
        f"topk={CONFIG['retrieve_top_k']} "
        f"context_cap={CONFIG['context_max_tokens']}"
    )
    app.run(host=host, port=port, debug=False)


