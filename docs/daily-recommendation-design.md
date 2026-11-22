# XiaoEmby「今日推荐歌单」集成设计方案

> 来源设计：`/home/nick/workspace/rustmusic-desktop/.scratch/llm-daily-recommendation/`（LLM Daily Recommendation）
> 目标仓库：`xiaoemby`（Python / FastAPI / 小爱音箱语音控制 Emby 音乐库）
> 状态：设计方案（待评审），对应分支 `feature/daily-recommend-playlist`
> 日期：2026-11-22

---

## 1. 目标（Destination）

给 XiaoEmby 增加「今日推荐歌单」：用户说一句「播放今日推荐」，系统用用户自配的 OpenAI-compatible LLM 编排当天的音乐意图，在本地从 Emby 曲库中筛出一组曲目、生成名为「今日推荐」的歌单并播放，同时用 TTS 播报一句推荐理由。

交付物是**可落地的设计规格 + 分阶段实施清单**，不是一次性把 rustmusic 的 Rust 代码搬过来。

---

## 2. 来源功能的设计本质（必须继承)

rustmusic 的「今日推荐」有一不可谈判的红线：

> **LLM 只做「节目编排意图 + 特征组合 + 一句人话解释」，永远不接触单曲 id / 标题 / path。所有选曲、去重、落库都在本地完成。**

理由：LLM 直出曲目在业界实测幻觉率最高达 61%；只让模型输出封闭词汇的特征组合，本地据此筛曲，才能做到可校验、可复现、无幻觉。移植时这条红线原样保留。

原设计的数据流（浓缩）：

```
读配置 → 取聚合特征目录 → 调 LLM → 本地校验 recipe → 映射选曲规则 → 筛曲打分
      → 每艺术家上限 + 跨库轮转 → 落一张 is_mix=1 的歌单 → 当天缓存冻结
```

---

## 3. 现状差距（为什么必须简化)

rustmusic 的设计依赖三样 XiaoEmby **完全没有**的底座：

| rustmusic 底座 | XiaoEmby 现状 | 影响 |
|---|---|---|
| 本地库 + taglib/mutagen 元数据扫描 | 无本地库，音源只有 Emby | 候选池只能从 Emby API 拉 |
| SQLite 行为埋点（播放/搜索/不喜欢） | 无数据库 | 无法做 14 天半衰期口味画像 |
| SmartRules 智能播放列表引擎（24 字段） | 无 | 需自写简化的过滤/打分函数 |

此外小爱音箱**无屏幕**，无法展示 rustmusic 那种带 reason、封面、评分的 GUI 卡片。

**结论**：采用「务实简化版」——保留红线与核心算法骨架，简化底座；不追求与 rustmusic 逐字段一致。

---

## 4. 已拍板的设计决策

以下决策在方案阶段直接拍板，不再回头询问（评审时可推翻）：

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| 1 | 实现路线 | 务实简化版 | 1~2 天可落地；完整移植需先补埋点/数据库，成本数倍 |
| 2 | 主触发方式 | 语音「播放今日推荐」；定时任务列为 P2 | 最贴合音箱场景；定时为增强项 |
| 3 | HTTP 入口 | 复用现成 `POST /cmd`（传「播放今日推荐」） | 无需新增路由即可触发和调试 |
| 4 | 推荐数量 | 默认 20 首，阈值 ≥10，每艺术家 ≤4 | 小爱音箱歌单不宜过长；对齐原设计护栏 |
| 5 | 口味信号 | 收藏 + Emby `PlayCount` + 流派/年代分布加权 | 立即可从 Emby 拿到；不做 7 天半衰期（列 P2） |
| 6 | 反馈机制 | 喜欢复用现有 Emby 收藏；不做「不喜欢」软降权（列 P2） | `favorite_audio()` 已存在；负反馈需新表，延后 |
| 7 | LLM 客户端 | `requests` + `asyncio.to_thread`，不新增依赖 | 与现有 Emby 调用风格一致 |
| 8 | 缓存位置 | `cache/daily_recommend.json`（自建 JSON） | 不污染 setting.json，不触发 `reinit()` |
| 9 | reason 展示 | TTS 播报 + 日志，Web 端不展示 | 音箱无屏幕 |
| 10 | 多用户 | 按 `get_default_emby_user()` 全局生成，不做用户级 UI | 认证已精简 |

