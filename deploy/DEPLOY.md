# Deploy runbook

Every step marked `[munir]` is Munir's: he types every `sudo` and every value.
`[claude]` steps are read-only checks a session may run; no session ever sees a
real secret. Phase 2's install and deploy steps arrive with T2.1.

## T1.11 The local trial (Phase 1)

### A. MCP Inspector against a local run (no sudo)

Every value in this part is a throwaway, made fresh by the trial script and
deleted when it stops, so a session may see it. `npx @modelcontextprotocol/inspector`
now starts Inspector v2, whose sign-in requests leave from its own Node process
with no `Origin`, the same shape as Claude's server-side calls: the closer
rehearsal, so it comes first. Inspector v1's browser UI is the fallback. Type
`localhost` exactly wherever it appears: `127.0.0.1` is another host to the
service (refused, 421) and another origin to the sign-in.

```
[claude] A1. On the branch under trial: `git status` is clean and `.venv/bin/pytest -q` passes.
[munir]  A2. On the laptop, first (the download can outlast the sign-in window):
             npm view @modelcontextprotocol/inspector version
             npx @modelcontextprotocol/inspector@<that version> --help
             Use that exact version in A5 too, and note it for A10. Note the command-line options for the
             server URL, the client id and secret, and the callback URL (default
             http://127.0.0.1:6276/oauth/callback).
[munir]  A3. In an SSH session on the box, in the repo:
             .venv/bin/python scripts/local_trial.py --callback http://127.0.0.1:6276/oauth/callback
             It prints the socket, the tunnel command, a client id and secret, and a passphrase.
             Leave it running; the sign-in window it opens lasts ten minutes from now.
[munir]  A4. On the laptop, the printed tunnel: ssh -N -L 8765:<socket> openclaw-vps
             The laptop's port must be 8765, the address the trial was built with.
[munir]  A5. On the laptop, Inspector's command line (options as --help named them in A2) with server URL
             http://localhost:8765/mcp, the printed client id and secret, and the callback URL from A3,
             listing the tools and then calling list_repos.
[munir]  A6. The browser opens the sign-in page naming "MCP Inspector (local trial)". Type the printed
             passphrase and tap "Sign in" once: a second tap loses the sign-in (Plan, Open Questions).
             After the ten minutes the page answers "Not Found": start A3 again.
[munir]  A7. Inspector shows list_repos, titled "List repos" and read-only, and its answer:
             "You can read these repos: claude-config (read only)."
[munir]  A8. Stop the trial with Ctrl-C first, so its passphrase and secret are dead, then paste Inspector's
             output and the trial's terminal output to the session. If the SSH session dropped instead, the
             folder was left behind: rm -rf /tmp/workspace-mcp-trial-*
[claude] A9. In the trial's output: "alert (trial, not sent)" lines for the window opened (from the server)
             and for the sign-in; one `tool call` record (user munir, tool list_repos); no traceback, and the
             passphrase only in the script's own banner.
[claude] A10. Record in Plan.md, with Inspector's version: Basic or form secret, `resource`, protocol
             revision, `Origin`. These are Inspector's habits, not Claude's; Claude's are seen in Phase 2.
```

Fallback, Inspector v1's browser UI (0.14.1 or later, which fixes its 2025
remote-execution advisory): in A2 run
`npx @modelcontextprotocol/inspector@v1-latest` and open exactly
`http://localhost:6274` (not `127.0.0.1`); in A3 start the trial with
`--callback http://localhost:6274/oauth/callback --origin http://localhost:6274`;
in A5 choose Streamable HTTP, a direct connection, URL
`http://localhost:8765/mcp`, and enter the client id, the client secret and the
redirect URL in its authentication settings. If it has no client-secret field,
`/token` answers `invalid_client`: use the command line instead.

### B. The sandbox trial (sudo)

A throwaway unit with the service's sandbox options (Plan, "systemd unit"),
running as a throwaway user of its own, never as root, tries each thing D-6
relies on. Its push test uses a throwaway key on a scratch repo.

