"""Alerts for the four sign-in events, failed deliveries, the tool-call record, and no secret in any of them."""

import asyncio
import email.message
import inspect
import io
import json
import logging
import urllib.error
from datetime import UTC, datetime, timedelta, timezone

import mcp.server.mcpserver.server as sdk_server
import mcp.types
import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from workspace_mcp import alerts
from workspace_mcp.alerts import Alerts, Event, TelegramSender

MARK = "Qz" + "7mark" + "er"
NOW = datetime(2026, 11, 16, 21, 5, 9, tzinfo=UTC)
CHAT_ID = "424242" + "424242"


def _token():
    # Built at run time, so the repo's credential scan never sees a token here.
    return "1234" + "56789:" + MARK + "A" * 30


class _Response:
    def __init__(self, status, body=b'{"ok": true, "result": {}}'):
        self.status = status
        self.body = body

    def read(self, size):
        return self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    def __init__(self, outcome):
        self.outcome = outcome
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if isinstance(self.outcome, bytes):
            return _Response(200, self.outcome)
        return _Response(self.outcome)


def _http_error(code):
    headers = email.message.Message()
    return urllib.error.HTTPError(f"https://api.telegram.org/bot{_token()}/sendMessage", code, MARK, headers, None)


@pytest.fixture
def records():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield handler, stream
    root.removeHandler(handler)
    root.setLevel(level)


@pytest.fixture
def quiet(records):
    handler, _ = records
    installed = alerts.quiet_sdk_logs(handler)
    yield installed
    logging.getLogger(alerts.SDK_LOGGER).removeFilter(installed)
    handler.removeFilter(installed)


def _alerts(sender):
    return Alerts(sender, logging.getLogger("test.alerts"), clock=lambda: NOW)


@pytest.mark.parametrize("event", list(Event))
def test_each_event_is_sent_with_its_time_and_address(event):
    """Covers: R-8.1"""
    sent = []
    assert _alerts(sent.append).send(event, "203.0.113.7") is True
    assert sent == [f"Workspace-MCP: {event.value}, at 2026-11-16 21:05:09 UTC, from 203.0.113.7."]


def test_the_time_is_given_in_utc():
    """Covers: R-8.1"""
    sent = []
    eastern = timezone(timedelta(hours=-5))
    Alerts(sent.append, logging.getLogger("test.alerts"), clock=lambda: NOW.astimezone(eastern)).send(
        Event.SIGN_IN_SUCCEEDED, "203.0.113.7"
    )
    assert "at 2026-11-16 21:05:09 UTC" in sent[0]


def test_the_four_events_are_the_ones_the_spec_names():
    """Covers: R-8.1"""
    assert {event.name for event in Event} == {
        "WINDOW_OPENED", "SIGN_IN_SUCCEEDED", "WRONG_PASSPHRASE", "REFRESH_TOKEN_REUSED",
    }


@pytest.mark.parametrize("event", ["a sign-in succeeded", None, 3])
def test_an_alert_is_only_ever_one_of_the_events(event):
    """Covers: R-8.1, R-8.3"""
    sent = []
    with pytest.raises(TypeError):
        _alerts(sent.append).send(event, "203.0.113.7")
    assert sent == []


@pytest.mark.parametrize(
    ("address", "shown"),
    [
        ("203.0.113.7", "203.0.113.7"),
        ("2001:DB8::1", "2001:db8::1"),
        (None, "unknown"),
        (b"203.0.113.7", "unknown"),
        (2130706433, "unknown"),
        ("", "unknown"),
        ("mcp.example.test", "unknown"),
        ("fe80::1%eth0", "unknown"),
        ("fe80::1%\nforged line", "unknown"),
        ("203.0.113.7\n", "unknown"),
        (MARK, "unknown"),
    ],
)
def test_the_address_is_an_ip_address_or_unknown(address, shown):
    """Covers: R-8.1, R-8.3"""
    sent = []
    _alerts(sent.append).send(Event.WINDOW_OPENED, address)
    assert sent[0].endswith(f"from {shown}.")


def test_the_sender_posts_to_telegram_with_a_bounded_wait():
    """Covers: R-8.1"""
    opener = _Opener(200)
    TelegramSender(_token(), CHAT_ID, opener=opener)("hello")
    [(request, timeout)] = opener.requests
    assert request.full_url == f"https://api.telegram.org/bot{_token()}/sendMessage"
    assert request.get_method() == "POST"
    assert dict(item.split("=") for item in request.data.decode().split("&")) == {"chat_id": CHAT_ID, "text": "hello"}
    assert timeout == 5


