"""Emby 音频流 URL 构建工具。"""

# 需要 Static=true 直出原文件的容器。
# Emby 对 m4a/mp4/aac 转封装后会生成 moov 在尾部的非 faststart 文件，小爱音箱无法解码，
# 因此这些格式必须直出原文件；其余格式交给 Emby 转封装，转封装会顺带去掉内嵌封面/ID3，
# 避免音箱在解析原始文件里的封面（如 FLAC 的 PNG 封面、MP3 的 APIC）时无声。
STATIC_CONTAINERS = {"m4a", "mp4", "aac", "m4b"}


def build_emby_stream_url(host: str, api_key: str, audio_id: str, container: str) -> str:
    """构建 Emby 音频流地址。

    仅对 remux 会破坏文件结构（非 faststart）的容器追加 `Static=true` 直出原文件，
    其余容器保持 Emby 默认的转封装行为，以便去掉内嵌封面等音箱无法解析的内容。
    """
    static_param = "&Static=true" if container.lower() in STATIC_CONTAINERS else ""
    return (
        f"{host}/emby/Audio/{audio_id}/stream"
        f"?Container={container}&api_key={api_key}{static_param}"
    )
