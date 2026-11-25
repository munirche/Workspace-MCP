"""Assembles the service: the SDK's app and the layers in front of it."""

import asyncio
import base64
import contextlib
import binascii
import hmac
import json
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote_plus, urlencode, urlsplit

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE, TransportSecuritySettings
from starlette.responses import PlainTextResponse, Response

from workspace_mcp import auth, login
from workspace_mcp.config import ConfigError

TRIAL_RETURN = re.compile(r"http://(localhost|127\.0\.0\.1):[0-9]{1,5}/[A-Za-z0-9._~/-]*")
FORM = b"application/x-www-form-urlencoded"
BODY_LIMIT = DEFAULT_MAX_REQUEST_BODY_SIZE


@dataclass(frozen=True)
class Trial:
    """What only a local trial may change; refused unless the service is on loopback."""

    return_address: str | None = None
    flag_path: str | None = None
    owner: int | None = None


@dataclass(frozen=True)
class Service:
    app: object
    provider: auth.Provider
    server: MCPServer
    window: login.Window


def build(config, *, store, alerts, clock=time.time, monotonic=time.monotonic, trial=None):
    if len(config.users) != 1:
        raise ConfigError(f"sign-in serves one user; the configuration names {len(config.users)}")
    (user,) = config.users
    if trial is not None or store.generation_path != auth.GENERATION_PATH:
        _check_trial(config, trial or Trial())
    trial = trial or Trial()
    passphrase_hash = login.check_hash(config.secrets.passphrase_hash)
    if trial.return_address is not None:
        return_address, label = trial.return_address, auth.TRIAL_LABEL
    else:
        return_address, label = auth.CLAUDE_CALLBACK, auth.LABEL
    client = auth.Client(config.secrets.client_id.reveal(), config.secrets.client_secret, return_address, label)
    window = login.Window(
        store, alerts, clock, monotonic,
        flag_path=trial.flag_path or login.FLAG_PATH,
        owner=0 if trial.owner is None else trial.owner,
    )
    provider = auth.Provider(client, user, config.public, store, alerts, clock, window)
    page = login.Page(provider, store, window, client, config.public.issuer, passphrase_hash, alerts, clock)
    server = MCPServer(
        "Workspace-MCP",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=config.public.issuer,
            resource_server_url=config.public.resource,
            validate_token_resource=True,
            required_scopes=[auth.SCOPE],
            client_registration_options=ClientRegistrationOptions(enabled=False, valid_scopes=list(auth.SCOPES)),
            revocation_options=RevocationOptions(enabled=False),
        ),
    )
    inner = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[config.public.allowed_host],
            allowed_origins=[],
        ),
    )
    app = CallerAddress(WindowWatch(TokenForm(SignInGate(inner, client, window, page), client), window))
    return Service(app=app, provider=provider, server=server, window=window)


def _check_trial(config, trial):
    address = urlsplit(config.public.issuer)
    if address.scheme != "http" or address.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ConfigError("trial settings are refused unless the service runs on the local machine")
    if trial.return_address is not None and not TRIAL_RETURN.fullmatch(trial.return_address):
        raise ConfigError("the trial return address must be one exact http://localhost:<port>/<path> address")


class CallerAddress:
    """Takes the caller's address from nginx's one X-Forwarded-For value, for alerts and records only."""

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self._app(scope, receive, send)
        values = [value for name, value in scope["headers"] if name == b"x-forwarded-for"]
        address = values[0].decode("latin-1").strip() if len(values) == 1 and b"," not in values[0] else None
        token = auth.CALLER.set(address)
        try:
            await self._app(scope, receive, send)
        finally:
            auth.CALLER.reset(token)


def _not_found():
    return PlainTextResponse("Not Found", status_code=404)


class WindowWatch:
    """Watches the sign-in window for as long as the service runs."""

    def __init__(self, app, window):
        self._app = app
        self._window = window

    async def __call__(self, scope, receive, send):
        if scope["type"] != "lifespan":
            return await self._app(scope, receive, send)
        task = asyncio.create_task(login.watch(self._window, logging.getLogger("workspace_mcp")))
        try:
            await self._app(scope, receive, send)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


