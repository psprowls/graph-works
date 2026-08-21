"""Where a work item's artifacts live — the layout contract, as one carrier.

Every artifact needs three spellings and only a leading slash separates two of
them: a filesystem `Path` for the writer, a bundle-relative string for `moves` and
`Bundle.assets`, and a root-absolute string for `sources[].resource`. Returning a
bare `str` for any of them makes the wrong one plausible at every call site, and
`okf_io.links.resolve_reference` treats a value without a leading `/` as relative
to the containing page's directory — so the mistake is silent, surfacing only as a
broken-reference finding. Hence one frozen `ArtifactRef` (C2-B).

`artifact_path` calls `source_id_for` and puts the result on the carrier, so **a
caller cannot obtain a resource without the matching id** (C2-C). That is the seam
child 3 left open when it deleted `artifact_slot` on the promise that the
destination is derivable from `on_complete.stamp_source` plus the in-flight phase.

`WORK_DIR` and `ARCHIVE_DIR` from `items.py` are still what every composer resolves
to by default, because reading the schema at path-composition time would make a
pure string function do file I/O. A caller that already holds a `SchemaSet` --
`cli.file`, and `compose.plan_file_and_reconcile` through it -- passes the
directory its type declares as `lane_dir=`, so the bundle's own
`x-okf-directory` is honoured wherever there is one to honour and this module
still reads no files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from work_tracker_okf.items import ARCHIVE_DIR, WORK_DIR
from work_tracker_okf.vocabulary import (
    ARTIFACT_KINDS,
    ARTIFACT_PHASES,
    PLAN_SOURCE_ID,
    SPEC_SOURCE_ID,
)

#: The per-item artifact directory's name, under `work/<slug>/`.
REFERENCES_DIRNAME = "references"

#: The decisions ledger's filename, under `work/<slug>/references/`. It lives
#: here beside `REFERENCES_DIRNAME` because a filename is a layout fact, and
#: because `decisions.py` derives its lock name from it — the other direction
#: would be a cycle.
LEDGER_FILENAME = "00-decisions.md"

#: `<phase>` -> its two-digit filename ordinal, **derived** from
#: `ARTIFACT_PHASES` rather than re-typed (C2-D). `work-io` hand-wrote the map and
#: carried a synthetic `open: "00"` for an archive-time page rename that W-E
#: deleted, so `00` has nothing left to name.
PHASE_ORDINALS: dict[str, str] = {phase: f"{index:02d}" for index, phase in enumerate(ARTIFACT_PHASES, 1)}

#: The two `(phase, kind)` pairs whose id is a shipped literal rather than the
#: `<kind>-<phase>` rule, mapped to that literal. Neither can carry a suffix: the
#: shipped `SOURCE_ID_PATTERN` has no suffixed form for either.
_LITERAL_IDS: dict[tuple[str, str], str] = {
    ("design", "spec"): SPEC_SOURCE_ID,
    ("plan", "plan"): PLAN_SOURCE_ID,
}


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """One bundle location in all three spellings, plus the id that names it.

    `source_id` is `None` for a location that is not an artifact — an item page,
    the `references/` directory itself. One type rather than two: a second
    near-identical dataclass buys nothing but a name, and composition costs every
    caller a `.location.` hop.
    """

    rel: str
    source_id: str | None = None

    @property
    def resource(self) -> str:
        """The root-absolute form `sources[].resource` wants."""
        return f"/{self.rel}"

    def path(self, root: Path) -> Path:
        """The filesystem form. The only place a root is needed, which is why no
        composition function takes one."""
        return root / self.rel


def _lane_dir(archived: bool, lane_dir: str | None = None) -> str:
    """The directory an item's artifacts hang off, active or archived.

    *lane_dir* is the bundle's own declaration for the item's type, already
    read out of `x-okf-directory` and stripped of its trailing slash by the
    caller that holds the `SchemaSet`. `None` -- every call site that holds no
    schema -- resolves to the hardcoded pair, which is what keeps this module a
    pure-string one and leaves the reader's constants in force.

    `_archive` stays a literal in both branches: nothing declares
    `work/_archive/`, so there is no annotation to read for the other half.
    """
    if lane_dir is None:
        return ARCHIVE_DIR if archived else WORK_DIR
    return f"{lane_dir}/_archive" if archived else lane_dir


def item_page(slug: str, *, archived: bool = False, lane_dir: str | None = None) -> ArtifactRef:
    """`work/<slug>.md`, or its archived twin."""
    return ArtifactRef(rel=f"{_lane_dir(archived, lane_dir)}/{slug}.md")


def references_dir(slug: str, *, archived: bool = False, lane_dir: str | None = None) -> ArtifactRef:
    """`work/<slug>/references`, or its archived twin. No trailing slash."""
    return ArtifactRef(rel=f"{_lane_dir(archived, lane_dir)}/{slug}/{REFERENCES_DIRNAME}")


def decisions_ledger(slug: str, *, archived: bool = False, lane_dir: str | None = None) -> ArtifactRef:
    """`work/<slug>/references/00-decisions.md`, or its archived twin.

    `source_id` stays `None`: the epic page does not stamp its ledger into
    `sources[]`, so there is no id to carry, and `references_dir`'s `.source_id`
    is `None` for the same reason.

    Composed over `references_dir` rather than through `artifact_path`, which
    builds `<NN>-<phase>-<kind>` from `PHASE_ORDINALS` and `ARTIFACT_KINDS`. A
    ledger has neither a phase nor a kind, and inventing a synthetic phase whose
    only purpose is to reach `00` is exactly the mistake `work-io`'s deleted
    `open: "00"` entry was.
    """
    return ArtifactRef(rel=f"{references_dir(slug, archived=archived, lane_dir=lane_dir).rel}/{LEDGER_FILENAME}")


def source_id_for(phase: str, kind: str, suffix: str | None = None) -> str:
    """The `sources[].id` naming *phase*'s *kind* artifact.

    Two literals plus a rule, because child 1's shipped ids are not uniform:
    `("design", "spec")` is `design-spec` and `("plan", "plan")` is `plan`, not
    `plan-plan`. Everything else is `f"{kind}-{phase}"` — note the flip against
    the filename's `<phase>-<kind>`, which predates the port and is not
    relitigated here.

    This is the one raising door in this module; `artifact_path` calls it first
    and adds no checks of its own. Raises `ValueError` for an unknown phase or
    kind (caller error, the class `InitError` occupies — no bundle content
    reaches here), for an invalid phase/kind combination (spec only in design,
    plan only in plan), and for a *suffix* on either literal pair: the shipped
    pattern refuses `design-spec-draft`, and dropping the suffix silently would
    hand two distinct resources the same id.
    """
    if phase not in PHASE_ORDINALS:
        raise ValueError(f"unknown phase {phase!r}; expected one of {sorted(PHASE_ORDINALS)}")
    if kind not in ARTIFACT_KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {sorted(ARTIFACT_KINDS)}")
    literal = _LITERAL_IDS.get((phase, kind))
    if literal is not None:
        if suffix:
            raise ValueError(f"`{literal}` cannot carry a suffix; {suffix!r} has no well-formed id")
        return literal
    # spec only valid in design phase, plan only valid in plan phase
    if kind == "spec":
        raise ValueError(f"kind {kind!r} is only valid with phase {'design'!r}, not phase {phase!r}")
    if kind == "plan":
        raise ValueError(f"kind {kind!r} is only valid with phase {'plan'!r}, not phase {phase!r}")
    identifier = f"{kind}-{phase}"
    return f"{identifier}-{suffix}" if suffix else identifier


def artifact_path(
    slug: str,
    phase: str,
    kind: str,
    *,
    suffix: str | None = None,
    ext: str = "md",
    archived: bool = False,
    lane_dir: str | None = None,
) -> ArtifactRef:
    """`work/<slug>/references/<NN>-<phase>-<kind>[-<suffix>].<ext>`, with its id.

    `kind` is **required**, where `work-io` allowed `None` and emitted a bare
    `01-design.md`. Nothing in the lane writes one, and the optional form is what
    would make the id underivable — `f"{kind}-{phase}"` has no answer without a
    kind. Dropping the option is what lets the carrier always hold both spellings.

    Transcripts land flat here, as `03-execute-transcript.jsonl`, matching child
    1's shipped fixture rather than the survey's `references/transcripts/` sketch.
    """
    source_id = source_id_for(phase, kind, suffix)
    segments = [PHASE_ORDINALS[phase], phase, kind]
    if suffix:
        segments.append(suffix)
    filename = f"{'-'.join(segments)}.{ext}"
    return ArtifactRef(
        rel=f"{references_dir(slug, archived=archived, lane_dir=lane_dir).rel}/{filename}",
        source_id=source_id,
    )


__all__ = [
    "LEDGER_FILENAME",
    "PHASE_ORDINALS",
    "REFERENCES_DIRNAME",
    "ArtifactRef",
    "artifact_path",
    "decisions_ledger",
    "item_page",
    "references_dir",
    "source_id_for",
]
