# 架构说明

## 总览

```
┌──────────────────────── Clavis Shell（Quickshell）─────────────────────────┐
│  Modules/Sidebars/Dashboard/infoTools/CalendarWidget.qml（信息页日历）      │
│      │  delegate 传 ymd                                                    │
│      ▼                                                                     │
│  DayButton（被本组件整块替换）                                             │
│      │  ChineseCalendar.dayInfo(ymd)                                       │
│      ▼                                                                     │
│  Common/functions/ChineseCalendar.js（payload，442 KB 构建产物）            │
│      └─ 内嵌 lunar-javascript 1.7.7（vendored 原文 + CommonJS shim）        │
└────────────────────────────────────────────────────────────────────────────┘
```

- **零运行时依赖**：`ChineseCalendar.js` 是 `.pragma library` 的纯计算库，
  没有 `Process`、没有网络、没有文件 IO——上游 Clavis `AGENTS.md` 明确要求展示组件不得
  创建 `Process` 或执行系统命令，本组件遵守该约束。
- **降级**：`dayInfo()` 遇到非法 `ymd` 或内部异常一律返回 `null`，QML 侧退回原样式
  （只有日号、无第二行与角标），绝不因数据层出错而让日历渲染失败。
- **多组件共存**：overlay 与多 kit 重放的语义权威是姊妹仓
  [clavis-ai-usage 的架构文档](https://github.com/LumiACGN/clavis-ai-usage/blob/main/docs/ARCHITECTURE.md#多-kit-重放引擎-v2)
  （两仓的 `scripts/clavis_overlay.py` 逐字节相同）。本文只写本组件特有的部分。

## 仓库结构

| 路径 | 作用 |
|---|---|
| `install.sh` | 顶层薄壳：转发给 overlay 引擎，外加 `--check-data` / `--with-timer` 两个糖 |
| `qml/` | **payload**：装进 Clavis 树的文件（`ChineseCalendar.js` + MIT 原文），引擎默认的 payload 目录 |
| `lunar/` | **数据层源材料**：`adapter.js`（唯一手改源码，148 行）+ `vendor/`（lunar-javascript 原文与 `SHA256SUMS`），不进 payload |
| `patches/manifest.json` | 锚点补丁的单一真相：`kit` / `upstream` / `marker` / 2 个文件共 **7 个 edit（5 exact + 2 block）** / **2 个 payload** / `gates` |
| `scripts/` | `clavis_overlay.py`（overlay 引擎 v2）、`patch_clavis_kit.py`（锚点补丁器）、`build_chinese_calendar.py`（构建产物）、`refresh-lunar-vendor.sh`、`check-lunar-data.sh`、`dev/`（冒烟、截图、lint） |
| `tests/` | `test_manifest.py`（manifest 自检）、补丁器/引擎用例、`smoke.qml`、假 HOME 端到端、`fixtures/`（上游基线副本） |
| `docs/` | 本目录 |
| `packaging/aur/clavis-lunar-calendar/` | PKGBUILD、`.SRCINFO`、`.install` 与两个 systemd 用户单元 |

`qml/` 与 `lunar/` 的分工是刻意的：**能被安装的只有 `qml/`**，`lunar/` 是重建它的原料
（构建链见 [DATA.md](DATA.md)）；因此 vendored 库以「`qml/` 里的构建产物」为交付形态，
而源材料仍在仓库里可审计、可离线重建。

## 补丁落点

两个上游文件、7 个 edit（登记顺序 = 文件位置升序，补丁器对此有机器断言）：

| # | edit id | mode | 落点 | 内容 |
|---|---|---|---|---|
| 1 | `cw-import` | exact | `CalendarWidget.qml` import 区 | 追加 `import "…/Common/functions/ChineseCalendar.js" as ChineseCalendar` |
| 2 | `cw-viewing-date` | exact | `readonly property date viewingDate: …` | 包成块：先读 `Time.now`（`qs.Services.Time`，30s tick）再返回，跨零点后重算 |
| 3 | `cw-calendar-layout` | exact | `readonly property var calendarLayout: …` | 同上——`viewingDate` 变了它就跟着重算 |
| 4 | `cw-delegate-ymd` | **block** | 日期格 delegate 的 `DayButton { … }`（`unique_line` = `day: String(parent.cell.day)`） | 给 delegate 传 `ymd: parent.cell.ymd` |
| 5 | `cw-daybutton` | **block** | `component DayButton: RippleButton { … }` 整组件 | 换成两行布局 + 休/班角标 + `StyledToolTip` |
| 6 | `cl-pad2` | exact | `calendar_layout.js` 的 `getCalendarLayout` 之前 | 插入 `_pad2()` 辅助函数 |
| 7 | `cl-cell-ymd` | exact | 每格 `"today": …` 行 | 追加 `"ymd": "YYYY-MM-DD"` 字段 |

为什么 4/5 用 **block** 模式：这两处跨度大（delegate 与整个 `DayButton` 组件），exact 模式会被
上游任何"外观类改动"（最典型的是全局字号补丁改了块内 `font.pixelSize`）击穿；block 只认结构
——`unique_line` 唯一命中 → 向上找开括号行 → 花括号配对 → `sanity` 子串校验。

**硬约束**：block edit 的 `replace` 里 `// BEGIN/END clavis-lunar-calendar` 必须落在所定位组件的
**花括号之内**（`END` 不能放到组件的 `}` 之后），否则回滚会残留标记。维护细节与失败处理见
[`patches/README.md`](../patches/README.md#block-模式维护手记)。

## 字号策略

被替换的 `DayButton` 里有三个 `readonly` 派生量，全部从主题 token 出发：

| 用途 | 表达式 | pristine 上游取值 |
|---|---|---|
| 日号 | `Typography.bodyMedium.pixelSize` | 14 |
| 第二行（农历/节气/节日） | `Math.max(8, Math.round(dayFontSize * 0.64))` | 9 |
| 休/班角标 | `Math.max(7, Math.round(dayFontSize * 0.57))` | 8 |

- 日号**不硬编码**：打了全局字号补丁（例如整树 +1px）的环境，`Typography.bodyMedium` 变大，
  本组件的两行字号按比例自动跟随，不会出现"日号 15、农历 9"的错位。
- 9 / 8 是 38 px 方形格内两行布局的标定值：`Typography` 体系里最小的 token 是 11，
  14 + 11 在 38 px 格里放不下三个汉字（第二行还有 `elide` 兜底）。
- **想微调？** 改的是 `patches/manifest.json` 里 `cw-daybutton` 的 `replace` 文本，
  而不是 overlay 里的文件——标记区内的任何手工修改都会在下一次 `--sync` 重放时被覆盖。

## 数据流

1. `calendar_layout.js:getCalendarLayout()` 逐格产出 `{ day, today, ymd }`；
   `ymd` 由**本地历**拼出（`getFullYear/getMonth/getDate` + `_pad2`），不经过 `toISOString()`，
   因此不受 UTC 偏移影响。
2. `CalendarWidget.qml` 的日期格 delegate 把 `ymd` 传给 `DayButton`（`cw-delegate-ymd`）。
3. `DayButton` 用 `ymd.length === 10` 做廉价校验后调用 `ChineseCalendar.dayInfo(ymd)`
   （`readonly property var dayInfo` 绑在 `ymd` 上：只在 `ymd` 变化时重算，适配层内另有
   2048 条缓存，翻月翻年都不会重复算）。
4. 渲染：
   - 第二行 `daySubText` ← `dayInfo.subText`，颜色按 `subKind`（`festival` primary /
     `jieqi` tertiary / `lunarMonth` secondary / `lunarDay` outline）；非当月格子（`todayState === -1`）不显示。
   - 角标 `dayBadge` ← `dayInfo.badge`（`休` / `班`；普通周末与无数据年份为空串，不显示）。
   - `StyledToolTip` ← `dayInfo.tooltip`（公历 + 星期、农历 + 干支年、节气、节日、假期与放假/调休）。
5. 降级：`dayInfo === null`（`ymd` 非法、库异常）时 `hasSub = false`、`badge = ""`、
   tooltip 为空 → 格子回到"只有日号"的原样式。首次异常会在控制台打一条
   `ChineseCalendar.dayInfo failed:` 警告（只打一次）。

## 门禁（`gates` 段）

引擎在写入后、**启用前**逐文件跑语法门禁（`scripts/clavis_overlay.py` 的 `run_gate`），
本组件的清单来自 `manifest.files[].path` + `manifest.payload[].dest`：

| 文件 | 工具 | 说明 |
|---|---|---|
| `CalendarWidget.qml` | `qmlformat` | 格式门禁；从仓库根执行时沿用根目录的 `.qmlformat.ini`（与上游一致） |
| `calendar_layout.js` | `node -e 'new Function(src)'` | 纯语法编译 |
| `Common/functions/ChineseCalendar.js` | `qmllint` | **`gates.overrides` 覆盖**：442 KB 产物用 node 编译会崩，必须用 qmllint |
| `licenses/lunar-javascript-MIT.txt` | （无） | 后缀无对应工具，跳过 |

外加**可选的 smoke 门禁**（`gates.smoke`）：把 overlay 里**已安装的那份字节** stage 成
`tests/smoke.qml` 期望的路径，用真 QML 引擎 offscreen 跑 18 条断言（闰月、节气、国庆休班、
越界降级等），`timeout_s: 60`。缺 `qml` / `qmllint`（未装 `qt6-declarative`）时引擎只告警并跳过，
本地开发建议装上（见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)）。

## 状态、备份与路径

| 路径 | 内容 |
|---|---|
| `~/.config/quickshell/clavis/` | overlay：Clavis 树镜像 + 各组件补丁与 payload（本组件：2 个锚点文件 + 2 个新增文件） |
| `~/.local/state/clavis-overlay/kits/clavis-lunar-calendar.json` | 本组件安装状态（repo 位置、order、补丁组、字节摘要、上游版本） |
| `~/.local/state/clavis-overlay/registry.json` | 引擎级共享事实（overlay/源树路径、全部已注册组件） |
| `~/.local/state/clavis-overlay/backups/` | overlay 整体快照（保留最近 3 份），`--sync` 失败时用于恢复 |

本组件没有后端脚本、没有配置目录、没有缓存目录（manifest 的 `kit.bin_name` / `config_dir` /
`cache_dir` 均为 `null`）——引擎据此跳过相应的安装与迁移步骤。
