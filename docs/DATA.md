# 数据层：构成、边界与年度更新

日历格上的三样东西来源不同，必须分开理解：

| 显示内容 | 来源 | 能不能"算"出来 |
|---|---|---|
| 农历月日、节气、传统节日、干支年 | lunar-javascript 1.7.7（天文/历法算法） | ✅ 任意年份本地计算 |
| 公历节日（国庆节、圣诞节…）与别名 | 同上（库内置的节日表，按公历月日匹配） | ✅ 固定月日 |
| **法定节假日 / 调休（休、班角标）** | 同上，但是**库内置的数据表** | ❌ 国务院每年公布，无法推算 |

## 一、数据层怎么组出来

```
lunar/vendor/lunar-1.7.7.js   （435,942 B，上游 npm 包 lunar.js 逐字节原文，MIT）
        │
        │  构建脚本注入一行 CommonJS shim：var module = { exports: {} };
        │  （QML 的 V4 引擎里顶层 this 不可靠，强制 UMD 走 module.exports 分支，避免全局污染）
        ▼
lunar/adapter.js              （148 行，唯一手改源码，ES5，不访问 window/document/Qt）
        │
        │  构建脚本把「.pragma library 头 + vendor 原文 + 适配层」拼成一份文件
        ▼
qml/ChineseCalendar.js        （442,876 B，入库的构建产物，.pragma library）
```

- 构建命令：`python3 scripts/build_chinese_calendar.py`（默认路径全相对本仓库，可零参数运行）。
  产物头部会写明版本、`HOLIDAY_DATA_END` 与记录条数，**请勿手改产物**——它由
  `release.yml` 在发版时重跑构建并逐字节 `cmp` 校验，源与产物不自洽会让发版直接失败。
- 交付形态：`qml/` 是 payload 目录，overlay 引擎把 `qml/ChineseCalendar.js` 装到 Clavis 树的
  `Common/functions/ChineseCalendar.js`，把 `qml/lunar-javascript-MIT.txt` 装到 `licenses/`
  （映射见 `patches/manifest.json` 的 `payload` 段）。
- 适配层对外只暴露三个函数：
  - `dayInfo(ymd)` → `{ subText, subKind, badge, tooltip, festivals, jieQi, holidayName, … }`，
    非法输入或内部异常一律返回 `null`（调用方降级为原样式）。带 2048 条缓存（约 4 年翻页量）。
  - `dataRange()` → `{ vendor, version, holidayDataEnd }`（`tests/smoke.qml` 会打印它；
    命令行查版本与数据截止日用 `./install.sh --check-data`，它读的是产物头部的 `HOLIDAY_DATA_END`）。
  - `clearCache()`。
- 时区：一律 `Solar.fromYmd(整数年,月,日)`，不用 `fromDate`，避免 UTC 差一天。

## 二、数据边界（重要）

- **农历/节气/传统节日/干支**：覆盖 1900–2100（`adapter.js` 的 `_parseYmd` 界限）内的任意日期，
  一次也不用联网。
- **法定节假日/调休**：来自 lunar-javascript 里那份编码后的数据表
  （`lunar/vendor/lunar-1.7.7.js` 的 `HolidayUtil._DATA`）。当前 vendor 版本 **1.7.7**，
  表内 **860 条记录**，**数据截止 2026-10-10**（该日期由构建脚本从 `_DATA` 的最后一条记录推出，
  写进产物头部的 `HOLIDAY_DATA_END`）。查看当前值：

  ```bash
  ./install.sh --check-data       # 需 curl + jq；git clone 用户
  clavis-lunar-calendar-check-data  # AUR 用户（同一个脚本）
  ```

  表覆盖范围之外的年月（典型：2026-10-11 之后、以及次年安排公布前）**不显示休/班角标**，
  农历与节气照常显示——这是正确行为，不是 bug。

## 三、年度更新步骤（次年放假安排公布后）

国务院通常每年 11~12 月公布次年安排，上游 lunar-javascript 会随之发一个新版本
（节假日数据只在版本升级里更新，不会单独更新数据表）。更新流程（维护者视角；
只想装新数据的普通用户跑最后一步即可）：

