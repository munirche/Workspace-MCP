# Tasks: Workspace MCP

Status: tasks approved by Munir 2026-11-11 (phases 1–2); phases 3–5 draft

<!-- Only Munir edits the status line. Format:
     Status: tasks approved by Munir <YYYY-MM-DD> (phases 1–2); phases 3–5 draft
     Tasks are 2–4 hour units. Each cites the criteria it satisfies and the test
     files that will prove them. [P] = may run in parallel with neighbours. -->

Spec: `Docs/workspace_mcp/Spec.md` · Plan: `Docs/workspace_mcp/Plan.md`

Each task runs as `/build mcp security task <id>`: its own branch, tests first,
a pull request, CI green, Munir merges. Manual checklist lines are Munir's;
the exact commands are in `deploy/DEPLOY.md`, written as `[munir]` and
`[claude]` lines. No session ever holds a real secret: local runs use
throwaway values.

## Phase 1: The skeleton, local

Sign-in, alerts and one tool, run on this box with throwaway secrets.

Done when: every task is ticked, CI is green, the sign-in flow completes in
MCP Inspector against the local run, and the sandbox trial's results are
recorded in `Plan.md`.

- [ ] T1.1 Settle the SDK's behaviour and pin it — criteria: none (a trial; the answers to Plan's open question "SDK behaviour" are recorded there, and `requirements.lock` gains the pinned SDK with hashes) — tests: none
- [ ] T1.2 The repo guards itself: no credential in tracked files, the assistant's deny rules, dependencies pinned by hash and audited on every change and weekly — criteria: R-9.3, R-9.4, R-9.5 — tests: `tests/workspace_mcp/test_repo_hygiene.py` [P]
- [ ] T1.3 Configuration and its validation — criteria: R-1.1, R-1.4, R-9.1, R-9.2 — tests: `tests/workspace_mcp/test_config.py`
- [ ] T1.4 Which repos exist for a user, and the tool that lists them — criteria: R-1.2, R-1.3 — tests: `tests/workspace_mcp/test_visibility.py`
- [ ] T1.5 The one place that runs version control: caller text is only ever data, time limits, and nothing a repo's own settings name is ever run — criteria: R-3.3, R-3.4, R-3.9 — tests: `tests/workspace_mcp/test_git.py` [P]
- [ ] T1.6 Starting and finishing a sign-in: the one client, its return address, the proof key, the client's secret, no registration, the "authorisation required" answer — criteria: R-5.8, R-5.9, R-5.10, R-6.6, R-6.8, R-6.11, R-6.12, R-6.13 — tests: `tests/workspace_mcp/test_auth_flow.py`
- [ ] T1.7 Tokens: lifetimes, replacement, grace, reuse, cancel-all, restart — criteria: R-6.1, R-6.2, R-6.3, R-6.4, R-6.5, R-6.7, R-6.9, R-6.10 — tests: `tests/workspace_mcp/test_tokens.py`
- [ ] T1.8 The sign-in window, the page and the passphrase — criteria: R-5.1, R-5.2, R-5.3, R-5.4, R-5.5, R-5.6, R-5.7 — tests: `tests/workspace_mcp/test_login.py`
- [ ] T1.9 Alerts and records — criteria: R-8.1, R-8.2, R-8.3, R-8.4 — tests: `tests/workspace_mcp/test_alerts.py` [P]
- [ ] T1.10 Assembling the service: tool titles and declarations, result size, plain refusals, and refusing any host name but its own — criteria: R-7.1, R-7.2, R-7.4, R-7.9 — tests: `tests/workspace_mcp/test_app.py`
- [ ] T1.11 The local trial — criteria: none (manual checklist below)

Manual checklist:
- [ ] Local run on a private socket with a throwaway passphrase and client secret; MCP Inspector on the laptop through an SSH tunnel completes sign-in and calls the one tool
- [ ] The log shows the alerts the fake sender was asked to send: window opened, sign-in succeeded
- [ ] The sandbox trial: a throwaway unit with the planned options, started with `sudo` by Munir but running as a throwaway user of its own, never as root; its script is copied to a root-owned path and read there before it runs. Each line of Plan's list (binds, socket, no connection to loopback or to the box's public addresses on 18789 and 2222, DNS and outside hosts still reachable, Python, git and ssh) is recorded in `Plan.md` as works or as changed, with the reason. Its push test uses a throwaway key on a scratch repo

