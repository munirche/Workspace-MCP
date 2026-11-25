"""Tokens: lifetimes, replacement, the grace period, reuse, cancel-all and restart."""

import asyncio
import importlib.machinery
import importlib.util
import os
import sqlite3
import stat
import time
from pathlib import Path

import pytest
from conftest import LOCAL, PUBLIC, pkce, query
from mcp.server.auth.provider import TokenError

from workspace_mcp import auth
from workspace_mcp.config import ConfigError

DAY = 24 * 3600
REVOKE = Path(__file__).resolve().parents[2] / "deploy" / "workspace-mcp-revoke-all"
INVALID_GRANT_TEXTS = {
    "refresh token does not exist",
    "refresh token has expired",
    "The refresh token is no longer valid.",
}


def _revoke_module():
    loader = importlib.machinery.SourceFileLoader("workspace_mcp_revoke_all", str(REVOKE))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def _refused(response):
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"] == "invalid_grant"
    assert body.get("error_description") in INVALID_GRANT_TEXTS
    assert "access_token" not in body


def _works(flow, tokens):
    return flow.mcp(tokens["access_token"]).status_code == 200


def _rotated(flow, refresh_token, **headers):
    response = flow.http.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": flow.client_id,
            "client_secret": flow.secret,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_an_access_token_lasts_one_hour(flow):
    """Covers: R-6.1"""
    tokens = flow.sign_in()
    assert tokens["expires_in"] == 3600
    flow.clock.advance(3599)
    assert _works(flow, tokens)
    flow.clock.advance(1)
    assert flow.mcp(tokens["access_token"]).status_code == 401


def test_a_refresh_token_lasts_thirty_days(flow):
    """Covers: R-6.1"""
    kept, lapsed = flow.sign_in(), flow.sign_in()
    flow.clock.advance(30 * DAY - 1)
    assert flow.refresh(kept["refresh_token"]).status_code == 200
    flow.clock.advance(1)
    _refused(flow.refresh(lapsed["refresh_token"]))


def test_a_refresh_starts_thirty_new_days(flow):
    """Covers: R-6.2"""
    tokens = flow.sign_in()
    flow.clock.advance(29 * DAY)
    renewed = _rotated(flow, tokens["refresh_token"])
    flow.clock.advance(29 * DAY)
    again = _rotated(flow, renewed["refresh_token"])
    assert _works(flow, again)


def test_a_refresh_retires_the_token_used(flow):
    """Covers: R-6.2"""
    first = flow.sign_in()
    _rotated(flow, first["refresh_token"])
    flow.clock.advance(61)
    _refused(flow.refresh(first["refresh_token"]))


def test_a_refresh_retires_every_other_token_of_the_sign_in(flow):
    """Covers: R-6.2"""
    a = flow.sign_in()
    b = _rotated(flow, a["refresh_token"])
    c = _rotated(flow, a["refresh_token"])
    d = _rotated(flow, b["refresh_token"])
    # c, issued in a's grace period, was retired by b's rotation, so using it
    # more than sixty seconds later is a reuse and cancels the sign-in.
    flow.clock.advance(61)
    _refused(flow.refresh(c["refresh_token"]))
    assert flow.mcp(d["access_token"]).status_code == 401


def test_the_access_token_already_issued_keeps_its_hour(flow):
    """Covers: R-6.2"""
    first = flow.sign_in()
    flow.clock.advance(600)
    _rotated(flow, first["refresh_token"])
    flow.clock.advance(2999)
    assert _works(flow, first)


def test_a_retired_token_used_within_sixty_seconds_gets_a_further_pair(flow):
    """Covers: R-6.3"""
    first = flow.sign_in()
    replacement = _rotated(flow, first["refresh_token"])
    flow.clock.advance(60)
    further = _rotated(flow, first["refresh_token"])
    assert _works(flow, further)
    assert _works(flow, replacement)
    # The replacement is still live, not retired by the grace use: past
    # another sixty seconds it rotates normally.
    flow.clock.advance(61)
    assert flow.refresh(replacement["refresh_token"]).status_code == 200
    flow.alerts.join()
    assert flow.sent == []