def test_the_sender_ignores_proxies_and_follows_no_redirect(monkeypatch):
    """Covers: R-8.3"""
    for variable in ("https_proxy", "HTTPS_PROXY", "all_proxy"):
        monkeypatch.setenv(variable, "http://proxy.invalid:3128")
    handlers = TelegramSender(_token(), CHAT_ID)._opener.handlers
    assert not [handler for handler in handlers if getattr(handler, "proxies", None)]
    redirects = [handler for handler in handlers if isinstance(handler, urllib.request.HTTPRedirectHandler)]
    assert [type(handler) for handler in redirects] == [alerts._NoRedirect]
    request = urllib.request.Request("https://api.telegram.org/x", data=b"x", method="POST")
    assert redirects[0].redirect_request(request, None, 302, "Found", {}, "https://elsewhere.test/") is None


@pytest.mark.parametrize(
    ("token", "chat_id"),
    [("12345:" + "A" * 35, CHAT_ID), ("123456789:" + "A" * 10, CHAT_ID), ("123456789:" + "A" * 34 + "/", CHAT_ID),
     (None, CHAT_ID), ("TOKEN", "@channel"), ("TOKEN", "12a"), ("TOKEN", ""), ("TOKEN", None)],
)
def test_malformed_alert_credentials_are_refused_without_showing_them(token, chat_id):
    """Covers: R-8.3"""
    token = _token() if token == "TOKEN" else token
    with pytest.raises(ValueError) as caught:
        TelegramSender(token, chat_id)
    assert MARK not in str(caught.value) and (token is None or token not in str(caught.value))


@pytest.mark.parametrize(
    "outcome",
    [_http_error(401), urllib.error.URLError(MARK), TimeoutError(MARK), ConnectionResetError(MARK), 500,
     b'{"ok": false, "description": "' + MARK.encode() + b'"}', b"not json " + MARK.encode()],
)
def test_a_failed_delivery_is_recorded_and_the_caller_carries_on(outcome, caplog):
    """Covers: R-8.2"""
    caplog.set_level(logging.WARNING)
    delivered = _alerts(TelegramSender(_token(), CHAT_ID, opener=_Opener(outcome))).send(
        Event.WRONG_PASSPHRASE, "203.0.113.7"
    )
    assert delivered is False
    [record] = caplog.records
    assert record.levelno == logging.WARNING
    message = record.getMessage()
    assert message.startswith("alert not delivered: WRONG_PASSPHRASE at 2026-11-16 21:05:09 UTC from 203.0.113.7 (")
    expected = {500: "HTTP 500", b"n": "JSONDecodeError", b"{": "NotOk"}.get(
        outcome[:1] if isinstance(outcome, bytes) else outcome, type(outcome).__name__)
    assert message.endswith(f"({expected})")


def test_any_failure_of_the_sender_is_recorded(caplog):
    """Covers: R-8.2"""
    def broken(text):
        raise RuntimeError(MARK)

    caplog.set_level(logging.WARNING)
    assert _alerts(broken).send(Event.WINDOW_OPENED, None) is False
    assert caplog.records[0].getMessage().endswith("from unknown (RuntimeError)")


@pytest.mark.parametrize(
    "outcome",
    [_http_error(401), _http_error(302), urllib.error.URLError(MARK), TimeoutError(MARK), OSError(MARK), 500],
)
def test_no_failed_delivery_shows_the_token_or_a_cause(outcome, records, capsys):
    """Covers: R-8.3"""
    _, stream = records
    sender = TelegramSender(_token(), CHAT_ID, opener=_Opener(outcome))
    with pytest.raises(alerts.AlertNotDelivered) as caught:
        sender("hello")
    error = caught.value
    assert error.__cause__ is None and error.__context__ is None
    _alerts(sender).send(Event.SIGN_IN_SUCCEEDED, "203.0.113.7")
    shown = stream.getvalue() + str(error) + repr(sender) + "".join(capsys.readouterr())
    assert MARK not in shown and CHAT_ID not in shown and "bot" not in shown


def test_a_tool_call_is_recorded_with_its_five_fields(caplog):
    """Covers: R-8.4"""
    caplog.set_level(logging.INFO)
    alerts.record_tool_call(logging.getLogger("test.calls"), "munir", "read_file", "claude-config", "SDD.md",
                            "203.0.113.7")
    [record] = caplog.records
    assert record.levelno == logging.INFO
    prefix, _, data = record.getMessage().partition(" {")
    assert prefix == "tool call"
    assert json.loads("{" + data) == {
        "user": "munir", "tool": "read_file", "repo": "claude-config", "path": "SDD.md", "address": "203.0.113.7",
    }


def test_a_tool_call_without_a_repo_path_or_address_is_still_recorded(caplog):
    """Covers: R-8.4"""
    caplog.set_level(logging.INFO)
    alerts.record_tool_call(logging.getLogger("test.calls"), "munir", "list_repos", None, None, None)
    data = json.loads(caplog.records[0].getMessage().removeprefix("tool call "))
    assert data == {"user": "munir", "tool": "list_repos", "repo": None, "path": None, "address": "unknown"}


