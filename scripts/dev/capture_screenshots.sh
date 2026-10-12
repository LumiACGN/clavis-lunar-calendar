#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 LumiACGN
# SPDX-License-Identifier: GPL-3.0-or-later
#
# 无头截图产线：假 HOME → overlay 安装到假 HOME → 纯日历 harness →
# cage（headless 合成器）渲染 → grim 抓图 → 裁切出 docs/screenshots。
#
# 用法: scripts/dev/capture_screenshots.sh [输出目录] [预览HOME] [--tree <Clavis 源树>]
#
# 注意：本机 /etc/xdg/quickshell/clavis 已被手工打过补丁（含字号 +1px），
# 想让文档图反映**纯净上游 + 本组件补丁**，请显式传：
#   --tree ~/rice-work/shell-inspect/clavis-shell-2026.9.25
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="$REPO/docs/screenshots"
PH="/tmp/clavis-lunar-shot-home"
TREE="/etc/xdg/quickshell/clavis"
POS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --tree) shift; TREE="${1:?--tree 需要路径}" ;;
        *) POS+=("$1") ;;
    esac
    shift
done
[ "${#POS[@]}" -gt 0 ] && OUT_DIR="${POS[0]}"
[ "${#POS[@]}" -gt 1 ] && PH="${POS[1]}"

if grep -rqE '// BEGIN clavis-' "$TREE" --include='*.qml' --include='*.js' 2>/dev/null; then
    echo "WARN: 源树 $TREE 里已含 // BEGIN clavis-* 补丁标记——截图反映的是**该源树**，不是纯净上游。" >&2
    echo "      建议显式传 --tree <pristine 副本>（如 ~/rice-work/shell-inspect/clavis-shell-2026.9.25）。" >&2
fi

echo "== 1) 准备预览 HOME（假 HOME，零真实数据）"
rm -rf "$PH"
mkdir -p "$PH/.config/clavis" "$PH/rt"
chmod 700 "$PH/rt"
# Clavis 界面偏好：暗色 + 24 小时制（贴近中文用户真实观感）
cat > "$PH/.config/clavis/ui-preferences.json" <<'EOF'
{
  "darkMode": true,
  "useTwelveHourClock": false
}
EOF
chmod 600 "$PH/.config/clavis/ui-preferences.json"

echo "== 2) 安装 overlay 到预览 HOME（跳过服务控制）"
env HOME="$PH" XDG_CONFIG_HOME="$PH/.config" XDG_STATE_HOME="$PH/.state" \
    "$REPO/install.sh" --tree "$TREE" --skip-service >/dev/null

OV="$PH/.config/quickshell/clavis"

echo "== 3) 写入纯日历 harness（480x560 = 6 行 x 38px + 头部 + spacing 不裁剪）"
cat > "$OV/shell.qml" <<'QML'
// SPDX-FileCopyrightText: 2026 LumiACGN
// SPDX-License-Identifier: GPL-3.0-or-later
// 截图 harness（仅预览用）：把信息页日历铺满输出（输出被设为 480x560）。
import QtQuick
import Quickshell
import "Modules/Sidebars/Dashboard/infoTools" as InfoTools

ShellRoot {
    FloatingWindow {
        color: "#17161f"
        implicitWidth: 480
        implicitHeight: 560
        InfoTools.CalendarWidget {
            anchors.fill: parent
        }
    }
}
QML

echo "== 4) cage 无头渲染 + grim 抓图"
SHOT_TMP="$(mktemp -d /tmp/clavis-lunar-shot.XXXXXX)"
mkdir -p "$SHOT_TMP/rt"
chmod 700 "$SHOT_TMP/rt"
trap 'rm -rf "$SHOT_TMP"' EXIT

timeout 90 env HOME="$PH" XDG_RUNTIME_DIR="$SHOT_TMP/rt" \
    WLR_BACKENDS=headless WLR_HEADLESS_OUTPUTS=1 \
    SHOT_OVERLAY="$OV" SHOT_DIR="$SHOT_TMP" SHOT_MODE=480x560 \
    cage -- "$REPO/scripts/dev/capture_shot.sh"

[ -f "$SHOT_TMP/shot.png" ] || { echo "截图失败，qs 日志：" >&2; cat "$SHOT_TMP/qs.log" >&2; exit 1; }

echo "== 5) 裁切产物"
mkdir -p "$OUT_DIR"
cp "$SHOT_TMP/shot.png" "$OUT_DIR/preview.png"                       # 整页（480x560，零裁切）
# 日期网格细节：前三个星期行（含 休/班 角标、节气、节日与「今天」高亮）
# 几何按 480x560 实测标定：标题 y≈150、星期行 y≈200、格子行距≈50（本机 ImageMagick
# 会把 -crop 的百分比**偏移**当绝对像素，所以这里用实测绝对像素，别改回百分比）
magick "$SHOT_TMP/shot.png" -crop 480x160+0+230 +repage "$OUT_DIR/cells.png"
magick identify "$OUT_DIR/preview.png" "$OUT_DIR/cells.png"
echo "完成：$OUT_DIR"
