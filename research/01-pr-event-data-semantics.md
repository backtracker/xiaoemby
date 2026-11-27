# 01 — PR 事件的数据语义与质量边界

> Context pointer：本调研来自工单 `.scratch/daily-recommend-v3/issues/01-pr-event-data-semantics.md`
> 关联上下文：`.scratch/daily-recommend-v3/map.md`、`docs/daily-recommendation-design.md`、`docs/daily-recommendation-design-v2.md`、`xiaomusic/utils/daily_recommend.py`、`xiaomusic/utils/emby_utils.py`
> 调研日期：2026-11-27；只读调研，未改代码、未写库。
> 凭据说明：`emby_host` / `emby_api_key` / `emby_user_id` 从 `conf/setting.json` 在进程内读取，本文不记录任何凭据、Token、主机地址或完整用户 ID。

---

## 0. 结论速览

| # | 问题 | 结论 | 强度 |
|---|---|---|---|
| 1 | `PlayDuration` 语义 | **实际播放秒数**，不是曲目总时长；`DateCreated` 是播放**开始**时刻。1030 秒行是 1425 秒的剧集播了 72%，不是总时长 | 强（全量 61 行 + 37 个 Item 对账） |
| 2 | 26 条 `UserId=NULL` 且 `PlayDuration=0` | **不是「0 秒跳过」**，而是整段会话的 `UserId` 与时长**上报丢失**；墙钟间隔与曲长吻合，说明当时确实在放。**不能作负反馈证据，也不能归属到账号** | 中强（间隔推断 + Emby 状态对账） |
| 3 | `PlayCount` 全为 0 | **既不是插件未写、也不是 Emby 未同步**：PR 表根本没有 `PlayCount` 列；Emby 侧只要显式请求 `Fields=UserDataPlayCount` 就有值。当前代码漏请求该 Field，读到的才恒为 0 | 强（同一批 Item 两种 Fields 对照） |
| 4 | `ItemId + 60 秒窗口` 合并 | 方向正确，但**不能只看时间**：必须加「上一段未播完」条件，否则会把短曲重播并成一次；建议改为**按上一段结束时刻**算 gap，并限定同一用户/设备 | 强（10 组相邻同曲对 + Emby PlayCount 反证） |
| 5 | `DateCreated` 无时区 | 服务器本地时间 **UTC+8**，TEXT、7 位小数秒、无时区标记；按 UTC 分天会把早 7–8 点的收听划到前一天。统一按 `Asia/Shanghai (+08:00)` 解析 | 强（SQLite 时钟对照 + Emby `LastPlayedDate` 逐行对账） |

**对 v3 最要命的两条**：

1. `PlayCount=0` 是**取数 Bug**，v2「PlayCount 不可用」的前提不成立；补上 `UserDataPlayCount` 后可恢复「生命周期播放次数」信号。
2. 2026-11-26 早间整段小爱音箱收听（8 个曲目块、18 行）全部是 `UserId=NULL / PlayDuration=0`，但墙钟上与曲长吻合——**PR 会整段丢时长**。v3 必须把「缺失」当缺失，不能当 0 秒或跳过。

---

## 1. 调研范围与方法

### 1.1 数据窗口

| 项 | 值 |
|---|---|
| 表 | `PlaybackActivity`（PR 插件自建 SQLite；库内只有 `PlaybackActivity` 与 `UserList` 两张表，后者为空） |
| 行数 | **61 行（全量拉取，不是抽样）** |
| 时间范围 | `2026-11-25 22:03:58` → `2026-11-27 12:48:59`（服务器本地时间，约 39 小时） |
| 用户 | 当前账号 `nick`：35 行；`UserId=NULL`：26 行 |
| 类型 | `Audio` 57 行；`Episode` 4 行 |
| 去重 Item | 37 个（33 Audio + 4 Episode），全部通过 `GET /emby/Users/{id}/Items` 补齐 `RunTimeTicks` 与 `UserData` |

> 窗口只有 2 天，且含诊断/探针数据（`Probe Artist`、`diag-*` 设备名）。**字段语义结论可信；行为比例、阈值标定不可信**，不要拿这 61 行去估「用户偏好分布」。

### 1.2 只读接口

```text
POST {emby_host}/emby/user_usage_stats/submit_custom_query
  header: X-Emby-Token: <key>
  body:   {"CustomQueryString":"<SELECT ...>","ReplaceUserId":false}

GET {emby_host}/emby/Users/{user_id}/Items
  header: X-Emby-Token: <key>
  params: Ids=<逗号分隔>, Fields=RunTimeTicks,UserDataPlayCount,UserDataLastPlayedDate
```

### 1.3 复现 SQL（不含凭据）

