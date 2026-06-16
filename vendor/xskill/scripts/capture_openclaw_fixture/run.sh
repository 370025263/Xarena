#!/bin/bash
# 容器入口：跑一次 openclaw agent --local，把产生的 trajectory.jsonl 输出到 /output/
set -e

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
    echo "DEEPSEEK_API_KEY env required" >&2
    exit 2
fi

mkdir -p /work/.openclaw/workspace /output

# 用一句明确要用工具的话触发 tool_use（exec 是 openclaw bundled tool）
echo "=== running openclaw agent ==="
openclaw agent --local --message "Use the exec tool to run 'ls /etc | head -5' and show me the output." --json \
    > /output/agent_output.json 2> /output/agent_stderr.log \
    || { echo "agent run failed; see agent_stderr.log"; exit 1; }

echo "=== agent done; trajectory files: ==="
find /work/.openclaw/agents -name '*.trajectory.jsonl' -print0 | xargs -0 ls -la

# 把产生的 trajectory 拷出
TRAJ_FILE=$(find /work/.openclaw/agents -name '*.trajectory.jsonl' | head -1)
if [ -z "$TRAJ_FILE" ]; then
    echo "no trajectory.jsonl produced" >&2
    exit 1
fi
cp "$TRAJ_FILE" /output/openclaw_session.trajectory.jsonl

# 顺手也拷一份配套的 runtime jsonl 和 pointer，便于排查
RUNTIME_FILE=$(find /work/.openclaw/agents -name '*.jsonl' ! -name '*.trajectory.jsonl' ! -name '*.bak-*' ! -name '*.reset.*' | head -1)
[ -n "$RUNTIME_FILE" ] && cp "$RUNTIME_FILE" /output/openclaw_session.runtime.jsonl

echo "=== summary ==="
echo "trajectory: $(wc -l < /output/openclaw_session.trajectory.jsonl) events"
echo "event types:"
jq -r '.type' /output/openclaw_session.trajectory.jsonl | sort -u | sed 's/^/  /'
echo "tool_use blocks in messagesSnapshot:"
jq -r 'select(.type == "model.completed") | .data.messagesSnapshot[]?.content | select(type == "array") | .[]? | select(.type == "tool_use") | .name' \
    /output/openclaw_session.trajectory.jsonl | sort -u | sed 's/^/  /'

echo "OK"
