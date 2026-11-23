"""播放今日推荐：后台生成 + 生成中提示 + 重复口令去重的单测。"""

import asyncio
from types import SimpleNamespace

from xiaomusic.xiaomusic import XiaoMusic


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _make_instance(monkeypatch, generate_delay=0.05):
    """绕过 __init__ 的重初始化，只装配 daily_recommend 需要的依赖。"""
    x = XiaoMusic.__new__(XiaoMusic)
    x.config = SimpleNamespace(enable_daily_recommend=True)
    x.log = FakeLogger()
    x._daily_recommend_generating = set()
    x.tts_calls = []
    x.play_calls = []
    x.stop_calls = []
    x.generate_calls = 0
    x.started = asyncio.Event()

    class FakeDevice:
        async def group_force_stop_xiaoai(self):
            x.stop_calls.append("force_stop")

    x.device_manager = SimpleNamespace(devices={"did1": FakeDevice()})

    async def fake_generate(renew=False):
        x.generate_calls += 1
        x.started.set()
        await asyncio.sleep(generate_delay)
        return SimpleNamespace(
            tracks=[object()],
            track_names=["歌 - 专辑 - 歌手"],
            reason="今天的理由",
            segments=[{"theme": "开场", "transition": "熟悉开场"}],
            renewals=0,
            source="llm",
        )

    async def fake_tts(did, value):
        x.tts_calls.append(value)

    async def fake_play_list(did, name, first):
        x.play_calls.append((did, name, first))

    x.generate_daily_recommend = fake_generate
    x.do_tts = fake_tts
    x.do_play_music_list = fake_play_list
    x._register_recommend_tracks = lambda rec: None
    x.music_library = SimpleNamespace(play_list_update_music=lambda *a, **kw: True)
    return x


def test_duplicate_command_generates_once_and_tts_once(monkeypatch):
    """生成期间重复的口令应被忽略，不再触发第二轮生成与播报。"""
    x = _make_instance(monkeypatch, generate_delay=0.05)
    monkeypatch.setattr(
        "xiaomusic.xiaomusic.daily_recommend_module.is_fresh",
        lambda cfg, renew=False: False,
    )

    async def scenario():
        first = asyncio.create_task(x.daily_recommend("did1"))
        await x.started.wait()  # 确保第一个已进入生成阶段
        second = asyncio.create_task(x.daily_recommend("did1"))
        return await asyncio.gather(first, second)

    _, second_result = asyncio.run(scenario())

    assert x.generate_calls == 1
    assert second_result is None
    assert x.tts_calls == ["今日推荐歌单生成中，请稍候", "今天的理由。开场：熟悉开场"]
    assert len(x.play_calls) == 1
    # 重复口令被去重，不应再次触发 force stop
    assert x.stop_calls == ["force_stop"]


def test_cache_hit_skips_generating_prompt(monkeypatch):
    """命中当天缓存时秒回，不应多播一次「生成中」。"""
    x = _make_instance(monkeypatch, generate_delay=0)
    monkeypatch.setattr(
        "xiaomusic.xiaomusic.daily_recommend_module.is_fresh",
        lambda cfg, renew=False: True,
    )

    asyncio.run(x.daily_recommend("did1"))

    assert x.tts_calls == ["今天的理由。开场：熟悉开场"]


def test_guard_released_after_success_and_failure(monkeypatch):
    """无论成功或失败，设备都要从生成中集合移除，避免永久吞掉后续口令。"""
    x = _make_instance(monkeypatch, generate_delay=0)
    monkeypatch.setattr(
        "xiaomusic.xiaomusic.daily_recommend_module.is_fresh",
        lambda cfg, renew=False: False,
    )

    asyncio.run(x.daily_recommend("did1"))
    assert x._daily_recommend_generating == set()
    asyncio.run(x.daily_recommend("did1"))
    assert x.generate_calls == 2

    async def boom(renew=False):
        raise RuntimeError("boom")

    x.generate_daily_recommend = boom
    asyncio.run(x.daily_recommend("did1"))
    assert x._daily_recommend_generating == set()
    assert x.tts_calls[-1] == "今日推荐生成失败，请稍后再试"
