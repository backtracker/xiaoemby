import requests
import os
from typing import List, Optional
import urllib.parse


class Audio:
    """音频类，用于存储音乐信息"""
    def __init__(self, name="", id="", index=0, duration=0, album="", album_id="", 
                 album_artist="", artist_items=None, album_artists=None, is_favorite=False):
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
        self.container = "flac"
        self.file_path = ""
        self.stream_url = ""
        self.type = "emby"
    
    def __str__(self):
        return f"Audio(name='{self.name}', album='{self.album}', album_artist='{self.album_artist}', duration={self.duration}, container='{self.container}',index={self.index},is_favorite={self.is_favorite})"


class EmbyUtil:
    def __init__(self, host, user_id, api_key, log):
        self.host = host
        self.user_id = user_id
        self.api_key = api_key
        self.log = log

    def __set_audio_container(self, audio: Audio):
        """
        设置音频流 container 和 file_path
        """
        url = f"{self.host}/Items/{audio.id}/PlaybackInfo?api_key={self.api_key}"
        response = requests.get(url)
        if response.status_code == 200:
            c = "mp3"
            try:
                media_sources = response.json().get("MediaSources", [])
                if media_sources and len(media_sources) > 0:
                    path = media_sources[0].get("Path", "")
                    audio.file_path = path
                    if path:
                        c = os.path.splitext(path)[1].replace(".", "")
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
        api_key_encoded = urllib.parse.quote(self.api_key)
        stream_url = f"/emby/Audio/{audio.id}/stream?Container={container_encoded}&api_key={api_key_encoded}"
        audio.stream_url = f"{self.host}{stream_url}"

    def search_music(self, name=None, artist=None, genre=None, is_favorite: bool = None, album=None, years=None, min_premiere_date=None, max_premiere_date=None, limit=50) -> List[
        Audio]:
        audio_list = []  # 音乐列表
        is_favorite = bool(is_favorite)
        all_locals = locals()
        self.log.info("Emby查询参数：{}".format(all_locals))
        url = "{}/emby/Users/{}/Items".format(self.host, self.user_id)

        payload = {
            "Recursive": True,
            "MediaTypes": "Audio",
            "Limit": limit,  # 总是获取多首歌曲
            "SortBy": "Random",
            "api_key": self.api_key
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

        print(payload)
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
                              is_favorite=is_favorite)
                audio_list.append(audio)

            # 播放专辑的话根据索引排序
            if album is not None:
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


if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.INFO)
    test_log = logging.getLogger("test_emby_util")
    
    BASE_URL = "http://127.0.0.1:48096"
    API_KEY = "your_emby_api_key_here"
    USER_ID = "your_emby_user_id_here"

    eu = EmbyUtil(host=BASE_URL, user_id=USER_ID, api_key=API_KEY, log=test_log)
    #audio_list = eu.search_music(album="天黑")
    #r = eu.search_music(name="东北民谣", artist="毛不易")
    r = eu.search_music( artist="游鸿明", years="2001")
    print(r[0].stream_url)
