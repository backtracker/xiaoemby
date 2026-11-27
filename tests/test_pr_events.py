"""播放记录（PR）事件读取与清洗：只通过注入客户端与纯函数验证外部行为。"""

from datetime import UTC, timedelta
from types import SimpleNamespace

import pytest
import requests

from xiaomusic.utils.pr_events import (
    EmbyPRClient,
    PRFailure,
    PRPluginUnavailable,
    PRQueryFailed,
    PRTimeout,
    build_events_sql,
    clean_events,
    parse_date_created,
    parse_query_response,
    read_pr_events,
    resolve_current_user,
    validate_select_sql,
)

SPEAKER_SESSIONS = [
    {"Client": "XiaoEmby", "DeviceName": "小爱音箱", "UserId": None, "LastActivityDate": "2026-11-27T12:00:00Z"},
    {"Client": "XiaoEmby", "DeviceName": "小爱音箱", "UserId": "nick", "LastActivityDate": "2026-11-27T00:40:00Z"},
]


def http_json(payload, status=200):
    return SimpleNamespace(status_code=status, json=lambda: payload)


class FakeTransport:
    """替代 requests.Session：记录请求，按脚本返回响应或抛异常。"""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def _next(self, call):
        self.calls.append(call)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def get(self, url, **kwargs):
        return self._next(("GET", url, kwargs))

    def post(self, url, **kwargs):
        return self._next(("POST", url, kwargs))


def pr_row(start, duration, *, user="nick", item="973820", device="小爱音箱", item_type="Audio"):
    """模拟插件返回：所有单元格都是字符串。"""
    return {
        "DateCreated": start,
        "UserId": user,
        "ItemId": item,
        "ItemType": item_type,
        "ItemName": f"曲目 {item}",
        "PlayDuration": str(duration),
        "DeviceName": device,
    }


def test_resolve_current_user_picks_latest_exact_speaker_session_with_user():
    sessions = [
        {
            "Client": "XiaoEmby",
            "DeviceName": "小爱音箱",
            "UserId": "old-user",
            "LastActivityDate": "2026-11-25T00:00:00Z",
        },
        {
            "Client": "XiaoEmby",
            "DeviceName": "小爱音箱",
            "UserId": None,
            "LastActivityDate": "2026-11-27T00:00:00Z",
        },
        {
            "Client": "XiaoEmby",
            "DeviceName": "diagplay",
            "UserId": "diag-user",
            "LastActivityDate": "2026-11-28T00:00:00Z",
        },
        {
            "Client": "RustMusic",
            "DeviceName": "小爱音箱",
            "UserId": "other-client",
            "LastActivityDate": "2026-11-28T00:00:00Z",
        },
        {
            "Client": "XiaoEmby",
            "DeviceName": "小爱音箱",
            "UserId": "nick",
            "LastActivityDate": "2026-11-26T00:00:00Z",
        },
    ]

    assert resolve_current_user(sessions) == "nick"


def test_resolve_current_user_returns_none_when_no_identifiable_session():
    sessions = [
        {"Client": "XiaoEmby", "DeviceName": "小爱音箱", "UserId": None},
        {"Client": "XiaoEmby", "DeviceName": "diagplay", "UserId": "diag-user"},
    ]

    assert resolve_current_user(sessions) is None
    assert resolve_current_user([]) is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM PlaybackActivity;",
        "SELECT * FROM PlaybackActivity LIMIT 10; DROP TABLE PlaybackActivity",
        "DELETE FROM PlaybackActivity LIMIT 1",
        "PRAGMA table_info(PlaybackActivity) LIMIT 1",
        "INSERT INTO PlaybackActivity VALUES (1) LIMIT 1",
        "SELECT * FROM PlaybackActivity",  # 缺少 LIMIT
    ],
)
def test_read_only_guard_rejects_unsafe_sql(sql):
    with pytest.raises(ValueError):
        validate_select_sql(sql)


def test_events_sql_is_single_limited_select_scoped_by_user():
    sql = build_events_sql("user' OR 1=1 --", limit=42)

    assert validate_select_sql(sql) == sql
    assert sql.count("SELECT") == 1
    assert "user'' OR 1=1 --" in sql
    assert "ItemType = 'Audio'" in sql
    assert "LIMIT 42" in sql
    assert "ClientName = " not in sql and "DeviceName = " not in sql


