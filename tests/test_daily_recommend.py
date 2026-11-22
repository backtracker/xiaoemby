"""今日推荐 v2 核心算法单测（纯函数，不需要网络与 Emby）。"""

import json
from collections import Counter
from types import SimpleNamespace

import pytest

from xiaomusic.utils.daily_recommend import (
    RecipeInvalid,
    _build_result,
    _pick_local,
    _recommend_penalty,
    _save_cache,
    _save_history,
    build_catalog,
    generate,
    pick_tracks,
    pick_tracks_segmented,
    snooze_track,
    validate_recipe,
)
from xiaomusic.utils.emby_utils import Audio

GENRES = ["流行", "民谣", "摇滚", "古典", "J-Pop", "爵士"]
ARTISTS = ["甲", "乙", "丙", "丁", "戊", "己"]


def make_audio(
    name,
    genres,
    artist,
    play_count=0,
    is_favorite=False,
    year=2000,
    album=None,
    last_played_at="",
    date_created="",
):
    return Audio(
        name=name,
        id=name,
        album=album or f"{name}专辑",
        album_artist=artist,
        genres=genres,
        production_year=year,
        play_count=play_count,
        is_favorite=is_favorite,
        last_played_at=last_played_at,
        date_created=date_created,
    )


def make_candidates():
    return [
        make_audio(
            f"歌{i}",
            [GENRES[i % len(GENRES)]],
            ARTISTS[i % len(ARTISTS)],
            play_count=i,
            year=1990 + (i % 4) * 10,
        )
        for i in range(24)
    ]


