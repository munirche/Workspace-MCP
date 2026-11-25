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

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE, TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from starlette.responses import PlainTextResponse, Response

from workspace_mcp import auth, login, repo_tools
from workspace_mcp.alerts import quiet_sdk_logs, record_tool_call
from workspace_mcp.config import NAME, ConfigError
from workspace_mcp.paths import RepoMissing

TRIAL_RETURN = re.compile(r"http://(localhost|127\.0\.0\.1):[0-9]{1,5}/[A-Za-z0-9._~/-]*")
TRIAL_ORIGIN = re.compile(r"http://(localhost|127\.0\.0\.1):[0-9]{1,5}")
FORM = b"application/x-www-form-urlencoded"
BODY_LIMIT = DEFAULT_MAX_REQUEST_BODY_SIZE
LOGGER = logging.getLogger("workspace_mcp")
CUT_NOTE = " [Cut short to fit; ask for less.]"
ECHOED_NAME_CHARS = 64


@dataclass(frozen=True)
class Trial:
    """What only a local trial may change; refused unless the service is on loopback."""

    return_address: str | None = None
    flag_path: str | None = None
    owner: int | None = None
    # MCP Inspector in a browser: its one origin, allowed on /mcp and /token.
    origin: str | None = None


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
        middleware=[plain_sdk_refusals],
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
            allowed_origins=[trial.origin] if trial.origin else [],
        ),
    )
    _add_tools(server, config)
    app = CallerAddress(WindowWatch(TokenForm(SignInGate(inner, client, window, page), client), window))
    if trial.origin:
        app = TrialCors(app, trial.origin)
    app = Shield(HostGate(app, config.public.allowed_host))
    return Service(app=app, provider=provider, server=server, window=window)


class NoTracebacks(logging.Filter):
    """Keeps an exception's class and drops its text and traceback, for every logger."""

    def filter(self, record):
        if record.exc_info or record.exc_text or record.stack_info:
            kind = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else "an exception"
            record.msg, record.args = f"{record.getMessage()} ({kind})", None
            record.exc_info = record.exc_text = record.stack_info = None
        return True


def configure_logging(stream=None):
    """The service's one log handler, set up before the SDK is built, with the SDK's records cut."""
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    quiet_sdk_logs(handler)
    # After the SDK's own filter, so its records are cut first; then anything
    # else's traceback (uvicorn's lifespan, asyncio's lost tasks) loses its text.
    handler.addFilter(NoTracebacks())
    return handler


