# 今日推荐歌单 v2 设计方案：新鲜度与丰富度

> 状态：**已实现**（2026-11-22，在 `feature/daily-recommend-playlist` 分支）
> 实测：候选池 800 首覆盖 45 个流派（原 200 首随机采样）；端到端生成 20 首、`source=llm`，
> DeepSeek 理由明确"避开已推过的组合"，歌单覆盖 12 个流派 / 16 位歌手；268 项测试全过。
> 日期：2026-11-22
> 分支：`feature/daily-recommend-playlist`
> 上游设计：`docs/daily-recommendation-design.md`（v1）、rustmusic-desktop 的 LLM Daily Recommendation

---

## 1. 为什么 v1 不够好

用户反馈：歌单质量已有提高，但**没有新鲜度和丰富度**，担心"每天都是差不多的"。

对当前代码做取证诊断后，根因按影响程度排序如下：

| # | 根因 | 证据 | 后果 |
|---|---|---|---|
| 1 | **候选池只有 200 首随机采样** | `CANDIDATE_LIMIT=200`，`fetch_recommend_candidates()` 用 `SortBy=Random`；用户库约 7 万首 | 覆盖 0.29%，长尾/冷门/老歌几乎不可见；LLM 以为"全库只有 200 首"；打分排序实际是在给抽样噪声排序，跨天不可复现 |
| 2 | **"换一批"是死代码** | `renew` 参数只存在于内部函数；语音口令、HTTP、cron 都没有入口；缓存里 `renewals=0` | 用户被锁死一整天，没有任何补救手段 |
| 3 | **打分塌缩为 `taste_weight × boost`，两个权重是死的** | `PlayCount` 全为 0 → `familiar=0` → `familiarity` 加分恒为 0、`novelty` 项恒为常数；`boost∈[1,2]` 乘法碾压全部加项 | LLM 给的 `novelty`/`familiarity` 完全不起作用；同 boost 流派连续入选 |
| 4 | **历史降权只作用于流派** | `_save_history()` 记录了 `track_ids`/`artists`，但 `_recommend_penalty()` 只读 `genres` | 同一首歌、同一歌手可跨天反复出现；且历史已覆盖 10 个主流流派 → 全局统一降权，相对排序不变，等于没降 |
| 5 | **多样性护栏极弱** | 第一阶段 `MIN_GENRES=3`，且一首多流派歌就能满足；无 MMR、无年代/语言/专辑维度；`PER_ARTIST_CAP=4` | 20 首可只来自 5 位歌手；歌单是主流熟悉曲目的窄切片 |
| 6 | **prompt 信号单一且自相矛盾** | catalog 只注入 `recently_played_genres`；规则 11"最近流派优先"与规则 12"novelty≥0.4"冲突；DeepSeek 不支持 json_schema，schema 降级后 recipe 被大幅裁剪 | LLM 只能围绕最近听的流派打转，产出保守的 comfort 模板 |
| 7 | **重启后歌单不可播** | 生成时把 Audio 注册进 `all_music`，但重启后 `all_music` 只有 50 首；`get_music_url` 直接下标访问 | 昨日推荐无法回听，无法形成稳定的口味反馈闭环 |

**一句话**：v1 把"新鲜度"寄托在 LLM 的 `novelty` 权重上，而这个权重在你的数据上**根本不起作用**。这是最关键的认知错误。

---

## 2. 业内做法研究结论

### 2.1 四个概念必须分清

| 概念 | 定义 | 度量 |
|---|---|---|
| Novelty 新颖性 | 对**该用户**没接触过 | `1 − P(被交互)`；与用户历史比，而非与全人群比 |
| Freshness 新鲜度 | **时间上新发行** | `DateCreated` 距今天数（YouTube Music 定义 7 天内为 new release） |
| Diversity 多样性 | 列表内部彼此差异 | Intra-List Similarity；集合覆盖的 genre/artist/decade 数 |
| Serendipity 意外之喜 | 出乎意料**且**确实有用 | `Unexpectedness × Relevance` |

关键辨析（RecSys 2026 YouTube Music 论文）：**去偏 ≠ 新鲜度**；Novelty（没听过的旧歌）与 Freshness（新发行）是两个独立目标。对用户而言的"每天不重复"属于 novelty/repeat-avoidance。

### 2.2 可移植的机制（按性价比）

