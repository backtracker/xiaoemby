# 01 — 统一缓存 store 的物理形态与迁移路径（研究笔记）

## 0. 调研口径与边界

- 事实来源：分支 `integration/v3-daily-recommend`（既有事实笔记的行号与该分支完全吻合）。本 research 工作树基于 `master`，而 `master` 上**没有** `library_mirror.py` / `mirror_sync.py`（`git ls-tree` 核对）；因此代码事实用 `git show integration/v3-daily-recommend:<path>` 只读取得，未改任何业务代码。
- 下文体积/条数为对主仓库工作树真实缓存文件的**统计量**，不记录曲目名、ID、token 等原始数据。
- 结论定位：只回答「物理形态 + 迁移可行性」，生命周期与陈旧语义留给后续工单。

## 1. 现状事实：三类 JSON 与镜像库

| 对象 | 路径 / 形状（仅字段名） | 实测体积 | 写方 | 读方 | 写频率 |
| --- | --- | --- | --- | --- | --- |
| 每日推荐快照 | `cache/daily_recommend.json`；writer 字段 `date/source/reason/renewals/track_ids/track_names/segments/mode/fallback_reason`（`daily_recommend.py:1066-1082`） | 4,009 B；20 个 id、3 个 segment；**旧文件缺 `mode`/`fallback_reason`**，segment 带 v2 键 `count/intent/share/start/theme/track_ids/track_names/transition` | `_save_cache`（1066-1082），在 `generate` 的成功/兜底两处调用（1772、1865） | `is_fresh`（1052-1063）、`generate` 当天冻结/恢复（1684-1712）、`music_library._restore_daily_recommend_tracks`（140-157）、语音提示判断（`xiaomusic.py:599`） | 每次生成/换批 ≤1 |
| 推荐历史 | `cache/recommend_history.json`；`dict[日期] -> {track_ids, genres, artists, decades}`（1098-1123，保留 `HISTORY_DAYS=14` 天） | 1,037 B；当前仅 1 天 | `_save_history`（1098-1123） | `_load_history`（1089-1096）→ `_recommend_penalty`/`_exposure_counts`/`_track_exposure`/`_recently_recommended`（1163-1213、1377-1390）；`generate`（1736-1741） | 每次生成 ≤1 |
| 静默名单 | `cache/snooze.json`；`dict[track_id 字符串] -> until ISO 日期`（1139-1149，30 天） | **文件当前不存在**（无历史数据） | `snooze_track`（1139-1149），由语音「不喜欢」触发（`xiaomusic.py:646-647`） | `_active_snooze`（1152-1160）→ 候选过滤/画像/选曲（1741、1750、1789-1797） | 用户点击时 |
| 曲库镜像 | `cache/library_mirror.sqlite3`（路径由 `cache_dir` 推导：`config.py:88,398-400`）；表 `tracks`(14 列，含 `taste/penalty/tombstone`)、`user_state(item_id,user_id,favorite,play_count,last_played_date)`、`meta(key,value)`（`library_mirror.py:391-436`） | 29,134,848 B；`tracks` 72,577 行、`user_state` 72,577 行；`meta` 4 键 `version/watermark/last_sync_day/stale_days` | `LibraryMirror.commit()` 单事务 `DELETE`+重插（453-506），仅由成功的 `sync_mirror()` 调用（`mirror_sync.py:253`） | `snapshot/candidates/version_info/user_state`（516-583）、`music_library._load_mirror_music`（58-93）、`daily_recommend._load_mirror`（1285-1309）、`GET /system/mirror_status`（`system.py:216-239`） | 每日 04:00 增量、周日 04:30 全量（`crontab.py:301-302`）、手动 POST（`system.py:137-183`） |

### 1.1 并发 / 原子性事实

- 三个 JSON 全部用 `open(path, "w") + json.dump`，**无临时文件+rename、无锁、无 fsync**；读侧 `except (OSError, ValueError): return {}`，解析失败会静默当作空状态（`daily_recommend.py:1044-1051,1089-1096,1130-1137`）。进程崩溃或并发写入可产生截断 JSON，并无声丢失状态。
- `generate` / `snooze_track` / `sync_mirror` 都经 `asyncio.to_thread` 落到不同线程（`xiaomusic.py:559,646`；`crontab.py:181`；`system.py:160,242`）。`_daily_recommend_generating` 只按 `did` 去重**同一设备**的语音重复口令（`xiaomusic.py:578-581,629`），**不覆盖** cron、HTTP 与其他设备的并发；JSON 读写无统一互斥。
- `LibraryMirror.__init__` 只做 `sqlite3.connect`（399），实测真实库 `journal_mode=delete`，无 WAL / `busy_timeout` 设置；`commit()` 在单个 `with self._conn` 事务内删 72k 行再重插（456-506），该写入会持有写锁；读侧都是短连接（`music_library.py:67-71`、`daily_recommend.py:1292-1301`、`system.py:234-242`）。同步失败不 commit，保留上一版（`mirror_sync.py:268-284`）。

