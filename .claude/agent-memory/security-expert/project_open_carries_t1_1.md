---
name: open-carries-from-t1-1
description: Security properties I asked later tasks (T1.2, T1.6, T1.9, T2.1, T2.2) to carry after the T1.1 SDK trial; check each landed when gating those tasks
metadata:
  type: project
---

Raised at T1.1 Review (2026-11-12). Nothing is deployed yet; these are design-stage properties, each to be verified at the named task's gate and then removed from this list.

- **T1.2:** the hygiene test asserts the lock has the only-binary line and no index, find-links, trusted-host, URL or editable line; optionally that installed versions equal the lock's pins after the dev tools install. CI audit step should not be skipped by a failing test step.
- **T1.6:** the SDK's audience check is off unless set (advisory named in Plan "The SDK, as Tried" point 5). pip-audit passing does not prove it is on; a test must.
- **T1.9 (R-8.3):** the SDK logs a full traceback on a plain tool exception, and Plan says a failed alert is logged. The alert service's URL carries the bot token, and the HTTP client puts URLs in exception text and INFO records (logger names changed to `httpx2` / `httpcore2` in the 2.x SDK's dependency). Property: with marker values planted for every secret and each failure forced, no marker appears in captured logs. Log level never DEBUG.
- **T2.1:** root starts the release venv's interpreter once, for the install, in a fresh venv per release; post-install checks run as the service user. Wheels run no build code but installed `.pth` files run at interpreter start.
- **T2.2 (R-10.1):** uvicorn sets the socket to mode 666 after bind, so the unit's UMask does not narrow it and the 0750 runtime folder is the only guard. Checks: folder mode, owner and group; the web server's group has no extra members; a connect by a non-member is refused. Any process in that group bypasses nginx's allowlist and limits, so the app must not rely on nginx for sign-in limits.
- **Phase 1 local run:** socket inside a 0700 folder, as the trial did.

**Why:** these came from observed SDK behaviour, not from the spec, so no criterion forces them yet.
**How to apply:** at each named task's Plan or Review gate, check the property has a test or a checklist line; if it does, delete the bullet here.

Related: [[supply-chain-decisions]]
