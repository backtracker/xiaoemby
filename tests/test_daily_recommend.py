"""今日推荐 v2/v3 核心算法单测（纯函数，不需要网络与 Emby）。"""

import json
from collections import Counter
from datetime import datetime
from types import SimpleNamespace

import pytest

from xiaomusic.utils.behavior_profile import (
    BehaviorProfile,
    build_profile,
    library_by_id,
)
from xiaomusic.utils.candidate_pool import (
    CandidatePool,
    build_candidate_pool,
    language_of,
)
from xiaomusic.utils.daily_recommend import (
    RecipeInvalid,
    _build_result,
    _mixed_multipliers,
    _pick_local,
    _recommend_penalty,
    _save_cache,
    _save_history,
    ask_llm,
    build_catalog,
    generate,
    is_fresh,
    pick_tracks,
    pick_tracks_segmented,
    pick_tracks_v3,
    snooze_track,
    validate_recipe,
)
from xiaomusic.utils.emby_utils import Audio
from xiaomusic.utils.library_mirror import MirrorModel
from xiaomusic.utils.pr_events import LOCAL_TZ, PREvent, PRFailure, PRReadResult

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
        "llm_max_tokens": 6000,
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


def test_system_prompt_marks_segments_deprecated_and_aligns_with_schema():
    from xiaomusic.utils.daily_recommend import SYSTEM_PROMPT, recipe_schema

    assert "最重要的部分" not in SYSTEM_PROMPT
    assert "3~4 段" not in SYSTEM_PROMPT
    assert "废弃" in SYSTEM_PROMPT or "可选" in SYSTEM_PROMPT
    assert "segments" not in recipe_schema()["properties"]


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
        wanted = [str(i) for i in ids]
        by_id = {str(t.id): t for t in self.tracks}
        return [by_id[i] for i in wanted if i in by_id]

    def fetch_recently_played(self, limit=50):
        return list(self.tracks[:limit])

    def fetch_total_tracks(self):
        return len(self.tracks)


def test_ask_llm_uses_configured_max_tokens(tmp_path, monkeypatch):
    """max_tokens 走配置（推理模型思考需更大预算），默认 6000。"""
    import xiaomusic.utils.daily_recommend as module

    cfg = make_cfg(tmp_path, llm_endpoint="http://fake/v1", llm_api_key="k", llm_model="m")
    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": '{"schema_version":1}'}}]}

    def fake_post(url, json, headers, timeout):
        captured.update(json)
        return FakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)
    ask_llm(build_catalog(make_candidates()), cfg)
    assert captured["max_tokens"] == 6000

    cfg.llm_max_tokens = 9000
    ask_llm(build_catalog(make_candidates()), cfg)
    assert captured["max_tokens"] == 9000

    # 缺省时兜底 6000（兼容旧配置对象没有该字段）
    del cfg.llm_max_tokens
    ask_llm(build_catalog(make_candidates()), cfg)
    assert captured["max_tokens"] == 6000


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


def test_is_fresh_matches_generate_cache_freeze(tmp_path, monkeypatch):
    """「生成中」提示的跳过条件要与 generate 的当天冻结逻辑一致。"""
    tracks = make_candidates()
    cfg = make_cfg(tmp_path)
    import xiaomusic.utils.daily_recommend as module

    original = module._today
    module._today = lambda cfg: "2026-11-22"
    try:
        assert is_fresh(cfg) is False  # 尚无缓存
        _save_cache(cfg, _build_result(tracks, "理由", "llm", "2026-11-22", 0))
        assert is_fresh(cfg) is True  # 当天缓存可秒回
        assert is_fresh(cfg, renew=True) is False  # 换批仍需重新生成

        _save_cache(cfg, _build_result(tracks, "理由", "llm", "2026-11-22", 3))
        assert is_fresh(cfg, renew=True) is True  # 换批配额用尽后走缓存

        module._today = lambda cfg: "2026-11-23"
        assert is_fresh(cfg) is False  # 隔天缓存失效
    finally:
        module._today = original


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


# ---- v3：档位判定、冻结与回退（主缝 generate(cfg, emby)）----
def _v3_audio(i, genres, artist="甲", year=2000, favorite=False, play_count=0):
    return Audio(
        name=f"歌{i}",
        id=str(i),
        album=f"专{i}",
        album_artist=artist,
        genres=genres,
        production_year=year,
        play_count=play_count,
        is_favorite=favorite,
        date_created="2020-01-01T00:00:00Z",
    )


