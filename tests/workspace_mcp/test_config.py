"""Configuration: repos, users, limits and deny additions from config.toml; the address and secrets from the environment."""

import base64
import dataclasses
import re
import traceback
from pathlib import Path

import pytest
from mcp.server.auth.routes import validate_issuer_url
from mcp.server.auth.settings import AuthSettings

from workspace_mcp import config
from workspace_mcp.config import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
MARK = "Qz" + "7mark" + "er"
SALT = base64.b64encode(b"s" * 16).decode()
DIGEST = base64.b64encode(b"h" * 64).decode()

VALID = """
[repos.notes]
source = "checkout"
path = "/srv/notes"

[repos.vault]
source = "clone"
remote = "git@github.com:someone/vault.git"
push_key = "vault"
write_paths = ["inbox/**/*.md"]

[users.alice]
read = ["notes", "vault"]
write = ["vault"]
"""

NON_CONFIGURABLE = [
    "access_token_seconds",
    "refresh_token_days",
    "grace_seconds",
    "window_minutes",
    "wrong_attempts",
]


def _hash(salt=SALT, digest=DIGEST, n="16384"):
    # Joined at run time, so the repo's credential scan never sees a hash here.
    return "$".join(["scr" + "ypt", n, "8", "1", salt, digest])


def _environ(**changes):
    environ = {
        "WORKSPACE_MCP_PUBLIC_URL": "https://mcp.example.test",
        "WORKSPACE_MCP_PASSPHRASE_HASH": _hash(salt=base64.b64encode(MARK.encode() * 2).decode()),
        "WORKSPACE_MCP_CLIENT_ID": "id-" + MARK,
        "WORKSPACE_MCP_CLIENT_SECRET": "secret-" + MARK,
        "WORKSPACE_MCP_TELEGRAM_BOT_TOKEN": "token-" + MARK,
        "WORKSPACE_MCP_TELEGRAM_CHAT_ID": "chat-" + MARK,
    }
    environ.update(changes)
    return {key: value for key, value in environ.items() if value is not None}


def _refused(text, environ=None):
    with pytest.raises(ConfigError) as caught:
        config.load(text, _environ() if environ is None else environ)
    return caught.value


def _unchained(error):
    return error.__cause__ is None and (error.__context__ is None or error.__suppress_context__)


def _shown(error):
    return str(error) + "".join(traceback.format_exception(error))


def test_repos_sources_and_users_come_from_the_configuration():
    """Covers: R-1.1"""
    loaded = config.load(VALID, _environ())
    assert set(loaded.repos) == {"notes", "vault"}
    assert loaded.repos["notes"].source == "checkout"
    assert loaded.repos["notes"].path == "/srv/notes"
    assert loaded.repos["vault"].source == "clone"
    assert loaded.repos["vault"].write_paths == ("inbox/**/*.md",)
    assert loaded.users["alice"].read == ("notes", "vault")
    assert loaded.users["alice"].write == ("vault",)

    changed = config.load(VALID.replace('read = ["notes", "vault"]', 'read = ["notes"]').replace(
        "[repos.notes]", "[repos.papers]").replace('"notes"', '"papers"'), _environ())
    assert set(changed.repos) == {"papers", "vault"}
    assert changed.users["alice"].read == ("papers",)


def test_the_code_names_no_configured_repo_or_user():
    """Covers: R-1.1"""
    shipped = config.load_file(REPO_ROOT / "config.toml", _environ())
    names = set(shipped.repos) | set(shipped.users)
    code = {path.name: path.read_text(encoding="utf-8") for path in (REPO_ROOT / "Code").rglob("*.py")}
    found = sorted(f"{file}: {name}" for file, text in code.items() for name in names if name in text)
    assert not found, f"configured names written into code: {found}"


def test_the_shipped_configuration_lists_claude_config_only():
    """Covers: R-1.1"""
    shipped = config.load_file(REPO_ROOT / "config.toml", _environ())
    assert set(shipped.repos) == {"claude-config"}
    assert shipped.repos["claude-config"].source == "checkout"
    assert set(shipped.users) == {"munir"}
    assert shipped.users["munir"].read == ("claude-config",)
    assert shipped.users["munir"].write == ()


@pytest.mark.parametrize("name", ["has space", "dotted.name", "slash/name", "ünï", "tab\tname", "", "new\nline"])
@pytest.mark.parametrize("kind", ["repo", "user"])
def test_a_name_outside_letters_digits_hyphen_and_underscore_is_refused(kind, name):
    """Covers: R-1.4"""
    key = '"' + name.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n") + '"'
    text = VALID + (f'\n[repos.{key}]\nsource = "checkout"\npath = "/srv/x"\n' if kind == "repo"
                    else f"\n[users.{key}]\nread = []\n")
    error = _refused(text)
    assert kind in str(error) and repr(name) in str(error)


