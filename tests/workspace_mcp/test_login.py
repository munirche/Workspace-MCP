"""The sign-in window, the sign-in page and the passphrase."""

import asyncio
import importlib.machinery
import importlib.util
import io
import logging
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from conftest import CHEAP_PARAMS, CONFIG, PUBLIC, REAL_PARAMS, environ, pkce, query

from workspace_mcp import alerts, app, auth, config, login
from workspace_mcp.config import ConfigError

HELPER = Path(__file__).resolve().parents[2] / "scripts" / "hash_passphrase.py"
SIGN_IN_PATHS = ["/authorize", "/authorize/", "/login", "/login/"]


def _helper():
    loader = importlib.machinery.SourceFileLoader("hash_passphrase", str(HELPER))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def _request(flow, **params):
    _, challenge = pkce()
    return flow.request_id(flow.authorize(challenge, **params))


def _closed(flow):
    unknown = flow.http.get("/nowhere")
    for path in SIGN_IN_PATHS:
        for method in ("GET", "POST", "HEAD"):
            response = flow.http.request(method, path)
            assert response.status_code == 404, (method, path)
            assert "location" not in response.headers
            if method != "HEAD":
                assert response.content == unknown.content
            assert dict(response.headers) == dict(unknown.headers)
    _, challenge = pkce()
    response = flow.authorize(challenge)
    assert response.status_code == 404
    assert response.content == unknown.content
    return True


async def _post_login(service, request_id, passphrase, origin=None):
    body = urlencode({"request": request_id, "passphrase": passphrase}).encode()
    headers = [(b"host", b"localhost:8765"), (b"content-type", b"application/x-www-form-urlencoded"),
               (b"content-length", str(len(body)).encode())]
    if origin:
        headers.append((b"origin", origin.encode()))
    scope = {"type": "http", "method": "POST", "path": "/login", "raw_path": b"/login", "query_string": b"",
             "headers": headers, "scheme": "http", "server": ("localhost", 8765), "client": ("127.0.0.1", 1),
             "http_version": "1.1", "root_path": ""}
    messages, sent = [{"type": "http.request", "body": body, "more_body": False}], []

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await service.app(scope, receive, send)
    return next(message["status"] for message in sent if message["type"] == "http.response.start")


def test_with_no_window_open_there_is_no_sign_in_page(make_flow):
    """Covers: R-5.1"""
    assert _closed(make_flow(window=False))


@pytest.mark.parametrize(
    "case",
    ["stale", "future", "malformed", "short nonce", "no newline", "group writable", "world writable", "linked",
     "a folder", "a pipe"],
)
def test_a_flag_that_is_not_a_fresh_root_stamp_leaves_the_window_closed(make_flow, case, tmp_path):
    """Covers: R-5.1"""
    flow = make_flow(window=False)
    if case == "stale":
        flow.open_window(stamp=flow.clock() - 600)
    elif case == "future":
        flow.open_window(stamp=flow.clock() + 5)
    elif case == "malformed":
        flow.flag.write_text("open\n")
    elif case == "short nonce":
        flow.flag.write_text(f"{'a' * 63} {int(flow.clock())}\n")
    elif case == "no newline":
        flow.flag.write_text(f"{'a' * 64} {int(flow.clock())}")
    elif case == "group writable":
        flow.open_window(mode=0o664)
    elif case == "world writable":
        flow.open_window(mode=0o646)
    elif case == "linked":
        target = tmp_path / "target"
        target.write_text(f"{'a' * 64} {int(flow.clock())}\n")
        target.chmod(0o644)
        flow.flag.symlink_to(target)
    elif case == "a folder":
        flow.flag.mkdir()
    elif case == "a pipe":
        os.mkfifo(flow.flag, 0o644)
    if case in ("malformed", "short nonce", "no newline", "a folder"):
        # Only the property under test is wrong: the mode is the one root's command writes.
        flow.flag.chmod(0o755 if case == "a folder" else 0o644)
    assert _closed(flow)


def test_a_flag_written_by_anyone_but_root_leaves_the_window_closed(make_flow):
    """Covers: R-5.1"""
    flow = make_flow(owner=0)
    assert flow.flag.exists() and flow.flag.stat().st_uid != 0
    assert _closed(flow)


def test_an_open_window_answers_another_client_exactly_as_a_closed_one(flow, make_flow):
    """Covers: R-5.1, R-5.8"""
    _, challenge = pkce()
    wrong = flow.authorize(challenge, client_id="client-other")
    closed = make_flow(window=False)
    shut = closed.authorize(challenge)
    assert (wrong.status_code, wrong.content, dict(wrong.headers)) == (
        shut.status_code, shut.content, dict(shut.headers))