def _v3_raw(i, genres, artist="甲", year=2000, favorite=False, play_count=0):
    return {
        "Id": str(i),
        "Name": f"歌{i}",
        "Genres": genres,
        "ProductionYear": year,
        "ArtistItems": [{"Name": artist}],
        "AlbumArtist": artist,
        "Album": f"专{i}",
        "DateCreated": "2020-01-01T00:00:00.0000000Z",
        "RunTimeTicks": 200 * 10_000_000,
        "Path": f"/music/{i}.flac",
        "UserData": {
            "PlayCount": play_count,
            "IsFavorite": favorite,
            "LastPlayedDate": None,
        },
    }


def _v3_library(specs, user_id="u1", stale=False):
    """构造镜像状态 + 快照 + 对应 Audio 列表；specs = [(i, genres, artist, year), ...]。"""
    raws = []
    audios = []
    for spec in specs:
        i, genres = spec[0], spec[1]
        artist = spec[2] if len(spec) > 2 else f"歌手{i % 8}"
        year = spec[3] if len(spec) > 3 else 2000
        raws.append(_v3_raw(i, genres, artist, year))
        audios.append(_v3_audio(i, genres, artist, year))
    state = MirrorModel.from_emby_pages([raws], user_id)
    if stale:
        state = MirrorModel.reduce(state, {"type": "ADVANCE_DAYS", "n": 30})
    # LibraryMirror.snapshot() 每行都带 id 列；MirrorModel 状态里 id 是字典键。
    snapshot = [
        dict(row, id=item_id)
        for item_id, row in state["mirror"]["items"].items()
    ]
    return state, snapshot, audios


def _v3_event(day, item_id, user_id="u1", *, valid=True, short=False, incomplete=False):
    return PREvent(
        user_id=user_id,
        item_id=str(item_id),
        device_name="dev",
        started_at=datetime(2026, 11, 1 + day, 10, 0, tzinfo=LOCAL_TZ),
        duration_seconds=30.0 if short else 200.0,
        completion_rate=None if short else (0.4 if incomplete else 1.0),
        is_valid=valid,
        is_extremely_short=short,
        is_incomplete=incomplete,
        segment_count=1,
    )


