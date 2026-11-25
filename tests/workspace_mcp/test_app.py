"""The service assembled: tool declarations, result size, plain refusals, and its own host name only."""

import asyncio
import io
import json
import logging

import pytest
from conftest import CONFIG, PUBLIC, environ
from mcp.server.mcpserver import MCPServer

from workspace_mcp import alerts, app, auth, config, login, repo_tools
from workspace_mcp.paths import RepoMissing

VERSIONS = ["2025-06-18", "2025-11-25", "2026-07-28"]
EXPECTED_TOOLS = {
    "list_repos": {"title": "List repos", "readOnlyHint": True, "openWorldHint": False},
}
ROUTES = [
    ("POST", "/mcp"),
    ("POST", "/token"),
    ("GET", "/authorize"),
    ("GET", "/login"),
    ("GET", "/.well-known/oauth-authorization-server"),
    ("GET", "/.well-known/oauth-protected-resource/mcp"),
    ("GET", "/nowhere"),
]
UNEXPECTED = "The list_repos tool could not answer just now; try again later."


def _token(flow):
    return flow.sign_in()["access_token"]


def _text(result):
    assert [item["type"] for item in result["content"]] == ["text"]
    return result["content"][0]["text"]


async def _raw(service, headers, scope_type="http", path="/mcp"):
    scope = {"type": scope_type, "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": headers, "scheme": "http", "server": ("localhost", 8765), "client": ("127.0.0.1", 1),
             "http_version": "1.1", "root_path": ""}
    sent = []

    async def receive():
        return {"type": "http.disconnect"} if scope_type == "http" else {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    await service.app(scope, receive, send)
    return sent


@pytest.mark.parametrize("version", VERSIONS)
def test_every_tool_has_a_title_and_says_whether_it_changes_anything(flow, version):
    """Covers: R-7.1"""
    response = flow.rpc("tools/list", token=_token(flow), version=version)
    assert response.status_code == 200, response.text
    tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
    assert set(tools) == set(EXPECTED_TOOLS)
    for name, expected in EXPECTED_TOOLS.items():
        assert tools[name]["title"] == expected["title"]
        annotations = tools[name]["annotations"]
        assert annotations["readOnlyHint"] is expected["readOnlyHint"]
        assert annotations.get("destructiveHint") in (None, False)
        assert annotations["openWorldHint"] is expected["openWorldHint"]
        assert "outputSchema" not in tools[name]


@pytest.mark.parametrize("version", VERSIONS)
def test_the_repo_list_is_one_plain_sentence(flow, version):
    """Covers: R-1.2, R-7.1"""
    result = flow.call("list_repos", token=_token(flow), version=version)
    assert result.get("isError") in (None, False)
    assert _text(result) == "You can read these repos: claude-config (read only)."
    assert "structuredContent" not in result


def test_an_empty_repo_list_is_still_a_sentence(make_flow):
    """Covers: R-1.2"""
    text = CONFIG.replace('read = ["claude-config"]', "read = []")
    flow = make_flow(text=text)
    assert _text(flow.call("list_repos", token=_token(flow))) == "You can read no repos."


def test_a_result_over_the_configured_size_is_cut_and_says_so(make_flow):
    """Covers: R-7.2"""
    repos = "".join(f'\n[repos.repo-number-{i:02d}]\nsource = "checkout"\npath = "/srv/r{i}"\n' for i in range(12))
    names = ", ".join(f'"repo-number-{i:02d}"' for i in range(12))
    text = repos + f'\n[users.munir]\nread = [{names}]\n\n[limits]\nresult_chars = 120\n'
    flow = make_flow(text=text)
    answer = _text(flow.call("list_repos", token=_token(flow)))
    assert len(answer) <= 120
    assert answer.endswith(app.CUT_NOTE)
    assert answer.startswith("You can read these repos: repo-number-00")


def test_a_result_within_the_size_is_left_whole():
    """Covers: R-7.2"""
    from mcp.types import CallToolResult, TextContent

    whole = CallToolResult(content=[TextContent(type="text", text="x" * 50)])
    assert app.fit(whole, 50) is whole


def test_the_size_counts_structured_content_too():
    """Covers: R-7.2"""
    from mcp.types import CallToolResult, TextContent

    doubled = CallToolResult(content=[TextContent(type="text", text="x" * 40)], structured_content={"t": "y" * 40})
    fitted = app.fit(doubled, 60)
    structured = fitted.structured_content
    total = sum(len(item.text) for item in fitted.content) + (len(json.dumps(structured)) if structured else 0)
    assert total <= 60
    assert fitted.content[-1].text.endswith(app.CUT_NOTE)


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize(
    "failure",
    ["repo missing", "os error with a path", "marker exception"],
)
def test_a_tool_that_fails_answers_one_plain_sentence(flow, monkeypatch, caplog, quiet_sdk, version, failure):
    """Covers: R-7.4"""
    marker = "marker-91c4"

    def broken(config, user):
        if failure == "repo missing":
            raise RepoMissing("claude-config")
        if failure == "os error with a path":
            raise OSError(2, "No such file", f"/home/munir/secret/{marker}")
        raise RuntimeError(marker)

    monkeypatch.setattr(repo_tools, "list_repos", broken)
    caplog.set_level(logging.DEBUG)
    result = flow.call("list_repos", token=_token(flow), version=version)
    assert result["isError"] is True
    assert any(record.getMessage().startswith("tool call ") for record in caplog.records)
    text = _text(result)
    if failure == "repo missing":
        assert text == "There is no repo called 'claude-config'; list_repos shows the ones you can read."
    else:
        assert text == UNEXPECTED
    for shown in [text] + [record.getMessage() for record in caplog.records]:
        assert marker not in shown and "/home/munir" not in shown and "Error executing" not in shown
    assert all(record.exc_info is None and not record.exc_text for record in caplog.records
               if record.name.startswith(("workspace_mcp", "mcp")))


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("no_such_tool", "There is no tool called 'no_such_tool'; the tool list shows the ones this service has."),
        ("x" * 65, "There is no such tool; the tool list shows the ones this service has."),
        ("bad name <marker-2b7d>", "There is no such tool; the tool list shows the ones this service has."),
    ],
)
def test_an_unknown_tool_is_one_plain_sentence(flow, caplog, quiet_sdk, version, name, expected):
    """Covers: R-7.4"""
    caplog.set_level(logging.DEBUG)
    result = flow.call(name, token=_token(flow), version=version)
    assert result["isError"] is True
    assert _text(result) == expected
    # The log keeps the name, quoted on one line (T1.9's fixed sentence): a name is not content.
    for record in caplog.records:
        if "marker-2b7d" in record.getMessage():
            assert record.getMessage() == 'tool "bad name <marker-2b7d>" is unknown'