def test_a_retired_token_used_with_the_clock_stepped_back_is_refused_without_cancelling(flow):
    """Covers: R-6.3, R-6.5"""
    first = flow.sign_in()
    second = _rotated(flow, first["refresh_token"])
    flow.clock.advance(-120)
    _refused(flow.refresh(first["refresh_token"]))
    flow.alerts.join()
    assert flow.sent == []
    flow.clock.advance(120)
    assert _works(flow, second)


def test_a_grace_use_does_not_restart_the_sixty_seconds(flow):
    """Covers: R-6.3, R-6.4"""
    first = flow.sign_in()
    _rotated(flow, first["refresh_token"])
    flow.clock.advance(30)
    _rotated(flow, first["refresh_token"])
    flow.clock.advance(31)
    _refused(flow.refresh(first["refresh_token"]))


def test_two_uses_of_one_live_token_side_by_side_both_succeed(flow):
    """Covers: R-6.3"""
    provider = flow.service.provider
    refresh_token = flow.sign_in()["refresh_token"]

    async def race():
        client = await provider.get_client(flow.client_id)
        first = await provider.load_refresh_token(client, refresh_token)
        second = await provider.load_refresh_token(client, refresh_token)
        return [await provider.exchange_refresh_token(client, loaded, loaded.scopes) for loaded in (first, second)]

    issued = asyncio.run(race())
    for pair in issued:
        assert flow.mcp(pair.access_token).status_code == 200
        assert flow.refresh(pair.refresh_token).status_code == 200
    flow.alerts.join()
    assert flow.sent == []


def test_a_retired_token_used_after_sixty_seconds_cancels_the_sign_in_and_alerts(flow):
    """Covers: R-6.4"""
    first = flow.sign_in()
    other = flow.sign_in()
    second = _rotated(flow, first["refresh_token"])
    grace = _rotated(flow, first["refresh_token"])
    newest = _rotated(flow, second["refresh_token"])
    flow.clock.advance(61)
    response = flow.http.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": flow.client_id,
            "client_secret": flow.secret,
        },
        headers={"x-forwarded-for": "203.0.113.9"},
    )
    _refused(response)
    for pair in (first, second, grace, newest):
        assert flow.mcp(pair["access_token"]).status_code == 401
        _refused(flow.refresh(pair["refresh_token"]))
    assert _works(flow, other)
    assert flow.refresh(other["refresh_token"]).status_code == 200
    flow.alerts.join()
    assert len(flow.sent) == 1
    assert "a retired refresh token was used after its grace period" in flow.sent[0]
    assert "from 203.0.113.9" in flow.sent[0]


@pytest.mark.parametrize(
    "headers",
    [
        [("x-forwarded-for", "203.0.113.9"), ("x-forwarded-for", "198.51.100.1")],
        [("x-forwarded-for", "203.0.113.9, 198.51.100.1")],
        [("x-forwarded-for", "not an address")],
        [],
    ],
    ids=["two headers", "a list", "not an address", "none"],
)
def test_a_reuse_alert_names_the_address_only_when_nginx_gave_exactly_one(flow, headers):
    """Covers: R-6.4"""
    first = flow.sign_in()
    _rotated(flow, first["refresh_token"])
    flow.clock.advance(61)
    response = flow.http.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": flow.client_id,
            "client_secret": flow.secret,
        },
        headers=headers,
    )
    _refused(response)
    flow.alerts.join()
    assert len(flow.sent) == 1
    assert flow.sent[0].endswith("from unknown.")


def test_a_reuse_that_also_drops_the_scope_still_cancels_the_sign_in(flow):
    """Covers: R-6.4"""
    first = flow.sign_in()
    second = _rotated(flow, first["refresh_token"])
    flow.clock.advance(61)
    _refused(flow.refresh(first["refresh_token"], scope="offline_access"))
    assert flow.mcp(second["access_token"]).status_code == 401
    flow.alerts.join()
    assert len(flow.sent) == 1


def test_a_live_token_that_drops_the_scope_is_refused_and_stays_live(flow):
    """Covers: R-6.2"""
    tokens = flow.sign_in()
    narrowed = flow.refresh(tokens["refresh_token"], scope="offline_access")
    assert narrowed.json()["error"] == "invalid_scope"
    flow.clock.advance(61)
    assert flow.refresh(tokens["refresh_token"]).status_code == 200


