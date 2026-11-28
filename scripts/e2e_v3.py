"""v3 今日推荐本地端到端验收（只读 Emby，凭据运行时从 conf/setting.json 读取）。

运行：.venv/bin/python scripts/e2e_v3.py
- 不打印、不落盘任何 API Key / 凭据；输出只含计数、档位与约束结论。
- 全库镜像、缓存与历史写到 /tmp/opencode/e2e-v3/，不进仓库。
- 只做 GET / 只读 SELECT；不写 Emby、不改插件 / 服务器配置。
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from dataclasses import fields as dataclass_fields
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import requests
from starlette.requests import Request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from xiaomusic.api.routers import system as system_router  # noqa: E402
from xiaomusic.config import Config  # noqa: E402
from xiaomusic.utils.behavior_profile import build_profile  # noqa: E402
from xiaomusic.utils.candidate_pool import (  # noqa: E402
    CandidateConfig,
    build_candidate_pool,
    language_of,
)
from xiaomusic.utils.daily_recommend import (  # noqa: E402
    _audio_from_mirror,
    _today,
    _user_states_from_pr,
    generate,
)
from xiaomusic.utils.library_mirror import LibraryMirror  # noqa: E402
from xiaomusic.utils.pr_events import (  # noqa: E402
    LOCAL_TZ,
    EmbyPRClient,
    PRReadResult,
    read_pr_events,
)

SETTINGS_PATH = Path("/home/nick/workspace/xiaoemby/conf/setting.json")
WORK_DIR = Path("/tmp/opencode/e2e-v3")
MIRROR_DB = WORK_DIR / "library_mirror.sqlite3"

# LLM 在端到端里用桩（规格 Testing Decisions：不测真实 LLM 调用）。
STUB_RECIPE = {
    "schema_version": 1,
    "intent": "comfort",
    "reason": "按本地镜像与播放事件编排",
    "include": {"genres": [], "artists": [], "decades": []},
    "exclude": {"genres": [], "artists": [], "decades": []},
    "boost": [],
    "weights": {"familiarity": 0.6, "novelty": 0.5, "mood_match": 0.5},
}


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("setting.json 顶层必须是对象")
    for key in ("emby_host", "emby_api_key", "emby_user_id"):
        if not data.get(key):
            raise ValueError(f"setting.json 缺少 {key}")
    return data


def make_config(settings: dict[str, Any], *, cache_dir: str) -> Config:
    """按 Config 已知字段装载配置；未知字段与复杂结构跳过，绝不输出值。"""
    cfg = Config()
    known = {f.name: f for f in dataclass_fields(Config)}
    for key, value in settings.items():
        field = known.get(key)
        if field is None or value is None or isinstance(value, (dict, list)):
            continue
        sample = getattr(cfg, key, "")
        try:
            if isinstance(sample, bool):
                value = bool(value)
            elif isinstance(sample, int) and not isinstance(sample, bool):
                value = int(value)
            elif isinstance(sample, float):
                value = float(value)
            elif isinstance(sample, str):
                value = str(value)
        except (TypeError, ValueError):
            continue
        setattr(cfg, key, value)
    cfg.cache_dir = cache_dir
    cfg.enable_daily_recommend = True
    return cfg


def new_session() -> requests.Session:
    session = requests.Session()
    session.verify = False  # 自签 HTTPS；仅在本地验收进程内生效
    requests.packages.urllib3.disable_warnings()
    return session


def _json_request(body: dict[str, Any]) -> Request:
    """构造一个最小 Starlette 请求，直接调用真实路由函数（与 HTTP 同一路径）。"""
    payload = json.dumps(body).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "headers": [(b"content-type", b"application/json")]},
        receive,
    )


def import_mirror_via_api(cfg: Config, session: requests.Session) -> dict[str, Any]:
    """通过产品同步入口（API 路由）做首次全量导入，验证真实用户路径。"""
    original_sync = system_router.sync_mirror
    original_app = system_router.xiaomusic

    def sync_with_transport(config, **kwargs):
        kwargs["transport"] = session  # 自签 HTTPS：复用本地验收的只读会话
        return original_sync(config, **kwargs)

    system_router.sync_mirror = sync_with_transport
    system_router.xiaomusic = SimpleNamespace(getconfig=lambda: cfg, emby=None)
    try:
        return asyncio.run(system_router.sync_mirror_now(_json_request({"kind": "full"})))
    finally:
        system_router.sync_mirror = original_sync
        system_router.xiaomusic = original_app


def load_snapshot_with_user_state(
    mirror: LibraryMirror, user_id: str
) -> list[dict[str, Any]]:
    """读全库静态快照；动态用户态不落镜像，由 PR 事件在 MirrorEmby 里推导。"""
    return list(mirror.snapshot())


def runtime_map(snapshot: list[dict[str, Any]]) -> dict[str, float]:
    return {
        str(row["id"]): float(row.get("runtime_ticks") or 0) / 1e7
        for row in snapshot
        if row.get("runtime_ticks")
    }


def event_summary(result: PRReadResult) -> dict[str, Any]:
    events = list(result.events or ())
    days = [event.started_at.date() for event in events]
    span = (max(days) - min(days)).days if len(days) >= 2 else 0
    return {
        "ok": result.ok,
        "reason": (result.reason.value if result.reason else "-"),
        "raw_rows": result.raw_rows,
        "merged_events": len(events),
        "valid_events": sum(1 for event in events if event.is_valid),
        "span_days": span,
    }


def make_synthetic_events(
    events: list[Any],
    valid_ids: set[str],
    *,
    count: int = 40,
    days: int = 14,
) -> tuple[Any, ...]:
    """用真实事件派生「跨度 >7 天且 ≥20 条有效」的 PR 场景，不写 Emby。"""
    source = [event for event in events if str(event.item_id) in valid_ids]
    if not source or count < 20 or days < 8:
        raise ValueError("合成 PR 场景的样本或跨度不足")
    base = datetime.now(LOCAL_TZ).replace(hour=9, minute=0, second=0, microsecond=0)
    result = []
    for index in range(count):
        src = source[index % len(source)]
        result.append(
            replace(
                src,
                started_at=base - timedelta(days=index % days, minutes=index * 3),
                duration_seconds=max(float(src.duration_seconds), 180.0),
                completion_rate=1.0,
                is_valid=True,
                is_extremely_short=False,
                is_incomplete=False,
                segment_count=1,
            )
        )
    return tuple(result)


def unheard_ids_from_pool(
    events: tuple[Any, ...],
    snapshot: list[dict[str, Any]],
    user_id: str,
    today: str,
) -> set[str]:
    """按与编排一致的公开入口重算探索池，用于校验「≥1 首没听过」。"""
    library = {str(row["id"]): row for row in snapshot}
    profile = build_profile(
        events,
        library=library,
        user_id=user_id,
        today=today,
        user_states=_user_states_from_pr(events, user_id),
    )
    pool = build_candidate_pool(snapshot, profile, config=CandidateConfig(), today=today)
    return set(pool.explore)


def front_five_check(
    ids: list[str],
    rows: dict[str, dict[str, Any]],
    *,
    unheard: set[str] | None = None,
    expected: int = 5,
) -> dict[str, bool]:
    """前五重点区约束：数量、每流派 ≤2、每语言 ≤3、非华语 1~2、≥1 首没听过。"""
    front = [str(item) for item in ids[:5]]
    if any(item not in rows for item in front):
        raise KeyError("前五曲目不在镜像快照中")
    genres: Counter = Counter()
    languages: Counter = Counter()
    foreign = 0
    for item in front:
        for genre in dict.fromkeys(str(g) for g in (rows[item].get("genres") or [])):
            genres[genre] += 1
        language = language_of(rows[item])
        languages[language] += 1
        if language != "华语":
            foreign += 1
    return {
        "count_ok": len(front) == min(5, expected),
        "genre_cap_ok": all(value <= 2 for value in genres.values()),
        "language_cap_ok": all(value <= 3 for value in languages.values()),
        "foreign_ok": 1 <= foreign <= 2,
        "unheard_ok": bool(front and unheard and set(front) & set(unheard)),
    }


class MirrorEmby:
    """给 generate 的轻量 Emby 替身：曲库与播放信息全部来自本地镜像。"""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        user_id: str,
        state: dict[str, Any],
        pr_result: PRReadResult,
    ) -> None:
        self.rows = rows
        self.user_id = user_id
        self.pr_events = pr_result
        self.library_snapshot = rows
        self.mirror_state = state
        self.by_id = {str(row["id"]): row for row in rows}
        # 动态用户态从 PR 有效播放推导（镜像不再存用户态）
        self.user_states = _user_states_from_pr(pr_result.events or (), user_id)
        played = [
            row for row in rows
            if self.user_states.get(str(row["id"]), {}).get(user_id, {}).get("last_played_date")
        ]
        self.recent = sorted(
            played,
            key=lambda row: str(
                self.user_states[str(row["id"])][user_id]["last_played_date"]
            ),
            reverse=True,
        )[:50]

    def _audio(self, row: dict[str, Any]) -> Any:
        return _audio_from_mirror(row, self.user_id)

    def fetch_recommend_candidates(self, limit: int = 800) -> list[Any]:
        ordered = sorted(
            self.rows,
            key=lambda row: (
                int(
                    self.user_states.get(str(row["id"]), {})
                    .get(self.user_id, {})
                    .get("play_count")
                    or 0
                ),
                bool(
                    self.user_states.get(str(row["id"]), {})
                    .get(self.user_id, {})
                    .get("favorite")
                ),
            ),
            reverse=True,
        )
        return [self._audio(row) for row in ordered[: max(int(limit), 1)]]

    def fetch_tracks_by_ids(self, ids: list[str]) -> list[Any]:
        return [
            self._audio(self.by_id[str(item)])
            for item in ids
            if str(item) in self.by_id
        ]

    def fetch_recently_played(self, limit: int = 50) -> list[Any]:
        return [self._audio(row) for row in self.recent[: max(int(limit), 0)]]

    def fetch_total_tracks(self) -> int:
        return len(self.rows)


def install_llm_stub() -> None:
    import xiaomusic.utils.daily_recommend as module

    module.ask_llm = lambda catalog, cfg: dict(STUB_RECIPE)


def check_playlist(rec: Any, expected: int, rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ids = [str(item) for item in rec.track_ids]
    return {
        "size": len(ids),
        "size_ok": len(ids) == expected,
        "unique_ok": len(set(ids)) == len(ids),
        "ids_valid": all(item in rows for item in ids),
        "mode": rec.mode,
        "source": rec.source,
        "fallback_reason": rec.fallback_reason or "-",
        "renewals": rec.renewals,
    }


def run() -> dict[str, Any]:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    host = str(settings["emby_host"])
    key = str(settings["emby_api_key"])
    user_id = str(settings["emby_user_id"])
    session = new_session()

    cfg = make_config(settings, cache_dir=str(WORK_DIR))

    print("步骤 1/6：通过产品同步入口（API）首次全量导入本地曲库镜像…", flush=True)
    api_result = import_mirror_via_api(cfg, session)
    if not api_result.get("ok"):
        raise RuntimeError(f"镜像同步入口失败: {api_result.get('reason')}")
    print(
        f"  入口返回：{api_result['kind']} / 版本 {api_result['version']} / 曲目 {api_result['tracks']}",
        flush=True,
    )

    print("步骤 2/6：读取本地镜像快照（/tmp/opencode/e2e-v3/）…", flush=True)
    mirror = LibraryMirror(str(MIRROR_DB))
    try:
        info = mirror.version_info()
        rows = load_snapshot_with_user_state(mirror, user_id)
        state = {
            "day": 0,
            "stale_days": int(cfg.mirror_stale_days),
            "mirror": {
                "version": info["version"],
                "last_sync_day": info.get("last_sync_day"),
                "items": {row["id"]: row for row in rows},
            },
        }
        print(
            f"  镜像版本 {info['version']}，落库静态曲目 {len(rows)}（动态信息走接口）",
            flush=True,
        )

        print("步骤 3/6：只读读取当前账号 PR 事件…", flush=True)
        client = EmbyPRClient(host, key, transport=session)
        pr_result = read_pr_events(client, user_id=user_id, runtimes=runtime_map(rows))
        summary = event_summary(pr_result)
        print(
            "  PR: 原始行 {raw_rows}，合并事件 {merged_events}，有效 {valid_events}，"
            "跨度 {span_days} 天，原因 {reason}".format(**summary),
            flush=True,
        )
        dynamic = _user_states_from_pr(pr_result.events or (), user_id)
        played = sum(
            1
            for row in rows
            if dynamic.get(str(row["id"]), {})
            .get(user_id, {})
            .get("last_played_date")
        )

        install_llm_stub()
        expected = int(cfg.daily_recommend_count)
        wrapper = MirrorEmby(rows, user_id, state, pr_result)
        by_id = wrapper.by_id

        print("步骤 4/6：生成真实歌单 + 当天冻结 + 换一批…", flush=True)
        first = generate(cfg, wrapper)
        first_check = check_playlist(first, expected, by_id)
        if not all(first_check[key] for key in ("size_ok", "unique_ok", "ids_valid")):
            raise RuntimeError(f"真实歌单断言失败: {first_check}")
        second = generate(cfg, wrapper)
        freeze_ok = (
            second.track_ids == first.track_ids
            and second.mode == first.mode
            and second.renewals == first.renewals
        )
        renewed = generate(cfg, wrapper, renew=True)
        renew_check = check_playlist(renewed, expected, by_id)

        print("步骤 5/6：合成 PR 场景（跨度 >7 天）验证 PR 档位…", flush=True)
        synthetic = make_synthetic_events(
            list(pr_result.events or ()), set(by_id), count=40, days=14
        )
        synthetic_result = PRReadResult(
            ok=True,
            reason=None,
            user_id=user_id,
            events=synthetic,
            raw_rows=len(synthetic),
        )
        synthetic_cfg = make_config(settings, cache_dir=str(WORK_DIR / "synthetic"))
        synthetic_wrapper = MirrorEmby(rows, user_id, state, synthetic_result)
        pr_rec = generate(synthetic_cfg, synthetic_wrapper)
        pr_check = check_playlist(pr_rec, expected, by_id)
        if pr_rec.mode != "pr" or pr_check["mode"] != "pr":
            raise RuntimeError(f"合成 PR 场景未命中 PR 档位: {pr_check}")
        if not all(pr_check[key] for key in ("size_ok", "unique_ok", "ids_valid")):
            raise RuntimeError(f"合成 PR 歌单断言失败: {pr_check}")
        unheard = unheard_ids_from_pool(synthetic, rows, user_id, _today(synthetic_cfg))
        front_check = front_five_check(
            pr_rec.track_ids, by_id, unheard=unheard, expected=expected
        )
        if not all(front_check.values()):
            raise RuntimeError(f"前五重点区约束未通过: {front_check}")
    finally:
        mirror.close()
        session.close()

    return {
        "mirror": {
            "total": api_result["tracks"],
            "rows": len(rows),
            "played": played,
            "version": info["version"],
        },
        "pr": summary,
        "real": first_check,
        "freeze_ok": freeze_ok,
        "renew": renew_check,
        "synthetic": pr_check,
        "front_five": front_check,
    }


def main() -> None:
    result = run()
    real = result["real"]
    mirror = result["mirror"]
    pr = result["pr"]
    print("\n===== 端到端验收结果 =====")
    print(
        f"镜像：Emby 总数 {mirror['total']} → 落库 {mirror['rows']} 首，"
        f"有上次播放 {mirror['played']} 首，版本 {mirror['version']}"
    )
    print(
        f"真实 PR：原始行 {pr['raw_rows']}，合并 {pr['merged_events']} 条，"
        f"有效 {pr['valid_events']} 条，跨度 {pr['span_days']} 天，原因 {pr['reason']}"
    )
    print(
        f"真实歌单：{real['size']} 首（数量{'通过' if real['size_ok'] else '不通过'}），"
        f"去重{'通过' if real['unique_ok'] else '不通过'}，id 有效{'通过' if real['ids_valid'] else '不通过'}，"
        f"档位 {real['mode']}，source={real['source']}，fallback_reason={real['fallback_reason']}"
    )
    print(f"当天冻结：{'通过' if result['freeze_ok'] else '不通过'}；换一批 renewals={result['renew']['renewals']}")
    synthetic = result["synthetic"]
    print(
        f"合成 PR 场景：档位 {synthetic['mode']}，source={synthetic['source']}，"
        f"回退原因 {synthetic['fallback_reason']}，{synthetic['size']} 首"
    )
    front = result["front_five"]
    print(
        "前五约束：数量"
        f"{'通过' if front['count_ok'] else '不通过'} / 流派"
        f"{'通过' if front['genre_cap_ok'] else '不通过'} / 语言"
        f"{'通过' if front['language_cap_ok'] else '不通过'} / 非华语"
        f"{'通过' if front['foreign_ok'] else '不通过'} / 未听过"
        f"{'通过' if front['unheard_ok'] else '不通过'}"
    )


if __name__ == "__main__":
    main()
