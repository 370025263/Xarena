#!/usr/bin/env bash
# Serially submit the 3 打榜 algo images to the k8s board. Each submission =
# native k8s Job (algo init/sidecar trains -> /shared/skill -> eval main scores
# -> posts back). Serial to fit the single 4cpu/15G kind node.
set -uo pipefail
B=http://localhost:30001
BID=1
login(){ curl -s -X POST $B/api/login -H 'Content-Type: application/json' -d '{"username":"p_user1","password":"user1pass"}'|python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }
status_of(){ # $1 = submission_name -> prints status
  curl -s "$B/api/my-submissions?per_page=50" -H "Authorization: Bearer $(login)" \
    | python3 -c "import sys,json;d=json.load(sys.stdin);m={i['name']:i['status'] for i in d['items']};print(m.get('$1','MISSING'))" 2>/dev/null || echo ERR
}
run_one(){ local algo=$1 name="$1-k8s"
  echo "########## $(date +%H:%M:%S) submit $algo ##########"
  curl -s -X POST $B/api/leaderboard/$BID/submit -H "Authorization: Bearer $(login)" -H 'Content-Type: application/json' \
    -d "{\"submission_name\":\"$name\",\"algorithm_image_url\":\"localhost:5000/p_user1/algo-$algo:v1\",\"env_text\":\"\",\"params\":{}}"; echo
  sleep 10
  while true; do
    s=$(status_of "$name")
    echo "[$(date +%H:%M:%S)] $name -> $s"
    case "$s" in Succeeded|Failed|Cancelled) break;; esac
    sleep 30
  done
  echo "########## $(date +%H:%M:%S) $name finished: $(status_of "$name") ##########"
}
run_one skillopt
run_one trace2skill
run_one xskill
echo "########## ALL 3 K8S RUNS DONE ##########"
curl -s "$B/api/leaderboard/$BID/rankings?per_submission=1" -H "Authorization: Bearer $(login)" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);[print(r.get("submission_name") or r.get("name"), r.get("score")) for r in d.get("rankings",[])]'
echo K8S_SERIAL_COMPLETE
