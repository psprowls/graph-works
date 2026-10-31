"""Full-corpus oracle comparisons for the touched-set planning path."""

from __future__ import annotations

import shutil
from collections import Counter
from dataclasses import replace
from datetime import date

import pytest
from gw_kernel.testing.transactions import workspace as _base_workspace
from gw_kernel.workspace import bundle as loaders
from gw_pipeline import transactions
from gw_work import commands
from okf_io import Document, validate
from test_reparent_fixture_equivalence import _postcondition_comparison
from work_tracker_okf import load_items
from work_tracker_okf.items import IGNORE
from work_tracker_okf.reparent import plan_reparent_many
from workspace_fixture import index_arm


def _write(layout, path, *, kind="Bug", target=None, terminal=False, needs="resolved"):
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    edge = "" if target is None else f"depends_on:\n- path: {target}\n  blocks: execute\n  needs: {needs}\n"
    page.write_text(
        f"---\ntitle: {path}\ndescription: D\ntype: {kind}\nstatus: stable\n"
        f"work_status: {'resolved' if terminal else 'open'}\nphase: execute\neffort: medium\n"
        f"opened: 2026-08-01\nupdated: 2026-08-01\n{edge}---\n\n## Summary\nD\n",
        encoding="utf-8",
        newline="",
    )


_ARM = {"mode": "index"}
# Tests that pin behavior only the touched-scan path has.
SCAN_ONLY = {
    "test_unreadable_directory_uses_full_walk_fallback",
    "test_dependency_context_matches_full_findings",
    "test_dependency_fallback_reuses_seed_documents_and_parses_every_admitted_member_once",
    "test_dependency_on_loaded_sibling_also_completes_remote_context",
}
# Tests that choose their own arm (they build the fallback themselves).
ARM_FREE = {
    "test_index_dependency_context_matches_full_findings",
    "test_index_fallback_arms_match_full_plan",
    "test_first_reparent_after_v1_database_matches_full_plan",
}


def pytest_generate_tests(metafunc):
    if "arm" in metafunc.fixturenames:
        name = metafunc.function.__name__
        arms = ["index"] if name in ARM_FREE else ["scan"] if name in SCAN_ONLY else ["index", "scan"]
        metafunc.parametrize("arm", arms, indirect=True)


@pytest.fixture(autouse=True)
def arm(request):
    _ARM["mode"] = request.param
    return request.param


def _workspace(path):
    layout = _base_workspace(path)
    index_arm(layout, _ARM["mode"])
    return layout


def _shape(plan):
    # Include bytes, source members, directory snapshots and every move field.
    return replace(plan, warnings=())


def _findings(layout, bundle, plan):
    report = validate(
        bundle,
        today=date.max,
        extra_rules=transactions._extra_rules(layout, None),
        scope=transactions._baseline_scope(plan),
    )
    return Counter(
        (f.path, f.code, f.severity, f.message) for f in report.findings if f.path in transactions._baseline_scope(plan)
    )


def _strict_read_index_warnings(plan, expected):
    """The index arm warns nothing; every other arm warns exactly once, naming *expected*."""
    notes = [w for w in plan.warnings if w.startswith("read index ")]
    if expected is None:
        assert not notes, notes
    else:
        assert len(notes) == 1 and notes[0].startswith(f"read index {expected}"), notes


def _sans_read_index(plan):
    return replace(plan, warnings=tuple(w for w in plan.warnings if not w.startswith("read index ")))


def _compare(layout, sources, parent, *, expected=...):
    if expected is ...:
        expected = None if _ARM["mode"] == "index" else "disabled"
    full = loaders.load_workspace_bundle(layout, ignore=())
    oracle = plan_reparent_many(full, load_items(full), sources, parent)
    partial, plan = commands._plan_reparent_partial(layout, sources, parent)
    assert _shape(plan) == _shape(oracle)
    _strict_read_index_warnings(plan, expected)
    assert (
        tuple(
            w
            for w in plan.warnings
            if not w.startswith(
                ("dependency context reaches unloaded members;", "touched scan could not read ", "read index ")
            )
        )
        == oracle.warnings
    )
    full_baseline = loaders.load_workspace_bundle(layout, ignore=IGNORE)
    assert _findings(layout, loaders.reclassify_ignored(partial, IGNORE), plan) == _findings(
        layout, full_baseline, oracle
    )
    return partial, plan


