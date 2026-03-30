#!/usr/bin/env python3
import asyncio
import logging
import os
import re
from logging.handlers import RotatingFileHandler
from xiaomusic.version import __version__
from xiaomusic.auth import AuthManager
from xiaomusic.command_handler import CommandHandler
from xiaomusic.config import Config
from xiaomusic.config_manager import ConfigManager
from xiaomusic.const import PLAY_TYPE_ALL, PLAY_TYPE_ONE, PLAY_TYPE_RND, PLAY_TYPE_SEQ, PLAY_TYPE_SIN
from xiaomusic.conversation import ConversationPoller
from xiaomusic.crontab import Crontab
from xiaomusic.device_manager import DeviceManager
from xiaomusic.events import CONFIG_CHANGED, DEVICE_CONFIG_CHANGED, EventBus
from xiaomusic.music_library import MusicLibrary
from xiaomusic.utils.system_utils import deepcopy_data_no_sensitive_info
from xiaomusic.utils.text_utils import chinese_to_number
from xiaomusic.utils.emby_utils import EmbyUtil


class XiaoMusic:
    def __init__(self, config: Config):
        self.config = config

        # 初始化事件总线
        self.event_bus = EventBus()

        # 初始化认证管理器（延迟初始化部分属性）
        self.auth_manager = None

        # 初始化设备管理器（延迟初始化）
        self.device_manager = None

        self.running_task = []

        # 音乐库管理器（延迟初始化，在配置准备好之后）
        self.music_library = None

        # 命令处理器（延迟初始化，在配置准备好之后）
        self.command_handler = None

        # 配置管理器（延迟初始化）
        self.config_manager = None

        # 初始化配置
        self.init_config()

        self.online_music_service = None

        # 初始化对话轮询器（延迟初始化，在配置和服务准备好之后）
        self.conversation_poller = None

        # 初始化日志
        self.setup_logger()

        # 计划任务
        self.crontab = Crontab(self.log)



        # 初始化配置管理器（在日志准备好之后）
        self.config_manager = ConfigManager(
            config=self.config,
            log=self.log,
        )

        # 尝试从设置里加载配置
        config_data = self.config_manager.try_init_setting()
        if config_data:
            self.update_config_from_setting(config_data)

        # 初始化 EmbyUtil
        try:
            self.emby_util = EmbyUtil(
                host=self.config.emby_host,
                user_id=self.config.emby_user_id,
                api_key=self.config.emby_api_key,
                log=self.log
            )
            self.log.info("EmbyUtil initialized successfully")
        except Exception as e:
            self.log.error(f"Failed to initialize EmbyUtil: {e}")
            self.emby_util = None

        # 初始化音乐库管理器（在配置准备好之后）
        self.music_library = MusicLibrary(
            config=self.config,
            log=self.log,
            event_bus=self.event_bus,
            emby_util=self.emby_util,
        )

        # 启动时重新生成一次播放列表
        self.music_library.gen_all_music_list()



        # 初始化设备管理器（在配置准备好之后）
        self.device_manager = DeviceManager(
            config=self.config,
            log=self.log,
            xiaomusic=self,
        )

        # 初始化认证管理器（在配置和设备管理器准备好之后）
        self.auth_manager = AuthManager(
            config=self.config,
            log=self.log,
            device_manager=self.device_manager,
        )



        # 初始化对话轮询器（在 device_id_did 准备好之后）
        self.conversation_poller = ConversationPoller(
            config=self.config,
            log=self.log,
            auth_manager=self.auth_manager,
            device_manager=self.device_manager,
        )

        # 初始化命令处理器（在所有依赖准备好之后）
        self.command_handler = CommandHandler(
            config=self.config,
            log=self.log,
            xiaomusic_instance=self,
        )

        # 启动统计


        # 订阅配置变更事件
        self.event_bus.subscribe(CONFIG_CHANGED, self.save_cur_config)
        self.event_bus.subscribe(DEVICE_CONFIG_CHANGED, self.save_cur_config)

        debug_config = deepcopy_data_no_sensitive_info(self.config)
        self.log.info(f"Startup OK. {debug_config}")



    def init_config(self):
        # 确保必要的目录存在
        if not os.path.exists(self.config.temp_path):
            os.makedirs(self.config.temp_path)

        if not os.path.exists(self.config.conf_path):
            os.makedirs(self.config.conf_path)

        if not os.path.exists(self.config.cache_dir):
            os.makedirs(self.config.cache_dir)

        self.continue_play = self.config.continue_play

    def setup_logger(self):
        log_format = f"%(asctime)s [{__version__}] [%(levelname)s] %(filename)s:%(lineno)d: %(message)s"
        date_format = "[%Y-%m-%d %H:%M:%S]"
        formatter = logging.Formatter(fmt=log_format, datefmt=date_format)

        self.log = logging.getLogger("xiaomusic")
        self.log.handlers.clear()  # 清除已有的 handlers
        self.log.setLevel(logging.DEBUG if self.config.verbose else logging.INFO)

        # 文件日志处理器
        log_file = self.config.log_file
        log_path = os.path.dirname(log_file)
        if log_path and not os.path.exists(log_path):
            os.makedirs(log_path)
        if os.path.exists(log_file):
            try:
                os.remove(log_file)
            except Exception as e:
                print(f"无法删除旧日志文件: {log_file} {e}")

        file_handler = RotatingFileHandler(
            self.config.log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=1,
            encoding="utf-8",
        )
        file_handler.stream.flush()
        file_handler.setFormatter(formatter)
        self.log.addHandler(file_handler)

        # 控制台日志处理器
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        self.log.addHandler(console_handler)





    async def run_forever(self):
        self.log.info("run_forever start")
        self.crontab.start()
        await self.auth_manager.init_all_data()
        # 启动对话循环，传递回调函数
        await self.conversation_poller.run_conversation_loop(
            self.do_check_cmd, self.reset_timer_when_answer
        )

    # 匹配命令
    async def do_check_cmd(self, did="", query="", ctrl_panel=True, **kwargs):
        """检查并执行命令（委托给 command_handler）"""
        return await self.command_handler.do_check_cmd(did, query, ctrl_panel, **kwargs)

    # 重置计时器
    async def reset_timer_when_answer(self, answer_length, did):
        await self.device_manager.devices[did].reset_timer_when_answer(answer_length)

    def append_running_task(self, task):
        self.running_task.append(task)

    async def cancel_all_tasks(self):
        if len(self.running_task) == 0:
            self.log.info("cancel_all_tasks no task")
            return
        for task in self.running_task:
            self.log.info(f"cancel_all_tasks {task}")
            task.cancel()
        await asyncio.gather(*self.running_task, return_exceptions=True)
        self.running_task = []

    async def is_task_finish(self):
        if len(self.running_task) == 0:
            return True
        task = self.running_task[0]
        if task and task.done():
            return True
        return False

    async def check_replay(self, did):
        return await self.device_manager.devices[did].check_replay()

    def did_exist(self, did):
        return did in self.device_manager.devices

    # 播放一个 url
    async def play_url(self, did="", arg1="", **kwargs):
        self.log.info(f"手动推送链接：{arg1}")
        url = arg1
        return await self.device_manager.devices[did].group_player_play(url)

    # 口令:单曲循环
    async def set_play_type_one(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_ONE)

    # 口令:全部循环
    async def set_play_type_all(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_ALL)

    # 口令:随机播放
    async def set_play_type_rnd(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_RND)

    # 口令:单曲播放
    async def set_play_type_sin(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_SIN)

    # 口令:顺序播放
    async def set_play_type_seq(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_SEQ)

    async def set_play_type(self, did="", play_type=PLAY_TYPE_RND, dotts=True):
        await self.device_manager.devices[did].set_play_type(play_type, dotts)

    # 口令:刷新列表
    async def gen_music_list(self, **kwargs):
        self.music_library.gen_all_music_list()
        self.update_all_playlist()
        self.log.info("gen_music_list ok")


    # ===========================================================

    def _find_real_music_list_name(self, list_name):
        """模糊搜索播放列表名称（委托给 music_library）"""
        return self.music_library.find_real_music_list_name(list_name)

    # 口令:播放歌单
    async def play_music_list(self, did="", arg1="", **kwargs):
        parts = arg1.split("|")
        list_name = parts[0]

        music_name = ""
        if len(parts) > 1:
            music_name = parts[1]
        return await self.do_play_music_list(did, list_name, music_name)

    async def do_play_music_list(self, did, list_name, music_name=""):
        # 查找并获取真实的音乐列表名称
        list_name = self._find_real_music_list_name(list_name)
        # 检查音乐列表是否存在，如果不存在则进行语音提示并返回
        if list_name not in self.music_library.music_list:
            await self.do_tts(did, f"播放列表{list_name}不存在")
            return

        # 调用设备播放音乐列表的方法
        await self.device_manager.devices[did].play_music_list(list_name, music_name)

    # 口令:播放列表第
    async def play_music_list_index(self, did="", arg1="", index=None, **kwargs):
        if index is not None:
            # 使用正则匹配到的数字索引
            try:
                index = int(index)
            except ValueError:
                # 如果转换失败，尝试中文数字转换
                index = chinese_to_number(str(index))
            
            # 提取列表名
            patternarg = r"^播放列表第\d+首(.*)"
            matcharg = re.match(patternarg, arg1)
            list_name = matcharg.groups()[0] if matcharg else ""
            list_name = self._find_real_music_list_name(list_name)
        else:
            # 传统匹配方式
            patternarg = r"^([零一二三四五六七八九十百千万亿]+)个(.*)"
            # 匹配参数
            matcharg = re.match(patternarg, arg1)
            if not matcharg:
                return await self.play_music_list(did, arg1)

            chinese_index = matcharg.groups()[0]
            list_name = matcharg.groups()[1]
            list_name = self._find_real_music_list_name(list_name)
            index = chinese_to_number(chinese_index)
        
        if list_name not in self.music_library.music_list:
            await self.do_tts(did, f"播放列表{list_name}不存在")
            return
        play_list = self.music_library.music_list[list_name]
        if 0 <= index - 1 < len(play_list):
            music_name = play_list[index - 1]
            self.log.info(f"即将播放 ${arg1} 里的第 ${index} 个: ${music_name}")
            await self.device_manager.devices[did].play_music_list(
                list_name, music_name
            )
            return
        await self.do_tts(did, f"播放列表{list_name}中找不到第${index}个")

    # 口令:播放歌曲
    async def play(self, did="", arg1="", **kwargs):
        # 从 kwargs 中获取匹配到的参数
        name = kwargs.get("name", "")
        artist = kwargs.get("artist", "")
        album = kwargs.get("album", "")
        genre = kwargs.get("genre", "")
        is_favorite = kwargs.get("is_favorite", None)
        year = kwargs.get("year", None)
        
        # 转换中文数字的年份为阿拉伯数字
        if year:
            # 检查是否是中文数字（包含中文数字字符）
            if any(char in "零一二三四五六七八九十百千万亿" for char in year):
                try:
                    from xiaomusic.utils.text_utils import chinese_to_year
                    year = str(chinese_to_year(year))
                except Exception as e:
                    self.log.error(f"转换中文年份失败: {e}")
                    year = None
        # 初始化年份相关参数
        years = None
        min_premiere_date = None
        max_premiere_date = None
        # 判断是否是年份之前或之后的命令
        if year and "之前" in arg1:
            # 年份之前的命令，设置 max_premiere_date 为年份的1月1日
            max_premiere_date = f"{year}-01-01"
        elif year and "之后" in arg1:
            # 年份之后的命令，设置 min_premiere_date 为年份的1月1日
            min_premiere_date = f"{year}-01-01"
        elif year:
            # 确切年份的命令
            years = year
        
        # 如果没有匹配到参数，使用传统方式解析
        if not any([name, artist, album, genre, is_favorite, year]):
            parts = arg1.split("|")
            search_key = parts[0]
            name = parts[1] if len(parts) > 1 else search_key
            if not name:
                name = search_key
            
            # 如果是简单的"播放音乐"或"来点音乐"，不设置name参数
            if name in ["播放音乐", "来点音乐", "播放歌曲", "来点歌曲"]:
                name = ""

        # 使用 EmbyUtil 获取音乐
        if self.emby_util:
            try:
                # 当name为空时，传递None给search_music方法
                emby_name = name if name else None
                self.log.info(f"使用 Emby 搜索音乐: name={emby_name}, artist={artist}, album={album}, genre={genre}, is_favorite={is_favorite}, years={years}, min_premiere_date={min_premiere_date}, max_premiere_date={max_premiere_date}")
                # 使用 asyncio.to_thread 运行同步方法
                audio_list = await asyncio.to_thread(
                    self.emby_util.search_music,
                    name=emby_name,
                    artist=artist,
                    album=album,
                    genre=genre,
                    is_favorite=is_favorite,
                    years=years,
                    min_premiere_date=min_premiere_date,
                    max_premiere_date=max_premiere_date
                )
                
                if audio_list:
                    # 构建播放列表
                    playlist_name = f"Emby音乐"
                    music_list = []
                    # 直接将Audio对象存储到all_music字典中
                    for audio in audio_list:
                        music_name = f"{audio.name}"
                        self.music_library.all_music[music_name] = audio
                        music_item = {
                            "name": music_name,
                            "url": audio.stream_url,
                            "type": "emby",
                            "duration": audio.duration
                        }
                        music_list.append(music_item)
                    
                    # 更新音乐库
                    self.music_library.update_music_list_json(playlist_name, music_list)
                    
                    # 播放音乐
                    await self.do_play_music_list(did, playlist_name, music_list[0]["name"])
                    return
                else:
                    # Emby搜索未找到结果，返回
                    self.log.info(f"Emby 未找到匹配的音乐")
                    return
            except Exception as e:
                import traceback
                self.log.error(f"Emby 搜索音乐失败: {e}")
                self.log.error(f"详细错误信息: {traceback.format_exc()}")
                # Emby搜索失败，返回
                return
        
        # 如果 EmbyUtil 不可用，使用传统方式
        # 根据参数构建搜索关键词
        search_key = arg1
        if artist:
            search_key = f"{artist}的歌"
        elif album:
            search_key = f"{album}专辑"
        elif genre:
            search_key = f"{genre}风格的歌"
        return await self.do_play(did, name, search_key)

    # 网页面板搜索播放
    async def do_play(self, did, name, search_key=""):
        return await self.device_manager.devices[did].play(name, search_key)



    # 口令:下一首
    async def play_next(self, did="", **kwargs):
        return await self.device_manager.devices[did].play_next()

    # 口令:上一首
    async def play_prev(self, did="", **kwargs):
        return await self.device_manager.devices[did].play_prev()

    # 口令:停止
    async def stop(self, did="", arg1="", **kwargs):
        return await self.device_manager.devices[did].stop(arg1=arg1)

    # 口令:分钟后关机
    async def stop_after_minute(self, did="", arg1=0, minute=None, **kwargs):
        try:
            if minute is not None:
                # 使用正则匹配到的分钟数
                minute = int(minute)
            else:
                # 尝试阿拉伯数字转换中文数字
                minute = int(arg1)
        except (KeyError, ValueError):
            # 如果阿拉伯数字转换失败，尝试中文数字
            minute_str = minute if minute is not None else str(arg1)
            minute = chinese_to_number(str(minute_str))
        return await self.device_manager.devices[did].stop_after_minute(minute)



    # 更新每个设备的歌单
    def update_all_playlist(self):
        """更新每个设备的歌单"""
        for device in self.device_manager.devices.values():
            device.update_playlist()

    # 获取音量
    async def get_volume(self, did="", **kwargs):
        return await self.device_manager.devices[did].get_volume()

    # 获取完整播放状态
    async def get_player_status(self, did="", **kwargs):
        return await self.device_manager.devices[did].get_player_status()

    # 设置音量
    async def set_volume(self, did="", arg1=0, **kwargs):
        if did not in self.device_manager.devices:
            self.log.info(f"设备 did:{did} 不存在, 不能设置音量")
            return
        volume = int(arg1)
        return await self.device_manager.devices[did].set_volume(volume)

    # 获取当前的播放列表
    def get_cur_play_list(self, did):
        return self.device_manager.devices[did].get_cur_play_list()

    # 正在播放中的音乐
    def playingmusic(self, did):
        cur_music = self.device_manager.devices[did].get_cur_music()
        self.log.debug(f"playingmusic. cur_music:{cur_music}")
        return cur_music

    def get_offset_duration(self, did):
        return self.device_manager.devices[did].get_offset_duration()

    # 当前是否正在播放歌曲
    def isplaying(self, did):
        return self.device_manager.devices[did].is_playing

    # 获取当前配置
    def getconfig(self):
        """获取当前配置（委托给 config_manager）"""
        return self.config_manager.get_config()

    # 保存配置并重新启动
    async def saveconfig(self, data):
        """保存配置并重新启动"""
        # 更新配置
        self.update_config_from_setting(data)
        # 配置文件落地
        self.save_cur_config()
        # 重新初始化
        await self.reinit()

    # 把当前配置落地
    def save_cur_config(self):
        """把当前配置落地（委托给 config_manager）"""
        self.config_manager.save_cur_config(self.device_manager.devices)

    def update_config_from_setting(self, data):
        """从设置更新配置"""
        # 委托给 config_manager 更新配置
        self.config_manager.update_config(data)

        # 重新初始化配置相关的属性
        self.init_config()

        debug_config = deepcopy_data_no_sensitive_info(self.config)
        self.log.info(f"update_config_from_setting ok. data:{debug_config}")

        self.log.info("语音控制已启动, 支持播放、暂停、停止、下一首、上一首等命令")
        self.log.debug(f"key_word_dict: {self.config.key_word_dict}")



        # 重新加载计划任务
        self.crontab.reload_config(self)

    # 重新初始化
    async def reinit(self):
        for handler in self.log.handlers:
            handler.close()
        self.setup_logger()
        await self.auth_manager.init_all_data()
        self.music_library.gen_all_music_list()
        self.update_all_playlist()

        debug_config = deepcopy_data_no_sensitive_info(self.config)
        self.log.info(f"reinit success. data:{debug_config}")

    # 获取所有设备
    async def getalldevices(self, **kwargs):
        device_list = []
        try:
            device_list = await self.auth_manager.mina_service.device_list()
        except Exception as e:
            self.log.warning(f"Execption {e}")
            # 重新初始化
            await self.reinit()
        return device_list

    async def debug_play_by_music_url(self, arg1=None):
        if arg1 is None:
            arg1 = {}
        data = arg1
        device_id = self.config.get_one_device_id()
        self.log.info(f"debug_play_by_music_url: {data} {device_id}")
        return await self.auth_manager.mina_service.ubus_request(
            device_id,
            "player_play_music",
            "mediaplayer",
            data,
        )



    async def do_tts(self, did, value):
        return await self.device_manager.devices[did].do_tts(value)
