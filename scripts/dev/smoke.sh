#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
#
# smoke.sh — 用真实 QML V4 引擎冒烟 tests/smoke.qml（offscreen，不进系统树）。
# 用法：./scripts/dev/smoke.sh   （QML_BIN 可覆盖解释器路径）
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

QML_BIN="${QML_BIN:-}"
if [ -z "$QML_BIN" ]; then
    if command -v qml >/dev/null 2>&1; then
        QML_BIN="$(command -v qml)"
    elif [ -x /usr/lib/qt6/bin/qml ]; then
        QML_BIN=/usr/lib/qt6/bin/qml
    else
        echo "ERROR: 未找到 qml（需要 qt6-declarative；CI 的 archlinux 容器里有）" >&2
        exit 2
    fi
fi

cd "$REPO/tests"
QT_QPA_PLATFORM=offscreen QT_FORCE_STDERR_LOGGING=1 timeout 60 "$QML_BIN" smoke.qml
