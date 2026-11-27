"""候选池与种子相似召回：只通过注入的镜像快照 / 画像断言外部可观察行为。"""

from xiaomusic.utils.behavior_profile import BehaviorProfile, Immersion
from xiaomusic.utils.candidate_pool import (
    CandidateConfig,
    SimilarityWeights,
    build_candidate_pool,
    language_of,
    select_strong_seeds,
    similarity,
)


def item(item_id, genres=(), artist=(), album="", year=0, **extra):
    row = {
        "id": item_id,
        "genres": list(genres),
        "artist": list(artist),
        "album": album,
        "year": year,
        "tombstone": False,
    }
    row.update(extra)
    return row


def test_similarity_uses_unified_weights_and_genre_based_language():
    base = item("a", ["流行"], ["甲"], "专辑", 2005)
    identical = item("b", ["流行"], ["甲"], "专辑", 2005)
    wider_genres = item("c", ["流行", "摇滚"], ["甲"], "专辑", 2005)
    other_artist = item("d", ["摇滚"], ["乙"], "别的专辑", 2015)

    assert similarity(base, identical) == 1.0
    assert similarity(base, wider_genres) == 0.75
    assert similarity(base, other_artist) == 0.2

    album_only = SimilarityWeights(genre=0.0, language=0.0, artist=0.0, album=1.0, era=0.0)
    assert similarity(base, identical, weights=album_only) == 1.0
    assert similarity(base, wider_genres, weights=album_only) == 1.0
    assert similarity(base, other_artist, weights=album_only) == 0.0


def test_language_is_approximated_from_genre_tags():
    assert language_of(item("a", ["Mandopop"])) == "华语"
    assert language_of(item("b", ["Cantopop", "粤语"])) == "华语"
    assert language_of(item("c", ["J-Pop", "Anime"])) == "日语"
    assert language_of(item("d", ["K-Pop"])) == "韩语"
    assert language_of(item("e", ["Instrumental"])) == "纯音乐"
    assert language_of(item("f", ["Rock", "流行"])) == "英语"


def test_pool_covers_whole_mirror_and_dedupes_by_id():
    snapshot = [item(f"t{i}", ["流行"], ["甲"]) for i in range(120)]
    snapshot.append(item("t0", ["重复来源"]))  # 同 id 再出现
    snapshot.append(item("gone", ["流行"], tombstone=True))

    pool = build_candidate_pool(
        snapshot,
        BehaviorProfile(),
        blocked_ids={"t7"},
        exposure={"t8": "2026-11-20", "t9": "2026-09-01"},
        today="2026-11-27",
    )
    ids = [row["id"] for row in pool.items]

    assert len(ids) == len(set(ids)) == 118
    assert "t0" in ids and "t9" in ids
    assert "t7" not in ids and "t8" not in ids and "gone" not in ids
    assert next(row for row in pool.items if row["id"] == "t0")["genres"] == ["流行"]
    assert pool.total_active == 120


def seed_profile():
    return BehaviorProfile(
        base_score={
            "seed": 4.0,
            "fav": 4.5,
            "near": 0.0,
            "same_artist": 0.0,
            "heard": 1.0,
        },
        familiar_of={"seed": 0.6, "fav": 1.0},
        window_counts={"seed": {"d7": 2, "d30": 2, "d180": 2, "all": 3}, "heard": {"all": 1}},
        confidence=1.0,
    )


def seed_snapshot():
    return [
        item("seed", ["爵士"], ["甲"], "种子专辑", 2000),
        item("fav", ["爵士"], ["丙"], "收藏专辑", 2000),
        item("near", ["爵士"], ["乙"], "近邻专辑", 2001),
        item("same_artist", ["爵士"], ["甲", "丙"], "同艺人专辑", 2001),
        item("heard", ["爵士"], ["丁"], "听过专辑", 2001),
    ]


def test_seed_recall_is_off_by_default_and_on_recalls_cross_artist_unheard():
    snapshot = seed_snapshot()
    off = build_candidate_pool(snapshot, seed_profile(), today="2026-11-27")
    assert off.seed_scores == {}
    assert off.seeds == ()
    assert all("seed" not in sources for sources in off.sources.values())

    on = build_candidate_pool(
        snapshot,
        seed_profile(),
        config=CandidateConfig(seed_recall_enabled=True),
        today="2026-11-27",
    )
    assert set(on.seeds) == {"seed", "fav"}
    assert "near" in on.seed_scores and on.seed_scores["near"] > 0
    assert "seed" not in on.seed_scores and "fav" not in on.seed_scores
    assert "same_artist" not in on.seed_scores  # 同艺人归探索池
    assert "heard" not in on.seed_scores  # 听过的不算「没听过但像」
    assert "seed" in on.sources["near"]


def test_explore_pool_merges_sources_dedupes_and_caps_per_artist_and_ratio():
    snapshot = [
        item("a1", ["流行"], ["熟悉"]),
        item("a2", ["流行"], ["熟悉"]),
        item("a3", ["流行"], ["熟悉"]),
        item("fav", ["流行"], ["熟悉"]),
        item("cold", ["冷门"], ["冷艺人"]),
        item("new", ["新发行"], ["新艺人"], year=2026, date_created="2026-11-01"),
    ]
    profile = BehaviorProfile(
        familiar_artists=("熟悉",),
        unheard_by_artist={"熟悉": ("a1", "a2", "a3", "fav")},
        familiar_of={"fav": 1.0},
        base_score={
            "a1": 1.0,
            "a2": 0.9,
            "a3": 0.8,
            "cold": 0.7,
            "new": 0.6,
        },
    )

    pool = build_candidate_pool(
        snapshot,
        profile,
        cold_cluster_ids=["cold", "a1"],  # 与熟悉歌手来源重复
        today="2026-11-27",
    )
    assert pool.explore == ("a1", "a2", "cold")  # 3 = round(0.15 × 20)
    assert len(pool.explore) == len(set(pool.explore))
    assert "a3" not in pool.explore  # 每歌手最多 2 首
    assert "fav" not in pool.explore  # 收藏不进探索池

    wider = build_candidate_pool(
        snapshot,
        profile,
        cold_cluster_ids=["cold", "a1"],
        explore_k=5,
        today="2026-11-27",
    )
    assert set(wider.explore) == {"a1", "a2", "cold", "new"}


def test_seed_set_caps_at_20_and_excludes_immersed_blocked_and_penalized():
    snapshot = [item(f"fav{i}", ["流行"], [f"艺人{i}"]) for i in range(25)]
    snapshot += [
        item("imm", ["流行"], ["沉浸艺人"]),
        item("blocked", ["流行"], ["乙"]),
        item("penalized", ["流行"], ["丙"]),
    ]
    profile = BehaviorProfile(
        base_score={row["id"]: float(30 - index) for index, row in enumerate(snapshot)},
        familiar_of={row["id"]: 1.0 for row in snapshot},
        immersions=(
            Immersion(
                artist="沉浸艺人",
                burst_plays=9,
                share=0.8,
                weight=0.9,
                days_since_burst=1,
            ),
        ),
        penalties={"track": {"penalized": 0.8}, "artist": {}, "genre": {}},
        confidence=1.0,
    )
    seeds = select_strong_seeds(
        {row["id"]: row for row in snapshot},
        profile,
        blocked_ids={"blocked"},
    )
    assert len(seeds) == CandidateConfig().max_seeds == 20
    assert "imm" not in seeds and "blocked" not in seeds and "penalized" not in seeds
    assert "fav0" in seeds and "fav24" not in seeds
