"""The query vertical: hybrid retrieval, two pipelines, and the citation guardrails.

`run_query` takes a `WorkspaceLayout` and a constructed embedder. It resolves
nothing, reads no environment and reads no clock — every path it touches is a
layout member, and the one vendor decision (which embedding model) is made by
whoever built the embedder.

**The corpus is `bundle.concepts`.** The module this ports from walked
`wiki/**/*.md` and skipped `index.md`, `log.md` and dot-prefixed components.
`okf_io.load_bundle` separates indexes, logs and assets into their own members,
so the skip-list has nothing left to skip: the three rules are unrepresentable
rather than enforced.

**The embedding index lives under `layout.cache_dir`** — `<cache>/search/search.db`.
Constraint 4 makes `_cache/` gitignored and scanner-excluded, which is what a
derived index needs on both counts. The lexical half is not persisted: it is
`okf_ext.search`, rebuilt per query from the bundle already in memory, which is
also what keeps one BM25 implementation in this workspace rather than two.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import shutil
import sqlite3
import struct
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from code_graph_io import GraphNotInitializedError, GraphReader, SchemaMismatchError, open_reader
from code_wiki_okf.config import load_config
from langchain_core.embeddings import Embeddings
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, tool
from models_io import make_bedrock_embeddings
from models_io.pricing import cost_for_usage
from okf_ext import search as ext_search
from okf_ext.schemas import load_schemas
from okf_io import Bundle, load_bundle
from subagents_io import FanOutResult, SubagentPool, TaskResult, write_trace_record

from graph_works_core.agent_substrate.agent_loop import run_tool_loop
from graph_works_core.agent_substrate.agent_tools import read_bounded_page
from graph_works_core.agent_substrate.roles import make_llm, role_binding
from graph_works_core.graph import graph_tools as gt
from graph_works_core.graph.commands import GraphTarget, graph_target
from graph_works_core.query.prompts.code_reader import CODE_READER_SYSTEM
from graph_works_core.query.prompts.librarian import build_librarian_system
from graph_works_core.query.prompts.synthesizer import SYNTHESIZER_SYSTEM
from graph_works_core.workspace.errors import QueryError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.provenance import head_sha

if TYPE_CHECKING:
    # Type position only. `query_orchestrator` imports this module at load time
    # (for `_read_file_bounded`), so a runtime import here would be circular —
    # `_initial_candidates` imports the same name inside its own body instead.
    from graph_works_core.query.query_orchestrator import InitialCandidate

logger = logging.getLogger(__name__)

#: Bounds on `top_k`. The reference raises `RuntimeError` outside them, which
#: says nothing a `ValueError` does not.
MIN_TOP_K = 3
MAX_TOP_K = 10

#: Where the derived index lives, relative to `layout.cache_dir`.
SEARCH_SUBDIR = "search"
SEARCH_DB_NAME = "search.db"

#: Bumped whenever the **embedding** index's on-disk shape or the manifest's
#: shape changes. A manifest recording any other value describes a table this
#: code cannot read, so it is re-embedded rather than interpreted.
#:
#: Deliberately NOT bumped when the persisted lexical index was removed: the
#: embedding table's schema did not change, and a bump would have charged every
#: existing workspace a full re-embed for a directory that stopped being
#: written. `_LEGACY_BM25_SUBDIR` is swept on build instead.
INDEX_SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"

#: The embedding model, named in exactly one place. `run_query` never reads
#: these — `default_embedder()` does, and it exists because the adapters are
#: zero-argument constructors and E7's CLI needs one call.
DEFAULT_EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
DEFAULT_EMBED_REGION = "us-east-1"

_DDL_PAGES = """
CREATE TABLE IF NOT EXISTS pages (
    path         TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    embedding    BLOB NOT NULL
)
"""
_PRAGMA_WAL = "PRAGMA journal_mode=WAL"

_RRF_K = 60
_OVERSAMPLE = 3
_EMBED_WARN_CHARS = 32_000


class Embedder(Protocol):
    """The one method retrieval calls, plus the name of the model behind it.

    `model_id` is what the index records so that changing the embedding model
    invalidates vectors produced by the previous one. A fake is five lines and
    no network.
    """

    @property
    def model_id(self) -> str: ...

    def embed_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True)
class _NamedEmbedder:
    """The model id, carried beside a client that has no place to put it.

    `make_bedrock_embeddings` is annotated `-> Embeddings`, and langchain's
    base class declares no `model_id`, so a bare return fails `mypy --strict`
    against the widened protocol. `BedrockEmbeddings` itself still satisfies
    the protocol structurally — a caller who constructs one directly is
    unaffected.
    """

    model_id: str
    delegate: Embeddings

    def embed_query(self, text: str) -> list[float]:
        return self.delegate.embed_query(text)


def default_embedder() -> Embedder:
    """The Bedrock embedder this vertical defaults to, when a caller has no opinion.

    `run_query` does not call this: its `embedder` argument is required and has
    no default, which is what keeps a vendor decision out of the pipeline. This
    is for the zero-argument adapter constructors and for E7's command line.
    """
    return _NamedEmbedder(
        DEFAULT_EMBED_MODEL_ID,
        make_bedrock_embeddings(DEFAULT_EMBED_MODEL_ID, region=DEFAULT_EMBED_REGION),
    )


@dataclass(frozen=True)
class PreparedQueryRetrieval:
    """The shared front half both pipelines run on. Retrieval happens once."""

    layout: WorkspaceLayout
    bundle: Bundle
    top_pages: tuple[str, ...]
    search_scores: Mapping[str, Mapping[str, float]]


def _corpus(bundle: Bundle) -> list[tuple[str, str]]:
    """Every concept as `(concept_id, raw_text)`, in id order.

    `Bundle.indexes` and `Bundle.logs` are separate members, so this yields
    exactly the pages that were meant to be indexed — the reference's
    three-rule skip-list has nothing left to skip.
    """
    return [(concept_id, bundle.concepts[concept_id].raw_text) for concept_id in sorted(bundle.concepts)]


def _search_db(cache_dir: Path) -> Path:
    return cache_dir / SEARCH_SUBDIR / SEARCH_DB_NAME


def _search_dir(cache_dir: Path) -> Path:
    return cache_dir / SEARCH_SUBDIR


def _manifest_path(cache_dir: Path) -> Path:
    return _search_dir(cache_dir) / MANIFEST_NAME


@dataclass(frozen=True)
class _Manifest:
    """What the index on disk was derived from. Three fields, all three read."""

    schema_version: int
    embed_signature: str
    corpus_fingerprint: str


def _page_hashes(pages: Sequence[tuple[str, str]]) -> dict[str, str]:
    """`{concept_id: sha256(raw_text)}`, computed once per build.

    The same hashes serve the corpus fingerprint and the incremental embed
    step, so a page's text is hashed exactly once no matter which path runs.
    """
    return {concept_id: hashlib.sha256(text.encode()).hexdigest() for concept_id, text in pages}


def _corpus_fingerprint(page_hashes: Mapping[str, str]) -> str:
    """sha256 over the sorted `(concept_id, page_hash)` pairs.

    Both halves are delimited, which makes the encoding injective: a renamed
    page cannot produce the fingerprint of an edited one.
    """
    digest = hashlib.sha256()
    for concept_id in sorted(page_hashes):
        digest.update(concept_id.encode())
        digest.update(b"\0")
        digest.update(page_hashes[concept_id].encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _read_manifest(cache_dir: Path) -> _Manifest | None:
    """The manifest, or `None` when it is missing, unreadable or malformed.

    Every `None` means one thing to every caller: nothing on disk vouches for
    the index, so it must be rebuilt. A partially-typed manifest is no more
    trustworthy than no manifest, so it is not salvaged field by field.
    """
    try:
        raw = json.loads(_manifest_path(cache_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    version = raw.get("schema_version")
    signature = raw.get("embed_signature")
    fingerprint = raw.get("corpus_fingerprint")
    if not isinstance(version, int) or isinstance(version, bool):
        return None
    if not isinstance(signature, str) or not isinstance(fingerprint, str):
        return None
    return _Manifest(version, signature, fingerprint)


def _write_manifest(cache_dir: Path, manifest: _Manifest) -> None:
    """Record what the index was derived from.

    Called **last** by `build_index`, after the sqlite commit: an interrupted
    rebuild must leave the previous embedding table intact and a manifest that
    still describes it, never a manifest claiming a generation that is not on
    disk.
    """
    path = _manifest_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": manifest.schema_version,
                "embed_signature": manifest.embed_signature,
                "corpus_fingerprint": manifest.corpus_fingerprint,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _rrf_fuse(bm25_ranks: dict[str, int], embed_ranks: dict[str, int], k: int = _RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion: `1/(k + rank_bm25) + 1/(k + rank_embed)`.

    A page absent from one ranking scores against a sentinel rank of `n + k`,
    where `n` is the number of distinct pages across both rankings.
    """
    all_pages = set(bm25_ranks) | set(embed_ranks)
    n = len(all_pages)
    return {p: 1.0 / (k + bm25_ranks.get(p, n + k)) + 1.0 / (k + embed_ranks.get(p, n + k)) for p in all_pages}


