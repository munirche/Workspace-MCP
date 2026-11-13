---
name: supply-chain-decisions
description: Decisions and accepted risks on the dependency lock, dev tools and CI, taken at T1.1 (2026-11-12), with revisit triggers
metadata:
  type: project
---

Decided by Munir at T1.1 (2026-11-12). Do not reopen without new evidence or a fired trigger.

- **Dev tools stay version-pinned and unhashed** (`requirements-dev.txt`), with unpinned transitive dependencies, and CI keeps `pip install --upgrade pip`. Accepted risk, declined at Clarify.
  **Why:** kept out of T1.1's scope; the service's own lock is the thing root installs.
  **How to apply:** do not re-raise as a finding. Munir named no revisit trigger; mine would be: a dev tool is ever installed on the box by root, CI gains a secret or a write-capable token, or a dev-tool compromise is reported upstream. Cheap bounds that do not reopen it: `permissions: contents: read` in the workflow (suggested at Review, optional).
- **14-day upload cutoff** for the lock (`--uploaded-prior-to`, pip 26+), recorded in Plan D-2. I suggested D-2 name the exception for a security fix younger than the cutoff. Not in D-2 at b13f934; put to Munir again at T1.2's Clarify (2026-11-12) with the audit step's placement (my view: `if: !cancelled()` on the audit step, so a failing test never hides it) and the pins-equal-installed assertion. Read D-2 and the workflow for what he chose before raising any of the three again.
- **`--only-binary :all:` line inside `requirements.lock`** (written by pip-compile): reviewed and accepted as defence in depth, kept alongside the explicit flags in CI, README and deploy.
- The lock holds wheel hashes only (pure-Python packages carry one hash), so the hash check alone refuses a source build.

Related: [[open-carries-from-t1-1]]
