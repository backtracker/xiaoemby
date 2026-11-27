"""行为画像与信号映射：只通过注入的事件与镜像快照断言外部可观察的画像行为。"""

from datetime import datetime, timedelta

import pytest

from xiaomusic.utils.behavior_profile import ProfileConfig, build_profile, library_by_id
from xiaomusic.utils.pr_events import LOCAL_TZ, PREvent

TODAY = "2026-11-27"
USER = "nick"


def make_event(
    item_id,
    days_ago,
    *,
    hour=10,
    duration=120.0,
    completion=1.0,
    short=False,
    incomplete=False,
    valid=None,
    user=USER,
    device="小爱音箱",
):
    if valid is None:
        valid = not short and not incomplete
    started = datetime(2026, 11, 27, hour, tzinfo=LOCAL_TZ) - timedelta(days=days_ago)
    return PREvent(
        user_id=user,
        item_id=item_id,
        device_name=device,
        started_at=started,
        duration_seconds=duration,
        completion_rate=completion if not short else None,
        is_valid=valid,
        is_extremely_short=short,
        is_incomplete=incomplete,
        segment_count=1,
    )


def make_item(item_id, genres, artist=(), *, favorite=False, play_count=0, last_played=None):
    return {
        "id": item_id,
        "genres": list(genres),
        "artist": list(artist),
        "users": {
            USER: {
                "favorite": favorite,
                "play_count": play_count,
                "last_played_date": last_played,
            }
        },
    }


def build(events, items=(), **kwargs):
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("user_id", USER)
    return build_profile(events, library=library_by_id(items), **kwargs)


def test_window_counts_count_only_completed_plays_in_four_windows():
    events = [make_event("t1", days) for days in (0, 5, 10, 40, 200)]
    profile = build(events, [make_item("t1", ["民谣"])])
    counts = profile.window_counts["t1"]
    assert counts["d7"] == 2
    assert counts["d30"] == 3
    assert counts["d180"] == 4
    assert counts["all"] == 5


def test_short_and_incomplete_are_not_counted_and_produce_penalties():
    events = [
        make_event("short", 0, short=True, duration=12, completion=None),
        make_event("weak", 0, incomplete=True, duration=100, completion=0.6),
    ]
    items = [
        make_item("short", ["流行"]),
        make_item("weak", ["流行"]),
    ]
    profile = build(events, items)
    assert "short" not in profile.window_counts
    assert "weak" not in profile.window_counts
    assert profile.penalties["track"]["short"] > profile.penalties["track"]["weak"] > 0
    assert profile.penalties["genre"]["流行"] > 0


def test_penalty_decays_by_real_date_half_life_and_favorite_is_exempt():
    events = [
        make_event("fresh", 0, short=True, duration=12, completion=None),
        make_event("stale", 7, short=True, duration=12, completion=None),
        make_event("fav", 0, short=True, duration=12, completion=None),
    ]
    items = [
        make_item("fresh", ["流行"]),
        make_item("stale", ["流行"]),
        make_item("fav", ["流行"], favorite=True),
    ]
    profile = build(events, items)
    fresh = profile.penalties["track"]["fresh"]
    stale = profile.penalties["track"]["stale"]
    assert fresh == pytest.approx(stale * 2, rel=0.01)
    assert profile.penalties["track"]["fav"] == 0.0


def test_confidence_converges_with_valid_sample_size():
    few = build([make_event(f"t{i}", 0) for i in range(5)])
    many = build([make_event(f"t{i}", 0) for i in range(20)])
    assert few.confidence == pytest.approx(5 / 15)
    assert many.confidence == 1.0


def fillers(count, start_day, artist_prefix="其他"):
    """样本填充：不落在被测艺人的 7 天窗口里，彼此不同艺人避免误判沉浸。"""
    events = [
        make_event(f"fill{i}", start_day + i, hour=9)
        for i in range(count)
    ]
    items = [
        make_item(f"fill{i}", ["流行"], [f"{artist_prefix}{i}"])
        for i in range(count)
    ]
    return events, items


def test_revisit_requires_gap_and_exclusion_window():
    events, items = fillers(5, 0)
    events += [make_event("rev", 90), make_event("rev", 40)]
    items += [make_item("rev", ["民谣"], ["水木年华"], play_count=2)]
    profile = build(events, items)

    revisit = next(r for r in profile.revisits if r.item_id == "rev")
    assert revisit.valid_plays >= 2
    assert revisit.max_gap_days >= 30
    assert revisit.days_since >= 30
    assert "rev" in profile.revisit_daily


