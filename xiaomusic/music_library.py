"""音乐库管理模块

负责音乐库的管理、播放列表操作、音乐搜索和标签管理。
"""

import asyncio
import base64
import copy
import json
import os
import random
import time
import urllib.parse
from collections import OrderedDict
from dataclasses import asdict
from urllib.parse import urlparse
from xiaomusic.const import SUPPORT_MUSIC_TYPE
from xiaomusic.events import CONFIG_CHANGED
from xiaomusic.utils.text_utils import custom_sort_key, find_best_match, fuzzyfinder


class MusicLibrary:
    """音乐库管理类

    负责管理本地和网络音乐库，包括：
    - 音乐列表生成和管理
    - 播放列表的增删改查
    - 音乐搜索和模糊匹配
    - 音乐标签的读取和更新
    """

    def __init__(
        self,
        config,
        log,
        event_bus=None,
        emby_util=None,
    ):
        """初始化音乐库

        Args:
            config: 配置对象
            log: 日志对象
            event_bus: 事件总线对象（可选）
            emby_util: EmbyUtil对象（可选）
        """
        self.config = config
        self.log = log
        self.event_bus = event_bus
        self.emby_util = emby_util

        # 音乐库数据
        self.all_music = {}  # 所有音乐 {name: Audio对象}
        self.music_list = {}  # 播放列表 {list_name: [music_names]}
        self.default_music_list_names = []  # 非自定义歌单名称列表
        self.custom_play_list = None  # 自定义播放列表缓存

    def gen_all_music_list(self):
        """生成所有音乐列表

        从网络歌单（如Emby）生成音乐列表。
        """
        # 初始化all_music字典
        self.all_music = {}

        # 初始化播放列表（使用 OrderedDict 保持顺序）
        self.music_list = OrderedDict(
            {
                "所有歌曲": [],
                "全部": [],  # 包含所有歌曲
            }
        )

        # 从Emby服务器获取音乐
        if self.emby_util:
            try:
                # 获取Emby音乐
                emby_music = self.emby_util.search_music(limit=50)
                for audio in emby_music:
                    # 构建歌曲名称（只使用歌曲名，与play方法保持一致）
                    music_name = f"{audio.name}"
                    # 添加到音乐库（存储Audio对象）
                    self.all_music[music_name] = audio
                self.log.info(f"从Emby获取到 {len(emby_music)} 首歌曲")
            except Exception as e:
                self.log.exception(f"从Emby获取音乐失败: {e}")

        # 全部，所有歌曲
        self.music_list["全部"] = list(self.all_music.keys())
        self.music_list["所有歌曲"] = list(self.all_music.keys())

        # 歌单排序
        for _, play_list in self.music_list.items():
            play_list.sort(key=custom_sort_key)

        # 非自定义歌单
        self.default_music_list_names = list(self.music_list.keys())

        # 刷新自定义歌单
        self.refresh_custom_play_list()


    def refresh_custom_play_list(self):
        """刷新自定义歌单"""
        try:
            # 删除旧的自定义歌单
            for k in list(self.music_list.keys()):
                if k not in self.default_music_list_names:
                    del self.music_list[k]

            # 合并新的自定义歌单
            custom_play_list = self.get_custom_play_list()
            custom_play_list, changed = self._normalize_custom_playlist_conflicts(
                custom_play_list
            )
            if changed:
                self.custom_play_list = custom_play_list
                self.config.custom_play_list_json = json.dumps(
                    custom_play_list, ensure_ascii=False
                )

            for k, v in custom_play_list.items():
                self.music_list[k] = list(v)
        except Exception as e:
            self.log.exception(f"Execption {e}")

    def _is_reserved_playlist_name(self, name):
        """判断是否与系统/目录歌单冲突（自定义歌单不可占用）"""
        return name in self.default_music_list_names

    def _build_custom_conflict_name(self, base_name, existed_names):
        """为冲突的自定义歌单生成可用的新名称"""
        suffix = "(自定义)"
        candidate = f"{base_name}{suffix}"
        if candidate not in existed_names:
            return candidate

        index = 2
        while True:
            candidate = f"{base_name}{suffix}{index}"
            if candidate not in existed_names:
                return candidate
            index += 1

    def _normalize_custom_playlist_conflicts(self, custom_play_list):
        """清理历史同名冲突：目录/系统歌单名被自定义占用时自动改名"""
        normalized = {}
        changed = False

        reserved_names = set(self.default_music_list_names)
        occupied_names = set(reserved_names)

        for name, musics in custom_play_list.items():
            final_name = name
            if final_name in reserved_names or final_name in occupied_names:
                final_name = self._build_custom_conflict_name(name, occupied_names)
                changed = True
                self.log.info(
                    "自定义歌单名与系统/目录歌单冲突，已自动改名: %s -> %s",
                    name,
                    final_name,
                )

            occupied_names.add(final_name)
            normalized[final_name] = list(musics)

        return normalized, changed

    def get_custom_play_list(self):
        """获取自定义播放列表

        Returns:
            dict: 自定义播放列表字典
        """
        if self.custom_play_list is None:
            self.custom_play_list = {}
            if self.config.custom_play_list_json:
                self.custom_play_list = json.loads(self.config.custom_play_list_json)
        return self.custom_play_list

    def save_custom_play_list(self):
        """保存自定义播放列表"""
        custom_play_list = self.get_custom_play_list()
        self.refresh_custom_play_list()
        self.config.custom_play_list_json = json.dumps(
            custom_play_list, ensure_ascii=False
        )
        # 发布配置变更事件
        if self.event_bus:
            self.event_bus.publish(CONFIG_CHANGED)

    # ==================== 播放列表管理 ====================

    def play_list_add(self, name):
        """新增歌单

        Args:
            name: 歌单名称

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if self._is_reserved_playlist_name(name):
            self.log.info(f"歌单名字与系统/目录歌单冲突 {name}")
            return False
        if name in custom_play_list:
            return False
        custom_play_list[name] = []
        self.save_custom_play_list()
        return True

    def play_list_del(self, name):
        """移除歌单

        Args:
            name: 歌单名称

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            return False
        custom_play_list.pop(name)
        self.save_custom_play_list()
        return True

    def play_list_update_name(self, oldname, newname):
        """修改歌单名字

        Args:
            oldname: 旧歌单名称
            newname: 新歌单名称

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if oldname not in custom_play_list:
            self.log.info(f"旧歌单名字不存在 {oldname}")
            return False
        if self._is_reserved_playlist_name(newname):
            self.log.info(f"新歌单名字与系统/目录歌单冲突 {newname}")
            return False
        if newname in custom_play_list:
            self.log.info(f"新歌单名字已存在 {newname}")
            return False

        play_list = custom_play_list[oldname]
        custom_play_list.pop(oldname)
        custom_play_list[newname] = play_list
        self.save_custom_play_list()
        return True

    def get_play_list_names(self):
        """获取所有自定义歌单名称

        Returns:
            list: 歌单名称列表
        """
        custom_play_list = self.get_custom_play_list()
        return list(custom_play_list.keys())

    def play_list_musics(self, name):
        """获取歌单中所有歌曲

        Args:
            name: 歌单名称

        Returns:
            tuple: (状态消息, 歌曲列表)
        """
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            return "歌单不存在", []
        play_list = custom_play_list[name]
        return "OK", play_list

    def play_list_update_music(self, name, music_list):
        """歌单更新歌曲（覆盖）

        Args:
            name: 歌单名称
            music_list: 歌曲列表

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            # 歌单不存在则新建
            if not self.play_list_add(name):
                return False

        play_list = []
        for music_name in music_list:
            if (music_name in self.all_music) and (music_name not in play_list):
                play_list.append(music_name)

        # 直接覆盖
        custom_play_list[name] = play_list
        self.save_custom_play_list()
        return True

    def update_music_list_json(self, list_name, update_list, append=False):
        """
        更新内存中的播放列表，如果歌单存在则根据 append：False:覆盖； True:追加
        Args:
            list_name: 更新的歌单名称
            update_list: 更新的歌单列表
            append: 追加歌曲，默认 False

        Returns:
            list: 转换后的音乐项目列表
        """
        # 获取或创建歌单
        if list_name in self.music_list:
            existing_musics = self.music_list[list_name]
        else:
            existing_musics = []

        # 构建新歌单数据
        new_music_names = []
        for item in update_list:
            # 检查是否是Audio对象
            if hasattr(item, 'name'):
                new_music_names.append(item.name)
            else:
                # 处理字典形式的项目
                new_music_names.append(item["name"])

        if append:
            # 追加模式：将新项目添加到现有歌单中，避免重复
            existing_names = set(existing_musics)
            for music_name in new_music_names:
                if music_name not in existing_names:
                    existing_musics.append(music_name)
            self.music_list[list_name] = existing_musics
        else:
            # 覆盖模式：替换整个歌单
            self.music_list[list_name] = new_music_names

        # 更新 all_music 字典
        for item in update_list:
            if hasattr(item, 'name'):
                music_name = item.name
                self.all_music[music_name] = item

    def play_list_add_music(self, name, music_list):
        """歌单新增歌曲

        Args:
            name: 歌单名称
            music_list: 歌曲列表

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            # 歌单不存在则新建
            if not self.play_list_add(name):
                return False

        play_list = custom_play_list[name]
        for music_name in music_list:
            if (music_name in self.all_music) and (music_name not in play_list):
                play_list.append(music_name)

        self.save_custom_play_list()
        return True

    def play_list_del_music(self, name, music_list):
        """歌单移除歌曲

        Args:
            name: 歌单名称
            music_list: 歌曲列表

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            return False

        play_list = custom_play_list[name]
        for music_name in music_list:
            if music_name in play_list:
                play_list.remove(music_name)

        self.save_custom_play_list()
        return True

    # ==================== 音乐搜索 ====================

    def find_real_music_name(self, name, n):
        """模糊搜索音乐名称

        Args:
            name: 搜索关键词
            n: 返回结果数量

        Returns:
            list: 匹配的音乐名称列表
        """
        if not self.config.enable_fuzzy_match:
            self.log.debug("没开启模糊匹配")
            return []

        all_music_list = list(self.all_music.keys())
        real_names = find_best_match(
            name,
            all_music_list,
            cutoff=self.config.fuzzy_match_cutoff,
            n=n,
        )
        if not real_names:
            self.log.info(f"没找到歌曲【{name}】")
            return []
        self.log.info(f"根据【{name}】找到歌曲【{real_names}】")
        if name in real_names:
            return [name]

        # 音乐不在查找结果同时n大于1, 模糊匹配模式，扩大范围再找，最后保留随机 n 个
        if n > 1:
            real_names = find_best_match(
                name,
                all_music_list,
                cutoff=self.config.fuzzy_match_cutoff,
                n=n * 2,
            )
            random.shuffle(real_names)
        self.log.info(f"没找到歌曲【{name}】")
        return real_names[:n]

    def find_real_music_list_name(self, list_name):
        """模糊搜索播放列表名称

        Args:
            list_name: 播放列表名称

        Returns:
            str: 匹配的播放列表名称
        """
        if not self.config.enable_fuzzy_match:
            self.log.debug("没开启模糊匹配")
            return list_name

        # 模糊搜一个播放列表（只需要一个，不需要 extra index）
        real_names = find_best_match(
            list_name,
            self.music_list,
            cutoff=self.config.fuzzy_match_cutoff,
            n=1,
        )

        if real_names:
            real_name = real_names[0]
            self.log.info(f"根据【{list_name}】找到播放列表【{real_name}】")
            list_name = real_name
        else:
            self.log.info(f"没找到播放列表【{list_name}】")

        return list_name

    def searchmusic(self, name):
        """搜索音乐

        Args:
            name: 搜索关键词

        Returns:
            list: 搜索结果列表
        """
        all_music_list = list(self.all_music.keys())
        search_list = fuzzyfinder(name, all_music_list)
        self.log.debug(f"searchmusic. name:{name} search_list:{search_list}")
        return search_list

    # ==================== 音乐信息 ====================

    # ==================== 标签管理 ====================

    async def get_music_duration(self, name: str) -> float:
        """获取歌曲时长
        优先从Audio对象中读取，如果不是Audio对象则返回 0
        Args:
            name: 歌曲名称
        Returns:
            float: 歌曲时长（秒），失败返回 0
        """
        # 检查歌曲是否存在
        if name not in self.all_music:
            self.log.warning(f"歌曲 {name} 不存在")
            return 0

        # 从Audio对象中获取时长
        music = self.all_music[name]
        if hasattr(music, 'duration'):
            duration = music.duration
            self.log.info(f"从Audio对象获取音乐 {name} 时长: {duration} 秒")
            return duration

        return 0



    # ==================== 辅助方法 ====================

    def get_music_list(self):
        """获取所有播放列表

        Returns:
            dict: 播放列表字典
        """
        return self.music_list

    # ==================== URL处理方法 ====================

    async def get_music_url(self, name):
        """获取音乐播放地址
        Args:
            name: 歌曲名称
        Returns:
            tuple: (播放地址, 原始地址) - 网络音乐时可能有原始地址
        """
        self.log.info(f"get_music_url name:{name}")
        music = self.all_music[name]
        url = music.stream_url
        self.log.info(f"get_music_url web music. name:{name}, url:{url}")

        if url.startswith("self://"):
            proxy_url = self._get_proxy_url(music)
            return proxy_url, url

        return url, None



    def _get_proxy_url(self, music):
        """获取代理URL

        Args:
            music: Audio对象或URL字符串

        Returns:
            str: 代理URL
        """
        # 获取原始URL
        if hasattr(music, 'stream_url'):
            origin_url = music.stream_url
        else:
            origin_url = music
        
        # 强制使用原始URL（NAS地址）进行代理
        self.log.info(f"Using original emby url: {origin_url}")
        return origin_url



    @staticmethod
    async def get_play_url(proxy_url):
        """获取播放URL

        Args:
            proxy_url: 代理URL

        Returns:
            str: 最终重定向的URL
        """
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(proxy_url) as response:
                # 获取最终重定向的 URL
                return str(response.url)

    def _get_file_url(self, file_path):
        """获取文件的URL

        Args:
            file_path: 本地文件路径

        Returns:
            str: 可访问的URL
        """
        import os
        from urllib.parse import quote

        # 获取文件的相对路径（相对于temp_path）
        temp_path = self.config.temp_path
        if file_path.startswith(temp_path):
            relative_path = os.path.relpath(file_path, temp_path)
        else:
            # 如果不是在temp_path中，使用完整路径
            relative_path = file_path

        # 构建URL
        encoded_path = quote(relative_path.replace(os.sep, '/'))
        hostname = self.config.hostname
        # 智能检查hostname是否已包含协议前缀
        if not hostname.startswith(('http://', 'https://')):
            hostname = f"http://{hostname}"
        url = f"{hostname}:{self.config.public_port}/music/tmp/{encoded_path}"
        self.log.info(f"文件路径转换为URL: {file_path} -> {url}")
        return url

    def expand_self_url(self, origin_url):
        parsed_url = urlparse(origin_url)
        self.log.info(f"链接处理前 ${parsed_url}")
        if parsed_url.scheme != "self":
            return parsed_url, origin_url

        hostname = self.config.hostname
        # 智能检查hostname是否已包含协议前缀
        if not hostname.startswith(('http://', 'https://')):
            hostname = f"http://{hostname}"
        url = f"{hostname}:{self.config.public_port}{parsed_url.path}"
        if parsed_url.query:
            url += f"?{parsed_url.query}"
        if parsed_url.fragment:
            url += f"#{parsed_url.fragment}"

        return urlparse(url), url

