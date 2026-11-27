"""本地 Emby 曲库镜像：纯状态归约 + 落库后的只读查询。

本模块只负责「镜像的数据形状与同步状态机」以及「对候选池 / 编排模块的只读接口」。
不连 Emby、不拼播放地址、不改音频代理——播放 URL 仍由 Emby 链路生成（见 ADR-0001 / ADR-0002）。
"""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from typing import Any

# 陈旧阈值起点：超过这个天数未成功同步就回退 v2（配置化，init 时可覆盖）。
STALE_DAYS = 7

# Emby 唯一权威字段：元数据（全库共享）+ 用户态（按 UserId 分桶）。
# 这些字段一律由 Emby 覆盖，本地不写回。
AUTHORITATIVE_META = (
    "name",
    "genres",
    "year",
    "artist",
    "album_artist",
    "album",
    "date_created",
    "runtime_ticks",
    "path",
    "ext",
)
AUTHORITATIVE_USER = ("favorite", "play_count", "last_played_date")

# 本地派生的画像字段，另存且不受 Emby 覆盖影响。
DERIVED_FIELDS = ("taste", "penalty")

# 变更水位字段的假设（必须实测后再接定时任务，切勿凭空假设一个字段存在）：
# 2026-11-27 只读探测 /emby/Users/{UserId}/Items（72577 首 Audio）确认的可用字段只有
# Id / Name / Genres / ProductionYear / RunTimeTicks / Album / AlbumArtist /
# ArtistItems / Path / 顶层 DateCreated / 嵌套 UserData{PlayCount,LastPlayedDate,IsFavorite}，
# 分页靠 StartIndex/Limit + 顶层 TotalRecordCount。响应中没有确认可用的「库变更水位」字段。
# 因此这里假设：增量水位候选为 MinDateLastSaved / MinDateLastSavedForUser（若实测该 Emby
# 版本支持），再换算成归约层单调递增的 seq；**在实测确认前，增量同步退化为每日全量对账**，
# 不依赖任何未验证字段。纯归约层只认抽象 seq，便于日后替换适配层而不改对外接口。
# 用户态在原始响应里嵌套在 item["UserData"]，由 normalize_emby_item 摊平后再进归约器。


def _clone(value: Any) -> Any:
    return deepcopy(value)


def _copy_authoritative(item: dict[str, Any]) -> dict[str, Any]:
    copied = {key: _clone(item.get(key)) for key in AUTHORITATIVE_META}
    copied["users"] = _clone(item.get("users") or {})
    return copied


def _blank_mirror_item(item: dict[str, Any]) -> dict[str, Any]:
    result = _copy_authoritative(item)
    result["taste"] = 0.5
    result["penalty"] = 0.0
    result["tombstone"] = bool(item.get("deleted", False))
    return result


def normalize_emby_item(raw: dict[str, Any], user_id: str) -> dict[str, Any]:
    """把实测的 Emby /Items 单条响应摊平为归约器的内部条目形状（纯函数）。

    实测坑：PlayCount / LastPlayedDate / IsFavorite 不在顶层，而在 item["UserData"]；
    DateCreated 在顶层，但必须显式列进 Fields 才稳定返回。
    """
    user_data = raw.get("UserData") or {}
    user_state = {
        "favorite": bool(user_data.get("IsFavorite", False)),
        "play_count": user_data.get("PlayCount", 0) or 0,
        "last_played_date": user_data.get("LastPlayedDate"),
    }
    path = raw.get("Path") or ""
    ext = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    return {
        "id": str(raw.get("Id", "")),
        "name": raw.get("Name", ""),
        "genres": list(raw.get("Genres") or []),
        "year": raw.get("ProductionYear") or 0,
        "artist": [a.get("Name", "") for a in (raw.get("ArtistItems") or [])],
        "album_artist": raw.get("AlbumArtist", ""),
        "album": raw.get("Album", ""),
        "date_created": raw.get("DateCreated", ""),
        "runtime_ticks": raw.get("RunTimeTicks", 0) or 0,
        "path": path,
        "ext": ext,
        "deleted": False,
        "users": {user_id: user_state},
    }


