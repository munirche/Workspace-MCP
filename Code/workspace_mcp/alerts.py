"""Telegram alerts, the tool-call record, and the SDK's own log records made safe."""

import enum
import ipaddress
import json
import logging
import queue
import re
import threading
import urllib.parse
import urllib.request
from datetime import UTC, datetime

SDK_LOGGER = "mcp.server.mcpserver.server"
TELEGRAM_API = "https://api.telegram.org"
BOT_TOKEN = re.compile(r"[0-9]{6,12}:[A-Za-z0-9_-]{30,64}")
CHAT_ID = re.compile(r"-?[0-9]{1,20}")
FIELD_CHARS = 512
REASON = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}|HTTP [0-9]{3}")


class Event(enum.Enum):
    WINDOW_OPENED = "the sign-in window was opened"
    SIGN_IN_SUCCEEDED = "a sign-in succeeded"
    WRONG_PASSPHRASE = "a wrong passphrase was entered"
    REFRESH_TOKEN_REUSED = "a retired refresh token was used after its grace period"


class AlertNotDelivered(Exception):
    def __init__(self, reason):
        self.reason = reason if isinstance(reason, str) and REASON.fullmatch(reason) else "unknown"
        super().__init__(f"alert not delivered ({self.reason})")


# The window is opened by a command on the box, not from an address.
FROM_SERVER = object()


def address_text(address):
    if address is FROM_SERVER:
        return "the server"
    # A scoped IPv6 address carries free text after "%", so it is refused whole.
    if not isinstance(address, str) or "%" in address:
        return "unknown"
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        return "unknown"


class Alerts:
    def __init__(self, sender, logger, clock=lambda: datetime.now(UTC)):
        self._sender = sender
        self._logger = logger
        self._clock = clock

    def note(self, event, address):
        # Recorded before the alert is sent, so a stuck or failed sender still leaves a trace.
        when = self._clock().astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        self._logger.warning("alert: %s at %s from %s", getattr(event, "name", "unknown"), when,
                             address_text(address))

    def send(self, event, address):
        if not isinstance(event, Event):
            raise TypeError("an alert is one of the fixed events")
        where = address_text(address)
        when = self._clock().astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        try:
            self._sender(f"Workspace-MCP: {event.value}, at {when}, from {where}.")
        except Exception as error:
            reason = error.reason if isinstance(error, AlertNotDelivered) else type(error).__name__
            self._logger.warning("alert not delivered: %s at %s from %s (%s)", event.name, when, where, reason)
            return False
        return True

    def lost(self, error):
        self._logger.warning("alert not delivered (%s)", type(error).__name__)


class Queue:
    """Sends alerts on one worker thread, so no answer waits on Telegram."""

    def __init__(self, alerts):
        self._alerts = alerts
        self._queue = queue.Queue()
        threading.Thread(target=self._run, name="alerts", daemon=True).start()

    def send_later(self, event, address):
        self._alerts.note(event, address)
        self._queue.put((event, address))

    def join(self):
        self._queue.join()

    def _run(self):
        while True:
            event, address = self._queue.get()
            try:
                self._alerts.send(event, address)
            except Exception as error:
                self._alerts.lost(error)
            finally:
                self._queue.task_done()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class TelegramSender:
    def __init__(self, token, chat_id, timeout=5, opener=None):
        if not (isinstance(token, str) and BOT_TOKEN.fullmatch(token)):
            raise ValueError("the alert bot token is malformed")
        if not (isinstance(chat_id, str) and CHAT_ID.fullmatch(chat_id)):
            raise ValueError("the alert chat id is malformed")
        self._url = f"{TELEGRAM_API}/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout
        self._opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

    def __repr__(self):
        return "TelegramSender(<hidden>)"

    def __call__(self, text):
        body = urllib.parse.urlencode({"chat_id": self._chat_id, "text": text}).encode()
        request = urllib.request.Request(self._url, data=body, method="POST")
        # The URL holds the token and urllib's errors keep it, so none leaves
        # here, not even as the context of the error raised instead.
        failure = None
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.status != 200:
                    failure = f"HTTP {response.status}"
                elif json.loads(response.read(65536)).get("ok") is not True:
                    failure = "NotOk"
        except Exception as error:
            failure = type(error).__name__
        if failure is not None:
            raise AlertNotDelivered(failure)


def _field(value):
    if value is None:
        return None
    text = str(value)
    if len(text) <= FIELD_CHARS:
        return text
    return f"{text[:FIELD_CHARS]}... ({len(text)} characters)"


def record_tool_call(logger, user, tool, repo, path, address):
    fields = {"user": user, "tool": tool, "repo": repo, "path": path, "address": address_text(address)}
    logger.info("tool call %s", json.dumps({key: _field(value) for key, value in fields.items()}, ensure_ascii=True))


def _quoted(value):
    return json.dumps(str(value)[:FIELD_CHARS], ensure_ascii=True)


def _exception_class(record):
    if not record.exc_info or record.exc_info[1] is None:
        return None
    error = record.exc_info[1]
    # The SDK wraps a tool's own exception; the class worth recording is the cause.
    if type(error).__name__ in ("UnexpectedToolError", "UnexpectedResourceError") and error.__cause__ is not None:
        error = error.__cause__
    return type(error).__name__


def _sdk_sentence(record, cause):
    args = record.args if isinstance(record.args, tuple) else ()
    tool = _quoted(args[0]) if args else '"?"'
    if record.msg == "Tool %r rejected arguments: %r":
        return f"tool {tool} rejected its arguments"
    if record.msg == "Tool %r failed: %r":
        unknown = len(args) > 1 and str(args[1]).startswith("Unknown tool: ")
        return f"tool {tool} is unknown" if unknown else f"tool {tool} failed"
    if record.msg == "Tool %r raised an unexpected exception":
        return f"tool {tool} raised {cause}"
    if record.msg == "Resource %r raised an unexpected exception":
        return f"a resource read raised {cause}"
    if record.msg == "Resource %r failed: %r":
        return "a resource read failed"
    return None


class QuietSdkRecords(logging.Filter):
    def filter(self, record):
        if getattr(record, "workspace_mcp_quiet", False):
            return True
        if record.name != "mcp" and not record.name.startswith("mcp."):
            return True
        cause = _exception_class(record)
        sentence = _sdk_sentence(record, cause) if record.name == SDK_LOGGER else None
        if sentence is None:
            # Some SDK messages are f-strings that already hold the caller's
            # text or an exception's, so only where the record came from is kept.
            sentence = f"logged at {record.module}:{record.lineno}"
            if cause:
                sentence += f" ({cause})"
        record.msg, record.args = sentence, ()
        record.exc_info = record.exc_text = record.stack_info = None
        record.workspace_mcp_quiet = True
        return True


def quiet_sdk_logs(*handlers):
    quiet = QuietSdkRecords()
    logging.getLogger(SDK_LOGGER).addFilter(quiet)
    for handler in handlers:
        handler.addFilter(quiet)
    return quiet
