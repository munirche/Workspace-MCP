---
name: open-carries-from-t1-1
description: Security properties later tasks must prove (T1.2, T1.3, T1.5, T1.6, T1.9, T1.11, T2.1, T2.2, T3.1, Phase 3); raised at T1.1 Review and at the T1.2/T1.3/T1.5/T1.9 Clarify gate. Check each landed when gating the named task
metadata:
  type: project
---

Nothing is deployed yet; these are design-stage properties. At each named task's Plan or Review gate, check the property has a test or a checklist line; if it does, delete the bullet.

**Why:** most came from observed SDK or tool behaviour, not from the spec, so no criterion forces them.
**How to apply:** verify against the branch's tests, not against this list; positions marked "my view" were advice at Clarify (2026-11-12), not Munir's decisions, so read Plan.md for what he chose.

From T1.1 Review (2026-11-12):

- **T1.2:** landed in `tests/workspace_mcp/test_repo_hygiene.py` (Review 2026-11-16, nothing blocking): lock shape, installed equals pinned, audit step not skipped. Offered as optional tightenings, Munir's to take or decline, so read the test before raising again: the workflow text checks do not see a job-level `if:`, a filter under a trigger, a never-firing cron or extra keys on the audit step; the required deny set left out the bare `export` and `declare` rules the settings hold; no check on tracked file names (`.env*`, `*.pem`, `*.key`). Standing limit: the scanner works by shape, so the project's own random client secret and tokens are invisible to it, and it reads the working tree, not history.
- **T1.3 merge:** the scanner's hash rule is its own literal (a superset of D-4's form). Once the helper exists, one test should feed the helper's output to the scanner's rule, so the two grammars cannot drift apart.
- **T1.6:** the SDK's audience check is off unless set (advisory named in Plan "The SDK, as Tried" point 5). pip-audit passing does not prove it is on; a test must.
- **T1.9 (R-8.3):** landed, see "From T1.9 Review" below.
- **T2.1:** root starts the release venv's interpreter once, for the install, in a fresh venv per release; post-install checks run as the service user (`.pth` files run at interpreter start).
- **T2.2 (R-10.1):** uvicorn makes the socket mode 666, so the 0750 runtime folder is the only guard. Checks: folder mode, owner, group; the web server's group has no extra member; a non-member's connect is refused. The app must not rely on nginx for sign-in limits.
- **Phase 1 local run:** socket inside a 0700 folder.

From the Clarify gate for T1.2, T1.3, T1.5, T1.9 (2026-11-12, built unattended on separate branches):