def _write_authoritative_field(
    target: dict[str, Any], field: str, value: Any, user_id: str | None = None
) -> None:
    """唯一权威字段写入口：用户态按 UserId 分桶，元数据写在条目顶层。"""
    if field in AUTHORITATIVE_USER:
        target.setdefault("users", {}).setdefault(user_id or "default", {})[field] = _clone(
            value
        )
    else:
        target[field] = _clone(value)


def _overwrite_authoritative(target: dict[str, Any], source: dict[str, Any]) -> None:
    """全量对账：权威字段整体覆盖，派生 taste / penalty 不参与。"""
    for field, value in _copy_authoritative(source).items():
        target[field] = value
    target["tombstone"] = False


def _reduce_emby_add(state: dict[str, Any], action: dict[str, Any]) -> None:
    item = _clone(action["item"])
    item.setdefault("users", {})
    state["emby"]["items"][item["id"]] = item
    state["emby"]["watermark"] += 1
    state["emby"]["changes"].append(
        {"seq": state["emby"]["watermark"], "id": item["id"], "type": "add"}
    )
    state["last_action"] = f"Emby 新增曲目《{item.get('name', '')}》。"


def _reduce_full_import(state: dict[str, Any]) -> None:
    state["mirror"]["items"] = {
        item_id: _blank_mirror_item(item)
        for item_id, item in state["emby"]["items"].items()
    }
    state["mirror"]["version"] = 1
    state["mirror"]["watermark"] = state["emby"]["watermark"]
    state["mirror"]["last_sync_day"] = state["day"]
    state["alerts"] = []
    state["last_result"] = None
    state["last_action"] = "首次全量导入完成：镜像建立，水位对齐 Emby。"


def _reduce_emby_edit(state: dict[str, Any], action: dict[str, Any]) -> None:
    item = state["emby"]["items"].get(action["id"])
    if item is None or item.get("deleted"):
        state["last_action"] = "Emby 里没有这首歌（或已删除）。"
        return
    field = action["field"]
    _write_authoritative_field(item, field, action["value"], action.get("user_id"))
    state["emby"]["watermark"] += 1
    state["emby"]["changes"].append(
        {
            "seq": state["emby"]["watermark"],
            "id": action["id"],
            "type": "edit",
            "field": field,
            "value": _clone(action["value"]),
            "user_id": action.get("user_id"),
        }
    )
    state["last_action"] = f"Emby（权威）改了《{item.get('name', '')}》的 {field}。"


def _reduce_emby_delete(state: dict[str, Any], action: dict[str, Any]) -> None:
    item = state["emby"]["items"].get(action["id"])
    if item is None or item.get("deleted"):
        state["last_action"] = "这首歌已删除或不存在。"
        return
    item["deleted"] = True
    state["emby"]["watermark"] += 1
    state["emby"]["changes"].append(
        {"seq": state["emby"]["watermark"], "id": action["id"], "type": "delete"}
    )
    state["last_action"] = f"Emby 删除了《{item.get('name', '')}》。"


def _reduce_daily_sync(state: dict[str, Any]) -> None:
    if not state["mirror"]["version"]:
        state["last_action"] = "镜像尚未建立——先做首次全量。"
        return
    changed: list[str] = []
    consumed = 0
    for change in sorted(state["emby"]["changes"], key=lambda item: item["seq"]):
        if change["seq"] <= state["mirror"]["watermark"]:
            continue
        item_id = change["id"]
        mirror_item = state["mirror"]["items"].get(item_id)
        if change["type"] == "delete":
            if mirror_item is not None:
                mirror_item["tombstone"] = True
                changed.append(item_id)
        elif change["type"] == "add":
            source_item = state["emby"]["items"].get(item_id)
            if source_item is not None and mirror_item is None:
                state["mirror"]["items"][item_id] = _blank_mirror_item(source_item)
                changed.append(item_id)
        elif mirror_item is not None:
            _write_authoritative_field(
                mirror_item, change["field"], change["value"], change.get("user_id")
            )
            changed.append(item_id)
        state["mirror"]["watermark"] = max(
            state["mirror"]["watermark"], change["seq"]
        )
        consumed += 1
    state["changed"] = sorted(set(changed))
    state["mirror"]["last_sync_day"] = state["day"]
    state["mirror"]["version"] += 1
    state["alerts"] = [
        alert for alert in state["alerts"] if not alert.startswith("同步失败")
    ]
    state["last_result"] = None
    state["last_action"] = (
        f"每日水位增量：消费 {consumed} 个 Emby 变更，水位推进到 "
        f"{state['mirror']['watermark']}，派生字段不动。"
    )


