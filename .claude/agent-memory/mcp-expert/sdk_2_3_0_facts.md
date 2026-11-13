---
name: sdk-2-3-0-facts
description: mcp 2.3.0 server behaviour verified from GitHub source and PyPI on 2026-11-12 (auth routes, client auth, token audience, Host check, stateless, UDS)
metadata:
  type: reference
---

Verified 2026-11-12 from tag v2.3.0 source and PyPI JSON (SDK not yet installed in .venv). Re-check if the pin changes.

- PyPI: 2.3.0 newest (2026-10-02), no advisories listed; 1.30.0 newest 1.x (maintenance, security fixes only). 2.3.0 depends on `mcp-types==2.3.0` and `httpx2` (pydantic's httpx successor, pure wheel).
- Client auth (`server/auth/middleware/client_auth.py`): post, basic and none supported, but the method is taken from the stored client's `token_endpoint_auth_method`, one per client; metadata hardcodes both post and basic. Secret compared with hmac.compare_digest.
- `create_auth_routes`: /authorize always mounted, no hook to disable; /register and /revoke only when enabled. S256 only, hardcoded.
- `AuthSettings.validate_token_resource` (added 2.2.0 / 1.30.0): default None -> False with MCPDeprecationWarning when resource_server_url set; True compares AccessToken.resource to resource_server_url.
- `streamable_http_app(json_response=, stateless_http=, transport_security=, host="127.0.0.1")`: with transport_security None and loopback host, Host allowlist auto-set to loopback only (mcp.rsboom.com would get 421). Security settings go to the session manager, so Host check likely covers /mcp only (not verified for OAuth routes).
- MCPServer builds ProviderTokenVerifier from auth_server_provider automatically; RequireAuthMiddleware wraps /mcp only.
- No Unix socket in MCPServer.run(); serve the Starlette app with uvicorn uds= directly.
- Claude docs (2026-11-12) list auth spec revisions up to 2025-11-25 only; the SDK 2.x also speaks the 2026-07-28 stateless revision.

Verified 2026-11-12 from the installed .venv (rg skips .venv via gitignore: Read or Grep exact file paths):
- Era routing (streamable_http_manager.py:192-198): any MCP-Protocol-Version header not in HANDSHAKE_PROTOCOL_VERSIONS goes to `_streamable_http_modern` (2026-07-28: no initialize, every request needs params._meta envelope, else 400 INVALID_PARAMS). Modern path runs its own TransportSecurity check; still behind RequireAuthMiddleware.
- RequireAuthMiddleware wraps /mcp only and runs BEFORE the Host/Origin check (Host check is inside the session manager). 401 always carries error="invalid_token" (even no token; RFC 6750 says SHOULD NOT) and never `scope=`.
- BearerAuthBackend with validate on: AccessToken.resource None -> refused. Provider must stamp own URL when client omits `resource`. authorize/token handlers pass `resource` through to the provider.
- PRM scopes_supported = AuthSettings.required_scopes; AS metadata scopes_supported = client_registration_options.valid_scopes (settable with enabled=False).
- Tool errors (tools/base.py, server.py:437-456): ToolError -> "Error executing tool <name>: <msg>", logged INFO with msg; other exceptions -> generic, logger.exception. A tool returning CallToolResult(is_error=True) passes through convert_result unchanged (func_metadata.py:204-207), no prefix, no SDK log.
- Origin: absent passes; present must exactly match allowed_origins (or `:*` port wildcard), else 403 and the value is logged at WARNING.
- uvicorn uds chmods socket 0o666 unless the file pre-exists.
- Tool annotated `-> str` (or any primitive) gets an output schema with wrap_output: result carries the text twice (TextContent + structuredContent {"result": ...}) (func_metadata.py:209-225, 534-536). Returning CallToolResult directly or structured_output=False avoids it.
- validate_issuer_url (auth/routes.py:25-43): https required except host localhost/127.0.0.1/[::1]; no query, no fragment. allowed_hosts match is exact incl. port, or `host:*`.
- uvicorn on uds: scope client is None (getpeername on AF_UNIX not a tuple, protocols/utils.py:13-27); ProxyHeadersMiddleware trusts only if client in forwarded_allow_ips (default "127.0.0.1,::1" or env FORWARDED_ALLOW_IPS), None never trusted unless "*". So XFF is ignored on a UDS unless forwarded_allow_ips="*"; with "*" it takes the FIRST XFF entry.
- ctx.request_context.request is the Starlette Request (mcpserver/context.py:296 reads its headers).
- Claude building page 2026-11-12: ~150,000 chars per result and 240 s per call (claude.ai/Desktop); Claude Code 25,000 tokens.
- Claude auth docs 2026-11-12: canonical 401 is `Bearer resource_metadata=...`; scopes from 401 `scope` else PRM scopes_supported; offline_access appended only if AS metadata lists it; outbound range stated as 160.79.104.0/21.