def make_cfg(cache_dir, **overrides):
    values = {
        "cache_dir": str(cache_dir),
        "timezone": "Asia/Shanghai",
        "enable_daily_recommend": False,
        "llm_endpoint": "",
        "llm_api_key": "",
        "llm_model": "",
        "daily_recommend_count": 20,
        "llm_supports_schema": False,
        "explore_ratio": 0.15,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def recipe_of(catalog, **extra):
    data = {
        "schema_version": 1,
        "intent": "discover",
        "reason": "今天想听点不一样的",
        "include": {"genres": GENRES[:5]},
        "weights": {"familiarity": 0.5, "novelty": 0.7, "mood_match": 0.5},
    }
    data.update(extra)
    return validate_recipe(data, catalog)


# ---- 红线与契约 ----
def test_catalog_never_leaks_single_track_metadata():
    candidates = make_candidates()
    catalog = build_catalog(candidates)
    text = json.dumps(catalog, ensure_ascii=False)
    for audio in candidates:
        assert audio.name not in text
    assert "流行" in catalog["vocabulary"]["genres"]


def test_recipe_rejects_single_track_fields():
    with pytest.raises(RecipeInvalid):
        validate_recipe(
            {
                "schema_version": 1,
                "intent": "discover",
                "reason": "今天听点好的",
                "tracks": ["歌A"],
            },
            build_catalog(make_candidates()),
        )


def test_recipe_rejects_wrong_version_and_bad_reason():
    catalog = build_catalog(make_candidates())
    with pytest.raises(RecipeInvalid):
        validate_recipe({"schema_version": 2, "intent": "discover", "reason": "ok"}, catalog)
    with pytest.raises(RecipeInvalid):
        validate_recipe({"schema_version": 1, "intent": "discover", "reason": ""}, catalog)


def test_sanitize_recipe_tolerates_deepseek_quirks():
    """DeepSeek 在 json_object 模式下的格式偏差应被归一化。"""
    catalog = build_catalog(make_candidates())
    recipe = validate_recipe(
        {
            "type": "json_object",
            "intent": "comfort",
            "reason": "今天想听熟悉的旋律",
            "include": {"genres": ["流行", "民谣"]},
            "exclude": {"genres": ["摇滚"]},
            "boost": {"artists": ["甲"], "genres": ["民谣"]},
            "weights": {"genres": 0.7, "familiarity": 0.8},
            "soft_context": "偏好温暖",
            "behavior": {"use": ["play_count_top_genres"]},
        },
        catalog,
    )
    assert recipe.schema_version == 1
    assert {item.value for item in recipe.boost} == {"甲", "民谣"}
    assert recipe.weights.familiarity == 0.8
    assert recipe.weights.novelty == 0.5
    assert isinstance(recipe.soft_context, dict)


def test_recipe_drops_out_of_vocabulary_values_and_exclude_wins():
    catalog = build_catalog(make_candidates())
    recipe = validate_recipe(
        {
            "schema_version": 1,
            "intent": "discover",
            "reason": "今天听点熟悉的歌",
            "include": {"genres": ["流行", "不存在的流派"]},
            "exclude": {"genres": ["流行"]},
            "boost": [{"value": "不存在的流派", "weight": 5}],
            "weights": {"familiarity": 2, "novelty": -1, "mood_match": 0.5},
        },
        catalog,
    )
    assert recipe.include.genres == []
    assert recipe.exclude.genres == ["流行"]
    assert recipe.boost == []
    assert recipe.weights.familiarity == 0.5
    assert recipe.weights.novelty == 0.5


# ---- 选曲机制 ----
def test_pick_tracks_enforces_multi_dimensional_caps():
    candidates = make_candidates()
    catalog = build_catalog(candidates)
    picked = pick_tracks(recipe_of(catalog), candidates, catalog, 20)
    artists = Counter(a.album_artist for a in picked)
    genres = {g for a in picked for g in a.genres}
    albums = Counter((a.album, a.album_artist) for a in picked)
    assert len(picked) >= 10
    assert all(n <= 3 for n in artists.values())  # 每歌手 ≤3
    assert all(n <= 2 for n in albums.values())  # 每专辑 ≤2
    assert len(genres) >= 5  # ≥5 流派
    assert max(Counter(g for a in picked for g in a.genres).values()) <= 8  # 单流派 ≤40%


def test_pick_tracks_penalizes_recently_recommended_tracks():
    candidates = make_candidates()
    catalog = build_catalog(candidates)
    penalties = {
        "genre": {},
        "track": {"歌0": 0.7},
        "artist": {"甲": 0.5},
    }
    picked = pick_tracks(
        recipe_of(catalog), candidates, catalog, 20, penalties=penalties
    )
    assert "歌0" not in [a.name for a in picked]
    assert sum(1 for a in picked if a.album_artist == "甲") <= 2  # 跨天降权后名额减 1


def test_recommend_penalty_builds_three_tables():
    history = {
        "2026-11-22": {
            "track_ids": ["t1", "t2"],
            "artists": ["甲"],
            "genres": ["流行", "民谣", "摇滚", "古典", "J-Pop"],
        }
    }
    penalties = _recommend_penalty(history, "2026-11-22")
    assert penalties["track"]["t1"] == pytest.approx(0.7)
    assert penalties["artist"]["甲"] == pytest.approx(0.5)
    # 流派只惩罚前 3 个（相对化），不是全表均匀
    assert set(penalties["genre"]) == {"流行", "民谣", "摇滚"}


def test_pick_tracks_uses_favorites_as_anchors():
    candidates = make_candidates()
    candidates[0] = make_audio("收藏歌", ["流行"], "甲", is_favorite=True)
    catalog = build_catalog(candidates)
    picked = pick_tracks(recipe_of(catalog), candidates, catalog, 20)
    assert picked[0].name == "收藏歌"


def test_pick_tracks_explore_quota_for_discover():
    candidates = make_candidates()
    catalog = build_catalog(candidates, today="2026-11-22")
    picked = pick_tracks(
        recipe_of(catalog),
        candidates,
        catalog,
        20,
        explore_ratio=0.2,
        today="2026-11-22",
    )
    fresh = [
        a for a in picked if a.is_favorite is False and a.play_count < 5
    ]
    assert len(fresh) >= 1  # 至少有探索位


def test_pick_tracks_returns_empty_when_too_few_candidates():
    candidates = [make_audio("歌A", ["流行"], "甲")]
    catalog = build_catalog(candidates)
    assert pick_tracks(recipe_of(catalog), candidates, catalog, 20) == []


def test_no_genre_library_uses_artist_diversity():
    """真实 Emby 库若没有流派标签，用艺术家做多样性维度。"""
    candidates = [make_audio(f"无流派{i}", [], f"歌手{i % 6}", 0) for i in range(24)]
    catalog = build_catalog(candidates)
    assert catalog["library"]["genres"] == 0
    picked = pick_tracks(recipe_of(catalog), candidates, catalog, 20)
    assert len(picked) >= 10
    assert len({a.album_artist for a in picked}) >= 5


def test_include_dimensions_are_or():
    """流派不匹配但艺术家匹配时仍应入选（include 各维度是 OR）。"""
    candidates = make_candidates()
    catalog = build_catalog(candidates)
    recipe = validate_recipe(
        {
            "schema_version": 1,
            "intent": "discover",
            "reason": "测试OR语义",
            "include": {"artists": ["甲"], "genres": ["摇滚"], "decades": ["2020s"]},
            "weights": {"novelty": 0.5},
        },
        catalog,
    )
    picked = pick_tracks(recipe, candidates, catalog, 20)
    assert picked and any(a.album_artist == "甲" for a in picked)


def test_recipe_accepts_and_validates_segments():
    """LLM 的节目分段应被解析、share 归一化、枚举/权重清洗。"""
    catalog = build_catalog(make_candidates())
    recipe = validate_recipe(
        {
            "schema_version": 1,
            "intent": "discover",
            "reason": "今天做一份有起承转合的节目",
            "include": {"genres": GENRES[:5]},
            "segments": [
                {
                    "theme": "熟悉开场",
                    "transition": "先用熟悉的歌接住情绪",
                    "intent": "comfort",
                    "share": 0.3,
                    "include": {"genres": ["流行"]},
                    "weights": {"familiarity": 0.9, "novelty": 0.1, "mood_match": 0.5},
                },
                {
                    "theme": "探索中段",
                    "transition": "往没听过的方向走一段",
                    "intent": "discover",
                    "share": 0.4,
                    "include": {"genres": ["摇滚"]},
                    "boost": [{"value": "不存在的流派", "weight": 3}],
                    "weights": {"familiarity": 0.1, "novelty": 0.9, "mood_match": 0.5},
                },
                {
                    "theme": "收尾沉淀",
                    "transition": "最后回到平缓节奏",
                    "intent": "mellow",
                    "share": 0.3,
                },
            ],
        },
        catalog,
    )
    assert len(recipe.segments) == 3
    assert [s.theme for s in recipe.segments] == ["熟悉开场", "探索中段", "收尾沉淀"]
    assert recipe.segments[1].boost == []  # 越界 boost 被丢弃
    assert recipe.segments[0].weights.novelty == 0.1


def test_pick_tracks_segmented_produces_program():
    """分段选曲应跨段去重、累计配额，并输出每段的主题与串联语。"""
    candidates = make_candidates()
    catalog = build_catalog(candidates)
    recipe = validate_recipe(
        {
            "schema_version": 1,
            "intent": "discover",
            "reason": "一份三段式节目",
            "include": {"genres": GENRES[:5]},
            "segments": [
                {
                    "theme": "开场", "transition": "熟悉开场", "intent": "comfort",
                    "share": 0.3, "include": {"genres": ["流行"]},
                    "weights": {"familiarity": 0.9, "novelty": 0.1, "mood_match": 0.5},
                },
                {
                    "theme": "中段", "transition": "往没听过方向走", "intent": "discover",
                    "share": 0.4, "include": {"genres": ["摇滚", "J-Pop"]},
                    "weights": {"familiarity": 0.1, "novelty": 0.9, "mood_match": 0.5},
                },
                {
                    "theme": "收尾", "transition": "平缓收束", "intent": "mellow",
                    "share": 0.3, "include": {"genres": ["民谣"]},
                },
            ],
        },
        catalog,
    )
    tracks, metas = pick_tracks_segmented(recipe, candidates, catalog, 20)
    assert len(tracks) >= 10
    assert len(metas) == 3
    assert [m["theme"] for m in metas] == ["开场", "中段", "收尾"]
    assert all(m["transition"] for m in metas)
    # 跨段曲目不重复
    ids = [str(a.id) for a in tracks]
    assert len(ids) == len(set(ids))


def test_local_fallback_caps_per_artist():
    candidates = [make_audio(f"歌{i}", ["流行"], "同一人", i) for i in range(24)]
    picked = _pick_local(candidates, 20)
    assert len(picked) == 3


# ---- 编排与持久化 ----
class FakeEmby:
    def __init__(self, tracks):
        self.tracks = tracks
        self.candidate_calls = 0

    def fetch_recommend_candidates(self, limit=800):
        self.candidate_calls += 1
        return list(self.tracks)

    def fetch_tracks_by_ids(self, ids):
        wanted = {str(i) for i in ids}
        return [t for t in self.tracks if str(t.id) in wanted]

    def fetch_recently_played(self, limit=50):
        return list(self.tracks[:limit])

    def fetch_total_tracks(self):
        return len(self.tracks)


def test_generate_with_llm_recipe_end_to_end(tmp_path, monkeypatch):
    tracks = make_candidates()
    cfg = make_cfg(
        tmp_path,
        enable_daily_recommend=True,
        llm_endpoint="http://fake/v1",
        llm_api_key="k",
        llm_model="m",
        daily_recommend_count=20,
        explore_ratio=0.15,
    )
    emby = FakeEmby(tracks)
    recipe = {
        "schema_version": 1,
        "intent": "discover",
        "reason": "今天想听点不一样的歌",
        "include": {"genres": GENRES[:5]},
        "weights": {"familiarity": 0.5, "novelty": 0.7, "mood_match": 0.5},
    }

    class FakeResponse:
        ok = True
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {"message": {"content": json.dumps(recipe, ensure_ascii=False)}}
                ]
            }

    monkeypatch.setattr(
        "xiaomusic.utils.daily_recommend.requests.post", lambda *a, **kw: FakeResponse()
    )

    result = generate(cfg, emby)
    assert result.source == "llm"
    assert result.reason == "今天想听点不一样的歌"
    assert len(result.tracks) >= 10
    # 历史与 snooze 数据结构已落盘
    assert (tmp_path / "recommend_history.json").exists()
    assert len(json.loads((tmp_path / "recommend_history.json").read_text())) >= 1


