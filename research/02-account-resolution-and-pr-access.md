# 02 — 当前账号解析与 PR 取数路径（调研结论）

> Context pointer: 来自工单 `.scratch/daily-recommend-v3/issues/02-account-resolution-and-pr-access.md`。
> 调研时间：2026-11-27。事实来源：本机 `conf/setting.json`（仅读 host / user_id，密钥不输出）、Emby 只读 GET + 只读 SELECT POST、仓库源码、Playback Reporting 插件上游源码。所有配置密钥均未写入本文。

标记约定：**【事实】** = 实测 / 源码直证；**【推断】** = 基于事实的规则建议，需产品确认。

---

## 0. 结论速览

| 问题 | 结论 | 级别 |
| --- | --- | --- |
| 音箱会话映射 | `Client=XiaoEmby` + `DeviceName=小爱音箱` 精确匹配 → 取非 null `UserId`，多条按 `LastActivityDate` 取最新；实测唯一有效映射 `UserName=nick → UserId=08bcd6f3…`（与配置 `emby_user_id` 一致） | 事实 + 推断 |
| `UserName=None` 会话 | 是旧版（0.0.18rc2，服务器级 API Key 上报）遗留，**不可作为身份来源**；其 PR 行 `UserId` 也为 null，仅在精确设备匹配 + 存在配置默认用户时可低置信度归属 | 事实 + 推断 |
| PR 取数 | 端点由 **Playback Reporting 2.1.0.7** 插件提供（非名为 `user_usage_stats` 的插件）；管理员 Key 可用；body 必须是 `{"CustomQueryString":"SELECT …","ReplaceUserId":false}` | 事实 |
| 失败模式 | 无/错 token → 401；错字段名/坏 SQL → **HTTP 200 + `message` 非空**；插件未注册 → 404；无服务端超时，需客户端限时 | 事实 + 推断 |
| 数据充足度 | 实测 PR 库仅 61 行、跨度约 1.7 天，nick 35 行、null 用户 26 行；**不足以支撑口味画像，应触发回退** | 事实 + 推断 |

---

## 1. 账号解析规则

### 1.1 实测会话证据（GET /emby/Sessions，2026-11-27）

| 筛选条件 | 命中数 | UserName | UserId | 版本 | LastActivityDate |
| --- | --- | --- | --- | --- | --- |
| `Client=XiaoEmby` & `DeviceName=小爱音箱` | 1 | `nick` | `08bcd6f3…` | 0.0.18rc4 | 2026-11-27T00:40Z |
| 同上（早期） | 1 | `null` | `null` | 0.0.18rc2 | 2026-11-26T00:25Z |
| `Client=XiaoEmby` 但 DeviceName 为 `diag*` / `diagplay` | 5 | `null` | `null` | 1.0.0 | 2026-11-26 |

**【事实】** `GET /emby/Users` 共 9 个用户，`nick → 08bcd6f3…`；`conf/setting.json` 的 `emby_user_id` 正是该 ID，`emby_users={}`。`GET /emby/Users/{id}` 返回 `Name=nick`、`LastLoginDate=2026-11-27T03:34Z`。
**【事实】** 源码 `xiaomusic/utils/emby_utils.py:241-242` 定义 `CLIENT_NAME="XiaoEmby"`、`DEVICE_NAME="小爱音箱"`；`:272-275` 明确注释：服务器级 API Key 创建的会话**没有 UserId**，用户登录 token 才带身份。这与 rc2（null）→ rc4（nick）的实测演进一致。
**【事实】** `xiaomusic/config.py:212-220`：旧配置 `emby_user_id` 自动迁移为 `emby_users["default"]`（alias=默认，`is_default=True`）；`:340-368` 的 `get_default_emby_user()` / `get_emby_user_id_by_alias()` 在无显式用户时返回默认用户。`xiaomusic/command_handler.py:96-120` 的别名解析**仅用于 `play` 命令**，不是全局账号切换。

### 1.2 解析规则（建议实现顺序）

| 优先级 | 规则 | 级别 |
| --- | --- | --- |
| 1 | 语音/实时触发：从 `/Sessions` 精确匹配 `Client=="XiaoEmby" && DeviceName=="小爱音箱"`，**丢弃 `UserId` 为 null 的会话**，按 `LastActivityDate` 降序取第一条；用 `/Users` 的 `Id→Name` 表交叉校验名称 | 推断 |
| 2 | 定时/无会话触发：用配置默认用户（`emby_users` 中 `is_default`，否则首项；旧 `emby_user_id` 已迁移） | 推断 |
| 3 | 两者冲突：**不静默切换画像主体**，保留配置默认用户并记 warning；要支持家庭多账号切换，需新增显式配置项（如 `daily_recommend_user_id` / `daily_recommend_user_alias`） | 推断 |
| 4 | 两者都缺：无画像主体 → 直接回退 | 推断 |