- **All four:** credential-shaped test values are built at run time in every test file, not only T1.2's, or master fails the R-9.3 scan after the merges. No test reads the real process environment or the real home folder (D-6: dev keys live in munir's environment, and pytest prints compared mappings on failure). Checks print file, line and name, never a matched value.
- **T1.2 (R-9.4):** my view: "any process's environment" includes the session's own; the deny list is a net (the vendor's docs say Bash rules match literal command text), and Munir names the exact rules, not the session. The test should also pin the settings file to deny rules only. Unit files read through the Read tool were not denied at b13f934.
- **T1.3:** landed, see "From T1.3 Review" below.
- **T1.5:** git's child environment is built from scratch (no service secret, no inherited GIT_*, pager or editor names); system and global config are not read; a timeout kills the whole process group; a negative count is refused; the module logs no argv, stdout or stderr.
- **T1.11 (sandbox trial):** the "repo owned by another user" path (safe.directory) cannot be exercised by offline tests, which run as the repo's owner. The trial should run the seeded hostile repo as the throwaway user.
- **T3.1 / T3.6:** repo settings that redirect what git reads (not programs, so R-3.9 as worded does not cover them). Property: git reads only the configured repo's own folder and object store. No criterion owned this at the gate; check whether R-3.9 was widened or a line was added.
- **Before Phase 3 and Phase 4 deploys:** git published security fixes on 2026-11-10 (2.53.1 on the box's track) touching merges, clones and submodules. Not relevant to Phase 1's read-only module; confirm the box's package carries them before the service fetches and rebases (Munir or a session: `git --version` and the package changelog, no secret in the output).

From the Plan gate for the same four tasks (2026-11-12; my advice, check each branch's tests at Review):

- **T1.2:** `.claude/` is a protected path in Claude Code (prompted, refused or classifier-decided by mode; checked in the vendor docs 2026-11-12), so the settings edit is Munir's, not an unattended session's. Scanner: prefix rules carry a body length, tracked symlinks are not followed, one hash grammar shared with T1.3 and the helper.
- **T1.3:** all four landed (Review 2026-11-16).
- **T1.5:** git never enters a submodule; `.git` and what git reads under it are real files and folders, and no `commondir` file; settings are listed with git's own parser, not a second one; every seed has a positive control (plain git writes the canary); output shape fixed on the command line. Put to Munir: the listing-then-override gap (a writer of `.git/config` can add a driver between the two) as an accepted residual; my trigger would be a live repo written by anyone but Munir. Read D-6 for what he chose.
- **Unattended runs:** a stopped task's files must not ride into the next branch (stash with untracked, stage by path).

From T1.3 Review (2026-11-16, uncommitted branch `t1.3-config`, nothing blocking; read `config.py` and `test_config.py` before raising any of these again, since the fixes offered were Munir's to take or decline):

What the loader guarantees, so later tasks need not recheck: names match R-1.4's set; `remote` is `git@host:path` or `ssh://git@host[:port]/path` with no leading hyphen in host or path; `push_key` is a name; the public address has no userinfo, path, query, upper case or default port, and `http` only for the three loopback spellings; the five secrets are wrapped and hidden in repr and str; every error names a key or variable and never an environment value.

What it does not guarantee, each owned by a later task:

- **T2.x drop-in generator:** a checkout `path` is only absolute and normalised; it may hold a newline, colon, space, percent sign or quote, and may be a symlink. The generator writes the bind line from the validated name and the fixed prefix, never from the config's string, and refuses a path that is not that exact string.
- **T2.1 deploy:** the deploy shows the generated drop-in, not `config.toml`, so a pushed change to users, `write_paths`, limits or deny additions ships unseen. Property: the deploy shows the diff of `config.toml` too. No decision recorded; put to Munir at the T2.1 Plan gate.
- **T1.5 / T3.3:** names may start with a hyphen (R-1.4 allows it), so a name is never a bare argv word; the remote goes after `--`, with ssh as the only allowed transport. No host allowlist in the loader: the pinned known-hosts file is the guard.
- **T1.5:** `git_timeout_seconds` had no ceiling at Review (the client allows 240 seconds per call); check whether one was added.
- **T1.6 / T1.10:** the loader accepted all-numeric and non-canonical IPv6 hosts, which the SDK's URL type rewrites, so issuer and allowed host could differ from what is published (fails closed). With a non-default port the allowed host carries the port while nginx forwards `$host` without one. The SDK's URL type puts its input in its error text, so build the settings from the loader's fields and catch its error by name only.
- **T1.8:** the hash's n, r and p are any digits; a value scrypt refuses, or one over its memory limit, starts cleanly and fails at the first sign-in. That failure must be a plain refusal with nothing of the hash in the log.
- **T1.6 / T1.8:** client id and client secret have no shape check (inner whitespace or a control character passes). The bot token and chat id are shape-checked by T1.9's sender.
- **T1.10 entry point:** raw secret strings are locals of `load()` while it validates; start-up reports `str(ConfigError)` only, and nothing that renders frame locals is ever installed.
- **Write tasks (Phase 4):** `write_paths` entries are any non-empty strings (absolute, `..`, leading `!` all pass); R-4.2's fixed refusals are the guard. Deny additions are a separate union, never one ordered pattern list where a later entry could negate a built-in.
- **Second user (D-12's trigger):** a user may hold `write` on a repo absent from their `read`; an edit's "text not found" answer would then tell them about a repo they cannot see. Put to Munir as a possible R-1.4 line.
- **Hash grammar:** the loader and the scanner each hold a literal (the scanner's is the wider). The T1.3-merge bullet above stays open until the helper exists.

From T1.9 Review (2026-11-16, uncommitted branch `t1.9-alerts`, nothing blocking; read `alerts.py` and `test_alerts.py` before raising again, the fixes offered were Munir's to take or decline):

What `alerts.py` guarantees: an alert is one of four fixed events plus UTC time plus an address that is a parsed IP or "unknown" (no free text); the sender is stdlib urllib with no proxy and no redirect, shape-checks token and chat id, and lets out only an exception class name or "HTTP <status>"; a failed delivery returns False with one WARNING (event, time, address, class); the tool-call record is one ASCII JSON line of R-8.4's five fields, each capped at 512 characters; the five tool and resource records of `mcp.server.mcpserver.server` are rewritten at the logger itself, so they are safe on any handler.

What it does not guarantee, each owned by a later task:

- **T1.10 (handlers):** records of every other `mcp.*` logger are cleaned only on handlers passed to `quiet_sdk_logs`. `MCPServer()` calls `logging.basicConfig` and puts its own unfiltered stderr handler on root when root has none (read in mcp 2.3.0; `rich` is not in the lock, so it is a plain stream handler). Property: after start-up every handler on root and on any logger carries the filter, or `mcp` does not propagate and holds only filtered handlers. A test must walk the handlers.
- **T1.10 (fallback text):** at Review the fallback for other `mcp.*` records kept the message text with only newlines removed. The SDK builds some messages with f-strings (refused Host and Origin values, session ids, a schema error's text), so peer text and carriage returns or escape bytes can pass. Offered: write module and line instead of the text, or at least JSON-quote it. Check which was taken.
- **T1.10 (other loggers):** `uvicorn.error`, `uvicorn.access`, `asyncio` and starlette records pass unchanged, tracebacks included. The access log would hold `/authorize`'s query string (client id, state). Property: access log off; the marker test repeated with an exception raised in a route and in middleware, not only in a tool.
- **T1.10 (level):** never DEBUG stays load-bearing: SDK debug records hold exception text and whole frames. Test the effective level of root and `mcp`.
- **T1.8 / T1.10 (blocking):** `Alerts.send` is synchronous; the 5 seconds bound each socket step, not name lookup and not the whole call. Called on the event loop, a wrong passphrase while Telegram is unreachable stalls every request. Property: it runs off the loop under one overall deadline, and the sign-in answer does not wait on it.
- **T1.10 / T2.2 (address):** the address comes from the one header nginx overwrites with the peer's address, never a client-supplied chain, or an alert can name any IP the caller likes.
- **T1.10 (token local):** the sender takes the bare token string; the frame that unwraps it must not be one whose locals can be rendered (same rule as the entry-point bullet above).
- **T1.10 (record order):** the tool-call record is written before the allow or deny decision acts, so a refused call is recorded too.

Related: [[supply-chain-decisions]]
