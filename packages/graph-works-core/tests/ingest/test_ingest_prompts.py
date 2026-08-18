"""The three ingest prompts: composition order, link syntax, and vocabulary."""

from __future__ import annotations

from dataclasses import replace

from doc_wiki_okf.proposals.lanes import Lane, lane_set
from doc_wiki_okf.sources import seed_source_kinds
from graph_works_core import prompts
from graph_works_core.ingest.prompts.extractor import build_extractor_system
from graph_works_core.ingest.prompts.ingestor import build_ingestor_system
from graph_works_core.ingest.prompts.proposal_reasoner import build_proposal_reasoner_system
from graph_works_core.workspace.layout import layout_for
from ingest_helpers import declarations
from suggest_fixtures import make_bundle


def _lane_set(tmp_path):
    """The real `LaneSet` a seeded bundle produces -- no lane name written down."""
    schema_set, _ = declarations(make_bundle(tmp_path))
    return lane_set(schema_set)


def _schema_set(tmp_path):
    """The real `SchemaSet` a seeded bundle produces -- same fixture the
    ingestor's own `kinds` vocabulary is read from."""
    schema_set, _ = declarations(make_bundle(tmp_path))
    return schema_set


def _extractor(tmp_path):
    return build_extractor_system(lane_set=_lane_set(tmp_path))


def _reasoner(tmp_path):
    return build_proposal_reasoner_system(lane_set=_lane_set(tmp_path))


def _system(tmp_path, kinds=None):
    return build_ingestor_system(
        layout=layout_for(tmp_path / ".works"),
        kinds=seed_source_kinds() if kinds is None else kinds,
        schema_set=_schema_set(tmp_path),
    )


def test_no_prompt_in_this_vertical_contains_a_wikilink(tmp_path):
    for text in (_system(tmp_path), _extractor(tmp_path), _reasoner(tmp_path)):
        assert "[[" not in text


def test_the_ingestor_composes_the_shared_fragments(tmp_path):
    text = _system(tmp_path)
    for fragment in (
        prompts.IRON_RULES,
        prompts.render_page_categories(_schema_set(tmp_path)),
        prompts.CITATION_RULES,
        prompts.STYLE_RULES,
    ):
        assert fragment in text


def test_the_no_code_fence_rule_is_last(tmp_path):
    assert _system(tmp_path).rstrip().endswith("MUST be `---`.")


def test_the_ingestor_names_every_source_kind(tmp_path):
    text = _system(tmp_path)
    for kind in seed_source_kinds():
        assert f"`{kind}`" in text


def test_the_ingestor_names_the_bundles_vocabulary_not_the_seeds(tmp_path):
    """K-D: a vault that edits its `Source` schema changes what the model is
    told, with no code change here."""
    text = _system(tmp_path, kinds=("alpha", "beta"))
    assert "- `alpha`" in text
    assert "- `beta`" in text
    assert "- `spec`" not in text


def test_the_ingestor_names_the_declared_source_sections(tmp_path):
    text = _system(tmp_path)
    for heading in ("TL;DR", "Key claims", "Touches", "Evidence / rationale"):
        assert heading in text


def test_the_ingestor_says_nothing_about_raw_or_page_type_or_stripping(tmp_path):
    text = _system(tmp_path)
    assert "raw/" not in text
    assert "page_type" not in text
    assert "STRIP" not in text.upper()
    assert "source_type" not in text


def test_output_format_defers_to_frontmatter_rules_not_a_partial_list(tmp_path):
    """The Output format section must point at Frontmatter rules rather than
    re-enumerate fields -- a partial list here is exactly how authors,
    source_date, tags and tokens got silently dropped from ingested pages."""
    text = _system(tmp_path)
    section = text.split("## Output format")[1].split("## Frontmatter format")[0]
    assert "Frontmatter rules" in section
    for key in ("source_kind", "authors", "source_date", "tags", "tokens"):
        assert f"`{key}`" not in section


def test_project_context_is_inserted_second_when_given(tmp_path):
    layout = layout_for(tmp_path / ".works")
    kinds = seed_source_kinds()
    schema_set = _schema_set(tmp_path)
    with_context = build_ingestor_system(
        layout=layout, kinds=kinds, schema_set=schema_set, project_context="## Project\n\nContext body."
    )
    assert "Context body." in with_context
    assert with_context.index("Context body.") < with_context.index(prompts.IRON_RULES)
    assert "Context body." not in build_ingestor_system(layout=layout, kinds=kinds, schema_set=schema_set)


def test_the_extractor_names_every_lane_and_demands_a_rationale(tmp_path):
    text = _extractor(tmp_path)
    for lane in _lane_set(tmp_path).lanes:
        assert lane.name in text
    assert "rationale" in text
    assert "JSON" in text


def test_the_extractor_no_longer_speaks_the_retired_vocabulary(tmp_path):
    for retired in ("concept_kind", "existing_slug", "create_new", "update_existing"):
        assert retired not in _extractor(tmp_path)


def test_the_reasoner_names_every_lane(tmp_path):
    text = _reasoner(tmp_path)
    for lane in _lane_set(tmp_path).lanes:
        assert lane.name in text


def test_a_sixth_lane_is_named_in_both_prompts_with_no_edit_to_either_module(tmp_path):
    """F1's whole point. A lane the `LaneSet` declares and the prompts do not
    is a silent zero-proposals run: the model keeps proposing the old names and
    `_validate_suggestion` drops every suggestion in the new lane with no error
    anywhere. Names come off the set, so that state is unrepresentable. The
    gloss is optional editorial -- a new lane renders as its bare name.
    """
    base = _lane_set(tmp_path)
    sixth = Lane(name="runbook", directory="runbooks/", type_name="HowTo", dated=False)
    widened = replace(base, lanes=(*base.lanes, sixth))
    for text in (build_extractor_system(lane_set=widened), build_proposal_reasoner_system(lane_set=widened)):
        assert "runbook" in text
