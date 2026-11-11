"""Structural checks: spec-driven traceability per claude-config/SDD.md, and agent models."""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "Docs"
AGENTS = REPO_ROOT / ".claude" / "agents"
MODULE = "workspace_mcp"
CRITERION = re.compile(r"\bR-\d+\.\d+\b")
CRITERION_LINE = re.compile(r"^- (R-\d+\.\d+) ", re.M)


def _sdd_modules():
    return [
        spec.parent.name
        for spec in sorted(DOCS.glob("*/Spec.md"))
        if CRITERION_LINE.search(spec.read_text(encoding="utf-8"))
    ]


MODULES = _sdd_modules()


def _spec_path(module):
    return DOCS / module / "Spec.md"


def _tasks_path(module):
    return DOCS / module / "Tasks.md"


def _tests_dir(module):
    return REPO_ROOT / "tests" / module


def _status(path):
    return next(
        (line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("Status:")),
        "",
    )


def _spec_criteria(module):
    defined, manual = set(), set()
    for line in _spec_path(module).read_text(encoding="utf-8").splitlines():
        match = re.match(r"- (R-\d+\.\d+) ", line)
        if not match:
            continue
        defined.add(match.group(1))
        if "[MANUAL]" in line:
            manual.add(match.group(1))
    return defined, manual


def _claimed_criteria(module):
    claimed = {}
    for path in sorted(_tests_dir(module).rglob("test_*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "Covers:" not in line:
                continue
            for criterion in CRITERION.findall(line.split("Covers:", 1)[1]):
                claimed.setdefault(criterion, set()).add(path.name)
    return claimed


def _phases(module):
    phases, heading, tasks, body = [], None, [], []
    for line in _tasks_path(module).read_text(encoding="utf-8").splitlines():
        if line.startswith("## Phase"):
            if heading:
                phases.append((heading, tasks, "\n".join(body)))
            heading, tasks, body = line, [], []
        elif heading:
            body.append(line)
            if line.startswith("- ["):
                tasks.append((line.startswith("- [x]"), set(CRITERION.findall(line))))
    if heading:
        phases.append((heading, tasks, "\n".join(body)))
    return phases


def _done_phase_criteria(module):
    return {
        criterion
        for _, tasks, _ in _phases(module)
        if tasks and all(ticked for ticked, _ in tasks)
        for _, cited in tasks
        for criterion in cited
    }


def _task_owned_criteria(module):
    owned = set()
    for line in _tasks_path(module).read_text(encoding="utf-8").splitlines():
        if line.startswith("- ["):
            owned.update(CRITERION.findall(line))
    return owned


def test_the_three_documents_exist():
    for name in ("Spec.md", "Plan.md", "Tasks.md"):
        assert (DOCS / MODULE / name).exists(), f"Docs/{MODULE}/{name} is missing"


def test_approved_tasks_have_a_spec_with_criteria():
    # Every check below runs only for a discovered module; without this, approving
    # tasks over an empty spec would pass them all with nothing examined.
    if "draft, not approved" in _status(_tasks_path(MODULE)):
        return
    assert MODULE in MODULES, f"Tasks.md is approved but Docs/{MODULE}/Spec.md defines no criterion"


@pytest.mark.parametrize("module", MODULES)
def test_every_spec_module_has_tasks_and_a_test_directory(module):
    assert _tasks_path(module).exists(), f"{module} has a Spec.md but no Tasks.md"
    assert _tests_dir(module).is_dir(), f"{module} has a Spec.md but no tests/{module}/ directory"


@pytest.mark.parametrize("module", MODULES)
def test_no_test_claims_a_criterion_that_does_not_exist(module):
    defined, _ = _spec_criteria(module)
    orphans = {
        criterion: sorted(files)
        for criterion, files in _claimed_criteria(module).items()
        if criterion not in defined
    }
    assert not orphans, (
        f"{module}: tests cite criteria that Spec.md does not define: {orphans}. "
        f"Either the criterion was renamed and the test not updated, or the id is a typo."
    )


@pytest.mark.parametrize("module", MODULES)
def test_every_criterion_of_a_finished_phase_has_a_test(module):
    defined, manual = _spec_criteria(module)
    claimed = _claimed_criteria(module)
    expected = _done_phase_criteria(module) & defined
    unproven = sorted(c for c in expected if c not in manual and c not in claimed)
    assert not unproven, (
        f"{module}: criteria cited by a finished phase with no test naming them: {unproven}. "
        f"Add a test with `Covers: {unproven[0]}` or move the task back to unticked."
    )


@pytest.mark.parametrize("module", MODULES)
def test_every_criterion_is_owned_by_a_task(module):
    # The other checks run task -> test and test -> spec, so a criterion added to the
    # spec and given to no task is invisible to both and simply never gets built.
    defined, _ = _spec_criteria(module)
    unowned = sorted(defined - _task_owned_criteria(module))
    assert not unowned, (
        f"{module}: criteria in Spec.md that no task in Tasks.md claims: {unowned}. "
        f"Add each to the task that builds it, or take it out of the spec."
    )


@pytest.mark.parametrize("module", MODULES)
def test_a_finished_phase_records_its_verification(module):
    unrecorded = [
        heading
        for heading, tasks, body in _phases(module)
        if tasks
        and all(ticked for ticked, _ in tasks)
        and ("spec-verifier at phase end" in body or "_(pending)_" in body)
    ]
    assert not unrecorded, (
        f"{module}: phases with every task ticked but no verification record: {unrecorded}. "
        f"Run the spec-verifier in a fresh session and paste its report into the record."
    )


@pytest.mark.parametrize("module", MODULES)
def test_the_spec_does_not_call_itself_a_draft_once_a_phase_is_closed(module):
    closed = "verified and closed" in _tasks_path(module).read_text(encoding="utf-8")
    spec_status = _status(_spec_path(module))
    if closed:
        assert "draft, not approved" not in spec_status, (
            f"{module}: Tasks.md records a closed phase but Spec.md still says "
            f"'{spec_status}'. Update the spec's status to match."
        )


def test_every_project_agent_names_its_own_model():
    # An agent with no model, or `inherit`, runs on whatever the main session is that day.
    offenders = {}
    for path in sorted(AGENTS.glob("*.md")):
        declared = re.search(r"^model:\s*([^\s#]+)", path.read_text(encoding="utf-8"), re.M)
        if declared is None:
            offenders[path.name] = "names no model"
        elif declared.group(1) == "inherit":
            offenders[path.name] = "says inherit"
    assert not offenders, (
        f"project agents that ride on the main session's model: {offenders}. "
        f"Give each one a model chosen for what it does, per claude-config's README."
    )
