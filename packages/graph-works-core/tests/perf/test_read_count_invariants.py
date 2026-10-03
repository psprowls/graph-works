"""End-to-end D-004 structural counts for the benchmark's public read probes.

Latency belongs in the read performance report. Warm and edit queue probes
abort at the first parse above their literal budget, avoiding its quadratic
1000-item routing path without weakening the invariant. Cold queue liveness
also stops after its first real parse; it does not claim to measure a complete
queue. Priming the read index suffices for queue: routing has no additional
cache to warm. Corpora are shared only by reads; edit tests get fresh corpora.
Before an invariant cutoff, already-observed graph/Git violations remain
ordinary assertion failures, distinct from the expected parse sentinel.
Cold counter liveness uses 100 items only after the review run took 89.88s
(the plan permits dropping its size 1000 above 60s); warm checks retain both.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from graph_works_core.read_session import open_read_session
from okf_io import build_link_graph, load_bundle
from okf_io.document import Document

# Scripts are repository tooling, imported only by this test (as in scripts/tests).
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))

from bench_reads import READS, Target
from read_bench_corpus import Corpus, generate
from read_bench_counters import Counts, counting

PROBES = (
    "work.status",
    "work.list",
    "work.item",
    "work.queue",
    "wiki.page",
    "wiki.tree",
    "wiki.citations",
    "search.brief-lexical",
    "search.brief-hybrid",
)
WORK_PROBES = ("work.status", "work.list", "work.item", "work.queue")
SIZES = (100, 1_000)
DISPLAY_PARSE_BUGS = {
    "work.item": "work/bug-work-item-warm-read-breaks-the",
    "work.queue": "work/bug-work-queue-read-count-invariants",
    "wiki.page": "work/bug-wiki-page-warm-read-breaks-the",
    "wiki.tree": "work/bug-wiki-tree-warm-zero-parses",
}
EDIT_PARSE_BUGS = {
    **DISPLAY_PARSE_BUGS,
    "search.brief-lexical": "work/bug-search-brief-one-edit-reparse",
    "search.brief-hybrid": "work/bug-search-brief-one-edit-reparse",
}


class _ParseBudgetExceeded(AssertionError):
    """Stop an expensive read at the first demonstrated count violation."""


def _probe_cases(bugs: dict[str, str]):
    return [
        pytest.param(
            probe,
            marks=pytest.mark.xfail(strict=True, reason=bugs[probe], raises=_ParseBudgetExceeded),
        )
        if probe in bugs
        else probe
        for probe in PROBES
    ]


@contextmanager
def _bounded_counting(max_parses: int, *, enforce_structural_counts: bool = True) -> Iterator[Counts]:
    with counting() as counts, pytest.MonkeyPatch.context() as patch:
        counted_parse = Document.parse

        def parse(cls: type[Document], text: str, *, path: Path | None = None) -> Document:
            result = counted_parse(text, path=path)
            if counts.files_parsed > max_parses:
                if enforce_structural_counts:
                    assert counts.link_graph_builds == 0, f"{counts.link_graph_builds} link graph builds before cutoff"
                    if max_parses == 0:
                        assert counts.git_calls == 0, f"{counts.git_calls} Git calls before warm cutoff"
                raise _ParseBudgetExceeded(f"{counts.files_parsed} parses exceeds budget {max_parses}")
            return result

        patch.setattr(Document, "parse", classmethod(parse))
        yield counts


def _target(corpus: Corpus) -> Target:
    return Target(
        label=f"{corpus.items}x{corpus.wiki_pages}",
        layout=corpus.layout,
        sample_item=corpus.sample_item,
        sample_page=corpus.sample_page,
        items=corpus.items,
        wiki_pages=corpus.wiki_pages,
    )


def _prime(probe: str, target: Target) -> None:
    if probe == "work.queue":
        with open_read_session(target.layout):
            pass
    else:
        assert READS[probe](target) is not None


def test_warm_parse_cutoff_preserves_observed_git_failure() -> None:
    with pytest.raises(AssertionError, match="Git") as failure, _bounded_counting(0):
        subprocess.run(["git", "--version"], check=True, capture_output=True)
        Document.parse("# Page\n")
    assert type(failure.value) is AssertionError


@pytest.mark.parametrize("budget", (0, 1), ids=("warm", "edit"))
def test_parse_cutoff_preserves_observed_link_graph_failure(tmp_path: Path, budget: int) -> None:
    bundle = load_bundle(tmp_path)
    with pytest.raises(AssertionError, match="link graph") as failure, _bounded_counting(budget):
        build_link_graph(bundle)
        for _ in range(budget + 1):
            Document.parse("# Page\n")
    assert type(failure.value) is AssertionError


def test_edit_parse_cutoff_does_not_impose_a_git_budget() -> None:
    with pytest.raises(_ParseBudgetExceeded), _bounded_counting(1) as counts:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
        Document.parse("# First\n")
        Document.parse("# Second\n")
    assert counts.git_calls == 1


@pytest.fixture(scope="module")
def corpus_for(tmp_path_factory: pytest.TempPathFactory) -> Callable[[int, int], Corpus]:
    root = tmp_path_factory.mktemp("read-count-corpora")
    corpora: dict[tuple[int, int], Corpus] = {}

    def make(items: int, wiki_pages: int) -> Corpus:
        key = (items, wiki_pages)
        if key not in corpora:
            corpora[key] = generate(root / f"{items}x{wiki_pages}", items=items, wiki_pages=wiki_pages)
        return corpora[key]

    return make


@pytest.mark.parametrize("size", (100,))
@pytest.mark.parametrize("probe", PROBES)
def test_counters_are_live(corpus_for, tmp_path: Path, probe: str, size: int) -> None:
    corpus = corpus_for(size, size)
    # A new cache guarantees a cold read even though corpus bytes are shared.
    target = replace(_target(corpus), layout=replace(corpus.layout, cache_dir=tmp_path / "cache"))
    if probe == "work.queue":
        # Cold liveness imposes no warm graph/Git invariant.
        with pytest.raises(_ParseBudgetExceeded), _bounded_counting(0, enforce_structural_counts=False) as counts:
            READS[probe](target)
    else:
        with counting() as counts:
            assert READS[probe](target) is not None
    assert counts.files_parsed > 0, f"{probe}: cold counter is disconnected"


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("probe", _probe_cases(DISPLAY_PARSE_BUGS))
def test_warm_unchanged_read_does_no_structural_work(corpus_for, probe: str, size: int) -> None:
    target = _target(corpus_for(size, size))
    _prime(probe, target)
    with _bounded_counting(0) as counts:
        assert READS[probe](target) is not None
    assert (counts.files_parsed, counts.link_graph_builds, counts.git_calls) == (0, 0, 0), probe


@pytest.mark.parametrize("probe", _probe_cases(EDIT_PARSE_BUGS))
def test_one_file_edit_parses_one_file(tmp_path: Path, probe: str) -> None:
    corpus = generate(tmp_path / "corpus", items=100, wiki_pages=100)
    target = _target(corpus)
    _prime(probe, target)
    # Edit another work item, so requested-item/page reparsing cannot hide it.
    edited = corpus.layout.bundle_dir / "work/feature-00004.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8", newline="\n")
    with _bounded_counting(1) as counts:
        assert READS[probe](target) is not None
    assert counts.files_parsed == 1, probe
    assert counts.link_graph_builds == 0, probe


@pytest.mark.parametrize("probe", WORK_PROBES)
def test_unrelated_wiki_growth_does_not_change_work_read_counts(corpus_for, probe: str) -> None:
    seen = []
    for wiki_pages in (100, 1_000):
        target = _target(corpus_for(100, wiki_pages))
        _prime(probe, target)
        with counting() as counts:
            assert READS[probe](target) is not None
        seen.append((counts.files_parsed, counts.link_graph_builds, counts.git_calls))
    assert seen[0] == seen[1], f"{probe}: unrelated wiki growth changed {seen}"
