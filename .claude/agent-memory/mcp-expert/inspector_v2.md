---
name: inspector-v2
description: MCP Inspector's npm `latest` is v2 (no proxy, Node backend does OAuth); v1 is `@v1-latest`; ports and callback URLs, read from its repo docs 2026-11-25
metadata:
  type: reference
---

Read 2026-11-25 from github.com/modelcontextprotocol/inspector (README, docs/v1-to-v2-migration.md, docs/environment-variables.md, clients/web/README.md):
- `npx @modelcontextprotocol/inspector` now runs v2. v1 is security-fixes-only, run as `@v1-latest` (branch `v1/main`).
- v2: one web server on 6274 (sandbox 6275); the 6277 proxy is gone. `MCP_PROXY_AUTH_TOKEN` renamed `MCP_INSPECTOR_API_TOKEN` (query param `?MCP_INSPECTOR_API_TOKEN=`); the docs' example UI URL uses 127.0.0.1.
- v2 web: OAuth discovery/token go through the Node backend (`POST /api/fetch`), so no browser Origin/CORS at /token. Whether MCP requests also go via Node is not stated. The web client's OAuth redirect URL is not documented.
- v2 CLI/TUI: `--client-id`, `--client-secret`, `--callback-url` (default `http://127.0.0.1:6276/oauth/callback`, env `MCP_OAUTH_CALLBACK_URL`).
- v1 (as known before): browser UI 6274, proxy 6277, OAuth runs in the browser through the TS SDK (prefers client_secret_basic, client_id only in the header), redirect = page origin + /oauth/callback.

**How to apply:** any Inspector step in DEPLOY.md must pin a version and match that version's ports, callback and token handling. Related: [[sdk-2-3-0-facts]].
