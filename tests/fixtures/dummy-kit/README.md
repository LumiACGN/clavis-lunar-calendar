<!--
SPDX-FileCopyrightText: 2026 LumiACGN
SPDX-License-Identifier: GPL-3.0-or-later
-->

# dummy-kit：测试夹具（不是真实组件）

这份「组件」只服务于 `tests/test_coexist.sh`（多组件共存彩排），**不会安装到任何真实
Clavis 环境**，也不随本项目发布。`tests/test_manifest.py` 会校验它的锚点在本仓的
上游基线里恰好命中一次。

它刻意设计成「最难场景」：

- `kit.id = clavis-dummy-kit`、`order = 20`——与本仓（`clavis-lunar-calendar`）**同 order**，
  按 `(order, id)` 排序时字母序在前，所以它**先重放**、本仓的补丁后打；
- 打的文件是 **`Modules/Sidebars/Dashboard/infoTools/CalendarWidget.qml`**——与本仓同一个
  文件（共享文件：两套标记必须并存、互不干扰，卸载其一不能伤到另一个）；
- 锚点选 `    property int monthShift: 0`——本仓 7 个 edit 都不碰这块区域，因此谁先打都成立；
- payload 是一个 QML JS 文件 `Common/functions/DummyProbe.js`；
- `bin_name` / `config_dir` / `cache_dir` / `legacy_config` 全为 `null`（组件无后端脚本、
  无配置），用于覆盖引擎的「无 bin 组件」分支。

`tests/test_coexist.sh` 会把 `scripts/patch_clavis_kit.py` 与本目录的 `manifest.json`、
`qml/` 组装成一个临时的完整组件仓库（`$T/dummy-kit/`），再用引擎的
`install --repo-root` 注册它。

维护须知：改这里的 `anchor` 之前先读
`tests/fixtures/upstream-2026.9.25/Modules/Sidebars/Dashboard/infoTools/CalendarWidget.qml`
的真实文本核对，并确认它不在本仓任何 edit 的 span 之内。