| 机制 | 原理 / 参数 | 适配 xiaoemby |
|---|---|---|
| **MMR**（Carbonell 1998） | `argmax[λ·Rel − (1−λ)·max sim]`；λ=0.6~0.8，熟悉型取 0.8、发现型 0.6 | ✅ 纯 Python，200~800 候选毫无压力，首选 |
| **Repeat penalty / 排除窗** | Navidrome `notInTheLast 90 天`、Troi 60 天、Spotify 30 天 snooze | ✅ 用 `LastPlayedDate`，**不依赖 PlayCount** |
| **Artist caps / Genre quotas** | Troi `max_artist_occurrence=2`；每流派 ≤40%；年代 ≥2 | ✅ 现设计已有雏形，需收紧 |
| **探索配额 ε-greedy** | `k = round(ε·N)`，ε≈0.15~0.2；无反馈闭环时用固定配额而非真学习 | ✅ 无需新增底座 |
| **Freshness boost** | `DateCreated` ≤30 天 ×1.2 | ✅ Emby 有 `DateCreated` |
| **熟悉锚点** | Spotify Discover Weekly 保留 1~2 首熟悉曲目建立信任 | ✅ 用 Emby 收藏替代 PlayCount |
| **半衰期口味画像** | `w = 2^(−Δt/7)`，7 天半衰期 | ✅ 用 `LastPlayedDate` + 收藏在滚动窗口聚合 |
| DPP / bandit 学习 / 去偏塔 / 音频 embedding | 需模型、训练数据、feedback 闭环 | ❌ 无底座，收益不抵成本 |

⚠️ 重要结论：RecSys 2026 发现"服务层启发式会被持续训练的学习环中和"（day-1 有效、几天后归零）。**xiaoemby 没有在线学习模型，反而不受影响**——本地规则能持久生效，这是小系统的优势。

### 2.3 商业产品的"每天不重复"工程手段

1. `lastPlayed` 硬窗口（30/60/90 天）或软时间衰减；
2. 收藏/不喜欢 veto（可逆、有时效，非永久封杀）；
3. **分池轮转**：近 30 天做 Daily Mix、近 90 天没听做 Discoveries；
4. **列表冻结**：Navidrome `refreshDelay: 1d`、Troi `expires_at=+2 周`，采样时间与冻结时间分离；
5. JSON/本地历史里记最近 N 天曲目做惩罚。

---

## 3. v2 设计

### 3.1 设计原则

**保留的红线**（不可谈判）：
- LLM 只输出特征组合 recipe + 一句理由，绝不返回单曲 id/标题/path；
- 选曲、去重、落库全部本地完成；
- 失败静默回退、当天冻结。

**三个理念转变**：

| v1 | v2 |
|---|---|
| novelty = 低 PlayCount | novelty = **没听过 / 最近没推荐过 / 最近新增** |
| 熟悉度 = PlayCount（实际全 0） | 熟悉度 = **IsFavorite + LastPlayedDate** |
| 多样性 = ≥3 流派 | 多样性 = **MMR + 多维配额 + 跨天曲目/歌手去重复** |

### 3.2 机制总览

| 机制 | 参数 | 解决的问题 |
|---|---|---|
| 候选池分层采样 | 上限 800，按流派 × 年代分桶 | 丰富度天花板、抽样噪声 |
| 重复硬窗 + 三张 penalty 表 | 30 天硬窗；track 0.7 / artist 0.5 / genre 0.3（半衰期 3~7 天） | 每天不重复 |
| 曲目级 familiarity / novelty | familiar = 收藏 1.0 / 7 天内播放 0.6 / 否则 0；novelty 系数 ×1.5 | 救活死权重 |
| 探索配额 | 20 首留 3~4 首（ε=0.15~0.2） | 新鲜感 |
| 熟悉锚点 | 前 2 首从收藏取 | "惊喜但不突兀" |
| MMR 重排 | λ=0.7；sim = 0.5·流派 Jaccard + 0.3·年代 + 0.2·艺人 | 列表内丰富度 |
| 多维配额 | 每艺人 ≤3、每专辑 ≤2、每流派 ≤40%、年代 ≥2 种 | 去同质化 |
| 换一批入口 | 语音 + HTTP，3 次/天 | 用户可控 |

### 3.3 候选池：分层采样（抬高丰富度天花板）

**问题**：200 首随机样本让"丰富度"从根上受限。

**方案**：
1. `CANDIDATE_LIMIT` 200 → **800**；
2. `fetch_recommend_candidates()` 改为**分桶采样**：
   - 先 `GET /emby/MusicGenres` 拿全库流派清单（当前实测 54 个）；
   - 按 `Genres` 分桶，每桶按 `SortBy=Random` 取 `800/流派数` 首；
   - 再补一个"最近新增"（`SortBy=DateCreated, Limit=100`）桶；
   - 合并去重。
3. `catalog["library"]["tracks"]` 传**真实库总量**（新增 `GET /Users/{id}/Items?Limit=0` 拿 TotalRecordCount），避免 LLM 按 800 首理解世界。

**代价**：约 30~60 次额外 HTTP（可并发），耗时约 1~3 秒，用 `asyncio.to_thread` 不阻塞。

