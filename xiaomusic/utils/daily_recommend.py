"""今日推荐歌单：LLM 编排 + 本地选曲（v2 增强版）。

设计红线（继承自 rustmusic-desktop 的 LLM Daily Recommendation）：
LLM 只输出「特征组合 recipe + 一句理由」，绝不输出单曲 id / 标题 / path。
所有选曲、去重、落库都在本地完成 —— 无幻觉、可校验、可复现。

v2 增强版相对 v1 的核心变化：
- novelty 从「低 PlayCount」改为「没听过 / 最近没推荐 / 最近新增」；
- 熟悉度用 IsFavorite + LastPlayedDate，不再依赖 PlayCount；
- 历史降权覆盖曲目 / 歌手 / 流派三张表；
- MMR 重排 + 多维配额 + 探索配额 + 熟悉锚点 + 引导游走 + UCB 冷门簇奖励；
- 30 天 snooze 名单；换一批入口；当天冻结保留。
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

import requests
from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger("xiaomusic")

# ---- 常量 ----
INTENTS = (
    "comfort", "energize", "focus", "discover",
    "nostalgia", "mellow", "celebrate",
)
MAX_CATALOG_CHARS = 6000
# 口味画像：按时间窗口取全部播放记录，样本量自适应（不写死首数）
TASTE_WINDOW_DAYS = 45
TASTE_HALFLIFE_DAYS = 7
TASTE_MAX_RECORDS = 2000      # 拉取上限保护
MIN_TASTE_SAMPLES = 15        # 低于此认为口味信号弱，以探索为主
# 候选池：按库总量比例自适应
CANDIDATE_RATIO = 0.03
CANDIDATE_MIN = 800
CANDIDATE_MAX = 3000
GENRE_MAX = 40
CANDIDATE_LIMIT = CANDIDATE_MIN  # 兼容旧调用；正常路径用 None 触发自适应
MIN_TRACKS = 10
MAX_TRACKS_DEFAULT = 20
PER_ARTIST_CAP = 3
MIN_GENRES = 5
MAX_ALBUM_CAP = 2
MAX_GENRE_SHARE = 0.40
MIN_DECADES = 2
MAX_RENEWALS = 3
MMR_LAMBDA = 0.7
EXPLORE_RATIO = 0.15
ANCHOR_COUNT = 2
CLUSTER_CAP = 3
FRESHNESS_DAYS = 30
REPEAT_WINDOW_DAYS = 30
SNOOZE_DAYS = 30
HISTORY_DAYS = 14
RECENT_PLAY_LIMIT = TASTE_MAX_RECORDS  # 兼容别名，实际按时间窗口过滤
CACHE_FILENAME = "daily_recommend.json"
HISTORY_FILENAME = "recommend_history.json"
SNOOZE_FILENAME = "snooze.json"

SYSTEM_PROMPT = """你是本地音乐库的「节目编排助手」。你只会收到聚合后的库特征，看不到任何单曲。\
请把今天的音乐意图翻译成一个特征组合（recipe），本地引擎会据此从曲库里筛歌。

【输出格式：严格照抄下面这个 JSON 结构，不要增加任何其它顶层字段】
{
  "schema_version": 1,
  "intent": "comfort|energize|focus|discover|nostalgia|mellow|celebrate 之一",
  "reason": "一句中文，不超过120字，说明今天为什么这么配",
  "include": {"genres": [], "artists": [], "decades": []},
  "exclude": {"genres": [], "artists": [], "decades": []},
  "boost": [{"value": "词汇", "weight": 1.5}],
  "weights": {"familiarity": 0.5, "novelty": 0.5, "mood_match": 0.5},
  "soft_context": null,
  "segments": [
    {
      "theme": "熟悉开场",
      "transition": "先用几首熟悉的歌把情绪接住",
      "intent": "comfort",
      "share": 0.3,
      "include": {"genres": [], "artists": [], "decades": []},
      "exclude": {"genres": [], "artists": [], "decades": []},
      "boost": [{"value": "词汇", "weight": 1.5}],
      "weights": {"familiarity": 0.6, "novelty": 0.3, "mood_match": 0.5}
    },
    {
      "theme": "探索中段",
      "transition": "再往没听过的方向走一段",
      "intent": "discover",
      "share": 0.4,
      "include": {"genres": [], "artists": [], "decades": []},
      "exclude": {"genres": [], "artists": [], "decades": []},
      "boost": [],
      "weights": {"familiarity": 0.2, "novelty": 0.8, "mood_match": 0.5}
    },
    {
      "theme": "收尾沉淀",
      "transition": "最后回到平缓的节奏慢慢收束",
      "intent": "mellow",
      "share": 0.3,
      "include": {"genres": [], "artists": [], "decades": []},
      "exclude": {"genres": [], "artists": [], "decades": []},
      "boost": [],
      "weights": {"familiarity": 0.6, "novelty": 0.3, "mood_match": 0.6}
    }
  ]
}

硬性规则：
1. schema_version 必须是数字 1，不可省略。
2. boost 必须是数组，每项含 value 和 0~2 的 weight；不要写成对象。
3. weights 的 key 只能是 familiarity / novelty / mood_match，取值 0~1。
4. 不要输出 type / behavior / limits 等任何结构外字段。
5. 所有枚举值只能从 vocabulary 里挑，不要发明艺术家或流派。
6. 禁止输出任何单曲级字段（id/title/path/songs/tracks）——你不知道库里有哪些具体的歌。
7. reason 用一句中文写，不超过 120 字，不要出现歌名或路径。
8. soft_context 是低权重软偏好，没有就填 null。
9. include 与 exclude 不要包含同一值。
10. behavior.pending_telemetry 列出的信号表示未埋点，不要假装能用。
11. behavior.recently_played_genres 是用户最近在听的流派，可作口味参考，\
但不得连续两天让它占 include 主导（否则天天重复）。
12. behavior.recently_recommended_genres / artists 是最近几天已经推荐过的，\
应尽量回避，以保留新鲜度。
13. novelty 至少给 0.4；当 intent=discover 时至少给 0.7，\
并至少包含一个不在 recently_recommended_genres 里的探索性流派。