@dataclass(frozen=True)
class IndexRefresh:
    """What a refresh did, and why.

    `reason` takes a closed vocabulary — `"no-manifest"`, `"corpus-changed"`,
    `"model-changed"`, `"schema-changed"`, `"forced"`, `"empty-bundle"`, or
    `None` when the index was already fresh and nothing ran.
    """

    rebuilt: bool
    reason: str | None
    pages: int
    embedded: int
    pruned: int


#: A directory earlier versions wrote a persisted lexical index into. Swept on
#: every build; nothing reads it.
_LEGACY_BM25_SUBDIR = "bm25"


def build_index(bundle: Bundle, cache_dir: Path, *, embedder: Embedder) -> IndexRefresh:
    """Rebuild the embedding index under `cache_dir`, unconditionally.

    The freshness decision belongs to `refresh_index`; this is the path
    underneath it, and the escape hatch for a cache a caller suspects. The
    lexical half has nothing to rebuild here — `okf_ext.search` builds it in
    memory per query. The embedding table is incremental — but only where the
    manifest vouches for the signature its rows were written under.
    """
    pages = _corpus(bundle)
    if not pages:
        logger.warning("build_index: no concepts in bundle")
        return IndexRefresh(rebuilt=False, reason="empty-bundle", pages=0, embedded=0, pruned=0)

    page_hashes = _page_hashes(pages)
    signature = embedder.model_id

    # ---- Lexical index ----
    # There isn't one on disk any more. Sweep what earlier versions left behind
    # so a long-lived cache does not keep nine dead files; best effort, and
    # deliberately not a schema bump — see `INDEX_SCHEMA_VERSION`.
    for name in (_LEGACY_BM25_SUBDIR, f"{_LEGACY_BM25_SUBDIR}.new", f"{_LEGACY_BM25_SUBDIR}.old"):
        shutil.rmtree(cache_dir / SEARCH_SUBDIR / name, ignore_errors=True)

    # ---- Embedding index (incremental, when the manifest vouches for it) ----
    previous = _read_manifest(cache_dir)
    vouched = (
        previous is not None
        and previous.schema_version == INDEX_SCHEMA_VERSION
        and previous.embed_signature == signature
    )

    db_path = _search_db(cache_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    embedded = 0
    stale: list[str] = []
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_PRAGMA_WAL)
        conn.execute(_DDL_PAGES)
        conn.commit()

        if not vouched:
            # Nothing on disk says which model wrote these vectors, or at what
            # dimension. Re-embed rather than score rows of unknown provenance.
            logger.info("no manifest vouches for the embedding table — re-embedding every page")
            conn.execute("DELETE FROM pages")

        existing = {str(row[0]) for row in conn.execute("SELECT path FROM pages")}
        stale = sorted(existing - set(bundle.concepts))
        conn.executemany("DELETE FROM pages WHERE path = ?", [(path,) for path in stale])

        for path, text in pages:
            if len(text) > _EMBED_WARN_CHARS:
                logger.warning(
                    "Page %s exceeds %d chars (%d); the embedding model may truncate it",
                    path,
                    _EMBED_WARN_CHARS,
                    len(text),
                )
            content_hash = page_hashes[path]
            row = conn.execute("SELECT content_hash FROM pages WHERE path = ?", (path,)).fetchone()
            if row is not None and row[0] == content_hash:
                continue  # unchanged, and the manifest vouches for the vector

            vec = embedder.embed_query(text)
            blob = struct.pack(f"{len(vec)}f", *vec)
            conn.execute(
                "INSERT OR REPLACE INTO pages (path, content_hash, embedding) VALUES (?, ?, ?)",
                (path, content_hash, blob),
            )
            embedded += 1
        # The prune and the inserts share this one commit, so a concurrent
        # reader under WAL sees a whole generation, never a half-pruned set.
        conn.commit()
    finally:
        conn.close()

    _write_manifest(cache_dir, _Manifest(INDEX_SCHEMA_VERSION, signature, _corpus_fingerprint(page_hashes)))
    return IndexRefresh(rebuilt=True, reason="forced", pages=len(pages), embedded=embedded, pruned=len(stale))


