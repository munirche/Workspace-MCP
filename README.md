# Workspace-MCP

A remote MCP server on `openclaw-vps` for the Claude iOS app. As a custom
connector it lets Claude on the phone read allowlisted repos on the box, save
and find notes in the vault, and leave handoff notes for the next Claude Code
session.

Status: scaffold only, nothing runs yet. The design is in
`claude-config/MCP_Server_Plan.md` and moves to `Docs/workspace_mcp/Plan.md`.

## Develop

```
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt -r requirements.lock
.venv/bin/pytest
```

## Deploy

Every deploy step needs `sudo` and is run by Munir from `deploy/DEPLOY.md`.
