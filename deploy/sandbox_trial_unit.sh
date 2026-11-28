#!/bin/bash
# Starts sandbox_trial.py in a throwaway unit with the service's sandbox options (DEPLOY.md, B7).
set -euo pipefail
if [ "$#" -lt 2 ]; then
    echo "usage: $0 <scratch repo ssh address> <public address>..." >&2
    exit 2
fi
remote=$1
shift
exec systemd-run --unit=workspace-mcp-trial --wait --pipe --collect \
    -p DynamicUser=yes -p Group=www-data \
    -p StateDirectory=workspace-mcp-trial -p StateDirectoryMode=0700 \
    -p RuntimeDirectory=workspace-mcp-trial -p RuntimeDirectoryMode=0750 -p UMask=0077 \
    -p Environment=HOME=/var/lib/workspace-mcp-trial \
    -p ProtectSystem=strict -p ProtectHome=yes -p TemporaryFileSystem=/repos:ro \
    -p BindReadOnlyPaths=/home/munir/GitRepos/claude-config:/repos/claude-config \
    -p BindReadOnlyPaths=/home/munir/GitRepos/Workspace-MCP:/srv/trial \
    -p ProtectProc=invisible -p ProcSubset=pid \
    -p IPAddressDeny=localhost -p IPAddressDeny=link-local -p "IPAddressDeny=$*" \
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
    /usr/bin/python3 -I /usr/local/lib/workspace-mcp-trial/sandbox_trial.py "$remote" "$@"
