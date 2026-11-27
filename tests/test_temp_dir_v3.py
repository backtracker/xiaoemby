"""临时目录并入缓存目录：清理时不得波及镜像 / 推荐缓存。"""

import asyncio
from pathlib import Path

from xiaomusic.config import Config
from xiaomusic.crontab import clean_temp_dir


def test_temp_dir_is_under_cache_dir(tmp_path):
    cache = tmp_path / "cache"
    cfg = Config(cache_dir=str(cache))
    assert cfg.temp_dir == str(cache / "tmp")
    assert (cache / "tmp").is_dir()


def test_clean_temp_keeps_other_cache_files(tmp_path):
    cfg = Config(cache_dir=str(tmp_path / "cache"))
    tmp = Path(cfg.temp_dir)
    (tmp / "voice.mp3").write_bytes(b"x")
    sentinel = tmp.parent / "daily_recommend.json"
    sentinel.write_text("keep", encoding="utf-8")

    asyncio.run(clean_temp_dir(cfg))

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (tmp / "voice.mp3").exists()
    assert (tmp_path / "cache" / "tmp").is_dir()
