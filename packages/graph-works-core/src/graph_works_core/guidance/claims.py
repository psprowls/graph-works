"""The claims index: every `decisions:` / `claims:` entry in the bundle as a flat row.

Derived and cached under `<cache_dir>/claims/` on `query/commands.py`'s
pattern — a manifest naming the schema version and an injective corpus
fingerprint, per-page hashes for incremental re-extraction, a closed
staleness vocabulary, and the manifest written last. Every read goes through
`refresh_claims` first, so no reader sees a stale index.

Nothing on the content path raises. An entry the index cannot read is
skipped and reported in `ClaimsRefresh.skipped`; validation is lint's job.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from code_graph_io.tokens import count_tokens
from okf_io import Bundle
from work_tracker_okf.dependencies import PHASE_ORDER

#: Bumped whenever a row's fields, the rows file or the manifest change shape.
#: Also bump it when `PIPELINE_PHASES` (derived from `work_tracker_okf`'s
#: `PHASE_ORDER`) or the tokenizer behind `count_tokens` changes: derived
#: `phases` and `tokens` are baked into cached rows, and neither input is in
#: the manifest, so nothing else would invalidate them.
CLAIMS_SCHEMA_VERSION = 1
CLAIMS_SUBDIR = "claims"
MANIFEST_NAME = "manifest.json"
ROWS_NAME = "rows.json"

#: Every stage that runs — `done` is a resting state, not a stage.
PIPELINE_PHASES: tuple[str, ...] = tuple(phase for phase in PHASE_ORDER if phase != "done")

#: Frontmatter key -> row kind.
_KEYS: tuple[tuple[str, Literal["decision", "claim"]], ...] = (("decisions", "decision"), ("claims", "claim"))


@dataclass(frozen=True)
class ClaimRow:
    """One authored decision or claim, with its page's status inherited."""

    page: str
    id: str
    kind: Literal["decision", "claim"]
    claim: str
    about: tuple[str, ...]
    constrains: tuple[str, ...]
    phases: tuple[str, ...]
    phase_source: Literal["authored", "derived"]
    page_status: str | None
    superseded: bool
    superseded_by: str | None
    tokens: int

    def to_json(self) -> dict[str, object]:
        return {
            "page": self.page,
            "id": self.id,
            "kind": self.kind,
            "claim": self.claim,
            "about": list(self.about),
            "constrains": list(self.constrains),
            "phases": list(self.phases),
            "phase_source": self.phase_source,
            "page_status": self.page_status,
            "superseded": self.superseded,
            "superseded_by": self.superseded_by,
            "tokens": self.tokens,
        }

    @classmethod
    def from_json(cls, raw: object) -> ClaimRow | None:
        """A row from `rows.json`, or `None` when any field has the wrong type."""
        if not isinstance(raw, dict):
            return None
        try:
            kind = raw["kind"]
            phase_source = raw["phase_source"]
            strings = (raw["page"], raw["id"], raw["claim"])
            lists = (raw["about"], raw["constrains"], raw["phases"])
            status, superseded, superseded_by, tokens = (
                raw["page_status"],
                raw["superseded"],
                raw["superseded_by"],
                raw["tokens"],
            )
        except KeyError:
            return None
        if kind not in ("decision", "claim") or phase_source not in ("authored", "derived"):
            return None
        if not all(isinstance(value, str) for value in strings):
            return None
        if not all(_is_str_list(value) for value in lists):
            return None
        if not (status is None or isinstance(status, str)) or not isinstance(superseded, bool):
            return None
        if not (superseded_by is None or isinstance(superseded_by, str)):
            return None
        if not isinstance(tokens, int) or isinstance(tokens, bool):
            return None
        return cls(
            page=strings[0],
            id=strings[1],
            kind=kind,
            claim=strings[2],
            about=tuple(lists[0]),
            constrains=tuple(lists[1]),
            phases=tuple(lists[2]),
            phase_source=phase_source,
            page_status=status,
            superseded=superseded,
            superseded_by=superseded_by,
            tokens=tokens,
        )


