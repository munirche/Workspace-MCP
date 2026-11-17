"""The one place that decides whether a user may read or write a path."""

from workspace_mcp.config import NAME

ECHOED_NAME_CHARS = 64
POINTER = "list_repos shows the ones you can read"


class RepoMissing(Exception):
    # One answer whether the repo is not configured or this user may not read
    # it, so a refusal never tells a user that a repo exists (R-1.3).
    def __init__(self, name):
        if isinstance(name, str) and len(name) <= ECHOED_NAME_CHARS and NAME.fullmatch(name):
            super().__init__(f"There is no repo called '{name}'; {POINTER}.")
        else:
            super().__init__(f"There is no such repo; {POINTER}.")


def _user(config, user):
    return config.users.get(user) if isinstance(user, str) else None


def readable(config, user):
    entry = _user(config, user)
    if entry is None:
        return ()
    names = sorted(set(entry.read), key=lambda name: (name.casefold(), name))
    return tuple((name, name in entry.write) for name in names)


def readable_repo(config, user, name):
    # Decided from the user's own read list before the repos are looked at.
    entry = _user(config, user)
    if entry is None or name not in entry.read:
        raise RepoMissing(name)
    return config.repos[name]
