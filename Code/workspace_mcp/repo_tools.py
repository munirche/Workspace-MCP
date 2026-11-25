"""The read tools."""

from workspace_mcp import paths


def list_repos(config, user):
    return [{"name": name, "writable": writable} for name, writable in paths.readable(config, user)]


def list_repos_text(config, user):
    # One sentence, so it reads the same in a chat and in voice, an empty list included.
    repos = list_repos(config, user)
    if not repos:
        return "You can read no repos."
    named = (f"{repo['name']} ({'writable' if repo['writable'] else 'read only'})" for repo in repos)
    return f"You can read these repos: {', '.join(named)}."
