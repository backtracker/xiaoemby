"""设置页「AI 今日推荐」v3 配置暴露的轻量静态断言。

缝：设置页 HTML 是配置字段与用户之间的公共边界。测试只读静态资源，
以 config.py 里已落地的默认值为独立真相源，断言字段一一对应、分组顺序、
中文标签/默认值/范围/说明/条件可见性标记，以及凭据与内部旋钮不进页面。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from xiaomusic.config import Config

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "xiaomusic" / "static" / "default" / "setting.html").read_text(
    encoding="utf-8"
)
JS = (ROOT / "xiaomusic" / "static" / "default" / "setting.js").read_text(
    encoding="utf-8"
)

START = '<section class="panel" id="sec-ai">'
END = '<section class="panel" id="sec-system">'
SECTION = HTML[HTML.index(START) : HTML.index(END)]


# 独立真相源：issue 18 Answer 定稿清单 → config.py 字段名。
# 字段 id → (分组, 条件可见性代码)
EXPECTED: dict[str, tuple[str, str]] = {
    # 歌单规模与排序
    "daily_recommend_count": ("歌单规模与排序", "global"),
    "explore_ratio": ("歌单规模与排序", "global"),
    "freshness_weight": ("歌单规模与排序", "pr"),
    "diversity_weight": ("歌单规模与排序", "pr"),
    "mmr_lambda": ("歌单规模与排序", "pr"),
    "front_five_genre_cap": ("歌单规模与排序", "pr"),
    "front_five_language_cap": ("歌单规模与排序", "pr"),
    "front_five_foreign_min": ("歌单规模与排序", "pr"),
    "front_five_foreign_max": ("歌单规模与排序", "pr"),
    "rest_genre_cap": ("歌单规模与排序", "pr"),
    "rest_language_cap": ("歌单规模与排序", "pr"),
    # 档位判定
    "pr_mode_min_days": ("档位判定", "pr"),
    "pr_mode_min_events": ("档位判定", "pr"),
    "mixed_mode_min_days": ("档位判定", "mixed"),
    "mixed_mode_min_events": ("档位判定", "mixed"),
    # 反馈衰减与有效播放
    "negative_half_life_days": ("反馈衰减与有效播放", "pr"),
    "positive_half_life_days": ("反馈衰减与有效播放", "pr"),
    "valid_completion": ("反馈衰减与有效播放", "pr"),
    "weak_completion": ("反馈衰减与有效播放", "pr"),
    "short_seconds": ("反馈衰减与有效播放", "pr"),
    # 高级生成规则（默认折叠）
    "revisit_max_per_day": ("高级生成规则", "pr"),
    "revisit_min_valid_plays": ("高级生成规则", "pr"),
    "revisit_min_gap_days": ("高级生成规则", "pr"),
    "exclusion_days": ("高级生成规则", "pr"),
    "immersion_window_days": ("高级生成规则", "pr"),
    "immersion_min_plays": ("高级生成规则", "pr"),
    "immersion_min_share": ("高级生成规则", "pr"),
    "immersion_prior_days": ("高级生成规则", "pr"),
    "immersion_prior_max": ("高级生成规则", "pr"),
    "familiar_play_count": ("高级生成规则", "pr"),
    "familiar_favorite_count": ("高级生成规则", "pr"),
    "familiar_recent_plays": ("高级生成规则", "pr"),
    "familiar_recent_days": ("高级生成规则", "pr"),
    "familiar_per_artist_cap": ("高级生成规则", "pr"),
    "seed_recall_enabled": ("高级生成规则", "pr"),
    "similarity_genre_weight": ("高级生成规则", "pr"),
    "similarity_language_weight": ("高级生成规则", "pr"),
    "similarity_artist_weight": ("高级生成规则", "pr"),
    "similarity_album_weight": ("高级生成规则", "pr"),
    "similarity_era_weight": ("高级生成规则", "pr"),
}

GROUP_ORDER = [
    "LLM 连接",
    "歌单规模与排序",
    "档位判定",
    "反馈衰减与有效播放",
    "高级生成规则",
]
CHIP_TEXT = {
    "pr": "仅 PR 模式生效",
    "mixed": "混合模式门槛",
    "global": "全局",
}
BOOL_FIELDS = {"seed_recall_enabled"}
CJK = re.compile(r"[\u4e00-\u9fff]")


def test_group_titles_in_expected_order():
    titles = re.findall(r'<h3 class="group-title">(.*?)</h3>', SECTION, re.S)
    titles = [re.sub(r"<[^>]+>", "", t).strip() for t in titles]
    assert [t for t in titles if t in GROUP_ORDER] == GROUP_ORDER


def test_advanced_rules_collapsed_by_default():
    details = re.search(r"<details[^>]*>(.*?)</details>", SECTION, re.S)
    assert details is not None
    assert "高级生成规则" in details.group(1)
    assert "open" not in details.group(0)


@pytest.mark.parametrize(("field_id", "spec"), EXPECTED.items())
def test_v3_field_present_labeled_and_mapped(field_id, spec):
    group, chip = spec
    block = re.search(
        rf'<div class="field[^"]*">\s*<label for="{field_id}">(.*?)</label>(.*?)</div>',
        SECTION,
        re.S,
    )
    assert block is not None, f"{field_id} 缺少 field 块"
    label, body = block.group(1), block.group(2)

    assert CJK.search(re.sub(r"<[^>]+>", "", label)), f"{field_id} 缺少中文标签"
    assert f'data-mode="{chip}"' in label, f"{field_id} 缺少条件可见性标记"
    assert CHIP_TEXT[chip] in label, f"{field_id} 标记文案不符"
    assert f'id="{field_id}"' in body, f"{field_id} 缺少控件"

    hint = re.search(r'<div class="hint">([^<]*)', body, re.S)
    assert hint is not None and CJK.search(hint.group(1)), f"{field_id} 缺少中文说明"

    default = getattr(Config(), field_id)
    if field_id in BOOL_FIELDS:
        selected = re.search(r'<option value="(\w+)" selected>', body)
        assert selected is not None and selected.group(1) == str(default).lower()
    else:
        value = re.search(r'value="([^"]+)"', body)
        assert value is not None
        assert float(value.group(1)) == float(default)
        for attr in ("min=", "max=", "step="):
            assert attr in body, f"{field_id} 缺少 {attr} 范围/步进"


def test_all_ai_controls_map_to_config_fields():
    ids = re.findall(r'<(?:input|select|textarea)[^>]*\bid="([^"]+)"', SECTION)
    assert len(ids) == len(set(ids)), "设置页存在重复控件 id"
    config_fields = set(Config.__dataclass_fields__)
    unknown = [i for i in ids if i not in config_fields]
    assert unknown == [], f"控件 id 不是配置字段（无法往返）: {unknown}"


def test_no_credentials_or_internal_knobs_on_page():
    assert "PR API Key" not in SECTION
    ids = re.findall(r'\bid="([^"]+)"', HTML)
    assert [i for i in ids if "api_key" in i] == ["llm_api_key"]
    forbidden = [
        "pr_api_key",
        "pr_timeout_seconds",
        "pr_event_limit",
        "mirror_db_path",
        "mirror_stale_days",
        "min_window_samples",
        "min_taste_samples",
        "min_repeat_count",
        "immersion_half_life_days",
        "immersion_upgrade_bursts",
        "immersion_upgrade_gap_days",
        "immersion_min_weight",
        "max_seeds",
        "min_seed_score",
        "seed_min_similarity",
        "seed_max_penalty",
        "seed_require_unheard",
        "taste_vector",
        "base_score",
        "penalty",
    ]
    for name in forbidden:
        assert name not in SECTION, f"内部旋钮/凭据不应出现在页面: {name}"


def test_js_roundtrip_mechanism_intact():
    assert '$("#" + key)' in JS
    assert 'setting.find("input, select, textarea")' in JS
