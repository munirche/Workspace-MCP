# Plan: Workspace MCP

## Current State

**As of 2026-11-11.** Nothing is built. The repo is scaffolded (Phase A) and
every file under `Code/workspace_mcp/` is a stub.
**Next:** Phase B moves the design here and writes `Spec.md` and `Tasks.md`.
**Watch:** sessions on the box cannot `sudo`; nothing is built before Munir
approves the `Tasks.md` status line.

---

**Created:** 2026-11-11
**Status:** design
**Spec:** `Docs/workspace_mcp/Spec.md` · **Tasks:** `Docs/workspace_mcp/Tasks.md`

---

## What It Is

A remote MCP server on `openclaw-vps` that the Claude iOS app uses as a
custom connector: it reads allowlisted repos, saves and finds memories in the
vault, and leaves handoff notes for the next Claude Code session.

## Decisions

The decisions (D-1 to D-12), the architecture, the file layout, the unit and
nginx files and the open questions are in `claude-config/MCP_Server_Plan.md`.
Phase B carries them into this file in the format of claude-config's
`templates/Plan.md`, and that file is then deleted.