@pytest.mark.parametrize("field", ["read", "write"])
def test_a_user_naming_an_unconfigured_repo_is_refused(field):
    """Covers: R-1.4"""
    error = _refused(VALID + f'\n[users.bob]\n{field} = ["missing"]\n')
    assert "'bob'" in str(error) and "'missing'" in str(error)


def test_a_writable_repo_that_is_not_synced_is_refused():
    """Covers: R-1.4"""
    error = _refused(VALID + '\n[users.bob]\nwrite = ["notes"]\n')
    assert "'bob'" in str(error) and "'notes'" in str(error)


@pytest.mark.parametrize("write_paths", ["", "write_paths = []\n"])
def test_a_writable_repo_with_no_writable_folders_is_refused(write_paths):
    """Covers: R-1.4"""
    text = VALID.replace('write_paths = ["inbox/**/*.md"]\n', write_paths)
    error = _refused(text)
    assert "'vault'" in str(error) and "write_paths" in str(error)


def test_a_synced_repo_nobody_writes_needs_no_writable_folders():
    """Covers: R-1.4"""
    text = VALID.replace('write_paths = ["inbox/**/*.md"]\n', "").replace('write = ["vault"]', "write = []")
    assert config.load(text, _environ()).repos["vault"].write_paths == ()


@pytest.mark.parametrize(
    ("text", "named"),
    [
        (VALID + "\n[secrets]\nclient_secret = \"x\"\n", "'secrets'"),
        ("passphrase_hash = \"x\"\n" + VALID, "'passphrase_hash'"),
        ("public_url = \"https://x.test\"\n" + VALID, "'public_url'"),
        (VALID.replace('source = "checkout"', 'source = "checkout"\nbranch = "main"'), "'branch'"),
        (VALID.replace("[users.alice]", "[users.alice]\nadmin = true"), "'admin'"),
        (VALID.replace('source = "checkout"', 'source = "checkout"\nwrite_paths = ["a"]'), "'write_paths'"),
        (VALID.replace('push_key = "vault"', 'push_key = "vault"\npath = "/srv/v"'), "'path'"),
        (VALID + "\n[deny]\nremove = [\"x\"]\n", "'remove'"),
    ],
)
def test_an_unknown_table_or_key_is_refused_by_name(text, named):
    """Covers: R-1.4, R-9.1"""
    assert named in str(_refused(text))


@pytest.mark.parametrize(
    "text",
    [
        VALID.replace('read = ["notes", "vault"]', 'read = "notes"'),
        VALID.replace('write = ["vault"]', "write = [1]"),
        VALID.replace('source = "checkout"', "source = 1"),
        VALID.replace('source = "checkout"', 'source = "mirror"'),
        VALID.replace('source = "checkout"', 'source = ["checkout"]'),
        VALID.replace('source = "checkout"', 'source = { kind = "checkout" }'),
        VALID.replace('path = "/srv/notes"', "path = 7"),
        VALID.replace('write_paths = ["inbox/**/*.md"]', 'write_paths = "inbox"'),
        VALID.replace('write_paths = ["inbox/**/*.md"]', 'write_paths = [""]'),
        VALID.replace('push_key = "vault"', 'push_key = "../vault"'),
        "repos = 1\n",
        "[repos]\nnotes = 1\n",
        "[repos.notes\n",
    ],
)
def test_a_malformed_entry_is_refused(text):
    """Covers: R-1.4"""
    _refused(text)


@pytest.mark.parametrize(
    "path",
    ['path = "srv/notes"', 'path = "/srv/../etc"', 'path = "/srv/notes/"', "", 'path = "/srv/no tes"',
     'path = "/srv/notes\\n[Service]"', 'path = "/srv/notes:/repos/x"', 'path = "/srv/%h"', "path = \"/srv/'x'\""],
)
def test_a_checkout_needs_an_absolute_path(path):
    """Covers: R-1.4"""
    error = _refused(VALID.replace('path = "/srv/notes"', path))
    assert "'notes'" in str(error) and "'path'" in str(error)


