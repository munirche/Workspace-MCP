"""The repo guards itself: no tracked credential, the assistant's deny rules, pinned and audited dependencies."""

import json
import os
import re
import subprocess
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"
LOCK = REPO_ROOT / "requirements.lock"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

# Each vendor prefix carries a body of the vendor's length, so prose that only
# names a prefix never matches. The passphrase hash is D-4's form with any
# non-empty base64 fields, a superset of what the configuration accepts.
RULES = {
    "private key block": re.compile(
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----[\s\S]{0,300}?[A-Za-z0-9+/]{40}"
    ),
    "AWS access key": re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
    "GitHub token": re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{36,}"),
    "GitHub fine-grained token": re.compile(r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{80,}"),
    "Anthropic key": re.compile(r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{40,}"),
    "OpenAI scoped key": re.compile(r"(?<![A-Za-z0-9_-])sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{40,}"),
    "OpenAI key": re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9]{48}(?![A-Za-z0-9])"),
    "Slack token": re.compile(r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9-]{20,}"),
    "Slack app token": re.compile(r"(?<![A-Za-z0-9])xapp-[A-Za-z0-9-]{20,}"),
    "Google API key": re.compile(r"(?<![A-Za-z0-9_-])AIza[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"),
    "Google OAuth client secret": re.compile(r"(?<![A-Za-z0-9_-])GOCSPX-[A-Za-z0-9_-]{28}(?![A-Za-z0-9_-])"),
    "Telegram bot token": re.compile(r"(?<![A-Za-z0-9])[0-9]{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"),
    "Stripe live key": re.compile(r"(?<![A-Za-z0-9_])(?:sk|rk)_live_[A-Za-z0-9]{24,}"),
    "passphrase hash": re.compile(r"scrypt\$[0-9]+\$[0-9]+\$[0-9]+\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+"),
}

# Every clause of R-9.4: environment files, the key folder, any process's
# environment, service definitions, and running as administrator.
REQUIRED_DENY = {
    "Read(//etc/*.env)",
    "Read(//etc/*.env.*)",
    "Bash(cat /etc/*.env*)",
    "Read(//etc/workspace-mcp/**)",
    "Read(//proc/**/environ)",
    "Bash(* /proc/*/environ*)",
    "Bash(env)",
    "Bash(printenv*)",
    "Bash(export -p*)",
    "Bash(export)",
    "Bash(declare -x*)",
    "Bash(declare -p*)",
    "Bash(declare)",
    "Bash(set)",
    "Bash(systemctl cat *)",
    "Bash(systemctl show *)",
    "Read(//etc/systemd/system/**)",
    "Bash(sudo *)",
}

# Files that hold a credential by their nature, mirroring .gitignore: a random
# secret has no vendor shape, so its file's name is what gives it away.
CREDENTIAL_FILE = re.compile(r"(?:^|/)(?:\.env(?:\.(?!example$)[^/]+)?|[^/]+\.(?:pem|key))$")

LOCK_INSTALL = "pip install --require-hashes --only-binary :all: -r requirements.lock"
AUDIT = "pip-audit --require-hashes --disable-pip -r requirements.lock"
TRIGGERS = """on:
  push:
    branches: [master]
  pull_request:
  schedule:
    - cron: '17 6 * * 1'"""


def _scan(path):
    text = path.read_bytes().decode("utf-8", "replace")
    return [
        (text.count("\n", 0, match.start()) + 1, rule)
        for rule, pattern in RULES.items()
        for match in pattern.finditer(text)
    ]


def _tracked_files(home):
    listed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
        env={
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "LC_ALL": "C",
        },
    ).stdout
    return [REPO_ROOT / name for name in listed.decode("utf-8", "surrogateescape").split("\0") if name]


def _samples():
    # Built from pieces at run time, so this file itself holds no credential.
    return {
        "private key block": "-----BEGIN " + "OPENSSH PRIVATE KEY-----\n" + "b3Bl" * 20 + "\n",
        "AWS access key": "AK" + "IA" + "Q7" * 8,
        "GitHub token": "gh" + "p_" + "a1" * 18,
        "GitHub fine-grained token": "github" + "_pat_" + "B2" * 41,
        "Anthropic key": "sk-" + "ant-api03-" + "c3" * 45,
        "OpenAI scoped key": "sk-" + "svcacct-" + "d4" * 30,
        "OpenAI key": "sk" + "-" + "e5" * 24,
        "Slack token": "xo" + "xb-" + "1234567890-" + "f6" * 12,
        "Slack app token": "xa" + "pp-1-" + "A1B2" * 6,
        "Google API key": "AI" + "za" + "g7" * 17 + "h",
        "Google OAuth client secret": "GOC" + "SPX-" + "m2" * 14,
        "Telegram bot token": "12345" + "678:" + "AA" + "i8" * 16 + "j",
        "Stripe live key": "sk" + "_live_" + "k9" * 12,
        "passphrase hash": "scr" + "ypt$16384$8$1$" + "bG9u" * 4 + "$" + "aGFz" * 8,
    }


def _workflow_lines():
    return [line for line in WORKFLOW.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]


def _workflow_steps():
    steps, current = [], None
    for line in _workflow_lines():
        if re.match(r"^\s{6}- ", line):
            current = [line]
            steps.append(current)
        elif current is not None and (line.startswith(" " * 8) or not line.strip()):
            current.append(line)
        else:
            current = None
    return ["\n".join(step) for step in steps]


