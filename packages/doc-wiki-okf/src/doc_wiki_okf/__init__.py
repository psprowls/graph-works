"""doc-wiki-okf: the documentation-wiki lane over OKF v0.2.

Tier 3 per ADR 2026-08-02-workspace-layering: depends on `okf-io` and `okf-ext[schemas]`, and nothing
depends on this package.

Five subpackages. `doc_wiki_okf.reading` is substrate-neutral file and format
inspection that knows nothing of OKF, wikis or workspaces:

    from doc_wiki_okf.reading import extract, gather_skill_sources, slugify

`doc_wiki_okf.diataxis` is the four Diátaxis types — the taxonomy as data, a
classifier that validates a decision rather than making one, placement read off
each type's `x-okf-directory`, and retyping as a move:

    from doc_wiki_okf.diataxis import TYPE_NAMES, classify, plan_retype

`doc_wiki_okf.proposals` is the lane map, the review renderer, and the two
compositions over `okf_ext.proposals` -- filing and promotion:

    from doc_wiki_okf.proposals import lane_set, plan_file, plan_promotion

`doc_wiki_okf.proposals.migrate` is the old-dialect rewriter -- the
`kind`/`mode`/`target_slug`/`origins[]` ledger converted to `okf_ext.proposals`
shape, as a plan you inspect before anything is written:

    from doc_wiki_okf.proposals import migrate_and_move, plan_migrate

`doc_wiki_okf.ingest` is brief assembly — point at a path and learn what you are
about to deal with, as one of three frozen briefs whose `as_data()` re-emits the
legacy dict verbatim:

    from doc_wiki_okf.ingest import plan_batch_brief, plan_document_brief, plan_folder_brief

`doc_wiki_okf.sources` records ingested material: one `Source` page and a copy
of the material beside it, as a single plan carrying two create writes:

    from doc_wiki_okf.sources import plan_ingest

`doc_wiki_okf.archive` mirrors `work_tracker_okf.archive`'s shape over this
lane's seven page directories -- targeted archiving unconditional, sweep mode
covering proposals only:

    from doc_wiki_okf.archive import apply_archive, plan_archive

`reading/` imports the standard library and itself, and nothing else; `ingest/`
may import `reading/`; `diataxis/` may import `reading/`; `proposals/` may import
both, and none of them imports `ingest/` or `sources/`. That is a test
(`tests/test_reading_boundaries.py`), not a convention.
"""

from __future__ import annotations

__version__ = "0.3.3"
