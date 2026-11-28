import requests
import os
import re
import uuid
from typing import List, Optional
import urllib.parse


class ChineseNumberConverter:
    """中文数字转阿拉伯数字工具类"""
    
    # 基本数字映射
    _num_map = {
        '零': 0, '一': 1, '二': 2, '三': 3, '四': 4,
        '五': 5, '六': 6, '七': 7, '八': 8, '九': 9
    }
    
    # 计数单位映射
    _unit_map = {
        '十': 10, '百': 100, '千': 1000, '万': 10000, '亿': 100000000
    }
    
    @classmethod
    def chinese_to_arabic(cls, chinese_num: str) -> int:
        """
        将中文数字转换为阿拉伯数字
        
        Args:
            chinese_num: 中文数字字符串
            
        Returns:
            对应的阿拉伯数字
        """
        if not chinese_num:
            return 0
            
        # 处理特殊情况
        if chinese_num == '十':
            return 10
        elif chinese_num == '百':
            return 100
        elif chinese_num == '千':
            return 1000
        elif chinese_num == '万':
            return 10000
        elif chinese_num == '亿':
            return 100000000
        elif chinese_num == '零':
            return 0
        
        # 处理没有单位的连续数字（如：二八 -> 28, 一一 -> 11）
        has_unit = any(char in cls._unit_map for char in chinese_num)
        if not has_unit:
            result = 0
            for char in chinese_num:
                if char in cls._num_map:
                    result = result * 10 + cls._num_map[char]
                else:
                    # 非数字字符，跳过
                    continue
            return result
            
        # 处理包含单位的数字
        result = 0
        temp = 0
        has_zero = False
        
        for char in chinese_num:
            if char == '零':
                has_zero = True
                continue
            elif char in cls._num_map:
                if has_zero:
                    has_zero = False
                temp = cls._num_map[char]
            elif char in cls._unit_map:
                unit = cls._unit_map[char]
                if temp == 0:
                    temp = 1
                temp *= unit
                result += temp
                temp = 0
                has_zero = False
            else:
                # 非数字和单位字符，跳过
                continue
        
        result += temp
        return result
    
    @classmethod
    def convert_chinese_numbers_in_string(cls, text: str) -> str:
        """
        将字符串中的中文数字转换为阿拉伯数字
        
        Args:
            text: 包含中文数字的字符串
            
        Returns:
            转换后的字符串
        """
        if not text:
            return text
            
        # 定义不应该被转换的固定词语列表
        fixed_words = {
            '一剪梅', '千与千寻', '万水千山', '亿万富翁', '百年孤独', '十年',
            '千言万语', '千方百计', '千军万马', '千秋万代', '千家万户', '千变万化',
            '一千零一','九百九十九','七里香','一些','一次','一整','一个','一滴','一天',
            '一起','一生','一场','一笑', '一见','一样','一半','一直','一步','一封'
        }
        
        # 检查整个字符串是否是固定词语，如果是则不转换
        if text in fixed_words:
            return text
        
        import re
        
        # 保留年份的枚举替换
        year_replacements = {
            '一九九七': '1997',
            '一九九八': '1998',
            '一九九九': '1999',
            '二零零零': '2000',
            '二零零一': '2001',
            '二零零二': '2002',
            '二零零三': '2003',
            '二零零四': '2004',
            '二零零五': '2005',
            '二零零六': '2006',
            '二零零七': '2007',
            '二零零八': '2008',
            '二零零九': '2009',
            '二零一零': '2010',
            '二零一一': '2011',
            '二零一二': '2012',
            '二零一三': '2013',
            '二零一四': '2014',
            '二零一五': '2015',
            '二零一六': '2016',
            '二零一七': '2017',
            '二零一八': '2018',
            '二零一九': '2019',
            '二零二零': '2020',
            '二零二一': '2021',
            '二零二二': '2022',
            '二零二三': '2023',
            '二零二四': '2024',
            '二零二五': '2025',
            '二零二六': '2026',
        }
        
        # 按照字符串长度降序排序，确保较长的年份先被替换
        for chinese_year, arabic_year in sorted(year_replacements.items(), key=lambda x: -len(x[0])):
            if chinese_year in text:
                # 检查是否是固定词语的一部分
                is_part_of_fixed_word = False
                for word in fixed_words:
                    if chinese_year in word:
                        is_part_of_fixed_word = True
                        break
                if not is_part_of_fixed_word:
                    text = text.replace(chinese_year, arabic_year)
        
        # 首先检查整个字符串是否是固定词语，如果是则直接返回
        if text in fixed_words:
            return text
        
        # 使用更精确的正则表达式模式，按优先级处理
        # 先处理三位数及以上的数字（如：一百零五）
        text = re.sub(r'([零一二三四五六七八九]+[千万亿][零一二三四五六七八九十百千万亿]*[零一二三四五六七八九]+)', lambda m: str(cls.chinese_to_arabic(m.group(0))) if m.group(0) not in fixed_words else m.group(0), text)
        
        # 处理两位数的数字组合
        # 先处理"数字+十+数字"形式（如：三十六、八十二）
        text = re.sub(r'([零一二三四五六七八九]+十[零一二三四五六七八九]+)', lambda m: str(cls.chinese_to_arabic(m.group(0))) if m.group(0) not in fixed_words else m.group(0), text)
        
        # 再处理"十+数字"形式（如：十九、十二）
        text = re.sub(r'(十[零一二三四五六七八九]+)', lambda m: str(cls.chinese_to_arabic(m.group(0))) if m.group(0) not in fixed_words else m.group(0), text)
        
        # 再处理"数字+十"形式（如：八十、三十）
        text = re.sub(r'([零一二三四五六七八九]+十)', lambda m: str(cls.chinese_to_arabic(m.group(0))) if m.group(0) not in fixed_words else m.group(0), text)
        
        # 处理连续的数字组合（如：二八、一一）
        text = re.sub(r'([零一二三四五六七八九]{2,})', lambda m: str(cls.chinese_to_arabic(m.group(0))) if m.group(0) not in fixed_words else m.group(0), text)
        
        # 最后处理单个数字（如：八、三）
        # 特别注意检查是否是固定词语的一部分
        def replace_single_digit(match):
            chinese_num = match.group(0)
            
            # 检查是否是固定词语的一部分
            for word in fixed_words:
                if chinese_num in word:
                    # 检查整个词语是否在文本中
                    if word in text:
                        return chinese_num
            
            return str(cls._num_map[chinese_num])
        
        text = re.sub(r'([零一二三四五六七八九])', replace_single_digit, text)
        
        return text