```
[munir]  B1. GitHub: a private scratch repo, e.g. munirche/workspace-mcp-scratch, with one commit.
[munir]  B2. sudo install -d -m 700 -o root -g root /root/workspace-mcp-trial
             sudo ssh-keygen -q -t ed25519 -N "" -C workspace-mcp-trial -f /root/workspace-mcp-trial/key
[munir]  B3. sudo cat /root/workspace-mcp-trial/key.pub
             Add it to the scratch repo only, as a deploy key with write access.
[munir]  B4. ssh-keyscan -t ed25519 github.com | sudo tee /root/workspace-mcp-trial/known_hosts
             sudo ssh-keygen -lf /root/workspace-mcp-trial/known_hosts
             Compare the fingerprint with GitHub's page "GitHub's SSH key fingerprints"; stop if different.
[munir]  B5. sudo install -d -m 755 -o root -g root /usr/local/lib/workspace-mcp-trial
             sudo install -m 755 -o root -g root deploy/sandbox_trial.py /usr/local/lib/workspace-mcp-trial/
             sudo less /usr/local/lib/workspace-mcp-trial/sandbox_trial.py      (read the copy root will run)
[claude] B6. ip -4 addr show scope global; ip -6 addr show scope global
             The box's public IPv4 and IPv6 addresses (not secrets), for B7.
             ss -ltn '( sport = :18789 or sport = :2222 )'
             Both ports listen, so a "blocked" in B7 is the sandbox's doing, not an empty port.
[munir]  B7. Run, as one command, with <ipv6> from B6. It binds this checkout read-only at /srv/trial, so
             the unit also runs the development venv beside the throwaway key: acceptable, since it runs as
             a throwaway user and the key reaches the scratch repo only.
             sudo systemd-run --unit=workspace-mcp-trial --wait --pipe --collect \
               -p DynamicUser=yes -p Group=www-data \
               -p StateDirectory=workspace-mcp-trial -p StateDirectoryMode=0700 \
               -p RuntimeDirectory=workspace-mcp-trial -p RuntimeDirectoryMode=0750 -p UMask=0077 \
               -p Environment=HOME=/var/lib/workspace-mcp-trial \
               -p ProtectSystem=strict -p ProtectHome=yes -p TemporaryFileSystem=/repos:ro \
               -p BindReadOnlyPaths=/home/munir/GitRepos/claude-config:/repos/claude-config \
               -p BindReadOnlyPaths=/home/munir/GitRepos/Workspace-MCP:/srv/trial \
               -p ProtectProc=invisible -p ProcSubset=pid \
               -p "IPAddressDeny=localhost link-local 15.204.210.85 <ipv6>" \
               -p "IPAddressAllow=127.0.0.53 127.0.0.54" \
               -p MemoryMax=512M -p TasksMax=64 -p CPUQuota=100% -p NoNewPrivileges=yes \
               -p PrivateTmp=yes -p PrivateDevices=yes -p ProtectKernelTunables=yes \
               -p ProtectKernelModules=yes -p ProtectKernelLogs=yes -p ProtectControlGroups=yes \
               -p ProtectClock=yes -p ProtectHostname=yes -p RestrictNamespaces=yes \
               -p RestrictRealtime=yes -p RestrictSUIDSGID=yes -p LockPersonality=yes \
               -p MemoryDenyWriteExecute=yes -p SystemCallArchitectures=native \
               -p SystemCallFilter=@system-service -p CapabilityBoundingSet= \
               -p "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX" \
               -p LoadCredential=key:/root/workspace-mcp-trial/key \
               -p LoadCredential=known_hosts:/root/workspace-mcp-trial/known_hosts \
               /usr/bin/python3 -I /usr/local/lib/workspace-mcp-trial/sandbox_trial.py \
               git@github.com:munirche/workspace-mcp-scratch.git 15.204.210.85 <ipv6>
[munir]  B8. When it prints "listening ... for 120 s", in a second session:
             sudo -u www-data python3 -c "import socket; s = socket.socket(socket.AF_UNIX); s.connect('/run/workspace-mcp-trial/mcp.sock'); print('reached')"
             python3 -c "import socket; s = socket.socket(socket.AF_UNIX); s.connect('/run/workspace-mcp-trial/mcp.sock')"
             The first prints "reached"; the second, as munir, fails with PermissionError.
[munir]  B9. Paste the trial's output, and the two results of B8, to the session.
[claude] B10. Record each line in Plan.md (D-6's list, "systemd unit") as works or changed, with the
             reason, and propose the unit change for anything CHANGED.
[munir]  B11. Remove the deploy key from the scratch repo (or delete the repo), then:
             sudo rm -r /root/workspace-mcp-trial /usr/local/lib/workspace-mcp-trial
             sudo rm -rf /var/lib/private/workspace-mcp-trial /var/lib/workspace-mcp-trial
             systemctl status workspace-mcp-trial    (no such unit)
```