def test_query_response_success_parses_string_cells():
    payload = {
        "colums": ["DateCreated", "PlayDuration"],
        "results": [["2026-11-27 07:51:01.1234567", "26"]],
        "message": "",
    }

    rows, reason = parse_query_response(payload)

    assert reason is None
    assert rows == [{"DateCreated": "2026-11-27 07:51:01.1234567", "PlayDuration": "26"}]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"message": "Query executed, no data returned. Number of rows effected : 0", "results": []},
            PRFailure.NO_DATA,
        ),
        (
            {"message": "Error Running Query: no such table: PlaybackActivity", "results": []},
            PRFailure.QUERY_FAILED,
        ),
        ({"message": "", "colums": ["x"], "results": []}, PRFailure.NO_DATA),
    ],
)
def test_query_response_reports_structured_failure(payload, expected):
    rows, reason = parse_query_response(payload)

    assert rows == []
    assert reason == expected


def test_date_created_is_utc_plus_8_naive_text():
    parsed = parse_date_created("2026-11-27 07:51:01.1234567")

    assert parsed.utcoffset() == timedelta(hours=8)
    assert parsed.astimezone(UTC).isoformat() == "2026-11-26T23:51:01.123456+00:00"
    assert parse_date_created("2026-11-26T23:51:01Z").astimezone(UTC).isoformat() == (
        "2026-11-26T23:51:01+00:00"
    )


def test_clean_events_drops_unattributable_and_non_audio_rows():
    rows = [
        pr_row("2026-11-27 07:50:00", 0, user=None),
        pr_row("2026-11-27 07:51:00", 120, item_type="Episode"),
        pr_row("2026-11-27 07:52:00", 120),
    ]

    events = clean_events(rows)

    assert len(events) == 1
    assert events[0].item_id == "973820"
    assert events[0].duration_seconds == 120


def test_clean_events_merges_incomplete_adjacent_segments():
    rows = [
        pr_row("2026-11-27 07:51:01", 26),
        pr_row("2026-11-27 07:51:28", 277),
    ]

    [event] = clean_events(rows, runtimes={"973820": 302.8})

    assert event.segment_count == 2
    assert event.duration_seconds == 303
    assert event.started_at == parse_date_created("2026-11-27 07:51:01")
    assert event.completion_rate == pytest.approx(1.0)
    assert event.is_valid is True
    assert event.is_extremely_short is False
    assert event.is_incomplete is False


def test_clean_events_keeps_replay_and_interleaved_playback_separate():
    rows = [
        pr_row("2026-11-27 07:51:01", 2, item="short"),
        pr_row("2026-11-27 07:51:03", 2, item="short"),
        pr_row("2026-11-27 08:51:01", 26, item="973820"),
        pr_row("2026-11-27 08:51:10", 5, item="other"),
        pr_row("2026-11-27 08:51:28", 277, item="973820"),
        pr_row("2026-11-27 09:51:01", 26, item="973820", device="手机"),
        pr_row("2026-11-27 09:51:28", 277, item="973820"),
        pr_row("2026-11-27 10:51:01", 26, item="973820", user="Manny", device="Windows"),
        pr_row("2026-11-27 10:51:28", 277, item="973820", device="Windows"),
    ]

    events = clean_events(
        rows,
        runtimes={"short": 2, "973820": 302.8, "other": 100},
    )

    assert len(events) == 9
    assert all(event.segment_count == 1 for event in events)


def test_clean_events_marks_extreme_short_and_incomplete_plays_invalid():
    rows = [
        pr_row("2026-11-27 07:51:01", 26, item="a"),
        pr_row("2026-11-27 08:51:01", 150, item="b"),
    ]

    a, b = clean_events(rows, runtimes={"a": 302.8, "b": 302.8})

    assert a.is_extremely_short is True
    assert a.is_incomplete is True
    assert a.is_valid is False
    assert a.completion_rate == pytest.approx(26 / 302.8)
    assert b.is_extremely_short is False
    assert b.is_incomplete is True
    assert b.is_valid is False


def test_client_posts_custom_query_body_with_header_auth_and_timeout():
    transport = FakeTransport(http_json({"colums": ["ok"], "results": [["1"]], "message": ""}))
    client = EmbyPRClient("https://emby.example/", "SECRET", transport=transport, timeout=7)

    payload = client.submit_custom_query("SELECT 1 AS ok LIMIT 1")

    assert payload["message"] == ""
    method, url, kwargs = transport.calls[0]
    assert method == "POST"
    assert url == "https://emby.example/emby/user_usage_stats/submit_custom_query"
    assert kwargs["json"] == {
        "CustomQueryString": "SELECT 1 AS ok LIMIT 1",
        "ReplaceUserId": False,
    }
    assert kwargs["headers"]["X-Emby-Token"] == "SECRET"
    assert "api_key" not in kwargs["params"]
    assert kwargs["timeout"] == 7


def test_client_can_put_api_key_in_query_string():
    transport = FakeTransport(http_json({"colums": [], "results": [], "message": ""}))
    client = EmbyPRClient(
        "https://emby.example", "SECRET", transport=transport, api_key_in="query"
    )

    client.submit_custom_query("SELECT 1 AS ok LIMIT 1")

    _, _, kwargs = transport.calls[0]
    assert kwargs["params"] == {"api_key": "SECRET"}
    assert "X-Emby-Token" not in kwargs["headers"]


