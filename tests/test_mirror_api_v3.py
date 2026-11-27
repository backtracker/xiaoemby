"""本地曲库镜像用户入口的公开缝：配置加载、设置接口脱敏、同步与状态接口。

测试只调用路由函数与配置公共 API；同步函数用替身，不触网、不读真实凭据。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from xiaomusic.api.routers import system
from xiaomusic.config import Config
from xiaomusic.utils.mirror_sync import sync_mirror as real_sync_mirror

PLACEHOLDER = "unit-test-placeholder"  # 合成占位符，不是真实凭据


def fake_logger(messages=None):
    class Log:
        def debug(self, msg, *args, **kwargs):
            pass

        def info(self, msg, *args, **kwargs):
            if messages is not None:
                messages.append(str(msg))

        def warning(self, msg, *args, **kwargs):
            if messages is not None:
                messages.append(str(msg))

        def error(self, msg, *args, **kwargs):
            if messages is not None:
                messages.append(str(msg))

        def exception(self, msg, *args, **kwargs):
            if messages is not None:
                messages.append(str(msg))

    return Log()


def request_with_json(body=None):
    payload = json.dumps(body if body is not None else {}).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "headers": [(b"content-type", b"application/json")]},
        receive,
    )


def test_config_loads_emby_api_key_from_setting_file(tmp_path):
    path = tmp_path / "setting.json"
    path.write_text(
        json.dumps(
            {
                "emby_api_key": PLACEHOLDER,
                "mirror_db_path": "conf/library_mirror.sqlite3",
            }
        ),
        encoding="utf-8",
    )
    loaded = Config.read_from_file(str(path))
    assert loaded["emby_api_key"] == PLACEHOLDER
    assert Config(**loaded).emby_api_key == PLACEHOLDER


def test_empty_mirror_path_is_refilled_on_config_and_save():
    assert Config(mirror_db_path="").mirror_db_path == "conf/library_mirror.sqlite3"
    cfg = Config()
    cfg.update_config({"mirror_db_path": ""})
    assert cfg.mirror_db_path == "conf/library_mirror.sqlite3"


def test_getsetting_masks_emby_api_key(monkeypatch):
    cfg = Config(emby_api_key=PLACEHOLDER)
    monkeypatch.setattr(system, "xiaomusic", SimpleNamespace(getconfig=lambda: cfg))

    data = asyncio.run(system.getsetting())

    assert data["emby_api_key"] == "******"
    assert PLACEHOLDER not in json.dumps(data)


def test_savesetting_preserves_masked_or_empty_api_key_and_logs_safely(monkeypatch):
    cfg = Config(emby_api_key=PLACEHOLDER)
    captured = []
    messages = []

    async def saveconfig(data):
        captured.append(dict(data))

    monkeypatch.setattr(
        system,
        "xiaomusic",
        SimpleNamespace(getconfig=lambda: cfg, saveconfig=saveconfig),
    )
    monkeypatch.setattr(system, "log", fake_logger(messages))

    for sent in ("******", ""):
        asyncio.run(
            system.savesetting(
                request_with_json({"emby_api_key": sent, "daily_recommend_count": 20})
            )
        )
        assert captured[-1]["emby_api_key"] == PLACEHOLDER

    assert PLACEHOLDER not in "".join(messages)


def test_modifiysetting_preserves_masked_or_empty_api_key(monkeypatch):
    cfg = Config(emby_api_key=PLACEHOLDER)
    monkeypatch.setattr(
        system,
        "xiaomusic",
        SimpleNamespace(getconfig=lambda: cfg, save_cur_config=lambda: None),
    )
    monkeypatch.setattr(system, "log", fake_logger())

    for sent in ("******", ""):
        asyncio.run(system.modifiysetting(request_with_json({"emby_api_key": sent})))
        assert cfg.emby_api_key == PLACEHOLDER


class FakeSync:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, cfg, *, token=None, kind=None, progress=None):
        self.calls.append((cfg, token, kind))
        if progress is not None:
            progress({"phase": "fetch", "page": 1, "pages": 2, "items": 3, "total": 6})
        if isinstance(self.result, Exception):
            raise self.result
        return dict(self.result)


def mirror_result(**overrides):
    result = {
        "ok": True,
        "skipped": False,
        "kind": "full",
        "version": 3,
        "items": 72577,
        "changed": 9,
        "alerts": [],
        "host": "private-host.invalid",
        "token": PLACEHOLDER,
        "path": "/private/mirror.sqlite3",
    }
    result.update(overrides)
    return result


def install_app(monkeypatch, cfg, *, client_token=None):
    emby_util = None
    if client_token is not None:
        emby_util = SimpleNamespace(get_access_token=lambda: client_token)
    monkeypatch.setattr(
        system,
        "xiaomusic",
        SimpleNamespace(getconfig=lambda: cfg, emby_util=emby_util),
    )
    monkeypatch.setattr(system, "log", fake_logger())


def test_sync_mirror_route_returns_compact_summary_without_secrets(monkeypatch):
    cfg = Config(mirror_db_path="conf/library_mirror.sqlite3", emby_api_key=PLACEHOLDER)
    install_app(monkeypatch, cfg)
    fake = FakeSync(mirror_result())
    monkeypatch.setattr(system, "sync_mirror", fake)

    result = asyncio.run(system.sync_mirror_now(request_with_json({"kind": "full"})))

    assert set(result) == {"ok", "skipped", "reason", "kind", "version", "tracks", "alerts"}
    assert result["ok"] is True and result["version"] == 3
    assert result["tracks"] == 72577 and result["kind"] == "full"
    assert PLACEHOLDER not in json.dumps(result)
    assert "private-host.invalid" not in json.dumps(result)
    assert "/private/mirror.sqlite3" not in json.dumps(result)
    assert fake.calls == [(cfg, PLACEHOLDER, "full")]


def test_sync_progress_endpoint_reports_latest_fetch_and_finishes(monkeypatch):
    cfg = Config(mirror_db_path="conf/library_mirror.sqlite3")
    install_app(monkeypatch, cfg)
    monkeypatch.setattr(system, "sync_mirror", FakeSync(mirror_result()))

    asyncio.run(system.sync_mirror_now(request_with_json({"kind": "full"})))
    progress = asyncio.run(system.mirror_sync_progress())

    assert progress["running"] is False
    assert progress["phase"] == "done"
    assert progress["page"] == 1 and progress["pages"] == 2
    assert progress["items"] == 3 and progress["total"] == 6
    assert PLACEHOLDER not in json.dumps(progress)


def test_sync_mirror_route_defaults_to_auto_and_reports_resolved_kind(monkeypatch):
    cfg = Config(mirror_db_path="conf/library_mirror.sqlite3")
    install_app(monkeypatch, cfg)
    fake = FakeSync(mirror_result(ok=False, skipped=True, reason="unconfigured", kind="weekly"))
    monkeypatch.setattr(system, "sync_mirror", fake)

    result = asyncio.run(system.sync_mirror_now(request_with_json()))

    assert result["skipped"] is True and result["reason"] == "unconfigured"
    assert result["kind"] == "weekly"
    assert fake.calls[0][2] == "auto"


def test_sync_mirror_route_falls_back_to_running_emby_client_token(monkeypatch):
    cfg = Config(mirror_db_path="conf/library_mirror.sqlite3")
    install_app(monkeypatch, cfg, client_token=PLACEHOLDER)
    fake = FakeSync(mirror_result())
    monkeypatch.setattr(system, "sync_mirror", fake)

    asyncio.run(system.sync_mirror_now(request_with_json({"kind": "daily"})))

    assert fake.calls[0][1] == PLACEHOLDER
    assert fake.calls[0][2] == "daily"


def test_sync_mirror_route_rejects_unknown_kind(monkeypatch):
    cfg = Config(mirror_db_path="conf/library_mirror.sqlite3")
    install_app(monkeypatch, cfg)
    fake = FakeSync(mirror_result())
    monkeypatch.setattr(system, "sync_mirror", fake)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(system.sync_mirror_now(request_with_json({"kind": "drop database"})))

    assert exc.value.status_code == 400
    assert fake.calls == []


def test_sync_mirror_route_structures_failures_without_leaking_details(monkeypatch):
    cfg = Config(mirror_db_path="conf/library_mirror.sqlite3", emby_api_key=PLACEHOLDER)
    install_app(monkeypatch, cfg)
    boom = RuntimeError(f"connect to {PLACEHOLDER} failed")
    monkeypatch.setattr(system, "sync_mirror", FakeSync(boom))

    result = asyncio.run(system.sync_mirror_now(request_with_json({"kind": "weekly"})))

    assert result["ok"] is False and result["reason"] == "RuntimeError"
    assert result["alerts"] == []
    assert PLACEHOLDER not in json.dumps(result)


class OneItemClient:
    def get_json(self, path, params):
        return {
            "Items": [
                {
                    "Id": "1",
                    "Name": "歌1",
                    "Genres": ["流行"],
                    "ProductionYear": 2000,
                    "ArtistItems": [{"Name": "甲"}],
                    "AlbumArtist": "甲",
                    "Album": "专1",
                    "DateCreated": "2020-01-01T00:00:00Z",
                    "RunTimeTicks": 2000000000,
                    "Path": "/music/1.flac",
                    "UserData": {"IsFavorite": False, "PlayCount": 0, "LastPlayedDate": None},
                }
            ],
            "TotalRecordCount": 1,
        }


def test_mirror_status_reports_version_count_and_last_sync_without_secrets(tmp_path, monkeypatch):
    path = str(tmp_path / "mirror.sqlite3")
    cfg = Config(mirror_db_path=path, emby_api_key=PLACEHOLDER)
    install_app(monkeypatch, cfg)

    cfg.mirror_db_path = ""
    empty = asyncio.run(system.mirror_status())
    assert empty["configured"] is False and empty["version"] == 0
    assert empty["tracks"] == 0 and empty["last_sync"] is None
    assert PLACEHOLDER not in json.dumps(empty)

    cfg.mirror_db_path = path
    real_sync_mirror(cfg, client=OneItemClient(), today="2026-11-27")
    status = asyncio.run(system.mirror_status())

    assert status["configured"] is True
    assert status["version"] == 1 and status["tracks"] == 1
    assert status["last_sync"] == "2026-11-27"
    assert PLACEHOLDER not in json.dumps(status)
    assert path not in json.dumps(status)