```sql
-- 表结构
PRAGMA table_info(PlaybackActivity);
-- 结果字段：DateCreated, UserId, ItemId, ItemType, ItemName,
--          PlaybackMethod, ClientName, DeviceName,
--          PlayDuration, PauseDuration, RemoteAddress, TranscodeReasons
-- 注意：没有 PlayCount 列。

-- 总量 / 窗口
SELECT COUNT(*), MIN(DateCreated), MAX(DateCreated) FROM PlaybackActivity;

-- 用户与类型
SELECT UserId, COUNT(*), SUM(PlayDuration) FROM PlaybackActivity GROUP BY UserId;
SELECT ItemType, COUNT(*) FROM PlaybackActivity GROUP BY ItemType;

-- 关键相关性：0 秒 ⇔ UserId 为空
SELECT SUM(CASE WHEN PlayDuration=0 THEN 1 ELSE 0 END) AS zero_rows,
       SUM(CASE WHEN UserId IS NULL THEN 1 ELSE 0 END) AS null_user_rows
FROM PlaybackActivity;

-- 相邻同曲对：start_gap 与 end_gap（窗口函数，服务端 SQLite 支持）
SELECT ItemId, DateCreated, PlayDuration,
  LAG(DateCreated) OVER (PARTITION BY ItemId ORDER BY DateCreated) AS prev_dt,
  LAG(PlayDuration) OVER (PARTITION BY ItemId ORDER BY DateCreated) AS prev_dur,
  ROUND((julianday(DateCreated)
       - julianday(LAG(DateCreated) OVER (PARTITION BY ItemId ORDER BY DateCreated)))*86400, 1) AS start_gap_s,
  ROUND((julianday(DateCreated)
       - julianday(LAG(DateCreated) OVER (PARTITION BY ItemId ORDER BY DateCreated)))*86400
       - LAG(PlayDuration) OVER (PARTITION BY ItemId ORDER BY DateCreated), 1) AS end_gap_s
FROM PlaybackActivity
WHERE UserId IS NOT NULL AND ItemType='Audio'
ORDER BY DateCreated;

-- 服务器时钟 / 本地时区
SELECT datetime('now') AS utc_now,
       datetime('now','localtime') AS local_now,
       typeof(DateCreated) AS t
FROM PlaybackActivity LIMIT 1;

-- 日 / 小时聚合（按服务器本地时间）
SELECT date(DateCreated) d, COUNT(*), SUM(PlayDuration)
FROM PlaybackActivity GROUP BY d;

SELECT strftime('%H', DateCreated) h, COUNT(*)
FROM PlaybackActivity WHERE UserId IS NOT NULL GROUP BY h ORDER BY h;
```

### 1.4 曲长补全

37 个 ItemId 批量拉取（一次 GET，非逐条）：

```text
GET /emby/Users/{id}/Items?Ids=<37 ids>&Fields=RunTimeTicks,UserDataPlayCount,UserDataLastPlayedDate
秒 = RunTimeTicks / 1e7
```

---

## 2. 字段 → 可信度 → 用法（事实表）

| 字段 / 来源 | 实测语义 | 可信度 | v3 用法与限制 |
|---|---|---|---|
| `DateCreated`（PR） | 播放**开始**时刻；服务器本地时间；TEXT；格式 `YYYY-MM-DD HH:MM:SS.fffffff`（7 位小数秒）；**无时区标记** | **高** | 会话起点、按天/时段聚合。统一按 `+08:00` 解释；不要用 UTC 日期 |
| `UserId`（PR） | 播放会话所属用户；本窗口内 `NULL` 出现 26 次，且与非零时长完全互斥 | **高（有值）；NULL 不可用** | `WHERE UserId = 当前账号`；`NULL` 行直接丢弃，不做归属推断 |
| `ItemId`（PR） | Emby Item 唯一 ID | **高** | 唯一键；与 Emby Items / 曲库 join；不要用 `ItemName` 当键 |
| `ItemType`（PR） | `Audio` / `Episode` | **高** | **必须** `ItemType='Audio'`；本窗口 4 行 Episode（6.6%）会污染音乐画像 |
| `ItemName`（PR） | `Artist - Title (Album)` 拼接串 | 中 | 仅展示/日志；可能重名、含别名，不作身份键 |
| `PlaybackMethod`（PR） | `DirectPlay` / `DirectStream` | **高** | 辅助识别切段：同曲第一段常为 `DirectStream`，第二段为 `DirectPlay`；但非绝对（存在全 `DirectStream` 对） |
| `ClientName`（PR） | 客户端名（`XiaoEmby` / `RustMusic` / `Vidzer` / `Emby for Android`） | 中 | **仅作过滤**；客户端自报，可伪造/被测试污染（`diagplay` 等） |
| `DeviceName`（PR） | 设备名（`小爱音箱` / `RONG的笔记本` / `Windows` / `diag-*`） | 中 | **仅作过滤**；同上，且同一账号多设备并行时会混入 |
| `PlayDuration`（PR） | **实际播放秒数**（墙钟），不是曲目总时长；0 = 未记录，不等于「播了 0 秒」 | **高（>0 时）；0 不可信** | 时长、完成度、短播放判断。必须先合并切段；0 值整行丢弃 |
| `PauseDuration`（PR） | 暂停秒数；61 行中仅 2 行非 0（45 / 72） | 低 | 暂不使用；与 `PlayDuration` 是否互斥**未知** |
| `RemoteAddress`（PR） | Docker 网桥 / 公网 / 局域网 IP | 低 | 不用；不能稳定标识设备 |
| `TranscodeReasons`（PR） | 全为 `NULL` | 无 | 不用 |
| `RunTimeTicks`（Emby Items） | 曲目/剧集总长（1 tick = 100ns） | **高** | 完成度分母；缺失时降级为「仅时长阈值」 |
| `LastPlayedDate`（Emby Items） | UTC、带 `Z`；通常等于本次**逻辑播放第一段的起点**（切段对的第二段不刷新）；2 条 0 秒行为 +60s 的例外，见 Q2 | **高**（有值时），但需 `Fields=UserDataLastPlayedDate` | 与 PR 交叉校验；兜底时间戳。**不要**请求 `Fields=UserData` 后读它（会得到 `null`） |
| `PlayCount`（Emby Items） | 生命周期播放次数（含 PR 窗口之外的旧播放）；切段对只计 1 次；短播放也会计 | **高**，但需 `Fields=UserDataPlayCount` | 熟悉度/长期偏好；**不是**窗口内次数，不能当「最近播放次数」 |
| `PlayedPercentage`（Emby Items） | 61 个 Item 里仅 1 个有值（17.5%，且与 167/1425≈11.7% 不符） | 低 | 不用 |
| `PlayCount`（PR 表） | **不存在该列** | — | 「PR 里 PlayCount 全 0」是伪命题 |

