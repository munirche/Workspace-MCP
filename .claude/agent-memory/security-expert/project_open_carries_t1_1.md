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
- **T1.9 (R-8.3):** with a marker planted in every secret and each failure forced, no marker appears in captured logs. Log level never DEBUG. Plan point 7 also gives T1.9 the SDK's two tool-error log paths.
- **T2.1:** root starts the release venv's interpreter once, for the install, in a fresh venv per release; post-install checks run as the service user (`.pth` files run at interpreter start).
- **T2.2 (R-10.1):** uvicorn makes the socket mode 666, so the 0750 runtime folder is the only guard. Checks: folder mode, owner, group; the web server's group has no extra member; a non-member's connect is refused. The app must not rely on nginx for sign-in limits.
- **Phase 1 local run:** socket inside a 0700 folder.

From the Clarify gate for T1.2, T1.3, T1.5, T1.9 (2026-11-12, built unattended on separate branches):

- **All four:** credential-shaped test values are built at run time in every test file, not only T1.2's, or master fails the R-9.3 scan after the merges. No test reads the real process environment or the real home folder (D-6: dev keys live in munir's environment, and pytest prints compared mappings on failure). Checks print file, line and name, never a matched value.
- **T1.2 (R-9.4):** my view: "any process's environment" includes the session's own; the deny list is a net (the vendor's docs say Bash rules match literal command text), and Munir names the exact rules, not the session. The test should also pin the settings file to deny rules only. Unit files read through the Read tool were not denied at b13f934.
- **T1.3:** my view: every secret's environment name carries the service's own prefix, so a same-named variable of another project inherited in a local run cannot satisfy R-9.1. Empty counts as missing; a malformed value is reported by name only; the config object's repr shows no secret; unknown keys stop start-up (that is what makes "not configurable" testable); a zero or negative time limit must never mean no limit.
- **T1.5:** git's child environment is built from scratch (no service secret, no inherited GIT_*, pager or editor names); system and global config are not read; a timeout kills the whole process group; a negative count is refused; the module logs no argv, stdout or stderr.
- **T1.11 (sandbox trial):** the "repo owned by another user" path (safe.directory) cannot be exercised by offline tests, which run as the repo's owner. The trial should run the seeded hostile repo as the throwaway user.
- **T3.1 / T3.6:** repo settings that redirect what git reads (not programs, so R-3.9 as worded does not cover them). Property: git reads only the configured repo's own folder and object store. No criterion owned this at the gate; check whether R-3.9 was widened or a line was added.
- **T1.9:** the sender's interface is a closed set of events plus time and address, no free text; the tool-call record takes exactly R-8.4's five fields; the address is validated before use; one record is one line; a failed delivery's record names the event, time and address, so the trail survives the alert service being down; the failure record never carries the exception's text.
- **Before Phase 3 and Phase 4 deploys:** git published security fixes on 2026-11-10 (2.53.1 on the box's track) touching merges, clones and submodules. Not relevant to Phase 1's read-only module; confirm the box's package carries them before the service fetches and rebases (Munir or a session: `git --version` and the package changelog, no secret in the output).

From the Plan gate for the same four tasks (2026-11-12; my advice, check each branch's tests at Review):

- **T1.2:** `.claude/` is a protected path in Claude Code (prompted, refused or classifier-decided by mode; checked in the vendor docs 2026-11-12), so the settings edit is Munir's, not an unattended session's. Scanner: prefix rules carry a body length, tracked symlinks are not followed, one hash grammar shared with T1.3 and the helper.
- **T1.3:** errors raised `from None` and the marker test renders the whole traceback; the public address accepted by a strict full match (no userinfo, odd port, control character); a clone's `remote` in strict ssh form before T3.3 uses it; `result_chars` capped below the client's limit.
- **T1.5:** git never enters a submodule; `.git` and what git reads under it are real files and folders, and no `commondir` file; settings are listed with git's own parser, not a second one; every seed has a positive control (plain git writes the canary); output shape fixed on the command line. Put to Munir: the listing-then-override gap (a writer of `.git/config` can add a driver between the two) as an accepted residual; my trigger would be a live repo written by anyone but Munir. Read D-6 for what he chose.
- **T1.9:** an address with an IPv6 scope is refused (the scope is free text); each record field has a length cap (journald splits long lines); the log filter sits on the handler and clears traceback fields; the failed-delivery record carries the address.
- **Unattended runs:** a stopped task's files must not ride into the next branch (stash with untracked, stage by path).

Related: [[supply-chain-decisions]]
