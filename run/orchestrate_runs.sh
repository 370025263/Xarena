#!/usr/bin/env bash
# Submit the 4 conditions (noskill baseline + 3 produced skills) to the
# spreadsheet leaderboard, SEQUENTIALLY (one active at a time, workers=3) to
# bound memory on this shared box. Each runs single-mode (claude_code_exec) +
# deepseek-v4-flash on the 28-task 10x-reduced test split. The backend
# local-executor runs them; the evaluator posts results back.
set -uo pipefail
B=http://localhost:7789
USER=p_user1; PASS=user1pass
login() { curl -s -X POST $B/api/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }
PTOK=$(login)

submit () {
  local algo=$1 name=$2
  curl -s -X POST $B/api/leaderboard/1/submit \
    -H "Authorization: Bearer $PTOK" -H 'Content-Type: application/json' \
    -d "{\"submission_name\":\"$name\",\"algorithm_image_url\":\"localhost:5000/p_user1/skill-$algo:v1\",\"env_text\":\"SKILL_ALGO=$algo\\nWORKERS=3\",\"params\":{}}"
  echo "  submitted $algo as $name"
}
active () {
  PTOK=$(login)
  curl -s "$B/api/my-submissions?per_page=50" -H "Authorization: Bearer $PTOK" \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);print(sum(1 for i in d["items"] if i["status"] in ("Submitted","Pending","Running")))' 2>/dev/null || echo 1
}
run_one () {
  local algo=$1 name=$2
  echo "########## $(date +%H:%M:%S) RUN $algo ##########"
  submit "$algo" "$name"
  sleep 8
  while true; do
    local a; a=$(active)
    echo "[$(date +%H:%M:%S)] $algo active=$a"
    [ "$a" = "0" ] && break
    sleep 25
  done
  echo "########## $(date +%H:%M:%S) DONE $algo ##########"
}

run_one noskill     "noskill-single-flash-28"
run_one skillopt    "skillopt-single-flash-28"
run_one trace2skill "trace2skill-single-flash-28"
run_one xskill      "xskill-single-flash-28"

echo "########## ALL DONE — rankings ##########"
PTOK=$(login)
curl -s "$B/api/leaderboard/1/rankings" -H "Authorization: Bearer $PTOK" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);[print(r.get("submission_name"),r.get("score")) for r in d.get("rankings",[])]'
echo "ORCHESTRATE_COMPLETE"
