"""本地曲库镜像的运行时同步入口：只读 Emby + 纯归约 + 原子落库。

同步链路：未建镜像做全量导入；已建镜像按水位做每日增量；距上次成功同步达到
配置天数（或显式指定）做每周全量对账。任何失败都不写库，保留上一版并告警，
不打断出歌单。传输层可注入；默认情况下只发 GET，绝不写 Emby。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import date
from typing import Any

from .library_mirror import (
    AUTHORITATIVE_META,
    AUTHORITATIVE_USER,
    STALE_DAYS,
    LibraryMirror,
    MirrorModel,
    normalize_emby_item,
)
from .recommend_common import as_day

log = logging.getLogger("xiaomusic")

PAGE_SIZE = 500
ITEM_FIELDS = (
    "Genres,ProductionYear,Path,UserDataLastPlayedDate,UserDataPlayCount,"
    "RunTimeTicks,ArtistItems,AlbumArtist,Album,IsFavorite,DateCreated"
)


class HttpMirrorClient:
    """只读 GET 适配器；transport 可注入（如 requests.Session）。"""

    def __init__(self, host: str, token: str, transport: Any = None, timeout: int = 120):
        self._host = str(host).rstrip("/")
        self._token = str(token)
        self._transport = transport
        self._timeout = timeout

    def get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        import requests

        if self._transport is None:
            self._transport = requests.Session()
        kwargs = {
            "params": params,
            "headers": {"X-Emby-Token": self._token},
            "timeout": self._timeout,
        }
        url = f"{self._host}{path}"
        try:
            response = self._transport.get(url, **kwargs)
        except requests.exceptions.SSLError:
            # 家用 Emby 常用自签证书：校验失败时仅本次降级重试，并明确告警。
            log.warning("Emby HTTPS 证书校验失败，改用不校验证书重试（自签环境）")
            response = self._transport.get(url, **dict(kwargs, verify=False))
        response.raise_for_status()
        return response.json()


def build_client(cfg, *, transport: Any = None, token: str | None = None):
    """按配置构造只读客户端；缺少 host/凭据时返回 None（调用方视为未配置）。"""
    if transport is not None and callable(getattr(transport, "get_json", None)):
        return transport
    host = str(getattr(cfg, "emby_host", "") or "")
    secret = str(token or getattr(cfg, "emby_api_key", "") or "")
    if not host or not secret:
        return None
    return HttpMirrorClient(host, secret, transport=transport)


def _emit(progress, **data):
    """进度回调绝不打断同步本身。"""
    if progress is None:
        return
    try:
        progress(data)
    except Exception:  # 进度展示失败不应影响同步
        pass


def fetch_library(
    client, user_id: str, *, page_size: int = PAGE_SIZE, progress=None
) -> list[dict[str, Any]]:
    """分页拉取全库 Audio；只调用客户端的只读 get_json。"""
    path = f"/emby/Users/{user_id}/Items"
    params = {
        "IncludeItemTypes": "Audio",
        "Recursive": "true",
        "Fields": ITEM_FIELDS,
        "StartIndex": 0,
        "Limit": page_size,
    }
    first = client.get_json(path, dict(params))
    rows = list(first.get("Items") or [])
    total = int(first.get("TotalRecordCount") or 0)
    pages = max(1, (total + page_size - 1) // page_size)
    _emit(
        progress, phase="fetch", page=1, pages=pages, items=len(rows), total=total
    )
    for start in range(page_size, total, page_size):
        page = client.get_json(path, dict(params, StartIndex=start))
        rows.extend(page.get("Items") or [])
        _emit(
            progress,
            phase="fetch",
            page=start // page_size + 1,
            pages=pages,
            items=len(rows),
            total=total,
        )
    return rows


def _normalized_items(rows: list[dict[str, Any]], user_id: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in rows:
        item = normalize_emby_item(raw, user_id)
        if item["id"]:
            result[item["id"]] = item
    return result


def _previous_state(mirror: LibraryMirror, cfg, user_id: str, day_number: int) -> dict[str, Any]:
    """从落库版本重建归约器输入；派生与用户态分开取，不写回任何输入。"""
    info = mirror.version_info()
    state = MirrorModel.init(
        stale_days=int(getattr(cfg, "mirror_stale_days", STALE_DAYS) or STALE_DAYS)
    )
    state["day"] = day_number
    state["mirror"]["version"] = int(info.get("version") or 0)
    state["mirror"]["watermark"] = int(info.get("watermark") or 0)
    state["mirror"]["last_sync_day"] = info.get("last_sync_day")
    items: dict[str, dict[str, Any]] = {}
    for row in mirror.snapshot(include_tombstones=True):
        item_id = str(row["id"])
        user_state = mirror.user_state(item_id, user_id) or {
            "favorite": False,
            "play_count": 0,
            "last_played_date": None,
        }
        items[item_id] = dict(row, users={user_id: user_state})
    state["mirror"]["items"] = items
    return state


def _diff_changes(
    previous: dict[str, dict[str, Any]], incoming: dict[str, dict[str, Any]], start_seq: int
) -> list[dict[str, Any]]:
    """把权威字段差异翻译成水位变更；派生字段从不进变更流。"""
    changes: list[dict[str, Any]] = []
    seq = start_seq

    def push(item_id: str, *, kind: str, field: str | None = None,
             value: Any = None, user_id: str | None = None) -> None:
        nonlocal seq
        change: dict[str, Any] = {"seq": seq, "id": item_id, "type": kind}
        if field is not None:
            change["field"] = field
            change["value"] = deepcopy(value)
        if user_id is not None:
            change["user_id"] = user_id
        changes.append(change)
        seq += 1

    for item_id, item in incoming.items():
        old = previous.get(item_id)
        if old is None:
            push(item_id, kind="add")
            continue
        for field in AUTHORITATIVE_META:
            if old.get(field) != item.get(field):
                push(item_id, kind="edit", field=field, value=item.get(field))
        old_users = old.get("users") or {}
        for user_id, user_state in (item.get("users") or {}).items():
            for field in AUTHORITATIVE_USER:
                if (old_users.get(user_id) or {}).get(field) != user_state.get(field):
                    push(
                        item_id, kind="edit", field=field,
                        value=user_state.get(field), user_id=user_id,
                    )
    for item_id, old in previous.items():
        if item_id not in incoming and not old.get("tombstone"):
            push(item_id, kind="delete")
    return changes


def _resolve_kind(kind: str | None, version: int, last_sync_day: Any, day_number: int) -> str:
    if kind in {"full", "daily", "weekly"}:
        return kind
    if not version:
        return "full"
    if last_sync_day is None or day_number - int(last_sync_day) >= 7:
        return "weekly"
    return "daily"


def sync_mirror(
    cfg,
    client=None,
    *,
    transport: Any = None,
    token: str | None = None,
    today: str | None = None,
    kind: str | None = None,
    mirror: LibraryMirror | None = None,
    progress=None,
) -> dict[str, Any]:
    """同步本地镜像；未配置或缺少只读传输时不产生任何副作用。"""
    db_path = str(getattr(cfg, "mirror_db_path", "") or "")
    if not db_path:
        return {"ok": False, "skipped": True, "reason": "unconfigured", "alerts": []}
    if client is None:
        client = build_client(cfg, transport=transport, token=token)
    if client is None:
        return {"ok": False, "skipped": True, "reason": "unconfigured", "alerts": []}

    user_id = str(getattr(cfg, "emby_user_id", "") or "default")
    day = as_day(today) or date.today()
    day_number = day.toordinal()
    own_mirror = mirror is None
    db = mirror or LibraryMirror(db_path)
    base = None
    try:
        _emit(progress, phase="prepare", kind=kind or "auto")
        base = _previous_state(db, cfg, user_id, day_number)
        rows = fetch_library(client, user_id, progress=progress)
        _emit(progress, phase="reconcile", items=len(rows))
        incoming = _normalized_items(rows, user_id)
        action = _resolve_kind(
            kind, base["mirror"]["version"], base["mirror"]["last_sync_day"], day_number
        )
        state = deepcopy(base)
        state["emby"]["items"] = incoming
        state["emby"]["watermark"] = len(incoming)
        state["emby"]["changes"] = []

        if action == "full":
            state = MirrorModel.reduce(state, {"type": "FULL_IMPORT"})
        elif action == "weekly":
            state = MirrorModel.reduce(state, {"type": "WEEKLY_RECONCILE"})
        else:
            state["emby"]["watermark"] = base["mirror"]["watermark"]
            # 墓碑复活：先清标记，再让增量变更流覆盖权威字段。
            for item_id in incoming:
                old = base["mirror"]["items"].get(item_id)
                if old and old.get("tombstone"):
                    state["mirror"]["items"][item_id]["tombstone"] = False
            state["emby"]["changes"] = _diff_changes(
                base["mirror"]["items"], incoming, base["mirror"]["watermark"] + 1
            )
            state = MirrorModel.reduce(state, {"type": "DAILY_SYNC"})

        db.commit(state)
        _emit(progress, phase="done", items=len(state["mirror"]["items"]))
        return {
            "ok": True,
            "skipped": False,
            "kind": action,
            "version": int(state["mirror"]["version"]),
            "items": len(state["mirror"]["items"]),
            "changed": len(state.get("changed") or []),
            "alerts": [],
        }
    except Exception as exc:  # 失败保留上一版：不 commit，记录告警后返回
        _emit(progress, phase="failed", reason=type(exc).__name__)
        log.warning(f"曲库镜像同步失败，保留上一版镜像: {type(exc).__name__}")
        alerts = []
        if base is not None:
            try:
                alerts = list(MirrorModel.reduce(base, {"type": "SYNC_FAIL"})["alerts"])
                for alert in alerts:
                    log.warning(alert)
            except Exception:  # pragma: no cover - 归约器输入异常也不打断出歌单
                pass
        return {
            "ok": False,
            "skipped": False,
            "reason": f"{type(exc).__name__}",
            "alerts": alerts,
        }
    finally:
        if own_mirror:
            db.close()
