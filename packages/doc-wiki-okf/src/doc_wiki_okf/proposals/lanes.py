"""The lane map for proposal rendering.

Five lanes: four Diátaxis types, each with a directory derived from the loaded
SchemaSet, plus one ADR lane (the only dated lane, using Explanation type in
adrs/).

The lane map determines where a new proposal lands (directory, schema type, dating)
based on the lane it targets. Dates are assigned at promotion time, not filing time,
so only the ADR lane (whose filename convention embeds the date) is dated; Diátaxis
lanes are undated. This layer lives in proposals because the lane concept belongs to
the proposal workflow, not the schema layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from okf_ext.schemas import SchemaSet

from doc_wiki_okf.diataxis.pages import directory_for
from doc_wiki_okf.reading import slugify

#: The four Diátaxis lane names, in order, mapped to their schema type names.
DIATAXIS_LANES = {
    "tutorial": "Tutorial",
    "how-to": "HowTo",
    "reference": "Reference",
    "explanation": "Explanation",
}

#: The ADR lane's directory and type (Explanation in adrs/).
ADR_DIRECTORY = "adrs/"
ADR_TYPE = "Explanation"


def is_adr(concept_id: str, type_name: str) -> bool:
    """An ADR is the ADR lane's type **in** the ADR lane's directory.

    Both halves are required: `ADR_TYPE` is `Explanation`, which the Diátaxis
    explanation lane also uses, so the type alone would sweep every explanation
    page into the ADR checks.

    Takes the type name rather than a document, so the package that owns the
    vocabulary needs no okf-io type in its interface; every caller already has
    `(document.fm.type or "")` in hand.
    """
    return concept_id.startswith(ADR_DIRECTORY) and type_name.strip() == ADR_TYPE


@dataclass(frozen=True, slots=True)
class Lane:
    """A proposal lane (directory + type).

    Attributes:
        name: The lane name (e.g. "tutorial", "adr").
        directory: The bundle-relative directory (e.g. "tutorials/").
        type_name: The OKF schema type name (e.g. "Tutorial", "Explanation").
        dated: Whether filenames in this lane use YYYY-MM-DD dating.
    """

    name: str
    directory: str
    type_name: str
    dated: bool


@dataclass(frozen=True, slots=True)
class LaneSet:
    """All five proposal lanes, in order.

    The four Diátaxis lanes (tutorial, how-to, reference, explanation) plus
    one ADR lane (the only dated lane).
    """

    lanes: tuple[Lane, ...]

    def target_for(self, lane_name: str, title: str, *, on: date | None = None) -> str:
        """The path a proposal targeting *lane_name* with *title* is created at.

        Bundle-relative, with `.md` suffix. Undated by default; prefixed with
        YYYY-MM-DD when on= is given AND the lane is dated (only adr).

        Raises `KeyError` for an unknown lane name.
        """
        lane = next((lane for lane in self.lanes if lane.name == lane_name), None)
        if lane is None:
            raise KeyError(lane_name)

        slug = slugify(title)
        if lane.dated and on is not None:
            return f"{lane.directory}{on.isoformat()}-{slug}.md"
        return f"{lane.directory}{slug}.md"

    def lane_for(self, target: str) -> Lane | None:
        """The lane that owns *target*, or None if no lane does.

        Looks up by directory prefix: returns the lane whose directory prefixes
        the target path (e.g. target="adrs/2026-08-12-x.md" matches lane.directory
        "adrs/"). Checks longer directory prefixes first (more specific wins).
        Returns None if no lane's directory prefixes the target.
        """
        # Sort lanes by directory length (descending) to match longer/more specific prefixes first
        sorted_lanes = sorted(self.lanes, key=lambda lane: len(lane.directory), reverse=True)
        return next((lane for lane in sorted_lanes if target.startswith(lane.directory)), None)

    def __getitem__(self, name: str) -> Lane:
        """Access a lane by name.

        Raises `KeyError` for an unknown lane name.
        """
        lane = next((lane for lane in self.lanes if lane.name == name), None)
        if lane is None:
            raise KeyError(name)
        return lane


def lane_set(schema_set: SchemaSet) -> LaneSet:
    """Build the LaneSet from the loaded SchemaSet.

    The four Diátaxis lanes' directories are derived via directory_for(); the
    ADR lane is hardcoded.

    Raises `KeyError` if any Diátaxis type is missing from the schema set.
    """
    diataxis_lanes: list[Lane] = []
    for lane_name, type_name in DIATAXIS_LANES.items():
        diataxis_dir = directory_for(schema_set, type_name)
        diataxis_lanes.append(
            Lane(
                name=lane_name,
                directory=diataxis_dir,
                type_name=type_name,
                dated=False,
            )
        )

    adr_lane = Lane(
        name="adr",
        directory=ADR_DIRECTORY,
        type_name=ADR_TYPE,
        dated=True,
    )

    return LaneSet(lanes=(*diataxis_lanes, adr_lane))


__all__ = ["Lane", "LaneSet", "lane_set"]
