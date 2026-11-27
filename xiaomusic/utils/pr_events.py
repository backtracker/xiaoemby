"""播放记录（Playback Reporting，PR）事件的只读读取与清洗。

本模块只做三件事：识别当前账号、只读查询 PR 事件、把原始行清洗成可归属的
有效播放。账号解析只回答「是谁」；取数按该账号的 UserId 跨所有客户端查询，
音箱会话的客户端/设备信息不参与事件过滤。
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

import requests

CLIENT_NAME = "XiaoEmby"
DEVICE_NAME = "小爱音箱"
SESSIONS_PATH = "/emby/Sessions"
PR_QUERY_PATH = "/emby/user_usage_stats/submit_custom_query"
PLAYBACK_TABLE = "PlaybackActivity"
LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
DEFAULT_LIMIT = 500


class PRFailure(StrEnum):
    """供上层决定降级的失败原因。"""

    ACCOUNT_UNRESOLVED = "account_unresolved"
    PLUGIN_UNAVAILABLE = "plugin_unavailable"
    QUERY_FAILED = "query_failed"
    NO_DATA = "no_data"
    TIMEOUT = "timeout"


class PRTransportError(Exception):
    """客户端侧失败；消息不含 URL、令牌或 SQL 内容。"""

    reason = PRFailure.QUERY_FAILED

    def __init__(self, message: str = "PR 请求失败"):
        super().__init__(message)


class PRTimeout(PRTransportError):
    reason = PRFailure.TIMEOUT

    def __init__(self) -> None:
        super().__init__("PR 请求超时")


class PRPluginUnavailable(PRTransportError):
    reason = PRFailure.PLUGIN_UNAVAILABLE

    def __init__(self) -> None:
        super().__init__("PR 插件端点不可用")


class PRQueryFailed(PRTransportError):
    reason = PRFailure.QUERY_FAILED

    def __init__(self) -> None:
        super().__init__("PR 查询失败")


@dataclass(frozen=True)
class PREvent:
    """一次清洗后的播放：切段已合并，完成率与有效标记已给出。"""

    user_id: str
    item_id: str
    device_name: str
    started_at: datetime
    duration_seconds: float
    completion_rate: float | None
    is_valid: bool
    is_extremely_short: bool
    is_incomplete: bool
    segment_count: int
    item_name: str = ""
    client_name: str = ""
    low_confidence: bool = False


EXTREME_SHORT_SECONDS = 60.0
VALID_COMPLETION_RATE = 0.8
SEGMENT_INCOMPLETE_RATIO = 0.95
MAX_SEGMENT_GAP_SECONDS = 60.0
FALLBACK_SEGMENT_GAP_SECONDS = 10.0
FALLBACK_SHORT_SECONDS = 60.0

_FORBIDDEN_SQL = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|REPLACE|PRAGMA|ATTACH|DETACH|"
    r"VACUUM|TRIGGER|BEGIN|COMMIT|ROLLBACK|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
_LIMIT_SQL = re.compile(r"\bLIMIT\s+\d+\b", re.IGNORECASE)


def validate_select_sql(sql: str, *, require_limit: bool = True) -> str:
    """只读守卫：只接受单条带 LIMIT 的 SELECT，拒绝分号与任何写/危险语句。"""
    if not isinstance(sql, str):
        raise ValueError("PR 查询必须是字符串")
    stripped = sql.strip()
    if not re.match(r"^SELECT\b", stripped, re.IGNORECASE):
        raise ValueError("PR 查询只接受 SELECT")
    if ";" in stripped:
        raise ValueError("PR 查询不接受分号或多条语句")
    if _FORBIDDEN_SQL.search(stripped):
        raise ValueError("PR 查询包含不允许的写操作或 PRAGMA")
    if require_limit and not _LIMIT_SQL.search(stripped):
        raise ValueError("PR 查询必须带 LIMIT")
    return stripped


def build_events_sql(user_id: str, limit: int = DEFAULT_LIMIT) -> str:
    """构造按账号取全部客户端 Audio 事件的只读查询。"""
    if not user_id:
        raise ValueError("缺少账号 UserId")
    count = int(limit)
    if count <= 0:
        raise ValueError("LIMIT 必须为正整数")
    escaped = str(user_id).replace("'", "''")
    columns = "DateCreated, UserId, ItemId, ItemType, ItemName, PlayDuration, DeviceName"
    sql = (
        f"SELECT {columns} FROM {PLAYBACK_TABLE} "
        f"WHERE UserId = '{escaped}' AND ItemType = 'Audio' "
        f"ORDER BY DateCreated DESC LIMIT {count}"
    )
    return validate_select_sql(sql)


def parse_query_response(payload: Mapping[str, Any]) -> tuple[list[dict[str, str]], PRFailure | None]:
    """按 message 判成功，而不是只看 HTTP 200；单元格保留插件返回的字符串。"""
    message = str(payload.get("message") or "")
    columns = payload.get("colums") or payload.get("columns") or []
    results = payload.get("results") or []
    if message:
        if "no data returned" in message.lower() and not results:
            return [], PRFailure.NO_DATA
        return [], PRFailure.QUERY_FAILED
    if not results:
        return [], PRFailure.NO_DATA
    rows = [
        dict(zip(columns, values, strict=False))
        for values in results
        if isinstance(values, (list, tuple))
    ]
    return rows, None


def parse_date_created(value: Any) -> datetime:
    """把无时区的本地时间文本按 UTC+8 解释；带时区的统一换算到 UTC+8。"""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("空的 DateCreated")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def _runtime_seconds(runtimes: Mapping[str, Any] | None, item_id: str) -> float | None:
    if not runtimes:
        return None
    value = runtimes.get(item_id)
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def clean_events(
    rows: Iterable[Mapping[str, Any]],
    *,
    runtimes: Mapping[str, Any] | None = None,
) -> tuple[PREvent, ...]:
    """剔除不可用行并按 (UserId, ItemId, DeviceName) 合并切段。

    合并判据是「上一段未完成 + 下一段紧接上一段结束时刻」，且中间不能有其它
    曲目的行；完整重播、跨设备与跨用户的行不合并。缺少曲目时长时退化为短段
    加紧邻判据，并标记低置信度。
    """
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        try:
            started_at = parse_date_created(row.get("DateCreated"))
        except (TypeError, ValueError):
            continue
        user_id = str(row.get("UserId") or "").strip()
        item_id = str(row.get("ItemId") or "").strip()
        if not user_id or not item_id:
            continue
        item_type = str(row.get("ItemType") or "").strip()
        if item_type and item_type != "Audio":
            continue
        try:
            duration = float(row.get("PlayDuration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        normalized.append(
            {
                "user_id": user_id,
                "item_id": item_id,
                "device_name": str(row.get("DeviceName") or ""),
                "item_name": str(row.get("ItemName") or ""),
                "client_name": str(row.get("ClientName") or ""),
                "started_at": started_at,
                "duration": max(duration, 0.0),
                "index": index,
            }
        )

    normalized.sort(key=lambda row: (row["started_at"], row["index"]))
    for order, row in enumerate(normalized):
        row["order"] = order

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in normalized:
        groups[(row["user_id"], row["item_id"], row["device_name"])].append(row)

    chains: list[dict[str, Any]] = []
    for group in groups.values():
        chain: dict[str, Any] | None = None
        for row in group:
            if chain is None:
                chain = dict(row, total=row["duration"], segment_count=1, last_order=row["order"])
                continue
            runtime = _runtime_seconds(runtimes, chain["item_id"])
            max_gap = (
                MAX_SEGMENT_GAP_SECONDS if runtime is not None else FALLBACK_SEGMENT_GAP_SECONDS
            )
            incomplete_before = (
                chain["total"] < SEGMENT_INCOMPLETE_RATIO * runtime
                if runtime is not None
                else chain["total"] < FALLBACK_SHORT_SECONDS
            )
            gap = (
                row["started_at"]
                - (chain["started_at"] + timedelta(seconds=chain["total"]))
            ).total_seconds()
            if (
                row["order"] == chain["last_order"] + 1
                and 0 <= gap <= max_gap
                and incomplete_before
            ):
                chain["total"] += row["duration"]
                chain["segment_count"] += 1
                chain["last_order"] = row["order"]
            else:
                chains.append(chain)
                chain = dict(row, total=row["duration"], segment_count=1, last_order=row["order"])
        if chain is not None:
            chains.append(chain)

    events: list[PREvent] = []
    for chain in chains:
        runtime = _runtime_seconds(runtimes, chain["item_id"])
        total = chain["total"]
        completion = min(total, runtime) / runtime if runtime is not None else None
        extremely_short = total < EXTREME_SHORT_SECONDS
        incomplete = completion is not None and completion < VALID_COMPLETION_RATE
        events.append(
            PREvent(
                user_id=chain["user_id"],
                item_id=chain["item_id"],
                device_name=chain["device_name"],
                started_at=chain["started_at"],
                duration_seconds=total,
                completion_rate=completion,
                is_valid=not extremely_short and not incomplete,
                is_extremely_short=extremely_short,
                is_incomplete=incomplete,
                segment_count=chain["segment_count"],
                item_name=chain["item_name"],
                client_name=chain["client_name"],
                low_confidence=runtime is None,
            )
        )
    return tuple(sorted(events, key=lambda event: (event.started_at, event.item_id, event.device_name)))


def _activity_key(value: Any) -> float:
    """把 LastActivityDate 转成可比较的时间戳；不可解析时排到最后。"""
    if not isinstance(value, str) or not value:
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.timestamp()


def resolve_current_user(sessions: Iterable[Mapping[str, Any]]) -> str | None:
    """从 Emby 会话中解析当前账号 UserId。

    精确匹配 Client=XiaoEmby 且 DeviceName=小爱音箱，丢弃无 UserId 的会话，
    按 LastActivityDate 取最新。诊断会话与其它客户端一律不参与。
    """
    candidates = [
        session
        for session in sessions
        if session.get("Client") == CLIENT_NAME
        and session.get("DeviceName") == DEVICE_NAME
        and session.get("UserId")
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda session: _activity_key(session.get("LastActivityDate")))
    return str(latest["UserId"])


class EmbyPRClient:
    """PR 取数的注入缝：只持有 host 与调用方传入的 key，不打印也不落盘。"""

    def __init__(
        self,
        host: str,
        api_key: str,
        *,
        transport: Any = None,
        timeout: float = 15.0,
        api_key_in: str = "header",
    ) -> None:
        if api_key_in not in ("header", "query"):
            raise ValueError("api_key_in 只能是 header 或 query")
        self._host = str(host).rstrip("/")
        self._api_key = api_key
        self._transport = transport if transport is not None else requests.Session()
        self._timeout = timeout
        self._api_key_in = api_key_in

    def _request(self, method: str, path: str, *, body: dict[str, Any] | None = None) -> Any:
        url = f"{self._host}{path}"
        headers = {"Accept": "application/json"}
        params: dict[str, str] = {}
        if self._api_key_in == "header":
            headers["X-Emby-Token"] = self._api_key
        else:
            params["api_key"] = self._api_key
        kwargs: dict[str, Any] = {"headers": headers, "params": params, "timeout": self._timeout}
        if body is not None:
            kwargs["json"] = body
        try:
            if method == "GET":
                response = self._transport.get(url, **kwargs)
            else:
                response = self._transport.post(url, **kwargs)
        except requests.exceptions.Timeout:
            raise PRTimeout() from None
        except requests.exceptions.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise _status_error(status) from None
        status = getattr(response, "status_code", None)
        if status is not None and not 200 <= status < 300:
            raise _status_error(status)
        try:
            return response.json()
        except (TypeError, ValueError):
            raise PRQueryFailed() from None

    def get_sessions(self) -> list[dict[str, Any]]:
        payload = self._request("GET", SESSIONS_PATH)
        return payload if isinstance(payload, list) else []

    def submit_custom_query(self, sql: str) -> dict[str, Any]:
        validate_select_sql(sql)
        payload = self._request(
            "POST",
            PR_QUERY_PATH,
            body={"CustomQueryString": sql, "ReplaceUserId": False},
        )
        return payload if isinstance(payload, dict) else {}


def _status_error(status: int | None) -> PRTransportError:
    if status == 404:
        return PRPluginUnavailable()
    return PRQueryFailed()


@dataclass(frozen=True)
class PRReadResult:
    """读取结果：成功给出事件，失败给出可直接用于降级的结构化原因。"""

    ok: bool
    reason: PRFailure | None
    user_id: str | None
    events: tuple[PREvent, ...] = ()
    raw_rows: int = 0
    dropped_rows: int = 0

    @classmethod
    def failure(cls, reason: PRFailure, *, user_id: str | None = None) -> "PRReadResult":
        return cls(ok=False, reason=reason, user_id=user_id)


def read_pr_events(
    client: EmbyPRClient,
    *,
    user_id: str | None = None,
    runtimes: Mapping[str, Any] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> PRReadResult:
    """解析账号 → 只读查询 → 清洗事件；传输与查询失败转成结构化原因。"""
    if not user_id:
        try:
            sessions = client.get_sessions()
        except PRTransportError as exc:
            return PRReadResult.failure(exc.reason)
        user_id = resolve_current_user(sessions)
        if not user_id:
            return PRReadResult.failure(PRFailure.ACCOUNT_UNRESOLVED)

    sql = build_events_sql(user_id, limit)
    try:
        payload = client.submit_custom_query(sql)
    except PRTransportError as exc:
        return PRReadResult.failure(exc.reason, user_id=user_id)

    rows, reason = parse_query_response(payload)
    if reason is not None:
        return PRReadResult(reason=reason, ok=False, user_id=user_id, raw_rows=len(rows))

    events = clean_events(rows, runtimes=runtimes)
    kept = sum(event.segment_count for event in events)
    return PRReadResult(
        ok=True,
        reason=None,
        user_id=user_id,
        events=events,
        raw_rows=len(rows),
        dropped_rows=max(len(rows) - kept, 0),
    )
