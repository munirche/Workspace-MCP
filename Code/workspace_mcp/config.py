"""Loads and validates config.toml: repos, users, write paths, deny-list, caps."""

import base64
import binascii
import ipaddress
import posixpath
import re
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

NAME = re.compile(r"[A-Za-z0-9_-]+")
# Written later into a systemd bind line, so only characters that need no quoting there.
CHECKOUT_PATH = re.compile(r"/[A-Za-z0-9._/-]+")
REMOTES = (
    re.compile(r"git@([A-Za-z0-9.-]+):(?![-/])[A-Za-z0-9._/-]+"),
    re.compile(r"ssh://git@([A-Za-z0-9.-]+)(?::([0-9]{1,5}))?/(?!-)[A-Za-z0-9._/-]+"),
)
PUBLIC_URL = re.compile(r"(https?)://([a-z0-9.-]+|\[[0-9a-f:.]+\])(?::([0-9]{1,5}))?/?")
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
PASSPHRASE_HASH = re.compile(
    r"scrypt\$[0-9]+\$[0-9]+\$[0-9]+\$([A-Za-z0-9+/]+={0,2})\$([A-Za-z0-9+/]+={0,2})"
)
LOOPBACK = {"localhost", "127.0.0.1", "[::1]"}
DEFAULT_PORT = {"https": 443, "http": 80}
RESULT_CHARS_CEILING = 140_000

ENVIRONMENT = {
    "public_url": "WORKSPACE_MCP_PUBLIC_URL",
    "passphrase_hash": "WORKSPACE_MCP_PASSPHRASE_HASH",
    "client_id": "WORKSPACE_MCP_CLIENT_ID",
    "client_secret": "WORKSPACE_MCP_CLIENT_SECRET",
    "telegram_bot_token": "WORKSPACE_MCP_TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "WORKSPACE_MCP_TELEGRAM_CHAT_ID",
}
REPO_KEYS = {"checkout": {"source", "path"}, "clone": {"source", "remote", "push_key", "write_paths"}}


class ConfigError(Exception):
    pass


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value):
        self._value = value

    def reveal(self):
        return self._value

    def __repr__(self):
        return "Secret(<hidden>)"

    __str__ = __repr__


@dataclass(frozen=True)
class Repo:
    name: str
    source: str
    path: str | None = None
    remote: str | None = None
    push_key: str | None = None
    write_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class User:
    name: str
    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()


@dataclass(frozen=True)
class Limits:
    result_chars: int = 100_000
    page_lines: int = 400
    list_entries: int = 500
    search_matches: int = 100
    search_line_chars: int = 300
    log_default: int = 10
    log_max: int = 50
    git_timeout_seconds: int = 15
    refresh_seconds: int = 60
    note_chars: int = 50_000


@dataclass(frozen=True)
class PublicAddress:
    issuer: str
    resource: str
    allowed_host: str


@dataclass(frozen=True)
class Secrets:
    passphrase_hash: Secret
    client_id: Secret
    client_secret: Secret
    telegram_bot_token: Secret
    telegram_chat_id: Secret


@dataclass(frozen=True)
class Config:
    repos: Mapping[str, Repo]
    users: Mapping[str, User]
    limits: Limits
    deny_additions: tuple[str, ...]
    public: PublicAddress
    secrets: Secrets


def load_file(path, environ):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ConfigError(f"cannot read the configuration file {str(path)!r}") from None
    return load(text, environ)


def load(text, environ):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(
            f"the configuration is not valid TOML (line {error.lineno}, column {error.colno})"
        ) from None
    _only_keys(data, {"repos", "users", "limits", "deny"}, "the configuration")
    repos = _repos(_table(data, "repos", "the configuration"))
    values = _present(environ)
    return Config(
        repos=MappingProxyType(repos),
        users=MappingProxyType(_users(_table(data, "users", "the configuration"), repos)),
        limits=_limits(_table(data, "limits", "the configuration")),
        deny_additions=_deny(_table(data, "deny", "the configuration")),
        public=_public_address(values["public_url"]),
        secrets=_secrets(values),
    )


def _table(data, key, where):
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: {key!r} must be a table")
    return value


def _only_keys(table, allowed, where):
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown key {unknown[0]!r}")


def _name(name, kind):
    if not NAME.fullmatch(name):
        raise ConfigError(f"{kind} {name!r}: a name may hold only letters, digits, hyphen and underscore")


def _string(table, key, where):
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: {key!r} must be a non-empty string")
    return value


def _strings(table, key, where):
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ConfigError(f"{where}: {key!r} must be a list of non-empty strings")
    return tuple(value)