@pytest.mark.parametrize("retired", [False, True], ids=["live", "retired"])
def test_a_refresh_token_expired_by_the_real_clock_is_refused_by_the_sdk_without_an_alert(flow, retired):
    """Covers: R-6.5, R-6.1"""
    issued_at = time.time() - 31 * DAY
    chain = auth.Chain("munir", flow.client_id, ("workspace", "offline_access"), flow.config.public.resource, 0)
    flow.store.start_chain(chain, issued_at, "old-access", "old-refresh")
    if retired:
        assert flow.store.rotate("old-refresh", issued_at + 10, 0, "next-access", "next-refresh") == "issued"
    response = flow.refresh("old-refresh")
    _refused(response)
    assert response.json()["error_description"] == "refresh token has expired"
    flow.alerts.join()
    assert flow.sent == []


def test_an_alert_that_cannot_be_sent_does_not_change_the_answer(make_flow):
    """Covers: R-6.4"""

    def broken(text):
        raise OSError("unreachable")

    flow = make_flow(sender=broken)
    first = flow.sign_in()
    _rotated(flow, first["refresh_token"])
    flow.clock.advance(61)
    _refused(flow.refresh(first["refresh_token"]))
    flow.alerts.join()


def test_a_refresh_token_that_expires_while_its_refresh_is_under_way_is_refused(flow):
    """Covers: R-6.1, R-6.5"""
    provider = flow.service.provider
    tokens = flow.sign_in()
    flow.clock.advance(30 * DAY - 1)

    async def interleaved():
        client = await provider.get_client(flow.client_id)
        loaded = await provider.load_refresh_token(client, tokens["refresh_token"])
        flow.clock.advance(1)
        with pytest.raises(TokenError) as refused:
            await provider.exchange_refresh_token(client, loaded, loaded.scopes)
        return refused.value.error

    assert asyncio.run(interleaved()) == "invalid_grant"


def test_a_sign_in_request_yields_one_code(flow):
    """Covers: R-6.6"""
    _, challenge = pkce()
    request_id = flow.request_id(flow.authorize(challenge))
    assert flow.service.provider.approve(request_id) is not None
    assert flow.service.provider.approve(request_id) is None


def test_a_refresh_under_way_cannot_issue_after_its_sign_in_is_cancelled(flow):
    """Covers: R-6.4"""
    provider = flow.service.provider
    first = flow.sign_in()
    second = _rotated(flow, first["refresh_token"])

    async def interleaved():
        client = await provider.get_client(flow.client_id)
        loaded = await provider.load_refresh_token(client, second["refresh_token"])
        flow.clock.advance(61)
        _refused(flow.refresh(first["refresh_token"]))
        with pytest.raises(TokenError) as refused:
            await provider.exchange_refresh_token(client, loaded, loaded.scopes)
        return refused.value.error

    assert asyncio.run(interleaved()) == "invalid_grant"


@pytest.mark.parametrize("case", ["expired", "cancelled", "unknown", "older generation", "retired and expired"])
def test_a_dead_refresh_token_is_told_to_sign_in_again_and_nothing_else(flow, case):
    """Covers: R-6.5"""
    first = flow.sign_in()
    token = first["refresh_token"]
    if case == "expired":
        flow.clock.advance(30 * DAY)
    elif case == "cancelled":
        _rotated(flow, token)
        flow.clock.advance(61)
        flow.refresh(token)
        flow.alerts.join()
        flow.sent.clear()
    elif case == "unknown":
        token = "x" * 43
    elif case == "older generation":
        flow.generation.write_text("1\n")
    elif case == "retired and expired":
        _rotated(flow, token)
        flow.clock.advance(30 * DAY)
    _refused(flow.refresh(token))
    flow.alerts.join()
    assert flow.sent == []


def test_tokens_codes_and_sign_ins_are_stored_only_as_hashes(flow):
    """Covers: R-6.7"""
    verifier, challenge = pkce()
    request_id = flow.request_id(flow.authorize(challenge))
    pending_verifier, pending_challenge = pkce()
    pending_id = flow.request_id(flow.authorize(pending_challenge))
    code = query(flow.service.provider.approve(request_id))["code"]
    tokens = flow.exchange(code, verifier).json()
    renewed = _rotated(flow, tokens["refresh_token"])
    issued = [request_id, pending_id, code, verifier, pending_verifier, tokens["access_token"],
              tokens["refresh_token"], renewed["access_token"], renewed["refresh_token"]]
    for path in flow.state.iterdir():
        raw = path.read_bytes()
        for value in issued:
            assert value.encode() not in raw, path.name