def test_revisit_rejects_single_recent_or_short_play():
    cases = {
        "single": [make_event("single", 90)],
        "recent": [make_event("recent", 20), make_event("recent", 0)],
        "short": [
            make_event("short", 90, short=True, duration=10),
            make_event("short", 40, short=True, duration=10),
        ],
    }
    # 每组各建一份画像，避免不同组的事件互相污染样本量。
    for key, group in cases.items():
        group_events, filler = fillers(5, 0, artist_prefix=f"填充{key}")
        profile = build(group + group_events, [make_item(key, ["民谣"], ["水木年华"], play_count=2)] + filler)
        assert all(r.item_id != key for r in profile.revisits)


def test_revisit_daily_injection_is_capped_and_respects_hard_block():
    events, items = fillers(5, 0)
    for i in range(3):
        item_id = f"rev{i}"
        events += [make_event(item_id, 90), make_event(item_id, 40)]
        items.append(make_item(item_id, ["民谣"], [f"艺人{i}"], play_count=2))
    profile = build(events, items, blocked_ids=["rev1"])
    assert len(profile.revisit_daily) <= 2
    assert "rev1" not in profile.revisit_daily
    assert all(track in {r.item_id for r in profile.revisits} for track in profile.revisit_daily)


def test_immersion_detects_burst_and_decays_to_zero_without_upgrade():
    events, items = fillers(7, 10)
    for i in range(8):
        item_id = f"lz{i}"
        events.append(make_event(item_id, i, hour=10))
        items.append(make_item(item_id, ["摇滚"], ["李志"]))

    today_profile = build(events, items)
    lz = next(imm for imm in today_profile.immersions if imm.artist == "李志")
    assert lz.burst_plays >= 5
    assert lz.share >= 0.5
    assert "李志" not in today_profile.familiar_artists

    later = (datetime(2026, 11, 27, tzinfo=LOCAL_TZ) + timedelta(days=40)).date().isoformat()
    later_profile = build(events, items, today=later)
    assert all(imm.artist != "李志" for imm in later_profile.immersions)
    assert "李志" not in later_profile.long_term_artists


def test_immersion_clusters_count_plays_not_distinct_dates():
    filler_events, filler_items = fillers(15, 10, artist_prefix="其他")
    events = list(filler_events)
    items = list(filler_items)
    for index in range(5):
        events.append(make_event(f"burst{index}", 0, hour=10))
        items.append(make_item(f"burst{index}", ["摇滚"], ["同日爆发"]))

    profile = build(events, items)
    immersion = next(imm for imm in profile.immersions if imm.artist == "同日爆发")
    assert immersion.burst_plays == 5
    assert immersion.share >= 0.5


def test_long_term_upgrade_requires_minimum_share():
    events = []
    items = []
    for offset in (0, 40):
        for index in range(6):
            item_id = f"y{offset}_{index}"
            events.append(make_event(item_id, offset + index, hour=10))
            items.append(make_item(item_id, ["民谣"], ["低占比艺人"]))
        for index in range(8):
            item_id = f"other{offset}_{index}"
            events.append(make_event(item_id, offset + index % 6, hour=9))
            items.append(make_item(item_id, ["流行"], [f"其他{offset}_{index}"]))

    profile = build(events, items)
    assert "低占比艺人" not in profile.long_term_artists
    assert all(imm.artist != "低占比艺人" for imm in profile.immersions)


def test_weak_completion_threshold_changes_downweight_strength():
    events = [
        make_event("strong", 0, incomplete=True, duration=100, completion=0.4),
        make_event("weak", 0, incomplete=True, duration=100, completion=0.7),
    ]
    items = [make_item("strong", ["流行"]), make_item("weak", ["流行"])]

    default_profile = build(events, items)
    strong = default_profile.penalties["track"]["strong"]
    weak = default_profile.penalties["track"]["weak"]
    assert strong > weak > 0

    stricter = build(events, items, config=ProfileConfig(weak_completion=0.8))
    assert stricter.penalties["track"]["weak"] > weak


