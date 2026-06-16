# agent/runner.py
# ============================================================
# Agent Runner v3.7 (Stable)
# - Fixed: ImportError 'AgentMemory'
# - Fixed: AttributeError 'RunContext' has no attribute 'agent'
# - Feature: EnvPreset & History Search (via Closures)
# ============================================================

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, List

from flask import Response, jsonify, request, stream_with_context
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

import httpx
import urllib3

# 🟢 [Fix] Only import Agent, removed AgentMemory
from agno.agent import Agent
from agno.run import RunContext
from agno.db.sqlite import SqliteDb
from agno.db.postgres import PostgresDb
from agno.models.deepseek import DeepSeek

# Import existing tools
from .tools import (
    AgentDeps,
    note_add, note_list, note_update, note_done, note_delete, note_clear,
    list_user, search_user, search_user_entry,
    search_ladder, read_ladder_info, search_ladder_entry,
    run_task, list_curr_task, get_task, cancel_task, get_task_logs, queue_status,
    wait, bash,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ─── System Prompt ─────────────────────────────────────────
SYSTEM_PROMPT = """\
你是「榜单评测平台」的 AI 运维助手。

## 平台概念
- **Leaderboard（榜单）**：creator 创建，含 evaluator/baseline 镜像。
- **Submission（提交/任务）**：参赛者提交算法镜像评测，生成分数。
- **EnvPreset（环境预设）**：用户保存的常用环境变量配置模板。

## 你的能力
1. **任务管理**：提交评测、取消任务、查看日志/状态 (Submitted/Running/Failed)。
2. **信息查询**：搜索榜单、用户、历史提交记录。
3. **配置助手**：
   - 帮用户查询保存的「环境预设」(EnvPreset)。
   - 帮用户回忆某个镜像在过去提交时使用了什么环境变量。
4. **备忘录**：管理用户待办事项。
5. **系统命令**：bash（受限）。

## 交互原则
- 结果导向：优先给出结论摘要（如"找到 3 个预设"），再列出详情。
- 智能截断：如果日志或配置过长，仅展示关键部分。
- 解释配置：当用户询问环境配置时，尝试解释其中的关键参数（如果能看懂key的话）。
- 任务提交前总是需要跟用户确认环境变量配置和任务名字，用户确认后再进行。
- 任务名遵循: 镜像-chunkType-ocrType-reranker-topk之类的，可以参考历史提交。
"""

# ─── 快捷指令 ──────────────────────────────────────────────
SHORTCUT_COMMANDS = [
    {"id": "my_running",   "label": "🏃 运行中任务", "icon": "🏃", "message": "查看当前 Running 和 Pending 的任务"},
    {"id": "my_presets",   "label": "💾 我的预设",   "icon": "💾", "message": "列出我保存的环境变量预设"},
    {"id": "recent_sub",   "label": "📋 最近提交",   "icon": "📋", "message": "列出我最近 5 次提交记录"},
    {"id": "queue_info",   "label": "📡 队列概况",   "icon": "📡", "message": "查看当前集群队列状况"},
]


# ─── 辅助函数：截断与摘要 ──────────────────────────────────
def _truncate_tool_result(func_name: str, result: str) -> str:
    MAX_LEN = 1500
    try:
        data = json.loads(result)
    except Exception:
        return (result[:MAX_LEN] + "\n...[truncated]") if len(result) > MAX_LEN else result

    if not isinstance(data, dict):
        s = json.dumps(data, ensure_ascii=False)
        return s[:MAX_LEN] if len(s) > MAX_LEN else s

    # 针对日志和长文本字段进行截断
    for key in ("evaluator_log", "algorithm_log", "stdout", "stderr", "env_json"):
        if key in data and isinstance(data[key], str) and len(data[key]) > 400:
            data[key] = "...[truncated]..." + data[key][-400:]

    # 针对列表进行截断
    for key in ("tasks", "entries", "users", "todos", "details", "submissions", "presets", "history"):
        if key in data and isinstance(data[key], list) and len(data[key]) > 5:
            total = len(data[key])
            data[key] = data[key][:5]
            data[f"_{key}_total"] = total

    s = json.dumps(data, ensure_ascii=False)
    return (s[:MAX_LEN] + "\n...[truncated]") if len(s) > MAX_LEN else s


def _extract_tool_summary(func_name: str, result: str) -> str:
    try:
        data = json.loads(result)
    except Exception:
        return ""
    if not isinstance(data, dict): return ""
    if not data.get("ok", True): return f"失败: {str(data.get('error', ''))[:50]}"

    # 通用列表摘要
    for key, unit in [
        ("tasks", "任务"), ("entries", "记录"), ("users", "用户"),
        ("todos", "备忘"), ("presets", "预设"), ("history", "历史记录")
    ]:
        if key in data and isinstance(data[key], list):
            return f"找到 {len(data[key])} 条{unit}"

    if "submission_id" in data: return f"提交 #{data['submission_id']}"
    if "preset_id" in data: return f"预设 #{data['preset_id']}"
    return "完成"


# ─── DB models ─────────────────────────────────────────────
_AGENT_MODELS = None

def init_agent_models(db):
    global _AGENT_MODELS
    if _AGENT_MODELS is not None: return _AGENT_MODELS

    class AgentProfile(db.Model):
        __tablename__ = "agent_profiles"
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, nullable=False, unique=True, index=True)
        agent_session_id = db.Column(db.String(64), nullable=False, index=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    class AgentEvent(db.Model):
        __tablename__ = "agent_events"
        id = db.Column(db.Integer, primary_key=True)
        agent_session_id = db.Column(db.String(64), nullable=False, index=True)
        seq = db.Column(db.Integer, nullable=False, index=True)
        event_type = db.Column(db.String(64), nullable=False)
        payload_json = db.Column(db.Text, nullable=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        __table_args__ = (db.UniqueConstraint("agent_session_id", "seq", name="_agent_sess_seq_uc"),)

    class AgentTodo(db.Model):
        __tablename__ = "agent_todos"
        id = db.Column(db.Integer, primary_key=True)
        agent_session_id = db.Column(db.String(64), nullable=False, index=True)
        title = db.Column(db.String(200), nullable=False)
        detail = db.Column(db.Text, nullable=True)
        status = db.Column(db.String(16), nullable=False, default="todo")
        priority = db.Column(db.Integer, nullable=False, default=2)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    _AGENT_MODELS = (AgentProfile, AgentEvent, AgentTodo)
    return _AGENT_MODELS


def make_agno_db(database_url: str):
    agno_db_url = os.environ.get("AGNO_DB_URL") or database_url or ""
    if os.environ.get("AGNO_DB_FILE"):
        return SqliteDb(db_file=os.environ["AGNO_DB_FILE"])
    if agno_db_url.startswith("postgres"):
        return PostgresDb(db_url=agno_db_url)
    return SqliteDb(db_file="agno_agent.db")


def _build_llm_httpx_client() -> httpx.Client:
    proxy = (os.environ.get("AGENT_LLM_PROXY") or "").strip() or None
    verify = os.environ.get("AGENT_LLM_SSL_VERIFY", "true").lower() == "true"
    kw = dict(verify=verify, trust_env=False)
    if proxy: return httpx.Client(proxy=proxy, **kw)
    return httpx.Client(**kw)


def _get_agent_llm_api_key() -> Optional[str]:
    return (os.environ.get("AGENT_LLM_API_KEY") or os.environ.get("LLM_API_KEY") or os.environ.get("DASHSCOPE_API_KEY"))


@dataclass
class BackendRefs:
    flask_app: Any; db: Any
    User: Any; Leaderboard: Any; Submission: Any; SubmissionLog: Any
    EnvPreset: Any  # <--- EnvPreset Model
    k8s_core_v1: Any; k8s_batch_v1: Any; K8S_NAMESPACE: str
    sync_submission_status: Any; persist_submission_logs: Any; start_k8s_job: Any
    database_url: str


# ============================================================
# AgentRunner Class
# ============================================================
class AgentRunner:
    def __init__(self, refs: BackendRefs):
        self.refs = refs
        AgentProfile, AgentEvent, AgentTodo = init_agent_models(refs.db)
        self.AgentProfile = AgentProfile
        self.AgentEvent = AgentEvent
        self.AgentTodo = AgentTodo
        self.agno_db = make_agno_db(refs.database_url)
        self._active_runs: Dict[str, threading.Event] = {}
        self._active_lock = threading.Lock()

    def get_or_create_session_id(self, user_id: int) -> str:
        with self.refs.flask_app.app_context():
            row = self.AgentProfile.query.filter_by(user_id=int(user_id)).first()
            if row:
                row.updated_at = datetime.utcnow()
                self.refs.db.session.commit()
                return row.agent_session_id
            sid = uuid.uuid4().hex
            row = self.AgentProfile(user_id=int(user_id), agent_session_id=sid)
            self.refs.db.session.add(row)
            self.refs.db.session.commit()
            return sid

    def reset_session(self, user_id: int) -> str:
        with self.refs.flask_app.app_context():
            row = self.AgentProfile.query.filter_by(user_id=int(user_id)).first()
            new_sid = uuid.uuid4().hex
            if row:
                old = row.agent_session_id
                row.agent_session_id = new_sid
                row.updated_at = datetime.utcnow()
                self.AgentEvent.query.filter_by(agent_session_id=old).delete()
                self.AgentTodo.query.filter_by(agent_session_id=old).delete()
                self.refs.db.session.commit()
            else:
                row = self.AgentProfile(user_id=int(user_id), agent_session_id=new_sid)
                self.refs.db.session.add(row)
                self.refs.db.session.commit()
            return new_sid

    def cancel_run(self, run_id: str) -> bool:
        with self._active_lock:
            flag = self._active_runs.get(run_id)
            if not flag: return False
            flag.set()
            return True

    @staticmethod
    def _make_tool_hook(event_queue: queue.Queue):
        def hook(run_context: RunContext, function_name: str, function_call, arguments: Dict[str, Any]):
            args_brief = {}
            for k, v in (arguments or {}).items():
                sv = str(v)
                args_brief[k] = (sv[:60] + "…") if len(sv) > 60 else sv

            # Send tool_start event
            event_queue.put({"type": "tool_start", "name": function_name, "args": args_brief})
            try:
                result = function_call(**arguments)
                summary = _extract_tool_summary(function_name, result)
                # Send tool_end event
                event_queue.put({"type": "tool_end", "name": function_name, "summary": summary})
                return _truncate_tool_result(function_name, result)
            except Exception as e:
                event_queue.put({"type": "tool_error", "name": function_name, "error": str(e)[:200]})
                raise
        return hook

    # ★★★ Core: Synchronous SSE Stream ★★★
    def run_chat_stream(self, user_id: int, role: str, session_id: str, message: str):
        eq = queue.Queue()
        cancel_flag = threading.Event()
        run_id = uuid.uuid4().hex

        def _worker():
            with self.refs.flask_app.app_context():
                def emit_noop(*args): pass

                # 🟢 [Closure] Capture dependencies for inner functions
                EnvPreset = self.refs.EnvPreset
                Submission = self.refs.Submission
                current_user_id = int(user_id)

                # ==================================================
                # 🟢 [Feature] Inner Tools (Access via Closure)
                # ==================================================
                def tool_list_presets(ctx: RunContext) -> str:
                    """查看当前用户保存的环境变量预设(Env Presets)。返回ID、名称、镜像、默认状态及完整配置。"""
                    try:
                        # Direct access via closure
                        presets = EnvPreset.query.filter_by(user_id=current_user_id) \
                            .order_by(EnvPreset.is_default.desc(), EnvPreset.updated_at.desc()) \
                            .all()

                        if not presets: return json.dumps({"ok": True, "presets": [], "msg": "无预设"})

                        res_list = []
                        for p in presets:
                            # 🟢 [修改] 尝试解析 JSON，返回完整字典而不是截断的字符串
                            env_data = {}
                            try:
                                if p.env_json:
                                    env_data = json.loads(p.env_json)
                            except:
                                env_data = str(p.env_json)  # 如果库里存的不是标准JSON，回退到字符串

                            res_list.append({
                                "id": p.id,
                                "name": p.name,
                                "image": p.image_url,
                                "is_default": p.is_default,
                                "env": env_data  # 这里传对象，最终 json.dumps 会自动处理
                            })
                        return json.dumps({"ok": True, "presets": res_list}, ensure_ascii=False)
                    except Exception as e:
                        return json.dumps({"ok": False, "error": str(e)})

                def tool_search_env_history(ctx: RunContext, image_url: str = "") -> str:
                    """
                    查询指定镜像(Image)的历史提交记录中使用的环境变量。
                    :param image_url: 镜像名称关键词，如果为空则查最近所有。
                    """
                    try:
                        # Direct access via closure
                        q = Submission.query.filter_by(user_id=current_user_id).filter(Submission.algo_env_json.isnot(None))
                        if image_url:
                            q = q.filter(Submission.algorithm_image_url.ilike(f"%{image_url}%"))

                        subs = q.order_by(Submission.submitted_at.desc()).limit(5).all()

                        if not subs: return json.dumps({"ok": True, "history": [], "msg": "未找到相关记录"})

                        res_list = []
                        for s in subs:
                            res_list.append({
                                "submission_id": s.id,
                                "status": s.status,
                                "image": s.algorithm_image_url,
                                "score": s.score,
                                "env_json": s.algo_env_json,
                                "time": s.submitted_at.isoformat() if s.submitted_at else ""
                            })
                        return json.dumps({"ok": True, "history": res_list}, ensure_ascii=False)
                    except Exception as e:
                        return json.dumps({"ok": False, "error": str(e)})

                # ==================================================

                deps = AgentDeps(
                    flask_app=self.refs.flask_app, db=self.refs.db,
                    User=self.refs.User, Leaderboard=self.refs.Leaderboard,
                    Submission=self.refs.Submission, SubmissionLog=self.refs.SubmissionLog,
                    k8s_core_v1=self.refs.k8s_core_v1, k8s_batch_v1=self.refs.k8s_batch_v1,
                    K8S_NAMESPACE=self.refs.K8S_NAMESPACE,
                    sync_submission_status=self.refs.sync_submission_status,
                    persist_submission_logs=self.refs.persist_submission_logs,
                    start_k8s_job=self.refs.start_k8s_job,
                    user_id=int(user_id), role=str(role),
                    agent_session_id=session_id,
                    emit_event=emit_noop, cancel_flag=cancel_flag,
                )

                model_id = os.environ.get("AGENT_LLM_MODEL", "deepseek-v3")
                base_url = os.environ.get("AGENT_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
                api_key = _get_agent_llm_api_key()

                if not api_key:
                    eq.put({"type": "error", "content": "API Key Missing"})
                    eq.put({"type": "done", "ok": False})
                    return

                model = DeepSeek(id=model_id, api_key=api_key, base_url=base_url, http_client=_build_llm_httpx_client())

                role_desc = {"admin": "管理员", "creator": "出题者", "participant": "参赛者"}.get(role, role)
                ctx_prompt = (
                    f"{SYSTEM_PROMPT}\n\n"
                    f"## 当前会话\n- 用户 ID：{user_id}，角色：{role}（{role_desc}）\n"
                )

                # 🟢 Register inner tools
                agent_tools = [
                    note_add, note_list, note_update, note_done, note_delete, note_clear,
                    list_user, search_user, search_user_entry,
                    search_ladder, read_ladder_info, search_ladder_entry,
                    run_task, list_curr_task, get_task, cancel_task, get_task_logs,
                    queue_status, wait, bash,
                    # New tools
                    tool_list_presets, tool_search_env_history
                ]

                # Initialize Agent
                agent = Agent(
                    name="leaderboard-agent",
                    description="榜单评测平台 AI 助手",
                    model=model,
                    # [Fix] Use instructions instead of system_prompt
                    instructions=[ctx_prompt],
                    db=self.agno_db,
                    session_id=session_id,
                    user_id=str(user_id),
                    add_history_to_context=True,
                    num_history_runs=6,
                    tool_hooks=[AgentRunner._make_tool_hook(eq)],
                    tools=agent_tools,
                    # [Clean] Removed 'EnvPreset' from here as tools use closure now
                    dependencies={
                        "deps": deps,
                        "AgentTodo": self.AgentTodo
                    },
                    markdown=True,
                )

                try:
                    # Stream logic
                    try:
                        stream = agent.run(message, stream=True)
                        buf = ""
                        for chunk in stream:
                            if cancel_flag.is_set():
                                eq.put({"type": "error", "content": "已取消"})
                                break

                            c = getattr(chunk, "content", None) or ""
                            if c:
                                buf += c
                                # Batch chunks to reduce SSE overhead
                                if len(buf) >= 10 or "\n" in c:
                                    eq.put({"type": "text_delta", "content": buf})
                                    buf = ""
                        if buf:
                            eq.put({"type": "text_delta", "content": buf})
                        eq.put({"type": "done", "ok": True})

                    except (TypeError, AttributeError):
                        # Fallback for non-streaming response
                        resp = agent.run(message)
                        text = (getattr(resp, "content", None) or getattr(resp, "output", None) or str(resp))
                        eq.put({"type": "answer", "content": str(text)})
                        eq.put({"type": "done", "ok": True})

                except Exception as e:
                    eq.put({"type": "error", "content": f"{type(e).__name__}: {str(e)[:300]}"})
                    eq.put({"type": "done", "ok": False})

        with self._active_lock:
            self._active_runs[run_id] = cancel_flag

        th = threading.Thread(target=_worker, daemon=True)
        th.start()

        # SSE Generator Loop
        yield f"data: {json.dumps({'type': 'run_start', 'run_id': run_id}, ensure_ascii=False)}\n\n"

        while True:
            try:
                event = eq.get(timeout=0.3)
            except queue.Empty:
                if not th.is_alive():
                    yield f"data: {json.dumps({'type': 'done', 'ok': False})}\n\n"
                    break
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
                continue

            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") == "done":
                break

        with self._active_lock:
            self._active_runs.pop(run_id, None)


# ============================================================
# Routes
# ============================================================
def init_agent_routes(
    flask_app, db, *,
    User, Leaderboard, Submission, SubmissionLog, EnvPreset, # <--- EnvPreset required
    k8s_core_v1, k8s_batch_v1, K8S_NAMESPACE: str,
    sync_submission_status, persist_submission_logs, start_k8s_job,
    database_url: str,
):
    refs = BackendRefs(
        flask_app=flask_app, db=db,
        User=User, Leaderboard=Leaderboard,
        Submission=Submission, SubmissionLog=SubmissionLog, EnvPreset=EnvPreset, # <--- Pass here
        k8s_core_v1=k8s_core_v1, k8s_batch_v1=k8s_batch_v1,
        K8S_NAMESPACE=K8S_NAMESPACE,
        sync_submission_status=sync_submission_status,
        persist_submission_logs=persist_submission_logs,
        start_k8s_job=start_k8s_job, database_url=database_url,
    )
    runner = AgentRunner(refs)

    with flask_app.app_context():
        init_agent_models(db)
        db.create_all()

    @flask_app.route("/api/agent/session", methods=["GET"])
    @jwt_required()
    def api_agent_get_session():
        uid = int(get_jwt_identity())
        sid = runner.get_or_create_session_id(uid)
        return jsonify({"ok": True, "agent_session_id": sid})

    @flask_app.route("/api/agent/session/reset", methods=["POST"])
    @jwt_required()
    def api_agent_reset_session():
        uid = int(get_jwt_identity())
        sid = runner.reset_session(uid)
        return jsonify({"ok": True, "agent_session_id": sid})

    @flask_app.route("/api/agent/cancel", methods=["POST"])
    @jwt_required()
    def api_agent_cancel():
        payload = request.get_json(silent=True) or {}
        run_id = str(payload.get("run_id", "")).strip()
        if not run_id: return jsonify({"ok": False, "error": "missing run_id"}), 400
        return jsonify({"ok": True, "cancelled": runner.cancel_run(run_id)})

    @flask_app.route("/api/agent/chat", methods=["POST"])
    @jwt_required()
    def api_agent_chat():
        uid = int(get_jwt_identity())
        role = (get_jwt() or {}).get("role", "participant")
        payload = request.get_json(silent=True) or {}
        sid = payload.get("agent_session_id") or payload.get("session_id") or runner.get_or_create_session_id(uid)
        msg = str(payload.get("message", "")).strip()

        if not msg: return jsonify({"ok": False, "error": "missing message"}), 400

        resp = Response(
            stream_with_context(runner.run_chat_stream(uid, role, sid, msg)),
            mimetype="text/event-stream",
        )
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"
        return resp

    @flask_app.route("/api/agent/shortcuts", methods=["GET"])
    @jwt_required()
    def api_agent_shortcuts():
        role = (get_jwt() or {}).get("role", "participant")
        commands = [c for c in SHORTCUT_COMMANDS if c.get("category") != "admin_only" or role == "admin"]
        return jsonify({"ok": True, "shortcuts": commands})

    return runner