Verification record: _(pasted from spec-verifier at phase end)_

## Phase 2: The skeleton, deployed and connected

Done when: the service runs on the box under its own user, the phone answers
"which repos can you see?" in text and in voice, and every manual line below
is ticked.

- [ ] T2.1 What gets installed: the service definition, the generator of its repo mounts, the install and deploy steps, the command that opens the sign-in window, the web front's site, and `deploy/DEPLOY.md` — criteria: R-10.5 — tests: `tests/workspace_mcp/test_unit_dropin.py`
- [ ] T2.2 Install and deploy on the box — criteria: R-5.11, R-10.1, R-10.2, R-10.3, R-10.4, R-10.6, R-10.7, R-10.8, R-10.9, R-10.10, R-10.11, R-10.12 [MANUAL] — manual checklist below
- [ ] T2.3 Connect from the Claude app — criteria: R-7.5, R-7.6, R-8.5 [MANUAL] — manual checklist below

Manual checklist:
- [ ] One change at a time: no other change to this box's SSH, firewall or web front is made in the same sitting
- [ ] R-10.9 Before anything: the alert bot `MRC_MCP_Bot` exists (D-4; `MRC_MCP_1_Bot` if the handle is taken), writing to Munir's private chat; the passphrase is in Bitwarden only; its hash, the bot token and the client secret are in Bitwarden and will be typed into the env file by Munir; none is a value used on ovh-vps. Each deploy key is generated in place as root and only its fingerprint is noted
- [ ] The install script is copied to a root-owned path, read there by Munir, and that copy is the one run
- [ ] R-10.7, R-10.6 The deploy installs a commit that is on the remote's `master`, installs dependencies only as ready-built files matching the lock's hashes, shows the difference of every file root will run or load, and waits for yes
- [ ] R-10.4 `/opt/workspace-mcp/`, the unit, `/etc/workspace-mcp.env` and `/etc/workspace-mcp/` are root-owned; the env file and keys are mode 600; the unit holds no secret value, and its five `Environment=` names are added to the recorded list of non-secrets
- [ ] R-10.1 No new listening port: the listening sockets are what they were, plus the Unix socket
- [ ] R-10.2 From this box, the tool, token and discovery addresses answer `403`, and the installed address list is identical to the release's file
- [ ] R-10.3 Inside the service's namespace: no home folder, only the configured repos under `/repos`, no other user's process; connections to 18789 and 2222 are refused on loopback and on the box's public IPv4 and IPv6 addresses, while DNS and an outside host still answer
- [ ] R-5.11 `curl -I` on the site shows the encrypted-only and no-embedding headers
- [ ] R-10.12 The certificate has a renewal hook that reloads the web front, and a forced dry-run renewal passes
- [ ] R-10.11 No `authorized_keys` line, no member of `sudo` or `docker`, no sudoers entry, no firewall rule and no fail2ban jail was added; `fail2ban-client status sshd` is as before
- [ ] R-10.10 The new key files under `~/.ssh` are mode 600
- [ ] R-10.8 AI-Data-Expert's `Invariants.md` has the inventory row and the `mcp` site; `vps-setup-summary.md` has the service, user, site, socket, unit, env file and keys; `/etc/workspace-mcp.env` joins the files the I-9 fingerprint check reads
- [ ] The sign-in page answers "not found" until the window is opened, and loads after
- [ ] R-7.5 The connector is added on claude.ai with the configured client; after sign-in, a text chat answers "which repos can you see?"
- [ ] R-8.5 The alerts for the window opening and the sign-in arrive on the phone
- [ ] R-7.6 The same question in voice mode is answered from the service
- [ ] What the app offers for tool permissions on this connector (ask every time, always allow, per tool or for all) is written into Plan D-9 now, two phases before the write tools depend on it

Verification record: _(pending)_

## Phase 3: The read tools

Done when: Claude on the phone answers questions about each of the three
repos, in text and in voice, and no test finds a way to read outside the
tracked files of a readable repo.