### 1.2 死配置与遗留文件核查（确认事实）

- `tag_cache_path` / `picture_cache_path` 仅在 `config.py:384-395` 定义；对 `integration/v3-daily-recommend` 全部 `*.py` 执行 `git grep`，**无任何调用方**，确认是死配置面。
- 运行时所有镜像路径均来自 `mirror_db_path = cache_dir + library_mirror.sqlite3`（`config.py:88,398-400`）；`git grep "conf/library_mirror"` 无结果。主仓库 `conf/library_mirror.sqlite3` 为未跟踪文件（`git status` 显示 `??`），且与 `cache/library_mirror.sqlite3` **md5 完全相同**——确认是遗留副本，无运行时引用。
- 三个 JSON 与 `cache/` 均被 `.gitignore` 忽略（`git status` 只报 `?? conf/library_mirror.sqlite3`），删除/迁移不涉及仓库版本控制。

## 2. 推荐形态：单库多表（附拆分逃生口）

**建议：复用现有 `cache/library_mirror.sqlite3`，在同一 SQLite 文件中新增缓存表（推荐一个 `cache_state(key PRIMARY KEY, value TEXT, updated_at)` 整对象 blob 表 + 必要索引列），并配套 WAL + busy_timeout 与严格表归属约定；保留一键切到独立小库的开关。**

理由（均为事实支撑的判断，见第 5 节标注）：

1. 四类对象都是**派生/可重建的私有本地状态**，生命周期与「可丢失但不致命」的语义一致；JSON 总量约 5 KB，相对 29 MB 镜像可忽略，**分库并不减少备份/运维对象**（备份无论如何都要带上镜像）。
2. 现有三个 reader 都是「整对象读入 + Python 解析」，对象级 `get/set(key)` 抽象几乎不改变调用方；单库还能把「冻结 mode + 快照 + history 裁剪」放进**一个事务**，这是分库做不到的。
3. 路径模型已统一由 `cache_dir` 推导（`config.py:398-400`），不引入第二套路径配置，减少漂移与误删风险。

代价与缓解（必须写入后续实现约束）：

- **锁竞争**：周同步在 delete-journal 下重插 72k 行会阻塞同库读写。→ 开启 `PRAGMA journal_mode=WAL` 与 `busy_timeout`，并让缓存写入与 04:00/04:30 同步错峰；实现后用并发测试实测。
- **爆炸半径**：单文件损坏影响全部对象。→ 迁移前备份；约定 mirror sync 只写 `tracks/user_state/meta.mirror_*`，cache store 只写自己的表；禁止「清空整库重建」式实现。
- **meta 键冲突**：`version` 已是镜像版本（`library_mirror.py:501`），缓存 schema 标记必须用独立键（如 `cache_schema_version` / `json_cache_migrated`），不可复用。
- **拆分触发条件（逃生口）**：若实测 WAL 不可用（网络卷/权限）或同步期间出现 `SQLITE_BUSY`，改落 `cache/recommend_state.sqlite3`；因 store API 是对象键粒度，退化为改一行路径即可。
- TTS 临时音频按地图既定边界**保留独立目录**，不入库。

## 3. 迁移步骤（read-once、不双写、可回滚）

1. **前置备份**：复制 `cache/library_mirror.sqlite3`；记录三个 JSON 的大小与 mtime。遗留 `conf/` 副本先确认无引用再处置（已确认，见 1.2），不要把它当活跃库。
2. **首次启动检测**：读 `meta.cache_schema_version`；已置位则直接走 SQLite，**永不再读 JSON**（只读一次、不双写）。
3. **防御性读旧 JSON**：逐文件 try-read；缺文件 / 非 dict / 损坏 → 记 `{}` 并告警，**不中断启动**。容忍旧形状：实测 `daily_recommend.json` 缺 `mode`/`fallback_reason`，segment 为 v2 键集合；用默认值补齐，`segments` 原样以 JSON 文本存储以保留未知字段；历史按日期行存，`track_ids/genres/artists/decades` 作为 JSON 文本列；snooze 按 `track_id -> until` 行存。
4. **单事务提交**：三对象写入 + 置迁移标记在同一事务；任一失败则 rollback、标记不落，下次启动重试。
5. **旧文件保留期**：旧 JSON **不删、不覆写**（保持 mtime），至少保留一个发布周期 / 直到回滚窗口关闭；最终删除归「生命周期统一」工单，不在本迁移里做。
6. **回滚**：提供 `cache_store=json`（或等价开关）把读路径切回 JSON；因旧文件从未被写，数据原样可用。保守做法 additionally 清标记 / drop 新增表后重启。**禁止回滚后自动重新迁移已标记的陈旧 JSON**（会覆盖新状态）：标记须含迁移批次/时间戳，回滚要显式清除。
7. **幂等与验收**：迁移后核对条数、日期、键集合；`schema_version` 不匹配走显式升级函数，不静默吞异常。
8. **多用户预留**：当前 JSON 无 `user_id` 键；给 snooze/history 表预留 `user_id` 列（默认配置账号），但**本轮不合并** PR 会话账号与镜像配置账号两套口径。