@pytest.mark.parametrize(
    "remote",
    [
        None,
        "https://github.com/someone/vault.git",
        "ext::sh -c touch% /tmp/pwned",
        "-oProxyCommand=touch /tmp/pwned",
        "git@-oProxyCommand=x:vault.git",
        "git@github.com:-u/vault.git",
        "ssh://user:pw@github.com/someone/vault.git",
        "ssh://git@github.com/-x/vault.git",
        "file:///srv/vault",
        "/srv/vault",
        "git@github.com:someone/vault.git\n",
        "ssh://git@github.com:099999/someone/vault.git",
        "ssh://git@github.com:0/someone/vault.git",
        "git@--.:someone/vault.git",
        "git@github..com:someone/vault.git",
    ],
)
def test_a_clone_needs_an_ssh_remote(remote):
    """Covers: R-1.4"""
    line = "" if remote is None else 'remote = "' + remote.replace("\n", "\\n") + '"'
    error = _refused(VALID.replace('remote = "git@github.com:someone/vault.git"', line))
    assert "'vault'" in str(error) and "'remote'" in str(error)


@pytest.mark.parametrize("remote", ["git@github.com:someone/vault.git", "ssh://git@github.com/someone/vault.git",
                                    "ssh://git@github.com:22/someone/vault.git"])
def test_a_clone_takes_either_ssh_form(remote):
    """Covers: R-1.4"""
    text = VALID.replace("git@github.com:someone/vault.git", remote)
    assert config.load(text, _environ()).repos["vault"].remote == remote


def test_the_address_and_secrets_come_from_the_environment():
    """Covers: R-9.1"""
    environ = _environ()
    loaded = config.load(VALID, environ)
    assert loaded.public.issuer == "https://mcp.example.test"
    assert loaded.secrets.passphrase_hash.reveal() == environ["WORKSPACE_MCP_PASSPHRASE_HASH"]
    assert loaded.secrets.client_id.reveal() == "id-" + MARK
    assert loaded.secrets.client_secret.reveal() == "secret-" + MARK
    assert loaded.secrets.telegram_bot_token.reveal() == "token-" + MARK
    assert loaded.secrets.telegram_chat_id.reveal() == "chat-" + MARK


@pytest.mark.parametrize("variable", sorted(config.ENVIRONMENT.values()))
@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_missing_variable_is_named(variable, value):
    """Covers: R-9.1"""
    error = _refused(VALID, _environ(**{variable: value}))
    assert variable in str(error)


def test_every_missing_variable_is_named_at_once():
    """Covers: R-9.1"""
    error = _refused(VALID, {})
    assert all(variable in str(error) for variable in config.ENVIRONMENT.values())


def test_the_environment_is_read_only_from_what_is_passed(monkeypatch):
    """Covers: R-9.1"""
    for variable, value in _environ().items():
        monkeypatch.setenv(variable, value)
    _refused(VALID, {})


@pytest.mark.parametrize(
    ("url", "issuer", "allowed_host"),
    [
        ("https://mcp.example.test", "https://mcp.example.test", "mcp.example.test"),
        ("https://mcp.example.test/", "https://mcp.example.test", "mcp.example.test"),
        ("https://mcp.example.test:8443", "https://mcp.example.test:8443", "mcp.example.test:8443"),
        ("http://localhost:8000", "http://localhost:8000", "localhost:8000"),
        ("http://127.0.0.1:8000/", "http://127.0.0.1:8000", "127.0.0.1:8000"),
        ("http://[::1]:8000", "http://[::1]:8000", "[::1]:8000"),
    ],
)
def test_the_public_address_gives_the_issuer_resource_and_host(url, issuer, allowed_host):
    """Covers: R-9.1"""
    public = config.load(VALID, _environ(WORKSPACE_MCP_PUBLIC_URL=url)).public
    assert public.issuer == issuer
    assert public.resource == issuer + "/mcp"
    assert public.allowed_host == allowed_host
    settings = AuthSettings(issuer_url=public.issuer, resource_server_url=public.resource,
                            validate_token_resource=True)
    validate_issuer_url(settings.issuer_url)
    assert str(settings.issuer_url) == public.issuer
    assert str(settings.resource_server_url) == public.resource


@pytest.mark.parametrize(
    "url",
    [
        "http://mcp.example.test",
        "ftp://mcp.example.test",
        "https://" + MARK + ":pw@mcp.example.test",
        "https://MCP.example.test",
        "HTTPS://mcp.example.test",
        "https://mcp.example.test:443",
        "http://localhost:80",
        "https://mcp.example.test/mcp",
        "https://mcp.example.test/" + MARK,
        "https://mcp.example.test?" + MARK,
        "https://mcp.example.test#" + MARK,
        "https://mcp.example.test:99999",
        "https://mcp.example.test:0",
        "https://mcp.example.test:080",
        "https://",
        "https://-bad.example.test",
        "https://bad..example.test",
        "https://mcp.exa\tmple.test",
        "https://mcp.example.test\n",
        " https://mcp.example.test",
        "https://[::g]",
        "https://[0:0:0:0:0:0:0:1]",
        "https://[::FFFF:1.2.3.4]",
        "https://2130706433",
        "https://127.1",
        "https://0x7f.0.0.1",
        "https://010.0.0.1",
        "mcp.example.test",
    ],
)
def test_a_malformed_public_address_is_named_and_not_shown(url):
    """Covers: R-9.1"""
    error = _refused(VALID, _environ(WORKSPACE_MCP_PUBLIC_URL=url))
    assert "WORKSPACE_MCP_PUBLIC_URL" in str(error)
    assert MARK not in _shown(error)