def test_the_window_stays_open_for_ten_minutes(flow):
    """Covers: R-5.2"""
    assert flow.authorize(pkce()[1]).status_code == 302
    flow.clock.advance(599)
    assert flow.authorize(pkce()[1]).status_code == 302
    flow.clock.advance(1)
    assert _closed(flow)


def test_a_flag_stamped_again_does_not_extend_the_window(flow):
    """Covers: R-5.2"""
    assert flow.authorize(pkce()[1]).status_code == 302
    flow.clock.advance(590)
    flow.open_window(nonce=flow.nonce)
    flow.clock.advance(10)
    assert _closed(flow)


def test_the_window_opening_is_alerted_once_from_the_server(flow):
    """Covers: R-5.2, R-8.1"""
    flow.service.window.poll()
    flow.service.window.poll()
    flow.alerts.join()
    assert len(flow.sent) == 1
    assert "the sign-in window was opened" in flow.sent[0]
    assert flow.sent[0].endswith("from the server.")
    flow.open_window()
    flow.service.window.poll()
    flow.alerts.join()
    assert len(flow.sent) == 2


def test_the_window_is_watched_while_the_service_runs(make_flow):
    """Covers: R-5.2, R-8.1"""
    flow = make_flow()
    for _ in range(50):
        flow.alerts.join()
        if flow.sent:
            break
        time.sleep(0.1)
    assert len(flow.sent) == 1


def test_a_watch_that_fails_once_keeps_watching(monkeypatch, caplog, tmp_path):
    """Covers: R-5.2, R-8.1"""
    from conftest import Flow

    # Not started, so the service's own watch is not also calling the window.
    flow = Flow(state=tmp_path)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("marker-8c2e /secret/path")

    monkeypatch.setattr(flow.service.window, "poll", flaky)
    monkeypatch.setattr(login, "POLL_SECONDS", 0.01)
    caplog.set_level(logging.INFO)

    async def watch():
        task = asyncio.create_task(login.watch(flow.service.window, logging.getLogger("test.watch")))
        for _ in range(300):
            if len(calls) >= 3 or task.done():
                break
            await asyncio.sleep(0.01)
        task.cancel()
        assert len(calls) >= 3, "the watch stopped after a failure"

    asyncio.run(watch())
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "OSError" in text
    assert "marker-8c2e" not in text and "/secret/path" not in text


def test_a_successful_sign_in_closes_the_window(flow):
    """Covers: R-5.3"""
    verifier, challenge = pkce()
    request_id = flow.request_id(flow.authorize(challenge))
    response = flow.login(request_id)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.split("?")[0] == flow.return_address
    assert query(location)["state"] == "state-1"
    assert _closed(flow)
    assert flow.exchange(query(location)["code"], verifier).status_code == 200
    flow.alerts.join()
    assert any("a sign-in succeeded" in text for text in flow.sent)


def test_the_state_comes_back_unchanged_through_the_pages_redirect(flow):
    """Covers: R-5.3, R-5.8"""
    state = "s t&a=te%2F~+é"
    request_id = _request(flow, state=state)
    response = flow.login(request_id)
    assert response.status_code == 303
    assert query(response.headers["location"])["state"] == state


def test_a_window_closed_by_a_sign_in_stays_closed_after_a_restart(flow):
    """Covers: R-5.3"""
    assert flow.login(_request(flow)).status_code == 303
    again = flow.rebuilt()
    with again.http:
        assert _closed(again)


def _held_verifier(monkeypatch, answer):
    started, release = [], threading.Event()
    real = login.verify

    def held(parsed, given):
        started.append(given)
        release.wait(5)
        return real(parsed, given) if answer is None else answer

    monkeypatch.setattr(login, "verify", held)
    return started, release


def test_two_right_passphrases_at_once_sign_in_once(flow, monkeypatch):
    """Covers: R-5.3"""
    started, release = _held_verifier(monkeypatch, None)
    request_id = _request(flow)

    async def both():
        posts = [asyncio.create_task(_post_login(flow.service, request_id, flow.passphrase)) for _ in range(2)]
        while flow.store.window(flow.nonce).attempts < 2:
            await asyncio.sleep(0.01)
        release.set()
        return sorted(await asyncio.gather(*posts))

    assert asyncio.run(both()) == [303, 404]


