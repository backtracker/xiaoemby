"""候选池与种子相似召回（纯函数，无网络 / DB / 副作用）。

输入只有调用方注入的镜像快照（LibraryMirror.snapshot() 形状）、行为画像
（behavior_profile.BehaviorProfile）与推荐历史；本模块不连 Emby、不查插件、
不改播放地址（见 ADR-0001 / ADR-0002）。候选来源为本地全库镜像，不受远程
800~3000 采样上限约束。术语以 GLOSSARY.md 为准。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .behavior_profile import BehaviorProfile
from .recommend_common import (
    active_items,
)
from .recommend_common import (
    as_day as _as_date,
)
from .recommend_common import (
    exposure_is_recent as _recently_exposed,
)

# 语言以流派标签近似：Mandopop / Cantopop → 华语，J-Pop / Anime → 日语，
# K-Pop → 韩语，纯音乐 / Instrumental → 纯音乐，其余 → 英语（Emby 无语言字段）。
_LANGUAGE_TAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("华语", ("mandopop", "cantopop", "华语", "国语", "粤语", "c-pop", "cpop")),
    ("日语", ("j-pop", "jpop", "anime", "日语", "日文", "japanese")),
    ("韩语", ("k-pop", "kpop", "韩语", "韩文", "korean")),
    ("纯音乐", ("纯音乐", "轻音乐", "instrumental", "classical", "古典", "ost", "soundtrack")),
)
_LANGUAGE_PRIORITY = ("华语", "日语", "韩语", "纯音乐", "英语")
_ENGLISH = "英语"


@dataclass(frozen=True)
class SimilarityWeights:
    """统一相似度权重：种子召回与 MMR 共用一把尺子。"""

    genre: float = 0.5
    language: float = 0.2
    artist: float = 0.15
    album: float = 0.1
    era: float = 0.05


def languages_of(item: Mapping[str, Any]) -> frozenset[str]:
    """把流派标签近似为语言集合；曲库无语言元数据，粒度只能到粗分类。"""
    genres = [str(tag).lower() for tag in (item.get("genres") or [])]
    found = {
        language
        for language, tags in _LANGUAGE_TAGS
        if any(tag in genre for genre in genres for tag in tags)
    }
    return frozenset(found or {_ENGLISH})


def language_of(item: Mapping[str, Any]) -> str:
    """主语言（供前五语言上限约束）；多标签时按固定优先级取一个。"""
    languages = languages_of(item)
    return next(name for name in _LANGUAGE_PRIORITY if name in languages)


def decade_of(item: Mapping[str, Any]) -> int:
    """年代：发行年落在十年区间；无发行年返回 0（不贡献相似度）。"""
    year = item.get("year") or 0
    try:
        value = int(year)
    except (TypeError, ValueError):
        return 0
    return (value // 10) * 10 if value > 0 else 0


def similarity(
    a: Mapping[str, Any],
    b: Mapping[str, Any],
    *,
    weights: SimilarityWeights | None = None,
) -> float:
    """统一相似度 = 流派 Jaccard + 语言 + 艺人 + 专辑 + 年代。"""
    w = weights or SimilarityWeights()
    genres_a = {str(tag) for tag in (a.get("genres") or [])}
    genres_b = {str(tag) for tag in (b.get("genres") or [])}
    union = genres_a | genres_b
    genre = len(genres_a & genres_b) / len(union) if union else 0.0
    lang_a, lang_b = languages_of(a), languages_of(b)
    language = (
        len(lang_a & lang_b) / len(lang_a | lang_b) if lang_a | lang_b else 0.0
    )
    artists_a = {str(name) for name in (a.get("artist") or [])}
    artists_b = {str(name) for name in (b.get("artist") or [])}
    artist = 1.0 if artists_a & artists_b else 0.0
    album_a = str(a.get("album") or "")
    album_b = str(b.get("album") or "")
    album = 1.0 if album_a and album_a == album_b else 0.0
    decade_a, decade_b = decade_of(a), decade_of(b)
    era = 1.0 if decade_a and decade_a == decade_b else 0.0
    return (
        w.genre * genre
        + w.language * language
        + w.artist * artist
        + w.album * album
        + w.era * era
    )


@dataclass(frozen=True)
class CandidateConfig:
    """候选池阈值；全部可注入，默认值为方案拍板起点值。"""

    weights: SimilarityWeights = field(default_factory=SimilarityWeights)
    seed_recall_enabled: bool = False  # A/B 未裁决前默认关闭
    max_seeds: int = 20
    min_seed_score: float = 2.0
    seed_min_similarity: float = 0.3
    seed_max_penalty: float = 0.5
    seed_require_unheard: bool = True
    familiar_per_artist_cap: int = 2
    explore_ratio: float = 0.15
    playlist_size: int = 20
    exclusion_days: int = 30
    new_release_explore_count: int = 5


@dataclass(frozen=True)
class CandidatePool:
    """候选池输出：主候选 + 探索池 + 种子召回诊断，按曲目 Id 去重。"""

    items: tuple[dict[str, Any], ...]
    sources: dict[str, tuple[str, ...]]
    explore: tuple[str, ...]
    seed_scores: dict[str, float]
    seeds: tuple[str, ...]
    total_active: int


def new_releases(
    items: Mapping[str, Mapping[str, Any]], limit: int | None = None
) -> list[str]:
    """新发行：按发行年 + 创建时间倒序；limit=None 表示不设采样上限。"""
    dated = [
        key
        for key, row in items.items()
        if (row.get("year") or 0) or str(row.get("date_created") or "")
    ]
    ordered = sorted(
        dated,
        key=lambda key: (
            items[key].get("year") or 0,
            str(items[key].get("date_created") or ""),
        ),
        reverse=True,
    )
    return ordered[:limit] if limit is not None else ordered


def items_for_genres(
    items: Mapping[str, Mapping[str, Any]], genres: Iterable[str]
) -> list[str]:
    """流派召回：命中任一口味流派的曲目 id（全库扫描，无远程采样上限）。"""
    wanted = {str(genre) for genre in genres}
    return [
        key
        for key, row in items.items()
        if wanted.intersection(str(tag) for tag in (row.get("genres") or []))
    ]


def _seed_strength(profile: BehaviorProfile, item_id: str, is_favorite: bool) -> float:
    """种子强度：base_score 归一到 0~1；非收藏按 confidence 收敛。"""
    score = max(profile.base_score.get(item_id, 0.0), 0.0)
    base = min(score / 5.0, 1.0)
    return base if is_favorite else base * profile.confidence


def select_strong_seeds(
    items: Mapping[str, Mapping[str, Any]],
    profile: BehaviorProfile,
    *,
    config: CandidateConfig | None = None,
    blocked_ids: Iterable[str] = (),
    favorite_ids: Iterable[str] = (),
) -> tuple[str, ...]:
    """强正反馈长期种子：收藏或完成播放且分数靠前，≤20 首，排除沉浸 / 硬屏蔽 / 强降权。"""
    cfg = config or CandidateConfig()
    blocked = {str(value) for value in blocked_ids}
    favorites = {str(value) for value in favorite_ids}
    favorites.update(key for key, value in profile.familiar_of.items() if value >= 1.0)
    immersed = {imm.artist for imm in profile.immersions}

    ranked: list[tuple[float, str]] = []
    for item_id, score in profile.base_score.items():
        if item_id in blocked or score < cfg.min_seed_score:
            continue
        if profile.penalties.get("track", {}).get(item_id, 0.0) >= cfg.seed_max_penalty:
            continue
        row = items.get(item_id) or {}
        if immersed.intersection(str(name) for name in (row.get("artist") or [])):
            continue
        is_favorite = item_id in favorites
        if not is_favorite and not profile.window_counts.get(item_id, {}).get("all", 0):
            continue
        ranked.append((score, item_id))
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    return tuple(item_id for _, item_id in ranked[: cfg.max_seeds])


def build_candidate_pool(
    snapshot: Iterable[Mapping[str, Any]],
    profile: BehaviorProfile | None = None,
    *,
    config: CandidateConfig | None = None,
    exposure: Mapping[str, Any] | None = None,
    blocked_ids: Iterable[str] = (),
    cold_cluster_ids: Iterable[str] = (),
    favorite_ids: Iterable[str] = (),
    today: Any = None,
    explore_k: int | None = None,
) -> CandidatePool:
    """从注入的镜像快照与画像构建候选池；历史只做去重复，不写回任何输入。"""
    cfg = config or CandidateConfig()
    prof = profile or BehaviorProfile()
    all_items = active_items(snapshot)
    blocked = {str(value) for value in blocked_ids}
    favorites = {str(value) for value in favorite_ids}
    favorites.update(
        key for key, value in prof.familiar_of.items() if value >= 1.0
    )
    today_date = _as_date(today)
    exposure = exposure or {}

    visible = {
        key: row
        for key, row in all_items.items()
        if key not in blocked
        and not _recently_exposed(
            exposure.get(key), today_date, cfg.exclusion_days
        )
    }

    sources: dict[str, tuple[str, ...]] = dict.fromkeys(visible, ())
    for key in items_for_genres(visible, prof.taste_vector):
        sources[key] = (*sources[key], "genre")
    for key in new_releases(visible, cfg.new_release_explore_count):
        sources[key] = (*sources[key], "new")

    seeds: tuple[str, ...] = ()
    seed_scores: dict[str, float] = {}
    if cfg.seed_recall_enabled:
        seeds = select_strong_seeds(
            all_items,
            prof,
            config=cfg,
            blocked_ids=blocked,
            favorite_ids=favorites,
        )
        seed_rows = [(key, all_items[key]) for key in seeds if key in all_items]
        for key, row in visible.items():
            if key in seeds or key in favorites:
                continue
            heard = (
                cfg.seed_require_unheard
                and (
                    prof.window_counts.get(key, {}).get("all", 0) > 0
                    or prof.familiar_of.get(key, 0.0) > 0.0
                )
            )
            if heard:
                continue
            artists = {str(name) for name in (row.get("artist") or [])}
            best = 0.0
            for seed_id, seed_row in seed_rows:
                seed_artists = {
                    str(name) for name in (seed_row.get("artist") or [])
                }
                if artists & seed_artists:
                    continue  # 同艺人未听归探索池，本通道只管跨艺人
                strength = _seed_strength(
                    prof, seed_id, seed_id in favorites
                )
                best = max(
                    best,
                    strength * similarity(seed_row, row, weights=cfg.weights),
                )
            if best >= cfg.seed_min_similarity:
                seed_scores[key] = round(best, 4)
                sources[key] = (*sources[key], "seed")

    # 探索池：熟悉歌手未听 + v2 冷门簇 + 新发行，合并到同一探索配额并按 Id 去重。
    explore_scores: dict[str, float] = {}
    for artist in prof.familiar_artists or prof.unheard_by_artist:
        used = 0
        for key in prof.unheard_by_artist.get(artist, ()):
            if used >= cfg.familiar_per_artist_cap:
                break
            key = str(key)
            if key not in visible or key in favorites:
                continue
            explore_scores[key] = prof.base_score.get(key, 0.0)
            used += 1
    for key in cold_cluster_ids:
        key = str(key)
        if key in visible and key not in explore_scores:
            explore_scores[key] = prof.base_score.get(key, 0.0)
    for key in new_releases(visible, cfg.new_release_explore_count):
        if key not in explore_scores:
            explore_scores[key] = prof.base_score.get(key, 0.0)
    ranked_explore = sorted(explore_scores, key=lambda key: (-explore_scores[key], key))
    limit = (
        explore_k
        if explore_k is not None
        else round(cfg.explore_ratio * cfg.playlist_size)
    )

    return CandidatePool(
        items=tuple(visible[key] for key in visible),
        sources=sources,
        explore=tuple(ranked_explore[: max(limit, 0)]),
        seed_scores=seed_scores,
        seeds=seeds,
        total_active=len(all_items),
    )
