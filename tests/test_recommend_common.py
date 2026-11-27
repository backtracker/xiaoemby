"""今日推荐共享口径：跨模块常量、字符串枚举、配置字段映射的公共边界。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from xiaomusic.config import Config
from xiaomusic.utils.behavior_profile import ProfileConfig
from xiaomusic.utils.candidate_pool import CandidateConfig, SimilarityWeights
from xiaomusic.utils.recommend_common import (
    MIN_TASTE_SAMPLES,
    MMR_LAMBDA,
    MODE_FIELDS,
    RecommendMode,
    RecommendSource,
    active_items,
    age_days,
    as_day,
    exposure_is_recent,
    library_by_id,
)


def test_mode_and_source_enums_behave_as_plain_strings():
    assert RecommendMode.PR == "pr"
    assert RecommendMode.MIXED == "mixed"
    assert RecommendMode.V2 == "v2"
    assert str(RecommendMode.PR) == "pr"
    assert json.dumps({"mode": RecommendMode.PR, "source": RecommendSource.LLM}) == (
        '{"mode": "pr", "source": "llm"}'
    )
    assert MODE_FIELDS == ("pr", "mixed", "v2")


def test_shared_constants_are_single_source_of_truth():
    assert MIN_TASTE_SAMPLES == 15
    assert MMR_LAMBDA == 0.7
    assert Config().min_taste_samples == MIN_TASTE_SAMPLES
    assert Config().mmr_lambda == MMR_LAMBDA


def test_shared_date_helpers_parse_emby_shapes():
    assert as_day("2026-11-27T02:52:24Z") is not None
    assert as_day("2026-11-27T02:52:24Z").isoformat() == "2026-11-27"
    assert as_day("bad-date") is None
    assert age_days("2026-11-25T10:00:00Z", "2026-11-27") == 2
    assert age_days("", "2026-11-27") == 99


def test_shared_exposure_check_treats_dated_and_undated_values_consistently():
    assert exposure_is_recent(None, as_day("2026-11-27"), 30) is False
    assert exposure_is_recent("2026-11-20", as_day("2026-11-27"), 30) is True
    assert exposure_is_recent("2026-10-01", as_day("2026-11-27"), 30) is False
    assert exposure_is_recent(2, None, 30) is True


def test_shared_item_shapes_preserve_authoritative_fields_and_tombstones():
    rows = [
        {"id": "a", "name": "A", "users": {"u": {"play_count": 1}}},
        {"id": "a", "name": "A2"},
        {"id": "b", "tombstone": True},
    ]
    assert library_by_id(rows)["a"]["name"] == "A2"
    active = active_items(rows)
    assert active["a"]["name"] == "A"
    assert "b" not in active


def test_all_recommend_dataclass_fields_have_config_counterparts():
    cfg_fields = set(Config.__dataclass_fields__)
    missing = [
        f"{cls.__name__}.{name}"
        for cls, names in (
            (ProfileConfig, ProfileConfig.__dataclass_fields__),
            (CandidateConfig, (
                name for name in CandidateConfig.__dataclass_fields__
                if name not in {"weights", "playlist_size"}
            )),
            (SimilarityWeights, (
                f"similarity_{name}_weight" for name in SimilarityWeights.__dataclass_fields__
            )),
        )
        for name in names
        if name not in cfg_fields
    ]
    assert missing == []

    html = (Path(__file__).resolve().parents[1] / "xiaomusic" / "static" / "default" / "setting.html").read_text(encoding="utf-8")
    section = html[html.index('<section class="panel" id="sec-ai">'):html.index('<section class="panel" id="sec-system">')]
    ids = re.findall(r'<(?:input|select|textarea)[^>]*\bid="([^"]+)"', section)
    assert [i for i in ids if i not in cfg_fields] == []