def test_two_sign_ins_at_once_in_one_window_give_one(flow, monkeypatch):
    """Covers: R-5.3"""
    started, release = _held_verifier(monkeypatch, None)
    first, second = _request(flow), _request(flow)

    async def both():
        posts = [asyncio.create_task(_post_login(flow.service, request_id, flow.passphrase))
                 for request_id in (first, second)]
        while flow.store.window(flow.nonce).attempts < 2:
            await asyncio.sleep(0.01)
        release.set()
        return sorted(await asyncio.gather(*posts))

    assert asyncio.run(both()) == [303, 404]
    import sqlite3

    with sqlite3.connect(flow.state / "tokens.db") as db:
        assert db.execute("SELECT count(*) FROM codes").fetchone()[0] == 1


def test_sign_in_alerts_name_the_address_nginx_gave(flow):
    """Covers: R-8.1"""
    request_id = _request(flow)
    flow.login(request_id, passphrase="wrong passphrase, quite long", headers={"x-forwarded-for": "198.51.100.7"})
    flow.login(request_id, headers={"x-forwarded-for": "198.51.100.8"})
    flow.alerts.join()
    assert any("a wrong passphrase was entered" in text and "from 198.51.100.7" in text for text in flow.sent)
    assert any("a sign-in succeeded" in text and "from 198.51.100.8" in text for text in flow.sent)


def test_five_wrong_passphrases_close_the_window(flow):
    """Covers: R-5.4"""
    request_id = _request(flow)
    for _ in range(4):
        page = flow.login(request_id, passphrase="wrong passphrase, quite long")
        assert page.status_code == 200
        assert "That passphrase was not accepted." in page.text
    assert flow.login(request_id).status_code == 303


def test_the_fifth_wrong_passphrase_closes_the_window_and_a_sixth_is_not_checked(flow, monkeypatch):
    """Covers: R-5.4, R-8.1"""
    request_id = _request(flow)
    for _ in range(4):
        flow.login(request_id, passphrase="wrong passphrase, quite long")
    fifth = flow.login(request_id, passphrase="wrong passphrase, quite long")
    assert "the sign-in window is now closed" in fifth.text
    assert "<form" not in fifth.text
    checked = []
    monkeypatch.setattr(login, "verify", lambda parsed, given: checked.append(given) or True)
    assert flow.login(request_id).status_code == 404
    assert checked == []
    assert _closed(flow)
    flow.alerts.join()
    assert sum("a wrong passphrase was entered" in text for text in flow.sent) == 5


def test_wrong_passphrases_at_once_start_at_most_five_checks(flow, monkeypatch):
    """Covers: R-5.4"""
    started, release = _held_verifier(monkeypatch, False)
    request_id = _request(flow)

    async def flood():
        posts = [asyncio.create_task(_post_login(flow.service, request_id, "wrong")) for _ in range(8)]
        while flow.store.window(flow.nonce).attempts < 5:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        release.set()
        return await asyncio.gather(*posts)

    statuses = asyncio.run(flood())
    assert len(started) == 5
    assert statuses.count(404) == 3


def test_the_wrong_passphrase_count_survives_a_restart(flow):
    """Covers: R-5.4"""
    request_id = _request(flow)
    for _ in range(4):
        flow.login(request_id, passphrase="wrong passphrase, quite long")
    again = flow.rebuilt()
    with again.http:
        again.login(request_id, passphrase="wrong passphrase, quite long")
        assert _closed(again)


@pytest.mark.parametrize("case", ["no request", "unknown request", "another window's request", "foreign origin",
                                  "null origin", "multipart"])
def test_a_post_with_no_live_sign_in_from_this_window_is_refused_and_not_counted(flow, case):
    """Covers: R-5.4, R-5.1"""
    request_id = _request(flow)
    headers, form = {}, {"request": request_id, "passphrase": "wrong passphrase, quite long"}
    if case == "no request":
        form.pop("request")
    elif case == "unknown request":
        form["request"] = "x" * 43
    elif case == "another window's request":
        other = auth.Pending(flow.client_id, flow.return_address, True, pkce()[1], flow.config.public.resource,
                             ("workspace",), None, 0, "f" * 64)
        flow.store.put_pending("other-window-request", other)
        form["request"] = "other-window-request"
    elif case == "foreign origin":
        headers = {"origin": "https://attacker.example"}
    elif case == "null origin":
        headers = {"origin": "null"}
    if case == "multipart":
        response = flow.http.post("/login", files={"passphrase": ("p", b"x")}, data={"request": request_id})
    else:
        response = flow.http.post("/login", data=form, headers=headers)
    assert response.status_code == 404
    flow.alerts.join()
    assert not any("wrong passphrase" in text for text in flow.sent)
    assert flow.store.window(flow.nonce).attempts == 0


def test_a_post_from_the_page_itself_is_accepted(flow):
    """Covers: R-5.4"""
    response = flow.login(_request(flow), headers={"origin": flow.config.public.issuer})
    assert response.status_code == 303


