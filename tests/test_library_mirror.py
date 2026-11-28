"""本地曲库镜像：纯状态归约 + SQLite 只读查询的单测。"""

from copy import deepcopy

from xiaomusic.utils.library_mirror import (
    STALE_DAYS,
    LibraryMirror,
    MirrorModel,
    is_stale,
    normalize_emby_item,
    source,
)


def make_item(
    item_id,
    name,
    genres=None,
    year=2010,
    artist=("甲",),
    album_artist="甲",
    album="专辑甲",
    date_created="2010-05-01T08:00:00",
    runtime_ticks=2670000000,
    path=None,
    ext="mp3",
):
    return {
        "id": item_id,
        "name": name,
        "genres": list(genres or []),
        "year": year,
        "artist": list(artist),
        "album_artist": album_artist,
        "album": album,
        "date_created": date_created,
        "runtime_ticks": runtime_ticks,
        "path": path or f"/music/{item_id}.{ext}",
        "ext": ext,
        "deleted": False,
    }


def add(state, item):
    return MirrorModel.reduce(state, {"type": "EMBY_ADD", "item": item})


def seeded_state(items):
    state = MirrorModel.init(stale_days=STALE_DAYS)
    for item in items:
        state = add(state, item)
    return MirrorModel.reduce(state, {"type": "FULL_IMPORT"})


def committed(tmp_path, state, name="mirror.sqlite"):
    mirror = LibraryMirror(tmp_path / name)
    mirror.commit(state)
    return mirror


# --- slice 1: 首次全量导入 + 全库快照 + 纯函数 ---


def test_full_import_builds_full_library_snapshot(tmp_path):
    items = [
        make_item("a1", "甲歌", genres=["流行"]),
        make_item("b2", "乙歌", genres=["民谣"], year=2009),
    ]
    state = seeded_state(items)

    assert state["mirror"]["version"] == 1
    assert state["mirror"]["watermark"] == state["emby"]["watermark"]
    assert state["mirror"]["last_sync_day"] == state["day"]

    rows = committed(tmp_path, state).snapshot()
    assert {row["id"] for row in rows} == {"a1", "b2"}
    a1 = next(row for row in rows if row["id"] == "a1")
    assert a1["name"] == "甲歌"
    assert a1["genres"] == ["流行"]
    assert a1["artist"] == ["甲"]
    assert a1["runtime_ticks"] == 2670000000
    assert a1["tombstone"] is False
    assert a1["taste"] == 0.5


def test_reduce_does_not_mutate_input_state():
    state = add(MirrorModel.init(), make_item("a1", "甲歌"))
    before = deepcopy(state)

    after = MirrorModel.reduce(state, {"type": "FULL_IMPORT"})

    assert state == before
    assert after is not state
    assert after["mirror"]["version"] == 1


def test_stale_uses_configured_days():
    base = MirrorModel.init(stale_days=7)
    base = MirrorModel.reduce(base, {"type": "FULL_IMPORT"})
    state = MirrorModel.reduce(base, {"type": "ADVANCE_DAYS", "n": 8})
    assert is_stale(state) is True

    base = MirrorModel.init(stale_days=30)
    base = MirrorModel.reduce(base, {"type": "FULL_IMPORT"})
    state = MirrorModel.reduce(base, {"type": "ADVANCE_DAYS", "n": 8})
    assert is_stale(state) is False


# --- slice 2: 按水位增量 upsert + 用户态按 UserId 分桶 ---


def test_daily_sync_consumes_watermark_and_metadata_edits(tmp_path):
    """镜像只存元数据：日常同步推进水位、消费元数据变更，不写用户态。"""
    state = seeded_state([make_item("a1", "甲歌")])
    state = MirrorModel.reduce(
        state, {"type": "EMBY_EDIT", "id": "a1", "field": "name", "value": "甲歌（新）"}
    )

    state = MirrorModel.reduce(state, {"type": "DAILY_SYNC"})
    assert state["mirror"]["watermark"] == state["emby"]["watermark"]
    assert len(state["changed"]) == 1

    after = committed(tmp_path, state, "after.sqlite")
    row = next(row for row in after.snapshot() if row["id"] == "a1")
    assert row["name"] == "甲歌（新）"
    assert "users" not in row