**【推断】** 不要按 `Client` 前缀或模糊设备名匹配：`diag-body-002`、`diag-lowcase-001` 等 XiaoEmby 会话是测试流量，会污染选择。也不要用 "最近活动" 单独判定，因为 diag 会话时间晚于有效音箱会话。
**【推断】** `Session.DeviceId = "xiaoemby-"+uuid5(host+"-"+username)`（源码 `:260-261`）理论上可反推用户，但当前 `setting.json` 无 `emby_username`，不可依赖。

### 1.3 `UserName=None` 音箱会话的处置

| 场景 | 处置 | 级别 |
| --- | --- | --- |
| 会话解析 | 视为**无身份**，不参与选账号；只记录 `account_source=none` 与告警 | 推断 |
| PR 行归属 | `PlaybackActivity.UserId IS NULL` 的行**不可无条件归给默认用户**：实测 null 行同时含 `ClientName=RustMusic` 和各 `diag*` 设备 | 事实 |
| 需要利用时 | 仅当 `ClientName='XiaoEmby' AND DeviceName='小爱音箱' AND UserId IS NULL` 且配置默认用户存在，才以默认用户归属，并标记 `low_confidence=true`、画像权重下调；实测这类行 18 条，集中在 2026-11-26 07:58–08:24（rc2 时段） | 事实 + 推断 |

---

## 2. 取数路径（只读）

| 步骤 | 请求 | 用途 / 实测结果 |
| --- | --- | --- |
| 1 | `GET /emby/Sessions`（`X-Emby-Token: <管理员 Key>`） | 解析当前账号；实测 22 条 |
| 2 | `GET /emby/Users` | 建立 `Id↔Name` 映射；实测 9 用户 |
| 3 | `GET /emby/Users/{id}` | 校验主体存在、非禁用；实测 nick 200 |
| 4 | `GET /emby/Plugins` | 探测插件：查名称 **`Playback Reporting`**（Id `9e6eb40f-…`），版本 2.1.0.7。不存在名为 `user_usage_stats` 的插件，但端点路径是 `/user_usage_stats/*` |
| 5 | `POST /emby/user_usage_stats/submit_custom_query`，body `{"CustomQueryString":"SELECT …","ReplaceUserId":false}` | 只读取 PR 数据；实测 `SELECT 1 AS ok` → `{"colums":["ok"],"results":[["1"]],"message":""}` |

**【事实】** 请求头 `X-Emby-Token` 实测可用；插件上游文档也接受 `Authorization: MediaBrowser Token=<token>`。
**【事实】** 响应字段拼写是 `colums`（源码/实测均为该拼写），另有 `results`、`message`。`results` 是按 `colums` 顺序排列的二维数组，单元格均为字符串。
**【事实】** 表名只有 `PlaybackActivity`、`UserList`；`PlaybackActivity` 列含 `DateCreated, UserId, ItemId, ItemType, ItemName, PlaybackMethod, ClientName, DeviceName, PlayDuration, PauseDuration, RemoteAddress, TranscodeReasons`。
**【事实】** `ReplaceUserId=false` 时返回原始 UserId；插件上游源码 `UserActivityAPI.cs` 显示 `ReplaceUserId=true` 会把 `UserId` 列改名为 `UserName` 并在服务端替换。**【推断】** 固定 `false`、在本地用步骤 2 的映射表改名，可避免服务端映射与多账号口径耦合。

推荐取数 SQL（均为 SELECT，实测通过）：

```sql
-- 探针
SELECT 1 AS ok;
-- 账号归属与数据量/跨度（按用户）
SELECT UserId, COUNT(*) AS n, MIN(DateCreated) AS first, MAX(DateCreated) AS last
FROM PlaybackActivity GROUP BY UserId ORDER BY n DESC;
-- 音箱维度和数据量
SELECT UserId, COUNT(*) AS n, MIN(DateCreated) AS first, MAX(DateCreated) AS last
FROM PlaybackActivity
WHERE ClientName='XiaoEmby' AND DeviceName='小爱音箱' GROUP BY UserId;
-- 画像主体明细
SELECT DateCreated, ItemId, ItemType, ItemName, PlayDuration, ClientName, DeviceName
FROM PlaybackActivity
WHERE UserId = '<配置/会话解析出的 id>' AND ItemType = 'Audio'
ORDER BY DateCreated DESC LIMIT 500;
```

---

## 3. 只读边界与失败模式清单

**【事实】** 插件上游 `UserActivityAPI.cs`：`[Route("/user_usage_stats/submit_custom_query","POST")]` + `[Authenticated(Roles = "admin")]`；`Post(CustomQuery)` 把 `CustomQueryString` 直接交给 `ActivityRepository.RunCustomQuery()`，后者在 `WriteLock.Write()` 下执行 `PrepareStatement/ExecuteQuery`——**服务端没有强制只读**。因此必须由调用方自律：只发单条 `SELECT`，禁止 `INSERT/UPDATE/DELETE/DROP/CREATE/REPLACE/PRAGMA/ATTACH/VACUUM` 与分号拼接，并加 `LIMIT`。源码中该方法无超时设置。

