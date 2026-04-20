"""命令处理模块

负责语音指令的解析、匹配和路由。
"""

import asyncio
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xiaomusic.xiaomusic import XiaoMusic
from xiaomusic.config import command_action_dict

if TYPE_CHECKING:
    pass


class CommandHandler:
    """命令处理器

    负责解析用户的语音指令，匹配对应的命令，并路由到相应的处理方法。
    """

    def __init__(self, config, log, xiaomusic_instance: "XiaoMusic"):
        """初始化命令处理器

        Args:
            config: 配置对象
            log: 日志对象
            xiaomusic_instance: XiaoMusic 主类实例，用于调用命令执行方法
        """
        self.config = config
        self.log = log
        self.xiaomusic = xiaomusic_instance
        self.last_cmd = ""

    async def do_check_cmd(self, did="", query="", ctrl_panel=True, **kwargs):
        """检查并执行命令
        这是命令处理的入口方法，负责：
        1. 记录命令
        2. 匹配命令
        3. 执行对应的方法
        4. 处理未匹配的情况

        Args:
            did: 设备ID
            query: 用户查询/命令
            ctrl_panel: 是否来自控制面板
            **kwargs: 其他参数
        """
        self.log.info(f"收到消息:{query} 控制面板:{ctrl_panel} did:{did}")

        # 记录最后一条命令
        self.last_cmd = query

        try:
            device = self.xiaomusic.device_manager.devices[did]
            # 匹配命令
            opvalue, oparg, match_groups = self.match_cmd(device, query, ctrl_panel)
            if not opvalue:
                # 未匹配到命令，等待后检查是否需要重播
                await asyncio.sleep(1)
                await device.check_replay()
                return

            # 执行命令
            func = getattr(self.xiaomusic, opvalue)
            if (opvalue == "play" or opvalue == "stop_after_minute" or opvalue == "play_music_list_index" or opvalue == "add_to_favorites") and match_groups:
                # 读取替换规则
                import json
                import os
                replace_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "conf", "replace.json")
                replace_rules = {}
                if os.path.exists(replace_file):
                    try:
                        with open(replace_file, "r", encoding="utf-8") as f:
                            replace_rules = json.load(f)
                    except Exception as e:
                        self.log.error(f"读取 replace.json 失败: {e}")
                
                # 替换匹配到的参数
                modified_groups = {}
                for key, value in match_groups.items():
                    if value and key in ["artist", "genre"]:
                        # 应用替换规则
                        for old, new in replace_rules.items():
                            if value == old:
                                modified_groups[key] = new
                                self.log.info(f"替换 {key}: {old} -> {new}")
                                break
                        else:
                            modified_groups[key] = value
                    else:
                        modified_groups[key] = value
                
                # 处理用户别名：如果匹配到user_alias，则设置is_favorite并移除user_alias（仅用于play命令）
                if opvalue == "play" and "user_alias" in modified_groups and modified_groups["user_alias"]:
                    user_alias = modified_groups.pop("user_alias")
                    # 检查是否是有效的用户别名
                    user = self.config.get_emby_user_by_alias(user_alias)
                    if user:
                        modified_groups["is_favorite"] = True
                        modified_groups["emby_user_id"] = user.user_id
                        self.log.info(f"匹配到用户别名: {user_alias} -> user_id: {user.user_id}")
                    else:
                        self.log.warning(f"未找到用户别名: {user_alias}，使用默认用户")
                
                # 处理"我喜欢"的情况：没有user_alias但有is_favorite时，确定使用哪个用户（仅用于play命令）
                if opvalue == "play" and "is_favorite" in modified_groups and modified_groups["is_favorite"] and "emby_user_id" not in modified_groups:
                    # 如果配置了多用户，使用默认用户
                    if self.config.emby_users:
                        default_user = self.config.get_default_emby_user()
                        if default_user:
                            modified_groups["emby_user_id"] = default_user.user_id
                            self.log.info(f"使用默认用户: {default_user.alias} -> user_id: {default_user.user_id}")
                    else:
                        # 没有配置多用户，使用单独配置的emby_user_id
                        if self.config.emby_user_id:
                            modified_groups["emby_user_id"] = self.config.emby_user_id
                            self.log.info(f"使用单独配置的emby_user_id: {self.config.emby_user_id}")
                
                # 传递修改后的参数
                await func(did=did, arg1=oparg, **modified_groups)
            else:
                await func(did=did, arg1=oparg)

        except Exception as e:
            self.log.exception(f"Execption {e}")

    def match_cmd(self, device, query, ctrl_panel):
        """匹配命令

        根据用户输入的查询字符串，匹配对应的命令和参数。

        匹配策略：
        1. 使用 command_action_dict 中的正则表达式进行匹配
        2. 提取匹配到的参数
        3. 检查是否在激活命令列表中

        Args:
            device: 设备
            query: 用户查询字符串
            ctrl_panel: 是否来自控制面板

        Returns:
            tuple: (命令值, 命令参数, 匹配到的组)，未匹配返回 (None, None, None)
        """
        # 使用 command_action_dict 进行正则匹配
        for pattern, action in command_action_dict.items():
            match = re.match(pattern, query)
            if match:
                # 检查是否在激活命令中
                active_cmd_arr = self.config.get_active_cmd_arr()
                # 管理命令不受激活命令列表限制
                management_commands = ['set_pull_ask_off']
                if (
                    not ctrl_panel
                    and not device.is_playing
                    and active_cmd_arr
                    and action not in active_cmd_arr
                    and action not in management_commands
                ):
                    self.log.info(f"不在激活命令中 {action}")
                    continue

                self.log.info(f"匹配到指令. pattern:{pattern} action:{action} groups:{match.groupdict()}")
                return action, query, match.groupdict()

        self.log.info(f"未匹配到指令 {query} {ctrl_panel}")
        return None, None, None
