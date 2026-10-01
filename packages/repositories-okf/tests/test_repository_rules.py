from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from gitrepo import git, make_upstream
from okf_io import load, load_bundle, validate
from repositories_okf.git import Git, clone_partial, detach, worktree_add
from repositories_okf.repository import CODES, RepositoryFacts, gather, repository_rule

SHA = "c" * 40
PAGE = (
    "---\n"
    "type: {type}\n"
    "title: {name}\n"
    "description: d\n"
    "url: https://example.com/{name}.git\n"
    "track: main\n"
    "pin:\n"
    "  commit: {sha}\n"
    "  fetched_at: '2026-09-29T20:40:00Z'\n"
    "---\n\n## Summary\n\nx\n"
)

CLEAN = RepositoryFacts(
    name="demo",
    type="ManagedRepository",
    url="https://example.com/demo.git",
    track="main",
    pin=SHA,
    clone_present=True,
    declared=True,
    detached=True,
    clean=True,
    origin="https://example.com/demo.git",
    checkout="/ws/.gw/worktrees/demo/main",
    checkout_linked=True,
    checkout_branch="main",
    ahead=0,
)


def _findings(
    tmp_path: Path, facts: RepositoryFacts, *, codes: frozenset[str] | None = None, body: str = ""
) -> list[tuple[str, str, str | None]]:
    lane = tmp_path / "repositories"
    lane.mkdir(parents=True, exist_ok=True)
    (lane / f"{facts.name}.md").write_text(
        PAGE.format(type=facts.type, name=facts.name, sha=SHA), encoding="utf-8", newline=""
    )
    if body:
        (tmp_path / "linker.md").write_text(f"---\ntitle: l\n---\n\n{body}\n", encoding="utf-8", newline="")
    report = validate(
        load_bundle(tmp_path), today=date(2026, 9, 29), extra_rules=(repository_rule((facts,), codes=codes),)
    )
    return [(f.code, f.severity, f.path) for f in report.findings if f.code.startswith("repository.")]


def test_the_codes_are_the_design_table() -> None:
    assert CODES == (
        "repository.not-detached",
        "repository.dirty",
        "repository.url-mismatch",
        "repository.worktree-in-bundle",
        "repository.checkout-undeclared",
        "repository.checkout-invalid",
        "repository.unmaterialized",
        "repository.behind-track",
    )


def test_a_clean_managed_repository_is_silent(tmp_path: Path) -> None:
    assert _findings(tmp_path, CLEAN) == []


@pytest.mark.parametrize(
    ("change", "code", "severity"),
    [
        ({"detached": False}, "repository.not-detached", "error"),
        ({"clean": False}, "repository.dirty", "error"),
        ({"origin": "https://example.com/elsewhere.git"}, "repository.url-mismatch", "error"),
        ({"origin": None}, "repository.url-mismatch", "error"),
        ({"worktrees_in_bundle": ("repositories/demo/wt",)}, "repository.worktree-in-bundle", "error"),
        (
            {"checkout": None, "checkout_linked": None, "checkout_branch": None},
            "repository.checkout-undeclared",
            "error",
        ),
        (
            {"checkout": None, "declared": False, "checkout_linked": None, "checkout_branch": None},
            "repository.checkout-undeclared",
            "error",
        ),
        ({"checkout_linked": False}, "repository.checkout-invalid", "error"),
        ({"checkout_branch": "feature"}, "repository.checkout-invalid", "error"),
        ({"ahead": 3}, "repository.behind-track", "warn"),
    ],
)
def test_each_code_fires_alone(tmp_path: Path, change: dict[str, object], code: str, severity: str) -> None:
    assert _findings(tmp_path, replace(CLEAN, **change)) == [(code, severity, "repositories/demo.md")]


def test_unmaterialized_counts_broken_links_into_the_clone(tmp_path: Path) -> None:
    facts = replace(
        CLEAN,
        clone_present=False,
        detached=None,
        clean=None,
        origin=None,
        checkout_linked=None,
        checkout_branch=None,
        ahead=None,
    )
    body = (
        "[a](/repositories/demo/references/git/src/a.py) "
        "[b](/repositories/demo/references/git/README.md) [c](/elsewhere.md)"
    )
    findings = _findings(tmp_path, facts, body=body)
    assert findings == [("repository.unmaterialized", "warn", "repositories/demo.md")]
    rule_messages = [
        f.message
        for f in validate(
            load_bundle(tmp_path), today=date(2026, 9, 29), extra_rules=(repository_rule((facts,)),)
        ).findings
        if f.code == "repository.unmaterialized"
    ]
    assert "2 broken link(s)" in rule_messages[0] and "gw repo restore demo" in rule_messages[0]


