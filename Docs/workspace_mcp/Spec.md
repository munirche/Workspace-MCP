# Spec: Workspace MCP

Status: in force for phases 1–2, approved with `Tasks.md` by Munir 2026-11-12; later phases draft
Created: 2026-11-11
Plan: `Docs/workspace_mcp/Plan.md` · Tasks: `Docs/workspace_mcp/Tasks.md`

<!-- What and why only. No files, functions, libraries, or vendors. One
     observable behaviour per criterion. See claude-config/SDD.md.
     Each criterion is one line: the traceability test reads the id and the
     [MANUAL] marker from the line the criterion starts on. -->

## Scope

A service on Munir's development server that lets the Claude app on his phone,
in text and in voice, read the repos he has allowed, save and find notes in his
vault, and leave handoff notes for his next coding session on the server. It is
for one person today and is shaped so a second person can be given a repo of
their own later. It does not send messages, run code, or change anything
outside the vault's two note folders.

Words used below:

- **repo**: a version-controlled project folder. A **tracked** file is one the
  repo's version control knows; an untracked file is any other file in the folder.
- **live repo**: a repo read from Munir's own working copy on the server.
- **synced repo**: a repo the service keeps its own copy of, kept in step with
  the shared remote copy that Munir's other devices also use. The vault is one.
- **the client**: the Claude app, reaching the service through Anthropic's servers.
- **refuse as missing**: answer exactly as for something that does not exist.
- **sign-in window**: a short period, opened by Munir on the server, during
  which the sign-in page exists.

## Actors and user stories

**Munir** — the owner and, today, the only user.
- As Munir, I want Claude on my phone to know the state of my work on the server so that I can discuss it and plan next steps away from my desk.
- As Munir, I want to save a memory to my vault by talking, and find it again later, so that ideas are not lost.
- As Munir, I want to leave a handoff note that my next coding session on the server picks up, so that plans made on the phone get carried out.
- As Munir, I want to be told of every sign-in and every sign that a token was stolen, so that access I did not grant never goes unnoticed.
- As Munir, I want to decide in one place which repos are readable and which are writable, so that adding one is a small, reviewable change.

**The Claude app** — the client that signs in on Munir's behalf and calls the tools.
- As the client, I want each tool to say what it does and whether it changes anything so that I ask Munir before a change.
- As the client, I want refusals as plain sentences so that I can tell Munir what happened or try something else.

**The next coding session** — Claude Code running on the server, with a shell.
- As the next session, I want handoff notes shown to Munir before I act on them so that text planted from outside is never carried out unseen.

**A later second user** — not served by this version; the permission model is built for them.

## Requirements

### R-1 Repos and who may see them

Which repos exist for a user is decided by configuration, and a repo a user may not read does not exist for them.

- R-1.1 THE SYSTEM SHALL read the repos, whether each is live or synced, and each user's readable and writable repos from configuration and SHALL NOT encode them in code.
- R-1.2 WHEN a user asks which repos exist THE SYSTEM SHALL return only the repos that user may read and SHALL say for each whether that user may write to it.
- R-1.3 IF a request names a repo the user may not read THEN THE SYSTEM SHALL refuse it as missing, with the same answer it gives for a repo that is not configured.
- R-1.4 IF the configuration is invalid (a repo or user name with characters other than letters, digits, hyphen and underscore; a user naming a repo that is not configured; a writable repo that is not synced; a writable repo with no writable folders) THEN THE SYSTEM SHALL refuse to start and SHALL name the faulty entry.

### R-2 Reading files

Only tracked text files are served, decided by membership in the tracked list rather than by cleaning up the requested path.

- R-2.1 THE SYSTEM SHALL serve a file only if the requested path is, character for character, one of the repo's tracked files.
- R-2.2 IF a requested path is untracked, is absolute, contains a step to a parent folder, or has a symbolic link in any part of it THEN THE SYSTEM SHALL refuse it as missing.
- R-2.3 IF a requested path matches the deny-list (always environment files, key and certificate files, and names containing "secret" or "credential"; configuration can add to this list and cannot take from it) THEN THE SYSTEM SHALL refuse it as missing, even when it is tracked.
- R-2.4 IF a requested file is not text THEN THE SYSTEM SHALL refuse it and say that it is not a text file.
- R-2.5 WHEN a user reads a file larger than one page THE SYSTEM SHALL return one page of the configured size, say that more remains, and say how to ask for the next page.
- R-2.6 WHEN a user lists files THE SYSTEM SHALL return the tracked files under the given folder, leave out those on the deny-list, return no more than the configured number, and say when the list was cut short.
- R-2.7 WHEN a user reads a file of a live repo THE SYSTEM SHALL return its content as it is on the server at that moment, including changes not yet committed.
- R-2.8 WHEN a user reads from a synced repo whose last refresh is older than the configured interval THE SYSTEM SHALL bring its copy up to date with the remote copy before answering.
- R-2.9 IF the remote copy cannot be reached THEN THE SYSTEM SHALL answer from the copy it has and SHALL say that the content may be out of date.

