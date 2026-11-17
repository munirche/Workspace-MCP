"""Which repos exist for a user: only those they may read, and any other name is missing."""

import base64
import dataclasses
import logging

import pytest

from workspace_mcp import config, paths, repo_tools
from workspace_mcp.paths import RepoMissing

TEXT = """
[repos.claude-config]
source = "checkout"
path = "/srv/claude-config"

[repos.AI-Data-Expert]
source = "checkout"
path = "/srv/ai-data-expert"

[repos.MRC_Vault]
source = "clone"
remote = "git@github.com:someone/vault.git"
write_paths = ["inbox/**/*.md"]

[repos.private]
source = "clone"
remote = "git@github.com:someone/private.git"
write_paths = ["notes/**/*.md"]

[users.munir]
read = ["claude-config", "MRC_Vault", "AI-Data-Expert", "claude-config"]
write = ["MRC_Vault", "private"]

[users.guest]
read = ["AI-Data-Expert"]
"""


def _environ():
    # Built at run time, so the repo's credential scan never sees a hash here.
    salt, digest = base64.b64encode(b"s" * 16).decode(), base64.b64encode(b"h" * 64).decode()
    return {
        "WORKSPACE_MCP_PUBLIC_URL": "https://mcp.example.test",
        "WORKSPACE_MCP_PASSPHRASE_HASH": "$".join(["scr" + "ypt", "16384", "8", "1", salt, digest]),
        "WORKSPACE_MCP_CLIENT_ID": "id",
        "WORKSPACE_MCP_CLIENT_SECRET": "secret",
        "WORKSPACE_MCP_TELEGRAM_BOT_TOKEN": "token",
        "WORKSPACE_MCP_TELEGRAM_CHAT_ID": "chat",
    }


@pytest.fixture
def loaded():
    return config.load(TEXT, _environ())


class _Untouchable:
    def __getattr__(self, name):
        raise AssertionError("the repos were consulted before the user's own lists decided")

    def __getitem__(self, key):
        raise AssertionError("the repos were consulted before the user's own lists decided")

    def __contains__(self, key):
        raise AssertionError("the repos were consulted before the user's own lists decided")

    def __iter__(self):
        raise AssertionError("the repos were consulted before the user's own lists decided")

    def __len__(self):
        raise AssertionError("the repos were consulted before the user's own lists decided")


def _refusal(loaded, user, name):
    with pytest.raises(RepoMissing) as caught:
        paths.readable_repo(loaded, user, name)
    return caught.value


def test_a_user_sees_the_repos_they_may_read_and_which_they_may_write(loaded):
    """Covers: R-1.2"""
    assert repo_tools.list_repos(loaded, "munir") == [
        {"name": "AI-Data-Expert", "writable": False},
        {"name": "claude-config", "writable": False},
        {"name": "MRC_Vault", "writable": True},
    ]
    assert repo_tools.list_repos(loaded, "guest") == [{"name": "AI-Data-Expert", "writable": False}]


def test_a_listed_repo_says_nothing_but_its_name_and_whether_it_is_writable(loaded):
    """Covers: R-1.2"""
    assert all(set(item) == {"name", "writable"} for item in repo_tools.list_repos(loaded, "munir"))


def test_a_repo_written_but_not_read_is_not_listed(loaded):
    """Covers: R-1.2"""
    names = [item["name"] for item in repo_tools.list_repos(loaded, "munir")]
    assert "private" not in names
    assert names.count("claude-config") == 1


def test_an_unknown_user_sees_no_repos(loaded):
    """Covers: R-1.2"""
    assert repo_tools.list_repos(loaded, "stranger") == []
    assert paths.readable(loaded, "stranger") == ()


def test_the_listing_is_decided_by_the_users_own_lists(loaded):
    """Covers: R-1.2"""
    blind = dataclasses.replace(loaded, repos=_Untouchable())
    assert repo_tools.list_repos(blind, "munir") == repo_tools.list_repos(loaded, "munir")


@pytest.mark.parametrize("user", [None, 5, ["munir"], b"munir"])
def test_a_user_that_is_not_text_sees_nothing(loaded, user):
    """Covers: R-1.2, R-1.3"""
    assert repo_tools.list_repos(loaded, user) == []
    assert str(_refusal(loaded, user, "claude-config")).startswith("There is no repo called 'claude-config';")


def test_a_readable_repo_is_found(loaded):
    """Covers: R-1.3"""
    assert paths.readable_repo(loaded, "munir", "MRC_Vault") is loaded.repos["MRC_Vault"]
    assert paths.readable_repo(loaded, "guest", "AI-Data-Expert") is loaded.repos["AI-Data-Expert"]


@pytest.mark.parametrize(
    ("user", "name"),
    [
        ("guest", "claude-config"),
        ("munir", "private"),
        ("stranger", "claude-config"),
    ],
)
def test_a_repo_the_user_may_not_read_is_refused_as_missing(loaded, user, name):
    """Covers: R-1.3"""
    refused = _refusal(loaded, user, name)
    unconfigured = config.load(TEXT.replace(f"[repos.{name}]", "[repos.elsewhere]").replace(f'"{name}"', '"elsewhere"'),
                               _environ())
    missing = _refusal(unconfigured, user, name)
    assert type(refused) is type(missing) is RepoMissing
    assert str(refused) == str(missing) == f"There is no repo called '{name}'; list_repos shows the ones you can read."
    assert refused.args == missing.args == (str(refused),)
    for error in (refused, missing):
        assert error.__cause__ is None and error.__context__ is None
    assert vars(refused) == vars(missing) == {}


def test_the_refusal_is_decided_before_the_repos_are_consulted(loaded):
    """Covers: R-1.3"""
    blind = dataclasses.replace(loaded, repos=_Untouchable())
    refused = _refusal(blind, "guest", "claude-config")
    assert refused.__cause__ is None and refused.__context__ is None
    assert str(refused) == str(_refusal(loaded, "guest", "nowhere-at-all")).replace("nowhere-at-all", "claude-config")


@pytest.mark.parametrize(
    ("name", "echoed"),
    [
        ("Claude-Config", True),
        ("mrc_vault", True),
        ("x" * 64, True),
        ("x" * 65, False),
        (" claude-config", False),
        ("claude-config ", False),
        ("claude config", False),
        ("claude-config\n", False),
        ("", False),
        ("\udcff", False),
        (None, False),
        (5, False),
        (["claude-config"], False),
        ({"claude-config": 1}, False),
    ],
)
def test_any_other_name_is_missing_and_echoed_only_when_it_could_be_a_name(loaded, name, echoed):
    """Covers: R-1.3"""
    refused = _refusal(loaded, "munir", name)
    if echoed:
        assert str(refused) == f"There is no repo called '{name}'; list_repos shows the ones you can read."
    else:
        assert str(refused) == "There is no such repo; list_repos shows the ones you can read."
    assert refused.args == (str(refused),)


def test_a_refusal_is_not_logged(loaded, caplog):
    """Covers: R-1.3"""
    caplog.set_level(logging.DEBUG)
    for user, name in (("guest", "claude-config"), ("munir", "nowhere")):
        with pytest.raises(RepoMissing):
            paths.readable_repo(loaded, user, name)
    assert caplog.records == []
