"""The one place that runs git."""

import os
import re
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

MAX_TEXT = 4096
MAX_BYTES = 8 * 1024 * 1024
DRIVER_NAME = re.compile(r"[A-Za-z0-9_.-]+")
LOOSE_OBJECTS = re.compile(r"[0-9a-f]{2}")

# Every program a repo's own settings could name is switched off on the
# command line, which git reads after the repo's config and so wins.
OVERRIDES = (
    "core.fsmonitor=false",
    "core.hooksPath=/dev/null",
    "core.pager=cat",
    "core.editor=false",
    "core.sshCommand=false",
    "core.askPass=",
    "core.attributesFile=/dev/null",
    "core.excludesFile=/dev/null",
    "credential.helper=",
    "diff.external=",
    "log.showSignature=false",
    "log.follow=false",
    "gpg.program=false",
    "gpg.openpgp.program=false",
    "gpg.ssh.program=false",
    "gpg.x509.program=false",
    "protocol.allow=never",
    "safe.bareRepository=explicit",
    "mailmap.file=",
    "mailmap.blob=",
    "submodule.recurse=false",
    "grep.fallbackToNoIndex=false",
)
ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_LITERAL_PATHSPECS": "1",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
}
REFUSED_FILES = ("commondir", "objects/info/alternates", "objects/info/http-alternates")
# "hook" names programs by config from git 2.54 on; core.hooksPath does not reach them.
REFUSED_SECTIONS = ("include", "includeif", "hook")
REFUSED_KEYS = ("core.worktree", "extensions.worktreeconfig", "extensions.partialclone")


class GitError(Exception):
    pass


class GitTimeout(GitError):
    pass


class RepoRefused(GitError):
    pass


@dataclass(frozen=True)
class Output:
    items: tuple
    truncated: bool


@dataclass(frozen=True)
class Match:
    path: str
    line: int
    text: str


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str
    author: str
    subject: str


class Git:
    def __init__(self, repo, home, timeout, log_max, git="/usr/bin/git", max_bytes=MAX_BYTES):
        self._repo = Path(repo)
        if not self._repo.is_absolute():
            raise ValueError("the repo's path must be absolute")
        self._git_dir = self._repo / ".git"
        self._home = Path(home)
        self._timeout = timeout
        self._log_max = log_max
        self._git = git
        self._max_bytes = max_bytes

    def ls_files(self, path=None):
        output = self._run("ls-files", ["ls-files", "-z", "--full-name", "--", *self._paths(path)])
        return Output(tuple(_text(item) for item in _whole(output)), output.truncated)

    def grep(self, query, path=None, fixed=True):
        _check_text(query)
        args = ["grep", "-z", "-n", "-I", "--full-name", "--no-color", "--no-column", "--no-textconv",
                "--no-recurse-submodules", "-F" if fixed else "-E", "-e", query, "--", *self._paths(path)]
        output = self._run("grep", args, codes=(0, 1))
        return Output(tuple(_matches(output.stdout)), output.truncated)

    def log(self, count, path=None):
        if type(count) is not int or not 1 <= count <= self._log_max:
            raise ValueError(f"the number of commits must be a whole number from 1 to {self._log_max}")
        args = ["log", "-z", f"--max-count={count}", "--no-color", "--no-mailmap", "--no-show-signature",
                "--no-decorate", "--no-notes", "--no-ext-diff", "--no-textconv", "--encoding=UTF-8",
                "--format=%H%x00%aI%x00%an%x00%s", "--", *self._paths(path)]
        output = self._run("log", args)
        fields = [_text(item) for item in output.stdout.split(b"\0")]
        if output.truncated:
            fields.pop()
        commits = [Commit(*fields[start:start + 4]) for start in range(0, len(fields) - 3, 4)]
        return Output(tuple(commits), output.truncated)

    def status(self):
        args = ["status", "--porcelain=v2", "-z", "--branch", "--untracked-files=no", "--ignore-submodules=all",
                "--no-renames"]
        output = self._run("status", args)
        return Output(tuple(_text(item) for item in _whole(output)), output.truncated)

    def _paths(self, path):
        if path is None:
            return []
        _check_text(path)
        return [path]

    def _run(self, operation, args, codes=(0,)):
        # One deadline for the settings check and the operation together (R-3.4).
        deadline = time.monotonic() + self._timeout
        drivers = self._refuse_or_list_drivers(deadline, operation)
        argv = [self._git, f"--git-dir={self._git_dir}", f"--work-tree={self._repo}", "--no-pager",
                "-c", f"safe.directory={self._repo}"]
        for override in OVERRIDES + drivers:
            argv += ["-c", override]
        result = self._spawn(argv + args, self._repo, operation, self._max_bytes, deadline)
        if result.code not in codes and not result.truncated:
            raise GitError(f"git {operation} failed")
        return result

    def _refuse_or_list_drivers(self, deadline, operation):
        git_dir = self._git_dir
        try:
            if not stat.S_ISDIR(os.lstat(git_dir).st_mode):
                raise RepoRefused("the repo's version-control folder is not a plain folder")
            if _links_inside(git_dir):
                raise RepoRefused("the repo's version-control folder links outside itself")
            if any(os.path.lexists(git_dir / name) for name in REFUSED_FILES):
                raise RepoRefused("the repo borrows another repo's files")
        except OSError:
            raise RepoRefused("the repo's version-control folder could not be checked") from None
        listing = self._spawn(
            [self._git, "config", "--no-includes", "--file", str(git_dir / "config"), "--list", "-z"],
            self._home, operation, MAX_BYTES, deadline,
        )
        if listing.code != 0 or listing.truncated:
            raise RepoRefused("the repo's settings could not be read")
        drivers = []
        for entry in _split(listing.stdout):
            key = entry.split(b"\n", 1)[0].decode("utf-8", "replace")
            section, _, rest = key.partition(".")
            subsection, _, name = rest.rpartition(".")
            lowered = f"{section}.{name}".lower()
            if section.lower() in REFUSED_SECTIONS or lowered in REFUSED_KEYS:
                raise RepoRefused("the repo's settings point outside it")
            if section.lower() == "remote" and name.lower() == "promisor":
                raise RepoRefused("the repo's settings point outside it")
            if section.lower() in ("filter", "diff") and subsection:
                if not DRIVER_NAME.fullmatch(subsection):
                    raise RepoRefused("the repo names a driver that cannot be switched off")
                drivers += _driver_overrides(section.lower(), subsection)
        return tuple(dict.fromkeys(drivers))

    def _spawn(self, argv, cwd, operation, max_bytes, deadline):
        environment = dict(ENVIRONMENT, HOME=str(self._home), GIT_CEILING_DIRECTORIES=str(self._home.parent))
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            raise GitError(f"git {operation} could not start") from None
        chunks, size, truncated = [], 0, False
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise GitTimeout(f"git {operation} timed out")
                    if not selector.select(remaining):
                        continue
                    data = os.read(process.stdout.fileno(), 65536)
                    if not data:
                        break
                    if size + len(data) > max_bytes:
                        chunks.append(data[: max_bytes - size])
                        truncated = True
                        break
                    chunks.append(data)
                    size += len(data)
            try:
                code = process.wait(timeout=max(deadline - time.monotonic(), 0) if not truncated else 0)
            except subprocess.TimeoutExpired:
                if not truncated:
                    raise GitTimeout(f"git {operation} timed out") from None
                code = None
        finally:
            _kill_group(process)
            process.stdout.close()
        return _Result(b"".join(chunks), code, truncated)


