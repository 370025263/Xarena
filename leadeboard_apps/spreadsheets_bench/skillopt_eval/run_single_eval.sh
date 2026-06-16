#!/usr/bin/env bash
# ============================================================================
# Spreadsheet leaderboard — SINGLE-mode (with-harness) evaluator.
#
# Wraps SkillOpt eval_only.py with:
#   target_backend = claude_code_exec   (the "single / with-harness" mode)
#   target_model   = deepseek-v4-flash  (a.k.a. dsv4flash)
#   mode           = single
# on the 10x-reduced test split, for one of the produced skills.
#
# Usage: run_single_eval.sh <skill_algo> <out_dir>
#   skill_algo: skillopt | trace2skill | xskill | noskill
#
# Skill conventions (per bench readme):
#   * single skill.md  (skillopt / trace2skill / noskill) -> reference mode,
#     injected into the system prompt via --skill <file>.
#   * multi-skill folder (xskill, Anthropic skills/ format) -> native mode:
#     skills copied into the isolated .claude/skills, XSKILL_SKILL_MODE=native,
#     and --skill points at an empty file.
# ============================================================================
set -uo pipefail

SKILL_ALGO="${1:?usage: run_single_eval.sh <skill_algo> <out_dir>}"
OUT_DIR="${2:?usage: run_single_eval.sh <skill_algo> <out_dir>}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# repo root: skillopt_eval -> spreadsheets_bench -> leadeboard_apps -> repo
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
# Self-contained defaults point at the in-repo vendored SkillOpt + data
# (override any of these via env). The K8s path uses the baked evaluator.py;
# this host local-exec path uses the vendored source.
SKILLOPT="${SKILLOPT_DIR:-$REPO_ROOT/vendor/SkillOpt}"
PY="${EVAL_PY:-python3}"
DATA_ROOT="${DATA_ROOT:-$REPO_ROOT/vendor/data_root}"
BENCH_DIR="${BENCH_DIR:-$REPO_ROOT/leadeboard_apps/spreadsheets_bench}"
SPLIT_DIR="${SPLIT_DIR:-$BENCH_DIR/data/test_10pct_split}"
SKILLS_DIR="${SKILLS_DIR:-$BENCH_DIR/skills}"
WORKERS="${WORKERS:-3}"
MODEL="${MODEL:-deepseek-v4-flash}"
EXEC_TIMEOUT="${EXEC_TIMEOUT:-600}"

# --- harness/model env (verified-good single-mode claude_code_exec settings) ---
# Resolve an ABSOLUTE claude binary. Passing the bare name 'claude' causes
# intermittent `FileNotFoundError: 'claude'` spawn failures under concurrent
# workers (SkillOpt bug #1) — which silently inflate the fail count. So resolve
# robustly and FAIL LOUDLY rather than ever pass an empty path.
resolve_claude() {
  local c
  # prefer an explicit CLAUDE_BIN, then PATH, then any nvm-installed claude.
  [ -n "${CLAUDE_BIN:-}" ] && [ -e "$CLAUDE_BIN" ] && { echo "$CLAUDE_BIN"; return 0; }
  c="$(command -v claude 2>/dev/null || true)"
  [ -n "$c" ] && { readlink -f "$c" 2>/dev/null || echo "$c"; return 0; }
  c="$(ls -1 "$HOME"/.nvm/versions/node/*/bin/claude 2>/dev/null | head -1)"
  [ -n "$c" ] && { echo "$c"; return 0; }
  return 1
}
CLAUDE_ABS="$(resolve_claude || true)"
if [ -z "$CLAUDE_ABS" ] || [ ! -e "$CLAUDE_ABS" ]; then
  echo "FATAL: cannot resolve an absolute 'claude' binary; aborting to avoid spurious spawn failures." >&2
  exit 3
fi
export CLAUDE_BIN="$CLAUDE_ABS"   # harness may also read this
export CLAUDE_CODE_EXEC_USE_SDK=cli
export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-https://api.deepseek.com/anthropic}"
# Auth token from env / config.local.env (written by install.sh); never hardcode.
[ -z "${ANTHROPIC_AUTH_TOKEN:-}" ] && [ -f "$REPO_ROOT/config.local.env" ] && \
  set -a && . "$REPO_ROOT/config.local.env" && set +a
export ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-${DEEPSEEK_API_KEY:-}}"
if [ -z "$ANTHROPIC_AUTH_TOKEN" ]; then
  echo "FATAL: no DEEPSEEK_API_KEY / ANTHROPIC_AUTH_TOKEN (set it in config.local.env or env)." >&2
  exit 4
fi

# --- isolated claude home (avoids ancestor .claude/skills leakage) ---
HOME_DIR="$OUT_DIR/_home"
rm -rf "$HOME_DIR"; mkdir -p "$HOME_DIR/.claude/skills" "$HOME_DIR/.claude/projects"
cat > "$HOME_DIR/.claude/settings.json" <<'JSON'
{
  "skillListingBudgetFraction": 0.1,
  "permissions": { "defaultMode": "bypassPermissions" }
}
JSON
export XSKILL_CLAUDE_HOME="$HOME_DIR/.claude"

EMPTY_SKILL="$SKILLS_DIR/empty_skill.md"
[ -f "$EMPTY_SKILL" ] || : > "$EMPTY_SKILL"

case "$SKILL_ALGO" in
  skillopt)    SKILL_FILE="$SKILLS_DIR/skillopt.skill.md";    unset XSKILL_SKILL_MODE || true ;;
  trace2skill) SKILL_FILE="$SKILLS_DIR/trace2skill.skill.md"; unset XSKILL_SKILL_MODE || true ;;
  noskill)     SKILL_FILE="$EMPTY_SKILL";                     unset XSKILL_SKILL_MODE || true ;;
  xskill)
      SKILL_FILE="$EMPTY_SKILL"
      export XSKILL_SKILL_MODE=native
      # materialise the multi-skill library into the isolated claude home
      for d in "$SKILLS_DIR"/xskill_skills/*/; do
        [ -f "$d/SKILL.md" ] || continue
        name="$(basename "$d")"
        mkdir -p "$HOME_DIR/.claude/skills/$name"
        cp -r "$d/." "$HOME_DIR/.claude/skills/$name/"
      done
      ;;
  *) echo "unknown skill_algo: $SKILL_ALGO" >&2; exit 2 ;;
esac

echo "=== single-mode eval ==="
echo "  algo=$SKILL_ALGO  skill_file=$SKILL_FILE  skill_mode=${XSKILL_SKILL_MODE:-reference}"
echo "  model=$MODEL  backend=claude_code_exec  workers=$WORKERS  split_dir=$SPLIT_DIR"
echo "  claude=$CLAUDE_ABS  out=$OUT_DIR"

cd "$SKILLOPT"
"$PY" scripts/eval_only.py \
  --config configs/spreadsheetbench/default.yaml \
  --skill "$SKILL_FILE" \
  --split test --split_mode split_dir \
  --split_dir "$SPLIT_DIR" --data_root "$DATA_ROOT" \
  --target_backend claude_code_exec --target_model "$MODEL" \
  --claude_code_exec_use_sdk cli --claude_code_exec_path "$CLAUDE_ABS" \
  --workers "$WORKERS" --seed 42 --out_root "$OUT_DIR" \
  --cfg-options env.mode=single env.exec_timeout="$EXEC_TIMEOUT"
rc=$?
echo "=== eval exit=$rc ==="
exit $rc