def _staleness(cache_dir: Path, *, pages: Sequence[tuple[str, str]], signature: str) -> str | None:
    """Why the index must be rebuilt, or `None` when it is already fresh.

    Three fields, all three compared, nothing else. A field written but never
    read is a field that can drift silently, which is the class of bug this
    replaces.
    """
    manifest = _read_manifest(cache_dir)
    if manifest is None:
        return "no-manifest"
    if manifest.schema_version != INDEX_SCHEMA_VERSION:
        return "schema-changed"
    if manifest.embed_signature != signature:
        return "model-changed"
    if manifest.corpus_fingerprint != _corpus_fingerprint(_page_hashes(pages)):
        return "corpus-changed"
    return None


def refresh_index(bundle: Bundle, cache_dir: Path, *, embedder: Embedder) -> IndexRefresh:
    """Make the index match *bundle*. Idempotent; rebuilds only when it must.

    Freshness is a property of the content the index was derived from, not of
    the files it was written to. The existence check this replaces was a
    one-bit, monotone predicate: it flipped false→true once per workspace and
    never back, which is why a deleted page kept being retrieved.
    """
    pages = _corpus(bundle)
    if not pages:
        logger.warning("refresh_index: no concepts in bundle — nothing to index")
        return IndexRefresh(rebuilt=False, reason="empty-bundle", pages=0, embedded=0, pruned=0)

    reason = _staleness(cache_dir, pages=pages, signature=embedder.model_id)
    if reason is None:
        return IndexRefresh(rebuilt=False, reason=None, pages=len(pages), embedded=0, pruned=0)

    logger.info("refreshing search index (%s)", reason)
    return replace(build_index(bundle, cache_dir, embedder=embedder), reason=reason)


def lexical_query(bundle: Bundle, query_text: str, top_k: int) -> tuple[list[str], list[float]]:
    """Top-k `(concept_ids, bm25_scores)` for *query_text* over *bundle*.

    The index is built per call. That is not a regression on the persisted one
    this replaces: every query already holds the parsed corpus in memory, so
    this is a tokenize pass with no I/O — cheaper than loading nine files back
    off disk.

    Callers must check `ext_search.tokenize(query_text)` first. `search()`
    returns the entire corpus at score `0.0` for text it cannot tokenize, and
    fusing that as lexical signal is a full-corpus ranking in concept-id order.
    """
    hits = ext_search.search(ext_search.build_index(bundle), query_text, limit=top_k)
    return [hit.concept_id for hit in hits], [hit.score for hit in hits]


def _cosine_search_sqlite(cache_dir: Path, query_vec: list[float], top_k: int) -> list[tuple[str, float]]:
    """Linear cosine scan over embeddings in `search.db`.

    Opens and closes the connection per call (no module-level state). Returns
    `(page_path, cosine_score)` pairs, sorted descending.

    A stored vector of the wrong dimension is refused rather than scored over
    its overlap. With the manifest in place a mismatch can only mean a
    hand-edited or corrupted cache — and a wrong ranking is the one outcome
    this whole change exists to end, so the invariant is enforced twice.
    """
    db_path = _search_db(cache_dir)
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT path, embedding FROM pages").fetchall()
    finally:
        conn.close()

    q_mag = math.sqrt(sum(x * x for x in query_vec))
    results: list[tuple[str, float]] = []
    for path, blob in rows:
        stored_dim = len(blob) // 4
        if stored_dim != len(query_vec):
            raise QueryError(
                f"embedding dimension mismatch for {path!r}: the index holds "
                f"{stored_dim}-dimensional vectors, the query is {len(query_vec)}-dimensional. "
                f"Delete {_search_dir(cache_dir)} and re-run to rebuild the index."
            )
        vec = struct.unpack(f"{stored_dim}f", blob)
        dot = sum(a * b for a, b in zip(query_vec, vec, strict=True))
        v_mag = math.sqrt(sum(x * x for x in vec))
        score = dot / (q_mag * v_mag) if (q_mag and v_mag) else 0.0
        results.append((path, score))
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]