---

## 3. 逐题结论

### Q1. `PlayDuration` 是否确为「实际播放秒数」？

#### 【事实】

1. **单行不可能超过曲长**：37 个 Item 逐一对照，没有一行 `PlayDuration > RunTimeTicks/1e7`。
2. **同一 Item 出现多个不同时长**，只能是实际播放：
   - Episode `1058397`（`手长与脚长`）：`RunTimeTicks` = 1425.0s；11-26 播了 **305s**，11-27 播了 **1030s**。两次都 `< 1425`。
   - `999148`（`一颗情泪`，202.0s）：29s + 20s。
   - `973606`（`Oh! Oh! Oh!.Remix`，272.5s）：18s + 132s。
3. **1030 秒异常行已定位**：它是 **Episode**，不是歌曲；曲长 1425s，1030s = 72.3%，是「看了 17 分钟」而非「总时长 1030」。同一条目 11-26 还有一条 305s，进一步排除「总时长」解释。
4. **完整播放的切段求和 ≈ 曲长**（误差 ≤ +0.9%）：

   | ItemId | 曲长(s) | 各段(s) | 合计 | 合计/曲长 |
   |---|---|---|---|---|
   | 973820 | 302.8 | 26 + 277 | 303 | 1.001 |
   | 1000023 | 255.8 | 22 + 234 | 256 | 1.001 |
   | 1010164 | 282.7 | 25 + 260 | 285 | 1.008 |
   | 1003353 | 168.1 | 19 + 150 | 169 | 1.005 |
   | 1006702 | 209.1 | 19 + 192 | 211 | 1.009 |
   | 973613 | 290.9 | 27 + 264 | 291 | 1.000 |
   | 982121 | 235.4 | 15 + 221 | 236 | 1.002 |

5. **`DateCreated` 是开始时刻，且 `PlayDuration` 是墙钟时长**：对 34 组「上一行时长 > 0」的相邻行，用 `下一行开始 - (上一行开始 + 上一行时长)` 计算余量，**22 组在 ≤5s 内**，26 组在 ≤60s 内。典型：
   - 973820：`07:51:01 + 26s ≈ 07:51:27`，第二行 `07:51:28`，end_gap = **0.8s**；第二行 `07:51:28 + 277s ≈ 07:56:05`，下一首 `07:56:06`。
   - 1058397：`12:07:27 + 1030s = 12:24:37`，下一行正好 `12:24:37`。
6. **Emby 侧独立印证**：抽查多个 Item，`LastPlayedDate`（UTC）与 PR 的 `DateCreated - 8h` 一致，例如 973820 的 `2026-11-26T23:51:01Z` ↔ `2026-11-27 07:51:01`；1006702 的 `00:08:49Z` ↔ `08:08:49`；1008768 的 `04:48:59Z` ↔ `12:48:59`。

#### 【推断】

- `PlayDuration` = 从开始到停止的实际播放秒数；第二段通常是**从第一段结束位置续播**（否则合计不会刚好≈曲长，且 Emby 会把切段对计为 1 次播放）。
- 合计略高于曲长（+0.5%~0.9%）是上报/取整/停止事件延迟导致，做「是否播完」判断时应允许 95%~105% 容差，或对合计做 `min(sum, runtime)`。
- `PauseDuration` 非 0 的 2 行（267s/45s、305s/72s）无法与曲长/下一行间隔对齐，**无法判断 `PlayDuration` 是否已扣除暂停**。

#### 【未知】

