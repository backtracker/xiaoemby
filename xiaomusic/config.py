from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from typing import get_type_hints



# 命令匹配字典
command_action_dict = {
    # ================== 1. 系统与基础控制类 ==================
    r"^(?:关机|暂停|停止|闭嘴)$": "stop",
    r"^(?P<minute>.+?)分钟后关机$": "stop_after_minute",
    r"^(?:刷新|更新|重新加载)(?:全部)?(?:播放)?(?:列表|歌单)$": "gen_music_list",
    r"^(?:关闭语音口令|关闭语音指令|关闭口令功能)$": "set_pull_ask_off",
    # 用户别名收藏（需要在普通收藏之前，避免别名被吞掉）
    # 更具体的模式放在前面
    r"^(?P<user_alias>.+?)添加当前(?:歌曲|音乐)到收藏$": "add_to_favorites",
    r"^(?P<user_alias>.+?)添加(?:歌曲|音乐)到收藏$": "add_to_favorites",
    r"^(?P<user_alias>.+?)添加到收藏$": "add_to_favorites",
    r"^(?P<user_alias>.+?)收藏(?:当前)?(?:歌曲|音乐|这(?:首)?(?:歌|歌曲))?$": "add_to_favorites",
    # 普通收藏（使用默认用户）
    r"^(?:收藏(?:当前)?(?:歌曲|音乐|这(?:首)?(?:歌|歌曲))|添加(?:当前)?(?:歌曲|音乐)?到收藏)$": "add_to_favorites",

    r"^(?:播(?:放)?)?下一首$": "play_next",
    r"^(?:播(?:放)?)?上一首$": "play_prev",
    # 今日推荐（必须放在终极兜底匹配之前，否则会被当成普通歌名）
    # 带「我的」的新口令在前：小爱会把裸「播放今日推荐」当成自己的原生技能抢答，
    # 新措辞可降低被抢答的概率；旧口令保留兼容。
    r"^(?:播(?:放)?|来点)我的(?:今日|今天的)?推荐(?:歌曲|音乐|歌单|的歌)?$": "daily_recommend",
    r"^(?:播(?:放)?|来点)(?:今日|今天的)推荐(?:歌曲|音乐|歌单|的歌)?$": "daily_recommend",
    r"^(?:换(?:一批|一拨)|再来一批)(?:今日|今天的)?推荐(?:歌单|歌曲|音乐)?$": "daily_recommend_renew",
    r"^(?:不喜欢|不想要)(?:这首|这)?(?:歌|歌曲|音乐)?$": "snooze_current",
    r"^(?:播(?:放)?|来点)(?:音乐|歌曲|歌)$": "play",
    r"^播(?:放)?列表第(?P<index>\d+|[零一二三四五六七八九十百千万亿]+)首(?:歌(?:曲)?|音乐)?$": "play_music_list_index",

    # ================== 2. 播放复杂句式 (需置于最前) ==================
    # 2.1 喜爱特征（"我喜欢"必须在用户别名之前，避免"我"被当作别名）
    r"^播(?:放)?我(?P<is_favorite>喜欢)的(?P<artist>.+?)的(?:歌(?:曲)?|音乐)?$": "play",
    r"^播(?:放)?我(?P<is_favorite>喜欢)的(?:歌(?:曲)?|音乐)?$": "play",
    # 2.1.1 用户别名喜爱特征（支持用户别名）
    r"^播(?:放)?(?P<user_alias>.+?)(?:喜欢|收藏)的(?P<artist>.+?)的(?:歌(?:曲)?|音乐)?$": "play",
    r"^播(?:放)?(?P<user_alias>.+?)(?:喜欢|收藏)的(?:歌(?:曲)?|音乐)?$": "play",

    # 2.2 专辑里的歌曲
    r"^播(?:放)?(?P<artist>.+?)的(?:专辑)?(?P<album>.+?)(?:专辑)?(?:里的|中的)(?:歌(?:曲)?|音乐)?(?P<name>.+?)(?:歌(?:曲)?|音乐)?$": "play",

    # 2.3 年份特征 (支持4位数字及中文年份)
    r"^播(?:放)?(?P<artist>.+?)(?P<year>\d{4}|[零一二三四五六七八九十百千万亿]{4})年(?:之前|之后)?的(?:歌(?:曲)?|音乐)?$": "play",
    r"^播(?:放)?(?P<year>\d{4}|[零一二三四五六七八九十百千万亿]{4})年(?:之前|之后)?的(?:歌(?:曲)?|音乐)?$": "play",

    # 2.4 风格特征
    r"^播(?:放)?(?P<artist>.+?)的(?P<genre>.+?)风格的(?:歌(?:曲)?|音乐)?$": "play",
    r"^播(?:放)?(?P<genre>.+?)风格的(?:歌(?:曲)?|音乐)?$": "play",

    # 2.5 纯专辑匹配 (无歌曲名)
    r"^播(?:放)?(?P<artist>.+?)的(?P<album>.+?)专辑$": "play",
    r"^播(?:放)?(?P<artist>.+?)的专辑(?P<album>.+?)$": "play",
    r"^播(?:放)?专辑(?P<album>.+?)$": "play",
    r"^播(?:放)?(?P<album>.+?)专辑$": "play",

    # ================== 3. 核心匹配提取区 ==================
    # 3.1 异常歌曲名前置拦截 (防止被后面的 "XX的XX" 将歌名切散)
    r"^播(?:放)?(?P<name>谢谢你的爱1999|一起走过的日子)$": "play",

    # 3.2 带有“歌曲/音乐/歌”等明确后缀或前缀的 (歌手+歌名)
    r"^播(?:放)?(?P<artist>.+?)的(?:歌(?:曲)?|音乐)(?P<name>.+?)$": "play",
    r"^播(?:放)?(?P<artist>.+?)的(?P<name>.+?)(?:歌(?:曲)?|音乐)$": "play",

    # 3.3 仅歌手带有“歌曲/音乐/歌”后缀
    # 【修复点】：必须排在3.4前面，防止“播放周杰伦的歌”中的“周杰伦的”被当成名字吞掉
    r"^播(?:放)?(?P<artist>.+?)的(?:歌(?:曲)?|音乐)$": "play",

    # 3.4 带有“歌曲/音乐/歌”等明确后缀或前缀的 (仅歌名)
    r"^播(?:放)?(?:歌(?:曲)?|音乐)(?P<name>.+?)$": "play",
    r"^播(?:放)?(?P<name>.+?)(?:歌(?:曲)?|音乐)$": "play",

    # ================== 4. 终极兜底匹配 ==================
    # 4.1 歌手 + 歌名常规兜底 (无任何特殊标识)
    r"^播(?:放)?(?P<artist>.+?)的(?P<name>.+?)$": "play",

    # 4.2 终极纯歌名兜底
    r"^播(?:放)?(?P<name>.+?)$": "play",
}
@dataclass
class EmbyUser:
    """Emby用户配置"""
    user_id: str = ""
    alias: str = ""  # 用户别名，用于语音指令匹配
    is_default: bool = False  # 是否为默认用户


