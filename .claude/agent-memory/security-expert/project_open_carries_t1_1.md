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
- **T1.5:** landed, see "From T1.5 Review" below.
- **T1.11 (sandbox trial):** the "repo owned by another user" path (safe.directory) cannot be exercised by offline tests, which run as the repo's owner. The trial should run the seeded hostile repo as the throwaway user.
- **T3.1 / T3.6:** R-3.10 was added 2026-11-12 for settings that redirect what git reads. It covers version-control settings only; what the work tree itself does to a search is in "From T1.5 Review" below.
- **Before Phase 3 and Phase 4 deploys:** git published security fixes on 2026-11-10 (2.53.1 on the box's track) touching merges, clones and submodules. Not relevant to Phase 1's read-only module; confirm the box's package carries them before the service fetches and rebases (Munir or a session: `git --version` and the package changelog, no secret in the output).

From the Plan gate for the same four tasks (2026-11-12; my advice, check each branch's tests at Review):

- **T1.2:** `.claude/` is a protected path in Claude Code (prompted, refused or classifier-decided by mode; checked in the vendor docs 2026-11-12), so the settings edit is Munir's, not an unattended session's. Scanner: prefix rules carry a body length, tracked symlinks are not followed, one hash grammar shared with T1.3 and the helper.
- **T1.3:** all four landed (Review 2026-11-16).
- **T1.5:** landed. The listing-then-override gap was accepted by Munir 2026-11-16 (Plan D-6, Git bullet; trigger: a live repo written by anyone but `munir`).
- **Unattended runs:** a stopped task's files must not ride into the next branch (stash with untracked, stage by path).

From T1.3 Review (2026-11-16, uncommitted branch `t1.3-config`, nothing blocking; read `config.py` and `test_config.py` before raising any of these again, since the fixes offered were Munir's to take or decline):

What the loader guarantees, so later tasks need not recheck: names match R-1.4's set; `remote` is `git@host:path` or `ssh://git@host[:port]/path` with no leading hyphen in host or path; `push_key` is a name; the public address has no userinfo, path, query, upper case or default port, and `http` only for the three loopback spellings; the five secrets are wrapped and hidden in repr and str; every error names a key or variable and never an environment value.

What it does not guarantee, each owned by a later task:

- **T2.x drop-in generator:** a checkout `path` is only absolute and normalised; it may hold a newline, colon, space, percent sign or quote, and may be a symlink. The generator writes the bind line from the validated name and the fixed prefix, never from the config's string, and refuses a path that is not that exact string.
- **T2.1 deploy:** the deploy shows the generated drop-in, not `config.toml`, so a pushed change to users, `write_paths`, limits or deny additions ships unseen. Property: the deploy shows the diff of `config.toml` too. No decision recorded; put to Munir at the T2.1 Plan gate.
- **T1.5 / T3.3:** names may start with a hyphen (R-1.4 allows it), so a name is never a bare argv word; the remote goes after `--`, with ssh as the only allowed transport. No host allowlist in the loader: the pinned known-hosts file is the guard.
- **Phase 3:** `git_timeout_seconds` had no ceiling at the T1.3 Review (the client allows 240 seconds per call); see the time bullet under "From T1.5 Review".
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

From T1.5 Review (2026-11-16, uncommitted branch `t1.5-git`, nothing blocking its four criteria; read `git.py` and `test_git.py` before raising again, the fixes offered were Munir's to take or decline):

What `git.py` guarantees: git's environment is built from nothing; system and global config, system attributes and replace refs are off; every program-naming key the four read operations consult is overridden on the command line, with each filter and diff driver found by git's own parser; caller text goes after `-e` or `--` with literal pathspecs and is one line of at most 4096 characters; the count is a bounded int; stderr is discarded and every error is a fixed sentence; the whole session is killed with SIGKILL on timeout, cap or normal exit. Seeds have positive controls and Munir's session ran mutations.

What it does not guarantee, each owned by a Phase 3 task (or a later T1.5 fix, check first):

- **Search tool (the top carry):** `git grep` on the work tree opens each tracked name with no check on leading folders (read in git 2.53.0's `grep.c` and `builtin/grep.c`; not run). A tracked folder swapped for a symlink sends the search outside the repo, to anything the service user can read. Not a version-control setting, so R-3.10 does not own it. Property: no search result comes from a file whose real path is outside the repo or which the path-deciding module would refuse; every path `grep` and `ls_files` return passes through that module before any text is returned; a seed with a positive control proves it.
- **Returned paths are untrusted text:** they come from the index, which a writer of `.git` can craft (`..`, absolute, control characters). Deny patterns apply to results, not only to the caller's path argument.
- **Symlink depth:** at Review only `.git`, `.git/info` and `.git/objects/info` were scanned. `objects/pack`, a fan-out folder or anything under `refs` could link to another store, and index entries flagged assume-unchanged make `grep` read blobs, under any name the index gives them (so past a path deny list). Empty room with one user; matters at D-12's second user. Check whether the scan was widened.
- **Git 2.54 or later on the box:** config-named hooks (`hook.<name>.command`, checked 2026-11-16 in GitHub's 2.54 highlights) are not switched off by `core.hooksPath`. Read operations fire no hook while `GIT_OPTIONAL_LOCKS=0` holds and the mount is read-only, so one guard fewer, not a hole. The override list is a denylist: each git upgrade needs the release notes read for new program-naming keys and the seeds re-run on that version.
- **Exceptions that are not `GitError`:** `OSError` from lstat, scandir or Popen (its text holds the repo path) and `UnicodeEncodeError` for a lone surrogate in caller text. Tools answer any unknown exception with a fixed sentence.
- **Truncation:** the last item of a cut `ls_files`, `status` or `log` answer may be a cut path or subject.
- **Blocking and time:** `_spawn` is synchronous and each call spawns twice (listing, then the operation), so one call may take twice the limit, and project context (R-3.7) several times that. Property: off the event loop, one overall deadline per tool call under the client's 240 seconds, and a cap on concurrent git calls.
- **Charsets:** a commit's `encoding` header and the `working-tree-encoding` attribute (which a pulled commit can set) choose a glibc iconv charset in-process. The box's glibc patch level is the guard.

From the T1.4 Clarify gate (2026-11-16; my advice, check `paths.py` and `test_visibility.py` at Review):

- **Write without read:** no decision was recorded in Spec or Plan by this date. Advised: build "invisible" now (the fail-closed reading of D-12's "does not exist for them"); an R-1.4 line refusing such a config at start stays Munir's optional extra, not a blocker. If he adds it, the invisible rule stays as the second layer.
- **Refusal decided from the user's own lists only:** the missing answer is reached without consulting `config.repos`, so "not configured" and "not readable" are one path by construction (type, text, args, no chained cause). Echo the name only when it fits R-1.4's name shape and a short bound; otherwise the same sentence without it.
- **T1.10 / Phase 3 / Phase 4:** the repo lookup is the first check in every tool that can give a repo-dependent answer (before path, write-permission or git); no second log record tells the two causes apart; `list_repos` returns name and writable only, never `path`, `remote`, `push_key` or `source`.
- **Plan gate (2026-11-16, nothing blocking):** the plan carried all of the above. Two test additions offered, check at Review: the refusal's `args` is the message alone (comparing `args` for one name across configs passes even when `args` holds the raw caller value, which then reaches any `repr`); `list_repos` also runs against a `repos` mapping that raises.

From T1.4 Review (2026-11-16, uncommitted branch `t1.4-visibility`, nothing blocking; read `paths.py` and `test_visibility.py` before raising again, the fixes offered were Munir's to take or decline):

What `paths.py` guarantees: the refusal is reached from the user's own read tuple alone, so "not configured" and "not readable" are one code path (same type, text, args, timing, no chained cause, no log record); the echoed name is at most 64 characters of R-1.4's ASCII set (no quote, no control character), and whether it is echoed depends on the caller's value only; `list_repos` gives name and writable for readable names only, in an order that depends on the user's own list only. Both Plan-gate test additions landed.

What the tests did not prove at Review (offered, low):

- The no-chained-cause assertion covered only the configured-but-unreadable refusal, not the not-configured one, and the raising-mapping test compared text only. A lookup of `config.repos` first inside a broad `except` would pass every test while chaining a `KeyError` on one side only (a traceback line that tells the causes apart). Check whether the assertion was widened.
- A non-str user (the `isinstance` guard) had no test. The user comes from the token, not the caller, and the failure is a `TypeError`, not a grant.

Still owned by T1.10: `RepoMissing` is caught by name and answered with its sentence; its traceback (frame locals hold the raw name) is never rendered; the no-log test proves only that `paths.py` is silent, not the tool wrapper.

From the Clarify gate for T1.6, T1.7, T1.8, T1.10 (2026-11-24, one long session; all of it my advice, so read Spec.md and Plan.md for what Munir chose, then check the tests at Plan and Review):

Read in mcp 2.3.0 and uvicorn 0.54.0, not run:

- The SDK's `/authorize` handler answers a bad client or return address with 400 JSON that echoes the caller's value, and a bad challenge with a redirect to the registered address, all before the provider is asked. So R-5.1's "not found" needs a gate of ours in front of the SDK's route, not inside `authorize()`.
- The SDK loads a code or refresh token in one await and exchanges it in another, and trusts the object it loaded. Single use and rotation are atomic only if the provider's exchange re-reads and changes state in one transaction.
- The SDK's expiry checks use the real clock and skip an expiry of None or 0. The provider enforces every expiry itself.
- An omitted `redirect_uri` is accepted (the one registered address is used); an omitted challenge method means S256. Return addresses are compared as parsed URLs, so host case and `:443` pass; the redirect goes to the parsed form.
- `/token` and both metadata routes carry the SDK's wildcard CORS. No cookie is involved and the client secret is required, so accepted as is.
- uvicorn turns proxy headers on by default, reads `FORWARDED_ALLOW_IPS` from the environment, and with trust-all takes the leftmost forwarded value and rewrites the scheme. Advised: `proxy_headers=False` and one reader of our own.

Positions advised (each needs a test or a recorded decision):

- **Local trial (T1.6, T1.11):** the extra return address, the allowed Origin, a non-root window flag and the fake alert sender are passed in by a trial script outside the package; the builder refuses each unless the public address is http on loopback; `__main__` reads no name beyond the six.
- **Cancel-all (T1.7):** a generation number in a root-owned file outside the service's state folder, compared for equality on every token check, fail closed when unreadable. Root never opens anything under the service's own folders (a link there would be a path from the service user to root). Covers unexchanged codes and pending sign-ins.
- **Store (T1.7):** SHA-256 of 256-bit random values, no pepper; the code sets 0600 itself (the local run has a loose umask); validity is never cached in memory.
- **Window (T1.8):** flag under `/run` in a root-owned folder, holding a nonce and the opening time; closings and the attempt count recorded by nonce in the store, the attempt reserved before scrypt runs; one verification at a time (128 MiB each under a 512M cap); `maxmem` passed explicitly; accepted parameter sets fixed in code and checked at start. A post with no pending sign-in is neither counted nor alerted. A wrong client id while the window is open gets the closed-window answer.
- **T1.10:** no Origin allowed in production (trigger: Claude's requests in Phase 2 are seen to carry one); the address is for records only, never a decision.
- **Put to Munir as unclear:** when and from what address the window-opened alert is sent (R-8.1); whether cancel-all covers codes and the window (R-6.9); expired-and-retired precedence and one alert per cancelled chain (R-6.4, R-6.5); what "name the client" shows (R-5.5).

Decided by Munir at that gate (2026-11-24, per the coordinator; confirm in Spec.md and Plan.md): all Clarify advice taken, so do not reopen: return address fixed in code with a trial-only exact loopback address; retired-and-expired or cancelled-chain tokens get `invalid_grant` only, no second alert; cancel-all also kills codes, pending sign-ins and an open window; the window alert is "from the server"; the page names a fixed label. Declined: a second use of a code does not cancel the first use's tokens (no trigger named; mine would be a reported code-replay against Claude's callback).

From the Plan gate for the same four (2026-11-24; my findings, check each at Review):

- **Origin on `/login` (top):** a page served with `Referrer-Policy: no-referrer` makes browsers send `Origin: null` on its own form post (MDN, read 2026-11-24), so "foreign Origin gets 404" would refuse Munir himself while the test client passes. Advised `same-origin`, with `null` treated as foreign.
- **Tests that pass for the wrong reason:** provider methods that never suspend cannot interleave on one event loop, so "two concurrent exchanges" proves nothing unless the test forces load, load, exchange, exchange; the concurrent-post tests need both posts held inside the verification.
- **Cancel-all and the window:** "generation unchanged since first seen" is in memory, so a restart after cancel-all reopens a flag younger than ten minutes. Advised: the revoke command removes the flag, and the window row stores its generation.
- **Revoke command:** no path argument in the installed command (a typo is a silent no-op of the kill switch); tests call its function; one test pins its path literal equal to the service's; shebang is the system Python with `-I`, never the release venv's.
- **Trial locks:** the non-root flag owner and the trial Origin need the same loopback-only refusal as the return address; the default owner 0 must be shown refusing a flag the test user wrote.
- **Smaller:** `/authorize` POST passes a query-only guard to the SDK's echoing 400 (advised GET only); `/login` body cap; `workers=1` (uvicorn reads `WEB_CONCURRENCY`); the unpatched scrypt set pinned as a literal and shown refusing the tests' cheap set; the T1.10 logging carries had no named test in the plan.
- **Safe because of ordering:** `MemoryStore` and the ungated `/authorize` sit on master between merges; fine only while `__main__` stays a stub until T1.10 and no route calls `approve` without the passphrase check.

From T1.6 Review (2026-11-24, uncommitted branch `t1.6-sign-in`, nothing blocking; read `auth.py`, `app.py` and `test_auth_flow.py` before raising again, the fixes offered were Munir's to take or decline):

What is guaranteed: `AuthorizeGuard` parses the query with the same call Starlette uses, requires exactly one client id and at most one byte-equal return address, GET only, and its refusal is identical to an unknown path; `TokenForm` rebuilds the body from its own parse (so the SDK's parser sees one canonical id and secret), strips every Authorization header and refuses with fixed text; a code is consumed atomically and the race test forces the interleaving; any `Trial` object is refused on https; zero or two users stop the build.

Offered, check whether taken:

- **T1.7 (store swap):** the "token for another service" test had no positive control (an identical row with the service's own resource getting 200), so any defect in the hand-written row passes it. `exchange_refresh_token` issued from the object the SDK loaded, not from the store. The provider leaned on the SDK flag alone for R-6.11.
- **T1.10:** the client record holds the secret in clear and pydantic's repr prints it; nothing in SDK 2.3.0 logs it. Offered a hidden repr and catching pydantic's error by name around `record()`. The SDK's `/token` 401 texts tell a wrong id from a wrong secret.
- **Untested at Review:** `TokenForm`'s body cap and 413; a parameter repeated with two different values; the 300 s boundary.
- **Phase 2:** the Basic header is decoded with `unquote_plus`, so the client secret should come from the URL-safe alphabet.

From T1.7 Review (2026-11-24, uncommitted on `t1.7-tokens`, nothing blocking; read `auth.py`, `alerts.py` (Queue), `deploy/workspace-mcp-revoke-all` and `test_tokens.py` before raising again, the fixes offered were Munir's to take or decline):

What is guaranteed: every value is hashed before it reaches a statement; each change is one `BEGIN IMMEDIATE` on a fresh connection and `rotate()` re-reads inside it; the generation is read per check through no link and compared for equality, unreadable means refuse; a public build refuses any generation path but the constant; the revoke command takes no argument, runs the system Python isolated, imports nothing of ours, writes by exclusive temp file and rename, and unlinks the flag; the T1.6 positive control for R-6.11 landed, with the provider's own resource check.

Offered, check whether taken:

- **A number that repeats revives tokens (top):** validity is equality, and a missing file restarted the count (the command wrote 1; a test pinned it). A chain stamped with a number the count reaches again is live if under thirty days old; same for `/etc` restored from a backup. Advised: the service keeps the highest number seen in its store and refuses everything below it; the command seeds a missing file from the clock.
- **An event with no record:** the queue's worker had no `except`, so one escaping exception ends all alerts silently; a send stuck in name lookup blocks the rest; queued alerts die at restart. Advised: a journal line at `send_later`, before queueing.
- **Clock stepped back:** a negative time since retirement counted as grace. Advised: dead.
- **Revoke command, small:** no folder fsync after the rename; a failed flag unlink exits 1 after the number moved; no test for a link planted at the temp name.
- **T1.8:** pin the command's flag path equal to the service's; pending rows had no expiry and nothing is purged.
- **T2.x:** `/etc/workspace-mcp` root-owned and 0755 is a checklist line, since the command trusts its folder.
- **Tests:** a public-URL flow in `conftest.py` reads the real generation file (not a secret, never written), so the suite can differ between the box and CI once deployed.

Currency (searched 2026-11-24): no advisory found against the server side of mcp 2.3.0; GHSA-qx49-fqc8-xw99 (2026-09-28) is client-side discovery, fixed in 2.2.0. OWASP's scrypt floor read the same day: N=2^17, r=8, p=1.

Related: [[supply-chain-decisions]]
