"""Guidance assembly: the budget cut, streams, degradation."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from code_graph_io import open_writer
from code_graph_io.tokens import count_tokens
from graph_works_core import apply_init, plan_init
from graph_works_core.graph.commands import graph_target
from graph_works_core.guidance import assembly as ga
from graph_works_core.guidance import closure as cl
from graph_works_core.guidance.claims import ClaimRow, ClaimsRefresh
from graph_works_core.guidance.closure import Closure, ClosureEntry
from graph_works_core.guidance.render import Candidate, GuidanceEntry, page_heading
from graph_works_core.workspace.errors import WorkspaceConfigError
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_ext.shape import SectionSet, SectionSpec, TypeSections
from okf_io import load_bundle
from work_tracker_okf.items import IGNORE, load_items


def _cand(entry_id: str, words: int, page: str = "/a.md") -> Candidate:
    text = "- **" + entry_id + "** " + " ".join(["word"] * words)
    return Candidate(
        "claims", page_heading("A", page), GuidanceEntry("claim", page, entry_id, "w", text, count_tokens(text))
    )


def test_everything_fits_under_the_default_budget() -> None:
    g = ga.build_guidance([_cand("C1", 5), _cand("C2", 5)], phase="plan", item_path="work/a")
    assert [e.id for e in g.entries] == ["C1", "C2"]
    assert g.warnings == ()
    assert g.tokens == count_tokens(g.rendered) <= ga.GUIDANCE_TOKEN_BUDGET


def test_admission_stops_at_the_first_misfit_and_never_skips_ahead() -> None:
    small, big, later_small = _cand("C1", 5), _cand("C2", 400), _cand("C3", 1)
    header = count_tokens(ga.build_guidance([small], phase="p", item_path="w").rendered)
    g = ga.build_guidance([small, big, later_small], phase="p", item_path="w", budget=header + 20)
    assert [e.id for e in g.entries] == ["C1"]
    assert count_tokens(g.rendered) <= header + 20
    assert g.warnings == (f"guidance cut at {header + 20} tokens: admitted 1 of 3 candidates; first dropped: /a.md#C2",)


def test_an_oversize_first_candidate_admits_nothing() -> None:
    g = ga.build_guidance([_cand("C1", 5000), _cand("C2", 1)], phase="p", item_path="w")
    assert g.entries == () and g.rendered == "" and g.tokens == 0
    assert len(g.warnings) == 1 and g.warnings[0].endswith("admitted 0 of 2 candidates; first dropped: /a.md#C1")


def test_caller_warnings_come_first_then_the_cut() -> None:
    g = ga.build_guidance([_cand("C1", 5000)], phase="p", item_path="w", warnings=("no graph",))
    assert g.warnings[0] == "no graph" and g.warnings[1].startswith("guidance cut at 3000 tokens")


def test_write_guidance_writes_lf_utf8_and_creates_parents(tmp_path: Path) -> None:
    g = ga.build_guidance([_cand("C1", 3)], phase="plan", item_path="work/a")
    target = tmp_path / "deep" / "guidance-plan.md"
    assert ga.write_guidance(g, target) == target
    assert target.read_bytes() == g.rendered.encode("utf-8")
    assert b"\r\n" not in target.read_bytes()


def test_write_guidance_skips_an_empty_guidance(tmp_path: Path) -> None:
    g = ga.build_guidance([], phase="plan", item_path="work/a")
    assert ga.write_guidance(g, tmp_path / "g.md") is None
    assert not (tmp_path / "g.md").exists()


# -- Task 3: claims and agent-section streams ---------------------------------

PHASES = ("design", "plan", "execute", "finish")


def _row(page: str, row_id: str, about: tuple[str, ...], *, phases=PHASES, superseded=False, kind="claim") -> ClaimRow:
    return ClaimRow(
        page=page,
        id=row_id,
        kind=kind,
        claim=f"{row_id} holds.",
        about=about,
        constrains=("a.py",) if row_id == "D1" else (),
        phases=phases,
        phase_source="derived" if phases == PHASES else "authored",
        page_status="stable",
        superseded=superseded,
        superseded_by=None,
        tokens=3,
    )


CLOSURE = Closure(
    entries=(
        ClosureEntry("file:o/r/packages/a/x.py", 0, "file under packages/a"),
        ClosureEntry("pkg:o/r/a", 1, "package directory packages/a"),
        ClosureEntry("dependency:o/r/pypi/ruamel.yaml", 2, "external dependency of pkg:o/r/a"),
        ClosureEntry("pkg:o/r/b", 2, "internal dependency of pkg:o/r/a"),
        ClosureEntry("repo:o/r", 3, "repository r"),
    ),
    warnings=(),
)


def _page(root: Path, concept_id: str, fm: str, body: str) -> None:
    path = root / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{fm}---\n\n{body}", encoding="utf-8", newline="\n")


def test_claims_follow_match_order_filter_phase_and_drop_superseded(tmp_path: Path) -> None:
    _page(tmp_path, "adrs/walk", "type: Adr\ntitle: Walk\n", "# Walk\n")
    bundle = load_bundle(tmp_path)
    rows = [
        _row("adrs/walk", "D2", ("repo:o/r",), kind="decision"),
        _row("adrs/walk", "D1", ("pkg:o/r/a",), kind="decision"),
        _row("adrs/walk", "C9", ("pkg:o/r/a",), phases=("execute",)),
        _row("adrs/walk", "C8", ("pkg:o/r/a",), superseded=True),
        _row("adrs/other", "C1", ("pkg:o/zzz",)),
    ]
    got = ga.claim_candidates(bundle, rows, CLOSURE, phase="design")
    assert [(c.entry.kind, c.entry.id) for c in got] == [("decision", "D1"), ("decision", "D2")]
    first = got[0]
    assert first.stream == "claims"
    assert first.group == "### [Walk](/adrs/walk.md)"
    assert first.entry.path == "/adrs/walk.md"
    assert first.entry.why == "package directory packages/a"
    assert first.entry.text == "- **D1** D1 holds. _(constrains: `a.py`)_"
    # `match_claims` order is (tier, page, id) with ids compared as text: C9 sorts before D1 in tier 1.
    assert [c.entry.id for c in ga.claim_candidates(bundle, rows, CLOSURE, phase="execute")] == ["C9", "D1", "D2"]


def test_a_claim_on_a_page_missing_from_the_bundle_uses_its_id_as_title(tmp_path: Path) -> None:
    got = ga.claim_candidates(load_bundle(tmp_path), [_row("gone/p", "C1", ("repo:o/r",))], CLOSURE, phase="plan")
    assert got[0].group == "### [gone/p](/gone/p.md)"


PLACEHOLDER = "> TODO: purpose.\n"
SECTIONS = SectionSet(
    types={
        "Package": TypeSections(
            sections=(
                SectionSpec(heading="Purpose", audience="agent", placeholder=PLACEHOLDER),
                SectionSpec(heading="History"),
                SectionSpec(heading="Public API", audience="agent", phases=("execute",)),
            )
        ),
        "File": TypeSections(sections=(SectionSpec(heading="Notes", audience="agent", placeholder=PLACEHOLDER),)),
        "Dependency": TypeSections(sections=(SectionSpec(heading="Gotchas / workarounds", audience="agent"),)),
    },
    sources={},
    fragments={},
    root=Path(),
)


def _sectioned_bundle(root: Path):
    _page(
        root,
        "code-graph/r/packages/a",
        "type: Package\ntitle: a\nresource: pkg:o/r/a\n",
        "## Purpose\n\nParses things.\n\n## History\n\nLong story.\n\n## Public API\n\n`parse()`.\n",
    )
    _page(
        root, "code-graph/r/packages/b", "type: Package\ntitle: b\nresource: pkg:o/r/b\n", "## Purpose\n\nB things.\n"
    )
    _page(
        root,
        "code-graph/r/files/x",
        "type: File\ntitle: x.py\nresource: file:o/r/packages/a/x.py\n",
        f"## Notes\n\n{PLACEHOLDER}",
    )
    _page(
        root,
        "code-graph/r/entities/dependencies/pypi/ruamel.yaml",
        "type: Dependency\ntitle: ruamel.yaml\nresource: dependency:o/r/pypi/ruamel.yaml\n",
        "## Gotchas / workarounds\n\nPin it.\n",
    )
    _page(root, "code-graph/r/repo", "type: Repository\ntitle: r\nresource: repo:o/r\n", "## Purpose\n\nRepo.\n")
    return load_bundle(root)


def test_sections_come_from_own_packages_files_and_dependencies_only(tmp_path: Path) -> None:
    got = ga.section_candidates(_sectioned_bundle(tmp_path), CLOSURE, SECTIONS, phase="design")
    assert [(c.entry.path, c.entry.id) for c in got] == [
        ("/code-graph/r/packages/a.md", "Purpose"),
        ("/code-graph/r/entities/dependencies/pypi/ruamel.yaml.md", "Gotchas / workarounds"),
    ]
    purpose = got[0]
    assert purpose.stream == "sections" and purpose.entry.kind == "section"
    assert purpose.group == "### [a](/code-graph/r/packages/a.md) — Purpose"
    assert purpose.entry.text == "Parses things."
    assert purpose.entry.why == "package directory packages/a"


def test_a_phase_scoped_section_appears_only_in_its_phase(tmp_path: Path) -> None:
    got = ga.section_candidates(_sectioned_bundle(tmp_path), CLOSURE, SECTIONS, phase="execute")
    assert [c.entry.id for c in got][:2] == ["Purpose", "Public API"]


def test_an_undeclared_type_contributes_no_sections(tmp_path: Path) -> None:
    bundle = _sectioned_bundle(tmp_path)
    empty = SectionSet(types={}, sources={}, fragments={}, root=Path())
    assert ga.section_candidates(bundle, CLOSURE, empty, phase="design") == []


# -- Task 4: the answered-ledger stream ---------------------------------------

EPIC = "work/epic-e"
LEAF = f"{EPIC}/children/feature-f"


def _item(
    root: Path, path: str, *, type="Feature", affects=("packages/a",), work_status="open", repo: str | None = "r"
) -> None:
    lines = "".join(f"  - {a}\n" for a in affects)
    _page(
        root,
        path,
        f"type: {type}\ntitle: T {path}\ndescription: d\nstatus: stable\nwork_status: {work_status}\n"
        f"phase: plan\neffort: medium\nopened: 2026-09-01\nupdated: 2026-09-01\n"
        + (f"repo: {repo}\n" if repo is not None else "")
        + (f"affects:\n{lines}" if affects else "affects: []\n"),
        "## Plan\n",
    )


def _ledger(root: Path, owner: str, body: str) -> None:
    path = root / owner / "references" / "00-decisions.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Decisions\n\n" + body, encoding="utf-8", newline="\n")


def _answered(n: int, q: str, answer: str | None = "Yes.") -> str:
    prose = f"\n**Answer:** {answer}\n\n**Rationale:** because.\n" if answer else "\n**Rationale:** only.\n"
    return f"## D-{n:03d} — {q}\nstatus: answered\n{prose}\n"


def _ledger_world(root: Path):
    _item(root, EPIC, type="Epic")
    _item(root, LEAF)
    _item(root, "work/feature-overlap", affects=("packages/a/sub/",))
    _item(root, "work/feature-other-repo", repo="ui")
    _item(root, "work/feature-done", work_status="resolved")
    _item(root, "work/feature-disjoint", affects=("packages/b",))
    _ledger(root, LEAF, _answered(1, "Own?"))
    _ledger(
        root,
        EPIC,
        _answered(1, "Epic?") + "## D-002 — Still open?\nstatus: open\n\n" + _answered(3, "No answer?", None),
    )
    for owner in ("work/feature-overlap", "work/feature-other-repo", "work/feature-done", "work/feature-disjoint"):
        _ledger(root, owner, _answered(1, f"{owner}?"))
    bundle = load_bundle(root, ignore=IGNORE)
    items = tuple(load_items(bundle))
    return bundle, items, next(i for i in items if i.path == LEAF)


def test_ledgers_are_own_then_ancestors_then_overlapping_items(tmp_path: Path) -> None:
    bundle, items, leaf = _ledger_world(tmp_path)
    got, warnings = ga.ledger_candidates(bundle.root, items, leaf)
    assert [(c.entry.path, c.entry.id, c.entry.why) for c in got] == [
        (f"/{LEAF}/references/00-decisions.md", "D-001", "own ledger"),
        (f"/{EPIC}/references/00-decisions.md", "D-001", "ancestor ledger"),
        ("/work/feature-overlap/references/00-decisions.md", "D-001", "affects overlap with work/feature-overlap"),
    ]
    assert got[0].stream == "ledger" and got[0].entry.kind == "ledger"
    assert got[0].entry.text == "- **D-001** Own? — Yes."
    assert got[0].group == f"### [T {LEAF}](/{LEAF}/references/00-decisions.md)"
    assert warnings == (ga.MISSING_ANSWER_WARNING.format(count=1),)


def test_overlap_compares_resolved_repos_so_an_inherited_repo_counts(tmp_path: Path) -> None:
    _item(tmp_path, "work/epic-g", type="Epic", affects=())
    _item(tmp_path, "work/epic-g/children/feature-h", repo=None)
    _item(tmp_path, "work/epic-u", type="Epic", affects=(), repo="ui")
    _item(tmp_path, "work/epic-u/children/feature-v", repo=None)
    _item(tmp_path, "work/feature-leaf")
    for owner in ("work/epic-g/children/feature-h", "work/epic-u/children/feature-v"):
        _ledger(tmp_path, owner, _answered(1, f"{owner}?"))
    bundle = load_bundle(tmp_path, ignore=IGNORE)
    items = tuple(load_items(bundle))
    leaf = next(i for i in items if i.path == "work/feature-leaf")
    got, _ = ga.ledger_candidates(bundle.root, items, leaf)
    assert [c.entry.why for c in got] == ["affects overlap with work/epic-g/children/feature-h"]


def test_an_absent_ledger_is_empty_not_an_error(tmp_path: Path) -> None:
    _item(tmp_path, "work/feature-lonely")
    bundle = load_bundle(tmp_path, ignore=IGNORE)
    items = tuple(load_items(bundle))
    assert ga.ledger_candidates(bundle.root, items, items[0]) == ([], ())


def test_affects_overlap_is_a_directory_relation() -> None:
    assert ga.affects_overlap(["packages/a"], ["packages/a/"])
    assert ga.affects_overlap(["./packages/a/x.py"], ["packages/a"])
    assert ga.affects_overlap(["packages/a"], ["packages/a/sub"])
    assert not ga.affects_overlap(["packages/a"], ["packages/ab"])
    assert not ga.affects_overlap([], ["packages/a"])


# -- Task 5: assemble_guidance ------------------------------------------------


def _ws(tmp_path: Path) -> WorkspaceLayout:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=date(2026, 9, 25), topic="G")).layout


def _load(layout: WorkspaceLayout):
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = tuple(load_items(bundle))
    return bundle, items, next(i for i in items if i.path == LEAF)


def test_no_graph_still_assembles_ledgers_and_skips_the_claims_cache(tmp_path: Path) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    bundle, items, leaf = _load(layout)
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert cl.WARN_NO_GRAPH in g.warnings
    assert [e.kind for e in g.entries] == ["ledger", "ledger", "ledger"]
    assert not (layout.cache_dir / "claims").exists()


def test_a_graph_os_error_is_a_warning_and_ledgers_still_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    bundle, items, leaf = _load(layout)

    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(ga, "open_closure", boom)
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert any(w.startswith("code graph unreadable: disk gone") for w in g.warnings)
    assert len(g.entries) == 3


def test_a_claims_cache_os_error_drops_only_stream_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    _sectioned_bundle(layout.bundle_dir)
    bundle, items, leaf = _load(layout)
    monkeypatch.setattr(ga, "open_closure", lambda *a, **k: CLOSURE)
    monkeypatch.setattr(ga, "load_sections", lambda path: SECTIONS)

    def boom(*a, **k):
        raise OSError("cache ro")

    monkeypatch.setattr(ga, "refresh_claims", boom)
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert any(w.startswith("claims index unavailable: cache ro") for w in g.warnings)
    assert {e.kind for e in g.entries} == {"section", "ledger"}


def test_an_unreadable_ledger_drops_only_stream_three(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    bundle, items, leaf = _load(layout)

    def boom(*a, **k):
        raise OSError("ledger locked")

    monkeypatch.setattr(ga, "ledger_candidates", boom)
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert any(w.startswith("decision ledgers unreadable: ledger locked") for w in g.warnings)
    assert g.entries == ()


def test_missing_section_declarations_warn_and_claims_still_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    bundle, items, leaf = _load(layout)
    monkeypatch.setattr(ga, "open_closure", lambda *a, **k: CLOSURE)
    monkeypatch.setattr(ga, "refresh_claims", lambda *a: ClaimsRefresh(False, None, 1, 0, 0, ()))
    monkeypatch.setattr(ga, "read_claims", lambda *a: (_row("adrs/walk", "C1", ("pkg:o/r/a",)),))
    monkeypatch.setattr(ga, "load_sections", lambda path: (_ for _ in ()).throw(OSError("no dir")))
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert any(w.startswith("section declarations unreadable: no dir") for w in g.warnings)
    assert [(e.kind, e.id) for e in g.entries if e.kind != "ledger"] == [("claim", "C1")]


def test_closure_warnings_pass_through_verbatim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    bundle, items, leaf = _load(layout)
    warned = Closure(CLOSURE.entries, ("affects path 'x' matches nothing in the code graph",))
    monkeypatch.setattr(ga, "open_closure", lambda *a, **k: warned)
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert g.warnings[0] == "affects path 'x' matches nothing in the code graph"


def test_two_runs_are_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    _sectioned_bundle(layout.bundle_dir)
    bundle, items, leaf = _load(layout)
    monkeypatch.setattr(ga, "open_closure", lambda *a, **k: CLOSURE)
    monkeypatch.setattr(ga, "load_sections", lambda path: SECTIONS)
    first = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    second = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert first.rendered == second.rendered and first.rendered
    # plan phase: Package `a` Purpose (Public API is execute-only), the dependency's Gotchas;
    # File `x` Notes is a placeholder; `b` is a tier-2 neighbour. Then the three ledger answers.
    assert [(e.kind, e.id) for e in first.entries] == [
        ("section", "Purpose"),
        ("section", "Gotchas / workarounds"),
        ("ledger", "D-001"),
        ("ledger", "D-001"),
        ("ledger", "D-001"),
    ]


def test_the_closure_repo_is_inherited_from_the_nearest_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _ws(tmp_path)
    _item(layout.bundle_dir, EPIC, type="Epic")
    _item(layout.bundle_dir, LEAF, repo=None)
    bundle, items, leaf = _load(layout)
    assert leaf.repo is None
    seen: list[str | None] = []

    def capture(layout, *, repo, affects):
        seen.append(repo)
        return Closure((), ())

    monkeypatch.setattr(ga, "open_closure", capture)
    ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert seen == ["r"]


def test_a_repo_resolution_warning_precedes_the_closure_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    bundle, items, leaf = _load(layout)
    monkeypatch.setattr(ga, "_item_repo", lambda *a: (None, "ambiguous repo"))
    monkeypatch.setattr(ga, "open_closure", lambda *a, **k: Closure((), (cl.WARN_NO_REPO, "later")))
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    # The repo warning already explains the empty closure; WARN_NO_REPO would repeat it.
    assert g.warnings[:2] == ("ambiguous repo", "later")
    assert cl.WARN_NO_REPO not in g.warnings


def test_a_repo_less_item_in_a_multi_repo_workspace_warns_once(tmp_path: Path) -> None:
    layout = _ws(tmp_path)
    text = layout.manifest_path.read_text(encoding="utf-8")
    two = text.replace('  "repo":\n    path: ".."\n', '  "r":\n    path: ".."\n  "ui":\n    path: "../ui"\n')
    assert two != text
    layout.manifest_path.write_text(two, encoding="utf-8", newline="\n")
    _seed_graph(layout)
    _item(layout.bundle_dir, EPIC, type="Epic", repo=None)
    _item(layout.bundle_dir, LEAF, repo=None)
    bundle, items, leaf = _load(layout)
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert g.warnings == (
        "the work item and its ancestors set no `repo:` and the workspace declares 2 repositories (r, ui); "
        "set `repo:` to choose one",
    )


def test_a_page_pair_claiming_one_resource_contributes_no_sections(tmp_path: Path) -> None:
    _sectioned_bundle(tmp_path)
    _page(
        tmp_path,
        "code-graph/r/packages/a-copy",
        "type: Package\ntitle: a2\nresource: pkg:o/r/a\n",
        "## Purpose\n\nDup.\n",
    )
    got = ga.section_candidates(load_bundle(tmp_path), CLOSURE, SECTIONS, phase="design")
    assert [(c.entry.path, c.entry.id) for c in got] == [
        ("/code-graph/r/entities/dependencies/pypi/ruamel.yaml.md", "Gotchas / workarounds"),
    ]


def test_a_tokenizer_failure_is_not_reported_as_a_ledger_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    bundle, items, leaf = _load(layout)

    def no_tokenizer(text: str) -> int:
        raise OSError("ProxyError")

    monkeypatch.setattr(ga, "count_tokens", no_tokenizer)
    with pytest.raises(ga.TokenizerUnavailable, match="token counting failed: ProxyError") as caught:
        ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert isinstance(caught.value, ValueError) and not isinstance(caught.value, OSError)


def test_a_malformed_workspace_manifest_degrades_to_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    bundle, items, leaf = _load(layout)

    def bad(*a):
        raise WorkspaceConfigError("workspace.yaml is malformed")

    monkeypatch.setattr(ga, "_item_repo", bad)
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert any(w.startswith("code graph unreadable: workspace.yaml is malformed") for w in g.warnings)
    assert len(g.entries) == 3


def _graph(layout: WorkspaceLayout, *sql: str) -> None:
    store = open_writer(graph_dir=graph_target(layout).graph_dir, create=True)
    try:
        for statement in sql:
            store._conn.execute(statement)
        store._conn.commit()
    finally:
        store.close()


def _seed_graph(layout: WorkspaceLayout) -> None:
    _graph(
        layout,
        "INSERT INTO nodes (id, kind, name, path, uri, repo) VALUES "
        "(1,'repository','r','','repo:o/r','repo:o/r'),"
        "(2,'package','a','packages/a','pkg:o/r/a','repo:o/r'),"
        "(3,'file','packages/a/x.py','packages/a/x.py','file:o/r/packages/a/x.py','repo:o/r')",
    )


def test_real_graph_admits_the_matched_claim_first(tmp_path: Path) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    _seed_graph(layout)
    _page(
        layout.bundle_dir,
        "explanations/a",
        "type: Explanation\ntitle: A\nstatus: stable\nclaims:\n  - id: C1\n    claim: A parses.\n"
        "    about: [pkg:o/r/a]\n",
        "# A\n",
    )
    bundle, items, leaf = _load(layout)
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert (g.entries[0].kind, g.entries[0].path, g.entries[0].id) == ("claim", "/explanations/a.md", "C1")
    assert (layout.cache_dir / "claims" / "manifest.json").exists()


def test_skipped_claim_entries_are_counted_in_one_warning(tmp_path: Path) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    _seed_graph(layout)
    _page(
        layout.bundle_dir,
        "explanations/a",
        "type: Explanation\ntitle: A\nstatus: stable\nclaims:\n  - id: C1\n    claim: A parses.\n"
        "    about: [pkg:o/r/a]\n  - not a mapping\n",
        "# A\n",
    )
    bundle, items, leaf = _load(layout)
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert ga.SKIPPED_CLAIMS_WARNING.format(count=1) in g.warnings
    assert g.entries[0].id == "C1"


# -- Fix round 1 ----------------------------------------------------------------


def test_a_corrupt_code_graph_is_a_warning_and_ledgers_still_run(tmp_path: Path) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    graph_dir = graph_target(layout).graph_dir
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "code.db").write_bytes(b"this is not a sqlite database\n" * 200)
    bundle, items, leaf = _load(layout)
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert any(w.startswith("code graph unreadable: ") for w in g.warnings)
    assert [e.kind for e in g.entries] == ["ledger", "ledger", "ledger"]


def test_a_repo_warning_survives_a_graph_failure_and_comes_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    bundle, items, leaf = _load(layout)

    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(ga, "_item_repo", lambda *a: (None, "ambiguous repo"))
    monkeypatch.setattr(ga, "open_closure", boom)
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert g.warnings[0] == "ambiguous repo"
    assert g.warnings[1].startswith("code graph unreadable: disk gone")


def test_an_undecodable_ledger_drops_only_stream_three(tmp_path: Path) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    (layout.bundle_dir / LEAF / "references" / "00-decisions.md").write_bytes(b"# Decisions\n\n\xff\xfe bad\n")
    bundle, items, leaf = _load(layout)
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert any(w.startswith("decision ledgers unreadable: ") for w in g.warnings)
    assert g.entries == ()


def test_an_archived_overlapping_item_contributes_no_ledger(tmp_path: Path) -> None:
    _item(tmp_path, "work/_archive/feature-old")
    _ledger(tmp_path, "work/_archive/feature-old", _answered(1, "Old?"))
    _item(tmp_path, "work/feature-live")
    _ledger(tmp_path, "work/feature-live", _answered(1, "Live?"))
    _item(tmp_path, "work/feature-leaf")
    bundle = load_bundle(tmp_path, ignore=IGNORE)
    items = tuple(load_items(bundle))
    assert next(i for i in items if i.path == "work/_archive/feature-old").archived
    leaf = next(i for i in items if i.path == "work/feature-leaf")
    got, _ = ga.ledger_candidates(bundle.root, items, leaf)
    assert [c.entry.why for c in got] == ["affects overlap with work/feature-live"]


def test_ledger_overlap_falls_back_to_the_given_repo_on_both_sides(tmp_path: Path) -> None:
    _item(tmp_path, "work/feature-norepo", repo=None)
    _ledger(tmp_path, "work/feature-norepo", _answered(1, "No repo?"))
    _item(tmp_path, "work/feature-leaf")
    bundle = load_bundle(tmp_path, ignore=IGNORE)
    items = tuple(load_items(bundle))
    leaf = next(i for i in items if i.path == "work/feature-leaf")
    assert ga.ledger_candidates(bundle.root, items, leaf)[0] == []
    got, _ = ga.ledger_candidates(bundle.root, items, leaf, fallback_repo="r")
    assert [c.entry.why for c in got] == ["affects overlap with work/feature-norepo"]
    assert ga.ledger_candidates(bundle.root, items, leaf, fallback_repo="ui")[0] == []


def _declare_sole_repo(layout: WorkspaceLayout, name: str) -> None:
    text = layout.manifest_path.read_text(encoding="utf-8")
    assert '  "repo":\n' in text
    layout.manifest_path.write_text(text.replace('  "repo":\n', f'  "{name}":\n'), encoding="utf-8", newline="\n")


def test_assembly_overlaps_a_repo_less_item_with_the_sole_declared_repo(tmp_path: Path) -> None:
    layout = _ws(tmp_path)
    _declare_sole_repo(layout, "r")
    _item(layout.bundle_dir, LEAF.replace("/children/feature-f", ""), type="Epic")
    _item(layout.bundle_dir, LEAF)
    _item(layout.bundle_dir, "work/feature-norepo", repo=None)
    _ledger(layout.bundle_dir, "work/feature-norepo", _answered(1, "No repo?"))
    bundle, items, leaf = _load(layout)
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert [(e.kind, e.why) for e in g.entries] == [("ledger", "affects overlap with work/feature-norepo")]


def test_an_unreadable_manifest_drops_only_the_overlap_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _ws(tmp_path)
    _ledger_world(layout.bundle_dir)
    bundle, items, leaf = _load(layout)

    def bad(*a):
        raise WorkspaceConfigError("workspace.yaml is malformed")

    monkeypatch.setattr(ga, "declared_repositories", bad)
    g = ga.assemble_guidance(layout, bundle, items, leaf, phase="plan")
    assert not any("malformed" in w for w in g.warnings)
    assert len(g.entries) == 3
