"""The OAuth provider: the client, codes and tokens."""

import re
import secrets
from dataclasses import dataclass, replace

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl, ValidationError

CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
LABEL = "the Claude app"
TRIAL_LABEL = "MCP Inspector (local trial)"
SCOPE = "workspace"
SCOPES = ("workspace", "offline_access")
CODE_SECONDS = 300
ACCESS_SECONDS = 3600
REFRESH_SECONDS = 30 * 24 * 3600
# An S256 challenge is the base64url of a SHA-256 digest: 43 characters, no padding.
CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")


class _ClientRecord(OAuthClientInformationFull):
    # The SDK compares the secret held here in clear; its repr must not print it.
    def __repr__(self):
        return "OAuthClientInformationFull(<hidden>)"

    __str__ = __repr__


@dataclass(frozen=True)
class Client:
    client_id: str
    secret: object
    return_address: str
    label: str

    def record(self):
        return _ClientRecord(
            client_id=self.client_id,
            client_secret=self.secret.reveal(),
            redirect_uris=[AnyUrl(self.return_address)],
            token_endpoint_auth_method="client_secret_post",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=" ".join(SCOPES),
        )


@dataclass(frozen=True)
class Pending:
    client_id: str
    redirect_uri: str
    explicit: bool
    challenge: str
    resource: str
    scopes: tuple[str, ...]
    state: str | None


@dataclass(frozen=True)
class Code:
    user: str
    client_id: str
    redirect_uri: str
    explicit: bool
    challenge: str
    resource: str
    scopes: tuple[str, ...]
    expires_at: float
    used: bool = False


@dataclass(frozen=True)
class Grant:
    user: str
    client_id: str
    scopes: tuple[str, ...]
    resource: str
    expires_at: float


class MemoryStore:
    # Reached only from the event loop, one call at a time; T1.7 replaces it.
    def __init__(self):
        self._pending = {}
        self._codes = {}
        self._access = {}
        self._refresh = {}

    def put_pending(self, key, pending):
        self._pending[key] = pending

    def take_pending(self, key):
        return self._pending.pop(key, None)

    def put_code(self, code, record):
        self._codes[code] = record

    def code(self, code):
        return self._codes.get(code)

    def use_code(self, code, now):
        record = self._codes.get(code)
        if record is None or record.used or record.expires_at <= now:
            return None
        self._codes[code] = replace(record, used=True)
        return record

    def put_tokens(self, access, access_grant, refresh, refresh_grant):
        self._access[access] = access_grant
        self._refresh[refresh] = refresh_grant

    def access(self, token):
        return self._access.get(token)

    def refresh(self, token):
        return self._refresh.get(token)


class Provider:
    """The SDK's authorization-server interface, for the one client and the one user."""

    def __init__(self, client, user, public, store, clock):
        self._client = client
        self._record = client.record()
        self._user = user
        self._public = public
        self._store = store
        self._clock = clock

    async def get_client(self, client_id):
        return self._record if client_id == self._client.client_id else None

    async def authorize(self, client, params):
        if not CHALLENGE.fullmatch(params.code_challenge):
            raise AuthorizeError("invalid_request", "The sign-in needs an S256 code challenge.")
        request_id = secrets.token_urlsafe(32)
        self._store.put_pending(
            request_id,
            Pending(
                client_id=client.client_id,
                redirect_uri=str(params.redirect_uri),
                explicit=params.redirect_uri_provided_explicitly,
                challenge=params.code_challenge,
                resource=self._resource(params.resource),
                scopes=_granted(params.scopes),
                state=params.state,
            ),
        )
        return f"{self._public.issuer}/login?request={request_id}"

    def approve(self, request_id):
        pending = self._store.take_pending(request_id)
        if pending is None:
            return None
        code = secrets.token_urlsafe(32)
        self._store.put_code(
            code,
            Code(
                user=self._user,
                client_id=pending.client_id,
                redirect_uri=pending.redirect_uri,
                explicit=pending.explicit,
                challenge=pending.challenge,
                resource=pending.resource,
                scopes=pending.scopes,
                expires_at=self._clock() + CODE_SECONDS,
            ),
        )
        return construct_redirect_uri(pending.redirect_uri, code=code, state=pending.state)

    async def load_authorization_code(self, client, authorization_code):
        # Whether the code may still be used is decided once, atomically, at the exchange.
        record = self._store.code(authorization_code)
        if record is None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=list(record.scopes),
            expires_at=record.expires_at,
            client_id=record.client_id,
            code_challenge=record.challenge,
            redirect_uri=AnyUrl(record.redirect_uri),
            redirect_uri_provided_explicitly=record.explicit,
            resource=record.resource,
            subject=record.user,
        )

    async def exchange_authorization_code(self, client, authorization_code):
        record = self._store.use_code(authorization_code.code, self._clock())
        if record is None:
            raise TokenError("invalid_grant", "The sign-in code is no longer valid.")
        return self._issue(record.user, record.client_id, record.scopes, record.resource)

    async def load_access_token(self, token):
        grant = self._store.access(token)
        if grant is None or grant.expires_at <= self._clock() or grant.user != self._user:
            return None
        # Also refused here, so R-6.11 does not rest on the SDK's setting alone.
        if grant.resource != self._public.resource:
            return None
        return AccessToken(
            token=token,
            client_id=grant.client_id,
            scopes=list(grant.scopes),
            expires_at=int(grant.expires_at),
            resource=grant.resource,
            subject=grant.user,
        )

    async def load_refresh_token(self, client, refresh_token):
        grant = self._store.refresh(refresh_token)
        if grant is None or grant.expires_at <= self._clock():
            return None
        if grant.client_id != client.client_id or grant.user != self._user:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=grant.client_id,
            scopes=list(grant.scopes),
            expires_at=int(grant.expires_at),
            resource=grant.resource,
            subject=grant.user,
        )

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        if SCOPE not in scopes:
            raise TokenError("invalid_scope", f"The {SCOPE} scope cannot be dropped.")
        return self._issue(refresh_token.subject, refresh_token.client_id, tuple(refresh_token.scopes),
                           refresh_token.resource)

    def _issue(self, user, client_id, scopes, resource):
        now = self._clock()
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self._store.put_tokens(
            access,
            Grant(user, client_id, scopes, resource, now + ACCESS_SECONDS),
            refresh,
            Grant(user, client_id, scopes, resource, now + REFRESH_SECONDS),
        )
        return OAuthToken(
            access_token=access,
            expires_in=ACCESS_SECONDS,
            scope=" ".join(scopes),
            refresh_token=refresh,
        )

    def _resource(self, requested):
        # Claude may leave the resource out; the SDK then refuses a token with
        # none, so the service's own address is put on it.
        own = self._public.resource
        if requested is None:
            return own
        try:
            given = str(AnyHttpUrl(requested))
        except ValidationError:
            given = None
        # Compared as the SDK's token check compares: as URLs, a trailing slash aside.
        if given is not None and given.removesuffix("/") == own:
            return own
        raise AuthorizeError("invalid_target", "This service issues tokens for itself only.")


def _granted(requested):
    asked = set(SCOPES) if requested is None else set(requested)
    return tuple(scope for scope in SCOPES if scope == SCOPE or scope in asked)