def test_client_rejects_unsafe_sql_before_any_request():
    transport = FakeTransport()
    client = EmbyPRClient("https://emby.example", "SECRET", transport=transport)

    with pytest.raises(ValueError):
        client.submit_custom_query("DROP TABLE PlaybackActivity LIMIT 1")

    assert transport.calls == []


@pytest.mark.parametrize(
    ("outcome", "error_type", "reason"),
    [
        (requests.exceptions.Timeout(), PRTimeout, PRFailure.TIMEOUT),
        (http_json({}, 404), PRPluginUnavailable, PRFailure.PLUGIN_UNAVAILABLE),
        (http_json({}, 401), PRQueryFailed, PRFailure.QUERY_FAILED),
    ],
)
def test_client_maps_transport_failures_without_leaking_key(outcome, error_type, reason):
    transport = FakeTransport(outcome)
    client = EmbyPRClient("https://emby.example", "SECRET", transport=transport)

    with pytest.raises(error_type) as caught:
        client.submit_custom_query("SELECT 1 AS ok LIMIT 1")

    assert caught.value.reason == reason
    assert "SECRET" not in str(caught.value)


def test_client_reads_sessions_from_emby():
    transport = FakeTransport(http_json([{"UserId": "nick"}]))
    client = EmbyPRClient("https://emby.example", "SECRET", transport=transport)

    assert client.get_sessions() == [{"UserId": "nick"}]
    method, url, kwargs = transport.calls[0]
    assert method == "GET"
    assert url == "https://emby.example/emby/Sessions"
    assert kwargs["headers"]["X-Emby-Token"] == "SECRET"


def pr_response(rows, message=""):
    columns = ["DateCreated", "UserId", "ItemId", "ItemType", "PlayDuration", "DeviceName"]
    return {
        "colums": columns,
        "results": [[str(row.get(column) or "") for column in columns] for row in rows],
        "message": message,
    }


def test_read_pr_events_returns_cleaned_events_for_resolved_account():
    rows = [
        pr_row("2026-11-27 07:51:01", 26),
        pr_row("2026-11-27 07:51:28", 277),
    ]
    transport = FakeTransport(http_json(SPEAKER_SESSIONS), http_json(pr_response(rows)))
    client = EmbyPRClient("https://emby.example", "SECRET", transport=transport)

    result = read_pr_events(client, runtimes={"973820": 302.8})

    assert result.ok is True
    assert result.reason is None
    assert result.user_id == "nick"
    assert len(result.events) == 1
    assert result.events[0].segment_count == 2
    assert result.events[0].is_valid is True
    assert result.raw_rows == 2
    assert result.dropped_rows == 0
    _, url, kwargs = transport.calls[1]
    assert url.endswith("/emby/user_usage_stats/submit_custom_query")
    assert "UserId = 'nick'" in kwargs["json"]["CustomQueryString"]


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (http_json({"message": "Error Running Query: bad sql", "results": []}), PRFailure.QUERY_FAILED),
        (
            http_json({"message": "Query executed, no data returned. Number of rows effected : 0", "results": []}),
            PRFailure.NO_DATA,
        ),
        (http_json({}, 404), PRFailure.PLUGIN_UNAVAILABLE),
        (requests.exceptions.Timeout(), PRFailure.TIMEOUT),
    ],
)
def test_read_pr_events_reports_structured_failure(outcome, expected):
    transport = FakeTransport(http_json(SPEAKER_SESSIONS), outcome)
    client = EmbyPRClient("https://emby.example", "SECRET", transport=transport)

    result = read_pr_events(client)

    assert result.ok is False
    assert result.reason == expected
    assert result.events == ()
    assert result.user_id == "nick"


def test_read_pr_events_without_identifiable_account_never_queries_plugin():
    transport = FakeTransport(http_json([{"Client": "XiaoEmby", "DeviceName": "小爱音箱", "UserId": None}]))
    client = EmbyPRClient("https://emby.example", "SECRET", transport=transport)

    result = read_pr_events(client)

    assert result.ok is False
    assert result.reason == PRFailure.ACCOUNT_UNRESOLVED
    assert result.user_id is None
    assert len(transport.calls) == 1


def test_read_pr_events_accepts_explicit_user_id_and_skips_session_lookup():
    transport = FakeTransport(http_json(pr_response([pr_row("2026-11-27 07:51:01", 120)])))
    client = EmbyPRClient("https://emby.example", "SECRET", transport=transport)

    result = read_pr_events(client, user_id="Manny")

    assert result.ok is True
    assert result.user_id == "Manny"
    assert len(transport.calls) == 1
    assert transport.calls[0][0] == "POST"