REFERRERS = {
    "plain": "[x](/work/a.md)\n",
    "relative": "[x](../work/a.md)\n",
    "percent": "[x](/work/%61.md)\n",
    "reference-def": "[x][r]\n\n[r]: /work/a.md\n",
    "source": "---\ntitle: R\nsources:\n- id: d\n  resource: /work/a/references/01-design.md\n---\n",
    "depends-on": "---\ntype: Bug\ntitle: R\nwork_status: open\ndepends_on:\n- path: work/a\n---\n",
    "wikilink": "see [[work/a]]\n",
    "parse-error": "---\ntitle: [x\n---\n[x](/work/a.md)\n",
    "index-body": "# Work\n\n- [a](/work/a.md) - an item\n",
    "log-body": "## 2026-08-01\n\n- **note** moved [a](/work/a.md)\n",
    "nfd-referrer": "[x](/work/a.md)\n",
    "folded-frontmatter": (
        "---\ntitle: R\nsources:\n- id: d\n  resource: >-\n    /work/a/references/01-design.md\n---\n"
    ),
    "title-mention": "---\ntitle: about a\n---\nnothing links here\n",
}
REFERRER_MEMBERS = {
    "index-body": "work/index.md",
    "log-body": "log.md",
    "nfd-referrer": "docs/r\u0065\u0301-nfd.md",
}


@pytest.mark.parametrize("form", sorted(REFERRERS))
def test_partial_plan_matches_full_for_referrer_forms(tmp_path, form):
    layout = _workspace(tmp_path)
    _write(layout, "work/e", kind="Epic")
    _write(layout, "work/a")
    page = layout.bundle_dir / REFERRER_MEMBERS.get(form, f"docs/r-{form}.md")
    page.parent.mkdir(exist_ok=True)
    page.write_text(REFERRERS[form], encoding="utf-8", newline="")
    _bundle, plan = _compare(layout, ["work/a"], "work/e")
    if form == "wikilink":
        assert plan.move_plan is not None and plan.move_plan.stranded
    elif form == "parse-error":
        assert any(refusal.kind == "parse-error" for refusal in plan.refusals)


def test_partial_plan_matches_full_for_batch_and_skips_unrelated_pages(tmp_path):
    layout = _workspace(tmp_path)
    for path, kind in [("work/e", "Epic"), ("work/bug-a", "Bug"), ("work/bug-b", "Bug")]:
        _write(layout, path, kind=kind)
    docs = layout.bundle_dir / "docs"
    docs.mkdir()
    for i in range(40):
        (docs / f"p{i}.md").write_text("---\ntitle: P\n---\nunrelated\n", encoding="utf-8", newline="")
    bundle, _ = _compare(layout, ["work/bug-a", "work/bug-b"], "work/e")
    assert bundle.partial is not None
    assert not any(member.startswith("docs/") for member in bundle.partial)
    assert {m for m in bundle.unloaded if m.startswith("docs/")} == {f"docs/p{i}.md" for i in range(40)}


def test_unreadable_directory_uses_full_walk_fallback(tmp_path, monkeypatch):
    from okf_ext.moves import touched

    layout = _workspace(tmp_path)
    _write(layout, "work/e", kind="Epic")
    _write(layout, "work/a")
    original = touched.walk

    def unreadable(root, **kwargs):
        return replace(original(root, **kwargs), unreadable_dirs={"docs/locked": "denied"})

    monkeypatch.setattr(touched, "walk", unreadable)
    bundle, plan = _compare(layout, ["work/a"], "work/e")
    assert bundle.partial is None
    assert any("docs/locked" in warning and "full load" in warning for warning in plan.warnings)


