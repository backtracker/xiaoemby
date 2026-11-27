"""e2e_v3.py 纯辅助函数的离线断言（真实网络调用只由脚本 main 负责）。"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import e2e_v3  # noqa: E402

from xiaomusic.config import Config  # noqa: E402
from xiaomusic.utils.pr_events import LOCAL_TZ, PREvent  # noqa: E402


def test_make_config_skips_unknown_keys_and_overrides_paths(tmp_path):
    settings = {
        "emby_host": "https://example.invalid",
        "emby_user_id": "uid-1",
        "daily_recommend_count": 7,
        "this_key_does_not_exist": 1,
        "devices": {"a": {"did": "1"}},
    }
    cfg = e2e_v3.make_config(
        settings,
        mirror_db_path=str(tmp_path / "mirror.db"),
        cache_dir=str(tmp_path / "cache"),
    )
    assert cfg.emby_host == "https://example.invalid"
    assert cfg.emby_user_id == "uid-1"
    assert cfg.daily_recommend_count == 7
    assert cfg.mirror_db_path == str(tmp_path / "mirror.db")
    assert cfg.cache_dir == str(tmp_path / "cache")
    assert not hasattr(cfg, "this_key_does_not_exist")
    assert cfg.devices == {}


def test_import_mirror_via_api_calls_full_sync_route(monkeypatch):
    captured = {}

    async def fake_route(request):
        body = json.loads((await request.body()).decode("utf-8"))
        captured["kind"] = body["kind"]
        return {"ok": True, "kind": "full", "version": 1, "tracks": 7}

    monkeypatch.setattr(e2e_v3.system_router, "sync_mirror_now", fake_route)
    result = e2e_v3.import_mirror_via_api(
        Config(), SimpleNamespace(get=lambda: None, verify=False)
    )
    assert result["tracks"] == 7
    assert captured["kind"] == "full"


def test_synthetic_events_span_over_week_and_are_valid():
    base = PREvent(
        user_id="uid-1",
        item_id="1",
        device_name="dev",
        started_at=datetime(2026, 11, 27, 10, tzinfo=LOCAL_TZ),
        duration_seconds=30,
        completion_rate=None,
        is_valid=False,
        is_extremely_short=True,
        is_incomplete=True,
        segment_count=1,
    )
    synthetic = e2e_v3.make_synthetic_events([base], {"1"}, count=25, days=14)
    assert len(synthetic) >= 20
    assert all(ev.is_valid and not ev.is_extremely_short for ev in synthetic)
    span = (max(ev.started_at for ev in synthetic) - min(ev.started_at for ev in synthetic)).days
    assert span >= 7
    assert {ev.item_id for ev in synthetic} == {"1"}


def test_front_five_check_reports_violations():
    rows = {
        str(i): {
            "id": str(i),
            "genres": ["吉他流行"] if i < 4 else ["古典"],
            "artist": [f"歌手{i}"],
            "year": 2020,
        }
        for i in range(5)
    }
    check = e2e_v3.front_five_check([str(i) for i in range(5)], rows)
    assert check["genre_cap_ok"] is False
    assert check["count_ok"] is True
    for index in (2, 3):
        rows[str(index)]["genres"] = ["古典"]
    rows["4"]["genres"] = ["爵士"]
    check = e2e_v3.front_five_check([str(i) for i in range(5)], rows)
    assert check["genre_cap_ok"] is True