- 暂停期间是否计入 `PlayDuration`（样本仅 2 行，且后面都有长间隔）。
- 缓冲/卡顿是否计入；播放失败但已产生时长的行为。
- `PlayDuration` 的最大精度（是否按秒截断）；本窗口全为整数秒。

#### 结论

**可用。** `PlayDuration > 0` 且 `UserId` 非空时，可当作实际播放秒数；完成度 = `min(合计, 曲长) / 曲长`。

---

### Q2. 26 条 `UserId=NULL` 且 `PlayDuration=0` 是什么？

#### 【事实】

1. **26 条 0 秒行 = 26 条 NULL 用户行，完全同一集合**（本窗口内 `PlayDuration=0 ⇔ UserId IS NULL`）。
2. 构成：

   | 来源 | 行数 | 时间 | 形态 |
   |---|---|---|---|
   | XiaoEmby / 小爱音箱 | 18 | 2026-11-26 07:58:54–08:24:22 | 8 个曲目块；每块 2–3 行（先 `DirectStream` 后 `DirectPlay`） |
   | XiaoEmby / `diagplay`、`diag-lowcase-001`、`diag-body-002` | 3 | 2026-11-26 10:37–10:41 | 同一 ItemId=1061526；其中两行同一秒、不同设备 |
   | RustMusic / RONG的笔记本 | 5 | 2026-11-27 11:25:15–11:29:33 | 每曲 1 行，间隔 41–99s |

3. **11-26 早间那 18 行不是「0 秒播放」**：把每个曲目块的首行到下一块首行的墙钟间隔，与该曲 `RunTimeTicks` 对比：

   | 块首时间 | ItemId | 行数 | 到下一块(s) | 曲长(s) | 比值 | 判读 |
   |---|---|---|---|---|---|---|
   | 07:58:54 | 988874 白鸽 | 3 | 385 | 365.9 | 1.05 | 接近整曲 |
   | 08:05:19 | 985916 大千世界 | 2 | 259 | 257.4 | 1.006 | 整曲 |
   | 08:09:38 | 1060549 小时候 | 3 | 59 | 139.7 | 0.42 | 中途切 |
   | 08:10:37 | 971527 Flowerwall | 2 | 301 | 298.4 | 1.009 | 整曲 |
   | 08:15:38 | 971475 Crazy In Love | 2 | 230 | 228.3 | 1.007 | 整曲 |
   | 08:19:28 | 971402 Ophelia | 2 | 44 | 406.4 | 0.11 | 中途切 |
   | 08:20:12 | 972513 불꽃 | 2 | 236 | 233.3 | 1.011 | 整曲 |
   | 08:24:08 | 1008998 舞娘 | 2 | — | 186.8 | — | 之后无同段数据 |

   若这些真是「播了 0 秒」，下一首应在几秒内开始；实际隔了 230–385 秒，且与曲长吻合。**说明当时确实在播放，只是 `PlayDuration` 和 `UserId` 都没写进来。**

4. **Emby 侧也没有为这段会话记账**：上述 8 个 Item 的 `LastPlayedDate` 均未更新到 2026-11-26（为 `NULL` 或 2020/2024/2025 的旧值）；`PlayCount` 也未增加。
5. **RustMusic 那 5 行更矛盾**：块间隔为 63 / 99 / 55 / 41 秒；其中 2 个 Item 的 Emby `LastPlayedDate` 正好等于 PR 开始时间 **+60s**（1023023、1058052），另外 3 个未更新/为 `NULL`。即「0 秒行」并不等于「Emby 没有记录」。
6. **Emby 侧确实存在无用户归属的播放会话**：`GET /emby/Sessions` 当前 22 个会话中多条 `UserId=NULL`（`nas-tool`、`Emby Server DLNA`、`for xiaoem_emby`、`XiaoEmby/diag-*`）；同一设备名 `RONG的笔记本` 同时存在「有用户」和「无用户」会话。因此 PR 的 `UserId=NULL` 不是异常编码，而是对应了 Emby 层面未解析用户的会话。

#### 【推断】

- 这 26 行是**上报缺失**（整个会话的 `UserId` 与 `PlayDuration` 丢失），而不是「用户播放了 0 秒」或「明确跳过」。
- 最可能的机制：PR 插件的会话在结束时没能拿到用户上下文/停止事件，于是落库为 `NULL` + `0`；11-26 早间整段会话都处于这个失败模式（连续 18 行），说明是**系统性失败**，不是随机噪声。
- 11-26 早间是一次真实的小爱音箱收听（至少 5 首接近整曲），但 v3 从 PR 只能看到「无归属的 0 秒行」。

#### 【未知】

- 根因：插件版本/会话解析/停止事件丢失/客户端未上报 PlaybackStop，无法从只读数据区分。
- 为什么 11-26 整段失败、11-27 同一设备同一客户端正常；是否有过一次插件重启或配置变更。
- 这些行是否会在后续被插件回填（本窗口内没有）。
- RustMusic 的 5 行到底是「丢失时长」还是「客户端只报了 start」，`+60s` 的 LPD 无法用现有语义解释（与其它行「LPD=开始时刻」矛盾）。

