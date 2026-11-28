# Workspace-MCP

A remote MCP server on `openclaw-vps` for the Claude iOS app. As a custom
connector it lets Claude on the phone read allowlisted repos on the box, save
and find notes in the vault, and leave handoff notes for the next Claude Code
session.

Status: Phase 1 done (2026-11-27): the skeleton (sign-in, alerts and one
tool, `list_repos`) runs locally and is verified; not deployed yet. See
`Docs/workspace_mcp/` for the spec, the plan and the tasks.

## Develop

```
python3 -m venv .venv
.venv/bin/pip install --require-hashes --only-binary :all: -r requirements.lock
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

## Deploy

Every deploy step needs `sudo` and is run by Munir from `deploy/DEPLOY.md`.
