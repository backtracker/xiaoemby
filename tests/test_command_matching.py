import re
import pytest
from xiaomusic.config import command_action_dict

# 测试数据：(测试文本, 期望动作, 期望匹配字段)
test_data = [
    # 测试播放用户别名喜欢的歌曲（新增多用户功能）
    ("播放爸爸喜欢的歌", "play", {"user_alias": "爸爸"}),
    ("播放妈妈喜欢的歌曲", "play", {"user_alias": "妈妈"}),
    ("播放小明收藏的音乐", "play", {"user_alias": "小明"}),
    ("播爸爸喜欢的歌", "play", {"user_alias": "爸爸"}),
    ("播妈妈收藏的歌曲", "play", {"user_alias": "妈妈"}),
    
    # 测试播放用户别名喜欢的歌手的音乐
    ("播放爸爸喜欢的周杰伦的歌", "play", {"user_alias": "爸爸", "artist": "周杰伦"}),
    ("播放妈妈收藏的邓丽君的歌曲", "play", {"user_alias": "妈妈", "artist": "邓丽君"}),
    ("播放小明喜欢的朴树的音乐", "play", {"user_alias": "小明", "artist": "朴树"}),
    ("播爸爸喜欢的周杰伦的歌", "play", {"user_alias": "爸爸", "artist": "周杰伦"}),
    ("播妈妈收藏的邓丽君的歌曲", "play", {"user_alias": "妈妈", "artist": "邓丽君"}),
    
    # 测试播放我喜欢的歌曲（使用默认用户）
    ("播放我喜欢的歌曲", "play", {"is_favorite": "喜欢"}),
    ("播放我喜欢的音乐", "play", {"is_favorite": "喜欢"}),
    ("播我喜欢的歌曲", "play", {"is_favorite": "喜欢"}),
    ("播我喜欢的音乐", "play", {"is_favorite": "喜欢"}),

    # 测试播放我喜欢的歌手的音乐
    ("播放我喜欢的朴树的音乐", "play", {"artist": "朴树", "is_favorite": "喜欢"}),
    ("播放我喜欢的朴树的歌", "play", {"artist": "朴树", "is_favorite": "喜欢"}),
    ("播放我喜欢的朴树的歌曲", "play", {"artist": "朴树", "is_favorite": "喜欢"}),
    ("播我喜欢的朴树的音乐", "play", {"artist": "朴树", "is_favorite": "喜欢"}),
    ("播我喜欢的朴树的歌", "play", {"artist": "朴树", "is_favorite": "喜欢"}),
    ("播我喜欢的朴树的歌曲", "play", {"artist": "朴树", "is_favorite": "喜欢"}),
    
    # 测试播放专辑
    ("播放周杰伦的七里香专辑", "play", {"artist": "周杰伦", "album": "七里香"}),
    ("播放专辑七里香", "play", {"album": "七里香"}),
    ("播放七里香专辑", "play", {"album": "七里香"}),
    ("播周杰伦的七里香专辑", "play", {"artist": "周杰伦", "album": "七里香"}),
    ("播专辑七里香", "play", {"album": "七里香"}),
    ("播七里香专辑", "play", {"album": "七里香"}),
    
    # 测试播放风格
    ("播放民谣风格的歌曲", "play", {"genre": "民谣"}),
    ("播放摇滚风格的音乐", "play", {"genre": "摇滚"}),
    ("播放流行风格的歌曲", "play", {"genre": "流行"}),
    ("播放古典风格的音乐", "play", {"genre": "古典"}),
    ("播民谣风格的歌曲", "play", {"genre": "民谣"}),
    ("播摇滚风格的音乐", "play", {"genre": "摇滚"}),
    ("播流行风格的歌曲", "play", {"genre": "流行"}),
    ("播古典风格的音乐", "play", {"genre": "古典"}),
    
    # 测试播放歌曲
    ("播放如愿", "play", {"name": "如愿"}),
    ("播放王菲的如愿", "play", {"name": "如愿", "artist": "王菲"}),
    ("播放王菲的歌曲如愿", "play", {"name": "如愿", "artist": "王菲"}),
    ("播放王菲的如愿歌曲", "play", {"name": "如愿", "artist": "王菲"}),
    ("播如愿", "play", {"name": "如愿"}),
    ("播王菲的如愿", "play", {"name": "如愿", "artist": "王菲"}),
    ("播王菲的歌曲如愿", "play", {"name": "如愿", "artist": "王菲"}),
    ("播王菲的如愿歌曲", "play", {"name": "如愿", "artist": "王菲"}),
    
    # 测试播放专辑中的歌曲
    ("播放周杰伦的专辑范特西里的歌曲简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "album": "范特西"}),
    ("播放周杰伦的范特西里的简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "album": "范特西"}),
    ("播放周杰伦的范特西专辑里的歌曲简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "album": "范特西"}),
    ("播放周杰伦的专辑范特西里的简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "album": "范特西"}),
    ("播放周杰伦的范特西专辑中的简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "album": "范特西"}),
    ("播放周杰伦的范特西里的歌曲简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "album": "范特西"}),
    ("播放周杰伦的范特西专辑里的简单爱歌曲", "play", {"name": "简单爱", "artist": "周杰伦", "album": "范特西"}),
    ("播周杰伦的专辑范特西里的歌曲简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "album": "范特西"}),
    ("播周杰伦的范特西里的简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "album": "范特西"}),
    ("播周杰伦的范特西专辑里的歌曲简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "album": "范特西"}),
    ("播周杰伦的专辑范特西里的简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "album": "范特西"}),
    ("播周杰伦的范特西专辑中的简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "album": "范特西"}),
    ("播周杰伦的范特西里的歌曲简单爱", "play", {"name": "简单爱", "artist": "周杰伦", "album": "范特西"}),
    ("播周杰伦的范特西专辑里的简单爱歌曲", "play", {"name": "简单爱", "artist": "周杰伦", "album": "范特西"}),
    
    # 测试播放歌曲的其他形式
    ("播放歌曲简单爱", "play", {"name": "简单爱"}),
    ("播放简单爱歌曲", "play", {"name": "简单爱"}),
    ("播放周杰伦的简单爱歌曲", "play", {"name": "简单爱", "artist": "周杰伦"}),
    ("播放音乐简单爱", "play", {"name": "简单爱"}),
    ("播放歌晴天", "play", {"name": "晴天"}),
    ("播放歌曲晴天", "play", {"name": "晴天"}),
    ("播放周杰伦的歌曲晴天", "play", {"name": "晴天", "artist": "周杰伦"}),
    ("播放周杰伦的晴天歌曲", "play", {"name": "晴天", "artist": "周杰伦"}),
    
    ("播歌曲简单爱", "play", {"name": "简单爱"}),
    ("播简单爱歌曲", "play", {"name": "简单爱"}),
    ("播周杰伦的简单爱歌曲", "play", {"name": "简单爱", "artist": "周杰伦"}),
    ("播音乐简单爱", "play", {"name": "简单爱"}),
    ("播歌晴天", "play", {"name": "晴天"}),
    ("播歌曲晴天", "play", {"name": "晴天"}),
    ("播周杰伦的歌曲晴天", "play", {"name": "晴天", "artist": "周杰伦"}),
    ("播周杰伦的晴天歌曲", "play", {"name": "晴天", "artist": "周杰伦"}),
    
    # 测试播放歌手的风格歌曲
    ("播放许巍的民谣风格的歌曲", "play", {"artist": "许巍", "genre": "民谣"}),
    ("播许巍的民谣风格的歌曲", "play", {"artist": "许巍", "genre": "民谣"}),
    
    # 测试播放年份相关的歌曲
    ("播放1999年的歌", "play", {"year": "1999"}),
    ("播放周杰伦2006年的歌", "play", {"artist": "周杰伦", "year": "2006"}),
    ("播放周杰伦2010年之前的歌", "play", {"artist": "周杰伦", "year": "2010"}),
    ("播放任贤齐2012年之后的歌", "play", {"artist": "任贤齐", "year": "2012"}),
    ("播放1999年之前的歌", "play", {"year": "1999"}),
    ("播放2000年之后的歌", "play", {"year": "2000"}),
    ("播1999年的歌", "play", {"year": "1999"}),
    ("播周杰伦2006年的歌", "play", {"artist": "周杰伦", "year": "2006"}),
    ("播周杰伦2010年之前的歌", "play", {"artist": "周杰伦", "year": "2010"}),
    ("播任贤齐2012年之后的歌", "play", {"artist": "任贤齐", "year": "2012"}),
    ("播1999年之前的歌", "play", {"year": "1999"}),
    ("播2000年之后的歌", "play", {"year": "2000"}),
    
    # 测试播放中文数字年份相关的歌曲
    ("播放二零一零年的歌", "play", {"year": "二零一零"}),
    ("播放周杰伦二零一零年的歌", "play", {"artist": "周杰伦", "year": "二零一零"}),
    ("播放周杰伦二零一零年之前的歌", "play", {"artist": "周杰伦", "year": "二零一零"}),
    ("播放任贤齐二零一二年之后的歌", "play", {"artist": "任贤齐", "year": "二零一二"}),
    ("播放二零零八年之前的歌", "play", {"year": "二零零八"}),
    ("播放二零一五年之后的歌", "play", {"year": "二零一五"}),
    ("播二零一零年的歌", "play", {"year": "二零一零"}),
    ("播周杰伦二零一零年的歌", "play", {"artist": "周杰伦", "year": "二零一零"}),
    ("播周杰伦二零一零年之前的歌", "play", {"artist": "周杰伦", "year": "二零一零"}),
    ("播任贤齐二零一二年之后的歌", "play", {"artist": "任贤齐", "year": "二零一二"}),
    ("播二零零八年之前的歌", "play", {"year": "二零零八"}),
    ("播二零一五年之后的歌", "play", {"year": "二零一五"}),

    # 歌曲名称中包含”的“的歌曲
    ("播放谢谢你的爱1999", "play", {"name": "谢谢你的爱1999"}),
    ("播一起走过的日子", "play", {"name": "一起走过的日子"}),
    ("播放歌曲的士司机", "play", {"name": "的士司机"}),
    ("播放歌曲生命中的精灵", "play", {"name": "生命中的精灵"}),
    ("播放歌曲我最亲爱的", "play", {"name": "我最亲爱的"}),
    
    # 测试播放歌手的所有歌曲
    ("播放周杰伦的歌", "play", {"artist": "周杰伦"}),
    ("播放周杰伦的歌曲", "play", {"artist": "周杰伦"}),
    ("播放周杰伦的音乐", "play", {"artist": "周杰伦"}),
    ("播周杰伦的歌", "play", {"artist": "周杰伦"}),
    ("播周杰伦的歌曲", "play", {"artist": "周杰伦"}),
    ("播周杰伦的音乐", "play", {"artist": "周杰伦"}),

    # 测试播放音乐/歌曲命令
    ("播放音乐", "play", {}),
    ("播放歌曲", "play", {}),
    ("来点音乐", "play", {}),
    ("来点歌曲", "play", {}),
    ("播音乐", "play", {}),
    ("播歌曲", "play", {}),
    
    # 测试下一首/上一首命令
    ("下一首", "play_next", {}),
    ("播放下一首", "play_next", {}),
    ("播下一首", "play_next", {}),
    ("上一首", "play_prev", {}),
    ("播放上一首", "play_prev", {}),
    ("播上一首", "play_prev", {}),
    
    # 测试播放列表第N首命令
    ("播放列表第5首歌", "play_music_list_index", {"index": "5"}),
    ("播放列表第10首歌曲", "play_music_list_index", {"index": "10"}),
    ("播放列表第3首音乐", "play_music_list_index", {"index": "3"}),
    ("播放列表第1首", "play_music_list_index", {"index": "1"}),
    ("播列表第5首歌", "play_music_list_index", {"index": "5"}),
    ("播列表第10首歌曲", "play_music_list_index", {"index": "10"}),
    ("播列表第3首音乐", "play_music_list_index", {"index": "3"}),
    ("播列表第1首", "play_music_list_index", {"index": "1"}),
    
    # 测试停止命令
    ("关机", "stop", {}),
    ("暂停", "stop", {}),
    ("停止", "stop", {}),
    ("闭嘴", "stop", {}),
    
    # 测试定时关机命令
    ("5分钟后关机", "stop_after_minute", {"minute": "5"}),
    ("10分钟后关机", "stop_after_minute", {"minute": "10"}),
    ("30分钟后关机", "stop_after_minute", {"minute": "30"}),
    
    # 测试刷新播放列表命令
    ("刷新播放列表", "gen_music_list", {}),
    ("更新歌单", "gen_music_list", {}),
    ("重新加载列表", "gen_music_list", {}),
    ("刷新全部歌单", "gen_music_list", {}),
    
    # 测试关闭语音口令命令
    ("关闭语音口令", "set_pull_ask_off", {}),
    ("关闭语音指令", "set_pull_ask_off", {}),
    ("关闭口令功能", "set_pull_ask_off", {}),

    # 测试添加收藏命令
    ("收藏歌曲", "add_to_favorites", {}),
    ("收藏音乐", "add_to_favorites", {}),
    ("收藏当前歌曲", "add_to_favorites", {}),
    ("收藏当前音乐", "add_to_favorites", {}),
    ("收藏这首歌", "add_to_favorites", {}),
    ("收藏这首歌曲", "add_to_favorites", {}),
    ("添加歌曲到收藏", "add_to_favorites", {}),
    ("添加音乐到收藏", "add_to_favorites", {}),
    ("添加当前歌曲到收藏", "add_to_favorites", {}),
    ("添加当前音乐到收藏", "add_to_favorites", {}),
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
                if field_name not in ["name", "artist", "is_favorite", "album", "genre", "index", "year", "minute", "user_alias"]:
                    pytest.fail(f"意外匹配的字段: {field_name}")
            
            break
    
    assert matched, f"没有找到匹配的规则: {test_text}"
