from xiaomusic.utils.stream_utils import build_emby_stream_url

HOST = "http://192.168.31.202:8096"
KEY = "test_api_key"


def _url(container):
    return build_emby_stream_url(HOST, KEY, "966482", container)


def test_m4a_uses_static():
    url = _url("m4a")
    assert "Static=true" in url
    assert "Container=m4a" in url
    assert "api_key=test_api_key" in url


def test_aac_family_uses_static():
    for container in ("mp4", "aac", "m4b"):
        assert "Static=true" in _url(container), container


def test_flac_does_not_use_static():
    # 转封装会去掉内嵌封面，避免音箱解析 PNG 封面时无声
    assert "Static=true" not in _url("flac")


def test_mp3_does_not_use_static():
    # 转封装会去掉 ID3/APIC 封面
    assert "Static=true" not in _url("mp3")


def test_container_case_insensitive():
    assert "Static=true" in _url("M4A")
    assert "Static=true" not in _url("FLAC")


def test_url_shape_is_stable():
    assert _url("flac") == (
        "http://192.168.31.202:8096/emby/Audio/966482/stream"
        "?Container=flac&api_key=test_api_key"
    )