@pytest.mark.parametrize("version", ["2025-03-26", "2025-06-18", "2025-11-25"])
def test_the_handshake_negotiates_each_revision_with_no_session(flow, version):
    """Covers: R-7.1"""
    response = flow.rpc("initialize", {"protocolVersion": version, "capabilities": {},
                                       "clientInfo": {"name": "test", "version": "1"}}, token=_token(flow))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert "mcp-session-id" not in response.headers
    result = response.json()["result"]
    assert result["protocolVersion"] == version
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "Workspace-MCP"
    initialized = flow.http.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"accept": "application/json, text/event-stream", "content-type": "application/json",
                 "mcp-protocol-version": version, "authorization": f"Bearer {_token(flow)}"},
    )
    assert initialized.status_code == 202
    assert "mcp-session-id" not in initialized.headers


@pytest.mark.parametrize("version", VERSIONS)
def test_a_tool_that_raises_past_its_own_handling_is_not_called_bad_arguments(flow, monkeypatch, version):
    """Covers: R-7.4"""

    def broken(*args, **kwargs):
        raise RuntimeError("marker-0d3a")

    monkeypatch.setattr(app, "record_tool_call", broken)
    result = flow.call("list_repos", token=_token(flow), version=version)
    assert result["isError"] is True
    assert _text(result) == UNEXPECTED