### 3.4 新鲜度：重复抑制 + 探索配额

**历史数据结构扩展**（`cache/recommend_history.json`）：

```json
{
  "2026-11-22": {
    "track_ids": ["..."],
    "artists": ["..."],
    "genres": ["..."],
    "decades": ["1990s", "2000s"]
  }
}
```

**三张 penalty 表**（`_recommend_penalty()` 扩展）：

```python
track_pen   = max(0.7 * 0.5**(age/3), ...)   # 同一首歌，最高降 70%
artist_pen  = max(0.5 * 0.5**(age/5), ...)   # 同一歌手，最高降 50%
genre_pen   = 相对化：只惩罚当天 top3 流派  # 避免全表均匀缩放
```

**重复硬窗**：
- 最近 30 天内 `LastPlayedDate` 有记录的曲目**排除**出候选池；
- 候选不足 10 首时按 `30 → 14 → 7 天` 逐级放宽（Aurora 的 automatic relaxation）；
- 用 `LastPlayedDate` 而非 `PlayCount`（已验证有值）。

**探索配额**：
- 20 首中留 `k=3~4` 首探索位，从"低熟悉度 + `DateCreated` 近 30 天 + 冷门流派"里取；
- 仅当 `intent=discover` 时启用完整配额，其它意图留 1~2 首；
- 前 2 位用**收藏曲目**做熟悉锚点（Spotify 信任结论）。

### 3.5 丰富度：MMR + 多维配额

**第二阶段选曲从"按分数补齐"改为 MMR**：

```python
sim(a, b) = 0.5 * jaccard(a.genres, b.genres)
          + 0.3 * (1 if a.decade == b.decade else 0)
          + 0.2 * (1 if a.artist == b.artist else 0)

score_mmr(a) = 0.7 * base_score(a) - 0.3 * max(sim(a, s) for s in selected)
```

**第一阶段加强**：
- `MIN_GENRES` 3 → **5**；
- 每个 diversity key 取 **2 首**而非 1 首；
- `diversity_seen` 只记**当前 key**，不被一首多流派歌一次喂饱。

**多维配额**：
- `PER_ARTIST_CAP` 4 → **3**；
- 每专辑 ≤2 首；
- 每流派 ≤40%（20 首即 ≤8 首）；
- 年代 ≥2 种（从 `decade_counter` 里覆盖）。

### 3.6 打分公式（新）

```python
def base_score(audio, recipe, taste_vector, penalties):
    # 熟悉度：收藏 / 最近播放，不再依赖 PlayCount
    familiar = 1.0 if a.is_favorite else (0.6 if played_within(a, 7) else 0.0)

    # 口味分：半衰期 7 天加权的流派向量（来自 LastPlayedDate + 收藏）
    taste = taste_vector.get(primary_genre, 0.0)

    # boost 改为加法并封顶，消除乘法碾压
    boost_bonus = min(recipe.boost.get(key, 1.0) - 1.0, 0.5) * 0.6

    # 跨天去重复（曲目 / 歌手 / 流派三张表）
    repeat = 1 - 0.7*track_pen - 0.5*artist_pen - 0.3*genre_pen

    # 新歌加成（DateCreated ≤30 天）
    fresh = 1.2 if created_within(a, 30) else 1.0

    # novelty 真正生效：对"没听过 + 最近没推荐 + 新发行"给大额加分
    novelty = (1 - familiar) * recipe.weights.novelty * 1.5

    return (1 + taste * 1.5 + boost_bonus) * repeat * fresh \
           + familiar * recipe.weights.familiarity \
           + novelty
```

`DETAIL_FIELDS` 补 `PlayCount,IsFavorite`（`IsFavorite` 已在 UserData 中，显式请求更稳）。

### 3.7 LLM 交互改造

1. catalog 增加 `behavior.recently_recommended_genres / artists / tracks`（来自 history），并注明"这些要回避"；
2. 解决 prompt 规则 11/12 冲突：
   - 规则 11 改为"recently_played_genres 用于口味参考，但**不得连续 2 天占 include 主导**"；
   - 规则 12 保留并强化"novelty≥0.4，必须包含至少一个探索性流派"；
3. 针对 DeepSeek 直接用 `json_object`（不再浪费一次 json_schema 400 请求）——可通过配置项 `llm_supports_schema` 控制，默认 false；
4. 解析失败时带错误反馈**重试一次**再回退。

### 3.8 用户入口：换一批

- 语音口令：`^(?:换(?:一批|一拨)|再来一批)(?:今日|今天的)?推荐?$` → `daily_recommend` handler 的 `renew=True` 分支；
- HTTP：复用 `POST /cmd` 传"换一批今日推荐"；
- 每次换批把上一批写入历史（强制差异化），播报剩余配额；
- `renewals >= 3` 时提示"今日换批次数已用完"。