| 失败模式 | 实测/证据 | 判定信号 | 处置 |
| --- | --- | --- | --- |
| 无 token / 错误 token | 【事实】401 `Access token is invalid or expired.` | HTTP 401 | 认定 Key 失效，回退 |
| 非管理员 token | 【推断】注解要求 `admin` 角色，未实测（无普通用户凭据）；预期 401/403 | HTTP 401/403 | 回退，并提示需管理员 Key |
| 字段名写错（如 `"query"`） | 【事实】HTTP 200，`message="Error Running Query…Misuse: not an error"`，`colums=[]`，`results=[]`（SQL 为空） | `message` 非空 | 视为失败，不能只看 HTTP 200 |
| SQL 错（表不存在） | 【事实】HTTP 200，`message` 含 `no such table`，结果为空 | 同上 | 同上 |
| 查询成功但无数据 | 【推断，源自源码】`message="Query executed, no data returned. Number of rows effected : 0"`，`results=[]` | `message` 非空 + 0 行 | 按"数据不足"回退 |
| 插件未安装/未启用 | 【推断】路由在插件启动时注册；禁用后 POST 应为 404。**注意【事实】：即使插件正常，GET 该路径也返回 404「找不到文件」（仅 POST 路由），故不能用 GET 探测插件** | POST 404 + `/Plugins` 无 `Playback Reporting` | 回退 |
| 查询超时 | 【推断】服务端无超时保护；实测用 45–90s 客户端上限 | curl/客户端超时 | 回退，降低 LIMIT 或重试一次 |
| 配置账号不存在于 `/Users` | 【推断】`GET /Users/{id}` 404 或会话 UserId 不在映射表 | 404 / 映射缺失 | 回退 |

---

## 4. 回退信号表（供 issue 05 消费）

| 信号 | 获取方式 | 2026-11-27 实测值 | 建议健康阈值（推断） | 失败/不足时 |
| --- | --- | --- | --- | --- |
| 插件存在 | `/Plugins` 含 `Playback Reporting` | 存在，v2.1.0.7 | 必须存在 | 硬回退 |
| 端点可用 | POST `SELECT 1 AS ok` | HTTP 200，`message=""` | 成功且 `message==""` | 硬回退 |
| 认证有效 | 同上 | 管理员 Key 200；无/错 Key 401 | 非 401 | 硬回退 |
| 查询成功 | 业务查询响应 | `message=""`，`colums` 非空 | 必须满足 | 硬回退 |
| 可归属行数 | `COUNT(*)` where `UserId=<主体> AND ItemType='Audio'` | nick 全部 35 行；音箱+nick 19 行（全表 61 行） | ≥20 行 / 窗口（建议值） | 软回退 |
| 时间跨度 | `MAX(DateCreated)-MIN(DateCreated)` | 全表 2026-11-25 22:03 → 11-27 12:48（≈1.7 天）；音箱+nick 11-26 13:41 → 11-27 08:21（≈19 小时） | ≥7–14 天（建议值） | 软回退（**当前触发**） |
| 账号归属比例 | 可归属行 / 主体设备总行 | 音箱 19/37≈51%；全表 null 用户 26/61≈43% | ≥70%（建议值） | 软回退或低权重 |
| 当前会话身份 | `/Sessions` 精确匹配 + 非 null UserId | 存在 nick（rc4）；另有 1 条 null（rc2） | 非 null 且非空 | 用配置默认；无则回退 |
| 配置/会话一致性 | `emby_user_id` vs 会话 UserId | 一致（均为 `08bcd6f3…`） | 一致 | 不一致时记 warning，不自动切换 |

**【推断】** 判定顺序：硬信号（插件 / 401 / 查询失败）→ 直接回退；软信号（行数 / 跨度 / 归属比例）→ 计分或任一不达标即回退。按当前实测，即使接口全部成功，**跨度与归属比例也不达标**，应走"数据不足回退"。

---

## 5. 未决 / 需拍板

1. 家庭多账号是否允许"音箱会话自动切换画像主体"？当前代码只支持 `play` 命令的别名，不支持全局切换（事实）；建议先加显式配置项再启用自动切换（推断）。
2. 回退阈值的具体数值（行数 / 跨度 / 归属比例）需 issue 05 统一定义；本文只给建议区间。
3. null 用户历史行是否纳入画像（低权重）还是永久排除，需产品决策；本文建议纳入但降权（推断）。

## 参考来源

- 本机实测：`GET /emby/Sessions`、`GET /emby/Plugins`、`GET /emby/Users`、`GET /emby/Users/{id}`、`POST /emby/user_usage_stats/submit_custom_query`（仅 SELECT，`ReplaceUserId=false`），2026-11-27；只读，无写接口调用。
- 仓库源码：`xiaomusic/utils/emby_utils.py:241-242,260-275`；`xiaomusic/config.py:212-220,340-368`；`xiaomusic/command_handler.py:96-120`；`xiaomusic/device_player.py:938-943`。
- 插件上游（primary source）：`faush01/playback_reporting` 的 `playback_reporting/Api/UserActivityAPI.cs`（路由、admin 注解、字段名、响应结构、ReplaceUserId 逻辑）与 `playback_reporting/Data/ActivityRepository.cs`（表结构、RunCustomQuery 无只读强制）。