def test_a_post_too_large_is_refused(flow):
    """Covers: R-5.4"""
    request_id = _request(flow)
    body = urlencode({"request": request_id, "passphrase": "x" * 9000}).encode()
    response = flow.http.post("/login", content=iter([body[:4000], body[4000:]]),
                              headers={"content-type": "application/x-www-form-urlencoded"})
    assert response.status_code == 404
    assert flow.store.window(flow.nonce).attempts == 0


def test_a_post_declaring_too_much_is_refused_before_it_is_read(flow):
    """Covers: R-5.4"""
    sent = []

    async def receive():
        raise AssertionError("the body was read")

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/login", "query_string": b"",
             "headers": [(b"host", b"localhost:8765"), (b"content-type", b"application/x-www-form-urlencoded"),
                         (b"content-length", b"8193")]}
    asyncio.run(flow.service.app(scope, receive, send))
    assert sent[0]["status"] == 404


def test_a_post_that_is_not_a_form_is_refused(flow):
    """Covers: R-5.4"""
    body = urlencode({"request": _request(flow), "passphrase": flow.passphrase})
    response = flow.http.post("/login", content=body, headers={"content-type": "text/plain"})
    assert response.status_code == 404
    assert flow.store.window(flow.nonce).attempts == 0


def test_the_page_names_the_client_and_where_the_browser_goes_next(flow):
    """Covers: R-5.5"""
    request_id = _request(flow)
    page = flow.http.get("/login", params={"request": request_id, "marker": "marker-3f1a<b>"})
    assert page.status_code == 200
    assert auth.LABEL in page.text
    assert auth.CLAUDE_CALLBACK in page.text
    assert "marker-3f1a" not in page.text
    assert flow.client_id not in page.text
    assert 'type="password"' in page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "same-origin"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]


def test_the_page_after_a_wrong_passphrase_refills_nothing(flow):
    """Covers: R-5.5"""
    secret_try = "wrong passphrase marker-77ac"
    page = flow.login(_request(flow), passphrase=secret_try)
    assert "marker-77ac" not in page.text
    assert page.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("request_id", [None, "x" * 43, ""])
def test_the_page_needs_a_live_sign_in_from_this_window(flow, request_id):
    """Covers: R-5.5, R-5.1"""
    params = {} if request_id is None else {"request": request_id}
    assert flow.http.get("/login", params=params).status_code == 404


def test_only_the_real_scrypt_parameters_are_accepted(monkeypatch, tmp_path):
    """Covers: R-5.6"""
    assert REAL_PARAMS == ((131072, 8, 1),)
    monkeypatch.setattr(login, "PASSPHRASE_PARAMS", REAL_PARAMS)
    loaded = config.load(CONFIG, environ())
    with pytest.raises(ConfigError, match="WORKSPACE_MCP_PASSPHRASE_HASH"):
        login.check_hash(loaded.secrets.passphrase_hash)


def test_a_stored_hash_too_long_is_refused(monkeypatch):
    """Covers: R-5.6"""
    import base64

    parts = environ()["WORKSPACE_MCP_PASSPHRASE_HASH"].split("$")
    parts[-1] = base64.b64encode(b"h" * 65).decode()
    with pytest.raises(ConfigError, match="WORKSPACE_MCP_PASSPHRASE_HASH"):
        login.check_hash(config.Secret("$".join(parts)))


def test_the_passphrase_is_never_kept_or_written(flow, caplog):
    """Covers: R-5.6, R-8.3"""
    caplog.set_level(logging.DEBUG)
    request_id = _request(flow)
    flow.login(request_id, passphrase="wrong marker-5d0e passphrase")
    flow.login(request_id)
    flow.alerts.join()
    for text in [record.getMessage() for record in caplog.records] + flow.sent:
        assert "marker-5d0e" not in text and flow.passphrase not in text
    for path in flow.state.iterdir():
        if path.is_file():
            raw = path.read_bytes()
            assert b"marker-5d0e" not in raw and flow.passphrase.encode() not in raw