def test_reference_repositories_skip_the_managed_only_codes(tmp_path: Path) -> None:
    reference = replace(
        CLEAN,
        name="ref",
        type="ReferenceRepository",
        checkout=None,
        checkout_linked=None,
        checkout_branch=None,
        ahead=5,
        declared=False,
    )
    assert _findings(tmp_path, reference) == []


def test_codes_narrows_the_rule(tmp_path: Path) -> None:
    facts = replace(CLEAN, clean=False, ahead=2)
    assert _findings(tmp_path, facts, codes=frozenset({"repository.behind-track"})) == [
        ("repository.behind-track", "warn", "repositories/demo.md")
    ]


def test_failed_origin_probe_does_not_claim_url_mismatch(tmp_path: Path) -> None:
    lane = tmp_path / "repositories"
    lane.mkdir()
    page_file = lane / "demo.md"
    page_file.write_text(PAGE.format(type="ManagedRepository", name="demo", sha=SHA), encoding="utf-8", newline="")
    (lane / "demo" / "references" / "git").mkdir(parents=True)
    unavailable = Git(executable=str(tmp_path / "missing-git"), environ={})

    facts = gather(unavailable, bundle_dir=tmp_path, name="demo", page=load(page_file), declared=True, checkout=None)

    assert facts.origin is None
    assert _findings(tmp_path, facts, codes=frozenset({"repository.url-mismatch"})) == []


def test_gather_reads_the_real_clone_and_checkout(tmp_path: Path, runner: Git) -> None:
    (tmp_path / "up").mkdir()
    upstream = make_upstream(tmp_path / "up")
    pinned = upstream.commit({"a.py": "a = 1\n"}, "c1")
    bundle = tmp_path / "okf"
    clone = bundle / "repositories" / "demo" / "references" / "git"
    clone.parent.mkdir(parents=True)
    assert clone_partial(runner, upstream.url, clone) is None and detach(runner, clone, pinned) is None
    checkout = tmp_path / ".gw" / "worktrees" / "demo" / "main"
    assert worktree_add(runner, clone, checkout, "main") is None
    git(checkout, "commit", "-q", "--allow-empty", "-m", "ahead")
    stray = bundle / "repositories" / "demo" / "stray"
    git(clone, "worktree", "add", "-q", "--detach", str(stray))
    page_file = bundle / "repositories" / "demo.md"
    page_file.write_text(
        PAGE.format(type="ManagedRepository", name="demo", sha=pinned).replace(
            "https://example.com/demo.git", upstream.url
        ),
        encoding="utf-8",
        newline="",
    )
    facts = gather(runner, bundle_dir=bundle, name="demo", page=load(page_file), declared=True, checkout=checkout)
    assert (facts.clone_present, facts.detached, facts.clean, facts.origin == upstream.url) == (True, True, True, True)
    assert (facts.checkout_linked, facts.checkout_branch, facts.ahead) == (True, "main", 1)
    assert facts.worktrees_in_bundle == ("repositories/demo/stray",)
    absent = gather(
        runner, bundle_dir=tmp_path / "empty", name="demo", page=load(page_file), declared=False, checkout=None
    )
    assert (absent.clone_present, absent.detached, absent.ahead, absent.checkout) == (False, None, None, None)


@pytest.mark.parametrize("replacement", ["absent", "directory", "repository"])
def test_stale_registration_is_an_invalid_checkout(tmp_path: Path, runner: Git, replacement: str) -> None:
    from repositories_okf.git import remove_tree

    (tmp_path / "up").mkdir()
    upstream = make_upstream(tmp_path / "up")
    pinned = upstream.commit({"a.py": "a = 1\n"}, "c1")
    bundle = tmp_path / "okf"
    clone = bundle / "repositories" / "demo" / "references" / "git"
    clone.parent.mkdir(parents=True)
    assert clone_partial(runner, upstream.url, clone) is None and detach(runner, clone, pinned) is None
    checkout = tmp_path / "checkout"
    assert worktree_add(runner, clone, checkout, "main") is None
    remove_tree(checkout)
    if replacement != "absent":
        checkout.mkdir()
    if replacement == "repository":
        git(checkout, "init", "-q", "-b", "main")
        git(checkout, "commit", "-q", "--allow-empty", "-m", "foreign")
    assert str(checkout) in git(clone, "worktree", "list", "--porcelain")
    page_file = bundle / "repositories" / "demo.md"
    page_file.write_text(PAGE.format(type="ManagedRepository", name="demo", sha=pinned), encoding="utf-8", newline="")
    facts = gather(runner, bundle_dir=bundle, name="demo", page=load(page_file), declared=True, checkout=checkout)
    assert _findings(bundle, facts, codes=frozenset({"repository.checkout-invalid"})) == [
        ("repository.checkout-invalid", "error", "repositories/demo.md")
    ]
