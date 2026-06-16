#!/usr/bin/env bash
# After the current orchestrator finishes (xskill), re-run any condition that
# suffered transient `FileNotFoundError: 'claude'` spawn failures, now that
# run_single_eval.sh resolves an absolute claude path. Submits fresh runs via
# the API (so they use the fixed script) and lets the evaluator post back.
set -uo pipefail
B=http://localhost:7789
login(){ curl -s -X POST $B/api/login -H 'Content-Type: application/json' -d '{"username":"p_user1","password":"user1pass"}'|python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }
active(){ curl -s "$B/api/my-submissions?per_page=50" -H "Authorization: Bearer $(login)" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(sum(1 for i in d["items"] if i["status"] in ("Submitted","Pending","Running")))' 2>/dev/null || echo 1; }
wait_idle(){ while [ "$(active)" != "0" ]; do sleep 30; done; }

transient_fails(){  # $1 = run dir
  local rp="$1/results.jsonl"; [ -f "$rp" ] || { echo 99; return; }
  python3 - "$rp" <<'PY'
import json,sys
n=0
for l in open(sys.argv[1]):
    r=json.loads(l)
    if int(r.get('hard',0))==0 and "FileNotFoundError" in (r.get('fail_reason','')+r.get('error','')):
        n+=1
print(n)
PY
}

echo "[rerun] waiting for current orchestrator to go idle..."
wait_idle
echo "[rerun] idle. checking which runs need re-running."

PTOK=$(login)
submit(){ # algo name
  curl -s -X POST $B/api/leaderboard/1/submit -H "Authorization: Bearer $PTOK" -H 'Content-Type: application/json' \
    -d "{\"submission_name\":\"$2\",\"algorithm_image_url\":\"localhost:5000/p_user1/skill-$1:v2\",\"env_text\":\"SKILL_ALGO=$1\\nWORKERS=3\",\"params\":{}}"
  echo "  resubmitted $1 as $2"
}

# noskill always re-run (16 transient fails observed)
submit noskill "noskill-single-flash-28-clean"
sleep 8; wait_idle
# xskill: re-run only if it had transient fails
XF=$(transient_fails "${EVAL_RUNS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/data/eval_runs}/sub_4_xskill")
echo "[rerun] xskill transient fails = $XF"
if [ "$XF" != "0" ]; then submit xskill "xskill-single-flash-28-clean"; sleep 8; wait_idle; fi

echo "RERUN_CLEAN_COMPLETE"
curl -s "$B/api/leaderboard/1/rankings" -H "Authorization: Bearer $(login)" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);[print(r.get("submission_name"),r.get("score")) for r in d.get("rankings",[])]'
