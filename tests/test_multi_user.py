"""测试多用户功能"""
import pytest
from xiaomusic.config import Config, EmbyUser


def test_emby_user_creation():
    """测试EmbyUser创建"""
    user = EmbyUser(
        user_id="123456",
        alias="爸爸",
        is_default=True
    )
    assert user.user_id == "123456"
    assert user.alias == "爸爸"
    assert user.is_default is True


def test_config_with_emby_users():
    """测试Config包含多用户配置"""
    config = Config()
    config.emby_users = {
        "user_0": EmbyUser(user_id="123", alias="爸爸", is_default=True),
        "user_1": EmbyUser(user_id="456", alias="妈妈", is_default=False),
    }
    
    assert len(config.emby_users) == 2
    assert config.emby_users["user_0"].alias == "爸爸"
    assert config.emby_users["user_1"].alias == "妈妈"


def test_get_default_emby_user():
    """测试获取默认用户"""
    config = Config()
    config.emby_users = {
        "user_0": EmbyUser(user_id="123", alias="爸爸", is_default=True),
        "user_1": EmbyUser(user_id="456", alias="妈妈", is_default=False),
    }
    
    default_user = config.get_default_emby_user()
    assert default_user is not None
    assert default_user.user_id == "123"
    assert default_user.alias == "爸爸"


def test_get_default_emby_user_no_default():
    """测试没有设置默认用户时返回第一个用户"""
    config = Config()
    config.emby_users = {
        "user_0": EmbyUser(user_id="123", alias="爸爸", is_default=False),
        "user_1": EmbyUser(user_id="456", alias="妈妈", is_default=False),
    }
    
    default_user = config.get_default_emby_user()
    assert default_user is not None
    assert default_user.user_id == "123"


def test_get_emby_user_by_alias():
    """测试根据别名获取用户"""
    config = Config()
    config.emby_users = {
        "user_0": EmbyUser(user_id="123", alias="爸爸", is_default=True),
        "user_1": EmbyUser(user_id="456", alias="妈妈", is_default=False),
    }
    
    user = config.get_emby_user_by_alias("妈妈")
    assert user is not None
    assert user.user_id == "456"
    assert user.alias == "妈妈"


def test_get_emby_user_by_alias_not_found():
    """测试别名不存在时返回None"""
    config = Config()
    config.emby_users = {
        "user_0": EmbyUser(user_id="123", alias="爸爸", is_default=True),
    }
    
    user = config.get_emby_user_by_alias("不存在的别名")
    assert user is None


def test_get_emby_user_id_by_alias():
    """测试根据别名获取用户ID"""
    config = Config()
    config.emby_users = {
        "user_0": EmbyUser(user_id="123", alias="爸爸", is_default=True),
        "user_1": EmbyUser(user_id="456", alias="妈妈", is_default=False),
    }
    
    user_id = config.get_emby_user_id_by_alias("妈妈")
    assert user_id == "456"


def test_get_emby_user_id_by_alias_default():
    """测试别名为空时返回默认用户ID"""
    config = Config()
    config.emby_users = {
        "user_0": EmbyUser(user_id="123", alias="爸爸", is_default=True),
        "user_1": EmbyUser(user_id="456", alias="妈妈", is_default=False),
    }
    
    user_id = config.get_emby_user_id_by_alias(None)
    assert user_id == "123"


def test_get_emby_user_id_by_alias_not_found():
    """测试别名不存在时返回默认用户ID"""
    config = Config()
    config.emby_users = {
        "user_0": EmbyUser(user_id="123", alias="爸爸", is_default=True),
    }
    
    user_id = config.get_emby_user_id_by_alias("不存在的别名")
    assert user_id == "123"


def test_legacy_config_migration():
    """测试旧配置迁移"""
    config = Config()
    config.emby_user_id = "old_user_id"
    config.emby_users = {}
    
    # 模拟__post_init__中的迁移逻辑
    if not config.emby_users and config.emby_user_id:
        config.emby_users = {
            "default": EmbyUser(
                user_id=config.emby_user_id,
                alias="默认",
                is_default=True
            )
        }
    
    assert len(config.emby_users) == 1
    assert config.emby_users["default"].user_id == "old_user_id"
    assert config.emby_users["default"].is_default is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
