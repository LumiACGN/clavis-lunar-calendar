# 兼容性与升级

## 为什么是 overlay + 锚点补丁

- Quickshell（0.3.x）**没有插件系统**；Clavis Shell 也没有第三方扩展 API。
- 唯一官方支持的用户扩展机制是 **XDG 覆盖**：
  `~/.config/quickshell/clavis/` 存在时**整体优先**于 `/etc/xdg/quickshell/clavis/`
  （Quickshell 按 XDG 顺序取第一个含 `<name>/shell.qml` 的目录，**整目录独占，不合并**）。
- 所以本项目把系统 Clavis 树**完整镜像**到 overlay 目录，再用锚点把农历/节气/休班角标
  缝进 **2 个上游文件**（外加 2 个新增文件：数据层与 MIT 原文）。**绝不写 `/etc`**。
- 同机的多个 clavis 组件共享这一个 overlay：引擎按各自的 `order` 重放全部已注册组件
  ——「两个组件互相覆盖 overlay」不再是问题（引擎语义见
  [clavis-ai-usage 的架构文档](https://github.com/LumiACGN/clavis-ai-usage/blob/main/docs/ARCHITECTURE.md#多-kit-重放引擎-v2)）。

> ⚠️ overlay 是全树遮蔽：生效期间 `/etc` 里的 Clavis 副本被完全忽略（包括你自己对它打过的
> 其它补丁）。Clavis 升级后 **必须跑一次 `./install.sh --sync`** 把 overlay 对齐到新版本，
> 否则你会继续跑旧代码（`--verify` 会提醒）。任一组件的 `--sync` 都会重放全部组件。

## 版本兼容表

| Clavis 版本 | 状态 | 说明 |
|---|---|---|
| 2026.9.25 | ✅ 基线 | `patches/manifest.json` 以此为锚点校准 |
| 其它版本 | ⚠️ 未验证 | 先跑 `./install.sh --sync --dry-run` 看锚点是否仍匹配 |

- 锚点校准基线是 **clavis-shell 2026.9.25**，对应的两个上游文件逐字节副本放在
  `tests/fixtures/upstream-2026.9.25/`（sha256 与 manifest 的 `upstream_sha256` 一致）：

  | 文件 | sha256 |
  |---|---|
  | `Modules/Sidebars/Dashboard/infoTools/CalendarWidget.qml` | `1a695db029c4162833c171fafed834681a652e94d0745faeedd627f2216f21f3` |
  | `Modules/Sidebars/Dashboard/infoTools/calendar_layout.js` | `1f3c315bcb24688badebf41fa1756ba600cee3865c3e3587300084a2ee63681e` |

- 版本探测顺序：`pacman -Q clavis-shell` → 源码树 `RELEASE.json` → `unknown`。
  版本字符串只做提示；**锚点是否精确匹配才是唯一判据**。

## block 模式带来的鲁棒性

7 个 edit 里有 2 个是 **block** 模式（`cw-delegate-ymd`、`cw-daybutton`），它们只认结构
（`unique_line` 唯一命中 + 花括号配对 + `sanity` 子串），不要求逐字节一致。因此下面这类
**"只改外观、不改结构"**的上游改动**不会**让 `--sync` 失败：

| 上游改动 | exact 锚点 | block 锚点 |
|---|---|---|
| 全局字号补丁（块内 `font.pixelSize` 14 → 15） | ❌ 一字之差全灭 | ✅ 仍能定位 |
| 改颜色 token / 调 `implicitWidth` / 加 `Behavior` 动画 | ❌ | ✅ |
| 重命名 `DayButton`、拆散组件、换掉 delegate 结构 | ❌ | ❌（会响亮失败，见下） |

> 本组件在替换块里刻意**不硬编码字号**（日号取 `Typography.bodyMedium.pixelSize`，另外两行按比例派生），
> 所以就算上游或你的 rice 打了全局字号补丁，第二行与角标也会自动跟着变——见
> [ARCHITECTURE.md 的字号策略](ARCHITECTURE.md#字号策略)。

## 会失效的情况与处理

上游若改写了这两处结构（换组件名、delegate 改成别的元素、`DayButton` 被拆开），
`--sync` 会**响亮失败**（先整体恢复旧 overlay，桌面继续可用），错误文案类似
`anchor matched 0 times` / `block 定位失败`。修复流程：

```bash
# 1) 看漂移详情（打印新版上游在锚点附近的实际文本，附机器可读 JSON）
python3 scripts/patch_clavis_kit.py --explain-drift \
    --manifest patches/manifest.json --target <你的 Clavis 源码树> --json

# 2) 按输出更新 patches/manifest.json（exact 改 anchor/replace；
#    block 改 unique_line/sanity，实在不行降级为 exact）
#    详细步骤：patches/README.md

# 3) 把新版上游的两个文件副本放进 tests/fixtures/upstream-<新版本>/
#    并更新 manifest 的 upstream.version / upstream_sha256

# 4) 本地验证
python3 -m unittest discover -s tests -v
python3 tests/test_manifest.py
./install.sh --sync --dry-run
```

仓库里还有 **compat-watch** 工作流（`.github/workflows/compat.yml`，**仅手动触发**：
Actions 页 → compat-watch → Run workflow）——它会拉取上游最新 release，对这两个文件跑一遍
补丁器，漂移时自动开 issue 预警。

## 上游若自带农历：本组件的退休路径

Clavis 未来若内置农历/节气/休班角标，本组件没有存在的必要。届时按这个顺序退场：

1. `./install.sh --uninstall` 移除本组件（其它 clavis 组件不受影响）；
2. 确认上游版本真的覆盖了你要的东西（农历、节气、法定节假日休班、跨零点跟随）；
3. 仓库归档（README 顶部标注「已由上游内置功能取代」），**不删** —— 老版本 Clavis 用户
   与历史 reference 仍需要它；
4. AUR 包跟进 `pre_remove` 提示或直接删除包。

compat-watch 在锚点漂移时开的 issue 正好是退休信号：如果漂移是因为上游重写了日历并加了农历，
那就走这条路径，而不是去修锚点。

## 升级流程（Clavis 升级后）

```bash
./install.sh --verify           # 会提示"Clavis 已升级但 overlay 陈旧"
./install.sh --sync --dry-run   # 预览：看补丁器对当前源树的 diff
./install.sh --sync             # 正式对齐（停 shell → 重镜像 → 重打补丁 → 门禁 → 起 shell）
```

`--sync` 的安全承诺（与姊妹仓一致）：

- 动手前把**当前 overlay 整体快照**到 `~/.local/state/clavis-overlay/backups/`（保留最近 3 份）；
- 若你手动改过 overlay 文件（哈希对不上），会**拒绝覆盖**，`--force` 才继续（仍先备份）；
  被兄弟 kit 补丁改写的共享文件不算手改，不会拦你；
- 补丁失败或语法门禁/smoke 不过时，**自动恢复 sync 前的 overlay 并把你外壳拉起来**，
  而且**一个 kit 状态文件都不写**——降级策略是"继续跑旧版"，绝不把桌面弄崩。

> 本组件还多一道保险：`Common/functions/ChineseCalendar.js` 的语法门禁是 `qmllint`
> （442 KB 产物用 node 编译会崩），smoke 门禁是 18 条真引擎断言——两者都通过才会被启用。

## 卸载与回退

```bash
./install.sh --uninstall          # 只移除本组件（其它已注册组件保持完好）
./install.sh --uninstall --all    # 整体拆除：所有组件 + overlay（状态与备份保留）
```

本组件没有配置目录与缓存目录，所以不需要 `--purge`；`--all --purge --yes` 会连同
引擎状态与备份一起删除（多组件场景下慎用，它管的是引擎级目录）。

AUR 用户：先 `clavis-lunar-calendar-overlay uninstall`（或 `uninstall --all`），再
`pacman -Rns` 卸包。