### R-3 Search, history, status and context

- R-3.1 WHEN a user searches THE SYSTEM SHALL return the matching lines with file and line number from the tracked text files of one named repo or of every repo that user may read, leave out files on the deny-list, return no more than the configured number, and say when results were cut short.
- R-3.2 THE SYSTEM SHALL treat a search query as literal text unless the caller asks for it to be treated as a pattern.
- R-3.3 IF any text supplied by the caller (a query, a path, a repo name, a count) begins with a hyphen or a colon, or contains characters that are special to a command line or to version control's path matching, THEN THE SYSTEM SHALL treat it only as data and SHALL NOT let it change how the operation runs.
- R-3.4 IF a read, search, history or status operation runs longer than the configured time limit THEN THE SYSTEM SHALL stop it and say that it timed out.
- R-3.5 WHEN a user asks for a repo's history THE SYSTEM SHALL return the most recent commits (identifier, date, author, subject), no more than the requested number and never more than the configured maximum, limited to one path when a path is given.
- R-3.6 WHEN a user asks for a repo's status THE SYSTEM SHALL return its current branch, how far it is ahead of and behind its remote, its modified tracked files, and the number of untracked files without their names.
- R-3.7 WHEN a user asks for a repo's project context THE SYSTEM SHALL return its instructions file, the end of its time log when it has one, its current branch, its last ten commits and a summary of its status, each obtained under the same rules as any other read.
- R-3.8 THE SYSTEM SHALL leave a live repo exactly as it found it: after any operation no file, folder or version-control state in it has been created, changed or removed.
- R-3.9 THE SYSTEM SHALL NOT run any program named by a repo's own configuration, attributes or hooks, whatever that repo's version-control settings say.
- R-3.10 THE SYSTEM SHALL NOT let a repo's own version-control settings make it read or write anything outside that repo.

### R-4 Writing notes

Notes go only into the folders configuration allows in a synced repo, are published at once, and are never lost.

- R-4.1 THE SYSTEM SHALL accept a write only when the user may write to the repo, the repo is synced, the path lies in one of that repo's configured writable folders, and the file is a Markdown file.
- R-4.2 IF the path names a file called `CLAUDE.md` or `AGENTS.md` in any letter case at any depth, passes through a folder whose name begins with a dot, matches the deny-list, or has a symbolic link in any part of it THEN THE SYSTEM SHALL refuse the write whatever the configuration says.
- R-4.3 IF a user asks to create a file that already exists THEN THE SYSTEM SHALL refuse and say that it exists.
- R-4.4 WHEN a user asks to replace text in a file THE SYSTEM SHALL make the change only if the old text occurs exactly once, and otherwise SHALL refuse and say whether it was found no times or how many.
- R-4.5 IF the content of a write is larger than the configured size THEN THE SYSTEM SHALL refuse it.
- R-4.6 WHEN THE SYSTEM writes a note, whether it creates it or edits one it did not create, it SHALL make sure the note begins with a line naming the user who ordered it, the application the order came from, the date, and that it has not been reviewed.
- R-4.7 WHEN a write is accepted THE SYSTEM SHALL record it as one change containing that file only, under an author identity used for nothing else, with a message naming the note and the user.
- R-4.8 WHEN a write is recorded THE SYSTEM SHALL publish it to the remote copy's main line at once and SHALL NOT replace or discard anything already published there.
- R-4.9 IF publishing fails THEN THE SYSTEM SHALL keep the recorded note, answer that it was saved but not published, and try again at the next read or write of that repo.
- R-4.10 WHEN a write begins while earlier notes are still unpublished THE SYSTEM SHALL keep those notes and publish them together with the new one.
- R-4.11 IF an unpublished note cannot be combined with the remote copy because the same file was changed elsewhere THEN THE SYSTEM SHALL save that note's content under a new name beside the original, report the conflict, and go on publishing the other notes.
- R-4.12 WHEN two writes to the same repo arrive at the same time THE SYSTEM SHALL carry them out one after the other, and both SHALL succeed.
- R-4.13 WHEN a write finishes THE SYSTEM SHALL return the identifier of the recorded change and whether it was published.
- R-4.14 THE SYSTEM SHALL offer no way to delete, rename or move a file.