def test_generate_restores_same_day_cache_without_calling_llm(tmp_path, monkeypatch):
    tracks = make_candidates()
    cfg = make_cfg(
        tmp_path,
        enable_daily_recommend=True,
        llm_endpoint="http://fake/v1",
        llm_api_key="k",
        llm_model="m",
    )
    emby = FakeEmby(tracks)

    cached = _build_result(tracks, "缓存的理由", "local", "2026-11-22", 1)
    _save_cache(cfg, cached)
    _save_history(cfg, cached)

    import xiaomusic.utils.daily_recommend as module

    original = module._today
    module._today = lambda cfg: "2026-11-22"
    monkeypatch.setattr(
        module,
        "ask_llm",
        lambda *a, **kw: pytest.fail("当天冻结不应调用 LLM"),
    )
    try:
        result = generate(cfg, emby)
    finally:
        module._today = original

    assert result.reason == "缓存的理由"
    assert result.source == "local"
    assert emby.candidate_calls == 0  # 命中缓存，没有重新拉候选池


def test_generate_renew_creates_new_result(tmp_path, monkeypatch):
    tracks = make_candidates()
    cfg = make_cfg(
        tmp_path,
        enable_daily_recommend=True,
        llm_endpoint="http://fake/v1",
        llm_api_key="k",
        llm_model="m",
    )
    emby = FakeEmby(tracks)
    recipes = [
        {
            "schema_version": 1,
            "intent": "discover",
            "reason": f"第{i}批推荐",
            "include": {"genres": GENRES[:5]},
            "weights": {"novelty": 0.7},
        }
        for i in range(2)
    ]
    calls = {"n": 0}

    def fake_ask(catalog, cfg):
        data = recipes[min(calls["n"], 1)]
        calls["n"] += 1
        return data

    monkeypatch.setattr("xiaomusic.utils.daily_recommend.ask_llm", fake_ask)
    import xiaomusic.utils.daily_recommend as module

    original = module._today
    module._today = lambda cfg: "2026-11-22"
    try:
        first = generate(cfg, emby)
        second = generate(cfg, emby, renew=True)
    finally:
        module._today = original

    assert first.source == "llm"
    assert second.source == "llm"
    assert second.renewals == 1
    assert second.reason != first.reason


def test_snooze_excludes_track(tmp_path, monkeypatch):
    tracks = make_candidates()
    cfg = make_cfg(
        tmp_path,
        enable_daily_recommend=True,
        llm_endpoint="http://fake/v1",
        llm_api_key="k",
        llm_model="m",
    )
    snooze_track(cfg, "歌0", days=30, today="2026-11-22")
    recipe = {
        "schema_version": 1,
        "intent": "discover",
        "reason": "听点新的",
        "include": {"genres": GENRES[:5]},
        "weights": {"novelty": 0.7},
    }
    monkeypatch.setattr(
        "xiaomusic.utils.daily_recommend.ask_llm", lambda *a, **kw: recipe
    )
    import xiaomusic.utils.daily_recommend as module

    original = module._today
    module._today = lambda cfg: "2026-11-22"
    try:
        result = generate(cfg, FakeEmby(tracks))
    finally:
        module._today = original
    assert "歌0" not in result.track_ids
