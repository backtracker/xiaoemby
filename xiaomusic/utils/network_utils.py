#!/usr/bin/env python3
"""网络请求和下载相关工具函数"""

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import edge_tts

log = logging.getLogger(__package__)


async def text_to_mp3(
    text: str, save_dir: str, voice: str = "zh-CN-XiaoxiaoNeural"
) -> str:
    """
    使用edge-tts将文本转换为MP3语音文件

    参数:
        text: 需要转换的文本内容
        save_dir: 保存MP3文件的目录路径
        voice: 语音模型（默认中文晓晓）

    返回:
        str: 生成的MP3文件完整路径
    """
    # 确保保存目录存在
    Path(save_dir).mkdir(parents=True, exist_ok=True)

    # 基于文本和语音模型生成唯一文件名（避免相同文本不同语音重复）
    content = f"{text}_{voice}".encode()
    file_hash = hashlib.md5(content).hexdigest()
    mp3_filename = f"{file_hash}.mp3"
    mp3_path = os.path.join(save_dir, mp3_filename)

    # 文件已存在直接返回路径
    if os.path.exists(mp3_path):
        return mp3_path

    # 调用edge-tts生成语音
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(mp3_path)
        log.info(f"语音文件生成成功: {mp3_path}")
    except Exception as e:
        raise RuntimeError(f"生成语音文件失败: {e}") from e

    return mp3_path
