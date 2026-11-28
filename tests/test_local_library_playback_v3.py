"""本地曲库镜像直接支撑启动取曲与前端列表，不依赖 Emby 搜索接口。"""

from types import SimpleNamespace

from xiaomusic.config import Config
from xiaomusic.music_library import MusicLibrary
from xiaomusic.utils.library_mirror import LibraryMirror, MirrorModel

PLACEHOLDER = "unit-test-placeholder"


def fake_log():
    silent = lambda *a, **kw: None
    return SimpleNamespace(info=silent, debug=silent, warning=silent, exception=silent, error=silent)


def raw_item(item_id):
    return {
        "Id": str(item_id),
        "Name": f"歌{item_id}",
        "Genres": ["Mandopop"],
        "ProductionYear": 2020,
        "ArtistItems": [{"Name": "甲"}],
        "AlbumArtist": "甲",
        "Album": "专辑",
        "RunTimeTicks": 200 * 10_000_000,
        "DateCreated": "2026-01-01T00:00:00.0000000Z",
        "Path": f"/music/{item_id}.mp3",
        "UserData": {
            "IsFavorite": False,
            "PlayCount": 1,
            "LastPlayedDate": None,
        },
    }


def test_startup_loads_full_library_from_mirror(tmp_path):
    state = MirrorModel.from_emby_pages([[raw_item(i) for i in range(1, 4)]], "u1")
    db = LibraryMirror(str(tmp_path / "library_mirror.sqlite3"))
    db.commit(state)
    db.close()

    cfg = Config(
        cache_dir=str(tmp_path),
        emby_host="https://example.invalid",
        emby_api_key=PLACEHOLDER,
        emby_user_id="u1",
    )
    lib = MusicLibrary(cfg, fake_log(), None, None)
    lib.gen_all_music_list()

    assert lib._mirror_loaded is True
    assert len(lib.all_music) == 3
    assert len(lib.music_list["全部"]) == 3
    audio = next(iter(lib.all_music.values()))
    assert audio.stream_url.startswith("https://example.invalid/emby/Audio/")
    assert PLACEHOLDER in audio.stream_url
    assert audio.container == "mp3"


def test_mirror_stream_url_keeps_playback_on_emby(tmp_path):
    state = MirrorModel.from_emby_pages([[raw_item(7)]], "u1")
    db = LibraryMirror(str(tmp_path / "library_mirror.sqlite3"))
    db.commit(state)
    db.close()

    cfg = Config(cache_dir=str(tmp_path), emby_host="https://example.invalid", emby_api_key=PLACEHOLDER)
    lib = MusicLibrary(cfg, fake_log(), None, None)
    lib.gen_all_music_list()
    audio = next(iter(lib.all_music.values()))
    assert "/emby/Audio/7/stream" in audio.stream_url
    assert audio.file_path == "/music/7.mp3"
