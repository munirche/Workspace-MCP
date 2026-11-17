"""The one place that runs git: caller text is data, a time limit, and nothing a repo's own settings name ever runs."""

import os
import pty
import stat
import subprocess
import time
from pathlib import Path

import pytest

from workspace_mcp import git
from workspace_mcp.git import Git, GitError, GitTimeout, RepoRefused

GIT = "/usr/bin/git"
SECRET = "Qz" + "7secr" + "et"


def _environment(home):
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.test",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.test",
    }


class Plain:
    """Plain git, as an attacker's victim would run it: the positive control for every seed."""

    def __init__(self, home):
        self.home = home

    def __call__(self, *args, cwd, check=True, stdin=None, stdout=subprocess.PIPE, extra=None):
        environment = _environment(self.home) | (extra or {})
        return subprocess.run([GIT, *args], cwd=cwd, env=environment, check=check, stdin=stdin, stdout=stdout,
                              stderr=subprocess.PIPE, timeout=30)


@pytest.fixture
def home(tmp_path):
    folder = tmp_path / "home"
    folder.mkdir()
    return folder


@pytest.fixture
def plain(home):
    return Plain(home)


@pytest.fixture
def canary(tmp_path):
    return tmp_path / "canary"


def _repo(plain, root, files=None):
    root.mkdir(parents=True, exist_ok=True)
    plain("init", "-q", "-b", "main", cwd=root)
    for name, content in (files or {"README.md": "hello\n"}).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    plain("add", "-A", cwd=root)
    plain("commit", "-q", "-m", "first", cwd=root)
    return root


def _git(repo, home, **options):
    return Git(repo, home, timeout=options.pop("timeout", 15), log_max=50, **options)


