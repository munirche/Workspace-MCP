"""Starting and finishing a sign-in, driven through the service's own routes."""

import asyncio
import base64
from urllib.parse import quote

import pytest
from conftest import CONFIG, LOCAL, PUBLIC, Flow, pkce, query
from mcp.server.auth.provider import TokenError

from workspace_mcp import app, auth
from workspace_mcp.config import ConfigError

INSPECTOR = "http://localhost:6274/oauth/callback"


def _basic(client_id, secret):
    return "Basic " + base64.b64encode(f"{quote(client_id, safe='')}:{quote(secret, safe='')}".encode()).decode()


def _not_found(flow, response):
    unknown = flow.http.get("/nowhere")
    assert response.status_code == 404
    assert "location" not in response.headers
    assert response.content == unknown.content
    assert dict(response.headers) == dict(unknown.headers)


def _refused_at_start(flow, response):
    # Refused as the protocol allows: a direct error, or an error sent back to
    # the configured client; never the sign-in page.
    if response.status_code == 302:
        location = response.headers["location"]
        assert location.split("?")[0] == flow.return_address
        assert "error" in query(location) and "code" not in query(location)
    else:
        assert response.status_code in (400, 404)


def test_a_sign_in_from_another_client_is_refused_with_no_page_and_no_redirect(flow):
    """Covers: R-5.8"""
    _, challenge = pkce()
    marker = "client-marker-8d1f"
    response = flow.authorize(challenge, client_id=marker)
    _not_found(flow, response)
    assert marker not in response.text


@pytest.mark.parametrize(
    "address",
    [
        auth.CLAUDE_CALLBACK + "/",
        auth.CLAUDE_CALLBACK.replace("https://", "http://"),
        auth.CLAUDE_CALLBACK.replace("auth_callback", "Auth_callback"),
        auth.CLAUDE_CALLBACK + "?next=x",
        auth.CLAUDE_CALLBACK + "#fragment",
        auth.CLAUDE_CALLBACK.replace("https://", "https://user@"),
        auth.CLAUDE_CALLBACK.replace("claude.ai", "claude.ai."),
        auth.CLAUDE_CALLBACK.replace("claude.ai", "claude.ai:443"),
        "https://claude.ai/api/mcp/other",
        "https://attacker.example/api/mcp/auth_callback",
    ],
)
def test_a_sign_in_to_another_return_address_is_refused_with_no_page_and_no_redirect(flow, address):
    """Covers: R-5.8"""
    _, challenge = pkce()
    response = flow.authorize(challenge, redirect_uri=address)
    _not_found(flow, response)
    assert "attacker" not in response.text


@pytest.mark.parametrize("repeated", ["client_id", "redirect_uri"])
@pytest.mark.parametrize("order", ["same twice", "right then wrong", "wrong then right"])
def test_a_repeated_client_or_return_address_is_refused(flow, repeated, order):
    """Covers: R-5.8"""
    _, challenge = pkce()
    right = flow.client_id if repeated == "client_id" else flow.return_address
    wrong = "client-other" if repeated == "client_id" else "https://attacker.example/cb"
    first, second = {"same twice": (right, right), "right then wrong": (right, wrong),
                     "wrong then right": (wrong, right)}[order]
    params = [
        ("response_type", "code"),
        ("client_id", flow.client_id),
        ("redirect_uri", flow.return_address),
        ("code_challenge", challenge),
        ("code_challenge_method", "S256"),
    ]
    params = [(key, value) for key, value in params if key != repeated] + [(repeated, first), (repeated, second)]
    _not_found(flow, flow.http.get("/authorize", params=params))