def _reduce_local_edit(state: dict[str, Any], action: dict[str, Any]) -> None:
    item = state["mirror"]["items"].get(action["id"])
    if item is None:
        state["last_action"] = "镜像里没有这首歌——先做首次全量。"
        return
    field = action["field"]
    _write_authoritative_field(item, field, action["value"], action.get("user_id"))
    state["changed"] = [action["id"]]
    state["last_action"] = (
        f"模拟本地手改：镜像里《{item.get('name', '')}》的 {field} 被改。"
    )


def _reduce_weekly_reconcile(state: dict[str, Any]) -> None:
    if not state["mirror"]["version"]:
        state["last_action"] = "镜像尚未建立——先做首次全量。"
        return
    changed: list[str] = []
    all_ids = set(state["emby"]["items"]) | set(state["mirror"]["items"])
    for item_id in all_ids:
        emby_item = state["emby"]["items"].get(item_id)
        mirror_item = state["mirror"]["items"].get(item_id)
        if emby_item is None or emby_item.get("deleted"):
            if mirror_item is not None:
                mirror_item["tombstone"] = True
                changed.append(item_id)
        elif mirror_item is None:
            state["mirror"]["items"][item_id] = _blank_mirror_item(emby_item)
            changed.append(item_id)
        else:
            _overwrite_authoritative(mirror_item, emby_item)
            changed.append(item_id)
    state["changed"] = sorted(set(changed))
    state["mirror"]["watermark"] = state["emby"]["watermark"]
    state["mirror"]["last_sync_day"] = state["day"]
    state["mirror"]["version"] += 1
    state["last_result"] = None
    state["last_action"] = (
        "每周全量对账完成：权威字段被 Emby 覆盖，删除项打墓碑，"
        "派生 taste / penalty 保留。"
    )


def _reduce_sync_fail(state: dict[str, Any]) -> None:
    version = state["mirror"]["version"]
    state["alerts"].append(
        f"同步失败（第 {state['day']} 天）：保留上一版镜像 v{version}，"
        f"水位 {state['mirror']['watermark']} 不动，告警已记录。"
    )
    state["last_action"] = (
        "同步失败：保留上一版镜像并告警——不打断出歌单；陈旧天数继续累计。"
    )


def _reduce_query(state: dict[str, Any]) -> None:
    active = sum(
        1 for item in state["mirror"]["items"].values() if not item.get("tombstone")
    )
    if is_stale(state):
        last_day = state["mirror"]["last_sync_day"]
        reason = (
            "镜像未建立"
            if last_day is None
            else f"镜像陈旧 {state['day'] - last_day} 天"
        )
        state["last_result"] = {
            "source": "v2",
            "candidates": "远程分层采样 800~3000 首",
            "fallback_reason": reason,
            "detail": "镜像不可用/陈旧，整链回退 v2；歌单照常产出。",
        }
        state["last_action"] = (
            "生成今日歌单：镜像不可用/陈旧 → 回退 v2 模式，"
            "不打断出歌单，记录 source 与 fallback_reason。"
        )
    else:
        state["last_result"] = {
            "source": "镜像",
            "candidates": f"全库 {active} 首",
            "detail": "候选池与相似度都查本地镜像；播放地址仍走 Emby。",
        }
        state["last_action"] = (
            f"生成今日歌单：走本地镜像，候选池 = 全库 active {active} 首。"
        )


