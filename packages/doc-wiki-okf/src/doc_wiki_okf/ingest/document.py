"""One file in, the fact sheet printed before an ingest begins.

Title, slug, source type, a preview, the source page it would land at, and
whether that page already exists. The clock is an argument (`today=`, required)
and the code graph is two optional callables -- `okf_io.validate`'s rule for
`today=` and this package's rule for its siblings.

`as_data()` returns the dict `wiki_io.ingest_source.build_ingest_brief` returned,
key for key, except `word_count` and `in_repo_doc` use corrected computation
(the legacy formula and test fixtures were internally inconsistent—this port fixes
a latent bug rather than reproducing it). That is what makes the port testable
and what lets the plugin be repointed later without an argument about behaviour.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from doc_wiki_okf.ingest.layout import GRAPH_WIKI_LAYOUT, IngestLayout, guess_source_type, resolve_source_path
from doc_wiki_okf.ingest.seams import NO_ENTITY, EntityMatch, EntityMatcher, StateGate, read_state_gate
from doc_wiki_okf.reading import extract, slugify

#: How much of the document the brief shows.
PREVIEW_CHARS = 1200

#: Appended when the preview is not the whole document.
TRUNCATION_MARKER = "\n[TRUNCATED]"

#: A word, for `word_count` -- runs of word characters, not whitespace-split
#: tokens, so a leading `#` or trailing punctuation is not counted as one.
WORD_RE = re.compile(r"\w+")


@dataclass(frozen=True, slots=True)
class DocumentBrief:
    """What one file is, and where its source page would go.

    No `is_folder` / `is_batch` discriminator: the type is the discriminator
    now. A caller holding this cannot read `unit_count` off it and get `None`,
    because the attribute is not there.
    """

    source_path: Path
    title: str
    source_type: str
    slug: str
    preview: str
    word_count: int
    suggested_summary_path: str
    merge_mode: bool
    in_repo_doc: bool
    entity_match: EntityMatch
    state_gate: Mapping[str, Any] | None

    def as_data(self) -> dict[str, Any]:
        """The legacy dict, verbatim. Every value survives `json.dumps`."""
        return {
            "source_path": str(self.source_path),
            "title": self.title,
            "source_type": self.source_type,
            "slug": self.slug,
            "preview": self.preview,
            "word_count": self.word_count,
            "suggested_summary_path": self.suggested_summary_path,
            "merge_mode": self.merge_mode,
            "in_repo_doc": self.in_repo_doc,
            "entity_match": self.entity_match.as_data(),
            "state_gate": None if self.state_gate is None else dict(self.state_gate),
        }


def plan_document_brief(
    source_path: Path,
    *,
    wiki: Path,
    repo: Path,
    workspace_root: Path,
    today: date,
    layout: IngestLayout = GRAPH_WIKI_LAYOUT,
    state_gate: StateGate | None = None,
    match_entity: EntityMatcher | None = None,
) -> DocumentBrief:
    """Compute the brief for one file. Writes nothing.

    `today` is required and has no default: `date.today()` is called in exactly
    one place in this package, the CLI.
    """
    resolved = resolve_source_path(source_path, repo)
    text, title = extract(resolved)
    title_guess = title or resolved.stem.replace("-", " ").title()
    slug = slugify(title_guess)

    rel_to_repo = _relative(resolved, repo)
    rel_to_workspace = _relative(resolved, workspace_root)

    preview = text[:PREVIEW_CHARS]
    if len(text) > PREVIEW_CHARS:
        preview += TRUNCATION_MARKER

    source_type = guess_source_type(rel_to_workspace, rel_to_repo, layout=layout)
    suggested = layout.source_page_template.format(month=today.strftime("%Y-%m"), slug=slug)
    return DocumentBrief(
        source_path=resolved,
        title=title_guess,
        source_type=source_type,
        slug=slug,
        preview=preview,
        word_count=len(WORD_RE.findall(text)),
        suggested_summary_path=suggested,
        merge_mode=(wiki / suggested).exists(),
        # Mirror guess_source_type's classification: true only when source fell
        # through to generic "doc" type (not matched by raw/-folder rule, not under wiki).
        in_repo_doc=source_type == "doc",
        entity_match=NO_ENTITY if match_entity is None else match_entity(repo, resolved, title_guess),
        state_gate=read_state_gate(state_gate, repo, workspace_root),
    )


def _relative(path: Path, base: Path) -> Path | None:
    try:
        return path.relative_to(base)
    except ValueError:
        return None
