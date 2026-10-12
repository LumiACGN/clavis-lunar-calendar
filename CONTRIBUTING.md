# 贡献指南

欢迎贡献！本项目最需要帮助的两类事：**适配 Clavis 新版本的锚点漂移**、以及**数据层与展示细节的改进**
（农历/节气显示优先级、角标样式、节假日数据源）。

## 目录结构速览

| 路径 | 作用 |
|---|---|
| `qml/ChineseCalendar.js` | **构建产物**（442 KB，入库）：装进 Clavis 树的数据层，请勿手改 |
| `lunar/adapter.js` | 唯一手改源码（148 行，ES5）：对外暴露 `dayInfo(ymd)` / `dataRange()` / `clearCache()` |
| `lunar/vendor/` | vendored lunar-javascript 原文与 `SHA256SUMS` |
| `scripts/build_chinese_calendar.py` | 由 adapter + vendor 组装 `qml/ChineseCalendar.js`（头部写入版本与数据截止日） |
| `scripts/refresh-lunar-vendor.sh` | 下载/校验新版本 vendor → 重建产物 →（`--check`）门禁 |
| `scripts/check-lunar-data.sh` | **只读**检查：本地版本、数据截止日、npm 上游最新版（定时器用） |
| `patches/manifest.json` | **锚点补丁的单一真相**：`kit`/`gates` 段 + 2 个上游文件、7 个 edit、2 个 payload |
| `scripts/patch_clavis_kit.py` | 通用锚点补丁器（exact / block 两种定位模式；apply / revert / verify / explain-drift / `--list-edits`） |
| `scripts/clavis_overlay.py` + `install.sh` | overlay 安装引擎 v2（与 [clavis-ai-usage](https://github.com/LumiACGN/clavis-ai-usage) **逐字节相同**） |
| `tests/` | `test_manifest.py` 自检、补丁器/引擎用例、`smoke.qml`（18 条真引擎断言）、假 HOME 端到端、`fixtures/` 基线副本 |
| `packaging/aur/clavis-lunar-calendar/` | PKGBUILD / `.SRCINFO` / `.install` / systemd 用户单元 |

## 开发流程

```bash
git clone https://github.com/LumiACGN/clavis-lunar-calendar.git && cd clavis-lunar-calendar
git config core.hooksPath .githooks        # 启用提交前密钥扫描（需要 gitleaks）

# 改数据层逻辑：只改 lunar/adapter.js，然后重建产物
$EDITOR lunar/adapter.js
python3 scripts/build_chinese_calendar.py
./scripts/dev/smoke.sh                     # 真 QML 引擎冒烟（需要 qml，即 qt6-declarative）

# 全量测试
python3 -m unittest discover -s tests -v
python3 tests/test_manifest.py
tests/test_overlay.sh && tests/test_smoke.sh && tests/test_coexist.sh

# 装进本机验证（会停/起 clavis-shell；只改 overlay，不碰 /etc）
./install.sh --sync
```

改动**必须**遵守的契约：

- `lunar/adapter.js` 只允许 ES5，不访问 `window` / `document` / `Qt` / `Process`，不联网；
  任何异常都要降级为 `null`（调用方会退回原样式），绝不让 QML 绑定炸掉。
- **改了 `lunar/adapter.js` 就必须重跑构建脚本并提交 `qml/ChineseCalendar.js`**：
  `release.yml` 会重跑构建并逐字节 `cmp`，源与产物不自洽会让发版直接失败。
- `dayInfo()` 的返回字段**只增不改**（消费方是 QML 里的 `readonly property var dayInfo`）。

## 数据层更新

第二年放假安排公布后的更新链（`check-lunar-data.sh` → `refresh-lunar-vendor.sh --version <新> --check`
→ `git diff` → 提交 → `./install.sh --sync`）详见 [docs/DATA.md](docs/DATA.md#三年度更新步骤次年放假安排公布后)。
一句话：**换 vendor、重建产物、跑门禁、提交**，不要手工编辑产物。

## 锚点维护：Clavis 升级后 sync 失败了怎么办

1. 用漂移诊断工具查看详情（会打印每个锚点在**新版上游**中的实际文本）：
   ```bash
   python3 scripts/patch_clavis_kit.py --explain-drift \
     --manifest patches/manifest.json --target <你的 Clavis 源码树>
   ```
   加 `--json` 可得到机器可读报告（用于附在 issue 里）。
2. 打开 `patches/manifest.json`，按错误信息里的 `id` 找到对应条目；
   用诊断输出中的新文本更新 `anchor`（保持锚点短且唯一），必要时同步调整 `replace`。
   block 模式（`mode: "block"`，本仓有两处：`cw-delegate-ymd` / `cw-daybutton`）改的是
   `block.unique_line` / `block.sanity` 一类结构定位参数，维护细节见
   [`patches/README.md`](patches/README.md#block-模式维护手记)。
3. 跑测试确认幂等：
   ```bash
   python3 -m unittest discover -s tests -v
   python3 tests/test_manifest.py
   ./install.sh --verify
   ```
4. 提交 PR，标题格式：`compat: adapt anchors to Clavis <版本号>`。

## 两种定位模式（exact / block）

manifest 里每个 edit 都有 `mode`（缺省 `exact`）：

- **exact**：整段 `anchor` 必须在上游文件里**精确命中一次**。最严：一字不差，上游任何相关改动
  都会让它失配报错——适合短行与希望"一漂移就报错"的长块。
- **block**：结构块定位——`block.unique_line` 唯一命中 → 向上找开括号行（`block.opener_lookback`，
  缺省 8）→ 花括号配对 → 用 `block.sanity` 子串校验。对上游**只改外观不改结构**的改动鲁棒
  （例如全局字号补丁改了块内 `font.pixelSize`，exact 会全灭而 block 照常定位）。
  **硬约束**：`replace` 里的 BEGIN/END 标记必须落在所定位组件的花括号内，否则回滚会残留标记。

同一文件内的多个 edit 还有一条机器断言契约：**必须按文件位置升序登记、且互不重叠**
（block 回滚依赖它）。用 `--list-edits` 打印「文件 → edit id / mode / 定位首行」核对：

```bash
python3 scripts/patch_clavis_kit.py --list-edits --manifest patches/manifest.json
```

当前 7 个 edit 的清单见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#补丁落点)。

## 代码风格

- QML：仓库根 `.qmlformat.ini`（与上游 Clavis 一致）；`qmlformat <file>` 可查格式。
- Shell：`shellcheck -S warning`；`bash -n` 过一遍语法。
- Python：仅标准库，`python3 -m py_compile scripts/*.py`。
- 文档、注释、提交信息一律中文；**不要**在仓库里出现真实姓名/邮箱/凭据（pre-commit 与 CI 会扫）。

## 发布流程

版本号有**三处必须一致**（`release.yml` 会逐个校验，不一致直接红）：

| # | 位置 | 说明 |
|---|---|---|
| 1 | `patches/manifest.json` 的 `tool_version` | 也是 overlay 记录里显示的组件版本 |
| 2 | `CHANGELOG.md` 的 `[x.y.z]` 小节 | Keep a Changelog 风格，标题带日期 |
| 3 | `packaging/aur/clavis-lunar-calendar/PKGBUILD` 的 `pkgver` | 和 tag 一致 |

```bash
# 1) 更新上面三处 + CHANGELOG 小节；提交
# 2) 打 tag 并推送（触发 release.yml）
git tag v0.1.0 && git push origin v0.1.0
```

`release.yml` 会：校验 tag == manifest 版本 / CHANGELOG 有该小节 / PKGBUILD `pkgver` 一致 →
跑产物自检（`.pragma library` 头、`HOLIDAY_DATA_END`、`LUNAR_JS_VERSION`、`dayInfo`，
以及**重跑构建并逐字节比对**）→ `git archive` 出 `clavis-lunar-calendar-<ver>.tar.gz` +
`SHA256SUMS` → 创建 GitHub Release。

发版后（AUR 跟进）：

```bash
cd packaging/aur/clavis-lunar-calendar
# 从 Release 页的 SHA256SUMS 回填 PKGBUILD 的 sha256sums
makepkg --printsrcinfo > .SRCINFO
```

`.SRCINFO` 是生成物，**永远用 `makepkg --printsrcinfo` 重新生成**，不要手改
（发版前 `sha256sums=('SKIP')` 是预期的；回填校验和后要重新生成一次）。