class MirrorModel:
    """决策级状态模型的纯归约器：无 DOM、无全局状态、无 I/O。"""

    STALE_DAYS = STALE_DAYS

    @staticmethod
    def init(stale_days: int = STALE_DAYS) -> dict[str, Any]:
        return {
            "day": 0,
            "stale_days": stale_days,
            "emby": {"watermark": 0, "changes": [], "items": {}},
            "mirror": {
                "version": 0,
                "watermark": 0,
                "last_sync_day": None,
                "items": {},
            },
            # PR 逐次事件在镜像之外，仅在此占位说明关系；真实事件走插件只读 API。
            "pr_events": [],
            "alerts": [],
            "last_action": "尚未导入——此刻候选池为空。",
            "last_result": None,
            "changed": [],
        }

    @staticmethod
    def from_emby_pages(
        pages: list[list[dict[str, Any]]],
        user_id: str,
        stale_days: int = STALE_DAYS,
    ) -> dict[str, Any]:
        """把分页拉取的原始 Emby 页（StartIndex/Limit）纯函数式地归约为已建立镜像。"""
        state = MirrorModel.init(stale_days=stale_days)
        for page in pages:
            for raw in page:
                state = MirrorModel.reduce(
                    state,
                    {
                        "type": "EMBY_ADD",
                        "item": normalize_emby_item(raw, user_id),
                    },
                )
        return MirrorModel.reduce(state, {"type": "FULL_IMPORT"})

    @staticmethod
    def reduce(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        """纯归约入口：按 action 分派到具名处理器，返回值永不复用输入对象。"""
        result = _clone(state)
        result["changed"] = []
        kind = action.get("type")
        if kind == "EMBY_ADD":
            _reduce_emby_add(result, action)
        elif kind == "FULL_IMPORT":
            _reduce_full_import(result)
        elif kind == "EMBY_EDIT":
            _reduce_emby_edit(result, action)
        elif kind == "EMBY_DELETE":
            _reduce_emby_delete(result, action)
        elif kind == "DAILY_SYNC":
            _reduce_daily_sync(result)
        elif kind == "LOCAL_EDIT":
            _reduce_local_edit(result, action)
        elif kind == "WEEKLY_RECONCILE":
            _reduce_weekly_reconcile(result)
        elif kind == "SYNC_FAIL":
            _reduce_sync_fail(result)
        elif kind == "QUERY":
            _reduce_query(result)
        elif kind == "ADVANCE_DAYS":
            n = int(action.get("n", 1))
            result["day"] += n
            result["last_action"] = f"时间推进 {action.get('n', 1)} 天：现在是第 {result['day']} 天。"
        return result


def is_stale(state: dict[str, Any], today: int | None = None) -> bool:
    """镜像未建立或距最近成功同步超过配置天数即为陈旧。"""
    mirror = state["mirror"]
    if mirror["version"] == 0 or mirror["last_sync_day"] is None:
        return True
    now = state["day"] if today is None else today
    return now - mirror["last_sync_day"] > state.get("stale_days", STALE_DAYS)


def source(state: dict[str, Any], today: int | None = None) -> str:
    """整条链路的数据来源：镜像可用返回「镜像」，否则回退 v2。"""
    return "v2" if is_stale(state, today) else "镜像"


class LibraryMirror:
    """SQLite 落库 + 只读查询；写侧只有 commit（成功同步时原子替换一版）。"""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    genres TEXT,
                    year INTEGER,
                    artist TEXT,
                    album_artist TEXT,
                    album TEXT,
                    date_created TEXT,
                    runtime_ticks INTEGER,
                    path TEXT,
                    ext TEXT,
                    taste REAL,
                    penalty REAL,
                    tombstone INTEGER
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_state (
                    item_id TEXT,
                    user_id TEXT,
                    favorite INTEGER,
                    play_count INTEGER,
                    last_played_date TEXT,
                    PRIMARY KEY (item_id, user_id)
                )
                """
            )

    def close(self) -> None:
        self._conn.close()

    def _set_meta(self, values: dict[str, Any]) -> None:
        for key, value in values.items():
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    def _get_meta(self, key: str, default: Any = None) -> Any:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def commit(self, state: dict[str, Any]) -> None:
        """原子替换整版镜像；调用方只在同步成功时调用，失败则保留上一版。"""
        mirror = state["mirror"]
        with self._conn:
            self._conn.execute("DELETE FROM tracks")
            self._conn.execute("DELETE FROM user_state")
            for item_id, item in mirror["items"].items():
                self._conn.execute(
                    """
                    INSERT INTO tracks (
                        id, name, genres, year, artist, album_artist, album,
                        date_created, runtime_ticks, path, ext, taste, penalty, tombstone
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        item.get("name", ""),
                        json.dumps(item.get("genres") or []),
                        item.get("year", 0),
                        json.dumps(item.get("artist") or []),
                        item.get("album_artist", ""),
                        item.get("album", ""),
                        item.get("date_created", ""),
                        item.get("runtime_ticks", 0),
                        item.get("path", ""),
                        item.get("ext", ""),
                        item.get("taste", 0.5),
                        item.get("penalty", 0.0),
                        int(bool(item.get("tombstone"))),
                    ),
                )
                for user_id, user_state in (item.get("users") or {}).items():
                    self._conn.execute(
                        """
                        INSERT INTO user_state (
                            item_id, user_id, favorite, play_count, last_played_date
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            item_id,
                            user_id,
                            int(bool(user_state.get("favorite"))),
                            user_state.get("play_count", 0),
                            user_state.get("last_played_date"),
                        ),
                    )
            self._set_meta(
                {
                    "version": mirror["version"],
                    "watermark": mirror["watermark"],
                    "last_sync_day": mirror["last_sync_day"],
                    "stale_days": state.get("stale_days", STALE_DAYS),
                }
            )

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["genres"] = json.loads(result["genres"])
        result["artist"] = json.loads(result["artist"])
        result["tombstone"] = bool(result["tombstone"])
        return result

    def snapshot(self, include_tombstones: bool = False) -> list[dict[str, Any]]:
        """全库快照；默认排除墓碑。"""
        sql = "SELECT * FROM tracks"
        if not include_tombstones:
            sql += " WHERE tombstone = 0"
        return [self._row_to_dict(row) for row in self._conn.execute(sql)]

    def tombstones(self) -> list[dict[str, Any]]:
        """已删除曲目（墓碑），不物理删除，也不进候选池。"""
        return [
            self._row_to_dict(row)
            for row in self._conn.execute("SELECT * FROM tracks WHERE tombstone = 1")
        ]

    def candidates(
        self,
        kind: str = "all",
        *,
        genre: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """候选池只读查询：全库 / 按流派 / 新发行；默认排除墓碑。"""
        if kind == "all":
            rows = self.snapshot()
        elif kind == "genre":
            if not genre:
                raise ValueError("按流派查询需要提供 genre")
            rows = [row for row in self.snapshot() if genre in row["genres"]]
        elif kind == "new":
            rows = sorted(
                self.snapshot(),
                key=lambda row: (row["year"], row["date_created"]),
                reverse=True,
            )
        else:
            raise ValueError(f"未知的候选池查询类型: {kind}")
        return rows[:limit] if limit is not None else rows

    def version_info(self) -> dict[str, Any]:
        """已提交镜像的版本 / 水位 / 最近同步日 / 陈旧阈值，供编排模块判新鲜度。"""
        return {
            "version": self._get_meta("version", 0),
            "watermark": self._get_meta("watermark", 0),
            "last_sync_day": self._get_meta("last_sync_day"),
            "stale_days": self._get_meta("stale_days", STALE_DAYS),
        }

    def user_state(self, item_id: str, user_id: str) -> dict[str, Any] | None:
        """按条目 Id + UserId 取用户态；无记录返回 None。"""
        row = self._conn.execute(
            "SELECT favorite, play_count, last_played_date FROM user_state "
            "WHERE item_id = ? AND user_id = ?",
            (str(item_id), user_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "favorite": bool(row["favorite"]),
            "play_count": row["play_count"],
            "last_played_date": row["last_played_date"],
        }
