import pytest
from xiaomusic.utils.emby_utils import ChineseNumberConverter

# 测试数据：(原始文本, 期望转换结果)
test_data = [
    # 基本转换测试
    ("七天", "7天"),
    ("七分爱情三分骗", "7分爱情3分骗"),
    ("爱情三十六计", "爱情36计"),
    ("爱的二八定律", "爱的28定律"),
    ("五十米深蓝", "50米深蓝"),
    ("八十块环游世界", "80块环游世界"),
    ("相约一九九八", "相约1998"),
    ("谢谢你的爱一九九九", "谢谢你的爱1999"),
    ("伤心一九九九", "伤心1999"),
    ("我的一九九七", "我的1997"),

    # 常见数字组合测试
    ("八", "8"),
    ("十二", "12"),
    ("十三", "13"),
    ("十九", "19"),
    ("二十", "20"),
    ("二十一", "21"),
    ("二十二", "22"),
    ("二十九", "29"),
    ("三十", "30"),
    ("三十九", "39"),
    ("五十", "50"),
    ("五十九", "59"),
    ("八十", "80"),
    ("八十二", "82"),

    
    # 固定词语不转换测试
    ("一剪梅", "一剪梅"),
    ("十年", "十年"),
    ("百年孤独", "百年孤独"),
    ("千与千寻", "千与千寻"),
    ("万水千山总是情", "万水千山总是情"),
    ("亿万富翁", "亿万富翁"),
    ("千言万语", "千言万语"),
    ("千方百计", "千方百计"),
    ("千军万马", "千军万马"),
    ("千秋万代", "千秋万代"),
    ("千家万户", "千家万户"),
    ("千变万化", "千变万化"),
    ("一千零一", "一千零一"),
    ("九百九十九", "九百九十九"),
    ("七里香", "七里香"),
    ("一些风景", "一些风景"),
    ("一次", "一次"),
    ("一整夜", "一整夜"),
    ("一个人", "一个人"),
    ("一滴", "一滴"),
    ("一天", "一天"),
    ("一起走过的日子", "一起走过的日子"),
    ("一生有你", "一生有你"),
    ("一场游戏一场梦", "一场游戏一场梦"),
    
    # 包含固定词语的句子不转换测试
    ("播放十年", "播放十年"),
    ("我喜欢千与千寻", "我喜欢千与千寻"),
    
    # 没有中文数字的文本不转换测试
    ("简单爱", "简单爱"),
    ("晴天", "晴天"),
    
    # 空字符串测试
    ("", ""),
    
]


@pytest.mark.parametrize("original_text, expected_result", test_data)
def test_chinese_number_conversion(original_text, expected_result):
    """测试中文数字转换功能"""
    result = ChineseNumberConverter.convert_chinese_numbers_in_string(original_text)
    assert result == expected_result, f"原始文本 '{original_text}'，期望结果 '{expected_result}'，实际结果 '{result}'"


def test_chinese_to_arabic_basic():
    """测试中文数字转阿拉伯数字的基本功能"""
    assert ChineseNumberConverter.chinese_to_arabic("零") == 0
    assert ChineseNumberConverter.chinese_to_arabic("一") == 1
    assert ChineseNumberConverter.chinese_to_arabic("二") == 2
    assert ChineseNumberConverter.chinese_to_arabic("三") == 3
    assert ChineseNumberConverter.chinese_to_arabic("四") == 4
    assert ChineseNumberConverter.chinese_to_arabic("五") == 5
    assert ChineseNumberConverter.chinese_to_arabic("六") == 6
    assert ChineseNumberConverter.chinese_to_arabic("七") == 7
    assert ChineseNumberConverter.chinese_to_arabic("八") == 8
    assert ChineseNumberConverter.chinese_to_arabic("九") == 9


def test_chinese_to_arabic_units():
    """测试中文数字单位的转换功能"""
    assert ChineseNumberConverter.chinese_to_arabic("十") == 10
    assert ChineseNumberConverter.chinese_to_arabic("百") == 100
    assert ChineseNumberConverter.chinese_to_arabic("千") == 1000
    assert ChineseNumberConverter.chinese_to_arabic("万") == 10000
    assert ChineseNumberConverter.chinese_to_arabic("亿") == 100000000


def test_chinese_to_arabic_complex():
    """测试复杂中文数字的转换功能"""
    assert ChineseNumberConverter.chinese_to_arabic("十二") == 12
    assert ChineseNumberConverter.chinese_to_arabic("十五") == 15
    assert ChineseNumberConverter.chinese_to_arabic("二十") == 20
    assert ChineseNumberConverter.chinese_to_arabic("三十六") == 36
    assert ChineseNumberConverter.chinese_to_arabic("一百零五") == 105
    assert ChineseNumberConverter.chinese_to_arabic("一千零一夜") == 1001
