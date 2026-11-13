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

- **T1.2:** the hygiene test asserts the lock has the only-binary line and no index, find-links, trusted-host, URL, editable or include line; installed versions equal the lock's pins; the CI audit step is not skipped by a failing test step.
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

Related: [[supply-chain-decisions]]
