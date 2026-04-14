"""配置管理模块

负责配置的加载、保存、更新和管理。
"""

import json
import os
import stat
from dataclasses import asdict


def _fix_file_permissions(filepath):
    """修复文件权限问题
    
    在Windows上，文件可能被设置为隐藏或只读属性，导致写入失败。
    此函数会移除这些属性以确保文件可写。
    
    Args:
        filepath: 文件路径
    """
    if os.path.exists(filepath):
        try:
            # Windows特殊处理：使用Windows API移除隐藏和只读属性
            if os.name == 'nt':
                import ctypes
                from ctypes import wintypes
                
                # Windows API常量
                FILE_ATTRIBUTE_HIDDEN = 0x02
                FILE_ATTRIBUTE_READONLY = 0x01
                FILE_ATTRIBUTE_NORMAL = 0x80
                
                # 获取当前文件属性
                GetFileAttributesW = ctypes.windll.kernel32.GetFileAttributesW
                GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
                GetFileAttributesW.restype = wintypes.DWORD
                
                SetFileAttributesW = ctypes.windll.kernel32.SetFileAttributesW
                SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
                SetFileAttributesW.restype = wintypes.BOOL
                
                attrs = GetFileAttributesW(filepath)
                if attrs != 0xFFFFFFFF:  # INVALID_FILE_ATTRIBUTES
                    # 移除隐藏和只读属性
                    new_attrs = attrs & ~(FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_READONLY)
                    # 如果所有属性都被移除，设置为NORMAL
                    if new_attrs == 0:
                        new_attrs = FILE_ATTRIBUTE_NORMAL
                    SetFileAttributesW(filepath, new_attrs)
            else:
                # Unix/Linux处理：确保文件可写
                os.chmod(filepath, stat.S_IWRITE | stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        except Exception as e:
            # 如果修复失败，不抛出异常，让后续操作正常处理
            pass


class ConfigManager:
    """配置管理类

    负责管理应用配置，包括：
    - 从文件加载配置
    - 保存配置到文件
    - 更新配置
    - 配置变更通知
    """

    def __init__(self, config, log):
        """初始化配置管理器

        Args:
            config: 配置对象
            log: 日志对象
        """
        self.config = config
        self.log = log

    def try_init_setting(self):
        """尝试从设置文件加载配置

        从配置文件中读取设置并更新当前配置。
        如果文件不存在或格式错误，会记录日志但不会抛出异常。
        """
        try:
            filename = self.config.getsettingfile()
            with open(filename, encoding="utf-8") as f:
                data = json.loads(f.read())
                return data
        except FileNotFoundError:
            self.log.info(f"The file {filename} does not exist.")
            return None
        except json.JSONDecodeError:
            self.log.warning(f"The file {filename} contains invalid JSON.")
            return None
        except Exception as e:
            self.log.exception(f"Execption {e}")
            return None

    def do_saveconfig(self, data):
        """配置文件落地

        将配置数据写入文件。

        Args:
            data: 要保存的配置数据（字典格式）
        """
        filename = self.config.getsettingfile()
        
        # 在写入前确保文件可写（修复可能的隐藏/只读属性）
        _fix_file_permissions(filename)
        
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.log.info(f"Configuration saved to {filename}")

    def save_cur_config(self, devices):
        """把当前配置落地

        将当前运行时的配置保存到文件。
        会同步设备配置到 config 对象中。

        Args:
            devices: 设备字典 {did: XiaoMusicDevice}
        """
        # 同步设备配置
        for did in self.config.devices.keys():
            deviceobj = devices.get(did)
            if deviceobj is not None:
                self.config.devices[did] = deviceobj.device

        # 转换为字典并保存
        data = asdict(self.config)
        self.do_saveconfig(data)
        self.log.info("save_cur_config ok")

    def update_config(self, data):
        """更新配置

        从字典数据更新配置对象。

        Args:
            data: 配置数据字典
        """
        # 自动赋值相同字段的配置
        self.config.update_config(data)

    def get_config(self):
        """获取当前配置

        Returns:
            Config: 当前配置对象
        """
        return self.config

    def get_setting_filename(self):
        """获取配置文件路径

        Returns:
            str: 配置文件的完整路径
        """
        return self.config.getsettingfile()
