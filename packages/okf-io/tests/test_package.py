from __future__ import annotations

import importlib.metadata
from datetime import date

import okf_io


def test_version_is_static():
    assert okf_io.__version__ == "0.1.0"


def test_version_matches_package_metadata():
    """The static version lives in two places; nothing else ties them together."""
    assert okf_io.__version__ == importlib.metadata.version("okf-io")


def test_public_surface():
    expected = {
        "Actor",
        "ActorKind",
        "Attester",
        "Bundle",
        "ChangeKind",
        "Describe",
        "Descriptions",
        "Drift",
        "Document",
        "EntryKind",
        "EntryTarget",
        "Executor",
        "Finding",
        "Frontmatter",
        "Generated",
        "IndexChange",
        "IndexUpdate",
        "Link",
        "LinkGraph",
        "Log",
        "LogAppend",
        "LogEntry",
        "LogSection",
        "Parameter",
        "ParseError",
        "Report",
        "Rule",
        "RuleContext",
        "Severity",
        "Source",
        "TrustTier",
        "UsageWindow",
        "Verified",
        "__version__",
        "append_log_entry",
        "build_frontmatter",
        "build_link_graph",
        "effective_status",
        "is_stale",
        "last_verified_at",
        "load",
        "load_bundle",
        "parse",
        "parse_log",
        "trust_tier",
        "update_index",
        "validate",
    }
    assert set(okf_io.__all__) == expected


def test_all_is_sorted_and_resolvable():
    assert list(okf_io.__all__) == sorted(okf_io.__all__)
    for name in okf_io.__all__:
        assert hasattr(okf_io, name), name


def test_nothing_private_is_exported():
    assert not [name for name in okf_io.__all__ if name.startswith("_") and name != "__version__"]
    assert "_yaml" not in okf_io.__all__


def test_dependency_rule_is_stated_in_the_docstring():
    assert okf_io.__doc__ is not None
    assert "two runtime dependencies" in okf_io.__doc__


def test_top_level_parse_and_load_are_reachable():
    doc = okf_io.parse("---\ntype: Metric\n---\n\n# Definition\n")
    assert isinstance(doc, okf_io.Document)
    assert doc.fm.type == "Metric"


def test_the_public_surface_alone_is_sufficient(tmp_path):
    """Read, derive, mutate, write back -- using only names from `__all__`.

    Every other test module imports from `okf_io.document` / `.models` /
    `.derive` directly, so nothing else would notice if the front door were
    missing something a consumer needs.
    """
    src = "\n".join(
        [
            "---",
            "type: Metric",
            "title: Revenue",
            "status: stable",
            "stale_after: 2026-12-31",
            "verified:",
            "  - { by: human:jsmith@acme, at: 2026-07-02T09:00:00Z }",
            "---",
            "",
            "# Definition",
            "",
            "Recognized revenue.",
            "",
        ]
    )
    target = tmp_path / "revenue.md"
    target.write_text(src, encoding="utf-8")

    doc = okf_io.load(target)
    assert isinstance(doc, okf_io.Document)
    assert isinstance(doc.fm, okf_io.Frontmatter)
    assert doc.parse_error is None

    assert okf_io.trust_tier(doc.fm) == "human-reviewed"
    assert okf_io.effective_status(doc.fm) == "stable"
    assert okf_io.is_stale(doc.fm, today=date(2027, 1, 1)) is True
    stamp = okf_io.last_verified_at(doc.fm)
    assert stamp is not None and stamp.year == 2026

    doc.set("status", "deprecated")
    doc.save()

    reread = okf_io.load(target)
    assert reread.fm.status == "deprecated"
    assert "Recognized revenue." in reread.body
    # The byte-fidelity promise, asserted from outside the package.
    assert reread.raw_text == src.replace("status: stable", "status: deprecated")


def test_the_public_surface_reaches_a_whole_bundle(tmp_path):
    """Walk, graph, validate -- using only names from `__all__`."""
    (tmp_path / "a.md").write_text(
        "---\ntype: Metric\ntitle: Revenue\ndescription: D\n---\n\n[t](./t.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "t.md").write_text(
        "---\ntype: Table\ntitle: Orders\ndescription: D\n---\n\n# Schema\n", encoding="utf-8"
    )

    loaded = okf_io.load_bundle(tmp_path)
    assert isinstance(loaded, okf_io.Bundle)
    assert loaded.by_type("Metric") == ("a",)

    graph = okf_io.build_link_graph(loaded)
    assert isinstance(graph, okf_io.LinkGraph)
    assert graph.backlinks["t"] == ("a",)

    report = okf_io.validate(loaded, today=date(2026, 8, 3))
    assert isinstance(report, okf_io.Report)
    assert report.ok is True
    # A clean two-file bundle produces no findings. If this fails in the future,
    # it means a new rule now fires on a minimal valid bundle — consider whether that is right.
    assert report.findings == ()

    strict = okf_io.validate(loaded, today=date(2026, 8, 3), strict=True)
    assert strict.ok is True


def test_the_writers_are_exported():
    import okf_io

    for name in (
        "Drift",
        "EntryTarget",
        "IndexChange",
        "IndexUpdate",
        "Log",
        "LogAppend",
        "LogEntry",
        "LogSection",
        "append_log_entry",
        "parse_log",
        "update_index",
    ):
        assert name in okf_io.__all__
        assert hasattr(okf_io, name)
