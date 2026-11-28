"""静态/动态拆分的离线端到端验收（不联网，不依赖真实 Emby）。

验证：镜像快照不带 users 字段时，PR 模式仍能产出歌单，且画像的用户态
（累计熟悉度 / 未听池）由 PR 有效播放推导，而非镜像用户态。

运行：.venv/bin/python scripts/verify_static_dynamic_split.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from test_daily_recommend import _v3_cfg, _v3_event, _v3_library, _v3_stub_llm  # noqa: E402

import pytest  # noqa: E402
from xiaomusic.utils.daily_recommend import generate  # noqa: E402
from xiaomusic.utils.pr_events import PRReadResult  # noqa: E402


class InjectEmby:
    """把 PR 结果与无用户态镜像快照注入 generate 的替身。"""

    def __init__(self, audios, snapshot, state, pr_result):
        self._audios = audios
        self.library_snapshot = snapshot
        self.mirror_state = state
        self.pr_events = pr_result

    def fetch_tracks_by_ids(self, ids):
        by_id = {str(a.id): a for a in self._audios}
        return [by_id[i] for i in ids if i in by_id]

    def fetch_recently_played(self, limit=50):
        return []

    def fetch_total_tracks(self):
        return len(self._audios)

    def fetch_recommend_candidates(self, limit=None):
        return list(self._audios)


def main() -> None:
    tmp = Path("/tmp/opencode/verify-static-dynamic")
    tmp.mkdir(parents=True, exist_ok=True)

    specs = [(i, [g]) for i, g in enumerate(
        ["Mandopop", "Cantopop", "国语", "华语民谣", "J-Pop", "Anime", "Rock", "Pop"] * 3
    )]
    state, snapshot, audios = _v3_library(specs)

    # 前置断言：静态/动态拆分后，镜像快照绝不能带 users 字段。
    assert all("users" not in row for row in snapshot), "镜像快照不应含用户态字段"

    events = tuple(_v3_event(d % 10, str(1 + d % 2)) for d in range(20))
    pr_result = PRReadResult(ok=True, reason=None, user_id="u1", events=events)
    cfg = _v3_cfg(tmp)
    emby = InjectEmby(audios, snapshot, state, pr_result)
    monkeypatch = pytest.MonkeyPatch()
    _v3_stub_llm(monkeypatch)

    result = generate(cfg, emby)

    assert result.mode == "pr", f"档位应为 pr，实际 {result.mode}"
    assert result.fallback_reason == "", f"不应回退，实际 {result.fallback_reason}"
    assert len(result.tracks) >= 10, f"曲目数不足: {len(result.tracks)}"
    assert result.segments == [], "segments 已废弃"

    # 画像由 PR 事件推导：至少一首被 PR 事件覆盖的曲目进入熟悉度/未听判定链路。
    from xiaomusic.utils.behavior_profile import build_profile
    from xiaomusic.utils.daily_recommend import _user_states_from_pr
    from xiaomusic.utils.recommend_common import library_by_id

    user_states = _user_states_from_pr(events, "u1")
    assert user_states, "PR 有效播放应推导出用户态"
    profile = build_profile(events, library=library_by_id(snapshot), user_id="u1")
    assert bool(profile.base_score), "画像应产出打分骨架"

    print(f"PASS: mode={result.mode}, tracks={len(result.tracks)}, "
          f"pr_user_states={len(user_states)}, snapshot_rows={len(snapshot)}")
    print("PASS: 镜像快照无 users，PR 模式画像由 PR 有效播放推导")


if __name__ == "__main__":
    main()