def _repos(table):
    repos = {}
    for name, entry in table.items():
        _name(name, "repo")
        where = f"repo {name!r}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a table")
        source = entry.get("source")
        if not isinstance(source, str) or source not in REPO_KEYS:
            raise ConfigError(f"{where}: 'source' must be \"checkout\" or \"clone\"")
        _only_keys(entry, REPO_KEYS[source], where)
        if source == "checkout":
            path = _string(entry, "path", where)
            if not CHECKOUT_PATH.fullmatch(path) or posixpath.normpath(path) != path:
                raise ConfigError(f"{where}: 'path' must be an absolute, normalised path of letters, digits, '.', '_', '-'")
            repos[name] = Repo(name, source, path=path)
            continue
        remote = _string(entry, "remote", where)
        if not _remote_ok(remote):
            raise ConfigError(f"{where}: 'remote' must be git@host:path or ssh://git@host/path")
        push_key = entry.get("push_key")
        if push_key is not None and not (isinstance(push_key, str) and NAME.fullmatch(push_key)):
            raise ConfigError(f"{where}: 'push_key' must be a credential name")
        repos[name] = Repo(name, source, remote=remote, push_key=push_key,
                           write_paths=_strings(entry, "write_paths", where))
    return repos


def _users(table, repos):
    users = {}
    for name, entry in table.items():
        _name(name, "user")
        where = f"user {name!r}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a table")
        _only_keys(entry, {"read", "write"}, where)
        read, write = _strings(entry, "read", where), _strings(entry, "write", where)
        for repo in read + write:
            if repo not in repos:
                raise ConfigError(f"{where}: repo {repo!r} is not configured")
        for repo in write:
            if repos[repo].source != "clone":
                raise ConfigError(f"{where}: repo {repo!r} is writable but not a clone")
            if not repos[repo].write_paths:
                raise ConfigError(f"{where}: repo {repo!r} is writable but has no write_paths")
        users[name] = User(name, read, write)
    return users


def _limits(table):
    names = {field.name for field in fields(Limits)}
    _only_keys(table, names, "[limits]")
    for key, value in table.items():
        if type(value) is not int or value <= 0:
            raise ConfigError(f"[limits]: {key!r} must be a positive whole number")
    limits = Limits(**table)
    if limits.result_chars > RESULT_CHARS_CEILING:
        raise ConfigError(f"[limits]: 'result_chars' may be at most {RESULT_CHARS_CEILING}")
    if limits.log_default > limits.log_max:
        raise ConfigError("[limits]: 'log_default' may not exceed 'log_max'")
    return limits


def _deny(table):
    _only_keys(table, {"add"}, "[deny]")
    additions = _strings(table, "add", "[deny]")
    if any(item.startswith("!") for item in additions):
        raise ConfigError("[deny]: an addition may not start with '!'")
    return additions


def _present(environ):
    missing = [variable for variable in ENVIRONMENT.values() if not environ.get(variable, "").strip()]
    if missing:
        raise ConfigError(f"missing from the environment: {', '.join(missing)}")
    return {key: environ[variable] for key, variable in ENVIRONMENT.items()}


def _public_address(value):
    variable = ENVIRONMENT["public_url"]
    match = PUBLIC_URL.fullmatch(value)
    if not match:
        raise ConfigError(f"{variable} must be scheme://host[:port] in lower case, with no other part")
    scheme, host, port = match.groups()
    # An address must be in the one form the SDK's URL type keeps unchanged,
    # or the issuer it publishes would differ from the one derived here.
    if host.startswith("["):
        if not _canonical(ipaddress.IPv6Address, host[1:-1]):
            raise ConfigError(f"{variable} has an IPv6 host not in its shortest form")
    elif host.rsplit(".", 1)[-1].isdigit():
        if not _canonical(ipaddress.IPv4Address, host):
            raise ConfigError(f"{variable} has an IPv4 host not in dotted decimal form")
    elif not all(LABEL.fullmatch(label) for label in host.split(".")):
        raise ConfigError(f"{variable} has a malformed host")
    if scheme == "http" and host not in LOOPBACK:
        raise ConfigError(f"{variable} must use https unless the host is the local machine")
    if port is not None and not _port_ok(port):
        raise ConfigError(f"{variable} has a malformed port")
    if port is not None and int(port) == DEFAULT_PORT[scheme]:
        raise ConfigError(f"{variable} must leave out the default port")
    authority = host if port is None else f"{host}:{port}"
    issuer = f"{scheme}://{authority}"
    return PublicAddress(issuer=issuer, resource=f"{issuer}/mcp", allowed_host=authority)


def _secrets(values):
    match = PASSPHRASE_HASH.fullmatch(values["passphrase_hash"])
    if not match or not _base64(match.group(1), 16) or not _base64(match.group(2), 32):
        raise ConfigError(f"{ENVIRONMENT['passphrase_hash']} is not scrypt$n$r$p$salt$hash")
    return Secrets(**{key: Secret(values[key]) for key in ENVIRONMENT if key != "public_url"})


def _remote_ok(remote):
    for form in REMOTES:
        match = form.fullmatch(remote)
        if match:
            host, port = match.group(1), (match.groups() + (None,))[1]
            labels_ok = all(LABEL.fullmatch(label) for label in host.lower().split("."))
            return labels_ok and (port is None or _port_ok(port))
    return False


def _port_ok(port):
    return port == str(int(port)) and 0 < int(port) < 65536


def _canonical(kind, text):
    try:
        return str(kind(text)) == text
    except ValueError:
        return False


def _base64(text, shortest):
    if len(text) < shortest or len(text) % 4:
        return False
    try:
        base64.b64decode(text, validate=True)
    except binascii.Error:
        return False
    return True
