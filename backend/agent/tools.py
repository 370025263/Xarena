# agent/tools.py
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from agno.tools import tool
from agno.run import RunContext


# -------------------------
# 依赖注入对象（由 runner 填充）
# -------------------------
@dataclass
class AgentDeps:
    # Flask app（用于 app_context）
    flask_app: Any

    # SQLAlchemy（Flask-SQLAlchemy）
    db: Any

    # 模型类（来自 app.py 注入，避免 circular import）
    User: Any
    Leaderboard: Any
    Submission: Any
    SubmissionLog: Any

    # 外部能力（K8s + 复用你 app.py 的 helper）
    k8s_core_v1: Any
    k8s_batch_v1: Any
    K8S_NAMESPACE: str

    sync_submission_status: Any  # _sync_submission_status(sub) -> status
    persist_submission_logs: Any  # _persist_submission_logs(sub) -> bool
    start_k8s_job: Any           # _start_k8s_job(sub, extra_env=...) -> job_name

    # 当前调用者身份（由 runner 每次 run 填）
    user_id: int
    role: str

    # agent 会话（agno session_id）
    agent_session_id: str

    # event emitter（runner 提供）
    emit_event: Any

    # 取消标志（runner 提供 threading.Event）
    cancel_flag: Any


# -------------------------
# 小工具：权限与可见性
# -------------------------
def _assert_role(deps: AgentDeps, allowed: Tuple[str, ...]) -> None:
    if deps.role not in allowed:
        raise ValueError(f"Forbidden: role={deps.role}, allowed={allowed}")


def _visible_submission_query(deps: AgentDeps):
    # 可见性规则（可按你实际想法调整）：
    # admin: 全部
    # creator: 自己提交 + 自己作为 owner 的榜单下的所有提交
    # participant: 仅自己提交
    db = deps.db
    Submission = deps.Submission
    Leaderboard = deps.Leaderboard

    if deps.role == "admin":
        return Submission.query

    if deps.role == "creator":
        # creator：允许看自己榜单下的所有提交（便于“管理任务生命周期”），以及自己提交
        return Submission.query.join(Leaderboard, Leaderboard.id == Submission.leaderboard_id).filter(
            (Submission.user_id == deps.user_id) | (Leaderboard.owner_id == deps.user_id)
        )

    return Submission.query.filter(Submission.user_id == deps.user_id)


def _visible_leaderboard_query(deps: AgentDeps):
    # 这里榜单本身默认可读（你现有 public 接口也是可读）
    return deps.Leaderboard.query


