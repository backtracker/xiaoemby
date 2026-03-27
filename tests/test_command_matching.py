import re
import pytest
from xiaomusic.config import command_action_dict

# 测试数据：(测试文本, 期望动作, 期望匹配字段)
test_data = [
    # 测试播放我喜欢的歌曲
    ("播放我喜欢的歌曲", "play", {"name": "", "artist": "", "is_favorite": "喜欢", "album": "", "genre": ""}),
    ("播放我喜欢的音乐", "play", {"name": "", "artist": "", "is_favorite": "喜欢", "album": "", "genre": ""}),
    
    # 测试播放我喜欢的歌手的音乐
    ("播放我喜欢的朴树的音乐", "play", {"name": "", "artist": "朴树", "is_favorite": "喜欢", "album": "", "genre": ""}),
    ("播放我喜欢的朴树的歌", "play", {"name": "", "artist": "朴树", "is_favorite": "喜欢", "album": "", "genre": ""}),
    ("播放我喜欢的朴树的歌曲", "play", {"name": "", "artist": "朴树", "is_favorite": "喜欢", "album": "", "genre": ""}),
    
    # 测试播放专辑
    ("播放周杰伦的七里香专辑", "play", {"name": "", "artist": "周杰伦", "is_favorite": "", "album": "七里香", "genre": ""}),
    ("播放专辑七里香", "play", {"name": "", "artist": "", "is_favorite": "", "album": "七里香", "genre": ""}),
    ("播放七里香专辑", "play", {"name": "", "artist": "", "is_favorite": "", "album": "七里香", "genre": ""}),
    
    # 测试播放风格
    ("播放民谣风格的歌曲", "play", {"name": "", "artist": "", "is_favorite": "", "album": "", "genre": "民谣"}),
    ("播放摇滚风格的音乐", "play", {"name": "", "artist": "", "is_favorite": "", "album": "", "genre": "摇滚"}),
    ("播放流行风格的歌曲", "play", {"name": "", "artist": "", "is_favorite": "", "album": "", "genre": "流行"}),
    ("播放古典风格的音乐", "play", {"name": "", "artist": "", "is_favorite": "", "album": "", "genre": "古典"}),
    
    # 测试播放歌曲
    ("播放如愿", "play", {"name": "如愿", "artist": "", "is_favorite": "", "album": "", "genre": ""}),
    ("播放王菲的如愿", "play", {"name": "如愿", "artist": "王菲", "is_favorite": "", "album": "", "genre": ""}),
    ("播放王菲的歌曲如愿", "play", {"name": "如愿", "artist": "王菲", "is_favorite": "", "album": "", "genre": ""}),
    ("播放王菲的如愿歌曲", "play", {"name": "如愿", "artist": "王菲", "is_favorite": "", "album": "", "genre": ""}),
    
    # 测试播放专辑中的歌曲
    ("播放周杰伦的专辑范特西里的歌曲简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "is_favorite": "", "album": "范特西", "genre": ""}),
    ("播放周杰伦的范特西里的简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "is_favorite": "", "album": "范特西", "genre": ""}),
    ("播放周杰伦的范特西专辑里的歌曲简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "is_favorite": "", "album": "范特西", "genre": ""}),
    ("播放周杰伦的专辑范特西里的简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "is_favorite": "", "album": "范特西", "genre": ""}),
    ("播放周杰伦的范特西专辑中的简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "is_favorite": "", "album": "范特西", "genre": ""}),
    ("播放周杰伦的范特西里的歌曲简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "is_favorite": "", "album": "范特西", "genre": ""}),
    ("播放周杰伦的范特西专辑里的简单爱歌曲", "play", {"name": "简单爱", "artist": "周杰伦", "is_favorite": "", "album": "范特西", "genre": ""}),
    
    # 测试播放歌曲的其他形式
    ("播放歌曲简单爱", "play", {"name": "简单爱", "artist": "", "is_favorite": "", "album": "", "genre": ""}),
    ("播放简单爱歌曲", "play", {"name": "简单爱", "artist": "", "is_favorite": "", "album": "", "genre": ""}),
    ("播放周杰伦的简单爱歌曲", "play", {"name": "简单爱", "artist": "周杰伦", "is_favorite": "", "album": "", "genre": ""}),
    ("播放音乐简单爱", "play", {"name": "简单爱", "artist": "", "is_favorite": "", "album": "", "genre": ""}),
    ("播放歌晴天", "play", {"name": "晴天", "artist": "", "is_favorite": "", "album": "", "genre": ""}),
    ("播放歌曲晴天", "play", {"name": "晴天", "artist": "", "is_favorite": "", "album": "", "genre": ""}),
    ("播放周杰伦的歌曲晴天", "play", {"name": "晴天", "artist": "周杰伦", "is_favorite": "", "album": "", "genre": ""}),
    ("播放周杰伦的晴天歌曲", "play", {"name": "晴天", "artist": "周杰伦", "is_favorite": "", "album": "", "genre": ""}),
    
    # 测试播放歌手的风格歌曲
    ("播放许巍的民谣风格的歌曲", "play", {"name": "", "artist": "许巍", "is_favorite": "", "album": "", "genre": "民谣"}),
    ("播放许巍的民谣歌曲", "play", {"name": "民谣", "artist": "许巍", "is_favorite": "", "album": "", "genre": ""}),
]


@pytest.mark.parametrize("test_text, expected_action, expected_fields", test_data)
def test_command_matching(test_text, expected_action, expected_fields):
    """测试命令匹配规则"""
    # 查找匹配的正则表达式
    matched = False
    for pattern, action in command_action_dict.items():
        match = re.match(pattern, test_text)
        if match:
            matched = True
            # 验证动作
            assert action == expected_action, f"期望动作 '{expected_action}', 实际动作 '{action}'"
            
            # 获取匹配的字段
            matched_fields = match.groupdict()
            
            # 验证所有期望的字段
            for field_name, expected_value in expected_fields.items():
                actual_value = matched_fields.get(field_name, "")
                assert actual_value == expected_value, f"字段 '{field_name}' 期望 '{expected_value}', 实际 '{actual_value}'"
            
            # 确保没有额外的匹配字段（除了我们关心的字段）
            for field_name in matched_fields:
                if field_name not in ["name", "artist", "is_favorite", "album", "genre", "index"]:
                    pytest.fail(f"意外匹配的字段: {field_name}")
            
            break
    
    assert matched, f"没有找到匹配的规则: {test_text}"