def _add_tools(server, config):
    @server.tool(
        name="list_repos",
        title="List repos",
        description="Lists the repos you can read, and which of them you can also write to.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def list_repos() -> CallToolResult:
        return _answer(config, "list_repos", None, None, lambda user: repo_tools.list_repos_text(config, user))


def _answer(config, tool, repo, path, produce):
    # Every refusal or failure is one plain sentence (R-7.4): the SDK hides a
    # plain exception's message and logs its traceback.
    try:
        token = get_access_token()
        user = token.subject if token is not None else None
        record_tool_call(LOGGER, user, tool, repo, path, auth.CALLER.get())
        result = CallToolResult(content=[TextContent(type="text", text=produce(user))])
    except RepoMissing as refusal:
        result = CallToolResult(content=[TextContent(type="text", text=str(refusal))], is_error=True)
    except Exception as error:
        LOGGER.warning("tool %s failed (%s)", tool, type(error).__name__)
        sentence = f"The {tool} tool could not answer just now; try again later."
        result = CallToolResult(content=[TextContent(type="text", text=sentence)], is_error=True)
    return fit(result, config.limits.result_chars)


def fit(result, limit):
    """The result cut to the limit, counting text and any structured content, and saying so when cut."""
    structured = len(json.dumps(result.structured_content)) if result.structured_content is not None else 0
    texts = [item.text for item in result.content]
    if structured + sum(len(text) for text in texts) <= limit:
        return result
    room = max(0, limit - len(CUT_NOTE))
    kept = []
    for text in texts:
        kept.append(text[:room])
        room -= len(kept[-1])
    kept[-1] = (kept[-1] + CUT_NOTE)[:limit]
    return CallToolResult(content=[TextContent(type="text", text=text) for text in kept], is_error=result.is_error)


async def plain_sdk_refusals(ctx, call_next):
    """Rewrites the SDK's own unknown-tool and bad-argument answers, which echo the caller's text."""
    result = await call_next(ctx)
    if ctx.method != "tools/call" or not isinstance(result, dict) or result.get("isError") is not True:
        return result
    content = result.get("content") or []
    text = content[0].get("text", "") if content and isinstance(content[0], dict) else ""
    name = ctx.params.get("name") if isinstance(ctx.params, dict) else None
    shown = name if isinstance(name, str) and len(name) <= ECHOED_NAME_CHARS and NAME.fullmatch(name) else None
    if text.startswith("Unknown tool:"):
        sentence = (f"There is no tool called '{shown}'" if shown else "There is no such tool") + (
            "; the tool list shows the ones this service has.")
    elif text.startswith(f"Error executing tool {name}: "):
        named = f"for '{shown}' " if shown else ""
        sentence = f"The arguments {named}were not valid; check them against the tool's description."
    elif text.startswith("Error executing tool "):
        # The bare form is the SDK's answer to a tool that raised, not to bad arguments.
        sentence = f"The {shown or 'requested'} tool could not answer just now; try again later."
    else:
        return result
    return {**result, "content": [{"type": "text", "text": sentence}]}


def _check_trial(config, trial):
    address = urlsplit(config.public.issuer)
    if address.scheme != "http" or address.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ConfigError("trial settings are refused unless the service runs on the local machine")
    if trial.return_address is not None and not TRIAL_RETURN.fullmatch(trial.return_address):
        raise ConfigError("the trial return address must be one exact http://localhost:<port>/<path> address")
    if trial.origin is not None and not TRIAL_ORIGIN.fullmatch(trial.origin):
        raise ConfigError("the trial origin must be one exact http://localhost:<port> origin")


class TrialCors:
    """Answers MCP Inspector's browser preflights and marks its responses readable, for its one origin."""

    # The SDK's own /token preflight allows no Authorization header, which a
    # browser client sending its secret by Basic needs.
    ALLOW_HEADERS = b"authorization, content-type, mcp-protocol-version, mcp-session-id, mcp-method, mcp-name"
    EXPOSE_HEADERS = b"www-authenticate, mcp-session-id, mcp-protocol-version"

    def __init__(self, app, origin):
        self._app = app
        self._origin = origin.encode()

    async def __call__(self, scope, receive, send):
        origins = [value for name, value in scope.get("headers", []) if name == b"origin"]
        if scope["type"] != "http" or origins != [self._origin] or scope["path"] not in ("/mcp", "/token"):
            return await self._app(scope, receive, send)
        if scope["method"] == "OPTIONS":
            headers = {"Access-Control-Allow-Origin": self._origin.decode(),
                       "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
                       "Access-Control-Allow-Headers": self.ALLOW_HEADERS.decode(),
                       "Access-Control-Max-Age": "600"}
            return await Response(status_code=204, headers=headers)(scope, receive, send)

        async def readable(message):
            if message["type"] == "http.response.start":
                kept = [(name, value) for name, value in message.get("headers", [])
                        if name.lower() not in (b"access-control-allow-origin", b"access-control-expose-headers")]
                kept += [(b"access-control-allow-origin", self._origin),
                         (b"access-control-expose-headers", self.EXPOSE_HEADERS)]
                message = dict(message, headers=kept)
            await send(message)

        await self._app(scope, receive, readable)


class Shield:
    """Turns any exception into a plain 500, recorded by its class alone, so no traceback reaches a log."""

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self._app(scope, receive, send)
        started = False

        async def tracked(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self._app(scope, receive, tracked)
        except Exception as error:
            LOGGER.warning("request failed (%s)", type(error).__name__)
            if not started:
                await PlainTextResponse("Internal Server Error", status_code=500)(scope, receive, send)


class HostGate:
    """Refuses any request whose one Host header is not the service's own name (R-7.9), on every route."""

    def __init__(self, app, allowed_host):
        self._app = app
        self._allowed = [allowed_host.encode()]

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self._app(scope, receive, send)
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        # X-Forwarded-Host is never read: nginx sets Host to its own server name.
        if [value for name, value in scope["headers"] if name == b"host"] != self._allowed:
            return await PlainTextResponse("Misdirected Request", status_code=421)(scope, receive, send)
        await self._app(scope, receive, send)


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
