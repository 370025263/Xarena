#!/usr/bin/env bash
# Fetch the full ALFWorld game dataset (json_2.1.1 / json_2.1.2 tw-pddl) into
# $ALFWORLD_DATA. Mirrors what the `alfworld-download` console script does, but
# without needing the alfworld package on the host. ~143 MB zipped -> ~1.7 GB
# extracted (4027 game.tw-pddl + 7080 traj_data.json + logic/).
#
# Usage:
#   ALFWORLD_DATA=/models/xarena/alfworld/_data bash fetch_data.sh
#   bash fetch_data.sh                 # defaults to ~/.cache/alfworld
#
# The repo commits only the split manifest + a tiny miniset; the full game
# payload is fetched here (see README "full dataset -> git" note).
set -euo pipefail

ALFWORLD_DATA="${ALFWORLD_DATA:-$HOME/.cache/alfworld}"
mkdir -p "$ALFWORLD_DATA"
cd "$ALFWORLD_DATA"

JSON_URL="https://github.com/alfworld/alfworld/releases/download/0.2.2/json_2.1.1_json.zip"
PDDL_URL="https://github.com/alfworld/alfworld/releases/download/0.2.2/json_2.1.1_pddl.zip"
TWPDDL_URL="https://github.com/alfworld/alfworld/releases/download/0.4.0/json_2.1.2_tw-pddl.zip"

fetch() {
  local url="$1" f
  f="$(basename "$url")"
  if [ -f "$f" ]; then echo "[fetch_data] $f already present, skipping download"; else
    echo "[fetch_data] downloading $f ..."
    curl -fSL --retry 3 -o "$f" "$url"
  fi
  echo "[fetch_data] extracting $f ..."
  unzip -q -o "$f" -d "$ALFWORLD_DATA"
  rm -f "$f"
}

fetch "$TWPDDL_URL"   # the .tw-pddl game files the split references
fetch "$JSON_URL"     # traj_data.json (reference trajectories / task descs)
fetch "$PDDL_URL"     # per-task pddl

# logic/ files (alfred.pddl, alfred.twl2) — config_tw.yaml needs these.
# If the alfworld package is importable, copy them from there; the evaluator
# also does this at runtime as a fallback.
if python3 - <<'PY' 2>/dev/null
import alfworld.info as i, os, shutil
d = os.path.join(os.environ["ALFWORLD_DATA"], "logic"); os.makedirs(d, exist_ok=True)
base = os.path.join(os.path.dirname(i.__file__), "data")
for fn in ("alfred.pddl", "alfred.twl2"):
    s = os.path.join(base, fn)
    if os.path.isfile(s): shutil.copy2(s, os.path.join(d, fn))
print("copied logic files")
PY
then :; else
  echo "[fetch_data] NOTE: alfworld package not importable here; logic/ files will be"
  echo "             copied by the evaluator at runtime from the in-image package."
fi

echo "[fetch_data] done. ALFWORLD_DATA=$ALFWORLD_DATA"
du -sh "$ALFWORLD_DATA" 2>/dev/null || true
