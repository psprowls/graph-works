"""Landed sibling evidence uses verified real Git history."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from graph_works_core.workspace import provenance
from graph_works_core.workspace.landed import landed_siblings, landed_since, stale_by_path, stale_spec_for
from graph_works_core.workspace.layout import layout_for
from okf_io import load_bundle
from work_tracker_okf.items import IGNORE, SpecBaseline, load_items

SUBJECT = "work/epic-a/children/feature-b"
SIBLING = "work/epic-a/children/feature-a"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    file = repo / "packages/a" / f"{name}.py"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(name + "\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def evidence(tmp_path: Path):
    repo = tmp_path / "code"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    c0, c1 = _commit(repo, "first"), _commit(repo, "second")
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "workspace.yaml").write_text(
        f"version: 1\nrepositories:\n  code:\n    path: {repo}\n", encoding="utf-8", newline="\n"
    )
    layout = layout_for(root)
    for path, type_, extra in (
        ("work/epic-a", "Epic", "repo: code\n"),
        (
            SUBJECT,
            "Feature",
            f"spec_baseline:\n  code: {c0}\ndepends_on:\n- path: {SIBLING}\n  blocks: execute\n  needs: resolved\n",
        ),
        (SIBLING, "Feature", f"resolved_in: {c1}\n"),
    ):
        page = layout.bundle_dir / f"{path}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        status = "resolved" if path == SIBLING else "open"
        page.write_text(
            f"---\ntype: {type_}\ntitle: {path}\ndescription: d\nstatus: stable\nwork_status: {status}\n"
            "phase: plan\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n"
            f"affects: [packages/a]\n{extra}---\n",
            encoding="utf-8",
            newline="\n",
        )
    items = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
    subject = next(i for i in items if i.path == SUBJECT)
    return layout, items, subject, repo, c0, c1


def test_a_sibling_resolved_at_the_baseline_is_not_an_entry(evidence):
    layout, items, subject, _, _, c1 = evidence
    subject = replace(subject, spec_baseline=SpecBaseline(code=c1))
    assert landed_since(layout, items, subject).entries == ()


def test_a_sibling_resolved_after_with_overlap_is_stale(evidence):
    layout, items, subject, *_ = evidence
    result = landed_since(layout, items, subject)
    assert [e.sibling.path for e in result.entries] == [SIBLING]
    assert result.entries[0].overlaps and result.stale == (SIBLING,)
    assert stale_spec_for(layout, items, subject) == (SIBLING,)
    assert stale_by_path(layout, items, (SUBJECT, SIBLING, "missing")) == {SUBJECT: (SIBLING,)}


def test_a_declared_dependency_without_overlap_is_an_entry_not_stale(evidence):
    layout, items, subject, *_ = evidence
    items = tuple(replace(i, affects=("packages/b",)) if i.path == SIBLING else i for i in items)
    result = landed_since(layout, items, subject)
    assert result.entries and not result.entries[0].overlaps and result.stale == ()


@pytest.mark.parametrize("ref", ["https://github.com/o/r/pull/1", "f" * 40])
def test_unresolvable_resolved_in_is_a_warning_not_an_entry(evidence, ref):
    layout, items, subject, *_ = evidence
    items = tuple(replace(i, resolved_in=ref) if i.path == SIBLING else i for i in items)
    result = landed_since(layout, items, subject)
    assert result.entries == () and any(ref in w for w in result.warnings) and result.stale == ()


def test_no_baseline_makes_no_git_calls(evidence, monkeypatch):
    layout, items, subject, *_ = evidence

    def boom(*args, **kwargs):
        raise AssertionError("unexpected Git call")

    monkeypatch.setattr(provenance, "probe_git", boom)
    monkeypatch.setattr(provenance, "run_git", boom)
    assert landed_since(layout, items, replace(subject, spec_baseline=None)).entries == ()


@pytest.mark.parametrize("phase", ["design", "execute"])
def test_stale_spec_for_is_empty_off_plan(evidence, phase):
    layout, items, subject, *_ = evidence
    assert stale_spec_for(layout, items, replace(subject, phase=phase)) == ()


def test_landed_siblings_selection_is_unchanged(evidence):
    _, items, subject, *_ = evidence
    assert [s.path for s in landed_siblings(items, subject)] == [SIBLING]
    assert landed_siblings(items, replace(subject, dependency_edges=(), affects=("gw:workspace",))) == ()


def test_comparison_failure_warns_without_authorizing_staleness(evidence, monkeypatch):
    layout, items, subject, *_ = evidence
    original = provenance.probe_git

    def probe(repo, *args, **kwargs):
        if args[:2] == ("merge-base", "--is-ancestor"):
            return provenance.GitOutcome(None, "", "timeout")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", probe)
    result = landed_since(layout, items, subject)
    assert result.entries == () and result.stale == ()
    assert any("timeout" in warning for warning in result.warnings)


def test_a_sibling_resolved_strictly_before_the_baseline_is_not_an_entry(evidence):
    layout, items, subject, repo, _, c1 = evidence
    later = _commit(repo, "third")
    assert later != c1
    assert landed_since(layout, items, replace(subject, spec_baseline=SpecBaseline(code=later))).entries == ()


@pytest.mark.parametrize("ref_kind", ["landed", "ancestor", "invalid", "comparison_failure"])
def test_shared_resolved_in_is_probed_once_per_call(evidence, monkeypatch, ref_kind):
    layout, items, subject, _, c0, c1 = evidence
    ref = {"landed": c1, "ancestor": c0, "invalid": "missing", "comparison_failure": c1}[ref_kind]
    sibling = next(i for i in items if i.path == SIBLING)
    items = tuple(replace(i, resolved_in=ref) if i.path == SIBLING else i for i in items)
    items += (replace(sibling, path="work/epic-a/children/feature-c", resolved_in=ref),)
    original = provenance.probe_git
    calls = []

    def probe(repo, *args, **kwargs):
        calls.append(args)
        if ref_kind == "comparison_failure" and args[0] == "merge-base":
            return provenance.GitOutcome(None, "", "timeout")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", probe)
    result = landed_since(layout, items, subject)
    assert sum(args[0] == "cat-file" for args in calls) == 1
    assert sum(args[0] == "merge-base" for args in calls) == (0 if ref_kind == "invalid" else 1)
    assert len(result.entries) == (2 if ref_kind == "landed" else 0)
    assert len(result.warnings) == (2 if ref_kind in ("invalid", "comparison_failure") else 0)