#### 结论

- **不可作负反馈证据**：没有时长、没有用户归属，且其中至少一部分实际上是完整播放。
- **也不可作曝光证据**：无法归属到当前账号；`diag-*` 三行还是诊断数据。
- 正确处理：**当作缺失丢弃**，并单独计数「缺失会话」用于监控数据覆盖率；不要把它们折算成 0 秒或「跳过」。

---

### Q3. `PlayCount` 全为 0：插件未写入还是 Emby 未同步？

#### 【事实】

1. **PR 表没有 `PlayCount` 列**。`PRAGMA table_info(PlaybackActivity)` 只有 12 个字段，`PlayCount` 不在其中。所以「PR 的 PlayCount 全 0」不成立——它根本不存在。
2. **Emby 的 `PlayCount` 是有值的，但必须显式请求 Field**。同一批 Item、同一个用户：

   | 请求的 `Fields` | 973820 | 1000023 | 1006702 |
   |---|---|---|---|
   | `Genres,ProductionYear,Path,UserDataLastPlayedDate`（当前代码） | pc=0, lpd=正确 | pc=0, lpd=正确 | pc=0, lpd=正确 |
   | `UserDataPlayCount` | **pc=4** | **pc=2** | **pc=1** |
   | `UserDataPlayCount,UserDataLastPlayedDate` | **pc=4**, lpd=正确 | **pc=2**, lpd=正确 | **pc=1**, lpd=正确 |
   | `UserData`（宽泛值） | pc=0, lpd=null | pc=0, lpd=null | pc=0, lpd=null |

   Emby 只 hydrate 明确列出的 user-data 字段；当前 `DETAIL_FIELDS = "Genres,ProductionYear,Path,UserDataLastPlayedDate"` 漏了 `UserDataPlayCount`，所以 `_parse_recommend_item()` 读到的 `UserData.PlayCount` 恒为 0。
3. **`PlayCount` 是生命周期累计，不是窗口内次数**：
   - `972513` pc=5，但 LPD 是 2025-05-22（本次 11-26 的 0 秒行没有增加它）。
   - `973613` pc=11、`982121` pc=7，均远高于 PR 两天的播放次数。
4. **`PlayCount` 按「逻辑播放」计数，切段对只算 1 次**：
   - `1006702`：11-27 有 2 行（19s + 192s），Emby `PlayCount=1`，且 LPD 指向第一段起点 `08:08:49`（不是第二段 `08:08:49+19s`）。
   - `999148`：29s + 20s（合计 49s / 曲长 202s，明显没播完），`PlayCount=1` → 未播完也算一次。
   - 反例 `1078409`：2 秒的探针曲连播两次（间隔 2s），`PlayCount=2` → **完整播放后的重播算两次**。
5. **短播放也会计入**：`970725` 只播 13s，LPD 仍更新为 `2026-11-26T05:41:16Z`（本地 13:41:16），pc=1。

#### 【推断】

- `PlayCount` 的语义是「Emby 记账的播放会话数」，与是否播完无关；切段续播算同一次，完整重播算新一次。
- v1/v2 把 `PlayCount` 判为「不可用/全 0」是**取数层 bug**，不是数据源缺陷。补 Field 后，`familiar = min(play_count/20, 1)` 这类公式可以恢复；但它只适合做「长期熟悉度」，不能回答「最近 7 天听了几次」。
- 11-26 早间 0 秒会话没有增加 `PlayCount`，说明「未正确上报停止事件」的会话连 Emby 记账也不会产生。

#### 【未知】

- Emby 在什么条件下才把一次播放计入 `PlayCount`（13s 计入，但 11-26 的 5 分钟会话没计入；差异更像「上报链路」而非「时长阈值」）。
- `PlayCount` 是否包含 2026-11-25 之前的历史（显然是，但无法知道起点）。
- 修改 `DETAIL_FIELDS` 是否会影响候选池请求体积/耗时（需实现时验证）。

#### 结论

**「播放次数」信号可用，但要分两层：**

- **窗口内次数**：用 PR 合并后的播放链计数（见 Q4），不要用 `PlayCount`。
- **长期熟悉度**：用 Emby `PlayCount`（补 `UserDataPlayCount`），不要当近期热度。

---

### Q4. `ItemId + 60 秒会话窗口` 合并是否合理？

#### 【事实】

本窗口有 **24 组相邻同曲对**；其中 10 组是「有播放时长的同曲相邻」，其余是 0 秒行。10 组可分为三类：