def test_a_callers_path_cannot_forge_a_record(caplog):
    """Covers: R-8.4"""
    caplog.set_level(logging.INFO)
    forged = 'a.md\ntool call {"user": "someone-else"}\r\x1b[2J é' + "x" * 2000
    alerts.record_tool_call(logging.getLogger("test.calls"), "munir", "read_file", "claude-config", forged,
                            "fe80::1%\nx")
    message = caplog.records[0].getMessage()
    assert message.isascii() and "\n" not in message and "\r" not in message and "\x1b" not in message
    data = json.loads(message.removeprefix("tool call "))
    assert data["path"] == forged[: alerts.FIELD_CHARS] + f"... ({len(forged)} characters)"
    assert data["address"] == "unknown"


def test_the_sdk_logger_and_its_messages_are_the_ones_filtered():
    """Covers: R-8.3"""
    assert sdk_server.logger.name == alerts.SDK_LOGGER
    calls = inspect.getsource(sdk_server.MCPServer._handle_call_tool)
    reads = inspect.getsource(sdk_server.MCPServer._handle_read_resource)
    for template in ("Tool %r rejected arguments: %r", "Tool %r failed: %r", "Tool %r raised an unexpected exception"):
        assert template in calls
    for template in ("Resource %r raised an unexpected exception", "Resource %r failed: %r"):
        assert template in reads


def _server():
    server = MCPServer("test")

    @server.tool()
    def boom(count: int) -> str:
        raise RuntimeError("file content " + MARK)

    @server.tool()
    def refuse(count: int) -> str:
        raise ToolError("refused " + MARK)

    @server.resource("res://boom")
    def crash() -> str:
        raise RuntimeError("file content " + MARK)

    return server


async def _calls(server):
    for name, arguments in [
        ("boom", {"count": 1}),
        ("refuse", {"count": 1}),
        ("boom", {"count": MARK, MARK: 1}),
        ("missing\nforged line", {}),
    ]:
        result = await server._handle_call_tool(None, mcp.types.CallToolRequestParams(name=name, arguments=arguments))
        assert result.is_error
    for uri in ("file:///" + MARK, "res://boom"):
        with pytest.raises(MCPError):
            await server._handle_read_resource(None, mcp.types.ReadResourceRequestParams(uri=uri))


def test_the_sdk_records_of_a_failed_call_hold_no_content(quiet, records, capsys):
    """Covers: R-8.3"""
    _, stream = records
    asyncio.run(_calls(_server()))
    logged = stream.getvalue()
    assert MARK not in logged + "".join(capsys.readouterr())
    lines = logged.splitlines()
    assert f"{alerts.SDK_LOGGER} ERROR tool \"boom\" raised RuntimeError" in lines
    assert f"{alerts.SDK_LOGGER} INFO tool \"refuse\" failed" in lines
    assert f"{alerts.SDK_LOGGER} INFO tool \"boom\" rejected its arguments" in lines
    assert f'{alerts.SDK_LOGGER} INFO tool "missing\\nforged line" is unknown' in lines
    assert "forged line" not in [line.strip() for line in lines]
    assert f"{alerts.SDK_LOGGER} INFO a resource read failed" in lines
    assert f"{alerts.SDK_LOGGER} ERROR a resource read raised RuntimeError" in lines


def test_records_from_other_sdk_loggers_keep_only_where_they_came_from(quiet, records):
    """Covers: R-8.3"""
    _, stream = records
    child = logging.getLogger("mcp.server.streamable_http")
    try:
        raise ValueError(MARK)
    except ValueError:
        child.exception("request %s failed", MARK)
    child.warning(f"Origin {MARK}\r\x1b[2J not allowed")
    lines = stream.getvalue().splitlines()
    assert MARK not in "\n".join(lines)
    assert [line.rsplit(":", 1)[0] for line in lines] == [
        "mcp.server.streamable_http ERROR logged at test_alerts",
        "mcp.server.streamable_http WARNING logged at test_alerts",
    ]
    assert lines[0].endswith("(ValueError)")


@pytest.mark.parametrize("reason", [None, MARK + " " + MARK, "HTTP 5000", "x" * 65, 3])
def test_a_failure_reason_is_only_ever_a_class_or_a_status(reason, caplog):
    """Covers: R-8.2, R-8.3"""
    def sender(text):
        raise alerts.AlertNotDelivered(reason)

    caplog.set_level(logging.WARNING)
    assert _alerts(sender).send(Event.WINDOW_OPENED, "203.0.113.7") is False
    assert caplog.records[0].getMessage().endswith("(unknown)")


def test_records_of_the_service_itself_pass_unchanged(quiet, records):
    """Covers: R-8.3"""
    _, stream = records
    logging.getLogger("workspace_mcp.test").info("kept %s", "as is")
    assert "workspace_mcp.test INFO kept as is" in stream.getvalue().splitlines()
