#!/usr/bin/env bash
# ALFWorld miniset 端到端验收：建榜（l_creator）→ 提交 noskill（p_user1）→ 等待 → 核验。
# 用法: bash run/alfworld_miniset_run.sh
set -uo pipefail
API="${API:-http://localhost:30001}"
EVAL_IMG="${EVAL_IMG:-localhost:5000/l_creator/alfworld-eval:latest}"
ALGO_IMG="${ALGO_IMG:-localhost:5000/p_user1/alfworld-noskill:v1}"

login() { curl -s --max-time 10 -X POST "$API/api/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$1\",\"password\":\"$2\"}" | python3.11 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))"; }

CTOK=$(login l_creator creatorpass); echo "creator token len ${#CTOK}"
PTOK=$(login p_user1 user1pass);     echo "participant token len ${#PTOK}"

echo "=== create board ==="
BOARD=$(curl -s --max-time 15 -X POST "$API/api/leaderboards" \
  -H "Authorization: Bearer $CTOK" -H 'Content-Type: application/json' \
  -d "{\"name\":\"ALFWorld Skill Bench (miniset)\",
       \"description\":\"ALFWorld TextWorld ReAct, 3-game miniset, deepseek-v4-flash. noskill baseline + custom trajectory result-view.\",
       \"evaluator_image\":\"$EVAL_IMG\",
       \"version\":\"alfworld-miniset\",
       \"resource_spec\":{\"cpu\":\"2\",\"memory\":\"4Gi\",\"cpu_limit\":\"4\",\"memory_limit\":\"8Gi\"},
       \"difficulty_factor\":1.0}")
echo "$BOARD"
BID=$(echo "$BOARD" | python3.11 -c "import sys,json;d=json.load(sys.stdin);print(d.get('id') or (d.get('leaderboard') or {}).get('id') or '')" 2>/dev/null)
[ -z "$BID" ] && BID=$(curl -s -H "Authorization: Bearer $CTOK" "$API/api/leaderboards" | python3.11 -c "import sys,json;bs=json.load(sys.stdin);print([b['id'] for b in bs if b['name'].startswith('ALFWorld')][-1])")
echo "BOARD ID=$BID"

echo "=== submit noskill ==="
SUB=$(curl -s --max-time 30 -X POST "$API/api/leaderboard/$BID/submit" \
  -H "Authorization: Bearer $PTOK" -H 'Content-Type: application/json' \
  -d "{\"submission_name\":\"noskill-miniset-$(date +%s)\",\"algorithm_image_url\":\"$ALGO_IMG\"}")
echo "$SUB"
SID=$(echo "$SUB" | python3.11 -c "import sys,json;print(json.load(sys.stdin).get('submission_id',''))")
echo "SUBMISSION ID=$SID"
echo "$BID $SID" > /tmp/alfworld_ids.txt