```bash
# 1) 看上游有没有新版（只读，不改任何文件）
./scripts/check-lunar-data.sh

# 2) 下载并校验新版本 → 重建产物 → 跑 qmllint + 真引擎冒烟
./scripts/refresh-lunar-vendor.sh --version <新版本> --check

# 3) 人工确认 diff（应只有 vendor 原文、版本号、数据截止日、记录条数在变）
git diff --stat && git diff qml/ChineseCalendar.js | head -40

# 4) 提交（建议 Conventional Commits，例如
#    chore(data): lunar-javascript 1.8.0（节假日数据截止 2027-10-09））
#    发版走 CONTRIBUTING.md 的发布流程

# 5) 装进本机（或等新版包）
./install.sh --sync
```

第 2 步做的事：从 `cdn.jsdelivr.net/npm/lunar-javascript@<版本>/lunar.js` 下载 →
**校验 npm registry 的 `dist.integrity`（sha512）** → 大小 / UMD 头 / `HolidayUtil` 导出三道校验 →
把新文件放进 `lunar/vendor/` 并追加到 `SHA256SUMS` → 重建 `qml/ChineseCalendar.js` →
（`--check`）`qmllint` 产物 + 跑 `scripts/dev/smoke.sh`（真 QML 引擎过一遍 `tests/smoke.qml`
的 18 条断言）。

> `--version` 也可省略（用现有 vendor 版本离线重建产物）；`--check` 也可省略（只重建不跑门禁）。

只改数据不改逻辑的话，`lunar/adapter.js` 与 `patches/manifest.json` 都不需要动；
**不要**手工编辑 `qml/ChineseCalendar.js`。

## 四、为什么不用在线 API

- **上游约束**：Clavis 的 `AGENTS.md` 要求展示组件不得创建 `Process`、不得执行系统命令——
  运行时发 HTTP 请求（无论用 QML 的 `XMLHttpRequest` 还是拉个后端进程）都不符合该约束，
  也不是"日历"该有的代价。
- **离线可用**：日历是打开侧边栏就要显示的东西，不该受网络、代理、限流影响；
  农历与节气本身是完全确定性的计算，没有任何联网理由。
- **数据体积**：整份数据（含节假日表）随 payload 一起装进 overlay，442 KB，一次性成本。
- 结论：**运行时零网络**；联网只出现在你手动执行的 `check-lunar-data.sh` /
  `refresh-lunar-vendor.sh` 里（`curl` + `jq`，都是 optdepends，不装也能用日历）。

## 五、Roadmap（不承诺）

- **`holiday-cn` 等外部节假日数据源**：如果上游 lunar-javascript 迟迟不更新，
  计划评估引入一个独立的节假日数据文件（例如社区维护的
  [NateScarlet/holiday-cn](https://github.com/NateScarlet/holiday-cn)，MIT）作为 `HolidayUtil` 的回退层：
  数据表放仓库内、纯数据、离线读取，仍然不联网。**目前没有实现，不要按此预期使用**。
- 未来的数据层版本会保持 `dayInfo()` 的返回字段**只增不改**，`docs/` 与 CHANGELOG 会写明。

## 六、可选的年度提醒定时器

`check-lunar-data.sh` 是**只读**检查（读 vendor 版本、产物里的数据截止日、npm 上的最新版），
**不会改任何文件**——定时器只负责提醒，更新永远是你手动做的（第 3 节）。

```bash
./install.sh --with-timer      # git clone 用户：写 ~/.config/systemd/user/clavis-lunar-calendar-refresh.{service,timer}
systemctl --user list-timers clavis-lunar-calendar-refresh.timer
```

AUR 用户：单元随包安装在 `/usr/lib/systemd/user/`（不自动启用）：

```bash
systemctl --user enable --now clavis-lunar-calendar-refresh.timer
```

- 触发时间：每年 11 月与 12 月各一次（`OnCalendar=*-11-* 10:17`、`*-12-* 10:17`，
  `Persistent=true`（错过补跑）、`RandomizedDelaySec=15min`）。
- 动作：`check-lunar-data.sh --notify` —— 有新版时发一条 `notify-send` 桌面通知；
  退出码恒为 0（"有新版本"不是失败），只有缺文件/缺 `curl`/`jq` 才非 0。
- **仓库搬家后**定时器会失效（`ExecStart` 里写的是绝对路径）：重跑 `./install.sh --with-timer`
  即可；`./install.sh --verify` 会顺带检测并提示（见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)）。