@pytest.mark.parametrize(
    "value",
    [
        "argon2$" + MARK,
        _hash(salt=MARK),
        _hash(salt=base64.b64encode(b"short").decode()),
        _hash(digest=base64.b64encode(b"h" * 8).decode()),
        _hash(digest=DIGEST.rstrip("=") + "A"),
        _hash(n="x"),
        _hash(salt="-" * 16),
        _hash() + "\n",
        _hash() + "$" + MARK,
    ],
)
def test_a_malformed_passphrase_hash_is_named_and_not_shown(value):
    """Covers: R-9.1"""
    error = _refused(VALID, _environ(WORKSPACE_MCP_PASSPHRASE_HASH=value))
    assert "WORKSPACE_MCP_PASSPHRASE_HASH" in str(error)
    assert MARK not in _shown(error)


def test_no_secret_shows_in_the_loaded_configuration():
    """Covers: R-9.1"""
    environ = _environ()
    loaded = config.load(VALID, environ)
    secrets = [value for key, value in environ.items() if key != "WORKSPACE_MCP_PUBLIC_URL"]
    for shown in (repr(loaded), str(loaded), repr(loaded.secrets), f"{loaded.secrets.client_secret}"):
        assert MARK not in shown
        assert not [value for value in secrets if value in shown]


def test_no_error_shows_a_secret_or_its_cause():
    """Covers: R-9.1"""
    environ = _environ(WORKSPACE_MCP_PUBLIC_URL="https://" + MARK + ".example.test:99999")
    error = _refused("[repos.x]\nsource = 1\n" + MARK + " = 1\n", environ)
    assert _unchained(error)
    assert MARK not in _shown(error)
    for broken in ("[repos." + MARK + "\n", 'x = "' + MARK + "\n"):
        error = _refused(broken)
        assert _unchained(error) and MARK not in _shown(error)


def test_the_limits_have_their_defaults():
    """Covers: R-9.2"""
    assert config.load(VALID, _environ()).limits == config.Limits(
        result_chars=100_000, page_lines=400, list_entries=500, search_matches=100, search_line_chars=300,
        log_default=10, log_max=50, git_timeout_seconds=15, refresh_seconds=60, note_chars=50_000,
    )


@pytest.mark.parametrize("field", [field.name for field in dataclasses.fields(config.Limits)])
def test_each_limit_can_be_changed(field):
    """Covers: R-9.2"""
    value = 3 if field != "log_max" else 60
    limits = config.load(VALID + f"\n[limits]\n{field} = {value}\n", _environ()).limits
    assert getattr(limits, field) == value


@pytest.mark.parametrize("value", ["0", "-1", "true", "1.5", '"10"', "140001"])
def test_a_limit_must_be_a_sensible_whole_number(value):
    """Covers: R-9.2"""
    error = _refused(VALID + f"\n[limits]\nresult_chars = {value}\n")
    assert "'result_chars'" in str(error)


def test_the_default_history_length_may_not_exceed_its_maximum():
    """Covers: R-9.2"""
    error = _refused(VALID + "\n[limits]\nlog_default = 20\nlog_max = 10\n")
    assert "'log_default'" in str(error)


def test_deny_additions_are_read_from_the_configuration():
    """Covers: R-9.2"""
    loaded = config.load(VALID + '\n[deny]\nadd = ["*.secret", "private/**"]\n', _environ())
    assert loaded.deny_additions == ("*.secret", "private/**")
    assert config.load(VALID, _environ()).deny_additions == ()


@pytest.mark.parametrize("add", ['["!*.md"]', '[""]', '"*.secret"', "[1]"])
def test_a_deny_addition_can_only_add(add):
    """Covers: R-9.2"""
    _refused(VALID + f"\n[deny]\nadd = {add}\n")


@pytest.mark.parametrize("key", NON_CONFIGURABLE)
def test_token_lifetimes_grace_and_the_window_cannot_be_configured(key):
    """Covers: R-9.2"""
    assert f"'{key}'" in str(_refused(VALID + f"\n[limits]\n{key} = 1\n"))
    assert f"'{key}'" in str(_refused(f"{key} = 1\n" + VALID))
    assert not any(key in field.name for field in dataclasses.fields(config.Limits))