class Audio:
    """音频类，用于存储音乐信息"""
    def __init__(self, name="", id="", index=0, duration=0, album="", album_id="", 
                 album_artist="", artist_items=None, album_artists=None, is_favorite=False,
                 user_id="", genres=None, production_year=0, play_count=0, date_created="",
                 last_played_at=""):
        self.name = name
        self.id = id
        self.index = index
        self.duration = duration
        self.album = album
        self.album_id = album_id
        self.album_artist = album_artist
        self.artist_items = artist_items or []
        self.album_artists = album_artists or []
        self.is_favorite = is_favorite
        self.user_id = user_id  # 搜索时使用的 Emby 用户 ID
        # 今日推荐所需的元数据（Emby 原始 item 里本就有，之前被丢弃）
        self.genres = genres or []
        self.production_year = production_year or 0
        self.play_count = play_count or 0
        self.date_created = date_created or ""
        self.last_played_at = last_played_at or ""
        self.play_session_id = ""  # PlaybackInfo 返回的播放会话 ID
        self.media_source_id = ""  # 媒体源 ID
        self.container = "flac"
        self.file_path = ""
        self.stream_url = ""
        self.type = "emby"
    
    def __str__(self):
        return f"Audio(name='{self.name}', album='{self.album}', album_artist='{self.album_artist}', duration={self.duration}, container='{self.container}',index={self.index},is_favorite={self.is_favorite})"


