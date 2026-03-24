#!/usr/bin/env python3
"""系统操作和环境相关工具函数"""

import asyncio
import copy
import hashlib
import logging
import os
import platform
import random
import string
import urllib.parse
from http.cookies import SimpleCookie
from urllib.parse import urlparse

from requests.utils import cookiejar_from_dict

log = logging.getLogger(__package__)


def parse_cookie_string_to_dict(cookie_string: str):
    """
    解析 Cookie 字符串
    Args:
        cookie_string: Cookie 字符串
    Returns:
        CookieJar 对象
    """
    cookie = SimpleCookie()
    cookie.load(cookie_string)
    cookies_dict = {k: m.value for k, m in cookie.items()}
    return cookies_dict


def parse_cookie_string(cookie_string: str):
    """
    解析 Cookie 字符串

    Args:
        cookie_string: Cookie 字符串

    Returns:
        CookieJar 对象
    """
    cookies_dict = parse_cookie_string_to_dict(cookie_string)
    return cookiejar_from_dict(cookies_dict, cookiejar=None, overwrite=True)


def validate_proxy(proxy_str: str) -> bool:
    """
    验证代理字符串格式

    Args:
        proxy_str: 代理字符串

    Returns:
        True 如果格式正确

    Raises:
        ValueError: 如果格式不正确
    """
    parsed = urlparse(proxy_str)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Proxy scheme must be http or https")
    if not (parsed.hostname and parsed.port):
        raise ValueError("Proxy hostname and port must be set")

    return True


def get_random(length: int) -> str:
    """
    生成随机字符串

    Args:
        length: 字符串长度

    Returns:
        随机字符串
    """
    return "".join(random.sample(string.ascii_letters + string.digits, length))


def deepcopy_data_no_sensitive_info(data, fields_to_anonymize: list = None):
    """
    深拷贝数据并脱敏

    Args:
        data: 要拷贝的数据（字典或对象）
        fields_to_anonymize: 需要脱敏的字段列表

    Returns:
        脱敏后的深拷贝数据
    """
    if fields_to_anonymize is None:
        fields_to_anonymize = [
            "account",
            "password",
        ]

    copy_data = copy.deepcopy(data)

    # 检查copy_data是否是字典或具有属性的对象
    if isinstance(copy_data, dict):
        # 对字典进行处理
        for field in fields_to_anonymize:
            if field in copy_data:
                copy_data[field] = "******"
    else:
        # 对对象进行处理
        for field in fields_to_anonymize:
            if hasattr(copy_data, field):
                setattr(copy_data, field, "******")

    return copy_data


def is_docker() -> bool:
    """判断是否在 Docker 容器中运行"""
    return os.path.exists("/app/.dockerenv")


def get_os_architecture() -> str:
    """
    获取操作系统架构类型：amd64、arm64、arm-v7

    Returns:
        str: 架构类型
    """
    arch = platform.machine().lower()

    if arch in ("x86_64", "amd64"):
        return "amd64"
    elif arch in ("aarch64", "arm64"):
        return "arm64"
    elif "arm" in arch or "armv7" in arch:
        return "arm-v7"
    else:
        return f"unknown architecture: {arch}"





async def restart_xiaomusic() -> int:
    """
    重启 xiaomusic 程序

    Returns:
        退出码
    """
    # 重启 xiaomusic 程序
    sbp_args = (
        "supervisorctl",
        "restart",
        "xiaomusic",
    )

    cmd = " ".join(sbp_args)
    log.info(f"restart_xiaomusic: {cmd}")
    await asyncio.sleep(2)
    proc = await asyncio.create_subprocess_exec(*sbp_args)
    exit_code = await proc.wait()  # 等待子进程完成
    log.info(f"restart_xiaomusic completed with exit code {exit_code}")
    return exit_code



