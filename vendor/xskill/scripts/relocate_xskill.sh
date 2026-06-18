#!/usr/bin/env bash
#
# relocate_xskill.sh —— 在线把 ~/.xskill 迁到别处(默认 /home/xskill)，用 symlink
# 让进程无感继续读写新位置。不停 xskill 进程。
#
# 背景(看代码得出的硬约束)：
#   - XSKILL_HOME 在 config.py 写死 = Path.home()/".xskill"，无 env 开关，
#     所以只能在文件系统层用 symlink 重定向。
#   - registry/atom 等 sqlite 全是短连接(开-用-关)，切换瞬间基本不被占。
#   - 日志是常驻 RotatingFileHandler，长期 open 着 logs/*.log。
#
# 两种迁移路径(脚本自动判别)：
#   ① 同文件系统 → 用 `mv`(rename)。inode 不变，连常驻日志句柄都自动跟到新位置，
#     近乎零风险。唯一窗口：mv 与 ln 之间几微秒 ~/.xskill 不存在(watcher 自愈)。
#   ② 跨文件系统 → 只能 rsync 拷贝。旧日志句柄会继续写旧位置直到进程重启；
#     且拷贝期间 watcher 仍在写 → 有竞态。两遍 rsync 压窄窗口，事后建议重启进程。
#
# 用法：
#   scripts/relocate_xskill.sh                 # dry-run：只打印计划与可行性
#   scripts/relocate_xskill.sh --apply         # 真正执行
#   SRC=/root/.xskill DST=/data/xskill scripts/relocate_xskill.sh --apply
#
set -euo pipefail

SRC="${SRC:-$HOME/.xskill}"
DST="${DST:-/home/xskill}"
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

die() { echo "ERROR: $*" >&2; exit 1; }
note() { echo "  - $*"; }

# ── 前置校验(遇问题直接 throw，不做 fallback) ──────────────────────────
[[ -e "$SRC" ]] || die "源不存在: $SRC"
[[ -L "$SRC" ]] && die "源已是 symlink，疑似已迁移过: $SRC -> $(readlink "$SRC")"
[[ -d "$SRC" ]] || die "源不是目录: $SRC"
if [[ -e "$DST" ]]; then
    [[ -d "$DST" && -z "$(ls -A "$DST" 2>/dev/null)" ]] \
        || die "目标已存在且非空，请先清理: $DST"
fi

# ── 判别同/跨文件系统(比较 device id) ──────────────────────────────────
DST_PARENT="$(dirname "$DST")"
mkdir -p "$DST_PARENT"
SRC_DEV="$(stat -c %d "$SRC")"
DST_DEV="$(stat -c %d "$DST_PARENT")"
if [[ "$SRC_DEV" == "$DST_DEV" ]]; then
    MODE="same-fs (mv/rename，inode 不变，最安全)"
    METHOD="mv"
else
    MODE="cross-fs (rsync 拷贝，旧句柄需重启才脱钩)"
    METHOD="rsync"
fi

SRC_SIZE="$(du -sh "$SRC" 2>/dev/null | cut -f1)"
echo "================================================================"
echo "  xskill 在线迁移计划"
echo "================================================================"
note "源 SRC      : $SRC  (~$SRC_SIZE)"
note "目标 DST    : $DST"
note "文件系统    : $MODE"
note "迁移方式    : $METHOD"
note "在跑的进程  :"
pgrep -af "xskill" | sed 's/^/      /' || echo "      (未发现 xskill 进程)"
echo "================================================================"

if [[ "$APPLY" -ne 1 ]]; then
    echo "（dry-run。确认无误后加 --apply 执行）"
    if [[ "$METHOD" == "rsync" ]]; then
        echo
        echo "⚠ 跨文件系统提醒：rsync 完成、symlink 切换后，进程里常驻的日志 fd"
        echo "  仍写在旧位置(${SRC}.bak.*)。要让日志也归位，需在低峰期重启 xskill。"
    fi
    exit 0
fi

# ── 真正执行 ──────────────────────────────────────────────────────────
TS="$(date +%Y%m%d-%H%M%S)"

if [[ "$METHOD" == "mv" ]]; then
    echo ">> 同 fs：原子 rename"
    # rename 不改 inode → 所有已 open 的句柄继续有效并跟到新位置。
    mv "$SRC" "$DST"
    ln -s "$DST" "$SRC"          # 几微秒窗口，watcher 下一拍 scan 自愈
    echo ">> 完成：$SRC -> $(readlink "$SRC")"
else
    echo ">> 跨 fs：两遍 rsync 压窄竞态窗口"
    command -v rsync >/dev/null || die "需要 rsync，请先安装"
    # 第一遍：批量(进程仍在写，允许有差异)
    rsync -aHAX --delete "$SRC"/ "$DST"/
    # sqlite WAL 落盘：把 -wal 合并进主库，让第二遍拷到一致的主文件
    for db in "$SRC"/*.db; do
        [[ -e "$db" ]] || continue
        sqlite3 "$db" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1 || true
    done
    # 第二遍：增量(快，仅补第一遍后的新写)
    rsync -aHAX --delete "$SRC"/ "$DST"/
    # 原子切换：旧目录改名备份 → 立刻建 symlink
    mv "$SRC" "${SRC}.bak.${TS}"
    ln -s "$DST" "$SRC"
    echo ">> 完成：$SRC -> $(readlink "$SRC")"
    echo ">> 旧数据已备份到 ${SRC}.bak.${TS}（确认新位置无误后再删）"
    echo "⚠ 进程内常驻日志 fd 仍指向备份目录，低峰期重启 xskill 后日志归位。"
fi

# ── 验证 ──────────────────────────────────────────────────────────────
echo ">> 验证:"
note "symlink : $SRC -> $(readlink "$SRC")"
note "registry: $(ls -l "$DST/registry.db" 2>/dev/null || echo '缺失!')"
if command -v sqlite3 >/dev/null; then
    INTEG="$(sqlite3 "$DST/registry.db" "PRAGMA integrity_check;" 2>&1 | head -1)"
    note "db 完整性: $INTEG"
fi
echo "完成。"
