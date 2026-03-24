"""依赖注入和认证相关功能"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from xiaomusic.config import Config
    from xiaomusic.xiaomusic import XiaoMusic


class _AppStateProxy:
    """应用状态代理类

    提供类似全局变量的访问方式，但实际上是动态获取的。
    这样既保持了代码的简洁性，又避免了真正的全局变量。
    """

    def __init__(self):
        self._xiaomusic: XiaoMusic | None = None
        self._config: Config | None = None
        self._log: logging.Logger | None = None

    def initialize(self, xiaomusic_instance: "XiaoMusic"):
        """初始化应用状态

        Args:
            xiaomusic_instance: XiaoMusic 实例
        """
        self._xiaomusic = xiaomusic_instance
        self._config = xiaomusic_instance.config
        self._log = xiaomusic_instance.log

    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        return self._xiaomusic is not None


# 创建内部状态管理器
_state = _AppStateProxy()


class _LazyProxy:
    """延迟代理类，用于模拟全局变量"""

    def __init__(self, attr_name: str):
        self._attr_name = attr_name

    def __getattr__(self, name):
        """代理所有属性访问"""
        obj = getattr(_state, self._attr_name)
        if obj is None:
            raise RuntimeError(
                f"{self._attr_name} not initialized. Call initialize() first."
            )
        return getattr(obj, name)

    def __call__(self, *args, **kwargs):
        """代理函数调用"""
        obj = getattr(_state, self._attr_name)
        if obj is None:
            raise RuntimeError(
                f"{self._attr_name} not initialized. Call initialize() first."
            )
        return obj(*args, **kwargs)

    def __bool__(self):
        """支持布尔判断"""
        obj = getattr(_state, self._attr_name)
        return obj is not None and bool(obj)

    def __repr__(self):
        obj = getattr(_state, self._attr_name)
        return repr(obj) if obj is not None else f"<Uninitialized {self._attr_name}>"


# 创建代理对象，可以像普通变量一样使用
# 添加类型注解以支持 IDE 代码跳转和补全
xiaomusic: "XiaoMusic" = _LazyProxy("_xiaomusic")  # type: ignore
config: "Config" = _LazyProxy("_config")  # type: ignore
log: "logging.Logger" = _LazyProxy("_log")  # type: ignore