def test_bad_arguments_to_a_tool_whose_name_is_not_echoed_still_read_plainly(flow):
    """Covers: R-7.4"""

    def counter(count: int) -> str:
        return str(count)

    flow.service.server.add_tool(counter, name="c" * 65)
    result = flow.call("c" * 65, {"count": "x"}, token=_token(flow))
    assert _text(result) == "The arguments were not valid; check them against the tool's description."


@pytest.mark.parametrize("version", VERSIONS)
def test_an_sdk_answer_to_a_tool_that_raised_is_not_called_bad_arguments(flow, version):
    """Covers: R-7.4"""

    def exploding() -> str:
        raise RuntimeError("marker-77e1")

    flow.service.server.add_tool(exploding, name="exploding")
    result = flow.call("exploding", token=_token(flow), version=version)
    assert result["isError"] is True
    assert _text(result) == "The exploding tool could not answer just now; try again later."


@pytest.mark.parametrize("version", VERSIONS)
def test_bad_arguments_are_one_plain_sentence_without_the_value(flow, caplog, quiet_sdk, version):
    """Covers: R-7.4"""

    def counter(count: int) -> str:
        return str(count)

    flow.service.server.add_tool(counter, name="counter")
    caplog.set_level(logging.DEBUG)
    result = flow.call("counter", {"count": "marker-6a0f"}, token=_token(flow), version=version)
    assert result["isError"] is True
    assert _text(result) == "The arguments for 'counter' were not valid; check them against the tool's description."
    assert "marker-6a0f" not in " ".join(record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("method_path", ROUTES)
@pytest.mark.parametrize("host", ["attacker.example", "localhost:8766", "localhost", "localhost.:8765",
                                  "LOCALHOST:8765", "localhost:8765 "])
def test_a_request_for_another_host_name_is_refused(flow, method_path, host):
    """Covers: R-7.9"""
    method, path = method_path
    response = flow.http.request(method, path, headers={"host": host})
    assert response.status_code == 421
    assert response.text == "Misdirected Request"
    assert "www-authenticate" not in response.headers


@pytest.mark.parametrize("method_path", ROUTES)
def test_the_services_own_host_name_is_let_through(flow, method_path):
    """Covers: R-7.9"""
    method, path = method_path
    assert flow.http.request(method, path).status_code != 421


@pytest.mark.parametrize(
    "headers",
    [[], [(b"host", b"localhost:8765"), (b"host", b"localhost:8765")],
     [(b"host", b"attacker.example"), (b"x-forwarded-host", b"localhost:8765")]],
    ids=["no host", "two hosts", "forwarded host"],
)
def test_a_request_without_exactly_the_own_host_is_refused(flow, headers):
    """Covers: R-7.9"""
    sent = asyncio.run(_raw(flow.service, headers))
    assert sent[0]["status"] == 421


def test_a_forwarded_host_does_not_override_a_wrong_one(flow):
    """Covers: R-7.9"""
    response = flow.http.get("/nowhere", headers={"host": "attacker.example", "x-forwarded-host": "localhost:8765"})
    assert response.status_code == 421


def test_a_websocket_is_refused(flow):
    """Covers: R-7.9"""
    sent = asyncio.run(_raw(flow.service, [(b"host", b"localhost:8765")], scope_type="websocket"))
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_the_service_starts_behind_the_host_check(flow):
    """Covers: R-7.9"""
    assert flow.mcp(_token(flow)).status_code == 200
    for _ in range(50):
        flow.alerts.join()
        if flow.sent:
            break
        import time

        time.sleep(0.1)
    assert any("the sign-in window was opened" in text for text in flow.sent)


@pytest.mark.parametrize("version", VERSIONS)
def test_every_tool_call_is_recorded(flow, caplog, version):
    """Covers: R-8.4"""
    caplog.set_level(logging.INFO, logger="workspace_mcp")
    flow.call("list_repos", token=_token(flow), version=version, headers={"x-forwarded-for": "198.51.100.4"})
    records = [json.loads(record.getMessage().removeprefix("tool call ")) for record in caplog.records
               if record.getMessage().startswith("tool call ")]
    assert records == [{"user": "munir", "tool": "list_repos", "repo": None, "path": None,
                        "address": "198.51.100.4"}]


@pytest.mark.parametrize("where", ["login", "token"])
def test_an_exception_in_the_service_reaches_no_record_and_no_answer(flow, monkeypatch, caplog, where):
    """Covers: R-7.4, R-8.3"""
    marker = "marker-4e19"
    caplog.set_level(logging.DEBUG)
    stream = io.StringIO()
    handler = app.configure_logging(stream)
    try:
        def explode(*args, **kwargs):
            raise RuntimeError(marker)

        if where == "login":
            monkeypatch.setattr(login.Page, "_post", explode)
            response = flow.http.post("/login", data={"request": "x", "passphrase": "y"})
        else:
            monkeypatch.setattr(app.TokenForm, "__call__", explode)
            response = flow.http.post("/token", data={"grant_type": "x"})
        assert response.status_code == 500
        assert response.text == "Internal Server Error"
        assert marker not in stream.getvalue()
        assert all(marker not in record.getMessage() and record.exc_info is None for record in caplog.records)
        assert "request failed (RuntimeError)" in stream.getvalue()
    finally:
        logging.getLogger().removeHandler(handler)


def test_the_services_log_handler_cuts_the_sdks_records_and_never_logs_debug():
    """Covers: R-8.3"""
    handler = app.configure_logging(io.StringIO())
    try:
        assert any(isinstance(item, alerts.QuietSdkRecords) for item in handler.filters)
        assert handler in logging.getLogger().handlers
        assert logging.getLogger().getEffectiveLevel() == logging.INFO
        assert logging.getLogger("mcp").getEffectiveLevel() > logging.DEBUG
    finally:
        logging.getLogger().removeHandler(handler)


def test_python_m_workspace_mcp_runs_the_production_shape_only(monkeypatch, tmp_path):
    """Covers: R-9.1, R-7.9"""
    import uvicorn

    import workspace_mcp.__main__ as entry

    config_file = tmp_path / "config.toml"
    config_file.write_text(CONFIG)
    monkeypatch.setattr(entry, "CONFIG_PATH", config_file)
    monkeypatch.setattr(entry, "STATE_PATH", tmp_path / "tokens.db")
    values = environ(PUBLIC)
    values["WORKSPACE_MCP_TELEGRAM_BOT_TOKEN"] = "1234" + "56789:" + "A" * 35
    values["WORKSPACE_MCP_TELEGRAM_CHAT_ID"] = "4242"
    values["WORKSPACE_MCP_TRIAL_RETURN"] = "http://localhost:6274/oauth/callback"
    built, served = [], []
    real_build = app.build

    def recording_build(*args, **kwargs):
        built.append(kwargs)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(app, "build", recording_build)
    monkeypatch.setattr(uvicorn.Server, "run", lambda self: served.append(self.config))
    monkeypatch.setattr(app, "configure_logging", lambda stream=None: None)
    # uvicorn reads this from the environment unless the worker count is given.
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    assert entry.main(values) == 0
    assert "trial" not in built[0]
    assert built[0]["store"].generation_path == auth.GENERATION_PATH
    server_config = served[0]
    assert server_config.uds == "/run/workspace-mcp/mcp.sock"
    assert server_config.proxy_headers is False
    assert server_config.access_log is False
    assert server_config.log_config is None
    assert server_config.lifespan == "on"
    assert server_config.workers == 1


def _entry(monkeypatch, tmp_path, values=None):
    import workspace_mcp.__main__ as entry

    config_file = tmp_path / "config.toml"
    config_file.write_text(CONFIG)
    monkeypatch.setattr(entry, "CONFIG_PATH", config_file)
    monkeypatch.setattr(entry, "STATE_PATH", tmp_path / "tokens.db")
    if values is None:
        values = environ(PUBLIC)
        values["WORKSPACE_MCP_TELEGRAM_BOT_TOKEN"] = "1234" + "56789:" + "A" * 35
        values["WORKSPACE_MCP_TELEGRAM_CHAT_ID"] = "4242"
    return entry, values


def test_python_m_workspace_mcp_leaves_one_filtered_handler_whatever_the_sdk_does(monkeypatch, tmp_path):
    """Covers: R-8.3"""
    import uvicorn

    entry, values = _entry(monkeypatch, tmp_path)
    monkeypatch.setattr(uvicorn.Server, "run", lambda self: None)
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    try:
        assert entry.main(values) == 0
        assert len(root.handlers) == 1
        (handler,) = root.handlers
        assert any(isinstance(item, alerts.QuietSdkRecords) for item in handler.filters)
        assert any(isinstance(item, app.NoTracebacks) for item in handler.filters)
        for name, logger in logging.Logger.manager.loggerDict.items():
            if isinstance(logger, logging.Logger) and name.startswith(("mcp", "uvicorn", "starlette", "workspace")):
                assert logger.handlers == [], name
    finally:
        root.handlers[:] = saved


@pytest.mark.parametrize("source", ["uvicorn.error", "asyncio", "starlette"])
def test_the_log_handler_keeps_no_traceback_from_any_logger(source):
    """Covers: R-8.3"""
    stream = io.StringIO()
    handler = app.configure_logging(stream)
    try:
        try:
            raise ValueError("marker-31dd /home/munir/secret")
        except ValueError:
            logging.getLogger(source).exception("Exception in 'lifespan' protocol")
        written = stream.getvalue()
        assert "marker-31dd" not in written and "/home/munir" not in written and "Traceback" not in written
        assert "Exception in 'lifespan' protocol (ValueError)" in written
    finally:
        logging.getLogger().removeHandler(handler)


def test_python_m_workspace_mcp_refuses_a_bad_alert_token_without_its_value(monkeypatch, tmp_path, caplog):
    """Covers: R-9.1, R-8.3"""
    entry, values = _entry(monkeypatch, tmp_path)
    values["WORKSPACE_MCP_TELEGRAM_BOT_TOKEN"] = "marker-5b7e"
    monkeypatch.setattr(app, "configure_logging", lambda stream=None: None)
    caplog.set_level(logging.DEBUG)
    assert entry.main(values) == 1
    messages = [record.getMessage() for record in caplog.records]
    assert "refused to start: the alert bot token or chat id is malformed" in messages
    assert not any("marker-5b7e" in message for message in messages)


def test_python_m_workspace_mcp_names_any_other_start_failure_by_class_alone(monkeypatch, tmp_path, caplog):
    """Covers: R-9.1, R-8.3"""
    entry, values = _entry(monkeypatch, tmp_path)

    def broken(*args, **kwargs):
        raise ValueError("input_value='marker-c81f'")

    monkeypatch.setattr(app, "build", broken)
    monkeypatch.setattr(app, "configure_logging", lambda stream=None: None)
    caplog.set_level(logging.DEBUG)
    assert entry.main(values) == 1
    messages = [record.getMessage() for record in caplog.records]
    assert "refused to start (ValueError)" in messages
    assert not any("marker-c81f" in message for message in messages)


def test_an_exception_inside_an_sdk_route_reaches_no_record_and_no_answer(flow, monkeypatch, caplog):
    """Covers: R-7.4, R-8.3"""

    async def explode(*args, **kwargs):
        raise RuntimeError("marker-e4c2")

    assert flow.service.server.settings.debug is False
    monkeypatch.setattr(flow.service.provider, "get_client", explode)
    caplog.set_level(logging.DEBUG)
    response = flow.http.post("/token", data={"grant_type": "refresh_token", "refresh_token": "x",
                                              "client_id": flow.client_id, "client_secret": flow.secret})
    assert response.status_code == 500
    assert "marker-e4c2" not in response.text
    assert all("marker-e4c2" not in record.getMessage() and record.exc_info is None for record in caplog.records
               if record.name.startswith("workspace_mcp"))


def test_python_m_workspace_mcp_refuses_to_start_on_a_bad_configuration(monkeypatch, tmp_path, caplog):
    """Covers: R-9.1"""
    import workspace_mcp.__main__ as entry

    monkeypatch.setattr(entry, "CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setattr(app, "configure_logging", lambda stream=None: None)
    caplog.set_level(logging.ERROR)
    assert entry.main(environ(PUBLIC)) == 1
    assert any("refused to start" in record.getMessage() for record in caplog.records)