---

## 5. 详细设计

### 5.1 数据层 — `xiaomusic/utils/emby_utils.py`

**扩展 `Audio`**（第 205~226 行）新增字段：

```python
self.genres: list[str] = []        # item["Genres"]
self.production_year: int = 0      # item["ProductionYear"]
self.play_count: int = 0           # item["UserData"]["PlayCount"]
self.date_created: str = ""        # item["DateCreated"]
```

**扩展解析**（`__search_music_internal`，第 362~370 行的 `Audio(...)` 构造）填入以上字段。

**新增候选池方法**：

```python
def fetch_recommend_candidates(self, user_id: str, limit: int = 200) -> list[Audio]:
    # 复用 /emby/Users/{user_id}/Items + SortBy=Random + Limit=limit
    # 解析保留 Genres / ProductionYear / UserData.PlayCount / DateCreated
    # 关键：不要对每个 Audio 调 __set_audio_container（那是 N 次 HTTP，极慢）
    #       候选池只需元数据；stream_url 由播放链路按需再取
```

`favorite_audio(audio_id, user_id)`（第 433~467 行）已实现 Emby 收藏写入，直接复用。

### 5.2 算法层 — 新建 `xiaomusic/utils/daily_recommend.py`

核心模块，约 300~400 行。公开接口：

```python
@dataclass
class DailyRecommendation:
    tracks: list[Audio]
    reason: str
    source: str        # "llm" | "local"
    date: str          # YYYY-MM-DD（本地日期，按 Config.timezone）
    renewals: int      # 当天「换一批」已用次数
    playlist_name: str = "今日推荐"

def build_catalog(candidates: list[Audio]) -> dict: ...
async def ask_llm(catalog: dict, cfg: Config) -> dict: ...
def validate_recipe(recipe: dict, catalog: dict) -> Recipe: ...   # fatal 抛 RecipeInvalid
def pick_tracks(recipe: Recipe, candidates: list[Audio], catalog: dict) -> list[Audio]: ...
def fallback(candidates: list[Audio]) -> DailyRecommendation: ...
async def generate(cfg: Config, emby: EmbyUtil) -> DailyRecommendation: ...
```

**① 特征目录 `build_catalog`**（发给 LLM，≤6000 字符）

- `vocabulary.genres`：候选池流派 top 10（带计数）
- `vocabulary.artists`：top 8 白名单
- `vocabulary.decades`：top 8（由 `production_year // 10 * 10` 生成 `"1990s"`）
- `vocabulary.rating_tiers`：`unrated / low / mid / high`（按 play_count 分档近似，Emby 无评分则退化为收藏标记）
- `vocabulary.intents`：`comfort / energize / focus / discover / nostalgia / mellow / celebrate`
- `taste_vector.genres`：按流派聚合 `收藏×3 + play_count归一化×1`，归一化保留 2 位小数，取 top 6
- `limits`：`{max_tracks: 20, min_tracks: 10, per_artist_cap: 4, min_genres: 3}`
- `pending_telemetry`：注明「最近播放/搜索/跳过未埋点」，提示模型不要假装能用

**② LLM 客户端 `ask_llm`**

- `POST {llm_endpoint}/chat/completions`，`Authorization: Bearer <key>`，**绝不打印 key**
- `temperature=0.85`、`max_tokens=700`、总超时 30s / 连接超时 10s
- 三级降级：`response_format={type:"json_schema", strict:true, schema}` → `json_object` → 纯 prompt
- 任何一级解析成功后**仍要过本地业务校验**
- 三级全失败 → 由 `generate` 走 `fallback`

**③ recipe 契约（Pydantic，`extra="forbid"` 强制红线）**

```python
class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")   # 出现 tracks/ids/titles 等字段 → 整份作废
    schema_version: int                         # 必须 == 1
    intent: Literal["comfort","energize","focus","discover",
                    "nostalgia","mellow","celebrate"]
    reason: str                                 # ≤120 字，中文，一句话，不含歌名
    include: Include | None = None              # genres/artists/decades
    exclude: Exclude | None = None
    boost: list[Boost] = []                     # {value, weight∈[0,2]}
    weights: Weights = Weights()                # familiarity/novelty/mood_match ∈[0,1]
```

