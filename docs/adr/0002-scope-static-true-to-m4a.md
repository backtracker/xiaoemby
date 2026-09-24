# ADR-0002: `Static=true` 只对 m4a/mp4/aac/m4b 生效

## 状态

Accepted

## 背景

[ADR-0001](0001-emby-audio-proxy-static-true.md) 为了让 m4a 直出 faststart 原文件，给所有格式的 Emby 音频流都加了 `Static=true`。上线当晚（2026-09-23）又出现两首歌无声：

- `If Ever`（Emby item 966482，mp3）：22:00:13–22:00:28 音箱对代理发起了 **294 次**请求（Emby 侧 292 次 200），典型的解码失败重试风暴。
- `L'Ete'S'Approche (夏天到了) [Live]`（Emby item 1022816，flac）：22:10:19 下发后 4 秒用户就"下一首"。代理到 Emby 的连接一直挂到 01:00:02 应用重启才断开（`Response completed after client disconnected. Time: 10182402ms`），说明音箱读了一小段就不再消费。

### 根因

`Static=true` 让 Emby **原样直出文件**，文件里内嵌的封面也随之进入音箱解码器：

| Item | 格式 | `Static=true`（原始文件） | Emby 转封装（去掉封面） |
| --- | --- | --- | --- |
| 966482 | mp3 | ID3v2.4，含 APIC 封面（~184KB） | 仅剩文本帧，无 APIC |
| 1022816 | flac | PICTURE 块为 **PNG**（1.77MB） | 无 PICTURE 块 |
| 1036938 等（正常） | flac | PICTURE 块为 JPEG | 无 PICTURE 块 |

对比同一晚播放正常的 flac：它们的内嵌封面都是 **JPEG**，而失败的那首是 **PNG**；mp3 则带有音箱无法处理的 APIC 封面。小爱音箱（L17A）的播放器在解析这些内嵌图片时会失败，表现为无声/重试。

历史 Emby 日志也支持这个结论：v0.0.15 之前每天每首歌只有 1–2 次 `Audio/*/stream` 请求，没有任何重试风暴；v0.0.15 上线后同一首歌瞬间 592 次。

## 决策

只对 **remux 会破坏文件结构** 的容器追加 `Static=true`：

```python
STATIC_CONTAINERS = {"m4a", "mp4", "aac", "m4b"}
```

其余格式（flac、mp3 等）保持 Emby 默认的转封装，让 Emby 顺带去掉内嵌封面/ID3。

同时：

- 代理流在生成器 `finally` 中关闭上游 `requests` 响应，避免客户端断开后连接长时间挂起（本次观察到 2.8 小时泄漏）。
- `Content-Type` 透传保留（ADR-0001 的修复）。

## 不做的

- **不做按文件探测封面格式再决定**：实现复杂，且 Emby 转封装已经免费解决，无需在代理里解容器。
- **不改成异步 httpx/aiohttp**：仍属已知技术债，但本次无声问题的直接原因是 `Static=true` 范围过大，先做最小修复。
- **不移除代理层**：音箱只认简单 URL，代理层仍然必要。

## 后果

- m4a/AAC 直出原文件（faststart）→ 正常播放
- flac/mp3 走 Emby 转封装 → 去掉内嵌封面 → 正常播放
- 代价：flac/mp3 响应为 chunked、无 Content-Length（与 v0.0.15 之前一致，音箱可顺序播放）
- 上游连接在客户端断开时会被及时关闭
