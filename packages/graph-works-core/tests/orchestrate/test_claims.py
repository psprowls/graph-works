"""The claim predicate: containment, write-vs-write, owner self-exclusion."""

from __future__ import annotations

import pytest
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.orchestrate.claims import (
    CODE_WRITE_PHASES,
    WORKSPACE,
    Claim,
    CodeScope,
    WorktreeScope,
    affects_claims,
    claims_for,
    code_claims,
    conflicts,
    first_conflicts,
    paths_overlap,
)


def _code(path: str | None, repo: str = "git-a", owner: str = "work/one", mode: str = "write") -> Claim:
    return Claim(CodeScope(repo, path), mode, owner)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        # equal and nested code paths, both directions
        (_code("packages/a"), _code("packages/a", owner="work/two"), True),
        (_code("packages/a"), _code("packages/a/src/x", owner="work/two"), True),
        (_code("packages/a/src/x"), _code("packages/a", owner="work/two"), True),
        # spelling variants normalize
        (_code("packages/a/"), _code("./packages/a", owner="work/two"), True),
        # siblings and string-prefix-only paths never overlap
        (_code("packages/a"), _code("packages/b", owner="work/two"), False),
        (_code("packages/a"), _code("packages/ab", owner="work/two"), False),
        # `..` is kept literally, not resolved
        (_code("packages/a/../b"), _code("packages/b", owner="work/two"), False),
        # equal paths in different Git identities do not conflict
        (_code("packages/a"), _code("packages/a", repo="git-b", owner="work/two"), False),
        # whole repository vs any path in the same identity, and not across identities
        (_code(None), _code("packages/a", owner="work/two"), True),
        (_code("packages/a"), _code(None, owner="work/two"), True),
        (_code(None), _code("packages/a", repo="git-b", owner="work/two"), False),
        # worktrees compare by equality, independently of code scope
        (Claim(WorktreeScope("/wt/x"), "write", "work/one"), Claim(WorktreeScope("/wt/x"), "write", "work/two"), True),
        (Claim(WorktreeScope("/wt/x"), "write", "work/one"), Claim(WorktreeScope("/wt/y"), "write", "work/two"), False),
        (Claim(WorktreeScope("packages/a"), "write", "work/one"), _code("packages/a", owner="work/two"), False),
        # workspace is its own kind
        (Claim(WORKSPACE, "write", "work/one"), Claim(WORKSPACE, "write", "work/two"), True),
        (Claim(WORKSPACE, "write", "work/one"), _code(None, owner="work/two"), False),
        # write vs read and read vs read never conflict
        (_code("packages/a"), _code("packages/a", owner="work/two", mode="read"), False),
        (_code("packages/a", mode="read"), _code("packages/a", owner="work/two", mode="read"), False),
        # the same owner never conflicts with itself
        (_code("packages/a"), _code("packages/a/src"), False),
    ],
)
def test_conflicts_table(a: Claim, b: Claim, expected: bool) -> None:
    assert conflicts(a, b) is expected
    assert conflicts(b, a) is expected


@pytest.mark.parametrize("blank", ["", ".", "./", " "])
def test_blank_path_is_the_whole_repository(blank: str) -> None:
    assert paths_overlap(blank, "packages/a")
    assert paths_overlap("packages/a", blank)


def test_first_conflicts_lists_every_pair_in_input_order() -> None:
    mine = code_claims("work/new", [("git-a", "packages/a"), ("git-a", "docs")])
    held = (
        _code("packages/a/src", owner="work/x"),
        _code("docs/guide", owner="work/y"),
        _code("other", owner="work/z"),
    )
    pairs = first_conflicts(mine, held)
    assert [(m.scope, t.owner) for m, t in pairs] == [
        (CodeScope("git-a", "packages/a"), "work/x"),
        (CodeScope("git-a", "docs"), "work/y"),
    ]


def test_claims_are_hashable_values() -> None:
    assert len({_code("packages/a"), _code("packages/a")}) == 1


@pytest.mark.parametrize(
    ("affects", "expected"),
    [
        (("packages/a", "docs"), (CodeScope("git-a", "packages/a"), CodeScope("git-a", "docs"))),
        ((), (CodeScope("git-a", None),)),
        (("gw:workspace",), (WORKSPACE,)),
        (("gw:workspace", "packages/a"), (CodeScope("git-a", "packages/a"), WORKSPACE)),
        (("gw:worksapce",), (CodeScope("git-a", "gw:worksapce"),)),
    ],
)
def test_affects_claims_table(affects: tuple[str, ...], expected: tuple[object, ...]) -> None:
    claims = affects_claims("work/one", "git-a", affects)
    assert tuple(claim.scope for claim in claims) == expected
    assert {claim.mode for claim in claims} == {"write"}
    assert {claim.owner for claim in claims} == {"work/one"}


def test_affects_claims_composes_workspace_and_code_from_generator() -> None:
    claims = affects_claims("work/one", "git-a", (entry for entry in ("gw:workspace", "packages/a")))
    assert tuple(claim.scope for claim in claims) == (CodeScope("git-a", "packages/a"), WORKSPACE)


def test_an_empty_affects_claim_conflicts_with_any_write_in_its_repository_only() -> None:
    (whole,) = affects_claims("work/one", "git-a", ())
    assert conflicts(whole, _code("packages/z", owner="work/two"))
    assert not conflicts(whole, _code("packages/z", repo="git-b", owner="work/two"))
    assert not conflicts(whole, Claim(WORKSPACE, "write", "work/two"))


def test_empty_affects_claims_every_finish_identity() -> None:
    claims = {c for identity in ("git-a", "git-b") for c in affects_claims("work/one", identity, ())}
    assert {c.scope for c in claims} == {CodeScope("git-a", None), CodeScope("git-b", None)}


@pytest.mark.parametrize(
    ("claim", "label"),
    [
        (_code("packages/a"), "packages/a"),
        (_code(None), "git-a (whole repository)"),
        (Claim(WorktreeScope("/wt/x"), "write", "work/one"), "/wt/x"),
        (Claim(WORKSPACE, "write", "work/one"), "workspace"),
    ],
)
def test_claim_scope_labels(claim: Claim, label: str) -> None:
    assert orchestrate._scope_label(claim) == label


@pytest.mark.parametrize(
    "affects",
    [
        ("packages/a",),
        ("packages/a", "packages/a/src"),
        (),
        ("gw:workspace",),
        ("gw:workspace", "packages/a"),
    ],
)
@pytest.mark.parametrize("phase", ["design", "plan", "execute", "finish", "review", ""])
def test_claims_for_is_the_stage_policy(phase: str, affects: tuple[str, ...]) -> None:
    """Epic D-002: only execute and finish hold affects claims; every other phase holds none,
    including the workspace claim of a `gw:workspace` item (item D-001)."""
    claims = claims_for("work/one", phase, "git-a", affects)
    if phase in ("execute", "finish"):
        assert claims == affects_claims("work/one", "git-a", affects)
    else:
        assert claims == ()


def test_code_write_phases_are_execute_and_finish_only() -> None:
    assert frozenset({"execute", "finish"}) == CODE_WRITE_PHASES
    assert not CODE_WRITE_PHASES & orchestrate.READ_ONLY_PHASES


def test_claims_for_consumes_a_generator_once() -> None:
    claims = claims_for("work/one", "execute", "git-a", (e for e in ("gw:workspace", "packages/a")))
    assert tuple(claim.scope for claim in claims) == (CodeScope("git-a", "packages/a"), WORKSPACE)