class SignInGate:
    """Answers the sign-in paths as an unknown path unless the window is open and the request is the client's."""

    # The SDK's own refusal of another client or return address echoes the value.

    def __init__(self, app, client, window, page):
        self._app = app
        self._client = client
        self._window = window
        self._page = page

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] in ("/authorize", "/authorize/", "/login", "/login/"):
            if not self._acceptable(scope) or self._window.current() is None:
                return await _not_found()(scope, receive, send)
            if scope["path"] == "/login":
                return await self._page(scope, receive, send)
        await self._app(scope, receive, send)

    def _acceptable(self, scope):
        if scope["path"] == "/login":
            return scope["method"] in ("GET", "POST")
        if scope["path"] != "/authorize" or scope["method"] != "GET":
            return False
        # Parsed as Starlette parses the query the SDK then reads.
        pairs = parse_qsl(scope["query_string"].decode("latin-1"), keep_blank_values=True)
        ids = [value for key, value in pairs if key == "client_id"]
        addresses = [value for key, value in pairs if key == "redirect_uri"]
        if ids != [self._client.client_id] or len(addresses) > 1:
            return False
        return not addresses or addresses[0] == self._client.return_address


class TokenForm:
    """Moves a Basic header's client id and secret into the form, the only place the SDK reads."""

    def __init__(self, app, client):
        self._app = app
        self._client = client

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/token" or scope["method"] != "POST":
            return await self._app(scope, receive, send)
        headers = scope["headers"]
        basic = [value for name, value in headers if name == b"authorization" and value[:6].lower() == b"basic "]
        if not basic:
            return await self._app(scope, receive, send)
        body = await login.read_body(headers, receive, BODY_LIMIT)
        if body is None:
            return await Response("Request body too large", status_code=413)(scope, receive, send)
        form = _merged(basic, headers, body, self._client)
        if form is None:
            return await _unauthenticated()(scope, receive, send)
        rewritten = [(name, value) for name, value in headers if name not in (b"authorization", b"content-length")]
        rewritten.append((b"content-length", str(len(form)).encode()))
        await self._app(dict(scope, headers=rewritten), _replay(form, receive), send)


def _merged(basic, headers, body, client):
    if len(basic) != 1:
        return None
    types = [value for name, value in headers if name == b"content-type"]
    if len(types) != 1 or types[0].split(b";")[0].strip().lower() != FORM:
        return None
    try:
        decoded = base64.b64decode(basic[0][6:].strip(), validate=True).decode("utf-8")
        pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, strict_parsing=bool(body))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if ":" not in decoded:
        return None
    raw_id, raw_secret = decoded.split(":", 1)
    client_id = _decoded(raw_id, client.client_id)
    secret = _decoded(raw_secret, client.secret.reveal())
    if client_id is None or secret is None:
        return None
    for key, given in (("client_id", client_id), ("client_secret", secret)):
        for value in (value for name, value in pairs if name == key):
            if not hmac.compare_digest(value.encode(), given.encode()):
                return None
    kept = [(name, value) for name, value in pairs if name not in ("client_id", "client_secret")]
    return urlencode(kept + [("client_id", client_id), ("client_secret", secret)]).encode()


def _decoded(raw, expected):
    # RFC 6749 form-encodes each part before Basic, but many clients (the
    # TypeScript SDK among them) send it raw; a secret holding "+" or "%"
    # differs between the two, so either may match.
    matched = None
    for candidate in (raw, unquote_plus(raw)):
        if hmac.compare_digest(candidate.encode(), expected.encode()):
            matched = candidate
    return matched


def _replay(body, receive):
    sent = False

    async def replayed():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replayed


def _unauthenticated():
    return Response(
        json.dumps({"error": "invalid_client", "error_description": "The client could not be authenticated."}),
        status_code=401,
        media_type="application/json",
        # RFC 6749 §5.2: a client that tried the header is answered in its scheme.
        headers={"Cache-Control": "no-store", "Pragma": "no-cache", "WWW-Authenticate": "Basic"},
    )
