"""Search probes for the read benchmark: briefs, the cosine scan alone, and a cold lexical sync.

Search is not a display read, so the harness left it out; feature-search-freshness
registers it here. Every probe is deterministic and offline: `BenchEmbedder`
derives vectors from sha256, so `search.brief-hybrid` never reaches a network.

The live-workspace target (`items is None`) runs its search probes against a
scratch cache directory, outside the workspace. A hybrid probe writes vectors
under its own model id, and that would otherwise replace the workspace's real
embeddings.
"""

from __future__ import annotations

import hashlib
import struct
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from graph_works_core.query import commands as q
from graph_works_core.workspace.layout import WorkspaceLayout
from read_bench_counters import counting

try:
    from graph_works_core.query import lexical_store
except ImportError:  # The pre-read-model revision has no persisted lexical store.
    lexical_store = None

if TYPE_CHECKING:
    from collections.abc import Callable

    from bench_reads import Target

#: Task 0 Step 4 chooses the words: at least two the corpus generator emits and one it never does.
SEARCH_QUERY = "synthetic related zzznotaword"
TOP_K = 5


class BenchEmbedder:
    model_id = "bench-hash-v1"

    def embed_query(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest() * 8  # 256 bytes
        return [value / 2**31 for value in struct.unpack("<64i", digest)]


def search_layout(target: Target) -> WorkspaceLayout:
    if target.items is not None:
        return target.layout
    key = hashlib.sha256(str(target.layout.root).encode()).hexdigest()[:12]
    return replace(target.layout, cache_dir=Path(tempfile.gettempdir()) / f"gw-bench-search-{key}")


def _brief(target: Target, *, hybrid: bool) -> object:
    return q.plan_query_brief(
        SEARCH_QUERY, search_layout(target), embedder=BenchEmbedder() if hybrid else None, top_k=TOP_K
    )


SEARCH_READS: dict[str, Callable[[Target], object]] = {
    "search.brief-lexical": lambda t: _brief(t, hybrid=False),
    "search.brief-hybrid": lambda t: _brief(t, hybrid=True),
}


def time_cosine_scan(target: Target, repeats: int) -> tuple[list[float], dict[str, int]]:
    """D-004's latency evidence: the linear scan alone, over a populated index."""
    layout = search_layout(target)
    _brief(target, hybrid=True)
    vector = BenchEmbedder().embed_query(SEARCH_QUERY)
    timings = []
    for _ in range(repeats):
        started = time.perf_counter()
        q._cosine_search_sqlite(layout.cache_dir, vector, TOP_K * 3)
        timings.append((time.perf_counter() - started) * 1000)
    import sqlite3

    with sqlite3.connect(q._search_db(layout.cache_dir)) as conn:
        vectors = int(conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0])
    return timings, {"vectors": vectors}


def time_cold_sync(target: Target, repeats: int) -> tuple[list[float], dict[str, int | None]]:
    """One lexical brief after dropping the `lex_*` tables: the one-time cost the postings store pays."""
    if lexical_store is None:
        raise LookupError("lexical store unavailable in this revision")
    layout = search_layout(target)
    _brief(target, hybrid=False)  # read index warm, so only the lexical sync is cold
    db = q._search_db(layout.cache_dir)
    timings, counts = [], {}
    for _ in range(repeats):
        lexical_store.drop_tables(db)
        with counting() as seen:
            started = time.perf_counter()
            _brief(target, hybrid=False)
            timings.append((time.perf_counter() - started) * 1000)
        counts = {"pages_tokenized": seen.pages_tokenized}
    counts["store_bytes"] = sum(path.stat().st_size for path in db.parent.glob(f"{db.name}*"))
    return timings, counts


__all__ = ["SEARCH_QUERY", "SEARCH_READS", "BenchEmbedder", "search_layout", "time_cold_sync", "time_cosine_scan"]