def _v3_cfg(tmp_path, **overrides):
    values = {
        "cache_dir": str(tmp_path),
        "timezone": "Asia/Shanghai",
        "enable_daily_recommend": True,
        "llm_endpoint": "http://fake/v1",
        "llm_api_key": "k",
        "llm_model": "m",
        "daily_recommend_count": 20,
        "explore_ratio": 0.15,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _v3_stub_llm(monkeypatch, reason="今天按你最近的收听编排"):
    recipe = {
        "schema_version": 1,
        "intent": "comfort",
        "reason": reason,
        "include": {"genres": ["Mandopop", "J-Pop"]},
        "weights": {"familiarity": 0.6, "novelty": 0.5, "mood_match": 0.5},
    }
    monkeypatch.setattr(
        "xiaomusic.utils.daily_recommend.ask_llm", lambda *a, **kw: recipe
    )


def _v3_inject(emby, pr_events, state, snapshot):
    emby.pr_events = pr_events
    emby.mirror_state = state
    emby.library_snapshot = snapshot
    return emby


def test_v3_pr_mode_when_span_and_events_qualified(tmp_path, monkeypatch):
    specs = [(i, [g]) for i, g in enumerate(
        ["Mandopop", "Cantopop", "国语", "华语民谣", "J-Pop", "Anime", "Rock", "Pop"] * 3
    )]
    state, snapshot, audios = _v3_library(specs)
    events = tuple(_v3_event(d % 10, str(1 + d % 2)) for d in range(20))
    cfg = _v3_cfg(tmp_path)
    emby = _v3_inject(
        FakeEmby(audios),
        PRReadResult(True, None, "u1", events),
        state,
        snapshot,
    )
    _v3_stub_llm(monkeypatch)

    result = generate(cfg, emby)
    assert result.mode == "pr"
    assert result.fallback_reason == ""
    assert result.segments == []
    assert len(result.tracks) >= 10


def test_v3_mixed_mode_when_weak_but_sufficient(tmp_path, monkeypatch):
    specs = [(i, [g]) for i, g in enumerate(
        ["Mandopop", "Cantopop", "国语", "华语民谣", "J-Pop", "Anime", "Rock", "Pop"] * 3
    )]
    state, snapshot, audios = _v3_library(specs)
    events = tuple(_v3_event(d, 1 + d) for d in range(5))
    cfg = _v3_cfg(tmp_path)
    emby = _v3_inject(
        FakeEmby(audios),
        PRReadResult(True, None, "u1", events),
        state,
        snapshot,
    )
    _v3_stub_llm(monkeypatch)

    result = generate(cfg, emby)
    assert result.mode == "mixed"
    assert emby.candidate_calls == 1  # 混合模式沿用 v2 候选池


def test_v3_v2_fallback_on_pr_failure(tmp_path, monkeypatch):
    specs = [(i, [g]) for i, g in enumerate(["Mandopop", "J-Pop", "Rock"] * 8)]
    state, snapshot, audios = _v3_library(specs)
    cfg = _v3_cfg(tmp_path)
    emby = _v3_inject(
        FakeEmby(audios),
        PRReadResult.failure(PRFailure.QUERY_FAILED),
        state,
        snapshot,
    )
    _v3_stub_llm(monkeypatch)

    result = generate(cfg, emby)
    assert result.mode == "v2"
    assert result.fallback_reason == "query_failed"
    assert len(result.tracks) >= 10  # 回退不打断出歌单


def test_v3_v2_fallback_on_insufficient_data(tmp_path, monkeypatch):
    specs = [(i, [g]) for i, g in enumerate(["Mandopop", "J-Pop", "Rock"] * 8)]
    state, snapshot, audios = _v3_library(specs)
    events = tuple(_v3_event(d, 1 + d) for d in range(3))
    cfg = _v3_cfg(tmp_path)
    emby = _v3_inject(
        FakeEmby(audios),
        PRReadResult(True, None, "u1", events),
        state,
        snapshot,
    )
    _v3_stub_llm(monkeypatch)

    result = generate(cfg, emby)
    assert result.mode == "v2"
    assert result.fallback_reason == "insufficient_data"


def test_v3_v2_fallback_when_mirror_stale(tmp_path, monkeypatch):
    specs = [(i, [g]) for i, g in enumerate(["Mandopop", "J-Pop", "Rock"] * 8)]
    state, snapshot, audios = _v3_library(specs, stale=True)
    events = tuple(_v3_event(d % 10, str(1 + d % 2)) for d in range(20))
    cfg = _v3_cfg(tmp_path)
    emby = _v3_inject(
        FakeEmby(audios),
        PRReadResult(True, None, "u1", events),
        state,
        snapshot,
    )
    _v3_stub_llm(monkeypatch)

    result = generate(cfg, emby)
    assert result.mode == "v2"
    assert result.fallback_reason == "mirror_stale"


@pytest.mark.parametrize("event_count", [20, 5])
def test_v3_v2_fallback_when_mirror_snapshot_missing(tmp_path, monkeypatch, event_count):
    specs = [(i, [g]) for i, g in enumerate(["Mandopop", "J-Pop", "Rock"] * 8)]
    _state, _snapshot, audios = _v3_library(specs)
    empty_state = MirrorModel.init()
    events = tuple(_v3_event(d % 10, str(1 + d % 2)) for d in range(event_count))
    cfg = _v3_cfg(tmp_path)
    emby = _v3_inject(
        FakeEmby(audios), PRReadResult(True, None, "u1", events), empty_state, None
    )
    _v3_stub_llm(monkeypatch)

    result = generate(cfg, emby)
    assert result.mode == "v2"
    assert result.fallback_reason == "mirror_unavailable"
    assert len(result.tracks) >= 10  # 回退不打断出歌单


def test_v3_v2_fallback_when_mirror_snapshot_empty(tmp_path, monkeypatch):
    specs = [(i, [g]) for i, g in enumerate(["Mandopop", "J-Pop", "Rock"] * 8)]
    state, _snapshot, audios = _v3_library(specs)
    events = tuple(_v3_event(d % 10, str(1 + d % 2)) for d in range(20))
    cfg = _v3_cfg(tmp_path)
    emby = _v3_inject(
        FakeEmby(audios), PRReadResult(True, None, "u1", events), state, []
    )
    _v3_stub_llm(monkeypatch)

    result = generate(cfg, emby)
    assert result.mode == "v2"
    assert result.fallback_reason == "mirror_unavailable"


def test_v3_mode_frozen_for_the_day_on_renew(tmp_path, monkeypatch):
    specs = [(i, [g]) for i, g in enumerate(["Mandopop", "J-Pop", "Rock"] * 8)]
    state, snapshot, audios = _v3_library(specs)
    qualified = tuple(_v3_event(d % 10, str(1 + d % 2)) for d in range(20))
    weak = tuple(_v3_event(d, 1 + d) for d in range(3))
    cfg = _v3_cfg(tmp_path)
    emby = _v3_inject(FakeEmby(audios), PRReadResult(True, None, "u1", qualified), state, snapshot)
    _v3_stub_llm(monkeypatch)

    import xiaomusic.utils.daily_recommend as module

    original = module._today
    module._today = lambda cfg: "2026-11-22"
    try:
        first = generate(cfg, emby)
        assert first.mode == "pr"
        # 「换一批」时 PR 数据突然变弱，仍沿用当天冻结的档位
        emby.pr_events = PRReadResult(True, None, "u1", weak)
        second = generate(cfg, emby, renew=True)
    finally:
        module._today = original
    assert first.mode == second.mode == "pr"
    assert second.renewals == 1


def test_build_catalog_carries_pr_behavior_fields_only_when_supplied():
    catalog = build_catalog(make_candidates())
    assert "play_events" not in catalog["behavior"]
    assert "daypart_note" not in catalog["behavior"]

    catalog = build_catalog(
        make_candidates(),
        play_events={"completed": 7, "short": 2, "window_days": 7},
        daypart_note="近7天 6-12点占80%，以Mandopop居多" * 4,
    )
    assert catalog["behavior"]["play_events"]["completed"] == 7
    assert len(catalog["behavior"]["daypart_note"]) <= 80


def test_v3_front_five_constraints(tmp_path, monkeypatch):
    specs = [(i, [g]) for i, g in enumerate(
        ["Mandopop", "Cantopop", "国语", "华语民谣", "J-Pop", "Anime", "Rock", "Pop"] * 3
    )]
    state, snapshot, audios = _v3_library(specs)
    events = tuple(_v3_event(d % 10, str(1 + d % 2)) for d in range(20))
    cfg = _v3_cfg(tmp_path)
    emby = _v3_inject(FakeEmby(audios), PRReadResult(True, None, "u1", events), state, snapshot)
    _v3_stub_llm(monkeypatch)

    result = generate(cfg, emby)
    assert result.mode == "pr"
    front = result.tracks[:5]
    assert len(front) == 5

    genre_counts = Counter(g for audio in front for g in audio.genres)
    assert max(genre_counts.values()) <= 2  # 每流派 ≤2
    language_counts = Counter(language_of({"genres": a.genres}) for a in front)
    assert max(language_counts.values()) <= 3  # 每语言 ≤3
    foreign = sum(
        1 for a in front if language_of({"genres": a.genres}) != "华语"
    )
    assert 1 <= foreign <= 2  # 非华语 1~2 首

    # ≥1 首来自探索池（没听过 / 新发行 / 冷门簇）
    profile = build_profile(
        events, library=library_by_id(snapshot), user_id="u1", today="2026-11-22"
    )
    pool = build_candidate_pool(snapshot, profile, today="2026-11-22")
    assert {str(a.id) for a in front} & set(pool.explore)


def test_seed_recall_changes_v3_pick_order_only_when_enabled():
    items = (
        {"id": "a", "genres": ["Mandopop"], "artist": ["甲"], "album": "专A", "year": 2000},
        {"id": "b", "genres": ["J-Pop"], "artist": ["乙"], "album": "专B", "year": 2000},
    )
    profile = BehaviorProfile(base_score={"a": 2.0, "b": 2.1})
    pool = CandidatePool(
        items=items,
        sources={"a": ("seed",), "b": ("genre",)},
        explore=(),
        seed_scores={"a": 0.9},
        seeds=("x",),
        total_active=2,
    )
    off = pick_tracks_v3(
        pool, profile, SimpleNamespace(seed_recall_enabled=False), "2026-11-27", count=2
    )
    on = pick_tracks_v3(
        pool, profile, SimpleNamespace(seed_recall_enabled=True), "2026-11-27", count=2
    )
    assert off[0] == "b"
    assert on[0] == "a"


def test_v3_caps_hold_for_mono_genre_library(tmp_path, monkeypatch):
    specs = [(i, ["Mandopop"]) for i in range(20)]
    state, snapshot, audios = _v3_library(specs)
    events = tuple(_v3_event(d, 1 + d % 10) for d in range(20))
    cfg = _v3_cfg(tmp_path)
    emby = _v3_inject(FakeEmby(audios), PRReadResult(True, None, "u1", events), state, snapshot)
    _v3_stub_llm(monkeypatch)

    result = generate(cfg, emby)
    assert result.mode == "pr"
    # 单流派库里前五只能放 2 首，其余最多补到「流派 ≤5 / 20 首」的上限；
    # 宁可少给，也不能靠放宽上限凑满 20 首。
    all_genres = Counter(g for a in result.tracks for g in a.genres)
    assert max(all_genres.values(), default=0) <= 5
    assert len(result.tracks) <= 5


def test_v3_foreign_min_replacement_respects_caps():
    items = tuple(
        {
            "id": str(i),
            "genres": ["Mandopop"],
            "artist": [f"甲{i}"],
            "album": f"专{i}",
            "year": 2000,
        }
        for i in range(10)
    ) + (
        {"id": "f", "genres": ["J-Pop"], "artist": ["乙"], "album": "专F", "year": 2000},
    )
    profile = BehaviorProfile(base_score={str(i): 2.0 - i / 10 for i in range(10)})
    profile.base_score["f"] = 1.0
    pool = CandidatePool(
        items=items, sources={}, explore=(), seed_scores={}, seeds=(), total_active=11
    )
    cfg = SimpleNamespace(front_five_foreign_min=1, front_five_foreign_max=2)
    picked = pick_tracks_v3(pool, profile, cfg, "2026-11-27", count=5)
    by_id = {item["id"]: item for item in items}
    genres = Counter(g for item_id in picked for g in by_id[item_id]["genres"])
    assert max(genres.values(), default=0) <= 2
    languages = Counter(language_of(by_id[item_id]) for item_id in picked)
    assert max(languages.values(), default=0) <= 3


def test_v3_mixed_multipliers_bounded_and_soft():
    specs = [(i, [g]) for i, g in enumerate(
        ["Mandopop", "Cantopop", "国语", "华语民谣", "J-Pop", "Anime", "Rock", "Pop"] * 3
    )]
    _state, snapshot, audios = _v3_library(specs)
    events = tuple(_v3_event(d, 1 + d) for d in range(5))
    profile = build_profile(
        events, library=library_by_id(snapshot), user_id="u1", today="2026-11-22"
    )
    multipliers = _mixed_multipliers(profile, [a.id for a in audios])
    assert multipliers
    assert all(0.8 <= value <= 1.2 for value in multipliers.values())

    catalog = build_catalog(audios)
    recipe = validate_recipe(
        {
            "schema_version": 1,
            "intent": "discover",
            "reason": "混合模式只做有限打分修正",
            "include": {"genres": ["Mandopop", "Cantopop", "国语", "华语民谣", "J-Pop", "Anime", "Rock", "Pop"]},
            "weights": {"novelty": 0.7},
        },
        catalog,
    )
    adjusted = pick_tracks(recipe, audios, catalog, 20, score_multipliers=multipliers)
    assert len(adjusted) >= 10  # 只修正打分，不剔除曲目


def test_v3_config_fields_have_decided_defaults():
    from xiaomusic.config import Config

    cfg = Config()
    assert (cfg.pr_mode_min_days, cfg.pr_mode_min_events) == (7, 20)
    assert (cfg.mixed_mode_min_days, cfg.mixed_mode_min_events) == (1, 5)
    assert cfg.mirror_stale_days == 7
    assert (cfg.short_seconds, cfg.valid_completion, cfg.weak_completion) == (60, 0.8, 0.5)
    assert (cfg.negative_half_life_days, cfg.positive_half_life_days) == (7, 14)
    assert (cfg.revisit_max_per_day, cfg.revisit_min_gap_days) == (2, 30)
    assert (cfg.immersion_min_plays, cfg.immersion_min_share) == (5, 0.5)
    assert cfg.familiar_per_artist_cap == 2
    assert cfg.seed_recall_enabled is False
    assert cfg.similarity_genre_weight == 0.5
    assert cfg.similarity_language_weight == 0.2
    assert (cfg.freshness_weight, cfg.diversity_weight, cfg.mmr_lambda) == (0.9, 0.8, 0.7)
    # 旧字段仍可用
    assert cfg.explore_ratio == 0.15
    assert cfg.daily_recommend_count == 20