def _prepare_query_retrieval(
    query: str,
    layout: WorkspaceLayout,
    bundle: Bundle,
    *,
    top_k: int,
    embedder: Embedder,
) -> PreparedQueryRetrieval:
    """Retrieve once. Both pipelines share the result; a fallback does not re-retrieve.

    Freshness is `refresh_index`'s call, not this function's: the reader states
    what it needs and the index makes itself match. Stays private — the epic
    named this function as the reason the adapters live in this band, and
    making it public would remove the reason without removing the coupling.
    """
    if not (MIN_TOP_K <= top_k <= MAX_TOP_K):
        raise ValueError(f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K} (got {top_k})")

    if not bundle.concepts:
        raise QueryError(f"no concepts to search under {bundle.root}: ingest at least one concept before querying")

    refresh_index(bundle, layout.cache_dir, embedder=embedder)

    # `search()` returns the whole corpus at 0.0 for text it cannot tokenize —
    # empty, all-stopwords, or any non-Latin script. Its own docstring tells
    # callers to check first rather than fuse that as lexical signal.
    if ext_search.tokenize(query):
        bm25_paths, bm25_raw = lexical_query(bundle, query, top_k * _OVERSAMPLE)
    else:
        bm25_paths, bm25_raw = [], []
    bm25_rank_map = {p: i + 1 for i, p in enumerate(bm25_paths)}
    bm25_score_map = dict(zip(bm25_paths, bm25_raw, strict=False))

    embed_hits = _cosine_search_sqlite(layout.cache_dir, embedder.embed_query(query), top_k * _OVERSAMPLE)
    embed_rank_map = {path: i + 1 for i, (path, _) in enumerate(embed_hits)}
    embed_score_map = dict(embed_hits)

    fused = _rrf_fuse(bm25_rank_map, embed_rank_map)
    top_pages = tuple(sorted(fused, key=lambda page: fused[page], reverse=True)[:top_k])
    return PreparedQueryRetrieval(
        layout=layout,
        bundle=bundle,
        top_pages=top_pages,
        search_scores={
            page: {
                "bm25": bm25_score_map.get(page, 0.0),
                "embed": embed_score_map.get(page, 0.0),
                "rrf": fused.get(page, 0.0),
            }
            for page in top_pages
        },
    )


_GRAPH_UNAVAILABLE_STDERR = "[graph unavailable: run 'gw graph build' to enable code-graph grounding tools]"


def build_graph_tools(reader: GraphReader) -> list[BaseTool]:
    """The five grounding tools, each closed over an open *reader*.

    C7 landed these as plain typed callables in `graph_works_core.graph.graph_tools`,
    deliberately without the decorator: C7 depends on C1 only, and a
    `langchain_core` import there would have falsified C2's acceptance. C5 is
    the only consumer and is downstream of C2, so the binding sits here.

    Reader lifetime is the caller's: open at command entry, close in `finally`.
    Public, and it moves to package root when it gets a second consumer.
    """

    @tool
    def cg_find(name: str | None = None, kind: str | None = None, in_package: str | None = None) -> str:
        """Find graph nodes by name and/or kind and/or containing package.

        Args:
            name: optional symbol name (exact match).
            kind: optional; one of class|function|file|module|package|entry_point|test_suite.
            in_package: optional case-insensitive package name.
        """
        return gt.find(reader, name=name, kind=kind, in_package=in_package)

    @tool
    def cg_describe(kind: str, identifier: str) -> str:
        """Describe one graph entity by kind and identifier.

        Args:
            kind: one of repository|package|app|path|test_suite|entry_point|dependency|agent_plugin|builtin.
            identifier: string; ignored when kind=repository. For entry_point the
                qualified `package:entry` form resolves an ambiguous bare name.
        """
        return gt.describe(reader, kind=kind, identifier=identifier)

    @tool
    def cg_callers(name: str, depth: int = 3) -> str:
        """Find callers of a function or method, up to `depth` levels out.

        Args:
            name: required symbol name.
            depth: default 3.
        """
        return gt.callers(reader, name=name, depth=depth)

    @tool
    def cg_callees(name: str, depth: int = 3) -> str:
        """Find callees of a function or method, up to `depth` levels in.

        Args:
            name: required symbol name.
            depth: default 3.
        """
        return gt.callees(reader, name=name, depth=depth)

    @tool
    def cg_imports(path: str) -> str:
        """List modules imported by a source file.

        Args:
            path: required, repo-relative.
        """
        return gt.imports(reader, path=path)

    return [cg_find, cg_describe, cg_callers, cg_callees, cg_imports]


def _load_query_graph_tools(target: GraphTarget) -> tuple[GraphReader | None, list[BaseTool]]:
    """Open a reader on *target* and bind the tools, or say why not — once.

    Three states are "no graph", not an error: uninitialized, schema-mismatched,
    and **zero-node**. The third is not defensive padding — a schema-valid empty
    graph binds tools that always return nothing, and the librarian then loops
    to its iteration cap and returns the sentinel, which sends a perfectly good
    query down the code fallback.

    On success the reader is returned open. The caller closes it in `finally`.
    """
    try:
        reader = open_reader(graph_dir=target.graph_dir)
    except (GraphNotInitializedError, SchemaMismatchError):
        sys.stderr.write(_GRAPH_UNAVAILABLE_STDERR + "\n")
        return None, []
    if reader.node_count() == 0:
        reader.close()
        sys.stderr.write(_GRAPH_UNAVAILABLE_STDERR + "\n")
        return None, []
    return reader, build_graph_tools(reader)


#: `[text](/lane/slug.md)` — the root-absolute markdown link `CITATION_RULES`
#: mandates. The capture is the concept id: the path minus the leading `/` and
#: the `.md`. A relative or external destination does not match, and that is
#: the rule, not an omission — the citation contract is root-absolute.
_LINK_RE = re.compile(r"\[[^\]]*\]\(/([^)\s]+?)\.md\)")

#: Marks an answer the code fallback produced. Countable in a trace summary.
CODE_FALLBACK_MARKER = "[vault-thin: answer derived from source code]"

#: Both pathways empty. Never fabricate.
CODE_FALLBACK_DISCLAIMER = "The bundle does not document this and source code did not yield a relevant match."


@dataclass(frozen=True)
class QueryResult:
    """One query's answer, and which pipeline produced it.

    `path` and `fallback_error` are additions to the reference, and they close
    the epic's sharpest instance of "a green gate cannot see a broken seam": a
    silent orchestrator failure returns a good answer from the fixed pipeline
    and every test still passes. One field makes that countable rather than
    discoverable — which matters most at E8 cutover, when the reference stops
    running beside the rebuild.

    `"legacy"` is distinct from `"fallback"`: the caller asked for the fixed
    pipeline rather than being given it.
    """

    answer: str
    citations: list[str]
    #: Pages that contributed evidence — librarian excerpts that said something
    #: on the fixed path (filtering out NO_RELEVANT_CONTENT), evidence rows with
    #: a non-blank excerpt on the orchestrated one (by construction, authored prose).
    pages_drilled: int
    search_scores: Mapping[str, Mapping[str, float]]
    path: str
    fallback_error: str | None = None


