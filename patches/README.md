# 锚点补丁：结构与维护手册

## 为什么需要补丁

Clavis Shell **没有插件系统**（Quickshell 与 Clavis 上游均无第三方注册机制），
唯一官方支持的用户扩展方式是 Quickshell 的 XDG 覆盖：
`~/.config/quickshell/clavis/` 存在时整体优先于 `/etc/xdg/quickshell/clavis/`。

因此本项目的接入方式是：**镜像整棵 Clavis 树到用户目录，再用锚点补丁把农历/节气/休班角标
"缝"进 2 个上游文件**（外加整文件安装 2 个新文件：数据层与 MIT 原文）。好处是零侵入 `/etc`
（不碰包管理器领地、Clavis 升级后一条 `--sync` 重新对齐、失败可整体回滚）。

## manifest.json 结构

| 字段 | 含义 |
|---|---|
| `kit` | 组件身份：`id`（注册表主键）、`order`（多组件重放顺序，本组件 20）、`bin_name`/`config_dir`/`cache_dir`（本组件均为 `null`——无后端脚本、无配置、无缓存） |
| `gates` | 语法门禁配置：`overrides`（按路径覆盖检查工具，`"skip"` 可跳过）与可选 `smoke` |
| `upstream.version` | 锚点所对应的 Clavis 版本（校准基线，当前 `2026.9.25`） |
| `upstream.tree_hint` | 默认目标树路径（`/etc/xdg/quickshell/clavis`） |
| `marker.begin/end` | 补丁标记模板，`{group}` 会被替换为组名（`// BEGIN clavis-lunar-calendar`） |
| `files[].path` | 要补丁的上游文件（相对树根） |
| `files[].upstream_sha256` | 该文件在基线版本下的整文件哈希（用于诊断信息） |
| `files[].edits[]` | 每处补丁：`id` / `mode`（缺省 `exact`，或 `block`）/ `anchor`（上游原文）/ `replace`（替换文本）/ `anchor_sha256`；block 另需 `block.unique_line` 等（见下） |
| `payload[]` | 整文件安装映射：`src`（本仓库 `qml/` 下的文件）→ `dest`（树内路径） |

本组件当前是 **7 个 edit（5 exact + 2 block）+ 2 个 payload**，登记在两个文件里：

| # | edit id | mode | 落点 | 内容 |
|---|---|---|---|---|
| 1 | `cw-import` | exact | `CalendarWidget.qml` import 区 | 追加 `import "…/Common/functions/ChineseCalendar.js" as ChineseCalendar` |
| 2 | `cw-viewing-date` | exact | `readonly property date viewingDate: …` | 包成块：先读 `Time.now` 再返回（跨零点重算） |
| 3 | `cw-calendar-layout` | exact | `readonly property var calendarLayout: …` | 同上 |
| 4 | `cw-delegate-ymd` | **block** | 日期格 delegate 的 `DayButton { … }` | 传 `ymd: parent.cell.ymd` |
| 5 | `cw-daybutton` | **block** | `component DayButton: RippleButton { … }` | 整组件换成两行 + 休/班角标 + `StyledToolTip` |
| 6 | `cl-pad2` | exact | `calendar_layout.js` 的 `getCalendarLayout` 之前 | 插入 `_pad2()` |
| 7 | `cl-cell-ymd` | exact | 每格 `"today": …` 行 | 追加 `"ymd"` 字段 |

同一文件内的 edits 有两条**机器断言的契约**：必须按**文件位置升序**登记、且互不重叠
（block 的回滚依赖它）。`--list-edits` 会按该顺序打印每个 edit：

```bash
python3 scripts/patch_clavis_kit.py --list-edits --manifest patches/manifest.json
```

## 标记与幂等

- 替换文本**内含** `BEGIN/END` 标记；补丁器据此判断"已打/未打"。
- 标记数 ≠ 该文件 edits 数 → 直接拒绝（绝不猜测半打补丁的文件）。
- 每次 apply **先做全树预校验**（写任何字节前所有锚点必须可精确命中一次）——
  宁可不开始，不会打一半。幂等：已打过的文件会校验并跳过。

## block 模式维护手记

每个 edit 的 `mode` 有两种：`exact`（缺省；整段 `anchor` 精确命中一次，最严）与 `block`。

**何时用 block**：上游"只改外观、不改结构"的块——例如全局字号补丁改了块内
`font.pixelSize`，exact 的 anchor 会一字之差全灭，而 block 只认结构：

```json
{
  "id": "cw-daybutton",
  "mode": "block",
  "block": {
    "unique_line": "    component DayButton: RippleButton {",
    "opener_lookback": 8,
    "sanity": ["contentItem: Text {", "font.pixelSize:", "dayButton.todayState === 1"],
    "sanity_post": ["daySubText", "ChineseCalendar.dayInfo", "dayButton.ymd.length"]
  },
  "anchor": "<上游原组件全文>",
  "replace": "<新组件全文，BEGIN/END 在花括号内>",
  "anchor_sha256": "<sha256(anchor)>"
}
```