- [ ] T3.1 The one place that decides whether a path may be read — criteria: R-2.1, R-2.2, R-2.3, R-2.4 — tests: `tests/workspace_mcp/test_paths.py`
- [ ] T3.2 Listing and reading files — criteria: R-2.5, R-2.6, R-2.7 — tests: `tests/workspace_mcp/test_read_tools.py`
- [ ] T3.3 Synced repos: refresh before reading, and answering when the remote is away — criteria: R-2.8, R-2.9 — tests: `tests/workspace_mcp/test_synced_read.py` [P]
- [ ] T3.4 Search — criteria: R-3.1, R-3.2 — tests: `tests/workspace_mcp/test_search.py` [P]
- [ ] T3.5 History, status, project context, and the instructions to the client — criteria: R-3.5, R-3.6, R-3.7, R-7.3 — tests: `tests/workspace_mcp/test_repo_info.py` [P]
- [ ] T3.6 Every tool against hostile input, and a live repo left untouched — criteria: R-3.3, R-3.8 — tests: `tests/workspace_mcp/test_hostile_input.py`
- [ ] T3.7 The vault's key, the two more repos, a deploy, and the phone — criteria: R-7.7 [MANUAL] — manual checklist below

Manual checklist:
- [ ] The vault's key is generated as root into `/etc/workspace-mcp/keys/` and added to the vault on GitHub as a deploy key **without** write access (reading is all this phase needs); its fingerprint is noted; GitHub's host key is pinned
- [ ] `AI-Data-Expert-V1-repo` and `MRC_Vault` join the configuration; the deploy shows the new mounts and the credential, and clones the vault
- [ ] R-10.3 and R-10.4 are checked again after this deploy
- [ ] R-7.7 On the phone, in text and in voice, a question about each of the three repos is answered from its content

Verification record: _(pending)_

## Phase 4: Writes

Done when: a memory saved from the phone is on GitHub within seconds and can
be found again, a handoff note reaches a coding session here through the
update command, and the app asks before every write.

- [ ] T4.1 Where a note may go, and where it never may — criteria: R-4.1, R-4.2, R-4.5, R-4.14 — tests: `tests/workspace_mcp/test_write_paths.py`
- [ ] T4.2 Creating and editing a note, and its first line — criteria: R-4.3, R-4.4, R-4.6, R-4.13, R-7.1 — tests: `tests/workspace_mcp/test_write_tools.py`
- [ ] T4.3 Recording a note and publishing it — criteria: R-4.7, R-4.8, R-4.9 — tests: `tests/workspace_mcp/test_publish.py`
- [ ] T4.4 Unpublished notes, conflicts, and two writes at once — criteria: R-4.10, R-4.11, R-4.12 — tests: `tests/workspace_mcp/test_pending.py`
- [ ] T4.5 The command that updates Munir's copy of the vault — criteria: R-11.1, R-11.2 — tests: `tests/workspace_mcp/test_vault_update.py` [P]
- [ ] T4.6 The vault's folders, the checkout's key, a deploy, and the phone — criteria: R-7.8, R-11.3 [MANUAL] — manual checklist below

Manual checklist:
- [ ] The write tools were tried by hand in MCP Inspector against a scratch repo, not the vault
- [ ] In the vault: `inbox/` and `handoff/` exist, and its `CLAUDE.md` says what they are and that a handoff note is shown to Munir before it is acted on
- [ ] The vault's deploy key on GitHub is removed and added again with write access, now that the write tools have passed their tests
- [ ] Munir's checkout of the vault here has a read-only key (mode 600), its remote uses it, and it is updated with the provided command
- [ ] R-10.3 and R-10.4 are checked again after this deploy
- [ ] On the phone, in text and in voice: a memory is saved, its commit is on GitHub, and it is found again
- [ ] R-7.8 With the write tools left on "ask", the app asks before each write with the content in view, in text and in voice; what it offers for skipping the question is recorded in Plan D-9
- [ ] R-11.3 A handoff note written from the phone is picked up by a Claude Code session here through the update command, shown to Munir, and acted on only after his go-ahead

Verification record: _(pending)_

## Phase 5: Verify and record

Done when: a session that wrote none of the code has checked every criterion,
and the box's records describe the service as it runs.

- [ ] T5.1 A last `spec-verifier` pass over the whole spec; its report pasted below — criteria: all
- [ ] T5.2 The records: `Plan.md`'s Current State, `CLAUDE.md`'s Current Status, `memory.md`; in AI-Data-Expert, the proposed new lines for `Invariants.md` put to Munir — criteria: R-10.8 [MANUAL]

Manual checklist:
- [ ] R-10.8 The proposed invariant lines (Plan D-11) are in `Invariants.md` for Munir's approval, and the service's section in `vps-setup-summary.md` matches what runs

Verification record: _(pending)_
