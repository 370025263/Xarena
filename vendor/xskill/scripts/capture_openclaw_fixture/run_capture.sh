#!/bin/bash
# host 入口：build 镜像 + 跑容器 + 把产物 cp 进 tests/fixtures/
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
OUTPUT_DIR="$REPO/tests/fixtures"

if [ ! -r "$HOME/.aikey" ]; then
    echo "~/.aikey not found — need DEEPSEEK_API_KEY in there" >&2
    exit 2
fi
DEEPSEEK_KEY="$(grep -m1 '^DEEPSEEK_API_KEY=' "$HOME/.aikey" | cut -d= -f2-)"
if [ -z "$DEEPSEEK_KEY" ]; then
    echo "DEEPSEEK_API_KEY not set in ~/.aikey" >&2
    exit 2
fi

echo "=== build capture image ==="
docker build -t xskill-openclaw-capture:local "$HERE"

mkdir -p "$OUTPUT_DIR"
TMP_OUT="$(mktemp -d)"
trap 'rm -rf "$TMP_OUT"' EXIT

echo "=== run capture (single agent turn against deepseek) ==="
docker run --rm \
    -e DEEPSEEK_API_KEY="$DEEPSEEK_KEY" \
    -v "$TMP_OUT:/output" \
    xskill-openclaw-capture:local

echo "=== move trajectory into $OUTPUT_DIR ==="
cp "$TMP_OUT/openclaw_session.trajectory.jsonl" "$OUTPUT_DIR/openclaw_session.trajectory.jsonl"
echo "fixture: $OUTPUT_DIR/openclaw_session.trajectory.jsonl ($(wc -l < "$OUTPUT_DIR/openclaw_session.trajectory.jsonl") events)"
