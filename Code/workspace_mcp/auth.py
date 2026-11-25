"""The OAuth provider: the client, codes and tokens, and the store that keeps them."""

import contextlib
import hashlib
import os
import re
import secrets
import sqlite3
import stat
from contextvars import ContextVar
from dataclasses import dataclass

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

from workspace_mcp.alerts import Event

CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
LABEL = "the Claude app"
TRIAL_LABEL = "MCP Inspector (local trial)"
SCOPE = "workspace"
SCOPES = ("workspace", "offline_access")
CODE_SECONDS = 300
ACCESS_SECONDS = 3600
REFRESH_SECONDS = 30 * 24 * 3600
GRACE_SECONDS = 60
# An S256 challenge is the base64url of a SHA-256 digest: 43 characters, no padding.
CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
# Root-owned; the cancel-all command adds one to it, and only what was issued
# under the current number is accepted.
GENERATION_PATH = "/etc/workspace-mcp/generation"
GENERATION = re.compile(rb"[0-9]{1,18}\n")
# The caller's address for the reuse alert, set per request by app.CallerAddress.
CALLER = ContextVar("workspace_mcp_caller", default=None)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending (
    hash TEXT PRIMARY KEY, client TEXT NOT NULL, redirect_uri TEXT NOT NULL, explicit INTEGER NOT NULL,
    challenge TEXT NOT NULL, resource TEXT NOT NULL, scopes TEXT NOT NULL, state TEXT,
    generation INTEGER NOT NULL, window TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS windows (
    nonce TEXT PRIMARY KEY, generation INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    closed_at REAL, reason TEXT, alerted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS codes (
    hash TEXT PRIMARY KEY, user TEXT NOT NULL, client TEXT NOT NULL, redirect_uri TEXT NOT NULL,
    explicit INTEGER NOT NULL, challenge TEXT NOT NULL, resource TEXT NOT NULL, scopes TEXT NOT NULL,
    generation INTEGER NOT NULL, expires_at REAL NOT NULL, used_at REAL);
CREATE TABLE IF NOT EXISTS chains (
    id INTEGER PRIMARY KEY, user TEXT NOT NULL, client TEXT NOT NULL, resource TEXT NOT NULL,
    scopes TEXT NOT NULL, generation INTEGER NOT NULL, cancelled_at REAL);
CREATE TABLE IF NOT EXISTS access_tokens (
    hash TEXT PRIMARY KEY, chain INTEGER NOT NULL REFERENCES chains (id), expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS highest (generation INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    hash TEXT PRIMARY KEY, chain INTEGER NOT NULL REFERENCES chains (id), expires_at REAL NOT NULL,
    retired_at REAL);
"""


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
    generation: int
    window: str


@dataclass(frozen=True)
class SignInWindow:
    generation: int
    attempts: int
    closed: bool


@dataclass(frozen=True)
class Code:
    user: str
    client_id: str
    redirect_uri: str
    explicit: bool
    challenge: str
    resource: str
    scopes: tuple[str, ...]
    generation: int
    expires_at: float


@dataclass(frozen=True)
class Chain:
    """One sign-in: every token refreshed from it belongs to it."""

    user: str
    client_id: str
    scopes: tuple[str, ...]
    resource: str
    generation: int


@dataclass(frozen=True)
class Grant:
    chain: Chain
    cancelled: bool
    expires_at: float
    retired_at: float | None = None


def _digest(value):
    # The values are 256-bit random strings, so a plain hash is enough to make
    # a copy of the database useless; a slow hash would only slow each request.
    return hashlib.sha256(value.encode()).hexdigest()


class SqliteStore:
    def __init__(self, path, generation_path=None):
        self.path = str(path)
        self.generation_path = generation_path or GENERATION_PATH
        # Owner-only before SQLite opens it, whatever the umask; never through a link.
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        with contextlib.closing(self._connect()) as db:
            db.executescript(SCHEMA)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10, isolation_level=None)

    @contextlib.contextmanager
    def _transaction(self):
        with contextlib.closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")

    def _one(self, query, *arguments):
        with contextlib.closing(self._connect()) as db:
            return db.execute(query, arguments).fetchone()

    def generation(self):
        number = self._generation_file()
        if number is None:
            return None
        # A number lower than one already seen (a restored or recreated file)
        # would revive what a cancel-all ended, so it refuses everything.
        row = self._one("SELECT max(generation) FROM highest")
        highest = row[0] if row else None
        if highest is not None and number < highest:
            return None
        if highest is None or number > highest:
            with self._transaction() as db:
                db.execute("INSERT INTO highest VALUES (?)", (number,))
        return number

    def _generation_file(self):
        try:
            # Non-blocking, so a pipe put in its place is refused rather than waited on.
            descriptor = os.open(self.generation_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return None
            text = os.read(descriptor, 32)
        finally:
            os.close(descriptor)
        return int(text) if GENERATION.fullmatch(text) else None

    def put_pending(self, request_id, pending):
        with self._transaction() as db:
            # A sign-in started in a new window ends every one an earlier window started.
            db.execute("DELETE FROM pending WHERE window != ?", (pending.window,))
            db.execute(
                "INSERT INTO pending VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_digest(request_id), pending.client_id, pending.redirect_uri, pending.explicit, pending.challenge,
                 pending.resource, " ".join(pending.scopes), pending.state, pending.generation, pending.window),
            )

    def pending(self, request_id):
        return _pending(self._one(f"SELECT {PENDING} FROM pending WHERE hash = ?", _digest(request_id)))

    def take_pending(self, request_id):
        with self._transaction() as db:
            row = db.execute(f"SELECT {PENDING} FROM pending WHERE hash = ?", (_digest(request_id),)).fetchone()
            db.execute("DELETE FROM pending WHERE hash = ?", (_digest(request_id),))
        return _pending(row)

    def see_window(self, nonce, generation):
        """The window's row, made at first sight with the number current then."""
        if self.window(nonce) is None:
            with self._transaction() as db:
                db.execute("INSERT OR IGNORE INTO windows (nonce, generation) VALUES (?, ?)", (nonce, generation))
        return self.window(nonce)

    def window(self, nonce):
        row = self._one("SELECT generation, attempts, closed_at FROM windows WHERE nonce = ?", nonce)
        return None if row is None else SignInWindow(row[0], row[1], row[2] is not None)

    def reserve_attempt(self, nonce, limit):
        with self._transaction() as db:
            cursor = db.execute(
                "UPDATE windows SET attempts = attempts + 1 WHERE nonce = ? AND closed_at IS NULL AND attempts < ?",
                (nonce, limit),
            )
            return cursor.rowcount == 1

    def close_window(self, nonce, reason, now):
        """Closes the window; True only for the one call that closed it."""
        with self._transaction() as db:
            cursor = db.execute(
                "UPDATE windows SET closed_at = ?, reason = ? WHERE nonce = ? AND closed_at IS NULL",
                (now, reason, nonce),
            )
            return cursor.rowcount == 1

    def mark_alerted(self, nonce):
        with self._transaction() as db:
            return db.execute("UPDATE windows SET alerted = 1 WHERE nonce = ? AND alerted = 0", (nonce,)).rowcount == 1

    def put_code(self, code, record):
        with self._transaction() as db:
            db.execute(
                "INSERT INTO codes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (_digest(code), record.user, record.client_id, record.redirect_uri, record.explicit,
                 record.challenge, record.resource, " ".join(record.scopes), record.generation, record.expires_at),
            )

    def code(self, code):
        row = self._one(
            "SELECT user, client, redirect_uri, explicit, challenge, resource, scopes, generation, expires_at"
            " FROM codes WHERE hash = ?",
            _digest(code),
        )
        return None if row is None else _code(row)

    def use_code(self, code, now):
        with self._transaction() as db:
            row = db.execute(
                "SELECT user, client, redirect_uri, explicit, challenge, resource, scopes, generation, expires_at"
                " FROM codes WHERE hash = ? AND used_at IS NULL AND expires_at > ?",
                (_digest(code), now),
            ).fetchone()
            if row is None:
                return None
            db.execute("UPDATE codes SET used_at = ? WHERE hash = ?", (now, _digest(code)))
        return _code(row)

    def start_chain(self, chain, now, access, refresh):
        with self._transaction() as db:
            cursor = db.execute(
                "INSERT INTO chains (user, client, resource, scopes, generation) VALUES (?, ?, ?, ?, ?)",
                (chain.user, chain.client_id, chain.resource, " ".join(chain.scopes), chain.generation),
            )
            _add_pair(db, cursor.lastrowid, now, access, refresh)

    def access(self, token):
        row = self._one(
            "SELECT c.user, c.client, c.scopes, c.resource, c.generation, c.cancelled_at, a.expires_at"
            " FROM access_tokens a JOIN chains c ON c.id = a.chain WHERE a.hash = ?",
            _digest(token),
        )
        return None if row is None else _grant(row)

    def refresh(self, token):
        row = self._one(
            "SELECT c.user, c.client, c.scopes, c.resource, c.generation, c.cancelled_at, r.expires_at,"
            " r.retired_at FROM refresh_tokens r JOIN chains c ON c.id = r.chain WHERE r.hash = ?",
            _digest(token),
        )
        return None if row is None else _grant(row)

    def rotate(self, token, now, generation, access, refresh, issue=True):
        """Replaces a refresh token in one transaction: "issued", "refused", "reused" (cancelled) or "dead"."""
        with self._transaction() as db:
            row = db.execute(
                "SELECT r.chain, r.expires_at, r.retired_at, c.generation, c.cancelled_at"
                " FROM refresh_tokens r JOIN chains c ON c.id = r.chain WHERE r.hash = ?",
                (_digest(token),),
            ).fetchone()
            if row is None:
                return "dead"
            chain, expires_at, retired_at, chain_generation, cancelled_at = row
            if cancelled_at is not None or chain_generation != generation or expires_at <= now:
                return "dead"
            if retired_at is not None and now < retired_at:
                return "dead"
            if retired_at is not None and now - retired_at > GRACE_SECONDS:
                db.execute("UPDATE chains SET cancelled_at = ? WHERE id = ?", (now, chain))
                return "reused"
            if not issue:
                return "refused"
            if retired_at is None:
                db.execute(
                    "UPDATE refresh_tokens SET retired_at = ? WHERE chain = ? AND retired_at IS NULL", (now, chain)
                )
            _add_pair(db, chain, now, access, refresh)
            return "issued"


def _add_pair(db, chain, now, access, refresh):
    db.execute("INSERT INTO access_tokens VALUES (?, ?, ?)", (_digest(access), chain, now + ACCESS_SECONDS))
    db.execute(
        "INSERT INTO refresh_tokens VALUES (?, ?, ?, NULL)", (_digest(refresh), chain, now + REFRESH_SECONDS)
    )


PENDING = "client, redirect_uri, explicit, challenge, resource, scopes, state, generation, window"


def _pending(row):
    if row is None:
        return None
    client, redirect_uri, explicit, challenge, resource, scopes, state, generation, window = row
    return Pending(client, redirect_uri, bool(explicit), challenge, resource, tuple(scopes.split()), state,
                   generation, window)


def _code(row):
    user, client, redirect_uri, explicit, challenge, resource, scopes, generation, expires_at = row
    return Code(user, client, redirect_uri, bool(explicit), challenge, resource, tuple(scopes.split()), generation,
                expires_at)


def _grant(row):
    user, client, scopes, resource, generation, cancelled_at, expires_at, *retired = row
    chain = Chain(user, client, tuple(scopes.split()), resource, generation)
    return Grant(chain, cancelled_at is not None, expires_at, retired[0] if retired else None)


class Provider:
    """The SDK's authorization-server interface, for the one client and the one user."""

    def __init__(self, client, user, public, store, alerts, clock, window):
        self._window = window
        self._client = client
        self._record = client.record()
        self._user = user
        self._public = public
        self._store = store
        self._alerts = alerts
        self._clock = clock

    async def get_client(self, client_id):
        return self._record if client_id == self._client.client_id else None

    async def authorize(self, client, params):
        if not CHALLENGE.fullmatch(params.code_challenge):
            raise AuthorizeError("invalid_request", "The sign-in needs an S256 code challenge.")
        generation = self._store.generation()
        window = self._window.current()
        if generation is None or window is None:
            raise AuthorizeError("temporarily_unavailable", "Sign-in is unavailable.")
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
                generation=generation,
                window=window,
            ),
        )
        return f"{self._public.issuer}/login?request={request_id}"

    def approve(self, request_id):
        pending = self._store.take_pending(request_id)
        if pending is None or pending.generation != self._store.generation():
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
                generation=pending.generation,
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
        generation = self._store.generation()
        if record is None or generation is None or record.generation != generation:
            raise TokenError("invalid_grant", "The sign-in code is no longer valid.")
        chain = Chain(record.user, record.client_id, record.scopes, record.resource, generation)
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self._store.start_chain(chain, self._clock(), access, refresh)
        return _token(access, refresh, chain.scopes)

    async def load_access_token(self, token):
        grant = self._store.access(token)
        if not self._live(grant) or grant.expires_at <= self._clock():
            return None
        # Also refused here, so R-6.11 does not rest on the SDK's setting alone.
        if grant.chain.resource != self._public.resource:
            return None
        return AccessToken(
            token=token,
            client_id=grant.chain.client_id,
            scopes=list(grant.chain.scopes),
            expires_at=int(grant.expires_at),
            resource=grant.chain.resource,
            subject=grant.chain.user,
        )

    async def load_refresh_token(self, client, refresh_token):
        # A retired, expired or cancelled token is still loaded: its exchange
        # decides, atomically, whether it is dead, in its grace period, or
        # reused past it.
        grant = self._store.refresh(refresh_token)
        if grant is None or grant.chain.user != self._user or grant.chain.client_id != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=grant.chain.client_id,
            scopes=list(grant.chain.scopes),
            expires_at=int(grant.expires_at),
            resource=grant.chain.resource,
            subject=grant.chain.user,
        )

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        generation = self._store.generation()
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        # A request that drops the scope is still judged for reuse first.
        outcome = "dead" if generation is None else self._store.rotate(
            refresh_token.token, self._clock(), generation, access, refresh, issue=SCOPE in scopes
        )
        if outcome == "reused":
            self._alerts.send_later(Event.REFRESH_TOKEN_REUSED, CALLER.get())
        if outcome == "refused":
            raise TokenError("invalid_scope", f"The {SCOPE} scope cannot be dropped.")
        if outcome != "issued":
            raise TokenError("invalid_grant", "The refresh token is no longer valid.")
        return _token(access, refresh, tuple(refresh_token.scopes))

    def _live(self, grant):
        return (
            grant is not None
            and not grant.cancelled
            and grant.chain.user == self._user
            and grant.chain.generation == self._store.generation()
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


def _token(access, refresh, scopes):
    return OAuthToken(access_token=access, expires_in=ACCESS_SECONDS, scope=" ".join(scopes), refresh_token=refresh)


def _granted(requested):
    asked = set(SCOPES) if requested is None else set(requested)
    return tuple(scope for scope in SCOPES if scope == SCOPE or scope in asked)