| 类型 | 例子 | 段时长 | start_gap | end_gap | Emby PlayCount | 说明 |
|---|---|---|---|---|---|---|
| A. 真实切段（应合并） | 973820 | 26 + 277 | 26.8s | **0.8s** | 4（含历史） | 第二段紧接着第一段结束开始，合计≈曲长 |
| A. 真实切段 | 1000023 | 22 + 234 | 22.4s | **0.4s** | 2 | 同上 |
| A. 真实切段 | 1010164 | 25 + 260 | 25.3s | **0.3s** | 2 | 同上 |
| A. 真实切段 | 1003353 | 19 + 150 | 19.6s | **0.6s** | 3 | 同上 |
| A. 真实切段 | 1006702 | 19 + 192 | 19.6s | **0.6s** | **1** | **最强证据：两行被 Emby 计为 1 次** |
| A. 真实切段 | 973613 | 27 + 264 | 27.6s | **0.6s** | 11 | 合计≈曲长 |
| A. 真实切段 | 982121 | 15 + 221 | 15.9s | **0.9s** | 7 | 合计≈曲长 |
| B. 切段后放弃（应合并，但结果是负反馈） | 999148 | 29 + 20 | 30.0s | **1.0s** | 1 | 合并后 49s / 202s = 24% |
| B. 切段后中途放弃 | 973606 | 18 + 132 | 18.4s | **0.4s** | 2 | 合并后 150s / 272.5s = 55% |
| C. 完整短曲重播（**不应合并**） | 1078409 | 2 + 2 | 4.0s | **2.0s** | **2** | 2 秒探针曲完整播了两次 |

其他边界：

- `1006691`：12:03:12 播 59s，12:43:31 再播 31s（相隔 40 分钟）→ 不合并，Emby `PlayCount=2`，正确。
- `1058397`（Episode）：11-26 305s，11-27 1030s（相隔 23.8 小时）→ 不合并。
- `1061526`（诊断）：3 行，其中两行**同一秒**、不同 `DeviceName`（`diag-lowcase-001` / `diag-body-002`）→ 若只按 ItemId+时间会误合并两个并发会话。
- `973820` 的「相隔 27 秒」是 **start-to-start**；按 end-to-start 只有 **0.8 秒**。

#### 【推断】

- 「按 60 秒窗口合并」的方向正确，但**只看 `DateCreated` 差值不够**：
  1. 当第一段时长 > 60s 时（例如网络切换发生在 90s），start_gap > 60s，会漏合并；
  2. 会把「完整短曲立即重播」误合并（1078409 就是反例）；
  3. 会把不同设备的并发播放误合并（1061526 同一秒两行）；
  4. 会跨用户合并（NULL 行与真实行同曲时）。
- 真正稳定的判据是：**上一段没播完，且下一段紧接着上一段的结束时刻开始**。

#### 建议的合并规则（可直接落 v3 口径）

```text
按 (UserId, ItemId, DeviceName) 分组，按 DateCreated 升序：
可合并当且仅当：
  1) 中间没有其它 ItemId 的行；
  2) 0 <= next.DateCreated - (prev.DateCreated + prev.PlayDuration) <= 60s；
  3) prev.PlayDuration < 0.95 * runtime(ItemId)   // 上一段未播完
合并后：
  merged_duration = min(sum(segments), runtime)
  play_count += 1
```

- 第 3 条是 1078409 反例逼出来的：**完整播放后的重播必须算新的一次**。
- 用 end-to-start 而不是 start-to-start，避免长第一段漏合并。
- 60s 上限可保留（覆盖失败重试的等待），但实测切段 end_gap 都在 0–1s，收到 10–30s 也不会损失本窗口样本。
- `runtime` 缺失时，退化为「同一 ItemId、end_gap ≤ 10s、且 prev < 60s」，并标记低置信。

#### 【未知】

- 第二段是否真的从第一段位置续播（合计≈曲长支持续播，但没有播放位置字段可直接证明）。
- 播放器在「暂停后恢复」时是写一行还是两行；`PauseDuration` 非 0 的两行样本无法验证。
- PR 插件自身的会话边界是否也有 60s 之类的阈值（Emby `PlayCount` 的行为提示它有自己的逻辑，但不可见）。

#### 结论

**合并是必要的，但 60 秒时间窗只能当辅助条件；主判据应是「上一段未完成 + 紧接结束时刻」。** 否则会把重播、并发、跨用户的行并掉。

---

### Q5. `DateCreated` 无时区标记的影响与统一口径

#### 【事实】

1. 存储形态：`typeof(DateCreated) = 'text'`，样例 `2026-11-25 22:03:58.7800817`，**7 位小数秒、无 `Z`/无偏移**。
2. 服务器本地时区 = **UTC+8**：
   ```sql
   SELECT datetime('now'), datetime('now','localtime');
   -- 2026-11-27 04:52:24  |  2026-11-27 12:52:24
   ```
3. 与 Emby 独立对账：Emby `LastPlayedDate` 是 UTC（带 `Z`），对多条记录都满足 `LPD = DateCreated - 8h`：
   - 973820：PR `2026-11-27 07:51:01` ↔ Emby `2026-11-26T23:51:01Z`
   - 1006702：PR `2026-11-27 08:08:49` ↔ Emby `2026-11-27T00:08:49Z`
   - 1008768：PR `2026-11-27 12:48:59` ↔ Emby `2026-11-27T04:48:59Z`