@pytest.mark.parametrize("method", ["POST", "HEAD", "PUT"])
def test_a_sign_in_started_other_than_by_get_is_refused(flow, method):
    """Covers: R-5.8"""
    _, challenge = pkce()
    form = {
        "response_type": "code",
        "client_id": flow.client_id,
        "redirect_uri": flow.return_address,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    response = flow.http.request(method, "/authorize", data=form if method != "HEAD" else None)
    assert response.status_code == 404
    assert "location" not in response.headers


def test_a_sign_in_posted_with_a_right_query_and_a_wrong_body_is_refused(flow):
    """Covers: R-5.8"""
    _, challenge = pkce()
    params = {
        "response_type": "code",
        "client_id": flow.client_id,
        "redirect_uri": flow.return_address,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    body = dict(params, redirect_uri="https://attacker.example/cb")
    response = flow.http.post("/authorize", params=params, data=body)
    _not_found(flow, response)
    assert "attacker" not in response.text


def test_a_sign_in_on_a_trailing_slash_path_is_refused(flow):
    """Covers: R-5.8"""
    _, challenge = pkce()
    response = flow.http.get("/authorize/", params={"client_id": flow.client_id, "code_challenge": challenge})
    assert response.status_code == 404
    assert "location" not in response.headers


def test_the_return_address_fully_percent_encoded_is_accepted(flow):
    """Covers: R-5.8"""
    verifier, challenge = pkce()
    pairs = {
        "response_type": "code",
        "client_id": flow.client_id,
        "redirect_uri": flow.return_address,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    target = "/authorize?" + "&".join(f"{key}={quote(value, safe='')}" for key, value in pairs.items())
    redirect = flow.service.provider.approve(flow.request_id(flow.http.get(target)))
    assert redirect.split("?")[0] == auth.CLAUDE_CALLBACK
    assert flow.exchange(query(redirect)["code"], verifier).status_code == 200


def test_a_sign_in_that_omits_the_return_address_returns_to_the_configured_one(flow):
    """Covers: R-5.8"""
    verifier, challenge = pkce()
    redirect = flow.service.provider.approve(flow.request_id(flow.authorize(challenge, redirect_uri=None)))
    assert redirect.split("?")[0] == auth.CLAUDE_CALLBACK
    code = query(redirect)["code"]
    assert flow.exchange(code, verifier, redirect_uri=auth.CLAUDE_CALLBACK).status_code == 400
    assert flow.exchange(code, verifier, redirect_uri=None).status_code == 200


def test_a_sign_in_that_names_the_return_address_must_name_it_again_at_the_exchange(flow):
    """Covers: R-5.8"""
    verifier, challenge = pkce()
    code = flow.code(challenge)
    assert flow.exchange(code, verifier, redirect_uri=None).status_code == 400
    assert flow.exchange(code, verifier).status_code == 200


def test_the_sign_in_returns_the_state_unchanged(flow):
    """Covers: R-5.8"""
    _, challenge = pkce()
    redirect = flow.service.provider.approve(flow.request_id(flow.authorize(challenge, state="s t&a=te")))
    assert query(redirect)["state"] == "s t&a=te"


def test_there_is_no_way_for_a_client_to_register(flow):
    """Covers: R-5.9"""
    assert flow.http.get("/register").status_code == 404
    assert flow.http.post("/register", json={"redirect_uris": [auth.CLAUDE_CALLBACK]}).status_code == 404
    metadata = flow.http.get("/.well-known/oauth-authorization-server").json()
    assert "registration_endpoint" not in metadata
    assert "revocation_endpoint" not in metadata


def test_a_grant_other_than_code_and_refresh_is_refused(flow):
    """Covers: R-5.9"""
    response = flow.http.post(
        "/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": "x",
            "client_id": flow.client_id,
            "client_secret": flow.secret,
        },
    )
    assert response.status_code == 400
    assert "access_token" not in response.text


def test_only_the_s256_proof_key_is_published(flow):
    """Covers: R-5.10"""
    metadata = flow.http.get("/.well-known/oauth-authorization-server").json()
    assert metadata["code_challenge_methods_supported"] == ["S256"]


@pytest.mark.parametrize(
    "params",
    [
        {"code_challenge": None},
        {"code_challenge_method": "plain"},
        {"code_challenge": "A" * 42},
        {"code_challenge": "A" * 44},
        {"code_challenge": "A" * 42 + "+"},
        {"code_challenge": "A" * 42 + "="},
    ],
)
def test_a_sign_in_without_an_s256_proof_key_is_refused(flow, params):
    """Covers: R-5.10"""
    _, challenge = pkce()
    _refused_at_start(flow, flow.authorize(challenge, **params))


def test_a_code_exchanges_once(flow):
    """Covers: R-6.6"""
    verifier, challenge = pkce()
    code = flow.code(challenge)
    assert flow.exchange(code, verifier).status_code == 200
    again = flow.exchange(code, verifier)
    assert again.status_code == 400
    assert again.json()["error"] == "invalid_grant"


def test_a_code_is_good_for_five_minutes(flow):
    """Covers: R-6.6"""
    verifier, challenge = pkce()
    code = flow.code(challenge)
    flow.clock.advance(299)
    assert flow.exchange(code, verifier).status_code == 200
    for seconds in (300, 301):
        late_verifier, late_challenge = pkce()
        late = flow.code(late_challenge)
        flow.clock.advance(seconds)
        response = flow.exchange(late, late_verifier)
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_grant"


def test_a_code_needs_the_matching_proof_key(flow):
    """Covers: R-6.6"""
    verifier, challenge = pkce()
    code = flow.code(challenge)
    wrong, _ = pkce()
    response = flow.exchange(code, wrong)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
    assert flow.exchange(code, verifier).status_code == 200


def test_two_exchanges_of_one_code_side_by_side_yield_one_pair(flow):
    """Covers: R-6.6"""
    provider = flow.service.provider
    _, challenge = pkce()
    code = flow.code(challenge)

    async def race():
        client = await provider.get_client(flow.client_id)
        first = await provider.load_authorization_code(client, code)
        second = await provider.load_authorization_code(client, code)
        assert first is not None and second is not None
        outcomes = []
        for loaded in (first, second):
            try:
                outcomes.append(await provider.exchange_authorization_code(client, loaded))
            except TokenError as error:
                outcomes.append(error.error)
        return outcomes

    outcomes = asyncio.run(race())
    assert outcomes[1] == "invalid_grant"
    assert outcomes[0].access_token


@pytest.mark.parametrize(
    "case",
    ["none", "malformed", "unknown", "expired", "in the query", "wrong scheme"],
)
def test_a_tool_request_without_a_valid_token_is_told_where_to_sign_in(flow, case):
    """Covers: R-6.8"""
    tokens = flow.sign_in()
    token, headers, path = None, {}, "/mcp"
    if case == "malformed":
        headers = {"authorization": "Bearer"}
    elif case == "unknown":
        token = "x" * 43
    elif case == "expired":
        token = tokens["access_token"]
        flow.clock.advance(3600)
    elif case == "in the query":
        response = flow.http.post(
            f"/mcp?access_token={tokens['access_token']}",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream"},
        )
    elif case == "wrong scheme":
        headers = {"authorization": f"Basic {tokens['access_token']}"}
    if case != "in the query":
        response = flow.mcp(token, headers)
    assert response.status_code == 401
    pointer = f'resource_metadata="{flow.config.public.issuer}/.well-known/oauth-protected-resource/mcp"'
    assert pointer in response.headers["www-authenticate"]


def test_the_sign_in_pointer_leads_to_this_service(flow):
    """Covers: R-6.8"""
    metadata = flow.http.get("/.well-known/oauth-protected-resource/mcp")
    assert metadata.status_code == 200
    assert metadata.json()["resource"] == flow.config.public.resource
    assert metadata.json()["authorization_servers"] == [flow.config.public.issuer]
    assert metadata.json()["scopes_supported"] == ["workspace"]
    server = flow.http.get("/.well-known/oauth-authorization-server").json()
    assert server["issuer"] == flow.config.public.issuer
    assert server["token_endpoint"] == flow.config.public.issuer + "/token"
    assert server["scopes_supported"] == ["workspace", "offline_access"]


def test_a_valid_token_is_let_through(flow):
    """Covers: R-6.8"""
    response = flow.mcp(flow.sign_in()["access_token"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


def test_the_service_checks_whom_a_token_was_issued_for(flow):
    """Covers: R-6.11"""
    assert flow.service.server.settings.auth.validate_token_resource is True
    assert str(flow.service.server.settings.auth.resource_server_url) == flow.config.public.resource


def test_a_token_issued_for_another_service_is_refused(flow):
    """Covers: R-6.11"""
    for name, resource in (("own", flow.config.public.resource), ("other", "https://other.example/mcp")):
        chain = auth.Chain("munir", flow.client_id, ("workspace", "offline_access"), resource, 0)
        flow.store.start_chain(chain, flow.clock(), f"{name}-access", f"{name}-refresh")
    assert flow.mcp("own-access").status_code == 200
    assert flow.mcp("other-access").status_code == 401
    # Refused by the service's own check too, whatever the SDK's setting.
    provider = flow.service.provider
    assert asyncio.run(provider.load_access_token("own-access")) is not None
    assert asyncio.run(provider.load_access_token("other-access")) is None


@pytest.mark.parametrize(
    "resource",
    [None, LOCAL + "/mcp", LOCAL + "/mcp/", "http://LOCALHOST:8765/mcp", "HTTP://localhost:8765/mcp/"],
)
def test_a_token_is_issued_for_this_service(flow, resource):
    """Covers: R-6.11"""
    tokens = flow.sign_in(resource=resource)
    assert flow.mcp(tokens["access_token"]).status_code == 200


@pytest.mark.parametrize(
    "resource",
    ["https://other.example/mcp", LOCAL, LOCAL + "/mcp/other", "http://localhost:8766/mcp", "not a url", ""],
)
def test_a_sign_in_for_another_service_is_refused(flow, resource):
    """Covers: R-6.11"""
    _, challenge = pkce()
    response = flow.authorize(challenge, resource=resource)
    assert response.status_code == 302
    assert query(response.headers["location"])["error"] == "invalid_target"


def test_token_requests_are_taken_in_form_encoding(flow):
    """Covers: R-6.12"""
    tokens = flow.sign_in()
    assert tokens["token_type"] == "Bearer"
    assert flow.refresh(tokens["refresh_token"]).status_code == 200


def test_a_token_request_in_another_encoding_is_refused(flow):
    """Covers: R-6.12"""
    verifier, challenge = pkce()
    code = flow.code(challenge)
    response = flow.http.post(
        "/token",
        json={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": flow.return_address,
            "client_id": flow.client_id,
            "client_secret": flow.secret,
        },
    )
    assert response.status_code in (400, 401)
    assert "access_token" not in response.text
    assert flow.exchange(code, verifier).status_code == 200


@pytest.mark.parametrize(
    "fields",
    [
        {"client_secret": None},
        {"client_secret": "wrong-secret"},
        {"client_id": "client-other"},
        {"client_id": None},
    ],
    ids=["no secret", "wrong secret", "wrong id", "no id"],
)
def test_a_code_exchange_without_the_clients_id_and_secret_is_refused(flow, fields):
    """Covers: R-6.13"""
    verifier, challenge = pkce()
    code = flow.code(challenge)
    response = flow.exchange(code, verifier, **fields)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"
    assert flow.exchange(code, verifier).status_code == 200


@pytest.mark.parametrize(
    "fields",
    [
        {"client_secret": None},
        {"client_secret": "wrong-secret"},
        {"client_id": "client-other"},
        {"client_id": None},
    ],
    ids=["no secret", "wrong secret", "wrong id", "no id"],
)
def test_a_refresh_without_the_clients_id_and_secret_is_refused(flow, fields):
    """Covers: R-6.13, R-6.5"""
    refresh_token = flow.sign_in()["refresh_token"]
    response = flow.refresh(refresh_token, **fields)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"
    assert flow.refresh(refresh_token).status_code == 200


def test_the_clients_secret_is_taken_in_the_basic_header(flow):
    """Covers: R-6.13, R-6.12"""
    verifier, challenge = pkce()
    code = flow.code(challenge)
    response = flow.http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": flow.return_address,
        },
        headers={"authorization": _basic(flow.client_id, flow.secret)},
    )
    assert response.status_code == 200, response.text
    refresh = flow.http.post(
        "/token",
        data={"grant_type": "refresh_token", "refresh_token": response.json()["refresh_token"]},
        headers={"authorization": _basic(flow.client_id, flow.secret)},
    )
    assert refresh.status_code == 200, refresh.text


@pytest.mark.parametrize("encoded", [True, False], ids=["form-encoded", "raw"])
def test_a_secret_with_reserved_characters_is_taken_in_the_basic_header_either_way(make_flow, encoded):
    """Covers: R-6.13"""
    secret = "a+b%41c d/e:f"
    flow = make_flow(secret=secret)
    verifier, challenge = pkce()
    credentials = _basic(flow.client_id, secret) if encoded else (
        "Basic " + base64.b64encode(f"{flow.client_id}:{secret}".encode()).decode()
    )
    response = flow.http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": flow.code(challenge),
            "code_verifier": verifier,
            "redirect_uri": flow.return_address,
        },
        headers={"authorization": credentials},
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    ("header", "body"),
    [
        ("wrong secret", {}),
        ("conflicting id", {}),
        ("conflicting secret", {}),
        ("not base64", {}),
        ("no colon", {}),
        ("two headers", {}),
    ],
)
def test_a_basic_header_that_does_not_match_is_refused(flow, header, body):
    """Covers: R-6.13"""
    verifier, challenge = pkce()
    code = flow.code(challenge)
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": flow.return_address,
    }
    headers = {"authorization": _basic(flow.client_id, flow.secret)}
    if header == "wrong secret":
        headers = {"authorization": _basic(flow.client_id, "wrong-secret")}
    elif header == "conflicting id":
        form["client_id"] = "client-other"
    elif header == "conflicting secret":
        form["client_secret"] = "wrong-secret"
    elif header == "not base64":
        headers = {"authorization": "Basic %%%not-base64%%%"}
    elif header == "no colon":
        headers = {"authorization": "Basic " + base64.b64encode(flow.client_id.encode()).decode()}
    if header == "two headers":
        headers = [("authorization", _basic(flow.client_id, flow.secret))] * 2
    response = flow.http.post("/token", data=form, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"
    assert response.headers["www-authenticate"] == "Basic"
    assert flow.exchange(code, verifier).status_code == 200


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "streamed"])
def test_a_basic_token_request_over_the_size_limit_is_refused(flow, monkeypatch, declared):
    """Covers: R-6.13"""
    monkeypatch.setattr(app, "BODY_LIMIT", 200)
    verifier, challenge = pkce()
    code = flow.code(challenge)
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": flow.return_address,
        "padding": "x" * 300,
    }
    headers = {"authorization": _basic(flow.client_id, flow.secret)}
    if declared:
        response = flow.http.post("/token", data=form, headers=headers)
    else:
        body = "&".join(f"{key}={quote(value, safe='')}" for key, value in form.items()).encode()
        headers["content-type"] = "application/x-www-form-urlencoded"
        response = flow.http.post("/token", content=iter([body[:100], body[100:]]), headers=headers)
    assert response.status_code == 413
    assert flow.exchange(code, verifier).status_code == 200


