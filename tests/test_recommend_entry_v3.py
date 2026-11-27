"""播放器页的今日推荐入口：HTTP 生成接口与页面按钮。"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

from xiaomusic.api.routers import system

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "xiaomusic" / "static" / "default" / "index.html").read_text(
    encoding="utf-8"
)
JS = (ROOT / "xiaomusic" / "static" / "default" / "md.js").read_text(encoding="utf-8")


def request_with_json(body):
    payload = json.dumps(body).encode("utf-8")
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


def install_app(monkeypatch, rec=None, error=None):
    called = []
    monkeypatch.setattr(
        system,
        "log",
        SimpleNamespace(warning=lambda *a, **kw: None, info=lambda *a, **kw: None),
    )

    async def generate(renew=False):
        called.append(renew)
        if error is not None:
            raise error
        return rec

    monkeypatch.setattr(
        system, "xiaomusic", SimpleNamespace(generate_daily_recommend=generate)
    )
    return called


def test_generate_endpoint_returns_summary_without_secrets(monkeypatch):
    rec = SimpleNamespace(
        track_ids=["1", "2", "3"], mode="pr", source="llm",
        fallback_reason="", date="2026-11-28", renewals=0,
    )
    called = install_app(monkeypatch, rec)

    result = asyncio.run(
        system.generate_daily_recommend_endpoint(request_with_json({"renew": True}))
    )

    assert result["ok"] is True and result["count"] == 3
    assert result["mode"] == "pr" and result["source"] == "llm"
    assert result["date"] == "2026-11-28"
    assert called == [True]


def test_generate_endpoint_reports_failure(monkeypatch):
    install_app(monkeypatch, error=RuntimeError("boom"))
    result = asyncio.run(
        system.generate_daily_recommend_endpoint(request_with_json({"renew": False}))
    )
    assert result["ok"] is False and result["reason"] == "RuntimeError"


def test_player_page_has_generate_and_renew_buttons():
    assert 'id="gen_daily_recommend"' in HTML
    assert 'id="renew_daily_recommend"' in HTML
    assert "今日推荐" in HTML
    assert "/xiaoemby/system/generate_daily_recommend" in JS