def _json_load_maybe(s: Optional[str]):
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _sanitize_env(env: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in (env or {}).items():
        if k is None:
            continue
        kk = str(k).strip()
        if not kk:
            continue
        # 只保留合理 key
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", kk):
            continue
        vv = "" if v is None else str(v)
        out[kk] = vv
    return out


def _check_cancel(deps: AgentDeps):
    if deps.cancel_flag is not None and deps.cancel_flag.is_set():
        raise RuntimeError("Cancelled")


# -------------------------
# note / todo 工具
# -------------------------
@tool
def note_add(title: str, detail: str = "", priority: int = 2, run_context: RunContext | None = None) -> str:
    """Add a todo item into agent notes (persisted in DB)."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        Todo = run_context.dependencies["AgentTodo"]
        todo = Todo(
            agent_session_id=deps.agent_session_id,
            title=str(title).strip(),
            detail=str(detail or "").strip(),
            status="todo",
            priority=int(priority or 2),
        )
        deps.db.session.add(todo)
        deps.db.session.commit()

        deps.emit_event(deps.agent_session_id, "note.add", {"todo_id": todo.id, "title": todo.title})
        return json.dumps({"ok": True, "todo_id": todo.id, "title": todo.title}, ensure_ascii=False)


@tool
def note_list(status: str | None = None, run_context: RunContext | None = None) -> str:
    """List todo items."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        Todo = run_context.dependencies["AgentTodo"]
        q = Todo.query.filter_by(agent_session_id=deps.agent_session_id)
        if status:
            q = q.filter(Todo.status == status)
        rows = q.order_by(Todo.status.asc(), Todo.priority.desc(), Todo.updated_at.desc()).limit(200).all()
        data = [
            {"id": r.id, "title": r.title, "detail": r.detail, "status": r.status, "priority": r.priority,
             "created_at": r.created_at.isoformat(), "updated_at": r.updated_at.isoformat()}
            for r in rows
        ]
        return json.dumps({"ok": True, "todos": data}, ensure_ascii=False)


@tool
def note_update(
    todo_id: int,
    title: str | None = None,
    detail: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    run_context: RunContext | None = None,
) -> str:
    """Update a todo item."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        Todo = run_context.dependencies["AgentTodo"]
        row = Todo.query.filter_by(agent_session_id=deps.agent_session_id, id=int(todo_id)).first()
        if not row:
            return json.dumps({"ok": False, "error": "todo not found"}, ensure_ascii=False)

        if title is not None:
            row.title = str(title).strip()
        if detail is not None:
            row.detail = str(detail).strip()
        if status is not None:
            row.status = str(status).strip()
        if priority is not None:
            row.priority = int(priority)
        row.updated_at = datetime.utcnow()
        deps.db.session.commit()

        deps.emit_event(deps.agent_session_id, "note.update", {"todo_id": row.id})
        return json.dumps({"ok": True, "todo_id": row.id}, ensure_ascii=False)


@tool
def note_done(todo_id: int, run_context: RunContext | None = None) -> str:
    """Mark a todo as done."""
    return note_update(todo_id=todo_id, status="done", run_context=run_context)


@tool
def note_delete(todo_id: int, run_context: RunContext | None = None) -> str:
    """Delete a todo."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        Todo = run_context.dependencies["AgentTodo"]
        row = Todo.query.filter_by(agent_session_id=deps.agent_session_id, id=int(todo_id)).first()
        if not row:
            return json.dumps({"ok": False, "error": "todo not found"}, ensure_ascii=False)
        deps.db.session.delete(row)
        deps.db.session.commit()
        deps.emit_event(deps.agent_session_id, "note.delete", {"todo_id": int(todo_id)})
        return json.dumps({"ok": True}, ensure_ascii=False)


@tool
def note_clear(done_only: bool = True, run_context: RunContext | None = None) -> str:
    """Clear todos. If done_only=True, only delete done ones."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        Todo = run_context.dependencies["AgentTodo"]
        q = Todo.query.filter_by(agent_session_id=deps.agent_session_id)
        if done_only:
            q = q.filter(Todo.status == "done")
        n = q.delete(synchronize_session=False)
        deps.db.session.commit()
        deps.emit_event(deps.agent_session_id, "note.clear", {"deleted": int(n)})
        return json.dumps({"ok": True, "deleted": int(n)}, ensure_ascii=False)


# -------------------------
# user 工具
# -------------------------
@tool
def list_user(limit: int = 50, run_context: RunContext | None = None) -> str:
    """List users (admin-only)."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _assert_role(deps, ("admin",))
    _check_cancel(deps)

    with deps.flask_app.app_context():
        rows = deps.User.query.order_by(deps.User.id.asc()).limit(int(limit or 50)).all()
        data = [{"id": u.id, "username": u.username, "role": u.role, "created_at": u.created_at.isoformat()} for u in rows]
        return json.dumps({"ok": True, "users": data}, ensure_ascii=False)


@tool
def search_user(keyword: str, limit: int = 50, run_context: RunContext | None = None) -> str:
    """Search users by username keyword."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _assert_role(deps, ("admin", "creator"))
    _check_cancel(deps)

    kw = f"%{str(keyword).strip()}%"
    with deps.flask_app.app_context():
        rows = deps.User.query.filter(deps.User.username.like(kw)).order_by(deps.User.username.asc()).limit(int(limit or 50)).all()
        data = [{"id": u.id, "username": u.username, "role": u.role} for u in rows]
        return json.dumps({"ok": True, "users": data}, ensure_ascii=False)


@tool
def search_user_entry(user_name: str, num: int = 10, run_context: RunContext | None = None) -> str:
    """Return recent submissions of a user (status/score/metrics if any), filtered by visibility."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        user = deps.User.query.filter_by(username=str(user_name).strip()).first()
        if not user:
            return json.dumps({"ok": False, "error": "user not found"}, ensure_ascii=False)

        q = _visible_submission_query(deps).filter(deps.Submission.user_id == user.id)
        rows = q.order_by(deps.Submission.submitted_at.desc()).limit(int(num or 10)).all()

        out = []
        for s in rows:
            st = deps.sync_submission_status(s)
            out.append({
                "submission_id": s.id,
                "submission_name": s.submission_name,
                "leaderboard_id": s.leaderboard_id,
                "leaderboard_name": s.leaderboard.name if s.leaderboard else None,
                "status": st,
                "score": s.score,
                "metrics": _json_load_maybe(s.metrics_json),
                "job_name": s.k8s_job_name,
                "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None
            })
        return json.dumps({"ok": True, "user": user.username, "entries": out}, ensure_ascii=False)


# -------------------------
# ladder 工具
# -------------------------
@tool
def search_ladder(keywords: str, num: int = 10, run_context: RunContext | None = None) -> str:
    """Search leaderboards by keyword in name/description."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    kw = f"%{str(keywords).strip()}%"
    with deps.flask_app.app_context():
        q = _visible_leaderboard_query(deps).filter(
            (deps.Leaderboard.name.like(kw)) | (deps.Leaderboard.description.like(kw))
        )
        rows = q.order_by(deps.Leaderboard.name.asc()).limit(int(num or 10)).all()
        data = [{"id": b.id, "name": b.name, "version": b.version, "owner_id": b.owner_id} for b in rows]
        return json.dumps({"ok": True, "leaderboards": data}, ensure_ascii=False)


@tool
def read_ladder_info(ladder_id: int, run_context: RunContext | None = None) -> str:
    """Read leaderboard detail (read-only)."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        b = deps.Leaderboard.query.get(int(ladder_id))
        if not b:
            return json.dumps({"ok": False, "error": "leaderboard not found"}, ensure_ascii=False)

        required_keys = _json_load_maybe(b.required_algo_env_keys) or None
        data = {
            "id": b.id,
            "name": b.name,
            "version": b.version,
            "description": b.description,
            "difficulty_factor": b.difficulty_factor,
            "sota_score": b.sota_score,
            "required_algo_env_keys": required_keys,
            "owner_username": b.owner.username if b.owner else None,
            "evaluator_image": b.evaluator_image,
            "baseline_image": b.baseline_image,
        }
        return json.dumps({"ok": True, "leaderboard": data}, ensure_ascii=False)


@tool
def search_ladder_entry(
    ladder_id: int,
    user_name: str | None = None,
    status: str | None = None,
    num: int = 20,
    run_context: RunContext | None = None,
) -> str:
    """List submissions under a leaderboard, optionally filter by user/status (visibility-aware)."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        b = deps.Leaderboard.query.get(int(ladder_id))
        if not b:
            return json.dumps({"ok": False, "error": "leaderboard not found"}, ensure_ascii=False)

        q = _visible_submission_query(deps).filter(deps.Submission.leaderboard_id == b.id)

        if user_name:
            u = deps.User.query.filter_by(username=str(user_name).strip()).first()
            if not u:
                return json.dumps({"ok": False, "error": "user not found"}, ensure_ascii=False)
            q = q.filter(deps.Submission.user_id == u.id)

        if status:
            q = q.filter(deps.Submission.status == str(status).strip())

        rows = q.order_by(deps.Submission.submitted_at.desc()).limit(int(num or 20)).all()
        out = []
        for s in rows:
            st = deps.sync_submission_status(s)
            out.append({
                "submission_id": s.id,
                "submission_name": s.submission_name,
                "username": s.user.username if s.user else None,
                "status": st,
                "score": s.score,
                "metrics": _json_load_maybe(s.metrics_json),
                "job_name": s.k8s_job_name,
                "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None
            })
        return json.dumps({"ok": True, "leaderboard": {"id": b.id, "name": b.name}, "entries": out}, ensure_ascii=False)


# -------------------------
# task 工具（生命周期管理）
# -------------------------
@tool
def run_task(
    ladder_id: int,
    image_url: str,
    image_env: Dict[str, Any] | None = None,
    submission_name: str | None = None,
    run_context: RunContext | None = None,
) -> str:
    """Create a submission and start k8s job (agent-managed)."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    env = _sanitize_env(image_env or {})

    with deps.flask_app.app_context():
        b = deps.Leaderboard.query.get(int(ladder_id))
        if not b:
            return json.dumps({"ok": False, "error": "leaderboard not found"}, ensure_ascii=False)

        # required env keys 校验（只读榜单字段，不依赖额外 helper）
        required_keys = _json_load_maybe(b.required_algo_env_keys) or []
        missing = [k for k in required_keys if not str(env.get(k, "")).strip()]
        if missing:
            return json.dumps({"ok": False, "error": "missing required env", "missing_keys": missing}, ensure_ascii=False)

        # submission_name 自动生成
        if not submission_name:
            submission_name = f"agent-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"

        # 唯一性：同 user 下 submission_name 唯一
        exist = deps.Submission.query.filter_by(user_id=deps.user_id, submission_name=submission_name).first()
        if exist:
            submission_name = f"{submission_name}-{uuid.uuid4().hex[:4]}"

        sub = deps.Submission(
            submission_name=submission_name,
            algorithm_image_url=str(image_url).strip(),
            status="Submitted",
            user_id=deps.user_id,
            leaderboard_id=b.id,
            algo_env_json=json.dumps(env, ensure_ascii=False) if env else None,
            algo_env_grid_keys=None,
        )
        deps.db.session.add(sub)
        deps.db.session.flush()

        # 启动 job（复用你 app.py 的 _start_k8s_job）
        job_name = deps.start_k8s_job(sub, extra_env=env)
        deps.db.session.commit()

        deps.emit_event(deps.agent_session_id, "task.run", {"submission_id": sub.id, "job_name": job_name})
        return json.dumps({"ok": True, "submission_id": sub.id, "job_name": job_name, "submission_name": submission_name}, ensure_ascii=False)


@tool
def list_curr_task(
    statuses: Tuple[str, ...] = ("Submitted", "Pending", "Running"),
    num: int = 50,
    run_context: RunContext | None = None,
) -> str:
    """List current tasks by status (visibility-aware)."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        q = _visible_submission_query(deps).filter(deps.Submission.status.in_(tuple(statuses)))
        rows = q.order_by(deps.Submission.submitted_at.desc()).limit(int(num or 50)).all()

        out = []
        for s in rows:
            st = deps.sync_submission_status(s)
            out.append({
                "submission_id": s.id,
                "submission_name": s.submission_name,
                "leaderboard_id": s.leaderboard_id,
                "leaderboard_name": s.leaderboard.name if s.leaderboard else None,
                "status": st,
                "score": s.score,
                "job_name": s.k8s_job_name,
                "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None
            })
        return json.dumps({"ok": True, "tasks": out}, ensure_ascii=False)


@tool
def get_task(task_id: int, run_context: RunContext | None = None) -> str:
    """Get a task details."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        s = _visible_submission_query(deps).filter(deps.Submission.id == int(task_id)).first()
        if not s:
            return json.dumps({"ok": False, "error": "task not found"}, ensure_ascii=False)
        st = deps.sync_submission_status(s)
        return json.dumps({
            "ok": True,
            "task": {
                "submission_id": s.id,
                "submission_name": s.submission_name,
                "username": s.user.username if s.user else None,
                "leaderboard_id": s.leaderboard_id,
                "leaderboard_name": s.leaderboard.name if s.leaderboard else None,
                "status": st,
                "score": s.score,
                "metrics": _json_load_maybe(s.metrics_json),
                "job_name": s.k8s_job_name,
                "algo_env": _json_load_maybe(s.algo_env_json),
                "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
            }
        }, ensure_ascii=False)


@tool
def cancel_task(task_id: int, run_context: RunContext | None = None) -> str:
    """Cancel a task (owner/admin; creator can cancel within owned leaderboards)."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        s = _visible_submission_query(deps).filter(deps.Submission.id == int(task_id)).first()
        if not s:
            return json.dumps({"ok": False, "error": "task not found"}, ensure_ascii=False)

        st = deps.sync_submission_status(s)
        if st not in ("Pending", "Running", "Submitted"):
            return json.dumps({"ok": False, "error": f"cannot cancel status={st}"}, ensure_ascii=False)

        # 取消前尽力固化一次日志
        try:
            deps.persist_submission_logs(s)
        except Exception:
            pass

        if s.k8s_job_name and deps.k8s_batch_v1:
            try:
                from kubernetes import client
                deps.k8s_batch_v1.delete_namespaced_job(
                    name=s.k8s_job_name,
                    namespace=deps.K8S_NAMESPACE,
                    body=client.V1DeleteOptions(propagation_policy="Background"),
                )
            except Exception as e:
                return json.dumps({"ok": False, "error": "k8s delete job failed", "details": str(e)}, ensure_ascii=False)

        s.status = "Cancelled"
        deps.db.session.commit()

        deps.emit_event(deps.agent_session_id, "task.cancel", {"submission_id": s.id, "job_name": s.k8s_job_name})
        return json.dumps({"ok": True, "submission_id": s.id}, ensure_ascii=False)


@tool
def get_task_logs(task_id: int, run_context: RunContext | None = None) -> str:
    """Get evaluator & algorithm logs (persisted-first, fallback to k8s)."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        s = _visible_submission_query(deps).filter(deps.Submission.id == int(task_id)).first()
        if not s:
            return json.dumps({"ok": False, "error": "task not found"}, ensure_ascii=False)

        # 1) persisted log
        rec = deps.SubmissionLog.query.filter_by(submission_id=s.id).first()
        if rec and (rec.evaluator_log is not None or rec.algorithm_log is not None):
            return json.dumps({
                "ok": True,
                "submission_id": s.id,
                "evaluator_log": rec.evaluator_log or "",
                "algorithm_log": rec.algorithm_log or "",
                "source": "persisted"
            }, ensure_ascii=False)

        # 2) fallback to k8s (简化版：不重复你全部事件/容错逻辑)
        if not s.k8s_job_name:
            return json.dumps({"ok": False, "error": "job not started"}, ensure_ascii=False)
        if not deps.k8s_core_v1:
            return json.dumps({"ok": False, "error": "kubernetes client not available"}, ensure_ascii=False)

        try:
            pod_list = deps.k8s_core_v1.list_namespaced_pod(
                namespace=deps.K8S_NAMESPACE,
                label_selector=f"job-name={s.k8s_job_name}"
            )
            if not pod_list.items:
                return json.dumps({"ok": False, "error": "pod not found"}, ensure_ascii=False)

            pod_name = pod_list.items[0].metadata.name

            evaluator_log = ""
            algorithm_log = ""
            try:
                evaluator_log = deps.k8s_core_v1.read_namespaced_pod_log(
                    name=pod_name, namespace=deps.K8S_NAMESPACE, container="evaluator-container"
                )
            except Exception as e:
                evaluator_log = f"Error retrieving evaluator log: {e}"

            try:
                algorithm_log = deps.k8s_core_v1.read_namespaced_pod_log(
                    name=pod_name, namespace=deps.K8S_NAMESPACE, container="submitter-container"
                )
            except Exception as e:
                algorithm_log = f"Error retrieving algorithm log: {e}"

            # 若终态，顺便固化
            if s.status in ("Succeeded", "Failed", "Cancelled"):
                try:
                    deps.persist_submission_logs(s)
                except Exception:
                    pass

            return json.dumps({
                "ok": True,
                "submission_id": s.id,
                "evaluator_log": evaluator_log,
                "algorithm_log": algorithm_log,
                "source": "k8s"
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"ok": False, "error": "k8s log fetch failed", "details": str(e)}, ensure_ascii=False)


@tool
def queue_status(run_context: RunContext | None = None) -> str:
    """Queue status (pending/running pods)."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    if not deps.k8s_core_v1:
        return json.dumps({"ok": False, "error": "kubernetes client not available"}, ensure_ascii=False)

    try:
        pending = deps.k8s_core_v1.list_namespaced_pod(
            namespace=deps.K8S_NAMESPACE,
            label_selector="app=leaderboard-eval",
            field_selector="status.phase=Pending"
        )
        running = deps.k8s_core_v1.list_namespaced_pod(
            namespace=deps.K8S_NAMESPACE,
            label_selector="app=leaderboard-eval",
            field_selector="status.phase=Running"
        )
        return json.dumps({"ok": True, "pending_tasks": len(pending.items), "running_tasks": len(running.items)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": "k8s query failed", "details": str(e)}, ensure_ascii=False)


# -------------------------
# wait 工具（可持续运行）
# -------------------------
@tool
def wait(minutes: int = 1, run_context: RunContext | None = None) -> str:
    """Sleep for N minutes (emits events; cancellable)."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]

    mins = max(0, int(minutes or 0))
    deps.emit_event(deps.agent_session_id, "agent.wait", {"minutes": mins, "phase": "start"})
    for _ in range(mins * 60):
        _check_cancel(deps)
        time.sleep(1)
    deps.emit_event(deps.agent_session_id, "agent.wait", {"minutes": mins, "phase": "end"})
    return json.dumps({"ok": True, "slept_minutes": mins}, ensure_ascii=False)


# -------------------------
# bash 工具（默认关闭）
# -------------------------
def _bash_allowed(mode: str, cmd: str) -> bool:
    mode = (mode or "disabled").lower()
    if mode == "disabled":
        return False
    if mode == "full":
        return True

    # readonly：拒绝明显破坏性命令（可按需扩充）
    deny = [
        r"\brm\b", r"\bmv\b", r"\bcp\b", r"\bchmod\b", r"\bchown\b", r"\bmkfs\b", r"\bdd\b",
        r"\bshutdown\b", r"\breboot\b", r"\buseradd\b", r"\buserdel\b",
    ]
    if any(re.search(p, cmd) for p in deny):
        return False
    return True


@tool
def bash(cmd: str, timeout_sec: int = 20, run_context: RunContext | None = None) -> str:
    """Run a bash command. Controlled by env AGENT_BASH_MODE=disabled|readonly|full."""
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    mode = os.environ.get("AGENT_BASH_MODE", "disabled")
    command = str(cmd or "").strip()
    if not command:
        return json.dumps({"ok": False, "error": "empty cmd"}, ensure_ascii=False)

    if not _bash_allowed(mode, command):
        return json.dumps({"ok": False, "error": f"bash disabled or not allowed (mode={mode})"}, ensure_ascii=False)

    deps.emit_event(deps.agent_session_id, "bash.run", {"cmd": command})

    try:
        # bash -lc 保持常见环境行为，但仍设置超时
        proc = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            text=True,
            timeout=int(timeout_sec or 20),
        )
        return json.dumps({
            "ok": True,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[-8000:],
            "stderr": (proc.stderr or "")[-8000:],
        }, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        return json.dumps({"ok": False, "error": "timeout"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)




# ==========================
# agent/tools.py  —— 优化版（仅展示新增部分）
# ============================================================
# 改动点汇总：
#
# 【改动 A】在原文件末尾（L738 之后）追加以下 3 个复合工具函数：
#   - diagnose_task()    一键诊断
#   - batch_rerun_failed()  批量重跑
#   - compare_submissions() 对比分析
#
# 原有代码（L1-L738）完全不动，以下为纯追加内容。
# 复制到原 agent_tools.py 末尾即可。
# ============================================================

# ---- 以下追加到原 agent/tools.py 文件末尾 (L738 之后) ----


# -------------------------
# 【P1 复合工具】一键诊断
# -------------------------
@tool
def diagnose_task(task_id: int, run_context: RunContext | None = None) -> str:
    """
    One-click diagnosis: fetch task status + evaluator/algorithm logs + failure analysis.
    Combines get_task + get_task_logs into a single call with added analysis.
    Equivalent to manually calling get_task → get_task_logs → reading logs.
    """
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        # 1) 获取任务详情
        s = _visible_submission_query(deps).filter(deps.Submission.id == int(task_id)).first()
        if not s:
            return json.dumps({"ok": False, "error": "task not found or not visible"}, ensure_ascii=False)

        st = deps.sync_submission_status(s)

        task_info = {
            "submission_id": s.id,
            "submission_name": s.submission_name,
            "username": s.user.username if s.user else None,
            "leaderboard_id": s.leaderboard_id,
            "leaderboard_name": s.leaderboard.name if s.leaderboard else None,
            "status": st,
            "score": s.score,
            "metrics": _json_load_maybe(s.metrics_json),
            "job_name": s.k8s_job_name,
            "algo_image": s.algorithm_image_url,
            "algo_env": _json_load_maybe(s.algo_env_json),
            "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
        }

        # 2) 获取日志
        evaluator_log = ""
        algorithm_log = ""
        log_source = "none"

        rec = deps.SubmissionLog.query.filter_by(submission_id=s.id).first()
        if rec and (rec.evaluator_log is not None or rec.algorithm_log is not None):
            evaluator_log = rec.evaluator_log or ""
            algorithm_log = rec.algorithm_log or ""
            log_source = "persisted"
        elif s.k8s_job_name and deps.k8s_core_v1:
            try:
                pod_list = deps.k8s_core_v1.list_namespaced_pod(
                    namespace=deps.K8S_NAMESPACE,
                    label_selector=f"job-name={s.k8s_job_name}"
                )
                if pod_list.items:
                    pod_name = pod_list.items[0].metadata.name
                    try:
                        evaluator_log = deps.k8s_core_v1.read_namespaced_pod_log(
                            name=pod_name, namespace=deps.K8S_NAMESPACE, container="evaluator-container"
                        )
                    except Exception:
                        evaluator_log = "[evaluator log unavailable]"
                    try:
                        algorithm_log = deps.k8s_core_v1.read_namespaced_pod_log(
                            name=pod_name, namespace=deps.K8S_NAMESPACE, container="submitter-container"
                        )
                    except Exception:
                        algorithm_log = "[algorithm log unavailable]"
                    log_source = "k8s"
            except Exception:
                log_source = "error"

        # 3) 自动分析：从日志中提取常见失败模式
        diagnosis_hints = []
        combined_log = (evaluator_log + "\n" + algorithm_log).lower()

        if st == "Failed":
            # OOM
            if "oomkilled" in combined_log or "out of memory" in combined_log or "killed" in combined_log:
                diagnosis_hints.append("疑似 OOM（内存不足），建议减小 batch_size 或申请更大资源")
            # 超时
            if "timeout" in combined_log or "deadline exceeded" in combined_log:
                diagnosis_hints.append("疑似超时，检查算法效率或增加 timeout 配置")
            # 镜像拉取失败
            if "imagepullbackoff" in combined_log or "errimagepull" in combined_log:
                diagnosis_hints.append("镜像拉取失败，检查 image URL 和 registry 权限")
            # Python 异常
            if "traceback" in combined_log:
                diagnosis_hints.append("检测到 Python Traceback，查看 algorithm_log 末尾获取详细错误")
            if not diagnosis_hints:
                diagnosis_hints.append("未匹配到常见失败模式，请人工查看日志末尾")

        # 截断日志（避免 token 爆炸）
        MAX_LOG_CHARS = 3000
        evaluator_log_truncated = evaluator_log[-MAX_LOG_CHARS:] if len(evaluator_log) > MAX_LOG_CHARS else evaluator_log
        algorithm_log_truncated = algorithm_log[-MAX_LOG_CHARS:] if len(algorithm_log) > MAX_LOG_CHARS else algorithm_log

        deps.emit_event(deps.agent_session_id, "composite.diagnose", {
            "submission_id": s.id, "status": st, "hints": diagnosis_hints
        })

        return json.dumps({
            "ok": True,
            "task": task_info,
            "logs": {
                "evaluator_log": evaluator_log_truncated,
                "algorithm_log": algorithm_log_truncated,
                "source": log_source,
                "truncated": len(evaluator_log) > MAX_LOG_CHARS or len(algorithm_log) > MAX_LOG_CHARS,
            },
            "diagnosis": {
                "hints": diagnosis_hints,
                "is_failed": st == "Failed",
            },
        }, ensure_ascii=False)


# -------------------------
# 【P1 复合工具】批量重跑失败任务
# -------------------------
@tool
def batch_rerun_failed(
    ladder_id: int | None = None,
    limit: int = 10,
    run_context: RunContext | None = None,
) -> str:
    """
    Batch re-run recently failed submissions. Finds failed tasks (optionally filtered by ladder_id),
    and re-submits them with the same image and env config. Returns summary of rerun results.
    """
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        q = _visible_submission_query(deps).filter(deps.Submission.status == "Failed")
        if ladder_id:
            q = q.filter(deps.Submission.leaderboard_id == int(ladder_id))

        failed_subs = q.order_by(deps.Submission.submitted_at.desc()).limit(int(limit or 10)).all()

        if not failed_subs:
            return json.dumps({"ok": True, "message": "没有找到失败的任务", "rerun_count": 0}, ensure_ascii=False)

        results = []
        for s in failed_subs:
            _check_cancel(deps)
            try:
                # 复用原始参数重新提交
                env = _json_load_maybe(s.algo_env_json) or {}
                env = _sanitize_env(env)

                new_name = f"rerun-{s.submission_name}-{uuid.uuid4().hex[:4]}"
                new_sub = deps.Submission(
                    submission_name=new_name,
                    algorithm_image_url=s.algorithm_image_url,
                    status="Submitted",
                    user_id=deps.user_id,
                    leaderboard_id=s.leaderboard_id,
                    algo_env_json=json.dumps(env, ensure_ascii=False) if env else None,
                    algo_env_grid_keys=s.algo_env_grid_keys,
                )
                deps.db.session.add(new_sub)
                deps.db.session.flush()

                job_name = deps.start_k8s_job(new_sub, extra_env=env)
                deps.db.session.commit()

                results.append({
                    "original_id": s.id,
                    "original_name": s.submission_name,
                    "new_id": new_sub.id,
                    "new_name": new_name,
                    "job_name": job_name,
                    "ok": True,
                })
            except Exception as e:
                deps.db.session.rollback()
                results.append({
                    "original_id": s.id,
                    "original_name": s.submission_name,
                    "ok": False,
                    "error": str(e),
                })

        success_count = sum(1 for r in results if r.get("ok"))
        deps.emit_event(deps.agent_session_id, "composite.batch_rerun", {
            "total": len(results), "success": success_count
        })

        return json.dumps({
            "ok": True,
            "rerun_count": len(results),
            "success_count": success_count,
            "fail_count": len(results) - success_count,
            "details": results,
        }, ensure_ascii=False)


# -------------------------
# 【P1 复合工具】对比分析
# -------------------------
@tool
def compare_submissions(
    task_ids: List[int] | None = None,
    ladder_id: int | None = None,
    top_n: int = 2,
    run_context: RunContext | None = None,
) -> str:
    """
    Compare multiple submissions side-by-side: scores, metrics diff, env diff.
    Either provide explicit task_ids, or specify ladder_id + top_n to auto-pick
    the user's most recent N succeeded submissions on that leaderboard.
    """
    assert run_context is not None
    deps: AgentDeps = run_context.dependencies["deps"]
    _check_cancel(deps)

    with deps.flask_app.app_context():
        submissions = []

        if task_ids and len(task_ids) >= 2:
            for tid in task_ids:
                s = _visible_submission_query(deps).filter(deps.Submission.id == int(tid)).first()
                if s:
                    submissions.append(s)
        elif ladder_id:
            q = _visible_submission_query(deps).filter(
                deps.Submission.leaderboard_id == int(ladder_id),
                deps.Submission.user_id == deps.user_id,
                deps.Submission.status == "Succeeded",
            )
            submissions = q.order_by(deps.Submission.submitted_at.desc()).limit(int(top_n or 2)).all()
        else:
            # fallback: 用户最近 N 个成功的提交
            q = _visible_submission_query(deps).filter(
                deps.Submission.user_id == deps.user_id,
                deps.Submission.status == "Succeeded",
            )
            submissions = q.order_by(deps.Submission.submitted_at.desc()).limit(int(top_n or 2)).all()

        if len(submissions) < 2:
            return json.dumps({"ok": False, "error": f"需要至少 2 个提交用于对比，当前找到 {len(submissions)} 个"}, ensure_ascii=False)

        # 构建对比矩阵
        items = []
        all_metric_keys = set()

        for s in submissions:
            deps.sync_submission_status(s)
            metrics = _json_load_maybe(s.metrics_json) or {}
            env = _json_load_maybe(s.algo_env_json) or {}
            all_metric_keys.update(metrics.keys())
            items.append({
                "submission_id": s.id,
                "submission_name": s.submission_name,
                "leaderboard_name": s.leaderboard.name if s.leaderboard else None,
                "status": s.status,
                "score": s.score,
                "metrics": metrics,
                "algo_env": env,
                "image_url": s.algorithm_image_url,
                "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
            })

        # 计算 diff（两两之间的分数差异）
        diffs = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                score_diff = None
                if a["score"] is not None and b["score"] is not None:
                    try:
                        score_diff = round(float(a["score"]) - float(b["score"]), 6)
                    except (ValueError, TypeError):
                        score_diff = None

                metric_diffs = {}
                for k in all_metric_keys:
                    va = a["metrics"].get(k)
                    vb = b["metrics"].get(k)
                    if va is not None and vb is not None:
                        try:
                            metric_diffs[k] = round(float(va) - float(vb), 6)
                        except (ValueError, TypeError):
                            metric_diffs[k] = f"{va} vs {vb}"
                    else:
                        metric_diffs[k] = f"{va} vs {vb}"

                # ENV 差异
                env_a = a.get("algo_env") or {}
                env_b = b.get("algo_env") or {}
                env_diff = {}
                for k in set(list(env_a.keys()) + list(env_b.keys())):
                    if env_a.get(k) != env_b.get(k):
                        env_diff[k] = {"a": env_a.get(k), "b": env_b.get(k)}

                diffs.append({
                    "pair": [a["submission_id"], b["submission_id"]],
                    "score_diff": score_diff,
                    "metric_diffs": metric_diffs,
                    "env_diff": env_diff,
                })

        deps.emit_event(deps.agent_session_id, "composite.compare", {
            "count": len(items), "ids": [it["submission_id"] for it in items]
        })

        return json.dumps({
            "ok": True,
            "submissions": items,
            "diffs": diffs,
            "all_metric_keys": sorted(all_metric_keys),
        }, ensure_ascii=False)

