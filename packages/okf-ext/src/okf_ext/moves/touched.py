"""Find move candidates by raw text without parsing documents.

An outside referrer to a moved subtree names its root segment, but Markdown
and YAML escapes or scalar folding can obscure its spelling. Percent decoding
and NFC handle literal spellings; escape syntax and whitespace-compacted matches
are admitted conservatively without interpreting either grammar. False positives
cost a parse; full/partial plan equivalence guards missed edits.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from okf_io.bundle import Walk, canonical_id, walk

_MARKDOWN = frozenset({"concept", "index", "log"})


@dataclass(frozen=True, slots=True)
class TouchedScan:
    """Candidate raw ids and the inventory used to find them in one walk."""

    inventory: Walk
    members: frozenset[str]
    complete: bool
    warnings: tuple[str, ...]


def _normal(text: str) -> str:
    return canonical_id(unquote(text))


def _compact(text: str) -> str:
    return canonical_id("".join(text.split()))


def scan_touched(
    root: Path,
    sources: Sequence[str],
    *,
    ignore: Sequence[str] = (),
    prune: Sequence[str] = (),
    extra: Collection[str] = (),
) -> TouchedScan:
    """Select moved subtrees, textual referrers and explicit extra members.

    Sources are bundle-relative, extensionless moved roots. Unreadable files
    are selected for the loader to diagnose; unreadable directories make the
    scan incomplete so callers can fall back to a full load.
    """
    inventory = walk(root, ignore=ignore, prune=prune)
    needles = {_normal(PurePosixPath(source).name) for source in sources}
    compact_needles = {_compact(needle) for needle in needles}
    subtree = tuple(canonical_id(source) for source in sources)
    members = set(extra)
    for member in inventory.members:
        # A caller may spell an NFD disk root in NFC. Keep the selected id raw,
        # but compare canonically so moved files need not cite their own name.
        member_id = canonical_id(member.id)
        if any(member_id == f"{source}.md" or member_id.startswith(f"{source}/") for source in subtree):
            members.add(member.id)
            continue
        if member.kind not in _MARKDOWN:
            continue
        try:
            text = root.joinpath(*member.id.split("/")).read_bytes().decode("utf-8", errors="replace")
        except OSError:
            members.add(member.id)
            continue
        normalized = _normal(text)
        # Markdown entities/backslash escapes and YAML quoted escapes can hide
        # any part of a basename. Admit their syntax, including doubled YAML
        # single quotes, rather than maintaining an incomplete grammar decoder.
        if "�" in text or any(marker in normalized for marker in ("\\", "&", "''")):
            members.add(member.id)
            continue
        if any(needle in normalized for needle in needles):
            members.add(member.id)
            continue
        # Folded and wrapped YAML scalars alter whitespace. Removing it on
        # both sides over-includes safely, including basenames with spaces.
        compact = _compact(normalized)
        if any(needle in compact for needle in compact_needles):
            members.add(member.id)
    warnings = tuple(
        f"touched scan could not read {directory}: falling back to a full load"
        for directory in sorted(inventory.unreadable_dirs)
    )
    return TouchedScan(inventory, frozenset(members), not inventory.unreadable_dirs, warnings)