def test_snapshot_has_no_user_state_fields(tmp_path):
    """静态/动态切分：镜像快照不再带 users 用户态字段。"""
    state = seeded_state([make_item("a1", "甲歌")])
    mirror = committed(tmp_path, state)

    row = next(row for row in mirror.snapshot() if row["id"] == "a1")
    assert "users" not in row
    assert row["name"] == "甲歌"


def test_daily_sync_without_new_changes_keeps_watermark(tmp_path):
    state = seeded_state([make_item("a1", "甲歌")])
    watermark = state["mirror"]["watermark"]

    state = MirrorModel.reduce(state, {"type": "DAILY_SYNC"})

    assert state["mirror"]["watermark"] == watermark
    assert state["changed"] == []


def test_full_import_from_paginated_raw_emby_items(tmp_path):
    # 实测响应：用户态嵌套在 UserData，DateCreated 在顶层，分页 StartIndex/Limit。
    page1 = [
        {
            "Id": "a1",
            "Name": "甲歌",
            "Genres": ["流行"],
            "ProductionYear": 2010,
            "RunTimeTicks": 2670000000,
            "Album": "专辑甲",
            "AlbumArtist": "甲",
            "ArtistItems": [{"Name": "甲", "Id": "ar1"}],
            "Path": "/music/a1.flac",
            "DateCreated": "2026-11-25T03:36:39.0000000Z",
            "UserData": {
                "IsFavorite": True,
                "PlayCount": 12,
                "LastPlayedDate": "2026-11-25T14:04:02.0000000Z",
            },
        }
    ]
    page2 = [
        {
            "Id": "b2",
            "Name": "乙歌",
            "Genres": ["民谣"],
            "ProductionYear": 2009,
            "Album": "专辑乙",
            "DateCreated": "2026-11-20T03:36:39.0000000Z",
            "UserData": {},
        }
    ]

    state = MirrorModel.from_emby_pages([page1, page2], user_id="u-nick")
    mirror = committed(tmp_path, state)

    assert len(mirror.snapshot()) == 2
    a1 = next(row for row in mirror.snapshot() if row["id"] == "a1")
    assert a1["artist"] == ["甲"]
    assert a1["ext"] == "flac"
    assert a1["date_created"] == "2026-11-25T03:36:39.0000000Z"
    assert "users" not in a1
    assert "users" not in normalize_emby_item(page1[0], "u-nick")


# --- slice 3: 每周对账（权威覆盖 + 派生保留）+ 墓碑 + 候选池只读查询 ---


def test_weekly_reconcile_overwrites_authoritative_and_preserves_derived(tmp_path):
    state = seeded_state([make_item("a1", "甲歌", genres=["流行"], year=2010)])
    state = MirrorModel.reduce(
        state, {"type": "LOCAL_EDIT", "id": "a1", "field": "genres", "value": ["古典"]}
    )
    state = MirrorModel.reduce(
        state, {"type": "LOCAL_EDIT", "id": "a1", "field": "taste", "value": 0.9}
    )
    state = MirrorModel.reduce(
        state, {"type": "EMBY_EDIT", "id": "a1", "field": "name", "value": "甲歌（修）"}
    )

    state = MirrorModel.reduce(state, {"type": "WEEKLY_RECONCILE"})
    mirror = committed(tmp_path, state)

    row = next(row for row in mirror.snapshot() if row["id"] == "a1")
    assert row["name"] == "甲歌（修）"
    assert row["genres"] == ["流行"]
    assert row["taste"] == 0.9
    assert row["penalty"] == 0.0
    assert "users" not in row


def test_weekly_reconcile_adds_item_missed_by_incremental(tmp_path):
    state = seeded_state([make_item("a1", "甲歌")])
    state = add(state, make_item("b2", "乙歌", genres=["爵士"]))

    state = MirrorModel.reduce(state, {"type": "WEEKLY_RECONCILE"})

    assert {row["id"] for row in committed(tmp_path, state).snapshot()} == {"a1", "b2"}