def _extract_links(text: str) -> list[str]:
    """Every root-absolute markdown link in *text*, as concept ids, in order."""
    return _LINK_RE.findall(text)


def _normalize_citation(raw: str) -> str | None:
    """A model-supplied citation as a concept id, or `None` if it is not one.

    Accepts both forms the planner has been shown — `/concepts/auth.md` and
    `concepts/auth.md`. Strict about the `.md` suffix: a bare `concepts/auth`
    is refused, because that is also the shape a malformed code reference
    takes, and a wrong id resolves to nothing just as loudly as no id.
    """
    candidate = raw.strip().removeprefix("/")
    if not candidate.endswith(".md"):
        return None
    stem = candidate.removesuffix(".md")
    if not stem:
        return None
    return stem


def _unresolved_links(answer: str, bundle: Bundle, citations: Sequence[str]) -> list[str]:
    """Concept ids cited in *answer* or listed in *citations* that name nothing in *bundle*.

    A dict membership test, where the reference did a `.md` stat plus an
    `rglob` fallback per citation. The glob resolved a bare page name from
    anywhere in the vault, so an ambiguous citation silently resolved to
    whichever file `rglob` yielded first. A root-absolute link is unambiguous
    by construction: that failure mode is gone rather than handled.

    *citations* is checked alongside the body links because the orchestrated
    path lists ids the answer body never links, and an unresolvable one there
    is worth the same warning.
    """
    cited = dict.fromkeys([*_extract_links(answer), *citations])
    return [concept_id for concept_id in cited if concept_id not in bundle.concepts]


def apply_guardrails(
    result: QueryResult,
    bundle: Bundle,
    fan_result: FanOutResult,
    *,
    skip_g4: bool = False,
    evidence_noun: Literal["librarian excerpts", "orchestrator evidence"] = "librarian excerpts",
) -> QueryResult:
    """G4 then G1. Neither rewrites the answer body; both append.

    **G4, empty-result safety.** No successful excerpts but a non-empty
    citation list means the answer is unsupported by anything retrieved:
    citations are cleared and a warning appended. It runs first so G1 does not
    then flag the cleared list as unresolved.

    **G1, citation resolution.** Every `[text](/path.md)` in the answer resolves
    against `bundle.concepts`; unresolved ids are listed in an appended warning.

    `skip_g4=True` is the code-fallback path: `fan_result` there reflects the
    librarian fan-out, which is empty by construction, while the answer is
    supported by the code reader's excerpts. G1 still runs — an unresolved link
    is worth flagging on either path.

    *evidence_noun* is what G4 says the answer lacked. It defaults to the fixed
    path's "librarian excerpts"; the orchestrated path passes "orchestrator
    evidence", because saying "no librarian excerpts" about an answer built
    from evidence rows is simply false.
    """
    flags: list[str] = []
    citations = list(result.citations)

    if not skip_g4 and not fan_result.successes and citations:
        flags.append(f"[warning: no {evidence_noun}; answer is unsupported by retrieved pages]")
        citations = []

    unresolved = _unresolved_links(result.answer, bundle, citations)
    if unresolved:
        flags.append(f"[warning: {len(unresolved)} citation(s) did not resolve: {unresolved}]")

    if not flags:
        return result
    return replace(result, answer=result.answer + "\n" + "\n".join(flags), citations=citations)


# ---------------------------------------------------------------------------
# The fixed pipeline: librarian fan-out, synthesis, and the bounded code
# reader it falls through to when the bundle has nothing relevant.
# ---------------------------------------------------------------------------


def _read_file_bounded(
    repo_root: Path,
    requested_path: str,
    *,
    exclude: Path | None = None,
    max_bytes: int = 200_000,
) -> str:
    """Bounded read of one file under *repo_root*.

    **This guard stays.** Every other path guard in this vertical dissolved
    into a bundle key lookup, but this one joins a model-supplied string onto
    a filesystem path outside the bundle, so traversal is real.

    Both sides go through `resolve(strict=False)` **before** the containment
    check, so a symlink whose target lives outside the root is rejected.
    Dropping the resolve silently leaks files; the symlink test exists to
    catch exactly that.

    *exclude* is the workspace root: derived output, not source. The caller
    has it and this function does not, which is why it is a parameter rather
    than the reference's hardcoded directory name.

    Raises `PermissionError` on any violation. The tool wrappers turn that
    into an `ERROR: …` string so the model can recover without a crash.
    """
    root = repo_root.resolve(strict=False)
    candidate = (repo_root / requested_path).resolve(strict=False)

    if not candidate.is_relative_to(root):
        raise PermissionError(f"refusing to read {requested_path!r}: resolves outside repo root {root}")
    if exclude is not None and candidate.is_relative_to(exclude.resolve(strict=False)):
        raise PermissionError(f"refusing to read {requested_path!r}: path is inside {exclude}")
    if not candidate.is_file():
        raise PermissionError(f"refusing to read {requested_path!r}: not a regular file")

    with candidate.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        return raw[:max_bytes].decode("utf-8", errors="replace") + "[TRUNCATED]"
    return raw.decode("utf-8", errors="replace")


_LIBRARIAN_MAX_ITERS = 5
_CODE_READER_MAX_ITERS = 5

#: The bound on what one librarian call carries. `drill_page` sends exactly one
#: page, so this — not a token gate — is what keeps a request inside any
#: reasonable context window. A gate measuring the same thing could not fire.
_LIBRARIAN_PAGE_CHARS = 24_000
_EXCERPTS_CHARS = 60_000

_LIBRARIAN_FALLBACK_ADDENDUM = (
    "\nNOTE: code graph tools are unavailable in this workspace; rely on bundle excerpts only."
)


def _code_fallback_candidates(page_path: str) -> list[str]:
    """Repo-relative hints for the code reader, derived from a concept id.

    A concept id has no `.md` suffix to strip (contrast the reference, which
    read a vault-relative path). The id itself, plus its parent segment, are
    the same two-hint heuristic the reference used.
    """
    parts = page_path.split("/")
    hints = [page_path]
    if len(parts) > 1:
        hints.append("/".join(parts[:-1]))
    return hints


