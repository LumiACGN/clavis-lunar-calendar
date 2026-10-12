#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
#
# clavis-lunar-calendar 顶层入口（薄壳，转发给 overlay 安装引擎）
#
#   ./install.sh                 首次安装（会自动停/起 clavis-shell）
#   ./install.sh --sync          Clavis 升级后重新对齐（多组件时按 order 重放全部）
#   ./install.sh --verify        逐组件检查 + 孤儿标记报告（附带本仓定时器路径检查）
#   ./install.sh --list          列出已注册组件
#   ./install.sh --uninstall     移除本组件（--all 整体拆除；默认保留配置/缓存）
#   ./install.sh --forget <id>   从注册表摘除某组件（不动 overlay；repo 被删/搬走时的逃生舱）
#   ./install.sh --check-data    只读检查上游农历库/节假日数据是否有新版
#   ./install.sh --with-timer    安装并启用「每年 11~12 月只读检查」用户定时器
#
# 其余参数原样转发：--dry-run / --adopt / --force / --tree <路径> / --repo-root <路径> /
# --overlay-dir <路径> / --skip-service / --skip-bin / --skip-missing / --allow-service …
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_NAME="clavis-lunar-calendar-refresh"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

# 假 HOME（彩排/测试）时不允许碰真实 systemd 用户会话
real_home="$(getent passwd "$(id -u)" | cut -d: -f6 || true)"
is_real_home() { [ -n "$real_home" ] && [ "$HOME" = "$real_home" ]; }

install_timer() {
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/$UNIT_NAME.service" <<EOF
[Unit]
Description=检查 clavis-lunar-calendar 上游农历数据是否有新版（只读，不改文件）
Documentation=file://$DIR/docs/DATA.md

[Service]
Type=oneshot
ExecStart=$DIR/scripts/check-lunar-data.sh --notify
EOF
    cat > "$UNIT_DIR/$UNIT_NAME.timer" <<EOF
[Unit]
Description=每年 11~12 月检查 clavis-lunar-calendar 上游数据更新

[Timer]
OnCalendar=*-11-* 10:17
OnCalendar=*-12-* 10:17
Persistent=true
RandomizedDelaySec=15min

[Install]
WantedBy=timers.target
EOF
    echo "== 已写入定时器单元：$UNIT_DIR/$UNIT_NAME.{service,timer}"
    echo "   ExecStart 指向本仓库（$DIR）——仓库搬家后请重跑 ./install.sh --with-timer"
    if ! is_real_home; then
        echo "== 非真实 HOME：跳过 systemctl（彩排安全）"
        return 0
    fi
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user daemon-reload || true
        if systemctl --user enable --now "$UNIT_NAME.timer"; then
            echo "== 定时器已启用（systemctl --user list-timers $UNIT_NAME.timer 可查看）"
        else
            echo "WARN: 启用定时器失败——可稍后手动：systemctl --user enable --now $UNIT_NAME.timer" >&2
        fi
    fi
}

check_timer_path() {
    local unit="$UNIT_DIR/$UNIT_NAME.service"
    [ -f "$unit" ] || return 0
    local exec_path
    exec_path="$(sed -n 's/^ExecStart=\([^ ]*\).*/\1/p' "$unit" | head -1)"
    if [ -n "$exec_path" ] && [ ! -x "$exec_path" ]; then
        echo "WARN: 定时器指向的脚本不存在：$exec_path（仓库搬过家？重跑 ./install.sh --with-timer）" >&2
    fi
}

MODE="install"
ARGS=()
WITH_TIMER=0
while [ $# -gt 0 ]; do
    case "$1" in
        --sync) MODE="sync" ;;
        --verify) MODE="verify" ;;
        --uninstall) MODE="uninstall" ;;
        --list) MODE="list" ;;
        --forget) MODE="forget"; shift; ARGS+=("${1:?--forget 需要一个组件 id（见 --list）}") ;;
        --check-data) MODE="check-data" ;;
        --with-timer) WITH_TIMER=1 ;;
        *) ARGS+=("$1") ;;
    esac
    shift
done

case "$MODE" in
    check-data)
        exec "$DIR/scripts/check-lunar-data.sh" ${ARGS[@]+"${ARGS[@]}"} ;;
    verify)
        check_timer_path ;;
esac

if [ "$WITH_TIMER" = 1 ]; then
    install_timer
fi

exec python3 "$DIR/scripts/clavis_overlay.py" "$MODE" ${ARGS[@]+"${ARGS[@]}"}