def test_emby_delete_tombstones_without_physical_delete(tmp_path):
    state = seeded_state(
        [make_item("a1", "甲歌"), make_item("b2", "乙歌", genres=["爵士"])]
    )
    state = MirrorModel.reduce(state, {"type": "EMBY_DELETE", "id": "b2"})
    state = MirrorModel.reduce(state, {"type": "DAILY_SYNC"})

    mirror = committed(tmp_path, state)
    assert state["mirror"]["items"]["b2"]["tombstone"] is True
    assert {row["id"] for row in mirror.snapshot()} == {"a1"}
    assert {row["id"] for row in mirror.tombstones()} == {"b2"}
    assert {row["id"] for row in mirror.snapshot(include_tombstones=True)} == {
        "a1",
        "b2",
    }
    assert "b2" not in {row["id"] for row in mirror.candidates(kind="all")}

    state = MirrorModel.reduce(state, {"type": "WEEKLY_RECONCILE"})
    assert state["mirror"]["items"]["b2"]["tombstone"] is True


def test_candidates_by_genre_new_and_all(tmp_path):
    state = seeded_state(
        [
            make_item(
                "a1",
                "甲歌",
                genres=["流行"],
                year=2010,
                date_created="2010-05-01T08:00:00",
            ),
            make_item(
                "b2",
                "乙歌",
                genres=["民谣"],
                year=2022,
                date_created="2022-12-01T08:00:00",
            ),
            make_item(
                "c3",
                "丙歌",
                genres=["流行"],
                year=1999,
                date_created="1999-03-01T08:00:00",
            ),
        ]
    )
    mirror = committed(tmp_path, state)

    assert {row["id"] for row in mirror.candidates(kind="all")} == {"a1", "b2", "c3"}
    assert {row["id"] for row in mirror.candidates(kind="genre", genre="流行")} == {
        "a1",
        "c3",
    }
    assert mirror.candidates(kind="new", limit=2)[0]["id"] == "b2"
    assert [row["id"] for row in mirror.candidates(kind="new")] == [
        "b2",
        "a1",
        "c3",
    ]


# --- slice 4: 同步失败保留上一版 + 陈旧回退 v2（不打断出歌单） ---


def test_sync_failure_keeps_previous_version_and_alerts(tmp_path):
    state = seeded_state([make_item("a1", "甲歌")])
    version = state["mirror"]["version"]
    watermark = state["mirror"]["watermark"]
    items = deepcopy(state["mirror"]["items"])

    state = MirrorModel.reduce(
        state, {"type": "EMBY_EDIT", "id": "a1", "field": "name", "value": "甲歌（新）"}
    )
    state = MirrorModel.reduce(state, {"type": "SYNC_FAIL"})

    assert state["mirror"]["version"] == version
    assert state["mirror"]["watermark"] == watermark
    assert state["mirror"]["items"] == items
    assert any(alert.startswith("同步失败") for alert in state["alerts"])
    row = next(row for row in committed(tmp_path, state).snapshot() if row["id"] == "a1")
    assert row["name"] == "甲歌"

    state = MirrorModel.reduce(state, {"type": "DAILY_SYNC"})
    assert not any(alert.startswith("同步失败") for alert in state["alerts"])
    row = next(
        row
        for row in committed(tmp_path, state, "recovered.sqlite").snapshot()
        if row["id"] == "a1"
    )
    assert row["name"] == "甲歌（新）"


def test_query_falls_back_to_v2_when_stale_but_still_produces_playlist():
    state = seeded_state([make_item("a1", "甲歌")])

    state = MirrorModel.reduce(state, {"type": "QUERY"})
    assert state["last_result"]["source"] == "镜像"
    assert source(state) == "镜像"

    state = MirrorModel.reduce(state, {"type": "SYNC_FAIL"})
    state = MirrorModel.reduce(state, {"type": "ADVANCE_DAYS", "n": 8})
    state = MirrorModel.reduce(state, {"type": "QUERY"})

    assert is_stale(state) is True
    assert source(state) == "v2"
    assert state["last_result"]["source"] == "v2"
    assert state["last_result"]["fallback_reason"]
    assert state["last_result"]["candidates"]


def test_version_info_reflects_committed_watermark(tmp_path):
    state = seeded_state([make_item("a1", "甲歌")])
    mirror = committed(tmp_path, state)

    assert mirror.version_info() == {
        "version": 1,
        "watermark": state["mirror"]["watermark"],
        "last_sync_day": 0,
        "stale_days": STALE_DAYS,
    }