定位流程：`block.unique_line` 必须在文件中**唯一命中** → 从该行向上最多 `opener_lookback`
行（缺省 8，可按 edit 覆盖）找以 `{` 结尾的开括号行 → 朴素花括号配对得到整块 →
校验块内包含全部 `sanity` 子串才算定位成功。**回滚（revert）阶段**用 `sanity_post`
（以及可选的 `unique_line_post`）描述"打补丁后"的文本，缺省回落 apply 用的值。

### 硬约束：BEGIN/END 必须在所定位组件的花括号内

`replace` 里的 `// BEGIN clavis-lunar-calendar` 与 `// END clavis-lunar-calendar` 必须落在
**所定位组件的花括号之内**（`END` 不能放到组件的 `}` 之后）。否则回滚时定位到的块不包含
尾部标记，会在文件里**残留一个标记**——之后标记数对不上，所有操作都会拒绝执行
（`marker count mismatch`）。把块替换按「`组件头 {` → BEGIN → 内容 → END → `}`」重排即可。
`tests/test_manifest.py` 会对 block 条目强制校验这一点。

### 三处强化（防花括号误配）

1. 提取结果的**末行** `strip()` 后必须以 `}` 结尾，否则报错（防块内字符串/注释里的花括号
   导致截断或错配）；
2. 末行缩进必须与 manifest `anchor` 的末行一致（防花括号错配导致过度提取）；
3. 报错文案区分四种失败原因（`unique_line` 命中数 / 向上找不到开括号行 / 花括号不平衡 /
   `sanity` 缺失），必要时用 `block.opener_lookback` 调整向上搜索行数。

### 漂移校准与降级为 exact

- `--list-edits`：按文件位置升序打印每个 edit 的 `id`/`mode`/定位首行，用来核对 edits
  顺序与定位文本（顺序错了补丁器会直接报错）。
- `--explain-drift [--json]`：exact 报 `anchor` 的最佳匹配窗口；block 报 `unique_line`
  命中数或定位/sanity 失败原因，并给出 ±3 行上下文。按输出更新 `unique_line` / `sanity`
  （必要时连同 `anchor` / `replace` 文本一起改）。
- **实在不行就降级**：上游把结构重写了的话（花括号无法朴素配对、组件被拆散、sanity 反复
  对不上），把那处 edit 改回 `mode: exact`——把新版上游文本原样抄成 `anchor`、相应重写
  `replace`；改完必须重跑全部测试（`python3 -m unittest discover -s tests`），
  manifest 自检会立刻暴露 `anchor`/定位声明与基线的偏差。

> 注意：**替换文本里的字号不硬编码**（日号取 `Typography.bodyMedium.pixelSize`，另外两行按比例
> 派生）。这样即使上游/rice 打了全局字号补丁，block 定位与视觉都仍然正确——不要"顺手"把 14/9/8
> 写死回替换文本。

## Clavis 升级导致 sync 失败时（标准修复流程）

```bash
# 1) 看漂移详情（会打印新版上游在锚点附近 ±3 行的实际文本）
python3 scripts/patch_clavis_kit.py --explain-drift \
  --manifest patches/manifest.json --target <你的 Clavis 源码树>
#    加 --json 可得机器可读报告（附在 issue 里用）
```

2) 打开 `patches/manifest.json`，按报告里的 `file` + `edit` 找到条目：

- 若上游只是**挪了位置/换了写法**：把 `anchor` 更新为诊断输出里对应的新文本。
  原则：**锚点尽量短且唯一**（1~3 行、含足够特征），避免整段函数。
- 若上游**动了补丁本身要插入的代码结构**（如 delegate 改了层级、`DayButton` 被拆开）：
  `replace` 里的引用片段也要跟着改，把 `// BEGIN/END` 标记保持包裹整个替换块
  （且落在组件的花括号内）。
- 更新 `files[].upstream_sha256`（改为新版文件的哈希）与 `upstream.version`。

3) 本地验证（在纯净的新版树上）：

```bash
python3 -m unittest discover -s tests -v     # 幂等/回滚/漂移/manifest 自检
python3 tests/test_manifest.py
python3 scripts/patch_clavis_kit.py --manifest patches/manifest.json --payload qml \
    --target tests/fixtures/upstream-<版本> --dry-run   # 看 diff 是否合理
python3 scripts/patch_clavis_kit.py --explain-drift --manifest patches/manifest.json \
    --target tests/fixtures/upstream-<版本>              # 期望 no drift
```

4) 提交 PR：`compat: adapt anchors to Clavis <版本号>`，并在 PR 里附 `--explain-drift --json`
   的输出作为证据。

## 校准小技巧

- 新版本的正交做法：把新版发行树里**这两个文件**放进
  `tests/fixtures/upstream-<版本>/Modules/Sidebars/Dashboard/infoTools/`，
  跑 `--target tests/fixtures/upstream-<版本> --dry-run`——这样不必动你的真实桌面
  （本仓的 `tests/fixtures/upstream-2026.9.25/` 就是这么来的，也是 `tests/test_manifest.py`
  的校验对象）。
- `--expect-upstream <版本>`：给 overlay 引擎传"当前系统版本"，与 manifest 记录不符时
  只告警不阻断——**锚点匹配才是唯一判据**，版本字符串只是提示。
- 只想验证"补丁还在不在"而不写文件：`--verify`；只想看"打完之后长什么样"：`--dry-run`。
