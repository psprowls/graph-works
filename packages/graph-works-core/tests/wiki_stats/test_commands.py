"""`compute_stats` over hand-built bundles -- one scenario per structural
property the seven-key contract locks: page/edge counts, components, hub
ordering, orphans, sinks, and the out_edges filter itself."""

from __future__ import annotations

from pathlib import Path

from graph_works_core.wiki_stats.commands import HubEntry, WikiStats, compute_stats
from okf_io import load_bundle


def _write(root: Path, member: str, links: str = "") -> None:
    path = root / f"{member}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"\n{links}\n" if links else "\n"
    path.write_text(f"---\ntype: Explanation\ntitle: {member}\n---\n{body}", encoding="utf-8")


def test_an_empty_bundle_reports_all_zeros(tmp_path):
    root = tmp_path / "okf"
    root.mkdir()

    stats = compute_stats(load_bundle(root))

    assert stats == WikiStats(
        total_pages=0,
        total_edges=0,
        component_count=0,
        top_outbound_hubs=(),
        top_inbound_hubs=(),
        orphans=(),
        sinks=(),
    )


def test_a_single_unlinked_page_is_its_own_orphan_sink_and_component(tmp_path):
    root = tmp_path / "okf"
    root.mkdir()
    _write(root, "concepts/alone")

    stats = compute_stats(load_bundle(root))

    assert stats.total_pages == 1
    assert stats.total_edges == 0
    assert stats.component_count == 1
    assert stats.orphans == ("concepts/alone",)
    assert stats.sinks == ("concepts/alone",)


def test_a_hub_and_spoke_ranks_the_hub_first_by_inbound_degree(tmp_path):
    root = tmp_path / "okf"
    root.mkdir()
    _write(root, "concepts/hub")
    for name in ("a", "b", "c"):
        _write(root, f"concepts/{name}", "See [hub](/concepts/hub.md).")

    stats = compute_stats(load_bundle(root))

    assert stats.top_inbound_hubs[0] == HubEntry(page="concepts/hub", degree=3)
    assert stats.total_edges == 3
    assert stats.component_count == 1
    assert stats.orphans == ("concepts/a", "concepts/b", "concepts/c")
    assert stats.sinks == ("concepts/hub",)


def test_two_disconnected_clusters_report_two_components(tmp_path):
    root = tmp_path / "okf"
    root.mkdir()
    _write(root, "concepts/a1", "See [a2](/concepts/a2.md).")
    _write(root, "concepts/a2")
    _write(root, "concepts/b1", "See [b2](/concepts/b2.md).")
    _write(root, "concepts/b2")

    stats = compute_stats(load_bundle(root))

    assert stats.component_count == 2


def test_external_broken_and_image_links_do_not_count_as_edges(tmp_path):
    root = tmp_path / "okf"
    root.mkdir()
    _write(
        root,
        "concepts/noisy",
        "External: [ext](https://example.com). Broken: [gone](/concepts/missing.md). Image: ![pic](/concepts/pic.png).",
    )

    stats = compute_stats(load_bundle(root))

    assert stats.total_edges == 0
    assert stats.sinks == ("concepts/noisy",)


def test_a_link_to_the_index_is_a_member_but_not_an_edge(tmp_path):
    """`index.md` resolves via `bundle.has_member` but is never in
    `bundle.concepts` -- the one way to reach the `target_id in
    bundle.concepts` False branch of `_out_edges`."""
    root = tmp_path / "okf"
    root.mkdir()
    (root / "index.md").write_text("# Index\n", encoding="utf-8")
    _write(root, "concepts/a", "See the [index](/index.md).")

    stats = compute_stats(load_bundle(root))

    assert stats.total_edges == 0
    assert stats.sinks == ("concepts/a",)


def test_tied_degree_pages_order_deterministically_by_page_id(tmp_path):
    root = tmp_path / "okf"
    root.mkdir()
    _write(root, "concepts/z", "See [a](/concepts/a.md).")
    _write(root, "concepts/y", "See [a](/concepts/a.md).")
    _write(root, "concepts/a")

    first = compute_stats(load_bundle(root)).top_outbound_hubs
    second = compute_stats(load_bundle(root)).top_outbound_hubs

    assert first == second
    assert [h.page for h in first if h.degree == 1] == ["concepts/y", "concepts/z"]


def test_a_self_link_counts_toward_its_own_in_and_out_degree(tmp_path):
    root = tmp_path / "okf"
    root.mkdir()
    _write(root, "concepts/loop", "See [itself](/concepts/loop.md).")

    stats = compute_stats(load_bundle(root))

    assert stats.top_outbound_hubs[0] == HubEntry(page="concepts/loop", degree=1)
    assert stats.top_inbound_hubs[0] == HubEntry(page="concepts/loop", degree=1)
    assert stats.orphans == ()
    assert stats.sinks == ()


def test_top_caps_the_hub_lists(tmp_path):
    root = tmp_path / "okf"
    root.mkdir()
    _write(root, "concepts/hub")
    for name in ("a", "b", "c"):
        _write(root, f"concepts/{name}", "See [hub](/concepts/hub.md).")

    stats = compute_stats(load_bundle(root), top=1)

    assert len(stats.top_inbound_hubs) == 1
    assert stats.top_inbound_hubs[0].page == "concepts/hub"


def test_a_link_to_an_asset_file_does_not_count_as_an_edge(tmp_path):
    """Links to non-.md members (like assets) are recognized as members but
    not counted as concept edges since they're not in bundle.concepts."""
    root = tmp_path / "okf"
    root.mkdir()
    (root / "assets").mkdir()
    (root / "assets" / "data.json").write_text("{}", encoding="utf-8")
    _write(root, "concepts/referring", "See [data](/assets/data.json).")

    stats = compute_stats(load_bundle(root))

    assert stats.total_edges == 0
    assert stats.sinks == ("concepts/referring",)
