"""前端短路由：播放器 / 设置页，以及静态资源的 base 注入。"""

import asyncio

from xiaomusic.api.routers import system


def test_player_short_route_serves_player_with_base():
    response = asyncio.run(system.player_page())
    html = response.body.decode("utf-8")
    assert response.status_code == 200
    assert '<base href="/static/default/">' in html
    assert "小爱音箱播放器" in html


def test_settings_short_route_serves_settings_with_base():
    response = asyncio.run(system.settings_page())
    html = response.body.decode("utf-8")
    assert response.status_code == 200
    assert '<base href="/static/default/">' in html
    assert "AI 今日推荐" in html