async def _synthesize(
    *,
    layout: WorkspaceLayout,
    query: str,
    excerpts_text: str,
    source_note: str,
    query_id: str,
    trace_name: str,
) -> str:
    """One synthesizer call: build the prompt, invoke, trace, return the text.

    Shared by both the librarian-excerpt path and the code-fallback path —
    the only difference between the two is what the excerpts are and one
    sentence saying where they came from.
    """
    binding = role_binding("synthesizer", layout=layout)
    llm = make_llm("synthesizer", layout=layout)
    messages: list[Any] = [
        SystemMessage(content=SYNTHESIZER_SYSTEM),
        HumanMessage(content=f"Query: {query}\n\n{source_note}Librarian excerpts:\n{excerpts_text}"),
    ]
    trace_dir = layout.cache_dir / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_file = trace_dir / f"{trace_name}_{query_id}.jsonl"
    started = time.monotonic()
    response = await llm.ainvoke(messages)
    latency_ms = int((time.monotonic() - started) * 1000)
    write_trace_record(
        trace_file,
        role="synthesizer",
        model_id=binding.spec.model_id,
        item=query_id,
        status="success",
        latency_ms=latency_ms,
        response=response,
    )
    answer = getattr(response, "content", "") or ""
    return answer if isinstance(answer, str) else str(answer)


async def _run_code_fallback(
    prepared: PreparedQueryRetrieval,
    *,
    query: str,
    query_id: str,
    pool: SubagentPool,
) -> str:
    """The bundle-thin fallback: read source code, then synthesize.

    Fans a bounded `code_reader` loop out over the same top pages the
    librarian drilled, using a `read_file` tool scoped to `layout.repo_root`
    and excluding `layout.root` (the workspace's own derived output).
    `CODE_FALLBACK_DISCLAIMER` — never fabricate — covers two distinct empty
    cases: no repo to read from at all, and a repo that yielded nothing
    useful either.
    """
    layout = prepared.layout
    repo_root = layout.repo_root
    if repo_root is None:
        return CODE_FALLBACK_DISCLAIMER

    code_binding = role_binding("code_reader", layout=layout)
    code_llm = code_binding.make_llm()

    @tool
    def read_file(path: str) -> str:
        """Read a source file by repo-relative path.

        Refuses paths outside the repo root, inside the workspace directory,
        or non-regular files. Truncates at 200,000 bytes.
        """
        try:
            return _read_file_bounded(repo_root, path, exclude=layout.root)
        except PermissionError as exc:
            return f"ERROR: {exc}"
        except OSError as exc:
            return f"ERROR: {exc}"

    async def code_drill(page_path: str) -> TaskResult:
        hints = _code_fallback_candidates(page_path)
        hint_lines = "\n".join(f"- {hint}" for hint in hints)
        messages: list[Any] = [
            SystemMessage(content=CODE_READER_SYSTEM),
            HumanMessage(
                content=(
                    f"Query: {query}\n\n"
                    f"The bundle page `{page_path}` did not cover this query. "
                    "Use the `read_file` tool to read source files under these "
                    "candidate path hints and any other plausible files you can "
                    "infer from what you find:\n"
                    f"{hint_lines}\n\n"
                    "Quote relevant code verbatim with `path:line` annotations. "
                    "If nothing you can read is relevant, respond with exactly "
                    "`NO_RELEVANT_CONTENT`."
                )
            ),
        ]
        loop_result = await run_tool_loop(
            llm=code_llm,
            tools=[read_file],
            messages=messages,
            max_iterations=_CODE_READER_MAX_ITERS,
            cap_label="code reader",
        )
        if loop_result.status != "ok":
            logger.warning(
                "code reader could not produce an answer for page %s (query_id=%s): %s",
                page_path,
                query_id,
                loop_result.error,
            )
            return TaskResult(value="NO_RELEVANT_CONTENT", response=None)
        return TaskResult(value=loop_result.final_text, response=None)

    code_fan: FanOutResult = await pool.run_all(
        items=list(prepared.top_pages),
        task=code_drill,
        role="code_reader",
        model_id=code_binding.spec.model_id,
        max_concurrency=code_binding.spec.max_concurrency,
    )

    code_useful = [
        (item, result)
        for item, result in code_fan.successes
        if (result or "").strip() and (result or "").strip() != "NO_RELEVANT_CONTENT"
    ]
    if not code_useful:
        return CODE_FALLBACK_DISCLAIMER

    code_excerpts_text = "\n\n---\n\n".join(f"[{item}]\n{result}" for item, result in code_useful)
    if len(code_excerpts_text) > _EXCERPTS_CHARS:
        code_excerpts_text = code_excerpts_text[:_EXCERPTS_CHARS]

    synth_answer = await _synthesize(
        layout=layout,
        query=query,
        excerpts_text=code_excerpts_text,
        source_note=(
            "Source: code (bundle did not cover this query). The excerpts below are quoted "
            "directly from source files by a code-reader subagent, not from bundle pages.\n\n"
        ),
        query_id=query_id,
        trace_name="synth_codefallback",
    )
    return f"{CODE_FALLBACK_MARKER}\n\n{synth_answer}"