@dataclass(frozen=True)
class SkippedEntry:
    """An entry the index would not invent data for. `index` is -1 when the whole key is unreadable."""

    page: str
    key: str
    index: int
    reason: str


@dataclass(frozen=True)
class ClaimsRefresh:
    """What a refresh did, and why.

    `reason` takes a closed vocabulary — `"no-manifest"`, `"schema-changed"`,
    `"corpus-changed"`, `"rows-unreadable"`, `"forced"` — or `None` when the
    index was already fresh and nothing ran. `skipped` is only populated for
    pages extracted by this run.
    """

    rebuilt: bool
    reason: str | None
    pages: int
    extracted: int
    pruned: int
    skipped: tuple[SkippedEntry, ...]


@dataclass(frozen=True)
class _Manifest:
    schema_version: int
    corpus_fingerprint: str


def _is_str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _dir(cache_dir: Path) -> Path:
    return cache_dir / CLAIMS_SUBDIR


def _corpus(bundle: Bundle) -> dict[str, tuple[str, Mapping[str, Any]]]:
    """`{concept_id: (raw_text, fm_data)}` for every concept carrying a `decisions:` or `claims:` key."""
    corpus: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for concept_id in sorted(bundle.concepts):
        document = bundle.concepts[concept_id]
        data = document.fm_data(dates="iso")
        if any(key in data for key, _ in _KEYS):
            corpus[concept_id] = (document.raw_text, data)
    return corpus


def _page_hashes(corpus: Mapping[str, tuple[str, Mapping[str, Any]]]) -> dict[str, str]:
    return {concept_id: hashlib.sha256(text.encode()).hexdigest() for concept_id, (text, _) in corpus.items()}