def test_every_stored_token_and_code_names_its_user(flow):
    """Covers: R-6.7"""
    _, challenge = pkce()
    flow.service.provider.approve(flow.request_id(flow.authorize(challenge)))
    flow.sign_in()
    with sqlite3.connect(flow.state / "tokens.db") as db:
        assert {row[0] for row in db.execute("SELECT user FROM chains")} == {"munir"}
        assert {row[0] for row in db.execute("SELECT user FROM codes")} == {"munir"}


def test_cancel_all_stops_every_token_code_and_sign_in_issued_before_it(flow):
    """Covers: R-6.9"""
    tokens = flow.sign_in()
    code_verifier, code_challenge = pkce()
    code = flow.code(code_challenge)
    _, pending_challenge = pkce()
    pending = flow.request_id(flow.authorize(pending_challenge))
    flag = flow.state / "open"
    flag.write_text("flag\n")
    printed = _revoke_module().revoke(str(flow.generation), str(flag))
    assert printed >= int(time.time()) - 5
    assert flow.generation.read_text() == f"{printed}\n"
    assert not flag.exists()
    assert flow.mcp(tokens["access_token"]).status_code == 401
    _refused(flow.refresh(tokens["refresh_token"]))
    assert flow.exchange(code, code_verifier).json()["error"] == "invalid_grant"
    assert flow.service.provider.approve(pending) is None
    assert _works(flow, flow.sign_in())


@pytest.mark.parametrize("content", [None, "", "x\n", "-1\n", "1\n2\n", " 1\n", "1"])
def test_without_a_readable_cancel_all_number_nothing_is_accepted(flow, content):
    """Covers: R-6.9"""
    tokens = flow.sign_in()
    if content is None:
        flow.generation.unlink()
    else:
        flow.generation.write_text(content)
    assert flow.mcp(tokens["access_token"]).status_code == 401
    _refused(flow.refresh(tokens["refresh_token"]))
    _, challenge = pkce()
    response = flow.authorize(challenge)
    assert response.status_code == 302
    assert query(response.headers["location"])["error"] == "temporarily_unavailable"


def test_a_pipe_in_place_of_the_cancel_all_number_is_refused_without_waiting(flow, tmp_path):
    """Covers: R-6.9"""
    import threading

    tokens = flow.sign_in()
    flow.generation.unlink()
    os.mkfifo(flow.generation)
    answers = []
    worker = threading.Thread(target=lambda: answers.append(flow.service.provider._store.generation()), daemon=True)
    worker.start()
    worker.join(5)
    assert answers == [None], "reading the cancel-all number waited on a pipe"
    assert flow.mcp(tokens["access_token"]).status_code == 401
    revoke = _revoke_module()
    with pytest.raises(revoke.Refused):
        revoke.revoke(str(flow.generation), str(tmp_path / "flag"))


def test_a_cancel_all_number_reached_through_a_link_is_refused(flow, tmp_path):
    """Covers: R-6.9"""
    tokens = flow.sign_in()
    target = tmp_path / "elsewhere"
    target.write_text("0\n")
    flow.generation.unlink()
    flow.generation.symlink_to(target)
    assert flow.mcp(tokens["access_token"]).status_code == 401


def test_the_cancel_all_command_refuses_arguments_a_link_and_a_malformed_number(tmp_path, capsys):
    """Covers: R-6.9"""
    revoke = _revoke_module()
    assert revoke.main(["workspace-mcp-revoke-all", str(tmp_path / "generation")]) == 2
    assert "takes no arguments" in capsys.readouterr().err
    real = tmp_path / "real"
    real.write_text("4\n")
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(revoke.Refused):
        revoke.revoke(str(link), str(tmp_path / "flag"))
    assert real.read_text() == "4\n"
    bad = tmp_path / "bad"
    bad.write_text("four\n")
    with pytest.raises(revoke.Refused):
        revoke.revoke(str(bad), str(tmp_path / "flag"))
    missing = tmp_path / "missing"
    first = revoke.revoke(str(missing), str(tmp_path / "flag"))
    assert first >= int(time.time()) - 5
    assert missing.read_text() == f"{first}\n"
    assert stat.S_IMODE(missing.stat().st_mode) == 0o644
    assert revoke.revoke(str(missing), str(tmp_path / "flag")) == first + 1