### R-5 Signing in

There is no sign-in page unless Munir has opened one, and only the one configured client can start a sign-in.

- R-5.1 WHILE the sign-in window is closed THE SYSTEM SHALL answer every request for the sign-in page or to start a sign-in as not found.
- R-5.2 WHEN Munir opens the sign-in window from the server THE SYSTEM SHALL accept sign-ins for ten minutes and then close the window.
- R-5.3 WHEN a sign-in succeeds THE SYSTEM SHALL close the sign-in window.
- R-5.4 WHEN five wrong passphrases have been entered since the window opened THE SYSTEM SHALL close the sign-in window.
- R-5.5 WHEN the sign-in page is shown THE SYSTEM SHALL name on it the client asking for access and the address the browser will be sent back to.
- R-5.6 THE SYSTEM SHALL hold the passphrase only as a one-way hash that is slow to guess, never the passphrase itself.
- R-5.7 IF the passphrase given to the hashing helper is shorter than twenty characters THEN the helper SHALL refuse it.
- R-5.8 IF a sign-in is started with a client identifier or a return address other than the one configured THEN THE SYSTEM SHALL refuse it without showing the sign-in page and without sending the browser anywhere.
- R-5.9 THE SYSTEM SHALL offer no way for a client to register itself and SHALL name no registration address in what it publishes about how to sign in.
- R-5.10 IF a sign-in is started without the client's proof-key challenge in its strong form (S256) THEN THE SYSTEM SHALL refuse it, and THE SYSTEM SHALL publish that form as the only one it supports.
- R-5.11 THE SYSTEM SHALL serve every page with instructions to the browser to use encrypted connections only and never to show the page inside another site. [MANUAL]

### R-6 Tokens

- R-6.1 WHEN a sign-in completes THE SYSTEM SHALL issue an access token valid for one hour and a refresh token valid for thirty days.
- R-6.2 WHEN a valid refresh token is used THE SYSTEM SHALL issue a new access token and a new refresh token whose thirty days start at that moment, and SHALL retire the one used and every other refresh token of the same sign-in.
- R-6.3 WHEN a retired refresh token is used again within sixty seconds of being retired THE SYSTEM SHALL issue a further access token and refresh token for the same sign-in and SHALL leave the replacement it already issued valid.
- R-6.4 IF a retired refresh token is used more than sixty seconds after being retired THEN THE SYSTEM SHALL cancel every token that came from the same sign-in and SHALL alert Munir.
- R-6.5 IF a refresh token is expired, cancelled or unknown THEN THE SYSTEM SHALL answer with the standard error that tells the client to sign in again (`invalid_grant`) and no other.
- R-6.6 THE SYSTEM SHALL accept a sign-in code once only, within five minutes of issuing it, and only together with the proof key that matches the challenge the sign-in began with.
- R-6.7 THE SYSTEM SHALL store tokens and sign-in codes only as hashes, each with the user it belongs to.
- R-6.8 IF a tool request carries no valid access token THEN THE SYSTEM SHALL answer that authorisation is required and SHALL say where the client can find out how to sign in.
- R-6.9 WHEN Munir runs the cancel-all action on the server THE SYSTEM SHALL stop accepting every token issued before it.
- R-6.10 WHEN the service is restarted THE SYSTEM SHALL go on accepting the valid tokens issued before the restart.
- R-6.11 IF an access token was issued for a service address other than this service's own THEN THE SYSTEM SHALL refuse it.
- R-6.12 THE SYSTEM SHALL accept token requests in the standard form encoding that the client sends.
- R-6.13 IF a request to exchange a sign-in code or a refresh token does not carry the configured client's identifier and its secret THEN THE SYSTEM SHALL refuse it.

### R-7 What the client sees