def test_a_basic_token_request_declaring_too_much_is_refused_before_it_is_read(flow, monkeypatch):
    """Covers: R-6.13"""
    monkeypatch.setattr(app, "BODY_LIMIT", 200)
    sent = []

    async def receive():
        raise AssertionError("the body was read")

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/token",
        "headers": [
            (b"authorization", _basic(flow.client_id, flow.secret).encode()),
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", b"201"),
        ],
    }
    asyncio.run(flow.service.app(scope, receive, send))
    assert sent[0]["status"] == 413


def test_the_clients_record_never_prints_its_secret(flow):
    """Covers: R-6.13"""
    record = asyncio.run(flow.service.provider.get_client(flow.client_id))
    assert record.client_secret == flow.secret
    for text in (repr(record), str(record), repr(flow.service.provider), repr(flow.service)):
        assert flow.secret not in text


def test_a_basic_header_matching_the_body_is_accepted(flow):
    """Covers: R-6.13"""
    verifier, challenge = pkce()
    response = flow.http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": flow.code(challenge),
            "code_verifier": verifier,
            "redirect_uri": flow.return_address,
            "client_id": flow.client_id,
            "client_secret": flow.secret,
        },
        headers={"authorization": _basic(flow.client_id, flow.secret)},
    )
    assert response.status_code == 200, response.text