@pytest.mark.parametrize("variant", ["remote", "cycle", "terminal", "phase-compatible", "absent", "unreadable"])
def test_dependency_context_matches_full_findings(tmp_path, variant):
    layout = _workspace(tmp_path)
    for root in ["left", "far", "third", "destination"]:
        _write(layout, f"work/{root}", kind="Epic")
    a = "work/left/children/bug-a"
    b = "work/far/children/bug-b"
    c = "work/third/children/bug-c"
    cycle = variant in {"cycle", "terminal", "phase-compatible"}
    _write(layout, a, target=b)
    if variant != "absent":
        _write(layout, b, target=c if cycle else None, terminal=variant == "terminal")
    if cycle:
        _write(layout, c, target=a, needs="design" if variant == "phase-compatible" else "resolved")
    if variant == "unreadable":
        (layout.bundle_dir / f"{b}.md").write_bytes(b"\xffnot UTF-8")
    bundle, plan = _compare(layout, [a], "work/destination")
    graph = _findings(layout, loaders.reclassify_ignored(bundle, IGNORE), plan)
    codes = {code for (_path, code, _severity, _message) in graph}
    assert ("graph.depends-on-missing" in codes) == (variant in {"absent", "unreadable"})
    assert ("graph.depends-on-cycle" in codes) == (variant == "cycle")
    assert (bundle.partial is None) == (variant not in {"absent", "unreadable"})
    if variant not in {"absent", "unreadable"}:
        assert any(code == "graph.depends-on-not-sibling" and severity == "warn" for _, code, severity, _ in graph)
    _postcondition_comparison(layout, bundle, plan, tmp_path / "postcondition")
    # Compare the current durable full postcondition path with either a full
    # baseline or the optimized baseline lens. This exercises the actual
    # forward-mapped error allowances and excused-note rendering.
    results = []
    for name in ["full", "selected"]:
        workspace = tmp_path / f"apply-{name}"
        shutil.copytree(layout.root, workspace)
        copied_layout = replace(
            layout,
            root=workspace,
            bundle_dir=workspace / "okf",
            config_dir=workspace / ".gw",
            cache_dir=workspace / ".gw/cache",
            worktrees_dir=workspace / ".gw/worktrees",
        )
        baseline = (
            loaders.load_workspace_bundle(copied_layout, ignore=IGNORE)
            if name == "full"
            else replace(loaders.reclassify_ignored(bundle, IGNORE), root=copied_layout.bundle_dir)
        )
        application = transactions.apply_mutation(
            copied_layout,
            replace(plan, root=copied_layout.bundle_dir),
            commit=None,
            baseline_bundle=baseline,
        )
        assert application.ok == (variant != "unreadable"), application.failures
        assert application.rolled_back == (variant == "unreadable")
        results.append((application.failures, application.rolled_back, application.warnings))
    assert results[0] == results[1]
    if variant == "cycle":
        assert any("graph.depends-on-cycle" in warning for warning in results[0][2])


def test_dependency_fallback_reuses_seed_documents_and_parses_every_admitted_member_once(tmp_path, monkeypatch):
    from okf_ext.moves import touched
    from okf_io import _md
    from okf_io import bundle as bundle_mod

    layout = _workspace(tmp_path)
    for root in ["left", "far", "destination"]:
        _write(layout, f"work/{root}", kind="Epic")
    _write(layout, "work/left/children/bug-a", target="work/far/children/bug-b")
    _write(layout, "work/far/children/bug-b")
    counts = Counter()
    original = Document.parse.__func__

    def counted(cls, text, *, path=None):
        if path is not None:
            counts[path] += 1
        return original(cls, text, path=path)

    monkeypatch.setattr(Document, "parse", classmethod(counted))
    bodies = []
    original_tokenize = _md.tokenize

    def record_body(body):
        bodies.append(body)
        return original_tokenize(body)

    monkeypatch.setattr(_md, "tokenize", record_body)
    walks = []
    original_walk = touched.walk

    def record_walk(root, **kwargs):
        walks.append(root)
        return original_walk(root, **kwargs)

    monkeypatch.setattr(touched, "walk", record_walk)
    monkeypatch.setattr(bundle_mod, "walk", record_walk)
    initial = []
    original_load = commands.load_partial_bundle

    def record_seed(*args, **kwargs):
        loaded = original_load(*args, **kwargs)
        if not initial:
            initial.append(loaded)
        return loaded

    monkeypatch.setattr(commands, "load_partial_bundle", record_seed)
    bundle, _ = commands._plan_reparent_partial(layout, ["work/left/children/bug-a"], "work/destination")
    assert bundle.partial is None and not bundle.unloaded
    assert len(counts) == len(bundle.concepts) + len(bundle.indexes) + len(bundle.logs)
    assert set(counts.values()) == {1}
    assert walks == [layout.bundle_dir]
    for field in ["concepts", "indexes", "logs"]:
        assert all(doc is getattr(bundle, field)[key] for key, doc in getattr(initial[0], field).items())
        for doc in getattr(bundle, field).values():
            doc.body_index()
            doc.body_index()
            assert sum(body is doc.body for body in bodies) == 1


