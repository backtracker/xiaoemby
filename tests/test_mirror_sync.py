"""镜像运行时同步：只通过公开同步入口与落库后的只读查询断言行为。"""

from __future__ import annotations

from types import SimpleNamespace

import requests

from xiaomusic.utils.library_mirror import LibraryMirror
from xiaomusic.utils.mirror_sync import HttpMirrorClient, fetch_library, sync_mirror


class FakeMirrorClient:
    """只读客户端替身：只有 get_json，没有任何写 Emby 的方法。"""

    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = 0
        self.fail = False

    def get_json(self, path, params):
        self.calls += 1
        if self.fail:
            raise RuntimeError("emby down")
        start = int(params.get("StartIndex", 0) or 0)
        limit = int(params.get("Limit", 500) or 500)
        return {
            "Items": self.rows[start : start + limit],
            "TotalRecordCount": len(self.rows),
        }


def raw(item_id, *, artist="甲", genre="Mandopop", favorite=False, play_count=0):
    return {
        "Id": str(item_id),
        "Name": f"歌{item_id}",
        "Genres": [genre],
        "ProductionYear": 2000,
        "ArtistItems": [{"Name": artist}],
        "AlbumArtist": artist,
        "Album": f"专{item_id}",
        "DateCreated": "2020-01-01T00:00:00Z",
        "RunTimeTicks": 200 * 10_000_000,
        "Path": f"/music/{item_id}.flac",
        "UserData": {
            "IsFavorite": favorite,
            "PlayCount": play_count,
            "LastPlayedDate": None,
        },
    }


def config(tmp_path, **overrides):
    values = {
        "mirror_db_path": str(tmp_path / "mirror.db"),
        "mirror_stale_days": 7,
        "emby_user_id": "u1",
        "emby_host": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_self_signed_certificate_is_retried_without_verification():
    class SSLFirstTransport:
        def __init__(self):
            self.verify_args = []

        def get(self, url, **kwargs):
            self.verify_args.append(kwargs.get("verify", "<default>"))
            if len(self.verify_args) == 1:
                raise requests.exceptions.SSLError("self signed")
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"Items": [], "TotalRecordCount": 0},
                raise_for_status=lambda: None,
            )

    transport = SSLFirstTransport()
    result = HttpMirrorClient(
        "https://example.invalid", "placeholder", transport=transport
    ).get_json("/emby/Users/u/Items", {})
    assert result["TotalRecordCount"] == 0
    assert transport.verify_args == ["<default>", False]


def test_fetch_library_reports_paged_progress():
    seen = []
    client = FakeMirrorClient([raw(i) for i in range(1200)])
    fetch_library(client, "u1", page_size=500, progress=seen.append)
    assert [s["phase"] for s in seen] == ["fetch", "fetch", "fetch"]
    assert [s["page"] for s in seen] == [1, 2, 3]
    assert seen[-1]["pages"] == 3 and seen[-1]["total"] == 1200
    assert seen[-1]["items"] == 1200


def test_sync_is_side_effect_free_when_unconfigured(tmp_path):
    client = FakeMirrorClient([raw(1)])
    result = sync_mirror(config(tmp_path, mirror_db_path=""), client)
    assert result["skipped"] is True
    assert result["ok"] is False
    assert client.calls == 0
    assert list(tmp_path.iterdir()) == []


def test_full_import_then_daily_incremental_and_weekly_reconcile(tmp_path):
    client = FakeMirrorClient([raw(1), raw(2), raw(3)])
    cfg = config(tmp_path)

    first = sync_mirror(cfg, client)
    assert first["ok"] is True
    assert first["kind"] == "full"
    mirror = LibraryMirror(cfg.mirror_db_path)
    try:
        assert len(mirror.snapshot()) == 3
        assert mirror.version_info()["version"] == 1

        # 用户态变化 + 新增 + 删除：日常同步只推进水位，不动派生字段。
        client.rows = [raw(1, favorite=True, play_count=4), raw(2), raw(4)]
        daily = sync_mirror(cfg, client)
        assert daily["ok"] is True
        assert daily["kind"] == "daily"
        rows = {row["id"]: row for row in mirror.snapshot(include_tombstones=True)}
        assert mirror.user_state("1", "u1")["favorite"] is True
        assert rows["1"]["taste"] == 0.5  # 派生字段未被覆盖
        assert "2" in rows and "3" in rows and rows["3"]["tombstone"] is True
        assert "3" not in {row["id"] for row in mirror.snapshot()}
        assert "4" in rows
        assert mirror.version_info()["version"] == 2

        weekly = sync_mirror(cfg, client, kind="weekly")
        assert weekly["ok"] is True
        assert weekly["kind"] == "weekly"
        assert mirror.version_info()["version"] == 3
    finally:
        mirror.close()


def test_sync_failure_keeps_previous_version_and_reports_alert(tmp_path, caplog):
    client = FakeMirrorClient([raw(1), raw(2)])
    cfg = config(tmp_path)
    assert sync_mirror(cfg, client)["ok"] is True
    client.fail = True

    with caplog.at_level("WARNING", logger="xiaomusic"):
        failed = sync_mirror(cfg, client)

    assert failed["ok"] is False
    assert failed["alerts"]
    assert "同步失败" in failed["alerts"][-1]
    mirror = LibraryMirror(cfg.mirror_db_path)
    try:
        assert mirror.version_info()["version"] == 1
        assert len(mirror.snapshot()) == 2
    finally:
        mirror.close()
    assert "同步失败" in caplog.text


def test_crontab_wires_mirror_jobs_only_when_configured(monkeypatch):
    from xiaomusic.crontab import Crontab

    class FakeLog:
        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

        def exception(self, *args, **kwargs):
            pass

    cron = Crontab(FakeLog())
    calls = []
    monkeypatch.setattr(
        cron, "add_job_mirror_sync", lambda *a, **kw: calls.append((a, kw))
    )
    app = SimpleNamespace(
        config=SimpleNamespace(
            crontab_json="", mirror_db_path="", enable_auto_clean_temp=False
        )
    )
    cron.reload_config(app)
    assert calls == []

    app.config.mirror_db_path = "mirror.db"
    cron.reload_config(app)
    assert [kw.get("arg1") for _a, kw in calls] == ["daily", "weekly"]