def test_every_token_carries_the_services_scope(flow):
    """Covers: R-6.11"""
    for scope in (None, "offline_access", "workspace", "workspace offline_access"):
        tokens = flow.sign_in(scope=scope)
        assert "workspace" in tokens["scope"].split()
        assert flow.mcp(tokens["access_token"]).status_code == 200


def test_a_refresh_may_not_drop_the_services_scope(flow):
    """Covers: R-6.11"""
    refresh_token = flow.sign_in()["refresh_token"]
    narrowed = flow.refresh(refresh_token, scope="offline_access")
    assert narrowed.status_code == 400
    assert narrowed.json()["error"] == "invalid_scope"
    kept = flow.refresh(refresh_token, scope="workspace offline_access")
    assert kept.status_code == 200
    assert flow.mcp(kept.json()["access_token"]).status_code == 200


def test_sign_in_serves_the_one_configured_user(make_flow):
    """Covers: R-6.7"""
    two = CONFIG + '\n[users.guest]\nread = ["claude-config"]\n'
    with pytest.raises(ConfigError, match="one user"):
        make_flow(text=two)


@pytest.mark.parametrize("public_url", [PUBLIC])
def test_a_trial_return_address_is_refused_for_a_public_service(make_flow, public_url):
    """Covers: R-5.8"""
    with pytest.raises(ConfigError, match="trial"):
        make_flow(public_url=public_url, trial=app.Trial(return_address=INSPECTOR))