校验优先级（照搬原设计）： fatal（版本/必填/单曲字段/意图越界 → 整份作废） → 目录外枚举值丢弃 → include∩exclude 时 exclude 胜出 → boost 越界丢弃、weights 越界钳到 0.5 → 候选 <10 首或 <3 流派 → 作废回退。

**④ 本地选曲 `pick_tracks`**

- 按 `include.exclude` 过滤候选池，无规则时保底全取
- 打分（简化版，对齐原实现）：
  ```
  familiar = min(play_count / 20, 1.0)
  score = (1 + taste_weight(genre) * 1.5) * boost(genre)
          + familiar * weights.familiarity
          + (1 - familiar) * weights.novelty * 0.3
  ```
- 贪心按 score 降序；每艺术家 ≤4；记录流派集合
- 护栏：结果 <10 首或 <3 流派 → 返回空 → `generate` 走 fallback
- 截断到 `daily_recommend_count`（默认 20）

**⑤ 失败兜底 `fallback`**

规则：`is_available` 全取 + 按 play_count 降序 + 流派轮转，本地文案 reason「本地每日混歌」，`source="local"`，**静默、不弹错、不冒充 LLM**。

**⑥ 编排 `generate`**

```
读缓存 → 命中当天且 renewals<3 → 直接返回（当天冻结）
      → 拉取候选池（asyncio.to_thread）
      → build_catalog → ask_llm → validate_recipe → pick_tracks
      → 成功：写 cache（date/playlist/reason/source/renewals），返回
      → 任何失败：fallback
```
日期键用 `Config.timezone`（默认 Asia/Shanghai）的本地日期，不用 UTC。

### 5.3 配置层 — `xiaomusic/config.py` + `static/default/setting.html`

在 `Config`（第 101~186 行）新增字段，类型注解会自动完成转换与 `asdict` 持久化：

```python
enable_daily_recommend: bool = os.getenv("XIAOMUSIC_ENABLE_DAILY_RECOMMEND", "false").lower() == "true"
llm_endpoint: str = os.getenv("XIAOMUSIC_LLM_ENDPOINT", "")
llm_api_key: str = os.getenv("XIAOMUSIC_LLM_API_KEY", "")
llm_model: str = os.getenv("XIAOMUSIC_LLM_MODEL", "")
daily_recommend_count: int = int(os.getenv("XIAOMUSIC_DAILY_RECOMMEND_COUNT", "20"))
```

- 检查 `utils/system_utils.py::deepcopy_data_no_sensitive_info` 的脱敏字段名列表，把 `llm_api_key` 加进去。
- `static/default/setting.html` 加同 id 的 `<input>`；`setting.js` 的通用循环（填充 + 收集）会自动回显和提交，JS 基本不用改。api_key 按密码字段处理脱敏。

### 5.4 编排与触发层

**`xiaomusic/xiaomusic.py` 新增两个方法：**

```python
async def generate_daily_recommend(self, did="", renew=False) -> DailyRecommendation:
    # asyncio.to_thread(emby.fetch_recommend_candidates, ...)
    # await daily_recommend.generate(cfg, emby)
    # 关键陷阱：play_list_update_music 只认 all_music 里已有的歌名，
    #   必须先把选中 Audio 按 f"{name} - {album} - {album_artist}" 塞进 all_music
    #   （照抄 xiaomusic.py:426-428 的写法）
    # music_library.play_list_update_music("今日推荐", names)  → 落盘到 custom_play_list_json
    # 可选：do_tts(did, reason)

async def daily_recommend(self, did="", **kwargs):
    rec = await self.generate_daily_recommend(did)
    await self.do_play_music_list(did, "今日推荐", rec.tracks[0].name)
    await self.do_tts(did, rec.reason)
```

**语音命令注册（`config.py`）：**

- `command_action_dict`（第 13~80 行）加一条，**必须放在第 74 行「终极兜底匹配」之前**：
  ```python
  r"^(?:播(?:放)?|来点)(?:今日|今天的)推荐(?:歌曲|音乐|歌单|的歌)?$": "daily_recommend",
  ```
- `active_cmd` 默认值（第 121~124 行）追加 `daily_recommend`，否则设备空闲时语音不触发。
- `command_handler.py` 无需改动（反射调用 `getattr`）。

**定时任务（P2，`xiaomusic/crontab.py`）：**

新增 `add_job_daily_recommend`（照抄现成的 `add_job_play_music_tmp_list`，第 134~146 行），可配置为每天定点生成；语音触发时直接播放当天已生成结果。