async def _run_fixed_query(
    prepared: PreparedQueryRetrieval,
    *,
    query: str,
    query_id: str,
    graph_tools: list[BaseTool],
    path: str,
    fallback_error: str | None = None,
) -> tuple[QueryResult, bool]:
    """The fixed pipeline: librarian fan-out, then synthesis or code fallback.

    Returns the `QueryResult` alongside whether the code fallback ran, so the
    caller can fold that into its trace summary without recomputing it.

    Retrieval already happened — *prepared* carries its result, so a fallback
    from a broken orchestrator does not re-retrieve. Ports the reference's
    `_run_legacy_query` body from its librarian fan-out onward.

    No one-shot unresolved-citation retry, unlike the reference's
    `_retry_synthesis_drop_unresolved`. That retry existed because the
    reference's glob-based resolver could hand back an ambiguous target the
    model plausibly guessed wrong. A root-absolute link is unambiguous by
    construction (§4.4 of the design spec), so telling the model "these do
    not exist" gives it no new information to retry with — G1's appended
    warning is the honest outcome instead.
    """
    bundle = prepared.bundle
    layout = prepared.layout
    config = load_config(layout.bundle_dir, config_path=layout.repositories_path)
    schema_set = load_schemas(config.declarations_dir / "_schema")
    librarian_system = build_librarian_system(schema_set=schema_set)

    librarian_binding = role_binding("librarian", layout=layout)
    librarian_llm = librarian_binding.make_llm()

    addendum = "" if graph_tools else _LIBRARIAN_FALLBACK_ADDENDUM
    page_texts: dict[str, str] = {
        page: read_bounded_page(bundle, page, max_chars=_LIBRARIAN_PAGE_CHARS) for page in prepared.top_pages
    }

    trace_dir = layout.cache_dir / "traces"
    pool = SubagentPool(trace_dir, price_lookup=cost_for_usage)

    async def drill_page(page_path: str) -> TaskResult:
        messages: list[Any] = [
            SystemMessage(content=librarian_system + addendum),
            HumanMessage(content=f"Query: {query}\n\nPage ({page_path}):\n{page_texts.get(page_path, '')}"),
        ]
        loop_result = await run_tool_loop(
            llm=librarian_llm,
            tools=graph_tools,
            messages=messages,
            max_iterations=_LIBRARIAN_MAX_ITERS,
            cap_label="librarian",
        )
        if loop_result.status != "ok":
            logger.warning(
                "librarian could not produce an answer for page %s (query_id=%s): %s",
                page_path,
                query_id,
                loop_result.error,
            )
            return TaskResult(value="NO_RELEVANT_CONTENT", response=None)
        return TaskResult(value=loop_result.final_text, response=None)

    fan_result: FanOutResult = await pool.run_all(
        items=list(prepared.top_pages),
        task=drill_page,
        role="librarian",
        model_id=librarian_binding.spec.model_id,
        max_concurrency=librarian_binding.spec.max_concurrency,
    )

    useful_excerpts = [
        (item, result)
        for item, result in fan_result.successes
        if (result or "").strip() and (result or "").strip() != "NO_RELEVANT_CONTENT"
    ]

    code_fallback_used = False
    if useful_excerpts:
        excerpts_text = "\n\n---\n\n".join(f"[{item}]\n{result}" for item, result in useful_excerpts)
        if len(excerpts_text) > _EXCERPTS_CHARS:
            logger.warning("truncating librarian excerpts before synthesis (query_id=%s)", query_id)
            excerpts_text = excerpts_text[:_EXCERPTS_CHARS]
        answer = await _synthesize(
            layout=layout,
            query=query,
            excerpts_text=excerpts_text,
            source_note="",
            query_id=query_id,
            trace_name="synth_librarian",
        )
    else:
        logger.info("librarian fan-out returned no useful excerpts; entering code fallback (query_id=%s)", query_id)
        code_fallback_used = True
        answer = await _run_code_fallback(prepared, query=query, query_id=query_id, pool=pool)

    query_result = QueryResult(
        answer=answer,
        citations=_extract_links(answer),
        pages_drilled=len(useful_excerpts),
        search_scores=prepared.search_scores,
        path=path,
        fallback_error=fallback_error,
    )
    return apply_guardrails(query_result, bundle, fan_result, skip_g4=code_fallback_used), code_fallback_used


#: Cap on the excerpt handed to the orchestrator for each initial candidate.
#: Kept far below `_LIBRARIAN_PAGE_CHARS` — this is a planning hint, not the
#: text a worker reasons over.
_CANDIDATE_EXCERPT_CHARS = 1_500


def _initial_candidates(prepared: PreparedQueryRetrieval, *, repo_head: str | None) -> list[InitialCandidate]:
    """The planner's candidate list, each row carrying a classified freshness.

    Shared by `run_query` and the loop adapter: two copies of this drifted
    once already, which is how `freshness` stayed at its `"unknown"` default
    on both paths.

    *repo_head* is resolved once per query by the caller — `head_sha` shells
    out to git, and the head does not move mid-query.
    """
    from graph_works_core.query.query_orchestrator import InitialCandidate, classify_wiki_freshness

    candidates: list[InitialCandidate] = []
    for page in prepared.top_pages:
        classification = classify_wiki_freshness(prepared.bundle.concepts[page], repo_head=repo_head)
        candidates.append(
            InitialCandidate(
                path=page,
                score=prepared.search_scores[page]["rrf"],
                excerpt=read_bounded_page(prepared.bundle, page, max_chars=_CANDIDATE_EXCERPT_CHARS),
                freshness=classification.freshness,
                staleness_reason=classification.reason,
            )
        )
    return candidates


async def run_query_orchestrator(**kwargs: Any) -> Any:  # noqa: ANN401 -- a patchable pass-through seam
    """Patchable import seam for the orchestrator.

    A module-level function rather than a top-level import, for one reason: it
    keeps `commands.query` importable without pulling in the orchestrator's own
    import closure, and it gives the tests one name to patch. `run_query`
    calls this unqualified so a caller — chiefly the test suite — can
    `monkeypatch.setattr(query, "run_query_orchestrator", …)` and have
    `run_query` actually observe the replacement.
    """
    from graph_works_core.query.query_orchestrator import run_query_orchestrator as _run

    return await _run(**kwargs)


