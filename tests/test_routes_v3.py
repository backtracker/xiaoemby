"""前端短路由：播放器 / 设置页，以及静态资源的 base 注入。"""

import asyncio

from xiaomusic import __version__
from xiaomusic.api.app import legacy_player_path, legacy_settings_path
from xiaomusic.api.routers import system


def test_player_short_route_serves_player_with_base():
    response = asyncio.run(system.player_page())
    html = response.body.decode("utf-8")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-cache"
    assert '<base href="/static/default/">' in html
    assert "XiaoEmby" in html and "小爱音箱播放器" in html
    assert f"?v={__version__}" in html
    assert "?version=" not in html


def test_legacy_static_paths_redirect_to_short_routes():
    assert asyncio.run(legacy_player_path()).headers["location"] == "/player"
    assert asyncio.run(legacy_settings_path()).headers["location"] == "/settings"


def test_settings_short_route_serves_settings_with_base():
    response = asyncio.run(system.settings_page())
    html = response.body.decode("utf-8")
    assert response.status_code == 200
    assert '<base href="/static/default/">' in html
    assert "AI 今日推荐" in html