【节目分段 segments —— 这是最重要的部分】
14. segments 必须给 3~4 段，构成一条有起承转合的情绪曲线：\
第一段用熟悉曲目开场（familiarity 高、novelty 低），中段探索（novelty 高），\
末段平缓收束（mellow / comfort）。不要所有段都是同一种情绪。
15. 每段的 share 是曲目占比（0~1），所有段的 share 之和必须约等于 1。
16. 每段 theme 是一句中文短语（≤12 字，如「熟悉开场」「探索中段」「收尾沉淀」）；\
transition 是一句中文串联语（≤40 字），说明为什么从这里转到下一段，\
不要出现歌名、路径或具体歌手名。
17. 每段的 include/exclude/boost/weights 是该段的局部特征组合，\
和顶层一样只能从 vocabulary 挑值；intent 从 intents 枚举里选。
18. 顶层的 include/exclude/boost/weights 是全局约束，所有段都要满足；\
段内特征在全局约束基础上叠加，用来区分各段的情绪。

只输出一个 JSON 对象，不要输出其它文字。"""


# ---- 异常 ----
class RecipeInvalid(Exception):
    """recipe 未通过本地业务校验（fatal，整份作废）。"""


class LLMError(Exception):
    """LLM 不可用或输出格式全部失败。"""


# ---- recipe 契约（extra=forbid 强制红线）----
class _LooseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Include(_LooseModel):
    genres: list[str] = []
    artists: list[str] = []
    decades: list[str] = []


class Exclude(_LooseModel):
    genres: list[str] = []
    artists: list[str] = []
    decades: list[str] = []


class Boost(_LooseModel):
    value: str = ""
    weight: float = 1.0


class Weights(_LooseModel):
    familiarity: float = 0.5
    novelty: float = 0.5
    mood_match: float = 0.5


class Segment(BaseModel):
    """节目单中的一段：主题 + 串联语 + 该段的特征组合。"""

    model_config = ConfigDict(extra="ignore")
    theme: str = ""
    transition: str = ""
    intent: Literal[
        "comfort", "energize", "focus", "discover",
        "nostalgia", "mellow", "celebrate",
    ] = "comfort"
    share: float = 0.3
    include: Include | None = None
    exclude: Exclude | None = None
    boost: list[Boost] = []
    weights: Weights = Weights()


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int
    intent: Literal[
        "comfort", "energize", "focus", "discover",
        "nostalgia", "mellow", "celebrate",
    ]
    reason: str = Field(default="", max_length=120)
    include: Include | None = None
    exclude: Exclude | None = None
    boost: list[Boost] = []
    weights: Weights = Weights()
    soft_context: dict | None = None
    segments: list[Segment] = []

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError("schema_version 必须为 1")
        return v

    @field_validator("reason")
    @classmethod
    def _check_reason(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("reason 不能为空")
        if "/" in v:
            raise ValueError("reason 不能包含 /")
        return v


@dataclass
class DailyRecommendation:
    """一次推荐的结果；缓存只存 id/name，Audio 对象按需从 Emby 重建。"""

    track_ids: list[str]
    track_names: list[str]
    reason: str
    source: str  # "llm" | "local"
    date: str
    renewals: int
    tracks: list = field(default_factory=list)
    # 节目单：每段 {theme, transition, intent, track_ids, track_names}
    segments: list = field(default_factory=list)


def _age_days(date_str, today_str: str) -> int:
    """ISO 时间戳距今天数；Emby 的 LastPlayedDate 形如 2026-11-21T02:52:24Z。"""
    if not date_str or not today_str:
        return 99
    try:
        return max(
            (date.fromisoformat(today_str) - date.fromisoformat(str(date_str)[:10])).days,
            0,
        )
    except (ValueError, TypeError):
        return 99


def _artist_of(audio) -> str:
    artist = getattr(audio, "album_artist", "") or ""
    if not artist:
        album_artists = getattr(audio, "album_artists", None) or []
        artist = album_artists[0] if album_artists else "未知艺术家"
    return artist


def _decade_of(audio) -> str:
    year = int(getattr(audio, "production_year", 0) or 0)
    return f"{year // 10 * 10}s" if year >= 1900 else ""


# ---- 特征目录 ----
def build_catalog(
    candidates: list,
    locale: str = "zh",
    recently_played=None,
    recently_recommended: dict | None = None,
    today: str | None = None,
    library_total: int | None = None,
) -> dict:
    """把候选池聚合为 LLM 可见的库画像（只含聚合值，绝无单曲元数据）。"""
    genre_counter: Counter = Counter()
    artist_counter: Counter = Counter()
    decade_counter: Counter = Counter()
    taste: defaultdict = defaultdict(float)

    for audio in candidates:
        genres = [g for g in (getattr(audio, "genres", None) or []) if g]
        for genre in genres:
            genre_counter[genre] += 1
            taste[genre] += (3.0 if getattr(audio, "is_favorite", False) else 0.0) + max(
                int(getattr(audio, "play_count", 0) or 0), 0
            )
        artist_counter[_artist_of(audio)] += 1
        decade = _decade_of(audio)
        if decade:
            decade_counter[decade] += 1

    # 最近播放：按时间窗口取全部记录（样本量自适应，不写死首数），
    # 半衰期 7 天加权。听得多就样本多、听得少就样本少，不再用固定 50 首。
    recent_genres: Counter = Counter()
    taste_samples = 0
    if recently_played:
        for rank, audio in enumerate(recently_played):
            age = _age_days(getattr(audio, "last_played_at", ""), today)
            if age > TASTE_WINDOW_DAYS:  # 超出时间窗口的记录不计入口味画像
                continue
            taste_samples += 1
            decay = 2 ** (-age / TASTE_HALFLIFE_DAYS)
            recency = 1.0 / (1 + rank / 10.0)
            for genre in getattr(audio, "genres", None) or []:
                weight = 5.0 * decay * recency
                taste[genre] += weight
                recent_genres[genre] += weight

    ranked_taste = sorted(taste.items(), key=lambda item: -item[1])
    if not ranked_taste:
        # 无流派库：口味画像退化为艺术家维度
        artist_taste: defaultdict = defaultdict(float)
        for audio in candidates:
            artist = _artist_of(audio)
            if artist:
                artist_taste[artist] += (
                    3.0 if getattr(audio, "is_favorite", False) else 0.0
                ) + max(int(getattr(audio, "play_count", 0) or 0), 0)
        ranked_taste = sorted(artist_taste.items(), key=lambda item: -item[1])

    total_taste = sum(value for _, value in ranked_taste) or 1
    taste_vector = {
        key: round(score / total_taste, 2) for key, score in ranked_taste[:6]
    }

    catalog = {
        "schema_version": 1,
        "locale": locale,
        "library": {
            "tracks": library_total or len(candidates),
            "artists": len(artist_counter),
            "genres": len(genre_counter),
        },
        "vocabulary": {
            "genres": [g for g, _ in genre_counter.most_common(10)],
            "artists": [a for a, _ in artist_counter.most_common(8)],
            "decades": [d for d, _ in decade_counter.most_common(8)],
            "formats": ["flac", "mp3", "m4a"],
            "rating_tiers": ["unrated", "low", "mid", "high"],
            "intents": list(INTENTS),
        },
        "distribution": {
            "genres": [[g, n] for g, n in genre_counter.most_common(10)],
            "artists": [[a, n] for a, n in artist_counter.most_common(8)],
            "decades": [[d, n] for d, n in decade_counter.most_common(8)],
        },
        "behavior": {
            "available_now": {
                "favorite_count": sum(
                    1 for a in candidates if getattr(a, "is_favorite", False)
                ),
                "play_count_top_genres": [g for g, _ in ranked_taste[:4]],
            },
            "recently_played_genres": [g for g, _ in recent_genres.most_common(6)],
            "taste_confidence": round(min(1.0, taste_samples / MIN_TASTE_SAMPLES), 2),
            "taste_samples": taste_samples,
            "recently_recommended_genres": (recently_recommended or {}).get("genres", [])[:6],
            "recently_recommended_artists": (recently_recommended or {}).get("artists", [])[:6],
            "pending_telemetry": ["search", "skipped", "dislike"],
        },
        "taste_vector": {"genres": taste_vector},
        "limits": {
            "max_tracks": MAX_TRACKS_DEFAULT,
            "min_tracks": MIN_TRACKS,
            "per_artist_cap": PER_ARTIST_CAP,
            "min_genres": MIN_GENRES,
        },
    }
    if len(json.dumps(catalog, ensure_ascii=False)) > MAX_CATALOG_CHARS:
        catalog["distribution"] = {}
    return catalog


def recipe_schema() -> dict:
    """给 LLM 的 JSON Schema（additionalProperties:false 对应 extra=forbid）。"""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "intent", "reason"],
        "properties": {
            "schema_version": {"const": 1, "type": "integer"},
            "intent": {"type": "string", "enum": list(INTENTS)},
            "reason": {"type": "string", "maxLength": 120},
            "include": {"type": "object", "additionalProperties": False, "properties": {
                "genres": {"type": "array", "items": {"type": "string"}},
                "artists": {"type": "array", "items": {"type": "string"}},
                "decades": {"type": "array", "items": {"type": "string"}},
            }},
            "exclude": {"type": "object", "additionalProperties": False, "properties": {
                "genres": {"type": "array", "items": {"type": "string"}},
                "artists": {"type": "array", "items": {"type": "string"}},
                "decades": {"type": "array", "items": {"type": "string"}},
            }},
            "boost": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "value": {"type": "string"},
                    "weight": {"type": "number", "minimum": 0, "maximum": 2},
                },
            }},
            "weights": {"type": "object", "additionalProperties": False, "properties": {
                "familiarity": {"type": "number", "minimum": 0, "maximum": 1},
                "novelty": {"type": "number", "minimum": 0, "maximum": 1},
                "mood_match": {"type": "number", "minimum": 0, "maximum": 1},
            }},
            "soft_context": {"type": ["object", "null"]},
        },
    }


def ask_llm(catalog: dict, cfg) -> dict:
    """调 OpenAI-compatible /chat/completions；默认 json_object，解析失败重试一次。"""
    endpoint = (getattr(cfg, "llm_endpoint", "") or "").rstrip("/")
    if not endpoint:
        raise LLMError("未配置 LLM endpoint")
    url = f"{endpoint}/chat/completions"
    payload = {
        "model": getattr(cfg, "llm_model", ""),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(catalog, ensure_ascii=False)[:MAX_CATALOG_CHARS],
            },
        ],
        "temperature": 0.85,
        "max_tokens": 1600,  # 分段节目单比原来的扁平 recipe 长得多
    }
    headers = {
        "Authorization": f"Bearer {getattr(cfg, 'llm_api_key', '')}",
        "Content-Type": "application/json",
    }
    supports_schema = bool(getattr(cfg, "llm_supports_schema", False))
    modes = ("json_schema", "json_object", "plain") if supports_schema else ("json_object", "plain")
    last_error = "未知错误"
    # 两轮：第二轮带上错误反馈，让 LLM 修正偶发的 JSON 格式错误
    for attempt in range(2):
        for mode in modes:
            request_payload = dict(payload)
            if attempt == 1:
                request_payload["messages"] = list(payload["messages"]) + [
                    {
                        "role": "user",
                        "content": (
                            f"你上一次的输出无法解析（{last_error}）。"
                            "请严格只输出一个完整合法的 JSON 对象，不要输出其它文字或注释。"
                        ),
                    }
                ]
                request_payload["temperature"] = 0.4  # 重试时降低随机性
            if mode == "json_schema":
                request_payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "daily_recipe",
                        "strict": True,
                        "schema": recipe_schema(),
                    },
                }
            elif mode == "json_object":
                request_payload["response_format"] = {"type": "json_object"}
            try:
                response = requests.post(
                    url, json=request_payload, headers=headers, timeout=(10, 30)
                )
            except requests.RequestException as exc:
                last_error = f"网络错误: {exc}"
                continue
            if not response.ok:
                last_error = f"HTTP {response.status_code}"
                continue
            try:
                content = response.json()["choices"][0]["message"]["content"]
                return json.loads(content)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = f"响应解析失败: {exc}"
                continue
    raise LLMError(last_error)


# ---- 本地校验 ----
SINGLE_TRACK_KEYS = frozenset(
    {"tracks", "ids", "titles", "songs", "paths", "items", "music", "files"}
)


def sanitize_recipe(data: dict) -> dict:
    """宽容归一化 LLM 的常见格式偏差；单曲级字段一律作废。"""
    if not isinstance(data, dict):
        return data
    leaked = SINGLE_TRACK_KEYS & set(data)
    if leaked:
        raise RecipeInvalid(f"recipe 含单曲级字段: {', '.join(sorted(leaked))}")

    clean = {
        "schema_version": data.get("schema_version", 1),
        "intent": data.get("intent"),
        "reason": data.get("reason", ""),
    }
    for key in ("include", "exclude"):
        value = data.get(key)
        if isinstance(value, dict):
            clean[key] = {
                k: value[k] for k in ("genres", "artists", "decades") if k in value
            }
    boost = data.get("boost", [])
    normalized_boost = []
    if isinstance(boost, dict):
        for key in ("genres", "artists"):
            for value in boost.get(key, []) or []:
                normalized_boost.append({"value": value, "weight": 1.2})
    elif isinstance(boost, list):
        for item in boost:
            if isinstance(item, dict) and "value" in item:
                normalized_boost.append(item)
    clean["boost"] = normalized_boost
    weights = data.get("weights")
    if isinstance(weights, dict):
        clean["weights"] = {
            k: weights[k]
            for k in ("familiarity", "novelty", "mood_match")
            if k in weights
        }
    soft_context = data.get("soft_context")
    if isinstance(soft_context, dict):
        clean["soft_context"] = soft_context
    elif isinstance(soft_context, str):
        clean["soft_context"] = {"note": soft_context}
    if isinstance(data.get("segments"), list):
        clean["segments"] = data["segments"]
    return clean


def validate_recipe(data: dict, catalog: dict) -> Recipe:
    """本地业务校验：fatal 作废；目录外值丢弃；exclude 胜出；越界钳位。"""
    try:
        recipe = Recipe.model_validate(sanitize_recipe(data))
    except RecipeInvalid:
        raise
    except Exception as exc:
        raise RecipeInvalid(f"recipe 结构非法: {exc}") from exc

    vocab = catalog.get("vocabulary", {})
    allowed = {
        "genres": set(vocab.get("genres", [])),
        "artists": set(vocab.get("artists", [])),
        "decades": set(vocab.get("decades", [])),
    }

    def clean(values, key):
        return [v for v in (values or []) if v in allowed[key]]

    include = recipe.include or Include()
    exclude = recipe.exclude or Exclude()
    for key in ("genres", "artists", "decades"):
        setattr(include, key, clean(getattr(include, key), key))
        setattr(exclude, key, clean(getattr(exclude, key), key))
        excluded = set(getattr(exclude, key))
        setattr(include, key, [v for v in getattr(include, key) if v not in excluded])

    boosts = []
    for item in recipe.boost:
        if not math.isfinite(item.weight) or not 0 <= item.weight <= 2:
            continue
        if item.value not in allowed["genres"] and item.value not in allowed["artists"]:
            continue
        boosts.append(item)
    recipe.boost = boosts

    for name in ("familiarity", "novelty", "mood_match"):
        value = getattr(recipe.weights, name)
        if not math.isfinite(value) or not 0 <= value <= 1:
            setattr(recipe.weights, name, 0.5)

    recipe.include = include
    recipe.exclude = exclude

    # 段落校验：段数 2~5，share 归一化，权重/词汇沿用同样的清洗
    if recipe.segments:
        valid_segments = []
        for seg in recipe.segments:
            seg.theme = (seg.theme or "").strip()[:12]
            seg.transition = (seg.transition or "").strip()[:40]
            if not math.isfinite(seg.share) or seg.share <= 0:
                seg.share = 0.25
            seg_inc = seg.include or Include()
            seg_exc = seg.exclude or Exclude()
            for key in ("genres", "artists", "decades"):
                setattr(seg_inc, key, clean(getattr(seg_inc, key), key))
                setattr(seg_exc, key, clean(getattr(seg_exc, key), key))
                ex = set(getattr(seg_exc, key))
                setattr(seg_inc, key, [v for v in getattr(seg_inc, key) if v not in ex])
            seg.include = seg_inc
            seg.exclude = seg_exc
            seg.boost = [
                b for b in seg.boost
                if math.isfinite(b.weight) and 0 <= b.weight <= 2
                and (b.value in allowed["genres"] or b.value in allowed["artists"])
            ]
            for name in ("familiarity", "novelty", "mood_match"):
                v = getattr(seg.weights, name)
                if not math.isfinite(v) or not 0 <= v <= 1:
                    setattr(seg.weights, name, 0.5)
            valid_segments.append(seg)
        if 2 <= len(valid_segments) <= 5:
            total_share = sum(s.share for s in valid_segments) or 1
            for seg in valid_segments:
                seg.share = round(seg.share / total_share, 3)
            recipe.segments = valid_segments
        else:
            recipe.segments = []
    return recipe


# ---- 选曲（v2 增强版核心）----
def _similarity(a, b) -> float:
    """MMR 相似度：流派 Jaccard + 年代 + 艺人。"""
    genres_a = set(getattr(a, "genres", None) or [])
    genres_b = set(getattr(b, "genres", None) or [])
    union = genres_a | genres_b
    jaccard = len(genres_a & genres_b) / len(union) if union else 0.0
    decade = 0.3 if _decade_of(a) and _decade_of(a) == _decade_of(b) else 0.0
    artist = 0.2 if _artist_of(a) == _artist_of(b) else 0.0
    return 0.5 * jaccard + decade + artist


def _guided_walk_order(genres: list, taste_vector: dict) -> list:
    """引导游走的簇序列：熟悉簇 → 不同簇 → 次熟簇 → 次不熟簇（Pandora hot sauce 简化）。"""
    ranked = sorted(genres, key=lambda g: -taste_vector.get(g, 0.0))
    sequence = []
    left, right = 0, len(ranked) - 1
    while left <= right:
        sequence.append(ranked[left])
        left += 1
        if left <= right:
            sequence.append(ranked[right])
            right -= 1
    return sequence


def pick_tracks(
    recipe: Recipe,
    candidates: list,
    catalog: dict,
    max_tracks: int,
    penalties: dict | None = None,
    exposure: dict | None = None,
    explore_ratio: float = EXPLORE_RATIO,
    today: str | None = None,
    used_ids: set | None = None,
    artist_count_seed: Counter | None = None,
    album_count_seed: Counter | None = None,
    min_tracks: int | None = None,
    min_genres: int | None = None,
) -> list:
    """按 recipe 过滤 + MMR 重排 + 多维配额 + 探索配额，在本地完成选曲。

    used_ids / *_seed 供分段编排复用：跨段去重并累计歌手/专辑配额。
    min_tracks / min_genres 可为单段放宽护栏（None 表示用全局默认）。
    """
    include = recipe.include or Include()
    exclude = recipe.exclude or Exclude()
    penalties = penalties or {"genre": {}, "track": {}, "artist": {}}
    genre_pen = penalties.get("genre") or {}
    track_pen = penalties.get("track") or {}
    artist_pen = penalties.get("artist") or {}
    exposure = exposure or {}
    total_exposure = sum(exposure.values()) or 1
    today = today or datetime.now().strftime("%Y-%m-%d")

    exclude_genres = set(exclude.genres)
    include_genres = set(include.genres)
    include_artists = set(include.artists)
    include_decades = set(include.decades)
    exclude_artists = set(exclude.artists)
    exclude_decades = set(exclude.decades)

    def accepted(audio) -> bool:
        genres = set(getattr(audio, "genres", None) or [])
        artist = _artist_of(audio)
        decade = _decade_of(audio)
        if genres & exclude_genres or artist in exclude_artists:
            return False
        if decade and decade in exclude_decades:
            return False
        if include_genres or include_artists or include_decades:
            return bool(
                (include_genres and genres & include_genres)
                or (include_artists and artist in include_artists)
                or (include_decades and decade in include_decades)
            )
        return True

    filtered = [audio for audio in candidates if accepted(audio)]
    if used_ids:
        filtered = [
            audio for audio in filtered
            if str(getattr(audio, "id", "")) not in used_ids
        ]
    if not filtered:
        return []

    boost_map = {item.value: item.weight for item in recipe.boost}
    taste_vector = (catalog.get("taste_vector") or {}).get("genres") or {}
    has_genres = bool((catalog.get("library") or {}).get("genres"))

    def primary_genre(audio) -> str:
        return next(iter(getattr(audio, "genres", None) or []), "")

    def familiar_of(audio) -> float:
        if getattr(audio, "is_favorite", False):
            return 1.0
        if _age_days(getattr(audio, "last_played_at", ""), today) <= 7:
            return 0.6
        return 0.0

    def created_recently(audio) -> bool:
        return _age_days(getattr(audio, "date_created", ""), today) <= FRESHNESS_DAYS

    def base_score(audio) -> float:
        genres = getattr(audio, "genres", None) or []
        artist = _artist_of(audio)
        taste = max(
            [taste_vector.get(g, 0.0) for g in genres]
            + [taste_vector.get(artist, 0.0)]
            + [0.0]
        )
        boost = max(
            [boost_map.get(g, 1.0) for g in genres]
            + [boost_map.get(artist, 1.0)]
            + [1.0]
        )
        boost_bonus = min(boost - 1.0, 0.5) * 0.6  # 加法封顶，消除乘法碾压

        # 三张跨天降权表
        repeat = (1 - 0.7 * track_pen.get(str(getattr(audio, "id", "")), 0.0))
        repeat *= (1 - 0.5 * artist_pen.get(artist, 0.0))
        repeat *= (1 - 0.3 * max([genre_pen.get(g, 0.0) for g in genres] or [0.0]))

        fresh = 1.2 if created_recently(audio) else 1.0
        # UCB 式冷门簇奖励
        cluster = primary_genre(audio) or artist
        ucb = 0.3 * math.sqrt(math.log(1 + total_exposure) / (1 + exposure.get(cluster, 0)))

        familiar = familiar_of(audio)
        novelty = (1 - familiar + (0.3 if created_recently(audio) else 0.0)) * recipe.weights.novelty * 1.5
        return (
            (1 + taste * 1.5 + boost_bonus + ucb) * repeat * fresh
            + familiar * recipe.weights.familiarity
            + novelty
        )

    ranked = sorted(filtered, key=base_score, reverse=True)
    remaining = list(ranked)
    picked: list = []
    artist_count: Counter = Counter(artist_count_seed or {})
    album_count: Counter = Counter(album_count_seed or {})
    genre_count: Counter = Counter()
    decade_count: Counter = Counter()
    cluster_count: Counter = Counter()
    genre_cap = max(MIN_TRACKS, math.ceil(max_tracks * MAX_GENRE_SHARE))
    explore_k = max(1, round(explore_ratio * max_tracks)) if recipe.intent == "discover" else 1

    def can_take(audio) -> bool:
        artist = _artist_of(audio)
        # 跨天歌手降权：最近推荐过的歌手名额上限减 1
        cap = PER_ARTIST_CAP - (1 if artist_pen.get(artist, 0.0) >= 0.4 else 0)
        if artist_count[artist] >= cap:
            return False
        album_key = (getattr(audio, "album", ""), _artist_of(audio))
        if album_count[album_key] >= MAX_ALBUM_CAP:
            return False
        genre = primary_genre(audio)
        if genre and genre_count[genre] >= genre_cap:
            return False
        return True

    def take(audio) -> None:
        picked.append(audio)
        remaining.remove(audio)
        artist_count[_artist_of(audio)] += 1
        album_count[(getattr(audio, "album", ""), _artist_of(audio))] += 1
        for g in getattr(audio, "genres", None) or []:
            genre_count[g] += 1
        decade = _decade_of(audio)
        if decade:
            decade_count[decade] += 1
        cluster_count[primary_genre(audio) or _artist_of(audio)] += 1

    # 阶段 A：熟悉锚点（前 2 首从收藏里取，建立信任）
    favorites = [a for a in remaining if getattr(a, "is_favorite", False)]
    for audio in favorites[:ANCHOR_COUNT]:
        if audio in remaining and can_take(audio):
            take(audio)

    # 阶段 B：引导游走（流派簇交替 + 单簇 ≤ CLUSTER_CAP）
    if has_genres:
        all_genres = [g for g, _ in Counter(
            g for a in ranked for g in (getattr(a, "genres", None) or [])
        ).most_common(12)]
        for genre in _guided_walk_order(all_genres, taste_vector):
            if len(picked) >= max_tracks - explore_k:
                break
            if cluster_count[genre] >= CLUSTER_CAP:
                continue
            candidates_for_genre = [
                a for a in remaining
                if genre in (getattr(a, "genres", None) or []) and can_take(a)
            ]
            if not candidates_for_genre:
                continue
            best = max(candidates_for_genre, key=base_score)
            take(best)

    # 阶段 C：探索配额（没听过 / 新发行 / 冷门簇）
    explore_pool = [
        a for a in remaining
        if familiar_of(a) == 0.0
        and (created_recently(a) or exposure.get(primary_genre(a) or _artist_of(a), 0) == 0)
    ]
    explore_pool.sort(key=base_score, reverse=True)
    for audio in explore_pool[:explore_k]:
        if audio in remaining and can_take(audio):
            take(audio)

    # 阶段 D：MMR 补齐剩余名额
    while len(picked) < max_tracks and remaining:
        best = max(
            remaining,
            key=lambda a: MMR_LAMBDA * base_score(a)
            - (1 - MMR_LAMBDA) * max((_similarity(a, s) for s in picked), default=0.0),
        )
        if can_take(best):
            take(best)
        else:
            remaining.remove(best)

    # 护栏（分段模式下可放宽）
    min_t = MIN_TRACKS if min_tracks is None else min_tracks
    min_g = MIN_GENRES if min_genres is None else min_genres
    if len(picked) < min_t:
        return []
    if has_genres and len({g for a in picked for g in (getattr(a, "genres", None) or [])}) < min_g:
        return []
    available_decades = {_decade_of(a) for a in candidates if _decade_of(a)}
    if min_t > 0 and len(available_decades) >= MIN_DECADES and len(decade_count) < MIN_DECADES:
        return []
    return picked


def pick_tracks_segmented(
    recipe: Recipe,
    candidates: list,
    catalog: dict,
    max_tracks: int,
    penalties: dict | None = None,
    exposure: dict | None = None,
    explore_ratio: float = EXPLORE_RATIO,
    today: str | None = None,
) -> tuple[list, list]:
    """按 LLM 的节目单分段选曲，返回 (全部曲目, 段落元数据)。

    每段有自己的主题/串联语/特征组合；跨段去重、累计歌手与专辑配额，
    让整份歌单呈现「熟悉开场 → 探索中段 → 平缓收尾」的情绪曲线。
    """
    segments = [s for s in recipe.segments if s.share > 0]
    if not segments:
        return pick_tracks(
            recipe, candidates, catalog, max_tracks,
            penalties=penalties, exposure=exposure,
            explore_ratio=explore_ratio, today=today,
        ), []

    # 按 share 分配每段曲目数（至少 1，最后一段补齐余数）
    sizes = [max(1, round(max_tracks * s.share)) for s in segments]
    while sum(sizes) > max_tracks:
        idx = sizes.index(max(sizes))
        if sizes[idx] <= 1:
            break
        sizes[idx] -= 1
    sizes[-1] += max_tracks - sum(sizes)

    all_tracks: list = []
    used_ids: set = set()
    artist_count: Counter = Counter()
    album_count: Counter = Counter()
    metas: list = []

    for seg, size in zip(segments, sizes):
        # 段级 recipe：段特征优先，全局 exclude 始终叠加
        seg_inc = seg.include or Include()
        seg_exc = seg.exclude or Exclude()
        global_exc = recipe.exclude or Exclude()
        merged_exc = Exclude(
            genres=list(dict.fromkeys(seg_exc.genres + global_exc.genres)),
            artists=list(dict.fromkeys(seg_exc.artists + global_exc.artists)),
            decades=list(dict.fromkeys(seg_exc.decades + global_exc.decades)),
        )
        # 若段未给 include，则沿用全局 include
        sub = Recipe(
            schema_version=recipe.schema_version,
            intent=seg.intent,
            reason=recipe.reason,
            include=seg_inc if (seg_inc.genres or seg_inc.artists or seg_inc.decades) else recipe.include,
            exclude=merged_exc,
            boost=seg.boost if seg.boost else recipe.boost,
            weights=seg.weights,
        )
        part = pick_tracks(
            sub, candidates, catalog, size,
            penalties=penalties, exposure=exposure,
            explore_ratio=explore_ratio if seg.intent == "discover" else 0.05,
            today=today,
            used_ids=used_ids,
            artist_count_seed=artist_count,
            album_count_seed=album_count,
            min_tracks=0,   # 单段不强求 10 首
            min_genres=0,   # 单段不强求 5 流派
        )
        if not part:
            continue
        for audio in part:
            used_ids.add(str(getattr(audio, "id", "")))
            artist_count[_artist_of(audio)] += 1
            album_count[(getattr(audio, "album", ""), _artist_of(audio))] += 1
        start = len(all_tracks)
        all_tracks.extend(part)
        metas.append({
            "theme": seg.theme,
            "transition": seg.transition,
            "intent": seg.intent,
            "share": seg.share,
            "start": start,
            "count": len(part),
            "track_ids": [str(getattr(a, "id", "")) for a in part],
            "track_names": _audio_names(part),
        })

    if not all_tracks:
        return [], []
    return all_tracks, metas


def _pick_local(candidates: list, max_tracks: int) -> list:
    """兜底选曲：熟悉曲目优先 + 每艺术家上限 + 标签多样性。"""
    ordered = sorted(
        candidates,
        key=lambda a: (
            int(getattr(a, "play_count", 0) or 0),
            bool(getattr(a, "is_favorite", False)),
        ),
        reverse=True,
    )
    picked = []
    artist_count: Counter = Counter()
    genre_seen: set = set()
    for audio in ordered:
        artist = _artist_of(audio)
        if artist_count[artist] >= PER_ARTIST_CAP:
            continue
        artist_count[artist] += 1
        genre_seen.update(getattr(audio, "genres", None) or [])
        picked.append(audio)
        if len(picked) >= max_tracks:
            break
    # 兜底场景允许少于 MIN_TRACKS（冷启动总比没有强）
    return picked


# ---- 缓存 / 历史 / snooze / 日期 ----
def _today(cfg) -> str:
    timezone_name = getattr(cfg, "timezone", "") or "Asia/Shanghai"
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _cache_dir(cfg) -> str:
    cache_dir = getattr(cfg, "cache_dir", "cache") or "cache"
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _cache_path(cfg) -> str:
    return os.path.join(_cache_dir(cfg), CACHE_FILENAME)


def _load_cache(cfg) -> dict:
    try:
        with open(_cache_path(cfg), encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cfg, rec: DailyRecommendation) -> None:
    data = {
        "date": rec.date,
        "source": rec.source,
        "reason": rec.reason,
        "renewals": rec.renewals,
        "track_ids": rec.track_ids,
        "track_names": rec.track_names,
        "segments": rec.segments,
    }
    try:
        with open(_cache_path(cfg), "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except OSError as exc:
        log.error(f"保存今日推荐缓存失败: {exc}")


def _history_path(cfg) -> str:
    return os.path.join(_cache_dir(cfg), HISTORY_FILENAME)


def _load_history(cfg) -> dict:
    try:
        with open(_history_path(cfg), encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_history(cfg, rec: DailyRecommendation) -> None:
    """记录当天推荐过的曲目/流派/歌手/年代，供跨天新鲜度使用。"""
    history = _load_history(cfg)
    genres, decades = [], []
    for audio in rec.tracks:
        for genre in getattr(audio, "genres", None) or []:
            if genre not in genres:
                genres.append(genre)
        decade = _decade_of(audio)
        if decade and decade not in decades:
            decades.append(decade)
    history[rec.date] = {
        "track_ids": rec.track_ids,
        "genres": genres[:12],
        "artists": sorted({_artist_of(a) for a in rec.tracks}),
        "decades": decades[:8],
    }
    for day in list(history):
        try:
            age = (date.fromisoformat(rec.date) - date.fromisoformat(day)).days
        except ValueError:
            del history[day]
            continue
        if age < 0 or age > HISTORY_DAYS:
            del history[day]
    try:
        with open(_history_path(cfg), "w", encoding="utf-8") as handle:
            json.dump(history, handle, ensure_ascii=False, indent=2)
    except OSError as exc:
        log.error(f"保存推荐历史失败: {exc}")


def _snooze_path(cfg) -> str:
    return os.path.join(_cache_dir(cfg), SNOOZE_FILENAME)


def _load_snooze(cfg) -> dict:
    try:
        with open(_snooze_path(cfg), encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def snooze_track(cfg, track_id: str, days: int = SNOOZE_DAYS, today: str | None = None) -> None:
    """把某首歌加入 snooze 名单（30 天内不再推荐），以后可接「不喜欢」入口。"""
    snooze = _load_snooze(cfg)
    today = today or _today(cfg)
    until = date.fromisoformat(today).fromordinal(date.fromisoformat(today).toordinal() + days)
    snooze[str(track_id)] = until.isoformat()
    try:
        with open(_snooze_path(cfg), "w", encoding="utf-8") as handle:
            json.dump(snooze, handle, ensure_ascii=False, indent=2)
    except OSError as exc:
        log.error(f"保存 snooze 名单失败: {exc}")


def _active_snooze(cfg, today: str) -> set:
    result = set()
    for track_id, until in _load_snooze(cfg).items():
        try:
            if date.fromisoformat(today) <= date.fromisoformat(str(until)[:10]):
                result.add(str(track_id))
        except ValueError:
            continue
    return result


def _recommend_penalty(history: dict, today: str) -> dict:
    """三张跨天降权表：曲目 0.7 / 歌手 0.5 / 流派 0.3（半衰期 3~5 天）。

    流派只惩罚最近一天的 top3，避免历史覆盖过宽导致全表均匀缩放。
    """
    result: dict = {"genre": {}, "track": {}, "artist": {}}
    try:
        today_date = date.fromisoformat(today)
    except ValueError:
        return result
    for day, entry in history.items():
        try:
            age = (today_date - date.fromisoformat(day)).days
        except ValueError:
            continue
        if not 0 <= age <= HISTORY_DAYS:
            continue
        track_decay = 0.7 * 0.5 ** (age / 3)
        artist_decay = 0.5 * 0.5 ** (age / 5)
        genre_decay = 0.3 * 0.5 ** (age / 3)
        for track_id in entry.get("track_ids", []) or []:
            key = str(track_id)
            result["track"][key] = max(result["track"].get(key, 0.0), track_decay)
        for artist in entry.get("artists", []) or []:
            result["artist"][artist] = max(result["artist"].get(artist, 0.0), artist_decay)
        # 相对化：只惩罚当天主要流派（前 3 个）
        for genre in (entry.get("genres", []) or [])[:3]:
            result["genre"][genre] = max(result["genre"].get(genre, 0.0), genre_decay)
    return result


def _exposure_counts(history: dict, today: str, window_days: int = FRESHNESS_DAYS) -> Counter:
    """近 N 天各流派的曝光次数，供 UCB 冷门簇奖励使用。"""
    exposure: Counter = Counter()
    try:
        today_date = date.fromisoformat(today)
    except ValueError:
        return exposure
    for day, entry in history.items():
        try:
            age = (today_date - date.fromisoformat(day)).days
        except ValueError:
            continue
        if 0 <= age <= window_days:
            for genre in (entry.get("genres", []) or [])[:3]:
                exposure[genre] += 1
    return exposure


def _recently_recommended(history: dict, today: str) -> dict:
    """最近 3 天推荐过的流派/歌手，用于注入 catalog 让 LLM 主动回避。"""
    genres: list = []
    artists: list = []
    try:
        today_date = date.fromisoformat(today)
    except ValueError:
        return {"genres": genres, "artists": artists}
    for day in sorted(history, reverse=True):
        try:
            age = (today_date - date.fromisoformat(day)).days
        except ValueError:
            continue
        if age > 3:
            continue
        entry = history[day]
        for genre in (entry.get("genres", []) or [])[:4]:
            if genre not in genres:
                genres.append(genre)
        for artist in (entry.get("artists", []) or [])[:4]:
            if artist not in artists:
                artists.append(artist)
    return {"genres": genres[:8], "artists": artists[:8]}


def _audio_names(audios: list) -> list:
    return [
        f"{getattr(a, 'name', '')} - {getattr(a, 'album', '')} - {getattr(a, 'album_artist', '')}"
        for a in audios
    ]


def _build_result(
    audios: list, reason: str, source: str, today: str, renewals: int, segments: list | None = None
) -> DailyRecommendation:
    return DailyRecommendation(
        track_ids=[str(getattr(a, "id", "")) for a in audios],
        track_names=_audio_names(audios),
        reason=reason,
        source=source,
        date=today,
        renewals=renewals,
        tracks=list(audios),
        segments=segments or [],
    )


# ---- 编排入口 ----
def generate(cfg, emby, renew: bool = False) -> DailyRecommendation:
    """生成（或恢复）今天的推荐。同步函数，由调用方用 asyncio.to_thread 包裹。"""
    cache = _load_cache(cfg)
    today = _today(cfg)
    renewals = int(cache.get("renewals", 0) or 0)
    cached_ids = cache.get("track_ids") or []

    def restore() -> DailyRecommendation | None:
        if not cached_ids or emby is None:
            return None
        tracks = emby.fetch_tracks_by_ids(cached_ids)
        if not tracks:
            return None
        return _build_result(
            tracks,
            cache.get("reason") or "",
            cache.get("source") or "local",
            today,
            renewals,
            cache.get("segments") or [],
        )

    # 当天冻结 / 换一批配额用尽 → 直接恢复缓存
    if cached_ids and cache.get("date") == today:
        if not renew or renewals >= MAX_RENEWALS:
            restored = restore()
            if restored:
                return restored

    count = max(int(getattr(cfg, "daily_recommend_count", 20) or 20), 1)
    history = _load_history(cfg)
    penalties = _recommend_penalty(history, today)
    exposure = _exposure_counts(history, today)
    recent_recommended = _recently_recommended(history, today)
    snoozed = _active_snooze(cfg, today)

    fetch_candidates = getattr(emby, "fetch_recommend_candidates", None)
    # 不传 limit → 由 Emby 侧按库总量自适应决定候选池大小
    candidates = fetch_candidates() if fetch_candidates else []
    candidates = [a for a in candidates if str(getattr(a, "id", "")) not in snoozed]

    fetch_recent = getattr(emby, "fetch_recently_played", None) if emby else None
    recently_played = fetch_recent(TASTE_MAX_RECORDS) if fetch_recent else []

    fetch_total = getattr(emby, "fetch_total_tracks", None) if emby else None
    library_total = fetch_total() if fetch_total else None

    explore_ratio = float(getattr(cfg, "explore_ratio", EXPLORE_RATIO) or EXPLORE_RATIO)

    def fallback(reason: str = "本地每日混歌（未配置 AI 或生成失败）") -> DailyRecommendation:
        audios = _pick_local(candidates, count)
        tracks = emby.fetch_tracks_by_ids([a.id for a in audios]) if emby else []
        if not tracks:
            tracks = audios
        result = _build_result(tracks, reason, "local", today, renewals)
        _save_cache(cfg, result)
        _save_history(cfg, result)
        return result

    if (
        not getattr(cfg, "enable_daily_recommend", False)
        or not getattr(cfg, "llm_endpoint", "")
        or not getattr(cfg, "llm_api_key", "")
        or not getattr(cfg, "llm_model", "")
    ):
        return fallback()

    picked = []
    segment_metas: list = []
    try:
        catalog = build_catalog(
            candidates,
            recently_played=recently_played,
            recently_recommended=recent_recommended,
            today=today,
            library_total=library_total,
        )
        recipe = validate_recipe(ask_llm(catalog, cfg), catalog)
        if recipe.segments:
            picked, segment_metas = pick_tracks_segmented(
                recipe, candidates, catalog, count,
                penalties=penalties, exposure=exposure,
                explore_ratio=explore_ratio, today=today,
            )
        else:
            picked = pick_tracks(
                recipe, candidates, catalog, count,
                penalties=penalties, exposure=exposure,
                explore_ratio=explore_ratio, today=today,
            )
    except (LLMError, RecipeInvalid) as exc:
        log.warning(f"今日推荐 LLM 生成失败，回退本地混歌: {exc}")

    if not picked:
        return fallback()

    tracks = emby.fetch_tracks_by_ids([a.id for a in picked]) if emby else []
    if not tracks:
        tracks = picked
    # 「换一批」成功生成才消耗配额，失败/兜底沿用旧值
    next_renewals = renewals + 1 if renew else renewals
    result = _build_result(tracks, recipe.reason, "llm", today, next_renewals, segment_metas)
    _save_cache(cfg, result)
    _save_history(cfg, result)
    return result