@pytest.mark.parametrize(
    "address",
    [
        "http://attacker.example/oauth/callback",
        "https://localhost:6274/oauth/callback",
        "http://localhost/oauth/callback",
        "http://localhost:6274/oauth/callback?x=1",
        "http://localhost:6274/oauth/callback#f",
        "http://user@localhost:6274/oauth/callback",
    ],
)
def test_a_trial_return_address_must_be_one_exact_local_address(make_flow, address):
    """Covers: R-5.8"""
    with pytest.raises(ConfigError, match="trial"):
        make_flow(trial=app.Trial(return_address=address))


def test_a_local_trial_signs_in_to_its_own_address_only(make_flow):
    """Covers: R-5.8"""
    trial = make_flow(trial=app.Trial(return_address=INSPECTOR))
    verifier, challenge = pkce()
    redirect = trial.service.provider.approve(trial.request_id(trial.authorize(challenge)))
    assert redirect.split("?")[0] == INSPECTOR
    assert trial.exchange(query(redirect)["code"], verifier).status_code == 200
    _not_found(trial, trial.authorize(challenge, redirect_uri=auth.CLAUDE_CALLBACK))


def test_a_public_service_takes_its_https_address_in_any_case_or_with_port_443(make_flow):
    """Covers: R-6.11"""
    provider = make_flow(public_url=PUBLIC).service.provider
    for resource in (PUBLIC + "/mcp", "https://MCP.example.test/mcp", "https://mcp.example.test:443/mcp",
                     PUBLIC + "/mcp/"):
        assert provider._resource(resource) == PUBLIC + "/mcp"
    for resource in ("http://mcp.example.test/mcp", "https://mcp.example.test:8443/mcp"):
        with pytest.raises(Exception, match="itself only"):
            provider._resource(resource)


def test_the_service_takes_its_address_in_any_case_or_with_the_default_port(make_flow):
    """Covers: R-6.11"""
    portless = make_flow(public_url="http://localhost")
    for resource in ("http://LOCALHOST/mcp", "http://localhost:80/mcp"):
        assert portless.mcp(portless.sign_in(resource=resource)["access_token"]).status_code == 200


def test_the_production_shape_publishes_its_https_address(make_flow):
    """Covers: R-6.8"""
    public = make_flow(public_url=PUBLIC)
    metadata = public.http.get("/.well-known/oauth-protected-resource/mcp").json()
    assert metadata["resource"] == PUBLIC + "/mcp"
    response = public.mcp()
    assert response.status_code == 401
    assert f'resource_metadata="{PUBLIC}/.well-known/oauth-protected-resource/mcp"' in (
        response.headers["www-authenticate"]
    )