### 3.9 重启可播修复

- `restore()` 恢复缓存时把曲目按 id 注册进 `all_music`（与语音 handler 一致）；
- `get_music_url()` 对缺失 name 增加"按 id 回查 Emby"兜底；
- 或对"今日推荐"歌单做按需懒加载。

---

## 4. 三档方案对比（供你选择）

| | 轻量版 | **标准版（推荐）** | 增强版 |
|---|---|---|---|
| 内容 | 换一批入口 + 三张 penalty 表 + 修正死权重 | 轻量版 + 分层候选池(800) + MMR(λ=0.7) + 多维配额 + 探索配额 + 熟悉锚点 | 标准版 + 引导游走 + UCB 冷门簇奖励 + 30 天 snooze 名单 |
| 预期效果 | 同一首歌/歌手不再跨天重复；当天可换批 | 每天有没听过的歌但不突兀；歌单内部明显更杂 | 接近商业产品的信任/探索平衡 |
| 成本 | ~0.5 天 | ~2 天 | ~4 天，JSON 状态更复杂 |
| 风险 | 低 | 中（候选池采样耗时、参数需调） | 中高（需迁移、更多状态） |

**建议**：先做标准版。它包含了所有"投入产出比高"的改动，且不引入新底座。增强版列为后续演进。

---

## 5. 改动清单（标准版）

| 序 | 文件 | 改动 |
|---|---|---|
| 1 | `xiaomusic/utils/emby_utils.py` | `CANDIDATE_LIMIT→800`；`fetch_recommend_candidates()` 改分层采样；`DETAIL_FIELDS` 补字段；新增 `fetch_recently_played()`（已做） |
| 2 | `xiaomusic/utils/daily_recommend.py` | 三张 penalty 表、MMR、多维配额、曲目级 familiarity/novelty、探索配额、熟悉锚点、prompt 改造、`json_object` 直连 |
| 3 | `xiaomusic/config.py` | 新增语音口令 + `active_cmd`；新增 `llm_supports_schema`、`explore_ratio` 等配置项 |
| 4 | `xiaomusic/xiaomusic.py` | `daily_recommend` 支持 `renew`；`restore()` 注册 `all_music` |
| 5 | `xiaomusic/music_library.py` | `get_music_url()` 缺名兜底 |
| 6 | `tests/test_daily_recommend.py` | 覆盖每条新约束（MMR、配额、penalty、探索配额）的单元测试 |
| 7 | `docs/daily-recommendation-design-v2.md` | 本文档（已建） |

不引入数据库，不新增第三方依赖。

---

## 6. 验收标准

1. 连续 3 天生成，每天的歌单**曲目重合率 < 20%**、**歌手重合率 < 40%**（自动化脚本对比 `recommend_history.json`）。
2. 同一首歌在 30 天内不重复出现；同一歌手 3 天内不重复占据榜首。
3. 20 首歌单覆盖 ≥5 个流派、≥2 个年代、≤40% 单一流派、每歌手 ≤3 首、每专辑 ≤2 首。
4. 每次生成含 3~4 首"最近 30 天没听过 / 新发行"的探索曲目。
5. 说"换一批今日推荐"能重新生成，且新歌单与上一批曲目重合率 < 30%；3 次后提示配额用完。
6. 服务重启后"今日推荐"歌单可直接播放，不报 KeyError。
7. `pytest` 全绿，新增约束均有测试覆盖。

---

## 7. 风险与待确认

| 风险 | 说明 | 缓解 |
|---|---|---|
| 分层采样耗时 | 30~60 次 HTTP | 并发请求 + 缓存流派清单；超时降级为普通随机采样 |
| 参数需要调优 | λ、ε、penalty 系数、配额阈值 | 集中在模块顶部常量，便于 A/B |
| 探索曲目可能突兀 | 冷门流派听感跳跃 | 熟悉锚点 2 首 + 探索位穿插而非集中 |
| 历史文件膨胀 | 14 天 × 20 首 | 自动裁剪，保持 <100KB |
| MMR 相似度粗糙 | 无音频 embedding，只有标签重叠 | 接受；v2 是"可解释的规则"而非黑盒 |

**需要你拍板的点**：
1. 选哪档方案？（建议标准版）
2. 每天默认多少首？（当前 20，是否改 30）
3. 探索比例 ε=0.15（20 首留 3 首）是否合适？
4. "换一批"的语音口令措辞是否用"换一批今日推荐"？
5. 重复硬窗 30 天是否太长（你的库 7 万首，30 天足够；若希望更快轮换可改 14 天）？

---

请审阅。确认后我在当前分支按选定方案实现；如有调整，我改文档后再动手。