### 5.5 展示层

- reason：`XiaoMusicDevice.do_tts(did, reason)` 播报（音色走 `Config.edge_tts_voice`）。
- Web：歌单只显示曲名；reason 存 `cache/daily_recommend.json` 与日志。

---

## 6. 文件改动清单

| 文件 | 改动 | 阶段 |
|---|---|---|
| `xiaomusic/config.py` | +5 配置项、+命令正则、+active_cmd | P0 |
| `xiaomusic/utils/emby_utils.py` | Audio +4 字段、解析填充、+候选池方法 | P0 |
| **新建** `xiaomusic/utils/daily_recommend.py` | 特征目录 / LLM 客户端 / 校验 / 选曲 / 兜底 / 编排 | P0 |
| `xiaomusic/xiaomusic.py` | +`generate_daily_recommend`、+`daily_recommend` | P0 |
| `xiaomusic/utils/system_utils.py` | 脱敏名单 +`llm_api_key` | P0 |
| `static/default/setting.html` | +LLM 配置输入框 | P0 |
| `tests/test_command_matching.py` | +命令匹配用例（直接遍历命令字典，无需 mock） | P0 |
| `xiaomusic/crontab.py` | +`add_job_daily_recommend` | P2 |
| 可选 `cache/daily_recommend.json` | 运行时生成（gitignore 已含 cache/？需确认） | 运行时 |

不引入数据库，不新增第三方依赖，全程复用现有 JSON 配置、`requests`、`aiohttp` 与歌单机制。

---

## 7. 分阶段实施

- **P0 — MVP（本次分支目标）**：配置 + 数据层 + 核心算法 + 语音触发 + 歌单落库 + TTS 理由 + 命令测试。完成后说「播放今日推荐」即可端到端跑通。
- **P1 — 稳健性**：测试连接按钮、HTTP 手动触发接口、reason 多候选容错、Emby 候选池分页/上限保护。
- **P2 — 增强（rustmusic 对齐）**：7 天半衰期口味画像（需先建行为事件表）、不喜欢软降权、MMR 多样性、定时任务、Web 端展示 reason。

---

## 8. 验收标准

1. 配置 LLM 三项后，说「播放今日推荐」→ 生成「今日推荐」歌单并开始播放，TTS 播报一句中文理由。
2. 同一天再次触发 → 命中缓存，歌单与理由不变（当天冻结）；连续「换一批」超过 3 次后不再变化。
3. 不配置 LLM / 网络失败 / LLM 返回非法 recipe / 候选不足 → 静默回退本地混歌，reason 标明「本地每日混歌」，不报错、不冒充 LLM。
4. LLM 输出若含 `tracks` / `ids` / `titles` 等单曲字段 → 整份作废并回退。
5. 生成的歌单曲目均来自 Emby，每艺术家 ≤4，数量 ≥10（默认 20），覆盖 ≥3 个流派。
6. 歌单落盘到 `conf/setting.json` 的 `custom_play_list_json`，重启后仍在。
7. `tests/test_command_matching.py` 中新增命令匹配用例通过；`pytest` 全绿。

---

## 9. 风险与待定

| 风险 | 说明 | 缓解 |
|---|---|---|
| Emby 元数据缺失 | 部分库可能无 Genres / ProductionYear / PlayCount | 降级为收藏 + 随机；候选不足走 fallback |
| 候选池拉全库慢 | 大库 `Limit=200` 仍可能慢 | `asyncio.to_thread` 不阻塞事件循环；超时保护 |
| `play_list_update_music` 只认内存歌名 | 启动时 `all_music` 仅 50 首 | 生成时把选中 Audio 塞进 `all_music`（方案已含） |
| `saveconfig` 触发 `reinit()` | 频繁调用会重复拉歌、重载定时任务 | 缓存写自建 JSON；歌单保存走 `play_list_update_music` |
| 命令字典先匹配先生效 | 新正则若放兜底后会被吞 | 放在第 74 行之前（方案已标明） |
| 口味画像与 rustmusic 有差距 | 无埋点、无半衰期 | P2 演进；当前用「收藏+PlayCount+分布」近似 |

**待定（评审时可裁决）**：推荐数量默认值（建议 20）、是否 P0 就做定时生成、是否给 Web 设置页加「测试连接」按钮。