def _script(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def _touch_files(repo):
    # A changed modification time makes git read the file again, through any filter.
    later = time.time() + 5
    for path in repo.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            os.utime(path, (later, later))


def _every_operation(runner):
    for call in (runner.status, runner.ls_files, lambda: runner.grep("hello"), lambda: runner.log(5),
                 lambda: runner.log(5, "README.md")):
        try:
            call()
        except GitError:
            pass


@pytest.fixture
def repo(tmp_path, plain):
    return _repo(plain, tmp_path / "repo", {"README.md": "hello\n", "docs/a b.md": "two words\n",
                                            "data.txt": "plain line\n"})


# R-3.3: caller text is only ever data.

TRICKS = [
    "--open-files-in-pager=touch {canary}",
    "-O" + "touch {canary}",
    "--output={canary}",
    "--no-index",
    "-v",
    "-e",
    "--",
    ":(exclude)*",
    ":/",
    ":",
    "*",
    "?",
    "[a]",
    "$(touch {canary})",
    ";touch {canary}",
    "|touch {canary}",
    "`touch {canary}`",
    "-",
]


@pytest.mark.parametrize("trick", TRICKS)
def test_a_search_query_is_only_text(trick, tmp_path, plain, home, canary):
    """Covers: R-3.3"""
    query = trick.format(canary=canary)
    root = _repo(plain, tmp_path / "searched", {"lines.txt": f"before\n{query}\nafter\n", "other.txt": "nothing\n"})
    found = _git(root, home).grep(query).items
    assert [(match.path, match.text) for match in found] == [("lines.txt", query)]
    assert not canary.exists()


@pytest.mark.parametrize("trick", TRICKS + ["-o", "--others", "--output"])
def test_a_path_is_only_a_literal_path(trick, repo, home, canary):
    """Covers: R-3.3"""
    path = trick.format(canary=canary)
    runner = _git(repo, home)
    assert runner.ls_files(path).items == ()
    assert runner.grep("hello", path).items == ()
    assert runner.log(5, path).items == ()
    assert not canary.exists()


def test_a_path_outside_the_repo_is_refused_by_git(repo, home):
    """Covers: R-3.3"""
    with pytest.raises(GitError):
        _git(repo, home).ls_files("../outside")


def test_a_path_with_special_characters_is_found_as_written(tmp_path, plain, home):
    """Covers: R-3.3"""
    names = ["*.md", "[a].md", "-n.md", ":(top)x.md", "a b.md", "?.md"]
    root = _repo(plain, tmp_path / "special", {name: f"in {name}\n" for name in names} | {"z.md": "z\n"})
    runner = _git(root, home)
    for name in names:
        assert runner.ls_files(name).items == (name,)
        assert [match.path for match in runner.grep("in", name).items] == [name]


@pytest.mark.parametrize("count", [0, -1, 51, True, False, "5", 5.0, None])
def test_a_count_must_be_a_whole_number_within_the_limit(count, repo, home):
    """Covers: R-3.3"""
    with pytest.raises(ValueError):
        _git(repo, home).log(count)


@pytest.mark.parametrize("text", ["a\0b", "a\nb", "a\rb", "x" * 4097, "", "\udcff"])
def test_text_that_is_not_one_short_line_is_refused(text, repo, home):
    """Covers: R-3.3"""
    runner = _git(repo, home)
    for call in (lambda: runner.grep(text), lambda: runner.ls_files(text), lambda: runner.log(1, text)):
        with pytest.raises(ValueError):
            call()


@pytest.mark.parametrize("text", [None, 5, b"README.md"])
def test_text_must_be_text(text, repo, home):
    """Covers: R-3.3"""
    with pytest.raises(TypeError):
        _git(repo, home).grep(text)


def test_the_operations_return_what_git_holds(repo, home, plain):
    """Covers: R-3.3"""
    runner = _git(repo, home)
    assert runner.ls_files().items == ("README.md", "data.txt", "docs/a b.md")
    assert runner.ls_files("docs").items == ("docs/a b.md",)
    [match] = runner.grep("two").items
    assert (match.path, match.line, match.text) == ("docs/a b.md", 1, "two words")
    [commit] = runner.log(5).items
    head = plain("rev-parse", "HEAD", cwd=repo).stdout.decode().strip()
    assert (commit.sha, commit.author, commit.subject) == (head, "Test", "first")
    (repo / "README.md").write_text("changed\n")
    entries = runner.status().items
    assert "# branch.head main" in entries
    assert any(entry.startswith("1 .M") and entry.endswith(" README.md") for entry in entries)


@pytest.mark.parametrize("limit", [12, 15, 20])
def test_output_beyond_the_byte_limit_is_cut_and_said_to_be(limit, repo, home):
    """Covers: R-3.3"""
    output = _git(repo, home, max_bytes=limit).ls_files()
    assert output.truncated
    assert set(output.items) <= {"README.md", "data.txt", "docs/a b.md"}


def test_a_cut_history_holds_only_whole_commits(repo, home, plain):
    """Covers: R-3.3"""
    for number in range(3):
        plain("commit", "-q", "--allow-empty", "-m", f"commit {number}", cwd=repo)
    newest, second = _git(repo, home).log(2).items
    sizes = [len("\0".join([c.sha, c.date, c.author, c.subject]).encode()) + 1 for c in (newest, second)]
    # Cut inside the second commit's fields, inside its subject, then inside the first's subject.
    for limit, subjects in ((sizes[0] + 50, ["commit 2"]), (sum(sizes) - 2, ["commit 2"]), (sizes[0] - 3, [])):
        output = _git(repo, home, max_bytes=limit).log(10)
        assert output.truncated
        assert [commit.subject for commit in output.items] == subjects


def test_a_matched_line_may_hold_any_byte(tmp_path, plain, home):
    """Covers: R-3.3"""
    root = _repo(plain, tmp_path / "bytes", {"a.txt": "x" * 9000 + "\nneedle \0 inside\nneedle again\n"})
    matches = _git(root, home).grep("needle").items
    assert [(match.line, match.text) for match in matches] == [(2, "needle \0 inside"), (3, "needle again")]


def test_a_repo_path_must_be_absolute(home):
    """Covers: R-3.10"""
    with pytest.raises(ValueError):
        Git("repo", home, timeout=15, log_max=50)


# R-3.4: a time limit, and the whole process group stopped at it.

def test_an_operation_that_runs_too_long_is_stopped(repo, home, tmp_path):
    """Covers: R-3.4"""
    marker = tmp_path / "grandchild.pid"
    fake = _script(tmp_path / "slow-git", f"""
case " $* " in *" config "*) exit 0 ;; esac
sleep 60 &
echo $! > {marker}
sleep 60
""")
    started = time.monotonic()
    with pytest.raises(GitTimeout, match="timed out"):
        _git(repo, home, timeout=1, git=str(fake)).status()
    assert time.monotonic() - started < 5
    grandchild = int(marker.read_text())
    for _ in range(50):
        if not Path(f"/proc/{grandchild}").exists() or "Z" in _state(grandchild):
            break
        time.sleep(0.1)
    assert not Path(f"/proc/{grandchild}").exists() or "Z" in _state(grandchild)


def _state(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return "Z"


def test_the_settings_check_counts_against_the_time_limit(repo, home, tmp_path):
    """Covers: R-3.4"""
    fake = _script(tmp_path / "steady-git", "sleep 0.7\nexit 0\n")
    with pytest.raises(GitTimeout):
        _git(repo, home, timeout=1, git=str(fake)).ls_files()


def test_a_git_that_cannot_start_says_so_plainly(repo, home, tmp_path):
    """Covers: R-3.4"""
    missing = tmp_path / "no-such-git"
    with pytest.raises(GitError) as caught:
        _git(repo, home, git=str(missing)).ls_files()
    assert str(caught.value) == "git ls-files could not start"


def test_a_child_that_holds_the_output_open_cannot_hold_the_call(repo, home, tmp_path):
    """Covers: R-3.4"""
    fake = _script(tmp_path / "leaky-git", """
case " $* " in *" config "*) exit 0 ;; esac
sleep 60 &
exit 0
""")
    with pytest.raises(GitTimeout):
        _git(repo, home, timeout=1, git=str(fake)).ls_files()


# R-3.9: nothing a repo's own settings, attributes or hooks name is ever run.

def _set(plain, repo, key, value):
    plain("config", key, value, cwd=repo)


def _assert_seed(canary, control, runner, prime=lambda: None):
    # Priming again before the runner's turn, since plain git may have spent
    # the seed (a refreshed index no longer reads the touched files).
    prime()
    control()
    assert canary.exists(), "the seed did nothing under plain git, so the test would prove nothing"
    canary.unlink()
    prime()
    _every_operation(runner)
    assert not canary.exists()


def test_a_repo_fsmonitor_is_not_run(repo, plain, home, canary, tmp_path):
    """Covers: R-3.9"""
    hook = _script(tmp_path / "fsmonitor", f"touch {canary}\n")
    _set(plain, repo, "core.fsmonitor", str(hook))
    _assert_seed(canary, lambda: plain("status", cwd=repo), _git(repo, home))


def test_a_repo_hook_is_not_run(repo, plain, home, canary, tmp_path):
    """Covers: R-3.9"""
    _script(repo / ".git" / "hooks" / "post-index-change", f"touch {canary}\n")
    _assert_seed(canary, lambda: plain("status", cwd=repo), _git(repo, home), prime=lambda: _touch_files(repo))


def test_a_repo_hooks_path_is_not_run(repo, plain, home, canary, tmp_path):
    """Covers: R-3.9"""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    _script(hooks / "post-index-change", f"touch {canary}\n")
    _set(plain, repo, "core.hooksPath", str(hooks))
    _assert_seed(canary, lambda: plain("status", cwd=repo), _git(repo, home), prime=lambda: _touch_files(repo))


@pytest.mark.parametrize("where", [".gitattributes", ".git/info/attributes"])
@pytest.mark.parametrize("kind", ["clean", "process"])
def test_a_filter_the_repo_names_is_not_run(where, kind, tmp_path, plain, home, canary):
    """Covers: R-3.9"""
    root = _repo(plain, tmp_path / "filtered", {"a.txt": "a\n"})
    attributes = root / where
    attributes.parent.mkdir(exist_ok=True)
    attributes.write_text("*.txt filter=evil\n")
    if where == ".gitattributes":
        plain("add", ".gitattributes", cwd=root)
        plain("commit", "-q", "-m", "attributes", cwd=root)
    command = f"sh -c 'touch {canary}; cat'" if kind == "clean" else f"sh -c 'touch {canary}'"
    _set(plain, root, f"filter.evil.{kind}", command)
    _set(plain, root, "filter.evil.required", "true")
    _assert_seed(canary, lambda: plain("status", cwd=root, check=False), _git(root, home),
                 prime=lambda: _touch_files(root))


def test_a_diff_driver_the_repo_names_is_not_run(tmp_path, plain, home, canary):
    """Covers: R-3.9"""
    root = _repo(plain, tmp_path / "diffed", {".gitattributes": "*.txt diff=evil\n", "a.txt": "a\n"})
    (root / "a.txt").write_text("b\n")
    plain("commit", "-q", "-am", "second", cwd=root)
    _set(plain, root, "diff.evil.textconv", f"sh -c 'touch {canary}; cat \"$1\"' -")
    _set(plain, root, "diff.evil.command", f"sh -c 'touch {canary}'")
    _assert_seed(canary, lambda: plain("log", "-p", cwd=root), _git(root, home))


def test_an_external_diff_is_not_run(repo, plain, home, canary):
    """Covers: R-3.9"""
    _set(plain, repo, "diff.external", f"sh -c 'touch {canary}'")
    (repo / "README.md").write_text("changed\n")
    _assert_seed(canary, lambda: plain("diff", cwd=repo), _git(repo, home))


def test_a_global_settings_file_is_never_read(tmp_path, plain, home, canary):
    """Covers: R-3.9, R-3.10"""
    root = _repo(plain, tmp_path / "globally", {".gitattributes": "*.txt filter=evil\n", "a.txt": "a\n"})
    plain("config", "--file", str(home / ".gitconfig"), "filter.evil.clean", f"sh -c 'touch {canary}; cat'",
          cwd=root)

    def control():
        environment = _environment(home)
        del environment["GIT_CONFIG_GLOBAL"]
        subprocess.run([GIT, "status"], cwd=root, env=environment, capture_output=True, timeout=30)

    _assert_seed(canary, control, _git(root, home), prime=lambda: _touch_files(root))


def test_an_attributes_file_outside_the_repo_is_never_read(repo, plain, home, outside):
    """Covers: R-3.10"""
    attributes = outside / "attributes"
    attributes.write_text("*.md binary\n")
    _set(plain, repo, "core.attributesFile", str(attributes))
    assert plain("grep", "-I", "-e", "hello", cwd=repo, check=False).stdout == b""
    assert [match.path for match in _git(repo, home).grep("hello").items] == ["README.md"]


def test_a_mailmap_outside_the_repo_is_never_read(repo, plain, home, outside):
    """Covers: R-3.10"""
    mailmap = outside / "mailmap"
    mailmap.write_text(SECRET + " <test@example.test>\n")
    _set(plain, repo, "mailmap.file", str(mailmap))
    assert SECRET in plain("log", "--format=%aN", cwd=repo).stdout.decode()
    [commit] = _git(repo, home).log(1).items
    assert commit.author == "Test"


def test_a_signature_program_is_not_run(tmp_path, plain, home, canary):
    """Covers: R-3.9"""
    # The fake reads all git writes to it first, or git's write could fail on a closed pipe.
    gpg = _script(tmp_path / "gpg", f"""
cat > /dev/null
for argument in "$@"; do
  if [ "$argument" = "--verify" ]; then touch {canary}; exit 1; fi
done
printf '\\n[GNUPG:] SIG_CREATED \\n' >&2
printf -- '-----BEGIN PGP SIGNATURE-----\\n\\nAAAA\\n-----END PGP SIGNATURE-----\\n'
""")
    root = tmp_path / "signed"
    root.mkdir()
    plain("init", "-q", "-b", "main", cwd=root)
    (root / "a.txt").write_text("a\n")
    plain("add", "a.txt", cwd=root)
    plain("-c", f"gpg.program={gpg}", "commit", "-q", "-S", "-m", "signed", cwd=root)
    _set(plain, root, "gpg.program", str(gpg))
    _set(plain, root, "log.showSignature", "true")
    _assert_seed(canary, lambda: plain("log", cwd=root), _git(root, home))


def test_a_pager_is_not_run(repo, plain, home, canary):
    """Covers: R-3.9"""
    _set(plain, repo, "core.pager", f"touch {canary}; cat")

    def control():
        # A pager runs only on a terminal, so the control gets one.
        leader, follower = pty.openpty()
        try:
            plain("log", cwd=repo, stdout=follower)
        finally:
            os.close(follower)
            os.close(leader)

    _assert_seed(canary, control, _git(repo, home))


def test_an_editor_is_not_run(repo, plain, home, canary):
    """Covers: R-3.9"""
    _set(plain, repo, "core.editor", f"touch {canary}; true")
    _assert_seed(canary, lambda: plain("commit", "--allow-empty", cwd=repo, check=False), _git(repo, home))


def test_an_ssh_command_is_not_run(repo, plain, home, canary):
    """Covers: R-3.9"""
    _set(plain, repo, "core.sshCommand", f"touch {canary}; false")
    _assert_seed(canary, lambda: plain("ls-remote", "ssh://example.invalid/x", cwd=repo, check=False),
                 _git(repo, home))


def test_a_credential_helper_is_not_run(repo, plain, home, canary):
    """Covers: R-3.9"""
    _set(plain, repo, "credential.helper", f"!touch {canary}; true")
    request = b"protocol=https\nhost=example.invalid\n\n"

    def control():
        subprocess.run([GIT, "credential", "fill"], cwd=repo, env=_environment(home) | {"GIT_TERMINAL_PROMPT": "0"},
                       input=request, capture_output=True, timeout=30)

    _assert_seed(canary, control, _git(repo, home))


def test_a_submodule_is_never_entered(tmp_path, plain, home, canary):
    """Covers: R-3.9"""
    inner = _repo(plain, tmp_path / "inner", {"a.txt": "a\n"})
    root = _repo(plain, tmp_path / "outer")
    plain("-c", "protocol.file.allow=always", "submodule", "-q", "add", str(inner), "sub", cwd=root)
    plain("commit", "-q", "-m", "submodule", cwd=root)
    sub = root / "sub"
    (sub / ".gitattributes").write_text("*.txt filter=evil\n")
    module_git = root / ".git" / "modules" / "sub"
    plain("--git-dir", str(module_git), "config", "filter.evil.clean", f"sh -c 'touch {canary}; cat'", cwd=tmp_path)
    # Same size, so git must read the file, through the submodule's own filter.
    (sub / "a.txt").write_text("b\n")
    _assert_seed(canary, lambda: plain("status", cwd=root), _git(root, home), prime=lambda: _touch_files(sub))


def test_an_embedded_bare_repo_is_never_used(repo, plain, home, canary):
    """Covers: R-3.9"""
    bare = repo / "evil.git"
    plain("init", "-q", "--bare", str(bare), cwd=repo)
    plain("--git-dir", str(bare), "config", "core.sshCommand", f"touch {canary}; false", cwd=repo)
    _assert_seed(canary, lambda: plain("ls-remote", "ssh://example.invalid/x", cwd=bare, check=False),
                 _git(repo, home))
    runner = _git(repo, home)
    runner.ls_files("evil.git")
    runner.grep("bare", "evil.git")
    assert not canary.exists()


# R-3.10: a repo's own settings never reach outside it.

@pytest.fixture
def outside(tmp_path):
    folder = tmp_path / "outside"
    folder.mkdir()
    (folder / "secret.txt").write_text(SECRET + "\n")
    return folder


def _assert_refused(runner):
    for call in (runner.status, runner.ls_files, lambda: runner.grep(SECRET), lambda: runner.log(5)):
        with pytest.raises(RepoRefused) as caught:
            call()
        assert SECRET not in str(caught.value)


def test_a_work_tree_set_elsewhere_is_refused(repo, plain, home, outside):
    """Covers: R-3.10"""
    _set(plain, repo, "core.worktree", str(outside))
    shown = plain("--git-dir", str(repo / ".git"), "grep", "--untracked", "-e", SECRET, cwd=outside, check=False)
    assert SECRET in shown.stdout.decode()
    _assert_refused(_git(repo, home))


@pytest.mark.parametrize("section", ["include.path", "includeIf.gitdir:/.path"])
def test_settings_included_from_elsewhere_are_refused(section, repo, plain, home, outside, canary, tmp_path):
    """Covers: R-3.9, R-3.10"""
    hook = _script(tmp_path / "fsmonitor", f"touch {canary}\n")
    included = outside / "included.config"
    included.write_text(f"[core]\n\tfsmonitor = {hook}\n")
    _set(plain, repo, section, str(included))
    plain("status", cwd=repo)
    assert canary.exists()
    canary.unlink()
    _assert_refused(_git(repo, home))
    assert not canary.exists()


def test_borrowed_objects_are_refused(repo, plain, home, tmp_path):
    """Covers: R-3.10"""
    other = _repo(plain, tmp_path / "other", {"secret.txt": SECRET + "\n"})
    blob = plain("rev-parse", "HEAD:secret.txt", cwd=other).stdout.decode().strip()
    (repo / ".git" / "objects" / "info" / "alternates").write_text(str(other / ".git" / "objects") + "\n")
    assert SECRET in plain("cat-file", "-p", blob, cwd=repo).stdout.decode()
    _assert_refused(_git(repo, home))


def test_a_git_folder_elsewhere_is_refused(tmp_path, plain, home):
    """Covers: R-3.10"""
    other = _repo(plain, tmp_path / "other", {"secret.txt": SECRET + "\n"})
    root = tmp_path / "pointer"
    root.mkdir()
    (root / ".git").write_text(f"gitdir: {other / '.git'}\n")
    assert "secret.txt" in plain("ls-files", cwd=root).stdout.decode()
    _assert_refused(_git(root, home))


def test_a_shared_common_folder_is_refused(repo, plain, home, tmp_path):
    """Covers: R-3.10"""
    other = _repo(plain, tmp_path / "other", {"secret.txt": SECRET + "\n"})
    (repo / ".git" / "commondir").write_text(str(other / ".git") + "\n")
    _assert_refused(_git(repo, home))


@pytest.mark.parametrize("key", ["extensions.worktreeConfig", "remote.origin.promisor", "extensions.partialClone"])
def test_settings_that_reach_elsewhere_are_refused(key, repo, plain, home):
    """Covers: R-3.10"""
    plain("config", "core.repositoryformatversion", "1", cwd=repo)
    _set(plain, repo, key, "true" if key != "extensions.partialClone" else "origin")
    _assert_refused(_git(repo, home))


@pytest.mark.parametrize("name", ["config", "objects", "refs", "info", "index", "HEAD", "packed-refs",
                                  "info/attributes", "info/exclude", "objects/pack", "refs/heads/main",
                                  "refs/tags", "objects/ab", "objects/info/packs", "logs"])
def test_a_linked_part_of_the_git_folder_is_refused(name, repo, plain, home, outside):
    """Covers: R-3.10"""
    plain("pack-refs", "--all", cwd=repo)
    part = repo / ".git" / name
    part.parent.mkdir(exist_ok=True)
    moved = outside / name.replace("/", "_")
    if part.exists():
        part.rename(moved)
    else:
        moved.write_text("")
    part.symlink_to(moved)
    _assert_refused(_git(repo, home))


def test_a_linked_git_folder_is_refused(repo, home, outside):
    """Covers: R-3.10"""
    (repo / ".git").rename(outside / "moved.git")
    (repo / ".git").symlink_to(outside / "moved.git")
    _assert_refused(_git(repo, home))


def test_a_linked_settings_file_is_followed_by_plain_git(repo, plain, home, outside, canary, tmp_path):
    """Covers: R-3.9, R-3.10"""
    hook = _script(tmp_path / "fsmonitor", f"touch {canary}\n")
    config = repo / ".git" / "config"
    linked = outside / "config"
    linked.write_text(config.read_text() + f"[core]\n\tfsmonitor = {hook}\n")
    config.unlink()
    config.symlink_to(linked)
    plain("status", cwd=repo)
    assert canary.exists()
    canary.unlink()
    _assert_refused(_git(repo, home))
    assert not canary.exists()


@pytest.mark.parametrize("name", ["evil=x", "evil x", 'evil"x', "évil"])
def test_a_driver_that_cannot_be_switched_off_is_refused(name, repo, home):
    """Covers: R-3.9"""
    written = name.replace("\\", "\\\\").replace('"', '\\"')
    with (repo / ".git" / "config").open("a") as config:
        config.write(f'[filter "{written}"]\n\tclean = false\n')
    _assert_refused(_git(repo, home))


def test_settings_that_name_a_program_for_a_hook_are_refused(repo, plain, home, canary):
    """Covers: R-3.9"""
    _set(plain, repo, "hook.audit.command", f"touch {canary}")
    _set(plain, repo, "hook.audit.event", "post-index-change")
    _assert_refused(_git(repo, home))


def test_a_git_folder_that_cannot_be_read_is_refused(repo, home):
    """Covers: R-3.10"""
    info = repo / ".git" / "info"
    info.mkdir(exist_ok=True)
    info.chmod(0)
    try:
        _assert_refused(_git(repo, home))
    finally:
        info.chmod(0o755)


def test_an_unreadable_settings_file_is_refused(repo, home):
    """Covers: R-3.10"""
    (repo / ".git" / "config").write_text("[core\n")
    _assert_refused(_git(repo, home))


def test_a_failure_says_nothing_of_git_s_output(tmp_path, plain, home, caplog):
    """Covers: R-3.3"""
    root = tmp_path / "empty"
    root.mkdir()
    plain("init", "-q", "-b", "main", cwd=root)
    with pytest.raises(GitError) as caught:
        _git(root, home).log(1)
    assert str(caught.value) == "git log failed"
    assert caught.value.__cause__ is None
    assert not caplog.records


def test_git_runs_with_nothing_inherited(repo, home, tmp_path, monkeypatch):
    """Covers: R-3.9"""
    seen, arguments = tmp_path / "seen", tmp_path / "arguments"
    fake = _script(tmp_path / "env-git", f"env > {seen}\nprintf '%s\\0' \"$@\" > {arguments}\n")
    monkeypatch.setenv("GIT_DIR", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.fsmonitor'='true'")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "touch")
    _git(repo, home, git=str(fake)).ls_files()
    environment = seen.read_text().splitlines()
    names = {line.split("=", 1)[0] for line in environment if "=" in line}
    assert names <= set(git.ENVIRONMENT) | {"HOME", "GIT_CEILING_DIRECTORIES", "PWD", "SHLVL", "_", "OLDPWD"}
    assert f"HOME={home}" in environment
    argv = arguments.read_text().split("\0")
    assert argv[:3] == [f"--git-dir={repo / '.git'}", f"--work-tree={repo}", "--no-pager"]
    assert ["-c", "core.fsmonitor=false"] in [argv[index:index + 2] for index in range(len(argv))]