def test_the_cancel_all_command_replaces_a_link_planted_beside_the_number(tmp_path):
    """Covers: R-6.9"""
    revoke = _revoke_module()
    generation = tmp_path / "generation"
    generation.write_text("4\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("untouched\n")
    (tmp_path / ".generation.new").symlink_to(elsewhere)
    new = revoke.revoke(str(generation), str(tmp_path / "flag"))
    assert generation.read_text() == f"{new}\n"
    assert not generation.is_symlink()
    assert elsewhere.read_text() == "untouched\n"


def test_the_cancel_all_command_says_so_when_the_window_could_not_be_closed(tmp_path):
    """Covers: R-6.9"""
    revoke = _revoke_module()
    generation = tmp_path / "generation"
    generation.write_text("4\n")
    flag = tmp_path / "flag"
    flag.mkdir()
    (flag / "inside").touch()
    with pytest.raises(revoke.WindowNotClosed, match="tokens are cancelled"):
        revoke.revoke(str(generation), str(flag))
    assert generation.read_text() != "4\n"


def test_a_cancel_all_number_that_goes_back_does_not_revive_anything(flow):
    """Covers: R-6.9"""
    flow.generation.write_text("5\n")
    tokens = flow.sign_in()
    flow.generation.write_text("6\n")
    assert flow.mcp(tokens["access_token"]).status_code == 401
    flow.generation.write_text("5\n")
    assert flow.mcp(tokens["access_token"]).status_code == 401
    _refused(flow.refresh(tokens["refresh_token"]))
    _, challenge = pkce()
    assert query(flow.authorize(challenge).headers["location"])["error"] == "temporarily_unavailable"
    again = flow.rebuilt()
    with again.http:
        assert again.mcp(tokens["access_token"]).status_code == 401


def test_the_cancel_all_command_writes_where_the_service_reads(tmp_path):
    """Covers: R-6.9"""
    revoke = _revoke_module()
    assert revoke.GENERATION == auth.GENERATION_PATH
    first_line = REVOKE.read_text().splitlines()[0]
    assert first_line == "#!/usr/bin/python3 -I"
    assert "workspace_mcp" not in REVOKE.read_text()


def test_a_public_service_reads_only_the_root_owned_cancel_all_number(make_flow, tmp_path):
    """Covers: R-6.9"""
    own = tmp_path / "generation"
    own.write_text("0\n")
    with pytest.raises(ConfigError, match="trial"):
        make_flow(public_url=PUBLIC, generation=own)


def test_tokens_survive_a_restart(flow):
    """Covers: R-6.10"""
    tokens = flow.sign_in()
    renewed = _rotated(flow, tokens["refresh_token"])
    again = flow.rebuilt()
    with again.http:
        assert _works(again, renewed)
        assert _works(again, tokens)
        assert again.refresh(renewed["refresh_token"]).status_code == 200


def test_a_cancelled_sign_in_stays_cancelled_after_a_restart(flow):
    """Covers: R-6.10, R-6.4"""
    first = flow.sign_in()
    second = _rotated(flow, first["refresh_token"])
    flow.clock.advance(61)
    _refused(flow.refresh(first["refresh_token"]))
    again = flow.rebuilt()
    with again.http:
        assert again.mcp(second["access_token"]).status_code == 401


def test_the_token_store_is_readable_by_the_service_alone(tmp_path):
    """Covers: R-6.10, R-6.7"""
    previous = os.umask(0)
    try:
        auth.SqliteStore(tmp_path / "tokens.db", str(tmp_path / "generation"))
    finally:
        os.umask(previous)
    assert stat.S_IMODE((tmp_path / "tokens.db").stat().st_mode) == 0o600
    loose = tmp_path / "loose.db"
    loose.touch(mode=0o644)
    os.chmod(loose, 0o644)
    auth.SqliteStore(loose, str(tmp_path / "generation"))
    assert stat.S_IMODE(loose.stat().st_mode) == 0o600


def test_the_token_store_refuses_a_link(tmp_path):
    """Covers: R-6.7"""
    target = tmp_path / "target.db"
    target.touch()
    (tmp_path / "tokens.db").symlink_to(target)
    with pytest.raises(OSError):
        auth.SqliteStore(tmp_path / "tokens.db", str(tmp_path / "generation"))