4. **按 UTC 分天会切碎早间收听块**：11-27 的 `07:51–08:21` 这一段，`07:51–07:59` 会落到 UTC 的 11-26，`08:00` 之后才落到 UTC 11-27。同一次连续收听被拆到两个「天」。
5. 本窗口按**本地日期**聚合：

   | 本地日期 | 行数 | 总时长(s) | 其中 NULL 行 |
   |---|---|---|---|
   | 2026-11-25 | 3 | 271 | 0 |
   | 2026-11-26 | 24 | 723 | 21 |
   | 2026-11-27 | 34 | 3880 | 5 |

   若按 UTC 日期，11-27 早间 07:51–07:59 的行会被算到 11-26，11-26 的「有效播放」会虚高、11-27 会虚低。
6. 本窗口用户行的小时分布（本地）：`07` 4 行、`08` 14 行、`11` 3 行、`12` 10 行、`13` 1 行、`22` 3 行。07–08 点合计 18/35 = 51%，08 点 14/35 = 40%——**与 map 里「6–12 点 81% / 08 点 50%」不一致**，但样本只有 2 天，不能用来否定 map，只能说明「不要用这 61 行标定先验」。
7. 解析陷阱：
   - Python `datetime.strptime(s, '%Y-%m-%d %H:%M:%S.%f')` 对 7 位小数**报错**（`unconverted data remains: 7`）。
   - `datetime.fromisoformat()`（Python 3.11+）可直接解析并截断到微秒。
   - SQLite `datetime()` / `date()` / `strftime('%s')` 能正常解析该格式（截断到秒）。
   - 现有 `_age_days()` 只取 `[:10]`，不受小数位影响；但任何新的全量时间解析要注意。

#### 【推断】

- PR 插件写的是 `DateTime.Now`（服务器本地），因此 `DateCreated` 的「日期/小时」天然就是用户所在时区的墙钟时间（服务器与用户同为 UTC+8）。
- 只要服务器时区不变，`+08:00` 固定偏移就是正确的；中国无夏令时，不需要 tz 数据库也能算对。
- 但如果服务器迁移/改时区，历史行没有标记，会静默漂移 8 小时。

#### 【未知】

- 插件是否有独立的时区配置项（只读接口看不到）。
- 跨时区客户端（例如出差用手机）写入的 `DateCreated` 是服务器时间还是客户端时间——从本窗口看是服务器本地时间，但没有异地样本。
- 夏令时/时区变更后的历史数据如何处理。

#### 统一口径（建议写入 v3）

```text
1) 解析：DateCreated 视为 naive local，绑定 Asia/Shanghai (+08:00)；
   入库同时保存 played_at_local 与 played_at_utc。
2) 「按天」= 本地日期（Asia/Shanghai），与 cache/daily_recommend.json 的 date 键一致。
3) 「时段画像」= 本地小时（0–23）；不要用 UTC 小时。
4) 与 Emby LastPlayedDate 对账时，先统一转 UTC 再比较。
5) 禁止 datetime.utcnow().date() 直接分天。
```

---

## 4. 信号清单（对应 v3 口径）

| 信号 | 判定 | 口径 / 依据 | 主要风险 |
|---|---|---|---|
| **播放次数（去重）** | **可用** | PR + `UserId=当前账号` + `ItemType='Audio'` + Q4 合并规则；每条合并链计 1 次；同日同曲可再按天去重 | 原始行数会翻倍（切段）；`PlayCount` 是生命周期不是窗口；0 秒行必须丢弃 |
| **短播放负反馈** | **可用（弱，软降权）** | 合并后 `duration > 0` 且 `duration < min(60s, 0.5×runtime)`，或 `duration/runtime < 0.5`；同日同曲只计一次；排除最近 5 分钟仍在播的行；排除 Episode | 60s 绝对阈值会漏掉「150s/272s（55%）」这类中段切歌（973606）；流媒体启动失败也会产生短播放（970725 13s）；11-26 整段缺失不能当负反馈 |
| **曝光 / 切换证据** | **曝光：不可用；切换：可用（弱）** | PR 没有「曝光未播放」事件；只有「开始播放」→ 可做「开始后很快切走」。`UserId=NULL` 的 0 秒行不可归属，不能当曝光 | 11-26 整段会话丢失（18 行），曝光覆盖率不可知；`diag-*` 行是测试数据；真正可靠的曝光要来自 XiaoEmby 自己的选曲/下发日志，不是 PR |
| **客户端 / 设备过滤** | **可用（仅过滤）** | `ClientName` / `DeviceName` 61 行 100% 有值；可 `include XiaoEmby+小爱音箱` 或 `exclude diag-*`；**不要**把客户端当口味特征 | 客户端自报、可伪造；同一账号在笔记本/手机/音箱都有播放；过滤到 XiaoEmby 也不能保证行可用（11-26 就是 XiaoEmby 全丢） |
| **时段画像** | **可用，但参数待定** | 本地小时聚合；本窗口 07/08/11/12/13/22 点有数据；早间为主 | 只有 2 天、35 条用户行；不能据此定 81%/50% 之类的先验；跨天/周末样本缺失；UTC 分天会切碎早间块 |