def test_the_helper_refuses_a_short_passphrase_and_hashes_a_long_one(monkeypatch, capsys):
    """Covers: R-5.7, R-5.6"""
    monkeypatch.setattr(login, "PASSPHRASE_PARAMS", REAL_PARAMS)
    helper = _helper()

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    short, long = "a" * 19, "correct horse battery"
    answers = iter([short, short])
    assert helper.main(["hash_passphrase.py"], stdin=Terminal(), prompt=lambda text: next(answers)) != 0
    assert "20" in capsys.readouterr().err
    answers = iter([long, long])
    assert helper.main(["hash_passphrase.py"], stdin=Terminal(), prompt=lambda text: next(answers)) == 0
    printed = capsys.readouterr().out.strip()
    parsed = login.check_hash(config.Secret(printed))
    assert login.verify(parsed, long)
    assert not login.verify(parsed, long + "x")
    loaded = config.load(CONFIG, dict(environ(), WORKSPACE_MCP_PASSPHRASE_HASH=printed))
    assert loaded.secrets.passphrase_hash.reveal() == printed
    from test_repo_hygiene import RULES

    assert RULES["passphrase hash"].fullmatch(printed)


@pytest.mark.parametrize("pasted", [" correct horse battery", "correct horse battery\n", "correct horse battery "])
def test_the_helper_refuses_a_passphrase_with_a_space_at_either_end(capsys, pasted):
    """Covers: R-5.7"""
    helper = _helper()

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    assert helper.main(["hash_passphrase.py"], stdin=Terminal(), prompt=lambda text: pasted) != 0
    assert capsys.readouterr().out == ""


def test_the_helper_refuses_arguments_a_pipe_and_a_mismatch(capsys):
    """Covers: R-5.7"""
    helper = _helper()

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    long = "correct horse battery"
    assert helper.main(["hash_passphrase.py", long], stdin=Terminal(), prompt=lambda text: long) != 0
    assert helper.main(["hash_passphrase.py"], stdin=io.StringIO(long), prompt=lambda text: long) != 0
    answers = iter([long, long + "x"])
    assert helper.main(["hash_passphrase.py"], stdin=Terminal(), prompt=lambda text: next(answers)) != 0
    assert capsys.readouterr().out == ""


def test_cancel_all_closes_the_window_for_good(flow):
    """Covers: R-6.9, R-5.1"""
    assert flow.authorize(pkce()[1]).status_code == 302
    flow.generation.write_text("1\n")
    assert _closed(flow)
    again = flow.rebuilt()
    with again.http:
        assert _closed(again)


def test_the_cancel_all_command_removes_the_flag_the_service_reads():
    """Covers: R-6.9"""
    loader = importlib.machinery.SourceFileLoader(
        "revoke_all", str(Path(__file__).resolve().parents[2] / "deploy" / "workspace-mcp-revoke-all"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    assert module.FLAG == login.FLAG_PATH


def test_a_public_service_reads_only_the_root_owned_flag(make_flow, tmp_path):
    """Covers: R-5.1"""
    with pytest.raises(ConfigError, match="trial"):
        make_flow(public_url=PUBLIC, trial=app.Trial(flag_path=str(tmp_path / "open")))
    with pytest.raises(ConfigError, match="trial"):
        make_flow(public_url=PUBLIC, trial=app.Trial(owner=os.getuid()))


def test_a_new_window_ends_the_sign_ins_an_earlier_one_started(flow):
    """Covers: R-5.2"""
    import sqlite3

    _request(flow)
    _request(flow)
    flow.open_window()
    assert flow.authorize(pkce()[1]).status_code == 302
    with sqlite3.connect(flow.state / "tokens.db") as db:
        windows = {row[0] for row in db.execute("SELECT window FROM pending")}
    assert windows == {flow.nonce}


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
def test_the_page_takes_only_get_and_post(flow, method):
    """Covers: R-5.4"""
    body = urlencode({"request": _request(flow), "passphrase": flow.passphrase})
    response = flow.http.request(method, "/login", content=body,
                                 headers={"content-type": "application/x-www-form-urlencoded"})
    assert response.status_code == 404
    assert flow.store.window(flow.nonce).attempts == 0


def test_a_closed_window_takes_no_further_attempt_even_mid_request(flow):
    """Covers: R-5.3, R-5.4"""
    _request(flow)
    flow.store.close_window(flow.nonce, "signed in", flow.clock())
    assert flow.store.reserve_attempt(flow.nonce, login.WRONG_LIMIT) is False


def test_a_sign_in_cannot_start_once_the_window_has_closed_mid_request(flow):
    """Covers: R-5.1"""
    from mcp.server.auth.provider import AuthorizationParams, AuthorizeError
    from pydantic import AnyUrl

    provider = flow.service.provider
    flow.flag.unlink()
    params = AuthorizationParams(state=None, scopes=None, code_challenge=pkce()[1],
                                 redirect_uri=AnyUrl(auth.CLAUDE_CALLBACK), redirect_uri_provided_explicitly=True)

    async def start():
        return await provider.authorize(await provider.get_client(flow.client_id), params)

    with pytest.raises(AuthorizeError):
        asyncio.run(start())