def test_two_bursts_30_days_apart_upgrade_to_long_term():
    events, items = fillers(8, 20)
    for i, days in enumerate(list(range(0, 6)) + list(range(40, 46))):
        item_id = f"zys{i}"
        events.append(make_event(item_id, days, hour=10))
        items.append(make_item(item_id, ["民谣"], ["张雨生"]))

    profile = build(events, items)
    assert "张雨生" in profile.long_term_artists
    assert all(imm.artist != "张雨生" for imm in profile.immersions)


def test_familiar_artist_thresholds_and_unheard_cap():
    events, items = fillers(15, 10)
    for i in range(1, 6):
        items.append(
            make_item(
                f"fam{i}",
                ["民谣"],
                ["熟悉艺人"],
                favorite=(i <= 2),
                last_played="2026-10-01T00:00:00.0000000Z" if i == 5 else None,
            )
        )
    items.append(make_item("high", ["流行"], ["高次数艺人"], play_count=25))

    profile = build(events, items)
    assert "熟悉艺人" in profile.familiar_artists
    assert "高次数艺人" in profile.familiar_artists
    unheard = profile.unheard_by_artist["熟悉艺人"]
    assert len(unheard) <= 2
    assert all(track.startswith("fam") for track in unheard)
    assert "fam5" not in unheard


def test_taste_familiar_and_base_score_skeleton():
    events, items = fillers(10, 10, artist_prefix="填充")
    for i in range(6):
        events.append(make_event(f"folk{i}", 1))
        items.append(make_item(f"folk{i}", ["民谣"], ["民谣艺人"]))
    events += [make_event("fav", 1), make_event("repeat", 1), make_event("repeat", 5)]
    items += [
        make_item("fav", ["摇滚"], ["摇滚艺人"], favorite=True),
        make_item("repeat", ["爵士"]),
        make_item("plain", ["古典"]),
    ]

    profile = build(events, items)
    assert list(profile.taste_vector)[0] == "民谣"
    assert len(profile.taste_vector) <= 6
    assert profile.familiar_of["fav"] == 1.0
    assert profile.familiar_of["repeat"] == pytest.approx(0.6)
    assert profile.familiar_of["plain"] == 0.0
    assert profile.base_score["fav"] > profile.base_score["repeat"] > profile.base_score["plain"]


def test_llm_signals_are_global_counts_and_short_daypart_note():
    events = [
        make_event("a", 0, hour=8),
        make_event("b", 0, hour=8, short=True, duration=10),
        make_event("c", 0, hour=20, incomplete=True, completion=0.6),
        make_event("a", 1, hour=9),
        make_event("d", 10, hour=8),
    ]
    items = [make_item(key, [f"流派{key}"]) for key in "abcd"]
    profile = build(events, items)

    assert profile.play_events == {
        "completed": 2,
        "incomplete": 1,
        "short": 1,
        "distinct_tracks": 3,
        "window_days": 7,
    }
    assert len(profile.daypart_note) <= 80
    assert "6-12" in profile.daypart_note
    assert "近7天" in profile.daypart_note


def test_exposure_only_dedups_and_no_dislike_signal_is_invented():
    import json
    from dataclasses import asdict
    from datetime import date

    events, items = fillers(15, 10)
    for i in range(1, 4):
        items.append(make_item(f"fam{i}", ["民谣"], ["熟悉艺人"], favorite=(i <= 2)))
    today = date(2026, 11, 27)
    exposure = {
        "fam3": (today - timedelta(days=5)).isoformat(),
        "fam4": (today - timedelta(days=60)).isoformat(),
    }
    items.append(make_item("fam4", ["民谣"], ["熟悉艺人"]))

    profile = build(events, items, exposure=exposure)
    unheard = profile.unheard_by_artist["熟悉艺人"]
    assert "fam3" not in unheard
    assert "fam4" in unheard
    dump = json.dumps(asdict(profile), ensure_ascii=False)
    assert "dislike" not in dump
    assert "不喜欢" not in dump


def test_build_profile_is_pure_and_repeatable():
    from copy import deepcopy
    from dataclasses import asdict

    events, items = fillers(6, 0)
    events += [make_event("rev", 90), make_event("rev", 40)]
    items.append(make_item("rev", ["民谣"], ["水木年华"], play_count=2))
    original_events = list(events)
    original_items = deepcopy(items)

    first = build(events, items)
    second = build(events, items)

    assert events == original_events
    assert items == original_items
    assert asdict(first) == asdict(second)