def _lock_logical_lines():
    logical, pending = [], ""
    for raw in LOCK.read_text(encoding="utf-8").splitlines():
        line = re.sub(r"(^|\s)#.*", "", raw).strip()
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        line = (pending + line).strip()
        pending = ""
        if line:
            logical.append(line)
    assert not pending, "requirements.lock ends inside a continued line"
    return logical


def _lock_pins():
    pins = {}
    for line in _lock_logical_lines():
        name, _, version = line.split()[0].partition("==")
        if version:
            pins[name] = version
    return pins


def test_no_tracked_file_holds_a_credential(tmp_path):
    """Covers: R-9.3"""
    found = [
        f"{path.relative_to(REPO_ROOT)}:{line}: {rule}"
        for path in _tracked_files(tmp_path)
        if path.is_file() and not path.is_symlink()
        for line, rule in _scan(path)
    ]
    assert not found, f"tracked files hold something credential-shaped: {found}"


def test_no_tracked_file_is_a_credential_file(tmp_path):
    """Covers: R-9.3"""
    names = [path.relative_to(REPO_ROOT).as_posix() for path in _tracked_files(tmp_path)]
    assert not [name for name in names if CREDENTIAL_FILE.search(name)]
    flagged = ["a/.env", ".env.local", "deploy/server.pem", "keys/vault.key"]
    passed = [".env.example", "docs/env.md", "keyring.py", "a/.envrc.md"]
    assert [name for name in flagged + passed if CREDENTIAL_FILE.search(name)] == flagged


def test_the_scan_finds_each_kind_of_credential(tmp_path):
    """Covers: R-9.3"""
    samples = _samples()
    planted = tmp_path / "planted.txt"
    planted.write_text("".join(f"value: {value}\n" for value in samples.values()), encoding="utf-8")
    assert {rule for _, rule in _scan(planted)} == set(samples)


def test_the_scan_ignores_prose_that_names_a_prefix(tmp_path):
    """Covers: R-9.3"""
    prose = tmp_path / "prose.md"
    prose.write_text(
        "Keys look like AKIA..., ghp_..., github_pat_..., sk-ant-..., sk-proj-..., sk-..., "
        "xoxb-..., AIza..., sk_live_..., 123456789:<token>, scrypt$<n>$<r>$<p>$<salt>$<hash>, "
        "and a -----BEGIN " + "PRIVATE KEY----- line.\n",
        encoding="utf-8",
    )
    assert _scan(prose) == []


def test_the_shared_settings_hold_only_deny_rules():
    """Covers: R-9.4"""
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    assert set(settings) == {"permissions"}, f"unexpected settings: {sorted(set(settings) - {'permissions'})}"
    assert set(settings["permissions"]) == {"deny"}, (
        f"permissions other than deny: {sorted(set(settings['permissions']) - {'deny'})}"
    )


def test_the_shared_settings_deny_each_way_to_a_secret():
    """Covers: R-9.4"""
    deny = set(json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["deny"])
    missing = sorted(REQUIRED_DENY - deny)
    assert not missing, f".claude/settings.json no longer denies: {missing}"


def test_every_dependency_is_pinned_by_version_and_hash():
    """Covers: R-9.5"""
    lines = _lock_logical_lines()
    assert "--only-binary :all:" in lines
    # Faults name the entry's position only: a wrong line could carry an index
    # address with a password in it, and CI logs are public.
    faults = []
    for number, line in enumerate(lines, 1):
        if line == "--only-binary :all:":
            continue
        requirement, *options = line.split()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9.+!]+", requirement):
            faults.append(f"entry {number}: not an exact pin")
        elif not options:
            faults.append(f"entry {number}: no hash")
        if not all(re.fullmatch(r"--hash=sha256:[0-9a-f]{64}", option) for option in options):
            faults.append(f"entry {number}: an option other than a sha256 hash")
    assert not faults, faults


def test_what_is_installed_is_what_is_pinned():
    """Covers: R-9.5"""
    pins = _lock_pins()
    assert pins, "requirements.lock pins nothing"
    drift = {}
    for name, version in pins.items():
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = None
        if installed != version:
            drift[name] = (version, installed)
    assert not drift, f"pinned versus installed: {drift}"


def test_ci_runs_on_every_change_and_weekly():
    """Covers: R-9.5"""
    text = "\n".join(_workflow_lines())
    triggers = re.search(r"^on:\n(?:[ \t]+\S.*\n|\s*\n)*", text + "\n", re.M)
    assert triggers and triggers.group(0).strip() == TRIGGERS, "the triggers changed"
    assert not re.search(r"^    if:", text, re.M), "the job has a condition"
    assert "continue-on-error" not in text


def test_ci_installs_the_lock_as_the_deploy_does():
    """Covers: R-9.5"""
    assert any(line.strip() == LOCK_INSTALL for line in _workflow_lines())


def test_ci_fails_on_a_known_advisory_even_when_tests_fail():
    """Covers: R-9.5"""
    audits = [step for step in _workflow_steps() if "pip-audit" in step]
    assert len(audits) == 1, "expected one audit step"
    step = audits[0]
    assert re.search(rf"^\s+run: {re.escape(AUDIT)}$", step, re.M), "the audit command changed"
    assert re.search(r"^\s+if: \$\{\{ !cancelled\(\) \}\}$", step, re.M), "the audit is skipped when tests fail"
    keys = set(re.findall(r"^ {6}- ([\w-]+):|^ {8}([\w-]+):", step, re.M))
    assert {key for pair in keys for key in pair if key} == {"name", "if", "run"}, "the audit step changed"
