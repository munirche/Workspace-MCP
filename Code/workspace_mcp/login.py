"""The sign-in window, the sign-in page and the passphrase."""

import asyncio
import base64
import hashlib
import hmac
import html
import os
import re
import stat
import threading
from urllib.parse import parse_qsl

from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from workspace_mcp import auth
from workspace_mcp.alerts import FROM_SERVER, Event
from workspace_mcp.config import ENVIRONMENT, ConfigError

# Written by the root-owned `workspace-mcp-signin open`: a random nonce and the
# time it was opened. The service decides the ten minutes itself.
FLAG_PATH = "/run/workspace-mcp-signin/open"
FLAG = re.compile(rb"([0-9a-f]{64}) ([0-9]{1,12})\n")
WINDOW_SECONDS = 600
WRONG_LIMIT = 5
POLL_SECONDS = 2
BODY_LIMIT = 8192
REQUEST_CHARS = 128
# The one scrypt setting accepted: OWASP's floor as read 2026-11-24 (128 MiB a check).
PASSPHRASE_PARAMS = ((2**17, 8, 1),)
MAXMEM = 256 * 1024 * 1024
HASH_BYTES = (32, 64)
SHORTEST_PASSPHRASE = 20
FORM = b"application/x-www-form-urlencoded"
HEADERS = {
    "Cache-Control": "no-store",
    # Not no-referrer: that makes the browser post "Origin: null", which the page refuses.
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
}
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in to Workspace-MCP</title>
<style>body{{font-family:system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem;line-height:1.5}}
input{{font-size:1rem;width:100%;padding:.5rem;margin:.5rem 0;box-sizing:border-box}}</style></head>
<body><h1>Sign in to Workspace-MCP</h1>
<p><strong>{label}</strong> is asking for access. After you sign in, your browser goes to <code>{address}</code>.</p>
{message}{form}</body></html>
"""
FORM_HTML = (
    '<form method="post" action="/login"><input type="hidden" name="request" value="{request}">'
    '<label>Passphrase <input type="password" name="passphrase" autocomplete="current-password" required autofocus>'
    '</label><input type="submit" value="Sign in"></form>'
)


def check_hash(secret):
    """The stored hash, parsed; refused at start-up unless it uses the accepted parameters."""
    name = ENVIRONMENT["passphrase_hash"]
    try:
        _, n, r, p, salt, digest = secret.reveal().split("$")
        params, salt, digest = (int(n), int(r), int(p)), base64.b64decode(salt), base64.b64decode(digest)
    except ValueError:
        raise ConfigError(f"{name} is not scrypt$n$r$p$salt$hash") from None
    if params not in PASSPHRASE_PARAMS:
        raise ConfigError(f"{name} uses scrypt parameters other than the accepted ones")
    if not HASH_BYTES[0] <= len(digest) <= HASH_BYTES[1]:
        raise ConfigError(f"{name} holds a hash of the wrong length")
    return params, salt, digest


def verify(parsed, given):
    (n, r, p), salt, digest = parsed
    derived = hashlib.scrypt(given.encode(), salt=salt, n=n, r=r, p=p, maxmem=MAXMEM, dklen=len(digest))
    return hmac.compare_digest(derived, digest)


def hash_passphrase(passphrase):
    if len(passphrase) < SHORTEST_PASSPHRASE:
        raise ValueError(f"the passphrase must be at least {SHORTEST_PASSPHRASE} characters")
    if passphrase != passphrase.strip():
        # Most likely a paste artefact, which would then fail at every sign-in.
        raise ValueError("the passphrase starts or ends with a space; nothing was printed")
    n, r, p = PASSPHRASE_PARAMS[0]
    salt = os.urandom(16)
    digest = hashlib.scrypt(passphrase.encode(), salt=salt, n=n, r=r, p=p, maxmem=MAXMEM, dklen=HASH_BYTES[1])
    encoded = (base64.b64encode(salt).decode(), base64.b64encode(digest).decode())
    return "$".join(["scrypt", str(n), str(r), str(p), *encoded])


class Window:
    """Whether the sign-in window is open: a fresh, root-owned flag not yet closed."""

    def __init__(self, store, alerts, clock, monotonic, flag_path=FLAG_PATH, owner=0):
        self._store = store
        self._alerts = alerts
        self._clock = clock
        self._monotonic = monotonic
        self._flag_path = flag_path
        self._owner = owner
        self._first_seen = {}

    def current(self):
        """The open window's nonce, or None."""
        flag = self._flag()
        if flag is None:
            return None
        nonce, stamp = flag
        if not 0 <= self._clock() - stamp < WINDOW_SECONDS:
            return None
        generation = self._store.generation()
        if generation is None:
            return None
        # Closings are kept in the store, since the service cannot remove
        # root's flag; a cancel-all since first sight closes it for good.
        seen = self._store.see_window(nonce, generation)
        if seen.closed or seen.generation != generation:
            return None
        # A flag stamped again keeps its first ten minutes; only the latest window is remembered.
        if nonce not in self._first_seen:
            self._first_seen = {nonce: self._monotonic()}
        first = self._first_seen[nonce]
        if self._monotonic() - first >= WINDOW_SECONDS:
            return None
        return nonce

    def poll(self):
        nonce = self.current()
        if nonce is not None and self._store.mark_alerted(nonce):
            self._alerts.send_later(Event.WINDOW_OPENED, FROM_SERVER)

    def _flag(self):
        try:
            descriptor = os.open(self._flag_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != self._owner or info.st_mode & 0o022:
                return None
            text = os.read(descriptor, 128)
        finally:
            os.close(descriptor)
        match = FLAG.fullmatch(text)
        return (match.group(1).decode(), int(match.group(2))) if match else None


async def watch(window, logger):
    """Checks the window every few seconds, so its opening is alerted at once."""
    while True:
        try:
            window.poll()
        except Exception as error:
            logger.warning("sign-in window check failed (%s)", type(error).__name__)
        await asyncio.sleep(POLL_SECONDS)


class Page:
    """The sign-in page: GET shows it, POST checks the passphrase."""

    def __init__(self, provider, store, window, client, issuer, passphrase_hash, alerts, clock):
        self._provider = provider
        self._store = store
        self._window = window
        self._client = client
        self._issuer = issuer
        self._hash = passphrase_hash
        self._alerts = alerts
        self._clock = clock
        # One check at a time: each takes 128 MiB.
        self._lock = threading.Lock()

    async def __call__(self, scope, receive, send):
        nonce = self._window.current()
        if nonce is None:
            response = _not_found()
        elif scope["method"] == "GET":
            pairs = parse_qsl(scope["query_string"].decode("latin-1"), keep_blank_values=True)
            request_id = _single(pairs, "request")
            response = self._page(request_id) if self._live(request_id, nonce) else _not_found()
        else:
            response = await self._post(scope, receive, nonce)
        await response(scope, receive, send)

    async def _post(self, scope, receive, nonce):
        headers = scope["headers"]
        origins = [value for name, value in headers if name == b"origin"]
        if origins and origins != [self._issuer.encode()]:
            return _not_found()
        types = [value for name, value in headers if name == b"content-type"]
        if len(types) != 1 or types[0].split(b";")[0].strip().lower() != FORM:
            return _not_found()
        body = await read_body(headers, receive, BODY_LIMIT)
        if body is None:
            return _not_found()
        try:
            pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True)
        except (UnicodeDecodeError, ValueError):
            return _not_found()
        request_id, passphrase = _single(pairs, "request"), _single(pairs, "passphrase")
        # Counted only for a sign-in this window started, so a stranger cannot
        # spend the five attempts; reserved before checking, so posts at once
        # cannot start more than five checks.
        if passphrase is None or not self._live(request_id, nonce):
            return _not_found()
        if not self._store.reserve_attempt(nonce, WRONG_LIMIT):
            return _not_found()
        if await asyncio.to_thread(self._check, passphrase):
            # Closing first, and approving only for the post that closed it,
            # keeps one window to one sign-in whatever else was in flight.
            if not self._store.close_window(nonce, "signed in", self._clock()):
                return _not_found()
            redirect = self._provider.approve(request_id)
            if redirect is None:
                return _not_found()
            self._alerts.send_later(Event.SIGN_IN_SUCCEEDED, auth.CALLER.get())
            return RedirectResponse(redirect, status_code=303, headers={"Cache-Control": "no-store"})
        self._alerts.send_later(Event.WRONG_PASSPHRASE, auth.CALLER.get())
        if self._store.window(nonce).attempts >= WRONG_LIMIT:
            self._store.close_window(nonce, "five wrong passphrases", self._clock())
            return self._page(None, "That passphrase was not accepted, and the sign-in window is now closed.")
        return self._page(request_id, "That passphrase was not accepted.")

    def _check(self, passphrase):
        with self._lock:
            return verify(self._hash, passphrase)

    def _live(self, request_id, nonce):
        if not request_id or len(request_id) > REQUEST_CHARS:
            return False
        pending = self._store.pending(request_id)
        # A cancel-all closes the window, so a live window's sign-ins are current.
        return pending is not None and pending.window == nonce

    def _page(self, request_id, message=None):
        text = PAGE.format(
            label=html.escape(self._client.label),
            address=html.escape(self._client.return_address),
            message=f"<p>{html.escape(message)}</p>" if message else "",
            form=FORM_HTML.format(request=html.escape(request_id)) if request_id else "",
        )
        return HTMLResponse(text, headers=HEADERS)


def _single(pairs, key):
    values = [value for name, value in pairs if name == key]
    return values[0] if len(values) == 1 else None


async def read_body(headers, receive, limit):
    """A request's body, or None when it declares or sends more than the limit."""
    declared = [value for name, value in headers if name == b"content-length"]
    if declared and (not declared[0].isdigit() or int(declared[0]) > limit):
        return None
    chunks, size = [], 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return None
        chunks.append(message.get("body", b""))
        size += len(chunks[-1])
        if size > limit:
            return None
        if not message.get("more_body", False):
            return b"".join(chunks)


def _not_found():
    return PlainTextResponse("Not Found", status_code=404)
