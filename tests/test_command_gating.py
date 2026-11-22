"""测试命令白名单门禁：今日推荐命令在旧 active_cmd 配置下仍应可用。"""

from types import SimpleNamespace

from xiaomusic.command_handler import CommandHandler
from xiaomusic.config import Config


def make_handler(active_cmd: str):
    config = Config()
    config.active_cmd = active_cmd
    config.init()
    log = SimpleNamespace(
        info=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
        exception=lambda *a, **k: None,
    )
    return CommandHandler(config=config, log=log, xiaomusic_instance=SimpleNamespace())


# 旧版 setting.json 的 active_cmd（不含今日推荐命令）
OLD_ACTIVE_CMD = (
    "play,play_music_list,play_music_list_index,stop_after_minute,stop,"
    "play_next,play_prev,gen_music_list,set_pull_ask_off,add_to_favorites,"
    "del_from_favorites,online_play,singer_play"
)


def test_daily_recommend_matches_with_old_active_cmd():
    """设备空闲 + 旧白名单下，播放今日推荐不应被降级成普通播放。"""
    handler = make_handler(OLD_ACTIVE_CMD)
    device = SimpleNamespace(is_playing=False)
    action, _, groups = handler.match_cmd(device, "播放今日推荐", ctrl_panel=False)
    assert action == "daily_recommend"
    assert groups == {}


def test_renew_and_snooze_match_with_old_active_cmd():
    handler = make_handler(OLD_ACTIVE_CMD)
    device = SimpleNamespace(is_playing=False)
    assert handler.match_cmd(device, "换一批今日推荐", False)[0] == "daily_recommend_renew"
    assert handler.match_cmd(device, "不喜欢这首歌", False)[0] == "snooze_current"


def test_normal_play_command_still_gated():
    """普通播放命令在白名单缺失时仍应被拦下（门禁没被整体削弱）。"""
    handler = make_handler("stop,play_next")  # 不含 play
    device = SimpleNamespace(is_playing=False)
    # "播放周杰伦" 匹配 play，但不在白名单 → 被拦，返回 None
    assert handler.match_cmd(device, "播放周杰伦", False)[0] is None
