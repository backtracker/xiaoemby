# ADR-0001: Emby 音频代理加 Static=true 直出原文件

## 状态

Accepted

## 背景

小爱音箱（L17A）播放 m4a/AAC 格式音乐时出现"命令下发成功、文件也下载了（200），但解码不出声音"的问题。flac 格式则正常播放。

### 根因

`proxy_emby_audio` 代理请求 Emby 时，URL 未带 `Static=true` 参数。Emby 认为需要转封装（remux），用 ffmpeg 生成临时 m4a 文件，产物的 `moov` atom 在文件尾部（非 faststart）。代理又不支持 Range 请求、不返回 Content-Length。小爱音箱做顺序 HTTP 播放，拿不到索引/不能 seek，于是无声。

### 证据链

- 原文件是 faststart（moov 在前），Emby remux 后 moov 在尾
- 代理响应头 `transfer-encoding: chunked`，无 Content-Length
- 加 `Static=true` 直出原文件，响应头正常（Content-Length、Accept-Ranges: bytes、moov 在前）
- flac 格式 remux 后仍是流式友好的，所以 flac 正常

### 约束

小爱音箱只认简单 URL 格式（如 `xxx/audio_id.m4a`），不认带 query string 的 URL。因此代理层不能移除——即使 Emby 支持 `Static=true` 直出，URL 仍带 `?Container=...&api_key=...&Static=true` 等参数，音箱无法处理。代理的职责是将复杂 Emby URL 简化为音箱可识别的格式。

## 决策

在 `proxy_emby_audio` 代理函数中：

1. **构造 Emby URL 时加 `&Static=true`**：让 Emby 直出原文件，避免 remux 产生 moov 在尾部的临时文件。
2. **Content-Type 从硬编码改为透传 Emby 的响应头**：修复 `audio/m4a`（错误）应为 `audio/mp4`（正确）的问题。

### 不做的

- **不加 Range 透传**：`Static=true` 后 Emby 直出 faststart 原文件，moov 在头部，音箱顺序读即可解码。Range 支持非必需，本次聚焦无声问题。
- **不换 httpx.AsyncClient**：当前 `requests.get` 的同步阻塞在单音箱场景影响可忽略，不属于无声问题的范畴。
- **不改 `__set_audio_stream_url`**：代理层加 `Static=true` 已足够解决问题，无需双改。
- **不移除代理层**：音箱不认 query string URL，代理层是必要的。

## 后果

- m4a/AAC 格式音乐恢复正常播放
- Content-Type 头正确反映文件 MIME 类型
- 代理仍使用同步 `requests` + `StreamingResponse`（已知技术债，后续可优化）