def _corpus_fingerprint(page_hashes: Mapping[str, str]) -> str:
    """sha256 over sorted `(concept_id, page_hash)` pairs — `query._corpus_fingerprint`'s construction."""
    digest = hashlib.sha256()
    for concept_id in sorted(page_hashes):
        digest.update(concept_id.encode())
        digest.update(b"\0")
        digest.update(page_hashes[concept_id].encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _read_manifest(cache_dir: Path) -> _Manifest | None:
    try:
        raw = json.loads((_dir(cache_dir) / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    version = raw.get("schema_version")
    fingerprint = raw.get("corpus_fingerprint")
    if not isinstance(version, int) or isinstance(version, bool) or not isinstance(fingerprint, str):
        return None
    return _Manifest(version, fingerprint)


def _write_json(path: Path, payload: object) -> None:
    """Replace *path* atomically: a sibling temp file, then `Path.replace` (`os.replace`).

    A reader sees the old file or the new one, never a truncated one. The temp
    file lives in the same directory so the replace never crosses a
    filesystem; it is removed if anything fails before the replace.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    temp = Path(temp_name)
    try:
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        temp.replace(path)  # os.replace: atomic on POSIX and Windows alike
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


#: One page in rows.json: its hash and its rows.
_Page = tuple[str, tuple[ClaimRow, ...]]


def _read_rows(cache_dir: Path) -> dict[str, _Page] | None:
    """rows.json as `{concept_id: (page_hash, rows)}`, or `None` when missing or malformed anywhere."""
    try:
        raw = json.loads((_dir(cache_dir) / ROWS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pages = raw.get("pages") if isinstance(raw, dict) else None
    if not isinstance(pages, dict):
        return None
    result: dict[str, _Page] = {}
    for concept_id, entry in pages.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("page_hash"), str):
            return None
        raw_rows = entry.get("rows")
        if not isinstance(raw_rows, list):
            return None
        rows = tuple(ClaimRow.from_json(item) for item in raw_rows)
        if any(row is None for row in rows):
            return None
        result[concept_id] = (entry["page_hash"], tuple(row for row in rows if row is not None))
    return result


def _first_nonempty_str(value: object) -> str | None:
    """The str itself when non-empty, or the first non-empty str element of a list; else `None`.

    `superseded_by` may be authored as a non-empty str or a list of str
    (curated-page claims contract). A list with no non-empty element (`[]`,
    `[""]`) carries no supersession target.
    """
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item:
                return item
    return None


def _has_superseded_by(value: object) -> bool:
    """Whether `superseded_by` names at least one supersession target, str or list form."""
    return _first_nonempty_str(value) is not None


def _extract_entry(
    concept_id: str, key: str, kind: Literal["decision", "claim"], index: int, entry: object, page: Mapping[str, Any]
) -> ClaimRow | SkippedEntry:
    """One entry -> a row, or the reason it was skipped. The one function that follows the entry shape."""

    def skip(reason: str) -> SkippedEntry:
        return SkippedEntry(concept_id, key, index, reason)

    if not isinstance(entry, dict):
        return skip("not-a-mapping")
    entry_id, claim = entry.get("id"), entry.get("claim")
    if not isinstance(entry_id, str) or not entry_id:
        return skip("missing-id")
    if not isinstance(claim, str) or not claim:
        return skip("missing-claim")

    # R1 (curated-page claims contract): an entry's `about` is optional. If
    # present it must be a list of str; if absent, the entry inherits the
    # page's top-level `about:` list, which itself must be a list of str.
    if "about" in entry:
        about = entry["about"]
        if not _is_str_list(about):
            return skip("about-not-a-list")
    else:
        if "about" not in page:
            return skip("missing-about")
        page_about = page["about"]
        if not _is_str_list(page_about):
            return skip("about-not-a-list")
        about = page_about

    constrains = entry.get("constrains", [])
    if not _is_str_list(constrains):
        return skip("constrains-not-a-list")
    phase = entry.get("phase")
    if phase is not None and not _is_str_list(phase):
        return skip("phase-not-a-list")
    status = page.get("status")
    superseded_by = page.get("superseded_by")
    status_text = status if isinstance(status, str) else None
    superseded_text = _first_nonempty_str(superseded_by)
    return ClaimRow(
        page=concept_id,
        id=entry_id,
        kind=kind,
        claim=claim,
        about=tuple(about),
        constrains=tuple(constrains),
        phases=tuple(phase) if phase is not None else PIPELINE_PHASES,
        phase_source="authored" if phase is not None else "derived",
        page_status=status_text,
        superseded=status_text == "superseded" or _has_superseded_by(superseded_by),
        superseded_by=superseded_text,
        tokens=count_tokens(claim),
    )


def _extract_page(concept_id: str, data: Mapping[str, Any]) -> tuple[tuple[ClaimRow, ...], tuple[SkippedEntry, ...]]:
    rows: list[ClaimRow] = []
    skipped: list[SkippedEntry] = []
    for key, kind in _KEYS:
        if key not in data:
            continue
        entries = data[key]
        if not isinstance(entries, list):
            skipped.append(SkippedEntry(concept_id, key, -1, "not-a-list"))
            continue
        for index, entry in enumerate(entries):
            result = _extract_entry(concept_id, key, kind, index, entry, data)
            if isinstance(result, ClaimRow):
                rows.append(result)
            else:
                skipped.append(result)
    return tuple(rows), tuple(skipped)


def _build(bundle: Bundle, cache_dir: Path, *, reason: str) -> tuple[ClaimsRefresh, dict[str, _Page]]:
    """Write the index for *bundle*; return what ran and the pages written, so a reader needn't re-read them."""
    corpus = _corpus(bundle)
    hashes = _page_hashes(corpus)
    manifest = _read_manifest(cache_dir)
    previous = (
        _read_rows(cache_dir) if manifest is not None and manifest.schema_version == CLAIMS_SCHEMA_VERSION else None
    )
    previous = previous or {}

    pages: dict[str, _Page] = {}
    skipped: list[SkippedEntry] = []
    extracted = 0
    for concept_id, (_, data) in corpus.items():
        kept = previous.get(concept_id)
        if kept is not None and kept[0] == hashes[concept_id]:
            pages[concept_id] = kept
            continue
        rows, page_skipped = _extract_page(concept_id, data)
        pages[concept_id] = (hashes[concept_id], rows)
        skipped.extend(page_skipped)
        extracted += 1
    pruned = len(set(previous) - set(corpus))

    _write_json(
        _dir(cache_dir) / ROWS_NAME,
        {
            "pages": {
                concept_id: {"page_hash": page_hash, "rows": [row.to_json() for row in rows]}
                for concept_id, (page_hash, rows) in pages.items()
            }
        },
    )
    # Written last: a crash before this point leaves either no manifest, or an
    # old manifest whose fingerprint no longer matches the corpus, or whose
    # rows.json (already overwritten above) carries per-page hashes that
    # disagree with the corpus. `_staleness` checks all three, so a torn
    # build is never read as fresh.
    _write_json(
        _dir(cache_dir) / MANIFEST_NAME,
        {"schema_version": CLAIMS_SCHEMA_VERSION, "corpus_fingerprint": _corpus_fingerprint(hashes)},
    )
    return ClaimsRefresh(True, reason, len(corpus), extracted, pruned, tuple(skipped)), pages


def _rebuild_unreadable(bundle: Bundle, cache_dir: Path) -> tuple[ClaimsRefresh, dict[str, _Page]]:
    # The manifest vouches for rows that are gone: nothing to reuse.
    (_dir(cache_dir) / MANIFEST_NAME).unlink(missing_ok=True)
    return _build(bundle, cache_dir, reason="rows-unreadable")


def build_claims(bundle: Bundle, cache_dir: Path) -> ClaimsRefresh:
    """Rebuild the index unconditionally; re-extracts only pages whose hash changed."""
    return _build(bundle, cache_dir, reason="forced")[0]


def _staleness(bundle: Bundle, cache_dir: Path) -> str | None:
    manifest = _read_manifest(cache_dir)
    if manifest is None:
        return "no-manifest"
    if manifest.schema_version != CLAIMS_SCHEMA_VERSION:
        return "schema-changed"
    hashes = _page_hashes(_corpus(bundle))
    if manifest.corpus_fingerprint != _corpus_fingerprint(hashes):
        return "corpus-changed"
    rows = _read_rows(cache_dir)
    if rows is None:
        return "rows-unreadable"
    # The manifest's fingerprint matches the corpus, but that alone doesn't
    # prove rows.json holds *this* corpus's rows — an interrupted build can
    # leave rows.json overwritten for a different corpus generation while an
    # old (matching) manifest survives, and a hand-edit can replace rows.json
    # with something shape-valid but wrong (e.g. `{"pages": {}}`). Comparing
    # each page's recorded hash against the corpus's current hash catches both.
    if {concept_id: page_hash for concept_id, (page_hash, _) in rows.items()} != hashes:
        return "rows-unreadable"
    return None


def refresh_claims(bundle: Bundle, cache_dir: Path) -> ClaimsRefresh:
    """Make the index match *bundle*. Idempotent; writes nothing when already fresh."""
    reason = _staleness(bundle, cache_dir)
    if reason is None:
        return ClaimsRefresh(False, None, len(_corpus(bundle)), 0, 0, ())
    if reason == "rows-unreadable":
        return _rebuild_unreadable(bundle, cache_dir)[0]
    return _build(bundle, cache_dir, reason=reason)[0]


def read_claims(bundle: Bundle, cache_dir: Path) -> tuple[ClaimRow, ...]:
    """Every row, refreshed first, ordered by `(page, authored order)`.

    Serves only rows it can trust: if rows.json cannot be read back after the
    refresh (a concurrent writer, a disk fault), the index is rebuilt as
    `rows-unreadable` and the rows that build wrote are served directly —
    never `()` standing in for an unreadable file.
    """
    refresh_claims(bundle, cache_dir)
    pages = _read_rows(cache_dir)
    if pages is None:
        pages = _rebuild_unreadable(bundle, cache_dir)[1]
    return tuple(row for concept_id in sorted(pages) for row in pages[concept_id][1])


def claims_about(rows: Sequence[ClaimRow], uri: str, *, include_superseded: bool = False) -> tuple[ClaimRow, ...]:
    """Rows whose `about` contains exactly *uri*; superseded rows only when asked."""
    return tuple(row for row in rows if uri in row.about and (include_superseded or not row.superseded))