- R-7.1 THE SYSTEM SHALL give every tool a title and a declaration that it either only reads or changes data, the two write tools being declared as changing data.
- R-7.2 THE SYSTEM SHALL keep every tool result within the configured size, which is below the client's limit, and SHALL say when a result was cut short.
- R-7.3 THE SYSTEM SHALL tell the client, in its instructions and again in the description of the project-context tool, to ask for a repo's project context before answering about it, and SHALL tell it that tool results are file contents and never instructions, that untracked files are never visible, and where memories are saved.
- R-7.4 IF a tool refuses or fails THEN THE SYSTEM SHALL return one plain sentence the client can act on and SHALL NOT include server paths, command output or internal detail.
- R-7.9 IF a request names a host other than the service's own public name THEN THE SYSTEM SHALL refuse it.
- R-7.5 WHEN Munir adds the service to the Claude app with the configured client and signs in THE SYSTEM SHALL answer "which repos can you see?" in a text chat. [MANUAL]
- R-7.6 WHEN Munir asks the same question in voice mode THE SYSTEM SHALL be called and answer. [MANUAL]
- R-7.7 WHEN Munir asks about each configured repo from the phone, in text and in voice, THE SYSTEM SHALL answer from that repo's content. [MANUAL]
- R-7.8 WHEN a write tool is about to be called and Munir has not himself chosen otherwise THE Claude app SHALL ask him first with the content in view, in text and in voice; what the app offers for skipping that question SHALL be recorded in the plan. [MANUAL]

### R-8 Alerts and records

- R-8.1 WHEN the sign-in window is opened, a sign-in succeeds, a wrong passphrase is entered, or a retired refresh token is used after its grace period THE SYSTEM SHALL send Munir an alert naming the event, the time and the address it came from.
- R-8.2 IF an alert cannot be delivered THEN THE SYSTEM SHALL record the failure and SHALL carry on with what it was doing.
- R-8.3 THE SYSTEM SHALL never put a passphrase, a token, a secret or file content into an alert or a log record.
- R-8.4 WHEN a tool is called THE SYSTEM SHALL record the user, the tool, the repo, the path and the address the call came from.
- R-8.5 WHEN Munir first signs in to the deployed service THE SYSTEM SHALL deliver real alerts to his phone for the window opening and for the sign-in. [MANUAL]

### R-9 Configuration and data requirements

