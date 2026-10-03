from __future__ import annotations

import os
import random
import shutil
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from graph_works_core.workspace.init import apply_init, plan_init

VOCAB = ["token", "refresh", "rotation", "storage", "retention", "blob", "lifecycle", "audit", "cohort", "metric"]
OKF_FIXTURES = Path(__file__).resolve().parents[3] / "okf-io" / "tests" / "fixtures" / "bundles"


class FakeEmbedder:
    model_id = "fake-embed-v1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        n = float(len(text) % 7 + 1)
        return [n, 1.0 / n, 0.5]


def synthetic(root: Path, *, seed: int, pages: int = 60) -> None:
    """Deterministic pages with shared words (ties), links (pinning) and edge cases."""
    rng = random.Random(seed)
    for i in range(pages):
        words = " ".join(rng.choice(VOCAB) for _ in range(rng.randint(0, 20)))
        links = " ".join(f"[l](/s/p{rng.randrange(pages)}.md)" for _ in range(rng.randint(0, 3)))
        (root / "s").mkdir(parents=True, exist_ok=True)
        (root / "s" / f"p{i}.md").write_text(
            f"---\ntitle: P{i}\n---\n{words}\n{links}\n", encoding="utf-8", newline="\n"
        )
    (root / "a.md").write_text("---\ntitle: Tie\n---\ntoken token\n", encoding="utf-8", newline="\n")
    (root / "a-b.md").write_text("---\ntitle: Tie\n---\ntoken token\n", encoding="utf-8", newline="\n")
    (root / "s" / "broken.md").write_text("---\ntitle: [x\n---\ntoken\n", encoding="utf-8", newline="\n")
    (root / "s" / "bom.md").write_bytes(b"\xef\xbb\xbf---\r\ntitle: Bom\r\n---\r\nrefresh rotation\r\n")


def settle_mtimes(root: Path) -> None:
    """Keep fixture creation outside the read index's deliberate racy-stat window."""
    for path in root.rglob("*.md"):
        os.utime(path, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))


@pytest.fixture
def make_workspace(tmp_path: Path):
    def make(kind: str, seed: int = 0):
        layout = apply_init(
            plan_init(tmp_path / f"{kind}-{seed}" / ".works", today=date(2026, 10, 2), topic="t")
        ).layout
        if kind == "synthetic":
            synthetic(layout.bundle_dir, seed=seed)
        else:
            shutil.copytree(OKF_FIXTURES / kind, layout.bundle_dir, dirs_exist_ok=True)
        settle_mtimes(layout.bundle_dir)
        return layout

    return make


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """Counts parses, hashes, tokenizations, link-graph builds, full loads and lexical write transactions."""
    import okf_ext.search as ext_search
    import okf_ext.search.index as ext_index
    from graph_works_core.query import commands as q
    from graph_works_core.query import lexical_store as ls
    from okf_ext.readindex import sync

    seen: Counter[str] = Counter()

    def wrap(module, name, key, weight=lambda *a, **k: 1):
        real = getattr(module, name)

        def counted(*args, **kwargs):
            seen[key] += weight(*args, **kwargs)
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, counted)

    wrap(sync, "read_member", "parsed")
    wrap(ls, "read_member", "parsed")
    wrap(q, "read_member", "parsed")
    wrap(sync, "_read_bytes", "hash_reads")
    wrap(q, "_page_hashes", "hashed", weight=lambda pages: len(pages))
    wrap(ext_index, "term_postings", "tokenized")
    wrap(ext_search, "term_postings", "tokenized")
    wrap(q, "build_link_graph", "link_graphs")
    wrap(q, "load_workspace_bundle", "full_loads")
    wrap(ls, "_begin_write", "lex_writes")
    return seen
