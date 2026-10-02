"""`scripts/read_bench_corpus.py`: a deterministic synthetic workspace for read benchmarks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from graph_works_core.work import commands as work
from graph_works_core.workspace.bundle import load_workspace_bundle
from okf_io import build_link_graph

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import read_bench_corpus  # noqa: E402
from read_bench_corpus import generate  # noqa: E402

WIKI_LANES = ("docs/", "sources/", "adrs/", "work/")


def _generated_files(bundle_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(bundle_dir).as_posix(): path.read_bytes()
        for path in sorted(bundle_dir.rglob("*.md"))
        if path.relative_to(bundle_dir).parts[0] == "work" or path.name.startswith("page-") or path.name == "malformed.md"
    }


def test_counts_match_the_shape(tmp_path: Path) -> None:
    corpus = generate(tmp_path / "c", items=20, wiki_pages=12)
    assert (corpus.items, corpus.wiki_pages) == (20, 12)
    assert corpus.active_items == 18  # items 9 and 19 are archived
    bundle = corpus.layout.bundle_dir
    assert len(list((bundle / "work").rglob("references/01-design.md"))) == 20
    assert len(list(bundle.rglob("page-*.md"))) == 12
    assert len(work.run_work_list(corpus.layout)) == corpus.active_items


def test_samples_point_at_real_members(tmp_path: Path) -> None:
    corpus = generate(tmp_path / "c", items=20, wiki_pages=12)
    assert corpus.sample_item == "work/epic-00000/children/feature-00001"
    assert (corpus.layout.bundle_dir / f"{corpus.sample_item}.md").is_file()
    assert corpus.sample_page == "docs/explanations/page-00000"
    assert (corpus.layout.bundle_dir / f"{corpus.sample_page}.md").is_file()


def test_no_wiki_pages_means_no_sample_page(tmp_path: Path) -> None:
    corpus = generate(tmp_path / "c", items=1, wiki_pages=0, malformed=False)
    assert corpus.sample_page is None
    assert corpus.sample_item == "work/epic-00000"


def test_generated_links_resolve_and_one_page_is_malformed(tmp_path: Path) -> None:
    corpus = generate(tmp_path / "c", items=20, wiki_pages=12)
    bundle = load_workspace_bundle(corpus.layout)
    broken = [link for link in build_link_graph(bundle).broken if link.source.startswith(WIKI_LANES)]
    assert broken == []
    malformed = [cid for cid, doc in bundle.concepts.items() if doc.parse_error is not None]
    assert malformed == ["docs/explanations/malformed"]


def test_the_host_is_a_git_repository_with_tracked_sources(tmp_path: Path) -> None:
    corpus = generate(tmp_path / "c", items=1, wiki_pages=1)
    host = corpus.layout.root.parent
    listing = subprocess.run(
        ["git", "ls-files"], cwd=host, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.split()
    assert listing == [f"src/module_{k:02d}.py" for k in range(read_bench_corpus.SOURCE_FILES)]


def test_generation_is_deterministic(tmp_path: Path) -> None:
    first = generate(tmp_path / "a", items=25, wiki_pages=9)
    second = generate(tmp_path / "b", items=25, wiki_pages=9)
    assert _generated_files(first.layout.bundle_dir) == _generated_files(second.layout.bundle_dir)


@pytest.mark.parametrize(("items", "wiki_pages"), [(0, 1), (1, -1)])
def test_rejects_impossible_sizes(tmp_path: Path, items: int, wiki_pages: int) -> None:
    with pytest.raises(ValueError):
        generate(tmp_path / "c", items=items, wiki_pages=wiki_pages)


def test_refuses_a_non_empty_root(tmp_path: Path) -> None:
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "x").write_text("x", encoding="utf-8", newline="\n")
    with pytest.raises(FileExistsError):
        generate(tmp_path / "c", items=1, wiki_pages=0)