## 4. 风险清单

| # | 风险 | 证据 | 缓解 |
| --- | --- | --- | --- |
| R1 | 旧 JSON 形状漂移导致迁移丢字段 | 实测快照缺 `mode`/`fallback_reason`、segment 为 v2 键（1 节） | 整对象 blob + 缺省值补齐；迁移后抽查键集合 |
| R2 | JSON 非原子写 + 静默降级为 `{}` | `open("w")`、无锁（`1044-1051` 等） | 迁移到 SQLite 单事务写；过渡期读侧告警而非静默吞 |
| R3 | 多来源并发写（cron / HTTP / 多设备）无统一互斥 | `to_thread` 调用点 + 仅按 did 去重 | 单库事务化 + WAL/busy_timeout；补并发测试 |
| R4 | 周同步 72k 行写入阻塞缓存读写 | `commit()` 456-506、`journal_mode=delete` 实测 | WAL；错峰；必要时按第 2 节拆库 |
| R5 | 迁移部分成功 / 回滚后陈旧 JSON 复活 | 无现成标记机制 | 数据+标记同事务；标记带批次；显式回滚清标记 |
| R6 | 遗留 `conf/library_mirror.sqlite3` 被误当活跃库删除或同步 | `??` 未跟踪、md5 与活跃库相同、无代码引用 | 处置前再次 `git grep` 确认；文档写明唯一路径来自 `cache_dir` |
| R7 | `tag_cache.json` / `picture_cache` 死配置误导后续实现 | `config.py:384-395` 无调用方 | 本轮只记录，清理归后续工单 |
| R8 | 单库 meta 键语义混淆 | `version` 已用于镜像版本 | 缓存 schema 用独立键并命名空间化 |
| R9 | 双用户口径未收敛，缓存无 user 维度 | PR 会话账号 vs 配置 `emby_user_id`（事实笔记 §4） | 预留列，不在本工单合并口径 |

## 5. 事实 vs 推断（显式分离）

**事实（可在源码/实测中复核）**

- 三 JSON 的文件名、字段、读写入口、保留天数（14 / 30 / 3 次换批）：`daily_recommend.py:96-98,1052-1160,1066-1123,1677-1712,1865-1866`。
- 实测体积/条数：快照 4,009 B（20 曲、3 段，无 `mode` 键）、历史 1,037 B（1 天）、snooze 文件不存在；镜像 29,134,848 B（72,577 曲 / 72,577 user_state / 4 个 meta 键）；真实库 `journal_mode=delete`，无 wal/shm 旁文件。
- 镜像 schema 与 `commit()` 的单事务全量替换、`snapshot/candidates/version_info/user_state` 只读接口：`library_mirror.py:391-436,453-506,516-583`。
- JSON 写为 `open("w")` 且读侧静默 `return {}`；并发入口均为 `to_thread`，按 did 的去重只覆盖同设备语音口令：`xiaomusic.py:559,578-581,599,629,646`。
- 死配置与遗留文件：`git grep`（全 `*.py`）无 `tag_cache_path`/`picture_cache_path` 调用；无 `conf/library_mirror` 引用；`git status` 显示该文件 `??`，md5 与活跃库相同。

**推断（建议/判断，非源码事实）**

- 「单库多表优于分库」：基于对象同生命周期、体积对比、整对象读取形态与跨对象原子性的权衡；分库作为 WAL 失败时的逃生口。
- WAL + busy_timeout 足以缓解周同步锁竞争：属预期，需实现后并发压测验证（本轮未做压测）。
- 迁移标记命名、保留期「一个发布周期」、回滚开关形态：均为建议参数，最终值由「生命周期统一」工单定。
- 给 snooze/history 预留 `user_id` 列：防御性设计，不改变当前单用户行为。

## 6. Caveats

- 本笔记的源码事实取自 `integration/v3-daily-recommend`；research 分支基于 `master`，后者不含镜像实现，直接对照 `master` 行号会失配。
- 未读取/记录任何凭据；未输出任何曲目原始数据。
- 只读调研，未改业务代码，未开 PR、未合并。