def test_dependency_on_loaded_sibling_also_completes_remote_context(tmp_path):
    layout = _workspace(tmp_path)
    for root in ["left", "far", "destination"]:
        _write(layout, f"work/{root}", kind="Epic")
    source = "work/left/children/bug-moved"
    _write(layout, source)
    _write(layout, "work/left/children/bug-sibling", target="work/far/children/bug-target")
    _write(layout, "work/far/children/bug-target")
    bundle, plan = _compare(layout, [source], "work/destination")
    assert bundle.partial is None
    assert any("full load" in warning for warning in plan.warnings)


def test_unicode_dependency_remains_invalid_without_inventing_an_item(tmp_path):
    import unicodedata

    layout = _workspace(tmp_path)
    for root in ["left", "far", "destination"]:
        _write(layout, f"work/{root}", kind="Epic")
    source = "work/left/children/bug-moved"
    target = "work/far/children/bug-café"
    _write(layout, source, target=target)
    _write(layout, unicodedata.normalize("NFD", target))
    bundle, plan = _compare(layout, [source], "work/destination")
    assert bundle.partial is not None
    assert any(code == "graph.depends-on-invalid" for _, code, _, _ in _findings(layout, bundle, plan))


def test_public_reparent_dry_run_uses_partial_planner(tmp_path, monkeypatch):
    layout = _workspace(tmp_path)
    _write(layout, "work/e", kind="Epic")
    _write(layout, "work/a")
    original = commands._plan_reparent_partial
    calls = []

    def record(layout, sources, parent):
        calls.append(tuple(sources))
        return original(layout, sources, parent)

    monkeypatch.setattr(commands, "_plan_reparent_partial", record)
    result = commands.run_reparent(layout, "work/a", "work/e")
    assert result.plan.path_mapping == {"work/a": "work/e/children/a"}
    assert calls == [("work/a",)]


ESCAPED_REFERRERS = {
    "entity": "[x](/work/bug&#45;x.md)\n",
    "hex-entity": "[x](/work/bug&#x2d;x.md)\n",
    "backslash": r"[x](/work/bug\-x.md)" + "\n",
    "entity-definition": "[x][r]\n\n[r]: /work/bug&#45;x.md\n",
    "backslash-definition": "[x][r]\n\n[r]: /work/bug\\-x.md\n",
    **{
        f"yaml-{name}": '---\ntitle: R\nsources:\n- id: r\n  resource: "/work/' + spelling + '.md"\n---\n# R\n'
        for name, spelling in [
            ("unicode", r"bug\u002dx"),
            ("long-unicode", r"bug\U0000002dx"),
            ("hex", r"bug\x2dx"),
            ("continuation", "bug\\\n    -x"),
        ]
    },
    "yaml-dependency": '---\ntitle: R\ndepends_on:\n- path: "work/bug\\u002dx"\n---\n# R\n',
}