class EmbyUtil:
    # Emby 客户端标识常量
    CLIENT_NAME = "XiaoEmby"
    DEVICE_NAME = "小爱音箱"
    # /Items 默认只返回精简字段，流派/年份/最近播放时间必须显式请求，
    # 否则 item 里连 Genres 字段都不存在（不是空数组）。
    DETAIL_FIELDS = "Genres,ProductionYear,Path,UserDataPlayCount,UserDataLastPlayedDate"

    def __init__(self, host, user_id, log, version="1.0.0",
                 username=None, password=None, api_key=None):
        # 确保 host 包含协议前缀
        if host and not host.startswith(("http://", "https://")):
            host = f"http://{host}"  # 默认使用 http
        self.host = host
        self.user_id = user_id
        self.log = log
        self.version = version
        self.username = username or ""
        self.password = password or ""
        self.api_key = str(api_key or os.getenv("XIAOMUSIC_EMBY_API_KEY", "") or "")
        self._access_token = None
        self._auth_user_id = None
        # 稳定的设备 ID（基于 host + 用户名，重启后一致）
        self._device_id = f"xiaoemby-{uuid.uuid5(uuid.NAMESPACE_DNS, f'{host}-{self.username}')}"
        if self.username and self.password:
            self.login()
        elif not self.api_key:
            self.log.error("未配置 Emby 用户名/密码或 API Key，无法访问 Emby")

    def get_access_token(self):
        """供外部模块（如音频代理）取用登录 token"""
        return self._access_token

    def login(self):
        """用用户名密码登录 Emby，换取用户级 access token。

        Emby 的服务器级 API Key 不绑定任何用户，用它上报播放时创建的会话
        没有 UserId，面板会显示 unknown；用户登录得到的 token 才带用户身份。
        """
        device_encoded = urllib.parse.quote(self.DEVICE_NAME)
        url = f"{self.host}/Users/AuthenticateByName"
        headers = {
            "X-Emby-Authorization": (
                f'MediaBrowser Client="{self.CLIENT_NAME}", '
                f'Device="{device_encoded}", '
                f'DeviceId="{self._device_id}", '
                f'Version="{self.version}"'
            ),
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(
                url,
                json={"Username": self.username, "Pw": self.password},
                headers=headers,
            )
            if response.status_code == 200:
                data = response.json()
                self._access_token = data.get("AccessToken", "")
                self._auth_user_id = (data.get("User") or {}).get("Id", "")
                if self._access_token:
                    self.log.info(
                        f"Emby 用户登录成功: username={self.username}, "
                        f"userId={data.get('User', {}).get('Id', '')}"
                    )
                    return True
                self.log.error("Emby 用户登录成功但未返回 AccessToken")
            else:
                self.log.error(
                    f"Emby 用户登录失败: username={self.username}, "
                    f"状态码={response.status_code}, 响应={response.text[:200]}"
                )
        except Exception as e:
            self.log.error(f"Emby 用户登录异常: {e}")
        self._access_token = None
        self._auth_user_id = None
        return False

    def _get_auth_header(self):
        """构建 Emby 请求 Headers：统一使用用户登录 token

        注意：HTTP Header 仅支持 ASCII 字符，中文需要 URL 编码
        """
        device_encoded = urllib.parse.quote(self.DEVICE_NAME)
        auth = (
            f'MediaBrowser Client="{self.CLIENT_NAME}", '
            f'Device="{device_encoded}", '
            f'DeviceId="{self._device_id}", '
            f'Version="{self.version}"'
        )
        if self._access_token:
            auth += f', UserId="{self.user_id}"'
        return {
            "X-Emby-Authorization": auth,
            "X-Emby-Token": self._access_token or self.api_key,
            "Content-Type": "application/json"
        }

    def _post_playback(self, url, payload, params):
        """发送播放上报；token 失效时重新登录并重试一次"""
        response = requests.post(
            url, json=payload, headers=self._get_auth_header(), params=params
        )
        if response.status_code == 401 and self.username and self.password:
            self.log.warning("Emby token 已失效，尝试重新登录")
            if self.login():
                response = requests.post(
                    url, json=payload, headers=self._get_auth_header(), params=params
                )
        return response

    def __set_audio_container(self, audio: Audio):
        """
        设置音频流 container、file_path，并提取 PlaySessionId 和 MediaSourceId
        """
        url = f"{self.host}/Items/{audio.id}/PlaybackInfo?api_key={self._access_token}"
        response = requests.get(url)
        if response.status_code == 200:
            c = "mp3"
            try:
                resp_json = response.json()
                # 提取 PlaySessionId
                audio.play_session_id = resp_json.get("PlaySessionId", "")
                
                media_sources = resp_json.get("MediaSources", [])
                if media_sources and len(media_sources) > 0:
                    path = media_sources[0].get("Path", "")
                    audio.file_path = path
                    if path:
                        c = os.path.splitext(path)[1].replace(".", "")
                    # 提取 MediaSourceId
                    audio.media_source_id = media_sources[0].get("Id", "")
            except Exception as e:
                self.log.error(f"设置音频容器失败: {e}")
                pass
            audio.container = c

    def __set_audio_stream_url(self, audio: Audio):
        """
        获取音频流地址
        """
        # 构建音频流URL，只对需要编码的参数进行编码
        container_encoded = urllib.parse.quote(audio.container)
        api_key_encoded = urllib.parse.quote(self._access_token or self.api_key)
        stream_url = f"/emby/Audio/{audio.id}/stream?Container={container_encoded}&api_key={api_key_encoded}"
        audio.stream_url = f"{self.host}{stream_url}"

    def __search_music_internal(self, name=None, artist=None, genre=None, is_favorite: bool = None, album=None, years=None, min_premiere_date=None, max_premiere_date=None, limit=50, user_id=None) -> List[
        Audio]:
        """
        内部搜索音乐的方法
        
        Args:
            user_id: 可选的用户ID，如果提供则使用该ID，否则使用初始化时的user_id
        """
        original_album = album
        
        audio_list = []  # 音乐列表
        is_favorite = bool(is_favorite)
        # 使用传入的user_id或默认的user_id
        current_user_id = user_id if user_id else self.user_id
        url = "{}/emby/Users/{}/Items".format(self.host, current_user_id)

        payload = {
            "Recursive": True,
            "MediaTypes": "Audio",
            "Limit": limit,  # 总是获取多首歌曲
            "SortBy": "Random",
            "Fields": self.DETAIL_FIELDS,
            "api_key": self._access_token
        }
        
        # 构建搜索关键词
        search_term = name
        payload.update({"SearchTerm": search_term}) if search_term is not None else None
        
        payload.update({"Artists": artist}) if artist is not None else None
        payload.update({"Albums": album}) if album is not None else None
        payload.update({"Genres": genre}) if genre is not None else None
        payload.update({"Years": years}) if years is not None else None
        payload.update({"MinPremiereDate": min_premiere_date}) if min_premiere_date is not None else None
        payload.update({"MaxPremiereDate": max_premiere_date}) if max_premiere_date is not None else None
        payload.update({"IncludeItemTypes": "Audio"}) if name is None else None
        if is_favorite:
            payload.update({"IsFavorite": is_favorite})

        try:
            r = requests.get(url=url, params=payload)
            self.log.info("Emby 接口返回：{} ...".format(r.text[:1000]))
        except Exception as e:
            self.log.error("Emby 接口异常：{}".format(e))
            return []

        try:
            items = r.json().get("Items", [])
            for item in items:
                item: dict
                # 安全获取UserData和IsFavorite
                user_data = item.get("UserData", {})
                is_favorite = user_data.get("IsFavorite", False)
                
                # 安全获取RunTimeTicks
                run_time_ticks = item.get("RunTimeTicks", 10000000)
                try:
                    duration = int(int(run_time_ticks) / 10000000)
                except Exception:
                    duration = 0
                
                audio = Audio(name=item.get("Name", ""), id=item.get("Id", 1), index=item.get("IndexNumber", 1),
                              duration=duration,
                              album=item.get("Album", 1), album_id=item.get("AlbumId", 1),
                              album_artist=item.get("AlbumArtist", ""),
                              artist_items=item.get("ArtistItems", []),
                              album_artists=item.get("AlbumArtists", []),
                              is_favorite=is_favorite,
                              user_id=current_user_id,
                              genres=item.get("Genres", []),
                              production_year=item.get("ProductionYear", 0),
                              play_count=user_data.get("PlayCount", 0),
                              date_created=item.get("DateCreated", ""),
                              last_played_at=user_data.get("LastPlayedDate", ""))
                audio_list.append(audio)

            # 播放专辑的话根据索引排序
            if original_album != '':
                audio_list.sort(key=lambda x: x.index)
            # 处理音频流
            for audio in audio_list:
                try:
                    self.__set_audio_container(audio)
                    self.__set_audio_stream_url(audio)
                except Exception as e:
                    self.log.error(f"处理音频流失败: {audio.name}, {e}")
                    pass
        except Exception as e:
            self.log.error(f"处理Emby返回数据失败: {e}")
            return []
        return audio_list

    def search_music(self, name=None, artist=None, genre=None, is_favorite: bool = None, album=None, years=None, min_premiere_date=None, max_premiere_date=None, limit=50, user_id=None) -> List[
        Audio]:
        """
        搜索音乐，支持中文数字转换
        
        如果name中包含中文数字（如"爱情三十六计"），会将其转换为阿拉伯数字（如"爱情36计"），
        同时使用原始名称和转换后的名称请求Emby接口，取返回结果条数多的结果返回
        
        Args:
            user_id: 可选的用户ID，如果提供则使用该ID，否则使用初始化时的user_id
        """
        all_locals = locals()
        #self.log.info("Emby查询参数：{}".format(all_locals))
        
        # 第一次使用原始名称搜索
        original_result = self.__search_music_internal(name, artist, genre, is_favorite, album, years, min_premiere_date, max_premiere_date, limit, user_id)
        
        # 如果name不为None且包含中文数字，则进行第二次搜索
        converted_result = []
        if name is not None:
            converted_name = ChineseNumberConverter.convert_chinese_numbers_in_string(name)
            if converted_name != name:  # 只有当转换后的名称与原始名称不同时才进行第二次搜索
                self.log.info(f"将中文数字转换后搜索：{name} -> {converted_name}")
                converted_result = self.__search_music_internal(converted_name, artist, genre, is_favorite, album, years, min_premiere_date, max_premiere_date, limit, user_id)
        
        # 比较两次搜索的结果数量，返回结果更多的那个
        original_count = len(original_result)
        converted_count = len(converted_result)
        
        if original_count > converted_count:
            self.log.info(f"使用原始名称搜索结果：{original_count}条")
            self.log.info("返回原始结果列表：{}".format([str(audio) for audio in original_result[:10]])) if original_result else None
            return original_result
        elif converted_count > original_count:
            self.log.info(f"使用转换后名称搜索结果：{converted_count}条")
            self.log.info("返回转换后结果列表：{}".format([str(audio) for audio in converted_result[:10]])) if converted_result else None
            return converted_result
        elif original_count > 0:  # 两者数量相同且都有结果，优先返回原始结果
            self.log.info(f"原始和转换后搜索结果数量相同：{original_count}条，返回原始结果")
            self.log.info("返回原始结果列表：{}".format([str(audio) for audio in original_result[:10]])) if original_result else None
            return original_result
        else:
            self.log.info(f"原始和转换后搜索均无结果")
            return []

    def _parse_recommend_item(self, item: dict, user_id: str) -> Audio:
        """把 Emby item 解析为 Audio（仅今日推荐路径使用，保持主搜索链路零改动）。"""
        user_data = item.get("UserData") or {}
        try:
            duration = int(int(item.get("RunTimeTicks", 10000000)) / 10000000)
        except (TypeError, ValueError):
            duration = 0
        return Audio(
            name=item.get("Name", ""),
            id=item.get("Id", ""),
            index=item.get("IndexNumber", 1),
            duration=duration,
            album=item.get("Album", ""),
            album_id=item.get("AlbumId", ""),
            album_artist=item.get("AlbumArtist", ""),
            artist_items=item.get("ArtistItems", []) or [],
            album_artists=item.get("AlbumArtists", []) or [],
            is_favorite=user_data.get("IsFavorite", False),
            user_id=user_id,
            genres=item.get("Genres", []) or [],
            production_year=item.get("ProductionYear", 0),
            play_count=user_data.get("PlayCount", 0),
            date_created=item.get("DateCreated", ""),
            last_played_at=user_data.get("LastPlayedDate", ""),
        )

    def fetch_music_genres(self) -> list[str]:
        """拿全库音乐流派清单（缓存到实例），用于分层采样。"""
        if getattr(self, "_music_genres_cache", None) is not None:
            return self._music_genres_cache
        genres: list[str] = []
        try:
            r = requests.get(
                f"{self.host}/emby/MusicGenres",
                params={"api_key": self._access_token, "UserId": self.user_id},
                timeout=20,
            )
            r.raise_for_status()
            genres = [
                x.get("Name") for x in r.json().get("Items", []) if x.get("Name")
            ]
        except Exception as e:
            self.log.error(f"拉取 Emby 音乐流派失败: {e}")
        self._music_genres_cache = genres
        return genres

    def fetch_total_tracks(self) -> int | None:
        """全库曲目总数，用于给 LLM 展示真实库规模。"""
        try:
            r = requests.get(
                f"{self.host}/emby/Users/{self.user_id}/Items",
                params={
                    "Recursive": True,
                    "MediaTypes": "Audio",
                    "Limit": 1,
                    "api_key": self._access_token,
                },
                timeout=20,
            )
            r.raise_for_status()
            return int(r.json().get("TotalRecordCount", 0)) or None
        except Exception as e:
            self.log.error(f"拉取 Emby 曲目总数失败: {e}")
            return None

    def fetch_recommend_candidates(self, limit=None, user_id=None) -> list[Audio]:
        """分层采样候选池：按流派分桶 + 新发行桶，并发拉取后去重。

        limit=None 时按库总量自适应（如 7 万首 → 约 2100 首），
        避免把候选数写死成 800 导致大库的丰富度天花板过低。
        """
        from concurrent.futures import ThreadPoolExecutor

        current_user_id = user_id or self.user_id
        url = f"{self.host}/emby/Users/{current_user_id}/Items"

        if limit is None:
            from xiaomusic.utils.daily_recommend import (
                CANDIDATE_MAX, CANDIDATE_MIN, CANDIDATE_RATIO, GENRE_MAX,
            )
            total = self.fetch_total_tracks() or 0
            limit = min(max(int(total * CANDIDATE_RATIO), CANDIDATE_MIN), CANDIDATE_MAX)

        genres = self.fetch_music_genres()
        selected = genres[:GENRE_MAX]
        per_genre = max(12, limit // max(len(selected), 1))

        def fetch_batch(extra: dict) -> list:
            params = {
                "Recursive": True,
                "IncludeItemTypes": "Audio",
                "Limit": extra.pop("Limit", per_genre),
                "Fields": self.DETAIL_FIELDS,
                "api_key": self._access_token,
            }
            params.update(extra)
            try:
                r = requests.get(url=url, params=params, timeout=30)
                r.raise_for_status()
                return r.json().get("Items", [])
            except Exception as e:
                self.log.error(f"分层采样失败 {extra}: {e}")
                return []

        batches: list[dict] = [{"Genres": g, "SortBy": "Random"} for g in selected]
        batches.append({"SortBy": "DateCreated", "SortOrder": "Descending", "Limit": 100})
        if not selected:
            batches = [{"SortBy": "Random", "Limit": limit}]

        try:
            with ThreadPoolExecutor(max_workers=6) as executor:
                results = list(executor.map(fetch_batch, batches))
        except Exception as e:
            self.log.error(f"分层采样并发失败: {e}")
            return []

        items: dict = {}
        for batch in results:
            for item in batch:
                if item.get("Id"):
                    items[item["Id"]] = item
        return [
            self._parse_recommend_item(item, current_user_id)
            for item in list(items.values())[:limit]
        ]

    def fetch_recently_played(self, limit=50, user_id=None) -> list[Audio]:
        """按 DatePlayed 拉最近播放。

        很多 Emby 库的 UserData.PlayCount / LastPlayedDate 是空的，但 SortBy=DatePlayed
        的排序本身就是播放历史，这是唯一可靠的「最近在听谁」信号。
        """
        current_user_id = user_id or self.user_id
        url = f"{self.host}/emby/Users/{current_user_id}/Items"
        payload = {
            "Recursive": True,
            "IncludeItemTypes": "Audio",
            "SortBy": "DatePlayed",
            "SortOrder": "Descending",
            "Limit": limit,
            "Fields": self.DETAIL_FIELDS,
            "api_key": self._access_token,
        }
        try:
            r = requests.get(url=url, params=payload, timeout=30)
            r.raise_for_status()
            items = r.json().get("Items", [])
        except Exception as e:
            self.log.error(f"拉取最近播放失败: {e}")
            return []
        return [self._parse_recommend_item(item, current_user_id) for item in items]

    def fetch_tracks_by_ids(self, ids, user_id=None) -> list[Audio]:
        """按 id 取回完整 Audio（含 stream_url），用于恢复当天推荐与真正播放。"""
        ids = [str(i) for i in ids if i]
        if not ids:
            return []
        current_user_id = user_id or self.user_id
        url = f"{self.host}/emby/Users/{current_user_id}/Items"
        payload = {
            "Ids": ",".join(ids),
            "Limit": len(ids),
            "Fields": self.DETAIL_FIELDS,
            "api_key": self._access_token,
        }
        try:
            r = requests.get(url=url, params=payload, timeout=30)
            r.raise_for_status()
            items = r.json().get("Items", [])
        except Exception as e:
            self.log.error(f"按 ID 拉取今日推荐曲目失败: {e}")
            return []
        audios = [self._parse_recommend_item(item, current_user_id) for item in items]
        # Emby 不保证按 Ids 顺序返回，按请求顺序重排
        id_order = {str(track_id): i for i, track_id in enumerate(ids)}
        audios.sort(key=lambda a: id_order.get(str(a.id), 9999))
        for audio in audios:
            try:
                self.__set_audio_container(audio)
                self.__set_audio_stream_url(audio)
            except Exception as e:
                self.log.error(f"准备今日推荐曲目播放信息失败: {audio.name}, {e}")
        return audios

    def favorite_audio(self, audio_id: str, user_id: str = None) -> bool:
        """
        将指定的歌曲添加到收藏

        参考 Emby API: POST /Users/{UserId}/FavoriteItems/{Id}
        :param audio_id: 歌曲ID
        :param user_id: 用户ID，如果为None则使用默认用户ID
        :return: 成功返回 True，失败返回 False
        """
        # 如果没有指定user_id，使用默认的user_id
        if user_id is None:
            user_id = self.user_id
            
        url = f"{self.host}/Users/{user_id}/FavoriteItems/{audio_id}"

        params = {
            "api_key": self._access_token
        }
        headers = {
            "Accept": "application/json"
        }

        try:
            response = requests.post(url, headers=headers, params=params)

            if response.status_code == 200:
                user_data = response.json()
                self.log.info(f"收藏歌曲成功, ID: {audio_id}, user_id: {user_id}, 状态码: {response.status_code}, 响应: {user_data}")
                return user_data.get("IsFavorite", True)
            else:
                self.log.error(f"收藏歌曲失败, ID: {audio_id}, 状态码: {response.status_code}, 响应: {response.text}")
                return False
        except Exception as e:
            self.log.error(f"请求 Emby API 收藏歌曲时发生异常: {e}")
            return False

    def report_playback_start(self, item_id, user_id=None, media_source_id=None,
                               play_session_id=None):
        """
        上报播放开始
        
        调用 Emby API: POST /Sessions/Playing
        
        Args:
            item_id: Emby 媒体项 ID
            user_id: Emby 用户 ID（用于关联播放记录到具体用户）
            media_source_id: 媒体源 ID
            play_session_id: 播放会话 ID
        """
        current_user_id = user_id or self.user_id
        url = f"{self.host}/Sessions/Playing"
        payload = {
            "ItemId": str(item_id),
            "MediaSourceId": str(media_source_id or item_id),
            "PlaySessionId": play_session_id or str(uuid.uuid4()),
            "PlayMethod": "DirectStream",
            "QueueableMediaTypes": ["Audio"],
            "PositionTicks": 0,
            "IsPaused": False,
        }
        
        try:
            # 使用 UserId 查询参数关联用户
            params = {"UserId": current_user_id} if current_user_id else {}
            response = self._post_playback(url, payload, params)
            self.log.info(
                f"上报播放开始: ItemId={item_id}, UserId={current_user_id}, "
                f"PlaySessionId={payload['PlaySessionId']}, 状态码={response.status_code}"
            )
        except Exception as e:
            self.log.error(f"上报播放开始失败: {e}")

    def report_playback_progress(self, item_id, position_ticks, user_id=None,
                                  media_source_id=None, play_session_id=None,
                                  is_paused=False):
        """
        上报播放进度
        
        调用 Emby API: POST /Sessions/Playing/Progress
        
        Args:
            item_id: Emby 媒体项 ID
            position_ticks: 当前播放位置（1 tick = 100 纳秒）
            user_id: Emby 用户 ID
            media_source_id: 媒体源 ID
            play_session_id: 播放会话 ID
            is_paused: 是否暂停
        """
        current_user_id = user_id or self.user_id
        url = f"{self.host}/Sessions/Playing/Progress"
        payload = {
            "ItemId": str(item_id),
            "MediaSourceId": str(media_source_id or item_id),
            "PlaySessionId": play_session_id or "",
            "PositionTicks": max(0, int(position_ticks)),
            "IsPaused": is_paused,
            "EventName": "TimeUpdate",
        }
        
        try:
            params = {"UserId": current_user_id} if current_user_id else {}
            response = self._post_playback(url, payload, params)
            self.log.debug(
                f"上报播放进度: ItemId={item_id}, PositionTicks={position_ticks}, "
                f"状态码={response.status_code}"
            )
        except Exception as e:
            self.log.error(f"上报播放进度失败: {e}")

    def report_playback_stop(self, item_id, position_ticks, user_id=None,
                              media_source_id=None, play_session_id=None):
        """
        上报播放停止
        
        调用 Emby API: POST /Sessions/Playing/Stopped
        
        Args:
            item_id: Emby 媒体项 ID
            position_ticks: 最终播放位置（1 tick = 100 纳秒）
            user_id: Emby 用户 ID
            media_source_id: 媒体源 ID
            play_session_id: 播放会话 ID
        """
        current_user_id = user_id or self.user_id
        url = f"{self.host}/Sessions/Playing/Stopped"
        payload = {
            "ItemId": str(item_id),
            "MediaSourceId": str(media_source_id or item_id),
            "PlaySessionId": play_session_id or "",
            "PositionTicks": max(0, int(position_ticks)),
        }
        
        try:
            params = {"UserId": current_user_id} if current_user_id else {}
            response = self._post_playback(url, payload, params)
            self.log.info(
                f"上报播放停止: ItemId={item_id}, UserId={current_user_id}, "
                f"PositionTicks={position_ticks}, 状态码={response.status_code}"
            )
        except Exception as e:
            self.log.error(f"上报播放停止失败: {e}")


if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.INFO)
    test_log = logging.getLogger("test_emby_util")
    
    BASE_URL = "http://127.0.0.1:48096"
    USER_ID = "your_emby_user_id_here"
    USERNAME = "your_emby_username_here"
    PASSWORD = "your_emby_password_here"

    eu = EmbyUtil(host=BASE_URL, user_id=USER_ID, username=USERNAME,
                  password=PASSWORD, log=test_log)
    #audio_list = eu.search_music(album="天黑")
    #r = eu.search_music(name="东北民谣", artist="毛不易")
    # audio_list = eu.search_music( artist="游鸿明", years="2001")
    # print(audio_list[0].stream_url)
    # audio_list = eu.search_music( name='看我72变')
    #eu.favorite_audio(1055149)
