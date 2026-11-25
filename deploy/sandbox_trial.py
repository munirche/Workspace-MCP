#!/usr/bin/python3 -I
"""T1.11's sandbox trial: run by systemd-run with the service's sandbox options, it tries each thing D-6 needs."""

import hashlib
import os
import socket
import stat
import subprocess
import sys
import time

GITHUB = ("github.com", 22)
TELEGRAM = ("api.telegram.org", 443)
GUARDED_PORTS = (18789, 2222)
VENV_PYTHON = "/srv/trial/.venv/bin/python"


def check(name, expected, attempt):
    try:
        detail = attempt()
    except Exception as error:
        outcome, detail = False, f"{type(error).__name__}: {error}"
    else:
        outcome = True
    print(f"{'works  ' if outcome == expected else 'CHANGED'} {name}: {detail}", flush=True)


def connect(host, port):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.settimeout(5)
        probe.connect((host, port))
    return f"connected to {host}:{port}"


def blocked(host, port):
    # Only the sandbox's own refusal counts: "nothing listening" is not proof.
    try:
        connect(host, port)
    except PermissionError as error:
        return f"blocked by the sandbox ({type(error).__name__})"
    except OSError as error:
        raise RuntimeError(f"not blocked by the sandbox ({type(error).__name__})") from None
    raise RuntimeError("connected")


def reachable_sockets():
    found = []
    for folder, _, names in os.walk("/run"):
        for name in names:
            path = os.path.join(folder, name)
            try:
                if not stat.S_ISSOCK(os.lstat(path).st_mode):
                    continue
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(1)
                    probe.connect(path)
                found.append(path)
            except OSError:
                continue
    return ", ".join(found) or "none"


def run(*command, **kwargs):
    done = subprocess.run(command, capture_output=True, text=True, timeout=60, **kwargs)
    if done.returncode:
        raise RuntimeError(f"exit {done.returncode}: {done.stderr.strip()[-300:]}")
    return done.stdout.strip()[-200:]


def main(argv):
    if len(argv) not in (3, 4) or os.geteuid() == 0:
        print("usage: sandbox_trial.py <scratch-remote> <public-ipv4> [<public-ipv6>], never as root",
              file=sys.stderr)
        return 2
    remote, public = argv[1], argv[2:]
    state, runtime = os.environ["STATE_DIRECTORY"], os.environ["RUNTIME_DIRECTORY"]
    credentials = os.environ.get("CREDENTIALS_DIRECTORY", "")

    check("the repo bind resolves under ProtectHome=yes", True,
          lambda: f"{len(os.listdir('/repos/claude-config'))} entries in /repos/claude-config")
    repos = os.listdir("/repos")
    check("only the configured repo is under /repos", True, lambda: str(repos) if repos == ["claude-config"] else 1 / 0)
    check("a tracked file of the bound repo reads", True,
          lambda: f"{len(open('/repos/claude-config/README.md', 'rb').read())} bytes of README.md")
    check("git reads the bound repo as another user", True,
          lambda: run("git", "-c", "safe.directory=/repos/claude-config", "-C", "/repos/claude-config",
                      "log", "-1", "--format=%h", env=dict(os.environ, HOME=state)))
    check("no home folder is visible", False, lambda: os.listdir("/home/munir"))
    check("other users' processes are invisible", True,
          lambda: "only own" if all(os.stat(f"/proc/{pid}").st_uid == os.getuid()
                                    for pid in os.listdir("/proc") if pid.isdigit()) else 1 / 0)
    control = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    control.bind(("127.0.0.1", 0))
    control.listen()
    check("loopback is blocked even to the unit's own listener", True,
          lambda: blocked("127.0.0.1", control.getsockname()[1]))
    control.close()
    for host in ("127.0.0.1", "::1", *public):
        for port in GUARDED_PORTS:
            check(f"{host}:{port} is blocked", True, lambda host=host, port=port: blocked(host, port))
    check("DNS resolves", True, lambda: socket.getaddrinfo("api.github.com", 443)[0][4][0])
    check("GitHub's SSH port is reachable", True, lambda: connect(*GITHUB))
    check("Telegram's API is reachable", True, lambda: connect(*TELEGRAM))
    check("the system Python starts under MemoryDenyWriteExecute (this script is running)", True,
          lambda: sys.version.split()[0])
    check("scrypt at N=2^17 fits MemoryMax", True, lambda: hashlib.scrypt(
        b"trial", salt=b"s" * 16, n=2**17, r=8, p=1, maxmem=256 * 1024 * 1024, dklen=32).hex()[:8])
    check("the service's venv imports under MemoryDenyWriteExecute", True,
          lambda: run(VENV_PYTHON, "-I", "-c", "import mcp, uvicorn, pydantic_core, sqlite3; print('ok')"))
    check("/etc is read-only", False, lambda: open("/etc/workspace-mcp-trial-probe", "w"))
    check("the state folder is writable", True,
          lambda: open(os.path.join(state, "probe"), "w").write("x") and "written")
    check("the runtime folder is 0750", True,
          lambda: oct(os.stat(runtime).st_mode & 0o777) if os.stat(runtime).st_mode & 0o777 == 0o750 else 1 / 0)

    ssh = (f"ssh -i {credentials}/key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes "
           f"-o UserKnownHostsFile={credentials}/known_hosts")
    clone = os.path.join(state, "scratch")
    env = dict(os.environ, HOME=state, GIT_SSH_COMMAND=ssh, GIT_TERMINAL_PROMPT="0")
    check("git clones the scratch repo over ssh", True, lambda: run("git", "clone", "-q", remote, clone, env=env)
          or "cloned")
    branch = f"trial-{int(time.time())}"

    def push():
        with open(os.path.join(clone, f"{branch}.md"), "w") as note:
            note.write("Written by the sandbox trial.\n")
        run("git", "-C", clone, "-c", "user.name=Workspace MCP trial", "-c", "user.email=trial@invalid",
            "checkout", "-q", "-b", branch, env=env)
        run("git", "-C", clone, "add", f"{branch}.md", env=env)
        run("git", "-C", clone, "-c", "user.name=Workspace MCP trial", "-c", "user.email=trial@invalid",
            "commit", "-q", "-m", "sandbox trial", env=env)
        return run("git", "-C", clone, "push", "-q", "origin", branch, env=env) or f"pushed {branch}"

    check("git pushes a branch with the throwaway key", True, push)

    print(f"info    Unix sockets this unit can connect to (for T2.2): {reachable_sockets()}", flush=True)
    sock_path = os.path.join(runtime, "mcp.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(sock_path)
    # As uvicorn does: under UMask=0077 the socket would be owner-only, and the
    # folder's 0750 with group www-data is then the guard being tried.
    os.chmod(sock_path, 0o666)
    print(f"info    socket {oct(os.stat(sock_path).st_mode & 0o777)}, folder "
          f"{oct(os.stat(runtime).st_mode & 0o777)} group {os.stat(runtime).st_gid}", flush=True)
    listener.listen()
    listener.settimeout(120)
    print(f"listening on {sock_path} for 120 s: run the two connection checks in DEPLOY.md now", flush=True)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            connection, _ = listener.accept()
        except TimeoutError:
            break
        print("works   a connection reached the socket", flush=True)
        connection.close()
    listener.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
