"""把清洗后的 PR 播放事件映射为可打分的行为画像（纯函数，无网络 / DB / 副作用）。

输入只有两类：19 清洗后的 ``PREvent`` 事件，与 20 的镜像快照（条目元数据 +
按 UserId 分桶的用户态）。两者都由调用方注入；本模块不连 Emby、不查插件、
不改播放链路。统计口径见 spec 与 04 / 03 / 15 号决策工单。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from math import log2
from typing import Any

from .pr_events import PREvent
from .recommend_common import (
    MIN_TASTE_SAMPLES,
)
from .recommend_common import (
    as_day as _as_date,
)
from .recommend_common import (
    exposure_is_recent as _exposure_is_recent,
)
from .recommend_common import (
    library_by_id as library_by_id,
)

WINDOWS: tuple[int, ...] = (7, 30, 180)
USER_ID_DEFAULT = "default"


@dataclass(frozen=True)
class ProfileConfig:
    """画像阈值；全部可注入，默认值是方案拍板起点值。"""

    short_seconds: float = 60.0
    valid_completion: float = 0.8
    weak_completion: float = 0.5
    min_window_samples: int = 5
    min_taste_samples: int = MIN_TASTE_SAMPLES
    min_repeat_count: int = 2
    negative_half_life_days: float = 7.0
    positive_half_life_days: float = 14.0
    immersion_half_life_days: float = 7.0
    revisit_min_valid_plays: int = 2
    revisit_min_gap_days: int = 30
    revisit_max_per_day: int = 2
    immersion_window_days: int = 7
    immersion_min_plays: int = 5
    immersion_min_share: float = 0.5
    immersion_prior_days: int = 180
    immersion_prior_max: int = 1
    immersion_upgrade_bursts: int = 2
    immersion_upgrade_gap_days: int = 30
    immersion_min_weight: float = 0.05
    familiar_play_count: int = 20
    familiar_favorite_count: int = 2
    familiar_recent_plays: int = 3
    familiar_recent_days: int = 30
    familiar_per_artist_cap: int = 2
    llm_window_days: int = 7


@dataclass(frozen=True)
class Revisit:
    """回访曲目：隔 ≥30 天又主动再听的曲目。"""

    item_id: str
    valid_plays: int
    max_gap_days: int
    days_since: int
    score: float


@dataclass(frozen=True)
class Immersion:
    """偶发沉浸：短期集中的艺人项，可衰减归零。"""

    artist: str
    burst_plays: int
    share: float
    weight: float
    days_since_burst: int


@dataclass(frozen=True)
class BehaviorProfile:
    """画像输出：既有骨架 + 独立信号 + 供 LLM 的全局行为摘要。"""

    taste_vector: dict[str, float] = field(default_factory=dict)
    familiar_of: dict[str, float] = field(default_factory=dict)
    base_score: dict[str, float] = field(default_factory=dict)
    penalties: dict[str, dict[str, float]] = field(
        default_factory=lambda: {"track": {}, "artist": {}, "genre": {}}
    )
    window_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    confidence: float = 0.0
    taste_samples: int = 0
    revisits: tuple[Revisit, ...] = ()
    revisit_daily: tuple[str, ...] = ()
    immersions: tuple[Immersion, ...] = ()
    long_term_artists: tuple[str, ...] = ()
    familiar_artists: tuple[str, ...] = ()
    unheard_by_artist: dict[str, tuple[str, ...]] = field(default_factory=dict)
    play_events: dict[str, int] = field(default_factory=dict)
    daypart_note: str = ""


def _resolve_today(events: Iterable[Any], today: Any) -> date:
    parsed = _as_date(today)
    if parsed is not None:
        return parsed
    days = [_as_date(getattr(event, "started_at", None)) for event in events]
    known = [day for day in days if day is not None]
    return max(known) if known else date.today()


def _user_state(item: Mapping[str, Any] | None, user_id: str) -> dict[str, Any]:
    if not item:
        return {}
    users = item.get("users")
    if isinstance(users, Mapping):
        state = users.get(user_id)
        if isinstance(state, Mapping):
            return dict(state)
    return {}


def _classify(event: Any, config: ProfileConfig) -> tuple[float | None, bool, bool]:
    """返回 (completion, is_short, is_incomplete)；收藏不豁免信号分类，只豁免降权。"""
    duration = float(getattr(event, "duration_seconds", 0) or 0)
    short = bool(getattr(event, "is_extremely_short", False)) or duration < config.short_seconds
    raw_completion = getattr(event, "completion_rate", None)
    completion = float(raw_completion) if raw_completion is not None else None
    incomplete = bool(getattr(event, "is_incomplete", False))
    if completion is not None and completion < config.valid_completion:
        incomplete = True
    return completion, short, incomplete


def _decay(age_days: int, half_life: float) -> float:
    return 0.5 ** (max(age_days, 0) / half_life) if half_life > 0 else 0.0


def _clusters(days: list[date], window_days: int) -> list[list[date]]:
    """按播放次数（含同日重播）切爆发簇：重复日期代表重复播放，不去重。"""
    result: list[list[date]] = []
    for day in sorted(days):
        if not result or (day - result[-1][0]).days >= window_days:
            result.append([day])
        else:
            result[-1].append(day)
    return result


def _log_capped(count: int) -> float:
    """次数 → 权重：门控后对数封顶 min(log2(1+n), 3) / 3。"""
    return min(log2(1 + max(count, 0)), 3.0) / 3.0


def build_profile(
    events: Iterable[PREvent],
    *,
    library: Mapping[str, Mapping[str, Any]] | None = None,
    user_id: str = USER_ID_DEFAULT,
    today: Any = None,
    config: ProfileConfig | None = None,
    exposure: Mapping[str, Any] | None = None,
    blocked_ids: Iterable[str] = (),
) -> BehaviorProfile:
    """从清洗事件与镜像用户态构建画像；所有输入只读，输出为全新结构。"""
    cfg = config or ProfileConfig()
    items = library or {}
    event_list = list(events)
    today_date = _resolve_today(event_list, today)
    blocked = {str(value) for value in blocked_ids}

    prepared: list[tuple[str, date, dict[str, Any], float | None, bool, bool, bool]] = []
    for event in event_list:
        item_id = str(getattr(event, "item_id", ""))
        if getattr(event, "user_id", user_id) != user_id:
            continue
        day = _as_date(getattr(event, "started_at", None))
        if day is None:
            continue
        item = items.get(item_id)
        state = _user_state(item, user_id)
        favorite = bool(state.get("favorite", False))
        completion, short, incomplete = _classify(event, cfg)
        prepared.append((item_id, day, state, completion, short, incomplete, favorite))

    window_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"d7": 0, "d30": 0, "d180": 0, "all": 0}
    )
    penalties: dict[str, dict[str, float]] = {"track": {}, "artist": {}, "genre": {}}
    valid_dates: dict[str, list[date]] = defaultdict(list)
    valid_sample_count = 0

    for item_id, day, _state, completion, short, incomplete, favorite in prepared:
        age = (today_date - day).days
        if not short and not incomplete:
            valid_sample_count += 1
            valid_dates[item_id].append(day)
            counts = window_counts[item_id]
            counts["all"] += 1
            for size in WINDOWS:
                if age < size:
                    counts[f"d{size}"] += 1
        item = items.get(item_id) or {}
        keys = {
            "track": (item_id,),
            "artist": tuple(str(a) for a in (item.get("artist") or [])),
            "genre": tuple(str(g) for g in (item.get("genres") or [])),
        }
        if favorite:
            for table, names in keys.items():
                for name in names:
                    penalties[table].setdefault(name, 0.0)
            continue
        weight = 0.0
        if short:
            weight = 1.0
        elif incomplete:
            value = min(max(completion or 0.0, 0.0), 1.0)
            if completion is not None and completion >= cfg.weak_completion:
                # 完成率落在弱完成率与计次门槛之间：按完成度弱软降权。
                weight = value * 0.5
            else:
                # 低于弱完成率：强软降权，不受完成度打折。
                weight = 1.0
        if weight <= 0:
            continue
        decayed = weight * _decay(age, cfg.negative_half_life_days)
        for table, names in keys.items():
            for name in names:
                penalties[table][name] = max(penalties[table].get(name, 0.0), decayed)

    confidence = min(1.0, valid_sample_count / cfg.min_taste_samples) if cfg.min_taste_samples else 0.0

    def cumulative(item_id: str) -> int:
        state = _user_state(items.get(item_id), user_id)
        mirror_count = int(state.get("play_count", 0) or 0)
        return max(mirror_count, len(valid_dates.get(item_id, ())))

    def repeat_boost(item_id: str) -> float:
        count = window_counts.get(item_id, {}).get("d30", 0)
        if count < cfg.min_repeat_count or valid_sample_count < cfg.min_window_samples:
            return 0.0
        return min(count, 3) / 3

    # 口味向量：累计播放次数作长期底分，窗口播放次数作近期正反馈，收藏固定强正反馈。
    genre_scores: defaultdict[str, float] = defaultdict(float)
    for item_id, item in items.items():
        genres = [str(g) for g in (item.get("genres") or [])]
        if not genres:
            continue
        count = cumulative(item_id)
        if count:
            for genre in genres:
                genre_scores[genre] += _log_capped(count) / len(genres)
        if _user_state(item, user_id).get("favorite"):
            for genre in genres:
                genre_scores[genre] += 3.0 / len(genres)
    for item_id, day, _state, _completion, _short, _incomplete, _favorite in prepared:
        genres = [str(g) for g in ((items.get(item_id) or {}).get("genres") or [])]
        if not genres:
            continue
        age = (today_date - day).days
        if age >= cfg.familiar_recent_days:
            continue
        strength = (
            5.0
            * _decay(age, cfg.positive_half_life_days)
            * confidence
            * (1.0 + repeat_boost(item_id))
        )
        for genre in genres:
            genre_scores[genre] += strength / len(genres)
    ranked = sorted(genre_scores.items(), key=lambda entry: -entry[1])
    total = sum(value for _, value in ranked) or 1.0
    taste_vector = {name: round(value / total, 4) for name, value in ranked[:6]}

    familiar_of: dict[str, float] = {}
    base_score: dict[str, float] = {}
    for item_id in set(items) | set(window_counts):
        state = _user_state(items.get(item_id), user_id)
        is_favorite = bool(state.get("favorite", False))
        familiar = (
            1.0
            if is_favorite
            else (
                0.6
                if window_counts.get(item_id, {}).get("d30", 0) >= cfg.min_repeat_count
                else 0.0
            )
        )
        familiar_of[item_id] = familiar
        base_score[item_id] = round(
            1.0
            + _log_capped(cumulative(item_id))
            + repeat_boost(item_id) * confidence
            + (3.0 if is_favorite else 0.0)
            - penalties["track"].get(item_id, 0.0),
            4,
        )

    # 回访曲目：≥2 次有效播放 + 历史间隔 ≥30 天 + 距上次 ≥30 天（排除窗到期）。
    revisits: list[Revisit] = []
    if valid_sample_count >= cfg.min_window_samples:
        for item_id, dates in valid_dates.items():
            if len(dates) < cfg.revisit_min_valid_plays:
                continue
            ordered = sorted(dates)
            max_gap = max(
                (later - earlier).days
                for earlier, later in zip(ordered, ordered[1:], strict=False)
            )
            days_since = (today_date - ordered[-1]).days
            if max_gap < cfg.revisit_min_gap_days or days_since < cfg.revisit_min_gap_days:
                continue
            if cumulative(item_id) < cfg.revisit_min_valid_plays:
                continue
            scores = {
                str(g) for g in ((items.get(item_id) or {}).get("genres") or [])
            }
            positive = max(taste_vector.get(genre, 0.0) for genre in scores) if scores else 0.0
            revisits.append(
                Revisit(
                    item_id=item_id,
                    valid_plays=len(dates),
                    max_gap_days=max_gap,
                    days_since=days_since,
                    score=round(
                        _log_capped(cumulative(item_id))
                        * (max_gap / cfg.revisit_min_gap_days)
                        * (1.0 + positive),
                        4,
                    ),
                )
            )
    revisits.sort(key=lambda item: (-item.score, item.item_id))
    revisit_daily = tuple(
        item.item_id
        for item in revisits[: cfg.revisit_max_per_day]
        if item.item_id not in blocked
    )

    # 偶发沉浸：7 天 ≥5 次有效播放且占比 ≥50%，此前 180 天 ≤1 次；独立短期项。
    artist_days: defaultdict[str, list[date]] = defaultdict(list)
    for item_id, dates in valid_dates.items():
        for artist in (items.get(item_id) or {}).get("artist") or []:
            artist_days[str(artist)].extend(dates)

    immersions: list[Immersion] = []
    long_term: list[str] = []
    if valid_sample_count >= cfg.min_taste_samples:
        all_play_days = [day for _item_id, day, *_rest in prepared]
        recent_total = sum(
            1 for day in all_play_days if (today_date - day).days < cfg.immersion_window_days
        )

        def cluster_share(cluster: list[date]) -> float:
            """簇占比按窗口内全部播放次数算（含同日重播），长期升级同样要达标。"""
            start = cluster[-1] - timedelta(days=max(cfg.immersion_window_days - 1, 0))
            total = sum(1 for day in all_play_days if start <= day <= cluster[-1])
            return len(cluster) / total if total else 0.0

        for artist, days in artist_days.items():
            clusters = _clusters(days, cfg.immersion_window_days)
            qualified = [
                cluster
                for cluster in clusters
                if len(cluster) >= cfg.immersion_min_plays
                and cluster_share(cluster) >= cfg.immersion_min_share
            ]
            if not qualified:
                continue
            first = qualified[0]
            prior = sum(
                1
                for day in days
                if 0 < (first[0] - day).days <= cfg.immersion_prior_days
            )
            if prior > cfg.immersion_prior_max:
                continue
            burst = max(qualified, key=lambda cluster: (cluster[-1], len(cluster)))
            burst_plays = sum(1 for day in days if burst[0] <= day <= burst[-1])
            share = burst_plays / recent_total if recent_total else 0.0
            days_since = (today_date - burst[-1]).days
            weight = (
                _log_capped(burst_plays)
                * confidence
                * _decay(days_since, cfg.immersion_half_life_days)
                if share >= cfg.immersion_min_share
                else 0.0
            )
            if weight >= cfg.immersion_min_weight:
                immersions.append(
                    Immersion(
                        artist=artist,
                        burst_plays=burst_plays,
                        share=round(share, 4),
                        weight=round(weight, 4),
                        days_since_burst=days_since,
                    )
                )
            starts = sorted(cluster[0] for cluster in qualified)
            bursts = 1
            for index in range(1, len(starts)):
                if (starts[index] - starts[index - 1]).days >= cfg.immersion_upgrade_gap_days:
                    bursts += 1
            if bursts >= cfg.immersion_upgrade_bursts:
                long_term.append(artist)
    immersions = [imm for imm in immersions if imm.artist not in long_term]
    immersions.sort(key=lambda item: (-item.weight, item.artist))
    active_immersion = {imm.artist for imm in immersions}

    # 熟悉歌手（三条取或）+ 未听曲目（每歌手 ≤2 首）；沉浸未归零者排除。
    familiar_artists: list[str] = []
    unheard: dict[str, tuple[str, ...]] = {}
    if valid_sample_count >= cfg.min_taste_samples:
        artist_items: defaultdict[str, list[str]] = defaultdict(list)
        for item_id, item in items.items():
            for artist in item.get("artist") or []:
                artist_items[str(artist)].append(item_id)
        for artist, member_ids in artist_items.items():
            if artist in active_immersion:
                continue
            total_plays = sum(cumulative(item_id) for item_id in member_ids)
            favorites = sum(
                1
                for item_id in member_ids
                if _user_state(items.get(item_id), user_id).get("favorite")
            )
            recent_plays = sum(
                1
                for item_id in member_ids
                for day in valid_dates.get(item_id, ())
                if (today_date - day).days < cfg.familiar_recent_days
            )
            if not (
                total_plays >= cfg.familiar_play_count
                or favorites >= cfg.familiar_favorite_count
                or recent_plays >= cfg.familiar_recent_plays
            ):
                continue
            familiar_artists.append(artist)
            candidates: list[str] = []
            for item_id in sorted(member_ids):
                item = items[item_id]
                state = _user_state(item, user_id)
                if state.get("favorite"):
                    continue
                if valid_dates.get(item_id):
                    continue
                if state.get("last_played_date"):
                    continue
                if exposure is not None and _exposure_is_recent(
                    exposure.get(item_id), today_date, cfg.revisit_min_gap_days
                ):
                    continue
                candidates.append(item_id)
            unheard[artist] = tuple(candidates[: cfg.familiar_per_artist_cap])
    familiar_artists.sort()

    # 供 LLM 的行为信号：只保留全局计数与 ≤80 字时段说明，不按单曲 / 客户端拆分。
    play_events: dict[str, int] = {
        "completed": 0,
        "incomplete": 0,
        "short": 0,
        "distinct_tracks": 0,
        "window_days": cfg.llm_window_days,
    }
    daypart_counts: defaultdict[int, int] = defaultdict(int)
    daypart_genres: defaultdict[str, int] = defaultdict(int)
    window_ids: set[str] = set()
    for event in event_list:
        if getattr(event, "user_id", user_id) != user_id:
            continue
        started = getattr(event, "started_at", None)
        day = _as_date(started)
        if day is None or (today_date - day).days >= cfg.llm_window_days:
            continue
        item_id = str(getattr(event, "item_id", ""))
        window_ids.add(item_id)
        completion, short, incomplete = _classify(event, cfg)
        if short:
            play_events["short"] += 1
        elif incomplete:
            play_events["incomplete"] += 1
        else:
            play_events["completed"] += 1
        if short or incomplete:
            continue
        hour = started.hour if isinstance(started, datetime) else 0
        daypart_counts[hour // 6] += 1
        for genre in ((items.get(item_id) or {}).get("genres") or []):
            daypart_genres[str(genre)] += 1
    play_events["distinct_tracks"] = len(window_ids)

    daypart_note = ""
    if daypart_counts:
        bucket = max(daypart_counts, key=lambda key: (daypart_counts[key], -key))
        share = daypart_counts[bucket] / sum(daypart_counts.values()) * 100
        note = (
            f"近{cfg.llm_window_days}天 "
            f"{bucket * 6}-{(bucket + 1) * 6}点占{share:.0f}%"
        )
        if daypart_genres:
            top_genre = max(daypart_genres, key=lambda genre: (daypart_genres[genre], genre))
            note += f"，以{top_genre}居多"
        daypart_note = note[:80]

    return BehaviorProfile(
        taste_vector=taste_vector,
        familiar_of=familiar_of,
        base_score=base_score,
        penalties=penalties,
        window_counts=dict(window_counts),
        confidence=confidence,
        taste_samples=valid_sample_count,
        revisits=tuple(revisits),
        revisit_daily=revisit_daily,
        immersions=tuple(immersions),
        long_term_artists=tuple(sorted(long_term)),
        familiar_artists=tuple(familiar_artists),
        unheard_by_artist=unheard,
        play_events=play_events,
        daypart_note=daypart_note,
    )