def _snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("form", sorted(ESCAPED_REFERRERS))
def test_escaped_references_match_full_plan_findings_and_durable_outcome(tmp_path, form):
    layout = _workspace(tmp_path / "original")
    _write(layout, "work/bug-x")
    _write(layout, "work/destination", kind="Epic")
    referrer = layout.bundle_dir / "docs/r.md"
    referrer.parent.mkdir()
    original_text = ESCAPED_REFERRERS[form]
    referrer.write_text(original_text, encoding="utf-8", newline="")
    full = loaders.load_workspace_bundle(layout, ignore=())
    oracle = plan_reparent_many(full, load_items(full), ["work/bug-x"], "work/destination")
    bundle, plan = _compare(layout, ["work/bug-x"], "work/destination")
    assert "docs/r.md" not in bundle.unloaded
    assert _sans_read_index(plan) == oracle  # Includes every rendered write byte and full MovePlan.
    successful = form.startswith("yaml-")
    assert plan.ok == successful
    if successful:
        assert any(write.member == "docs/r.md" for write in plan.writes)
        _postcondition_comparison(layout, bundle, plan, tmp_path / "postcondition")
    else:
        assert any(refusal.path == "docs/r.md" and refusal.kind == "unlocatable-reference" for refusal in plan.refusals)

    before = _snapshot(layout.bundle_dir)
    outcomes = []
    for lens in ["full", "partial"]:
        workspace = tmp_path / f"apply-{lens}"
        shutil.copytree(layout.root, workspace)
        copied_layout = replace(
            layout,
            root=workspace,
            bundle_dir=workspace / "okf",
            config_dir=workspace / ".gw",
            cache_dir=workspace / ".gw/cache",
            worktrees_dir=workspace / ".gw/worktrees",
        )
        baseline = (
            loaders.load_workspace_bundle(copied_layout, ignore=IGNORE)
            if lens == "full"
            else replace(loaders.reclassify_ignored(bundle, IGNORE), root=copied_layout.bundle_dir)
        )
        application = transactions.apply_mutation(
            copied_layout, replace(plan, root=copied_layout.bundle_dir), baseline_bundle=baseline, commit=None
        )
        after = _snapshot(copied_layout.bundle_dir)
        if successful:
            assert application.ok, application.failures
            assert "work/bug-x.md" not in after
            assert "work/destination/children/bug-x.md" in after
            for write in plan.writes:
                assert after[write.member] == write.after
            document = Document.parse(after["docs/r.md"].decode("utf-8"))
            if form == "yaml-dependency":
                assert document.fm_raw["depends_on"][0]["path"] == "work/destination/children/bug-x"
            else:
                assert document.fm_raw["sources"][0]["resource"] == "/work/destination/children/bug-x.md"
        else:
            assert after == before
            # A refused plan has no effects; the low-level executor's existing
            # empty-plan path returns a no-op result before checking refusals.
            # The public command below must still return the refusal and avoid
            # calling this executor at all.
            assert application.transaction_id == ""
            assert not application.written and not application.created_directories
            assert application.commit is None
            assert not application.rolled_back
        outcomes.append((after, application.failures, application.rolled_back, application.warnings))
    assert outcomes[0] == outcomes[1]

    public = commands.run_reparent(layout, "work/bug-x", "work/destination", dry_run=False)
    assert _sans_read_index(public.plan) == oracle
    if successful:
        assert public.application is not None and public.application.ok
        assert _snapshot(layout.bundle_dir) == outcomes[0][0]
    else:
        assert public.application is None
        assert _snapshot(layout.bundle_dir) == before


def test_reparent_leaves_source_and_destination_indexes(tmp_path):
    layout = _workspace(tmp_path)
    _write(layout, "work/e", kind="Epic")
    _write(layout, "work/a")
    before = {
        "work/index.md": b"# Work\n\n- [Bug: a](a.md) \xe2\x80\x94 open \xc2\xb7 execute\n",
        "work/e/children/index.md": b"# Children\n",
    }
    for member, content in before.items():
        path = layout.bundle_dir / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    result = commands.run_reparent(layout, ["work/a"], "work/e", dry_run=False)
    assert result.application is not None and result.application.ok, result.application
    for member, content in before.items():
        assert (layout.bundle_dir / member).read_bytes() == content


def test_index_path_skips_escape_syntax_pages(tmp_path, arm):
    layout = _workspace(tmp_path)
    _write(layout, "work/e", kind="Epic")
    _write(layout, "work/a")
    noise = layout.bundle_dir / "docs/noise.md"
    noise.parent.mkdir()
    noise.write_text(
        "---\ntitle: N\n---\nback\\slash & amp and ''quotes'' but no reference\n", encoding="utf-8", newline=""
    )
    partial, _plan = _compare(layout, ["work/a"], "work/e")
    # The index arm never admits escape syntax; the touched scan does, by design.
    assert ("docs/noise.md" in partial.unloaded) == (arm == "index")


def test_index_path_loads_dependency_closure_without_fallback(tmp_path, arm):
    layout = _workspace(tmp_path)
    for root in ["e", "far"]:
        _write(layout, f"work/{root}", kind="Epic")
    _write(layout, "work/a", target="work/far/children/b")
    _write(layout, "work/far/children/b", target="work/c")
    _write(layout, "work/c")
    _partial, plan = _compare(layout, ["work/a"], "work/e")
    if arm == "index":
        assert not [w for w in plan.warnings if w.startswith("dependency context reaches unloaded members;")]


