"""The read tools."""

from workspace_mcp import paths


def list_repos(config, user):
    return [{"name": name, "writable": writable} for name, writable in paths.readable(config, user)]