@dataclass(frozen=True)
class _Result:
    stdout: bytes
    code: int | None
    truncated: bool


def _kill_group(process):
    # A child of git may outlive it and hold the pipe, so the whole session goes.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    process.wait()


def _links_inside(folder, top=True):
    # Every link under .git is refused, except that loose-object folders are
    # checked themselves but not entered: they hold thousands of files.
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_symlink():
                return True
            if not entry.is_dir(follow_symlinks=False):
                continue
            if top and entry.name == "modules":
                continue
            if os.path.basename(folder) == "objects" and LOOSE_OBJECTS.fullmatch(entry.name):
                continue
            if _links_inside(entry.path, top=False):
                return True
    return False


def _driver_overrides(section, name):
    if section == "filter":
        return [f"filter.{name}.clean=", f"filter.{name}.smudge=", f"filter.{name}.process=",
                f"filter.{name}.required=false"]
    return [f"diff.{name}.command=", f"diff.{name}.textconv="]


def _check_text(value):
    if not isinstance(value, str):
        raise TypeError("expected text")
    if not value or "\0" in value or "\n" in value or "\r" in value or len(value) > MAX_TEXT:
        raise ValueError(f"text must be one line of 1 to {MAX_TEXT} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("text must be valid Unicode") from None


def _text(raw):
    return raw.decode("utf-8", "replace")


def _split(raw):
    return [item for item in raw.split(b"\0") if item]


def _whole(output):
    # A cut answer's last item may be cut too, so it is left out.
    items = _split(output.stdout)
    return items[:-1] if output.truncated else items


def _matches(raw):
    position, found = 0, []
    while position < len(raw):
        path_end = raw.find(b"\0", position)
        line_end = raw.find(b"\0", path_end + 1)
        text_end = raw.find(b"\n", line_end + 1)
        if -1 in (path_end, line_end, text_end):
            break
        found.append(Match(_text(raw[position:path_end]), int(raw[path_end + 1:line_end]),
                           _text(raw[line_end + 1:text_end])))
        position = text_end + 1
    return found