@pytest.mark.parametrize(
    "variant", ["remote", "cycle", "terminal", "phase-compatible", "absent", "unreadable", "archived", "nfd"]
)
def test_index_dependency_context_matches_full_findings(tmp_path, arm, variant):
    import unicodedata

    layout = _workspace(tmp_path)
    for root in ["left", "far", "third", "destination"]:
        _write(layout, f"work/{root}", kind="Epic")
    a = "work/left/children/bug-a"
    b = "work/far/children/bug-b"
    c = "work/third/children/bug-c"
    cycle = variant in {"cycle", "terminal", "phase-compatible"}
    target = b
    if variant == "archived":
        target = "work/_archive/bug-b"
        _write(layout, target, terminal=True)
    elif variant == "nfd":
        target = unicodedata.normalize("NFD", "work/far/children/bug-café")
        _write(layout, "work/far/children/bug-café")
    _write(layout, a, target=target)
    if variant not in {"absent", "archived", "nfd"}:
        _write(layout, b, target=c if cycle else None, terminal=variant == "terminal")
    if cycle:
        _write(layout, c, target=a, needs="design" if variant == "phase-compatible" else "resolved")
    if variant == "unreadable":
        (layout.bundle_dir / f"{b}.md").write_bytes(b"\xffnot UTF-8")
    _bundle, plan = _compare(layout, [a], "work/destination")
    assert not [w for w in plan.warnings if w.startswith("dependency context reaches unloaded members;")]


def _sans_root(plan):
    return replace(_sans_read_index(plan), root=None, move_plan=replace(plan.move_plan, root=None))


def _fallback_layout(tmp_path, inexact=False):
    layout = _base_workspace(tmp_path)
    for root in ["e", "far"]:
        _write(layout, f"work/{root}", kind="Epic")
    _write(layout, "work/a", target="work/far/children/b")
    _write(layout, "work/far/children/b")
    docs = layout.bundle_dir / "docs"
    docs.mkdir()
    (docs / "r.md").write_text("[x](/work/a.md)\n", encoding="utf-8", newline="")
    if inexact:
        page = layout.bundle_dir / "work/a.md"
        text = page.read_text(encoding="utf-8")
        page.write_text(text.replace("---\n\n", "payload: !!binary YWJj\n---\n\n", 1), encoding="utf-8", newline="")
    return layout


@pytest.mark.parametrize("reason", ["disabled", "busy", "unsettled", "unavailable", "inexact"])
def test_index_fallback_arms_match_full_plan(tmp_path, arm, monkeypatch, reason):
    from gw_kernel.read_session import open as opener
    from okf_ext import readindex

    layout = _fallback_layout(tmp_path / "fallback", inexact=reason == "inexact")
    expected = reason
    if reason == "disabled":
        index_arm(layout, "scan")
    elif reason == "busy":

        def busy(*args, **kwargs):
            raise readindex.IndexBusy("held")

        monkeypatch.setattr(opener.readindex, "open_index", busy)
    elif reason == "unsettled":
        real = readindex.reconcile

        def unsettled(index, **kwargs):
            return replace(real(index, **kwargs), unsettled=("work/a.md",))

        monkeypatch.setattr(opener.readindex, "reconcile", unsettled)
        expected = "unavailable"
    elif reason == "unavailable":

        def unavailable(*args, **kwargs):
            raise readindex.IndexUnavailable("gone")

        monkeypatch.setattr(opener.readindex, "open_index", unavailable)
    else:
        expected = "unavailable"
    _bundle, plan = _compare(layout, ["work/a"], "work/e", expected=expected)
    monkeypatch.undo()
    # The same corpus on a healthy index must plan identically.
    layout2 = _fallback_layout(tmp_path / "healthy", inexact=reason == "inexact")
    index_arm(layout2, "index")
    healthy_expected = "unavailable" if reason == "inexact" else None
    _bundle2, healthy = _compare(layout2, ["work/a"], "work/e", expected=healthy_expected)
    assert _sans_root(plan) == _sans_root(healthy)


def test_first_reparent_after_v1_database_matches_full_plan(tmp_path, arm, monkeypatch):
    from gw_kernel.read_session import open_read_session
    from okf_ext.readindex import store

    layout = _fallback_layout(tmp_path)
    monkeypatch.setattr(store, "SCHEMA_VERSION", 1)
    with open_read_session(layout):
        pass
    monkeypatch.setattr(store, "SCHEMA_VERSION", 2)
    _compare(layout, ["work/a"], "work/e", expected=None)