@dataclass
class Device:
    did: str = ""
    device_id: str = ""
    hardware: str = ""
    name: str = ""
    play_type: int = 0
    cur_music: str = ""
    cur_playlist: str = ""
    playlist2music: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    account: str = os.getenv("MI_USER", "")
    password: str = os.getenv("MI_PASS", "")
    mi_did: str = os.getenv("MI_DID", "")  # 逗号分割支持多设备
    verbose: bool = os.getenv("XIAOMUSIC_VERBOSE", "").lower() == "true"
    temp_path: str = os.getenv("XIAOMUSIC_TEMP_PATH", "temp")
    conf_path: str = os.getenv("XIAOMUSIC_CONF_PATH", "conf")
    cache_dir: str = os.getenv("XIAOMUSIC_CACHE_DIR", "cache")
    hostname: str = os.getenv("XIAOMUSIC_HOSTNAME", "")
    port: int = int(os.getenv("XIAOMUSIC_PORT", "8090"))  # 监听端口
    public_port: int = int(os.getenv("XIAOMUSIC_PUBLIC_PORT", 58090))  # 歌曲访问端口

    # Emby配置：用户名密码登录换取用户 token，全链路复用
    emby_host: str = os.getenv("XIAOMUSIC_EMBY_HOST", "")
    emby_user_id: str = os.getenv("XIAOMUSIC_EMBY_USER_ID", "")  # 保留兼容旧配置
    emby_username: str = os.getenv("XIAOMUSIC_EMBY_USERNAME", "")
    emby_password: str = os.getenv("XIAOMUSIC_EMBY_PASSWORD", "")
    emby_users: dict[str, EmbyUser] = field(default_factory=dict)  # 多用户配置

    active_cmd: str = os.getenv(
        "XIAOMUSIC_ACTIVE_CMD",
        "play,play_music_list,play_music_list_index,stop_after_minute,stop,play_next,play_prev,gen_music_list,set_pull_ask_off,add_to_favorites,del_from_favorites,online_play,singer_play,daily_recommend,daily_recommend_renew,snooze_current",
    )
    custom_play_list_json: str = os.getenv("XIAOMUSIC_CUSTOM_PLAY_LIST_JSON", "")

    use_music_api: bool = (
        os.getenv("XIAOMUSIC_USE_MUSIC_API", "false").lower() == "true"
    )
    use_music_audio_id: str = os.getenv(
        "XIAOMUSIC_USE_MUSIC_AUDIO_ID", "1582971365183456177"
    )
    log_file: str = os.getenv("XIAOMUSIC_LOG_FILE", "xiaoemby.log")
    # 模糊搜索匹配的最低相似度阈值
    fuzzy_match_cutoff: float = float(os.getenv("XIAOMUSIC_FUZZY_MATCH_CUTOFF", "0.6"))
    # 开启模糊搜索
    enable_fuzzy_match: bool = (
        os.getenv("XIAOMUSIC_ENABLE_FUZZY_MATCH", "true").lower() == "true"
    )
    stop_tts_msg: str = os.getenv("XIAOMUSIC_STOP_TTS_MSG", "收到,再见")
    enable_config_example: bool = False


    enable_force_stop: bool = (
        os.getenv("XIAOMUSIC_ENABLE_FORCE_STOP", "false").lower() == "true"
    )
    devices: dict[str, Device] = field(default_factory=dict)
    group_list: str = os.getenv(
        "XIAOMUSIC_GROUP_LIST", ""
    )  # did1:group_name,did2:group_name

    delay_sec: int = int(os.getenv("XIAOMUSIC_DELAY_SEC", 0))  # 下一首歌延迟播放秒数
    continue_play: bool = (
        os.getenv("XIAOMUSIC_CONTINUE_PLAY", "false").lower() == "true"
    )

    pull_ask_sec: int = int(os.getenv("XIAOMUSIC_PULL_ASK_SEC", "1"))
    enable_pull_ask: bool = (
        os.getenv("XIAOMUSIC_ENABLE_PULL_ASK", "false").lower() == "true"
    )
    crontab_json: str = os.getenv("XIAOMUSIC_CRONTAB_JSON", "")  # 定时任务

    get_ask_by_mina: bool = (
        os.getenv("XIAOMUSIC_GET_ASK_BY_MINA", "false").lower() == "true"
    )

    # edge-tts 语音角色
    edge_tts_voice: str = os.getenv("XIAOMUSIC_EDGE_TTS_VOICE", "zh-CN-XiaoyiNeural")
    # 是否启用定时清理临时文件
    enable_auto_clean_temp: bool = (
        os.getenv("XIAOMUSIC_ENABLE_AUTO_CLEAN_TEMP", "true").lower() == "true"
    )
    # 时区配置，用于定时任务等时间相关功能
    timezone: str = os.getenv("XIAOMUSIC_TIMEZONE", "Asia/Shanghai")

    # ===== 今日推荐（LLM 编排 + 本地选曲）=====
    enable_daily_recommend: bool = (
        os.getenv("XIAOMUSIC_ENABLE_DAILY_RECOMMEND", "false").lower() == "true"
    )
    llm_endpoint: str = os.getenv("XIAOMUSIC_LLM_ENDPOINT", "")
    llm_api_key: str = os.getenv("XIAOMUSIC_LLM_API_KEY", "")
    llm_model: str = os.getenv("XIAOMUSIC_LLM_MODEL", "")
    daily_recommend_count: int = int(
        os.getenv("XIAOMUSIC_DAILY_RECOMMEND_COUNT", "20")
    )
    # LLM 是否支持 response_format=json_schema（DeepSeek 等不支持，默认 false 直接用 json_object）
    llm_supports_schema: bool = (
        os.getenv("XIAOMUSIC_LLM_SUPPORTS_SCHEMA", "false").lower() == "true"
    )
    # LLM 输出预算（max_tokens）。推理模型思考会先吃掉一大截 token，
    # 预算太小会导致 content 为空、recipe 解析失败并回退本地混歌；默认 6000。
    llm_max_tokens: int = int(os.getenv("XIAOMUSIC_LLM_MAX_TOKENS", "6000"))
    # 探索配额比例（discover 意图下留多少比例的「没听过」曲目）
    explore_ratio: float = float(os.getenv("XIAOMUSIC_EXPLORE_RATIO", "0.15"))

    def init(self):
        # 转换数据
        self._active_cmd_arr = self.active_cmd.split(",") if self.active_cmd else []
        self._exclude_dirs_set = set()

    def __post_init__(self) -> None:
        if self.hostname:
            if not self.hostname.startswith(("http://", "https://")):
                self.hostname = f"http://{self.hostname}"  # 默认 http

        # 兼容旧配置：如果emby_users为空但emby_user_id不为空，则迁移到新格式
        if not self.emby_users and self.emby_user_id:
            self.emby_users = {
                "default": EmbyUser(
                    user_id=self.emby_user_id,
                    alias="默认",
                    is_default=True
                )
            }

        self.init()
        # 保存配置到 config-example.json 文件
        if self.enable_config_example:
            with open("config-example.json", "w") as f:
                data = asdict(self)
                json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def from_options(cls, options: argparse.Namespace) -> Config:
        config = {}
        if options.config:
            config = cls.read_from_file(options.config)
        for key, value in vars(options).items():
            if value is not None and key in cls.__dataclass_fields__:
                config[key] = value
        return cls(**config)

    @classmethod
    def convert_value(cls, k, v, type_hints):
        if v is not None and k in type_hints:
            expected_type = type_hints[k]
            try:
                if expected_type is bool:
                    converted_value = False
                    if str(v).lower() == "true":
                        converted_value = True
                elif expected_type == dict[str, Device]:
                    converted_value = {}
                    for kk, vv in v.items():
                        converted_value[kk] = Device(**vv)
                elif expected_type == dict[str, EmbyUser]:
                    converted_value = {}
                    for kk, vv in v.items():
                        converted_value[kk] = EmbyUser(**vv)
                else:
                    converted_value = expected_type(v)
                return converted_value
            except (ValueError, TypeError) as e:
                print(f"Error converting {k}:{v} to {expected_type}: {e}")
        return None

    @classmethod
    def read_from_file(cls, config_path: str) -> dict:
        result = {}
        with open(config_path, "rb") as f:
            data = json.load(f)
            type_hints = get_type_hints(cls)

            for k, v in data.items():
                converted_value = cls.convert_value(k, v, type_hints)
                if converted_value is not None:
                    result[k] = converted_value
        return result

    def update_config(self, data):
        type_hints = get_type_hints(self, globals(), locals())

        for k, v in data.items():
            converted_value = self.convert_value(k, v, type_hints)
            if converted_value is not None:
                setattr(self, k, converted_value)
        self.init()

    def get_active_cmd_arr(self):
        return self._active_cmd_arr

    def get_exclude_dirs_set(self):
        return self._exclude_dirs_set

    # 获取设置文件
    def getsettingfile(self):
        # 兼容旧配置空的情况
        if not self.conf_path:
            self.conf_path = "conf"
        if not os.path.exists(self.conf_path):
            os.makedirs(self.conf_path)
        filename = os.path.join(self.conf_path, "setting.json")
        return filename

    @property
    def tag_cache_path(self):
        if (len(self.cache_dir) > 0) and (not os.path.exists(self.cache_dir)):
            os.makedirs(self.cache_dir)
        filename = os.path.join(self.cache_dir, "tag_cache.json")
        return filename

    @property
    def picture_cache_path(self):
        cache_path = os.path.join(self.cache_dir, "picture_cache")
        if not os.path.exists(cache_path):
            os.makedirs(cache_path)
        return cache_path

    @property
    def temp_dir(self):
        if not os.path.exists(self.temp_path):
            os.makedirs(self.temp_path)
        return self.temp_path



    def get_ignore_tag_dirs(self):
        return []

    def get_one_device_id(self):
        """获取一个设备ID

        Returns:
            str: 第一个设备的device_id，如果没有设备则返回空字符串
        """
        device = next(iter(self.devices.values()), None)
        return device.device_id if device else ""

    def get_self_netloc(self):
        """获取网络地址"""
        host = self.hostname.split("//", 1)[1]
        return f"{host}:{self.public_port}"

    def get_default_emby_user(self) -> EmbyUser | None:
        """获取默认Emby用户"""
        for user in self.emby_users.values():
            if user.is_default:
                return user
        # 如果没有设置默认用户，返回第一个用户
        if self.emby_users:
            return next(iter(self.emby_users.values()))
        return None

    def get_emby_user_by_alias(self, alias: str) -> EmbyUser | None:
        """根据别名获取Emby用户"""
        for user in self.emby_users.values():
            if user.alias == alias:
                return user
        return None

    def get_emby_user_id_by_alias(self, alias: str | None) -> str:
        """根据别名获取Emby用户ID，如果别名为空则返回默认用户ID"""
        if alias:
            user = self.get_emby_user_by_alias(alias)
            if user:
                return user.user_id
        # 如果没有找到或别名为空，返回默认用户ID
        default_user = self.get_default_emby_user()
        if default_user:
            return default_user.user_id
        # 兼容旧配置
        return self.emby_user_id