- R-9.1 THE SYSTEM SHALL read its public address and its secrets (the passphrase hash, the client's identifier and secret, the alert credentials) from its environment, never from configuration kept in the repo, and IF one is missing THEN SHALL refuse to start, naming the missing item and never a value.
- R-9.2 THE SYSTEM SHALL read from configuration, each with a default, the page size, the list and search limits, the result size limit, the operation time limit, the refresh interval for synced repos, the note size limit and additions to the deny-list, and SHALL NOT make the token lifetimes, the grace period, or the sign-in window's length and allowed wrong attempts configurable.
- R-9.3 THE project's tracked files SHALL contain no credential: no key in a known vendor format, no private-key block, no passphrase hash.
- R-9.4 THE project's shared assistant settings SHALL deny the assistant reading the server's environment files, the service's key folder and any process's environment, showing service definitions, and running commands as administrator.
- R-9.5 THE service's dependencies SHALL be pinned by exact version and content hash, and the project's automated check SHALL fail when one has a known security advisory, on every change and on a weekly schedule.

### R-10 Running on the server

These criteria are how the service keeps the server's own list of what must
stay true (AI-Data-Expert's `Docs/Infrastructure/Invariants.md`); Plan D-11
maps each to its line there.

- R-10.1 THE SYSTEM SHALL listen on no network port and SHALL be reachable only through the server's web front. [MANUAL]
- R-10.2 IF a request to a tool, token or discovery address comes from outside Anthropic's published address ranges THEN the web front SHALL refuse it, and the installed list of allowed ranges SHALL be exactly the deployed version's, with no extra address. [MANUAL]
- R-10.3 THE SYSTEM SHALL run as a user of its own that cannot see any home folder, any repo that is not configured or any other user's processes, and cannot connect to any service listening on this server, whether on its loopback address or on its own public addresses. [MANUAL]
- R-10.4 THE SYSTEM's installed program, its service definition, its environment file and its keys SHALL be owned by the administrator account and not writable by Munir's ordinary account; the environment file and the keys SHALL be readable by the administrator only, and the service definition SHALL hold no secret value. [MANUAL]
- R-10.5 IF a configured repo name or credential name contains anything but letters, digits, hyphen and underscore, or a live repo's path does not resolve to the folder of that name under Munir's repos folder THEN the deployment step SHALL stop and change nothing.
- R-10.6 WHEN a deployment is run THE deployment step SHALL show the difference, from the installed version, of every file the administrator account will run or load (the deployment step itself, the mount generator, the sign-in and cancel-all commands, the service definition, the web front's files and the dependency lock), and SHALL wait for Munir's yes before changing anything. [MANUAL]
- R-10.7 THE deployment step SHALL install only a version that is on the main line of the project's remote copy, and SHALL install dependencies only as ready-built files that match the lock's hashes, running no dependency's own build code. [MANUAL]
- R-10.8 WHEN the service is installed or changed THE record of the server's services and the list of what must stay true on it SHALL be updated in the same sitting. [MANUAL]
- R-10.9 THE passphrase SHALL exist only in Munir's password manager; its hash, the alert credentials and the client secret SHALL exist only in the password manager and the service's administrator-only file (the client secret also in the Claude app's connector settings); each key SHALL exist only in its administrator-only file, its fingerprint recorded; and none SHALL be a value used on the production server. [MANUAL]
- R-10.10 NO key or credential file that this project adds to Munir's home folder SHALL be readable by anyone but him. [MANUAL]
- R-10.11 THE installation SHALL add no way to log in to the server, no member to its administrator groups, no rule letting any account run commands as administrator, no firewall rule and no change to its existing intrusion-blocking service. [MANUAL]
- R-10.12 WHEN the site's certificate is renewed THE web front SHALL serve the new certificate without anyone acting. [MANUAL]

### R-11 Handoff notes

- R-11.1 WHEN Munir's copy of the vault on the server is updated with the provided command THE command SHALL list the incoming changes that were written by the service.
- R-11.2 IF any incoming change touches an instructions file (`CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`) or the vault's assistant settings folder, or a change written by the service touches anything outside the note folders, THEN the command SHALL stop and SHALL leave Munir's copy unchanged.
- R-11.3 WHEN a coding session on the server finds a handoff note for its repo THE session SHALL show Munir the note and act only after he agrees. [MANUAL]

## Non-functional requirements

Covered by R-3.4 (time limit), R-6.10 (restart), R-7.2 (result size), R-8.3
(nothing secret in records) and R-10 (isolation). No real patient data is on
this server, so no health-data safeguard applies.

## Out of scope

- Sending messages of any kind (to Munir or to others). Plan: "Ideas for later".
- Sign-in for a second person, and sign-in through an outside identity provider. Plan: D-12, D-4.
- Deleting, renaming or moving files; writing anything but Markdown; writing to a live repo. Plan: D-9.
- Running code, tests or commands on the server.
- Search by meaning rather than by text.
- Claude Code as a client of this service.

## Needs clarification

None open.

Resolved 2026-11-11 by Munir: a note edited from the phone gains the first line even if the service did not create it, and the line names who ordered it and from which application, so other applications can be added later (R-4.6). `inbox/` and `handoff/<repo>.md` stand for now; they are configuration and can change as the vault is tuned. The alert bot is a new Telegram bot named `MRC_MCP_Bot` (`MRC_MCP_1_Bot` if the handle is taken), used for nothing else, writing to Munir's private chat with it (R-8.5, R-10.9); the handle BotFather actually grants is recorded in `Plan.md` when the bot is created in Phase 2.

Changed 2026-11-12 by Munir, at the Clarify gate of T1.2, T1.3, T1.5 and T1.9: R-9.1 gains the service's public address, which no criterion covered although the sign-in metadata and the Host check are built from it; R-3.10 is new, because a live repo's own settings can point version control at files outside the repo, which R-3.9 (programs only) does not cover.

## Completeness checklist

- [x] Every user story is served by at least one requirement
- [x] Every criterion is one observable behaviour with no implementation detail
- [x] Every criterion could be stated as a test sentence
- [x] Manual-only criteria are marked [MANUAL]
- [x] No [NEEDS CLARIFICATION] remains for the phases about to be approved
- [x] Domain expert has reviewed the sections in its domain (`mcp-expert` and `security-expert`, 2026-11-11; findings folded in)
