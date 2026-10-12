#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
#
# 在 cage 内运行的截图子脚本（由 capture_screenshots.sh 调用，勿单独使用）：
# 把无头输出切成日历窗比例 → 起 qs 渲染 harness → grim 抓图。
set -euo pipefail

OUT_NAME="$(wlr-randr 2>/dev/null | awk '/^[A-Za-z]/{print $1; exit}')"
wlr-randr --output "$OUT_NAME" --custom-mode "$SHOT_MODE" >/dev/null 2>&1 || true
sleep 1

LANG=zh_CN.UTF-8 qs -p "$SHOT_OVERLAY" 2>"$SHOT_DIR/qs.log" &
QS_PID=$!
sleep 10
grim "$SHOT_DIR/shot.png"
kill "$QS_PID" 2>/dev/null || true
wait "$QS_PID" 2>/dev/null || true