### 4.1 明确不可用的做法

- 用 `PlayDuration=0` 当「跳过 / 不喜欢」。
- 用 `UserId=NULL` 的行做任何用户级统计（包括曝光）。
- 用原始行数当播放次数（切段会把 1 次变 2 次）。
- 用 Emby `PlayCount` 当「最近 N 天播放次数」。
- 用 `Fields=UserData` 读 `PlayCount` / `LastPlayedDate`（会得到 0 / null）。
- 用 `ItemType` 不过滤就统计（4 行 Episode 会混进音乐画像）。
- 用 `datetime.utcnow().date()` 给 `DateCreated` 分天。

### 4.2 待定（需要实现前拍板）

- 短播放阈值：`<60s` 还是 `ratio<0.5`，还是两者取或；中段切歌（55%）是否降权。
- 「同日同曲多次短播放」是计一次还是累计降权。
- 是否把最近 5–10 分钟内的行排除（避免把「正在播」误判为短播放）。
- 数据覆盖率低于多少时整体回退 v2（本窗口 11-26 早间音乐收听全部不可用；当天音频仅 1 行、13 秒，其余 3 行是剧集）。
- 时段画像的粒度（小时 / 3 小时段 / 工作日-周末）。

---

## 5. 对 v3 的直接建议

1. **先修取数，再谈画像**：`DETAIL_FIELDS` 增加 `UserDataPlayCount`；否则 v3 会继续继承「PlayCount 全 0」的假前提。
2. **PR 主信号必须做缺失率监控**：按天统计 `UserId IS NULL` / `PlayDuration=0` 的占比；本窗口 11-26 高达 21/24 行，属于「整天不可用」，应触发回退而不是「今天没有负反馈」。
3. **合并逻辑按 Q4 规则实现**，不要只写 `ItemId + 60s`；至少加「上一段未完成」和「同一设备」两个条件。
4. **Episode / 诊断数据硬过滤**：`ItemType='Audio'`；`DeviceName LIKE 'diag%'` 或 `ItemName LIKE 'Probe Artist%'` 排除。
5. **短播放只做软降权**（与 map 一致），并排除「最新一行可能仍在播」的边界。
6. **时区统一 +08:00**，缓存日期、PR 日聚合、时段画像三处用同一口径；与 Emby 对账时转 UTC。
7. **Emby `LastPlayedDate` 可作兜底**：补 Field 后有值，且与 PR 开始时刻一致；`SortBy=DatePlayed` 也能给出最近顺序（即使不补 Field 也能排序）。但它是「逻辑播放第一段起点」，不是结束时间。

---

## 6. 局限与未确认事项

- **样本极小**：61 行 / 2 天 / 37 个 Item，且包含探针与诊断数据；所有「比例」都不具代表性。
- **无法看到插件源码/配置**：`UserId=NULL` 与 `PlayDuration=0` 的根因只能是推断。
- **无法确认数据保留策略**：窗口从 11-25 22:03 开始，可能是插件刚安装、被清理过，或只是近期才有播放；不能假设「有 30 天历史」。
- **无法确认 `PlayCount` 的历史起点**：它可能包含数年前的播放（972513 的 LPD 是 2025-05），所以只能当长期熟悉度。
- **无法确认暂停语义**：`PauseDuration` 只有 2 个非零样本，且无法与曲长/下一行对齐。
- **`PlayedPercentage` 基本不可用**：仅 1 行有值且与时长比例不符。
- **未验证多用户隔离**：本窗口只有 `nick` 一个有值的 UserId；其他账号的 PR 行是否存在、是否会串数据，未知。
- **未验证写入/回填**：只读调研，未确认插件在什么时机插入/更新行；「先插 0 行后更新」这种可能性无法排除。

---

## 7. 最小事实清单（给实现者）

| 事实 | 值 / 结论 |
|---|---|
| PR 表字段数 | 12；无 `PlayCount` |
| 窗口行数 | 61（35 有用户 / 26 NULL；57 Audio / 4 Episode） |
| 0 秒行与 NULL 行 | 完全同一集合（26 = 26） |
| 最大 `PlayDuration` | 1030s，Episode，曲长 1425s（72.3%） |
| 单行是否超过曲长 | 否（37/37 对照） |
| 切段 end_gap | 0.3–1.0s（本窗口全部切段对） |
| 完整切段合计误差 | +0.1%~+0.9% |
| Emby `PlayCount` 取数条件 | `Fields` 必须含 `UserDataPlayCount` |
| Emby `LastPlayedDate` 取数条件 | `Fields` 必须含 `UserDataLastPlayedDate` |
| Emby LPD 语义 | 逻辑播放第一段起点，UTC |
| 服务器时区 | UTC+8（SQLite 时钟与 Emby LPD 双重验证） |
| `DateCreated` 类型 | TEXT，7 位小数秒，无时区 |
| 本地日期聚合 | 11-25: 3 行 / 11-26: 24 行 / 11-27: 34 行 |