def _write_query_summary(
    *,
    cache_dir: Path,
    query_id: str,
    query: str,
    top_k: int,
    result: QueryResult,
    pages_retrieved: int,
    code_fallback: bool,
    started_at: str,
    orchestrator_status: str | None,
    worker_batches: int,
) -> None:
    """One JSONL summary record per query. A trace failure never masks an answer."""
    trace_dir = cache_dir / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "kind": "query_summary",
        "query_id": query_id,
        "query": query,
        "top_k": top_k,
        "pages_retrieved": pages_retrieved,
        "pages_drilled": result.pages_drilled,
        "code_fallback": code_fallback,
        # The two fields §4.3 added. A silent fallback is the sharpest instance
        # of "a green gate cannot see a broken seam"; these make it countable.
        "path": result.path,
        "fallback_error": result.fallback_error,
        "orchestrator_status": orchestrator_status,
        "orchestrator_batch_iterations": worker_batches,
        "started_at": started_at,
        "ended_at": datetime.now(tz=UTC).isoformat(),
    }
    try:
        (trace_dir / f"query_{query_id}.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("could not write query summary trace: %s", exc)


async def run_query(
    query: str,
    layout: WorkspaceLayout,
    *,
    bundle: Bundle | None = None,
    embedder: Embedder,
    top_k: int = 5,
    use_legacy: bool = False,
) -> QueryResult:
    """Answer *query* against the bundle *layout* names.

    The orchestrated path is the default and the fixed librarian→synthesizer
    path is its fallback. Both share one retrieval result, so a fallback does
    not re-retrieve, and `QueryResult.path` says which one ran.

    The fallback triggers on an orchestrator *exception*, and on the degradation
    statuses in `FALLBACK_STATUSES` — the three that mean the orchestrator
    itself broke. A planner that returned a badly-shaped answer (`invalid_json`,
    `validation_error`) or ran out of worker batches (`capped`) still returned:
    those reach the caller as `path="orchestrated"`, because a second pipeline
    over the same corpus does not diagnose a broken output contract.

    The graph reader `_load_query_graph_tools` may open is closed in `finally`
    on every path — legacy, a successful orchestration, an exception-driven
    fallback, and a status-driven fallback alike. The fallback's own
    `_run_fixed_query` call always gets
    `graph_tools=[]`: by the time it runs, the reader behind those tools has
    already been closed, so passing them in would hand it a closed reader.

    `bundle` is an argument so a caller that already loaded one does not walk
    the directory twice; omitted, this loads it from `layout.bundle_dir`.
    `embedder` is required and has no default — the vendor decision belongs to
    the caller, and `default_embedder()` is there for callers with no opinion.
    """
    query_id = uuid.uuid4().hex[:12]
    started_at = datetime.now(tz=UTC).isoformat()
    loaded_bundle = load_bundle(layout.bundle_dir) if bundle is None else bundle
    prepared = _prepare_query_retrieval(query, layout, loaded_bundle, top_k=top_k, embedder=embedder)

    reader, graph_tools = _load_query_graph_tools(graph_target(layout))
    fallback_error: str | None = None
    fallback_status: str | None = None
    orchestrated: Any = None
    try:
        if use_legacy:
            result, code_fallback = await _run_fixed_query(
                prepared, query=query, query_id=query_id, graph_tools=graph_tools, path="legacy"
            )
            _write_query_summary(
                cache_dir=layout.cache_dir,
                query_id=query_id,
                query=query,
                top_k=top_k,
                result=result,
                pages_retrieved=len(prepared.top_pages),
                code_fallback=code_fallback,
                started_at=started_at,
                orchestrator_status=None,
                worker_batches=0,
            )
            return result

        from graph_works_core.query.query_orchestrator import FALLBACK_STATUSES

        try:
            orchestrated = await run_query_orchestrator(
                query=query,
                bundle=prepared.bundle,
                repo_root=layout.repo_root,
                initial_candidates=_initial_candidates(
                    prepared, repo_head=head_sha(layout.repo_root) if layout.repo_root is not None else None
                ),
                graph_tools=graph_tools,
                trace_dir=layout.cache_dir / "traces",
                workspace_root=layout.root,
                layout=layout,
            )
        except Exception as exc:  # a broken orchestrator is what the fixed pipeline is for
            fallback_error = f"{type(exc).__name__}: {exc}"
            logger.warning("query orchestrator failed; falling back to the fixed pipeline: %s", fallback_error)
    finally:
        if reader is not None:
            reader.close()

    if fallback_error is None and orchestrated is not None:
        status = str(orchestrated.trace_metadata.get("status"))
        if status in FALLBACK_STATUSES:
            fallback_status = status
            fallback_error = f"{status}: {orchestrated.trace_metadata.get('error')}"
            logger.warning("orchestrator degraded (%s); falling back to the fixed pipeline", status)

    if fallback_error is not None:
        result, code_fallback = await _run_fixed_query(
            prepared,
            query=query,
            query_id=query_id,
            graph_tools=[],
            path="fallback",
            fallback_error=fallback_error,
        )
        worker_batches_run = (
            int(orchestrated.trace_metadata.get("worker_batches", 0)) if orchestrated is not None else 0
        )
        _write_query_summary(
            cache_dir=layout.cache_dir,
            query_id=query_id,
            query=query,
            top_k=top_k,
            result=result,
            pages_retrieved=len(prepared.top_pages),
            code_fallback=code_fallback,
            started_at=started_at,
            orchestrator_status=fallback_status,
            worker_batches=worker_batches_run,
        )
        return result

    output = orchestrated.output
    useful = [evidence for evidence in output.evidence if evidence.excerpt.strip()]
    listed = [cited for cited in (_normalize_citation(raw) for raw in output.citations) if cited is not None]
    orchestrated_result = QueryResult(
        answer=output.answer_markdown,
        citations=list(dict.fromkeys([*_extract_links(output.answer_markdown), *listed])),
        pages_drilled=len(useful),
        search_scores=prepared.search_scores,
        path="orchestrated",
        fallback_error=None,
    )
    orchestrated_result = apply_guardrails(
        orchestrated_result,
        prepared.bundle,
        FanOutResult(successes=[(item.id, item.excerpt) for item in useful], errors=[]),
        evidence_noun="orchestrator evidence",
    )
    _write_query_summary(
        cache_dir=layout.cache_dir,
        query_id=query_id,
        query=query,
        top_k=top_k,
        result=orchestrated_result,
        pages_retrieved=len(prepared.top_pages),
        code_fallback=False,
        started_at=started_at,
        orchestrator_status=str(orchestrated.trace_metadata.get("status")),
        worker_batches=int(orchestrated.trace_metadata.get("worker_batches", 0)),
    )
    return orchestrated_result
