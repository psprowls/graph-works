"""Shared prompt fragments: link syntax, and the layout-driven overview."""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Mapping, Sequence
from datetime import date

import pytest
from code_wiki_okf.config import load_config
from graph_works_core import (
    apply_init,
    plan_init,
    prompts,
)
from graph_works_core.lint_drift.drift_propagator import (
    build_drift_propagator_prompt,
    parse_drift_propagator_verdict,
)
from graph_works_core.prompts import _fragments
from graph_works_core.query.prompts.code_reader import (
    CODE_READER_SYSTEM,
    ORCHESTRATED_CODE_READER_SYSTEM,
)
from graph_works_core.query.prompts.librarian import build_librarian_system
from graph_works_core.query.prompts.query_orchestrator import QUERY_ORCHESTRATOR_SYSTEM
from graph_works_core.query.prompts.synthesizer import SYNTHESIZER_SYSTEM
from graph_works_core.workspace.layout import layout_for
from okf_ext.schemas import SchemaSet, load_schemas

_TODAY = date(2026, 8, 16)


def _full_schema_set(tmp_path) -> SchemaSet:
    """The eleven-type merged schema set `_TYPE_GLOSSES` names a row for.

    `graph_works_core.workspace.init.INSTALLERS` carries all three tier-3
    packages, so a default `apply_init(plan_init(...))` already installs
    `Tutorial`/`HowTo`/`Reference`/`Explanation`/`Source` alongside
    `code_wiki_okf`'s and `work_tracker_okf`'s schemas.
    """
    init = apply_init(plan_init(tmp_path, today=_TODAY, topic="Prompt tests", repo_root=tmp_path))
    config = load_config(init.layout.bundle_dir, config_path=init.layout.repositories_path)
    return load_schemas(config.declarations_dir / "_schema")


#: Fragment modules that export a renderer and no text constant, so the walk
#: below finds nothing in them. Naming one here is the deliberate decision the
#: guard demands; a new renderer-only fragment fails until someone makes it.
_RENDERER_ONLY = frozenset({"architecture_overview", "page_categories"})


def _texts(value: object) -> list[str]:
    """The strings *value* contributes, for a fragment module's exported name.

    A fragment is a string, or a container of them -- `LANE_GLOSSES` is a
    mapping of one gloss per lane, and every one of those reaches a model.
    Anything else (a renderer) contributes nothing and is skipped.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [item for item in value.values() if isinstance(item, str)]
    if isinstance(value, Sequence):
        return [item for item in value if isinstance(item, str)]
    return []


def _fragment_modules() -> list[tuple[str, object]]:
    """Every module under `_fragments/`, by its bare name."""
    return [
        (info.name, importlib.import_module(f"{_fragments.__name__}.{info.name}"))
        for info in pkgutil.iter_modules(_fragments.__path__)
    ]


def _fragment_texts() -> list[tuple[str, str, str]]:
    """Every `(module, name, text)` the `_fragments/` package exports.

    Derived from the package rather than hand-listed: a fragment added later
    is covered without anyone remembering to extend this, which is the whole
    point of the guards below. Each module's `__all__` is the source of truth,
    not its globals -- a globals scan picks up imported constants (e.g.
    `architecture_overview`'s `MANIFEST_FILENAME`) and would make a
    renderer-only module look as though it contributes text.
    """
    return [
        (name, exported, text)
        for name, module in _fragment_modules()
        for exported in getattr(module, "__all__", ())
        for text in _texts(getattr(module, exported))
    ]


FRAGMENTS = tuple(_fragment_texts())


def test_every_fragment_module_declares_its_exports():
    # `__all__` is what the walk above reads. A module without one contributes
    # nothing to `FRAGMENTS` and would be skipped in silence -- which is the
    # forgetting this guard exists to catch, one level up.
    for name, module in _fragment_modules():
        assert getattr(module, "__all__", None) is not None, f"{name} declares no __all__"


def test_every_fragment_module_contributes_text_or_is_a_renderer():
    covered = {module for module, _, _ in FRAGMENTS}
    for name, _ in _fragment_modules():
        assert name in covered or name in _RENDERER_ONLY, (
            f"{name} exports no fragment text and is not in _RENDERER_ONLY"
        )


def test_every_fragment_is_a_non_empty_string():
    for module, name, text in FRAGMENTS:
        assert text.strip(), f"{module}.{name} is empty"


def test_no_fragment_mandates_wikilink_syntax():
    # The vault uses root-absolute markdown links. A decision that lives only
    # in prose is one the next port re-breaks, so it is asserted here.
    for module, name, text in FRAGMENTS:
        assert "[[" not in text, f"{module}.{name} mandates wikilink syntax"


def test_no_fragment_ships_an_unsubstituted_placeholder():
    # `PAGE_CATEGORIES` shipped six `vault_path/…` rows into the ingestor and
    # librarian system prompts because nothing formatted the fragment and
    # nothing checked that it needed no formatting. A fragment is a finished
    # string by construction; anything that looks like a substitution slot in
    # one is a bug, not a template.
    for module, name, text in FRAGMENTS:
        assert "vault_path" not in text, f"{module}.{name} ships a vault_path placeholder"
        assert "{" not in text.replace("{{", "").replace("}}", ""), (
            f"{module}.{name} ships what looks like a substitution slot"
        )


def test_the_link_rules_name_the_syntax_they_do_want():
    assert "](/" in prompts.CITATION_RULES
    assert "](/" in prompts.STYLE_RULES
    assert "](/" in prompts.IRON_RULES


def test_the_overview_reflects_a_non_default_layout(tmp_path):
    layout = layout_for(tmp_path, bundle_dir="pages", cache_dir="state")
    rendered = prompts.render_architecture_overview(layout)
    assert "pages/" in rendered
    assert "state/" in rendered
    assert "okf/" not in rendered
    assert "_cache/" not in rendered


def test_the_overview_names_the_manifest_and_the_workspace_root(tmp_path):
    rendered = prompts.render_architecture_overview(layout_for(tmp_path))
    assert "workspace.yaml" in rendered
    assert tmp_path.name in rendered


def test_a_member_outside_the_root_renders_as_an_absolute_path(tmp_path):
    elsewhere = tmp_path.parent / "elsewhere-cache"
    layout = layout_for(tmp_path / "ws", cache_dir=str(elsewhere))
    assert str(elsewhere) in prompts.render_architecture_overview(layout)


_SCHEMA = """\
# Wiki schema

## Style

Be concise.

## Log format

```
## [YYYY-MM-DD] <op> | <title>
```

Valid ops: scan, ingest.

## Something else

Not extracted.
"""


def test_no_schema_file_renders_nothing(tmp_path):
    assert prompts.render_project_context(layout_for(tmp_path)) == ""


def test_claude_md_is_read_and_agents_md_is_the_fallback(tmp_path):
    (tmp_path / "AGENTS.md").write_text(_SCHEMA, encoding="utf-8")
    assert "AGENTS.md" in prompts.render_project_context(layout_for(tmp_path))

    (tmp_path / "CLAUDE.md").write_text(_SCHEMA, encoding="utf-8")
    rendered = prompts.render_project_context(layout_for(tmp_path))
    assert "CLAUDE.md" in rendered
    assert "AGENTS.md" not in rendered


def test_both_sections_are_extracted_and_named(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(_SCHEMA, encoding="utf-8")
    rendered = prompts.render_project_context(layout_for(tmp_path))
    assert "## Project style (CLAUDE.md §Style)" in rendered
    assert "## Log format (CLAUDE.md §Log format)" in rendered
    assert "Be concise." in rendered
    assert "wiki/CLAUDE.md" not in rendered


def test_a_heading_inside_a_fence_does_not_terminate_the_section(tmp_path):
    # The named regression: a `## [YYYY-MM-DD]` sample inside a fenced block
    # must not end the Log format section.
    (tmp_path / "CLAUDE.md").write_text(_SCHEMA, encoding="utf-8")
    rendered = prompts.render_project_context(layout_for(tmp_path))
    assert "Valid ops: scan, ingest." in rendered
    assert "Not extracted." not in rendered


def test_a_schema_with_neither_section_renders_empty(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Wiki\n\n## Other\n\nnope\n", encoding="utf-8")
    assert prompts.render_project_context(layout_for(tmp_path)) == ""


def test_only_the_style_section_renders_when_log_format_is_absent(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("## Style\n\nBe concise.\n", encoding="utf-8")
    rendered = prompts.render_project_context(layout_for(tmp_path))
    assert "§Style" in rendered
    assert "§Log format" not in rendered


# --------------------------------------------------------------------------
# Which directory project context comes from. The renderer takes the layout
# and derives the directory itself, so `ingest` and `lint` cannot disagree
# about it -- which they did, and one of them read a directory nothing ever
# writes a CLAUDE.md into.
# --------------------------------------------------------------------------


def test_project_context_comes_from_the_repo_the_workspace_lives_in(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".works").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("## Style\n\nFrom the repo root.\n", encoding="utf-8")
    layout = layout_for(repo / ".works", repo_root=repo)
    assert "From the repo root." in prompts.render_project_context(layout)


def test_project_context_falls_back_to_the_workspace_root_outside_a_repo(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("## Style\n\nFrom the workspace root.\n", encoding="utf-8")
    layout = layout_for(tmp_path)
    assert layout.repo_root is None
    assert "From the workspace root." in prompts.render_project_context(layout)


def test_the_config_dir_is_never_consulted_for_project_context(tmp_path):
    # `ingest` read `config.declarations_dir` -- the `_config/` directory --
    # which no package ever seeds a CLAUDE.md into, so its project-context
    # block rendered empty on every run.
    layout = layout_for(tmp_path)
    layout.config_dir.mkdir(parents=True)
    (layout.config_dir / "CLAUDE.md").write_text("## Style\n\nNot from here.\n", encoding="utf-8")
    assert prompts.render_project_context(layout) == ""


def test_the_disambiguation_fragment_describes_no_wiki_level_claude_md():
    # The fragment described the retired plugin layout: graph-works seeds no
    # wiki-level CLAUDE.md and reads none. A prompt that says otherwise sends
    # the model looking for a file that is not there.
    fragment = prompts.CLAUDE_MD_DISAMBIGUATION
    assert "the wiki's `CLAUDE.md`" not in fragment
    assert "CLAUDE.md" in fragment


def test_the_ingest_prompts_carry_no_wikilinks(tmp_path):
    """Spec §6.8: C2's rule extends to cover the three ingest prompts."""
    from doc_wiki_okf.proposals.lanes import lane_set
    from doc_wiki_okf.sources import seed_source_kinds
    from graph_works_core.ingest.prompts.extractor import build_extractor_system
    from graph_works_core.ingest.prompts.ingestor import build_ingestor_system
    from graph_works_core.ingest.prompts.proposal_reasoner import build_proposal_reasoner_system
    from graph_works_core.workspace.layout import layout_for
    from ingest_helpers import declarations
    from suggest_fixtures import make_bundle

    schema_set, _ = declarations(make_bundle(tmp_path))
    lanes = lane_set(schema_set)
    for text in (
        build_ingestor_system(layout=layout_for(tmp_path / ".works"), kinds=seed_source_kinds(), schema_set=schema_set),
        build_extractor_system(lane_set=lanes),
        build_proposal_reasoner_system(lane_set=lanes),
    ):
        assert "[[" not in text


def _role_prompts(schema_set: SchemaSet) -> tuple[str, ...]:
    return (
        build_librarian_system(schema_set=schema_set),
        SYNTHESIZER_SYSTEM,
        CODE_READER_SYSTEM,
        ORCHESTRATED_CODE_READER_SYSTEM,
        QUERY_ORCHESTRATOR_SYSTEM,
    )


def test_every_role_prompt_is_a_non_empty_string(tmp_path):
    for prompt in _role_prompts(_full_schema_set(tmp_path)):
        assert isinstance(prompt, str)
        assert prompt.strip()


def test_no_role_prompt_mandates_wikilink_syntax(tmp_path):
    # Same rule the fragments hold, extended to the prompts that compose them.
    # G1 resolves `[text](/path.md)` against the bundle; a prompt teaching the
    # model `[[…]]` would make every citation unresolvable by construction.
    for prompt in _role_prompts(_full_schema_set(tmp_path)):
        assert "[[" not in prompt


def test_the_librarian_preserves_the_sentinel_contract(tmp_path):
    # Both pipelines and the orchestrator's worker parser key off this literal.
    librarian_system = build_librarian_system(schema_set=_full_schema_set(tmp_path))
    assert "NO_RELEVANT_CONTENT" in librarian_system
    assert "NO_RELEVANT_CONTENT" in CODE_READER_SYSTEM
    assert "NO_RELEVANT_CONTENT" in ORCHESTRATED_CODE_READER_SYSTEM


def test_the_librarian_composes_the_shared_fragments(tmp_path):
    schema_set = _full_schema_set(tmp_path)
    librarian_system = build_librarian_system(schema_set=schema_set)
    for fragment in (
        prompts.IRON_RULES,
        prompts.render_page_categories(schema_set),
        prompts.CITATION_RULES,
        prompts.STYLE_RULES,
    ):
        assert fragment in librarian_system


def test_the_librarian_narrows_the_shared_write_role_rules(tmp_path):
    # The rider must land after STYLE_RULES so the override reads as "here's
    # how the rules above apply to you," not the reverse.
    librarian_system = build_librarian_system(schema_set=_full_schema_set(tmp_path))
    style_idx = librarian_system.index(prompts.STYLE_RULES)
    rider_idx = librarian_system.index("How the rules above apply to this role")
    assert rider_idx > style_idx + len(prompts.STYLE_RULES)

    assert "This call returns text; it does not file anything" in librarian_system
    assert "bare sentinel `NO_RELEVANT_CONTENT` — no" in librarian_system
    assert "Concision does not apply to extraction" in librarian_system


def test_the_librarian_workflow_describes_per_page_extraction(tmp_path):
    # The reference's step 1 told the model to read the index and pick 3-10
    # pages. This librarian is called per page, with the page already in its
    # human message — an instruction it cannot follow is one it improvises
    # around.
    librarian_system = build_librarian_system(schema_set=_full_schema_set(tmp_path))
    assert "read the index first" not in librarian_system.lower()
    assert "3-10 pages" not in librarian_system


def test_the_rendered_categories_match_the_schemas_declarations_exactly(tmp_path):
    # The structural guarantee that motivated a renderer over a static
    # rewrite: this fails the moment a schema's `x-okf-directory` changes and
    # the table doesn't, the same class of drift
    # test_no_fragment_ships_an_unsubstituted_placeholder already guards for
    # the vault_path case.
    from okf_ext.schemas import declared_directories

    schema_set = _full_schema_set(tmp_path)
    rendered = prompts.render_page_categories(schema_set)
    rendered_categories = {
        line.split("|")[1].strip().strip("`") for line in rendered.splitlines() if line.startswith("| `")
    }

    expected = {"adr", "work"}
    for type_name, directory in declared_directories(schema_set).items():
        if type_name == "File":
            continue  # File shares Repository's directory for its own mirror sub-pages; not a top-level category
        expected.add(directory.rstrip("/"))
    # Directory names and category names differ only by pluralization for the
    # eleven schema-sourced rows (e.g. "how-tos" -> "how-to"); assert on
    # cardinality and on each schema type's specific category name instead of
    # a literal string match against the directory.
    assert len(rendered_categories) == len(expected)
    assert {
        "app",
        "package",
        "dependency",
        "agent-plugin",
        "test-suite",
        "repository",
        "tutorial",
        "how-to",
        "reference",
        "explanation",
        "source",
        "adr",
        "work",
    } == rendered_categories


def test_a_schema_set_with_no_wiki_types_renders_only_the_constant_rows(tmp_path):
    # Isolates render_page_categories from the eleven-type production set:
    # an empty-but-valid SchemaSet still names adr and work, and nothing else.
    from okf_ext.schemas import SchemaSet

    empty = SchemaSet(schemas={}, sources={}, documents={}, root=tmp_path)
    rendered = prompts.render_page_categories(empty)
    assert "`adr`" in rendered
    assert "`work`" in rendered
    assert "`app`" not in rendered
    assert "`concept`" not in rendered


def test_file_does_not_produce_a_second_repositories_row(tmp_path):
    # File.schema.json declares the same x-okf-directory as Repository, for
    # its own mirror sub-pages -- a naive one-row-per-type render would show
    # `repositories/` twice. Counted by table row, not raw substring: the
    # `repository` row's own gloss text ("...repository this workspace
    # tracks") already contains the word a second time.
    schema_set = _full_schema_set(tmp_path)
    rendered = prompts.render_page_categories(schema_set)
    rows = [line for line in rendered.splitlines() if line.startswith("| `repository`")]
    assert len(rows) == 1


def test_the_orchestrator_prompt_states_the_json_contract():
    for key in ("answer_markdown", "answer_evidence_map", "worker_plan", "worker_results", "confidence"):
        assert key in QUERY_ORCHESTRATOR_SYSTEM


# --------------------------------------------------------------------------
# The drift-propagator judge prompt and its fail-safe parser.
# --------------------------------------------------------------------------

_ENTITIES = [("okf-io", "Reads OKF v0.2 documents.", ["packages/okf-io/src/okf_io/bundle.py"])]


def test_the_judge_prompt_names_the_page_and_every_entity():
    system, human = build_drift_propagator_prompt("concept", "Byte fidelity", "The body.", _ENTITIES)
    assert "JSON" in system
    assert "Byte fidelity" in human
    assert "The body." in human
    assert "okf-io" in human
    assert "packages/okf-io/src/okf_io/bundle.py" in human
    assert "Reads OKF v0.2 documents." in human


def test_an_entity_with_no_changed_files_says_so_rather_than_showing_an_empty_list():
    _system, human = build_drift_propagator_prompt("concept", "T", "B", [("e", "n", [])])
    assert "(no specific files identified)" in human


def test_the_adr_rubric_is_annotate_only():
    _system, human = build_drift_propagator_prompt("adr", "ADR-0001", "B", _ENTITIES)
    assert "decision history" in human


def test_an_unknown_kind_falls_back_to_the_concept_rubric():
    _s, concept = build_drift_propagator_prompt("concept", "T", "B", _ENTITIES)
    _s2, unknown = build_drift_propagator_prompt("tutorial", "T", "B", _ENTITIES)
    assert unknown.replace("Page kind: tutorial", "Page kind: concept") == concept


def test_a_well_formed_stale_verdict_parses():
    verdict = parse_drift_propagator_verdict(
        '{"stale": true, "findings": [{"entity_stem": "okf-io", "stale_claim": "c", "rationale": "r"}]}'
    )
    assert verdict == {"stale": True, "findings": [{"entity_stem": "okf-io", "stale_claim": "c", "rationale": "r"}]}


def test_a_fenced_reply_parses():
    verdict = parse_drift_propagator_verdict(
        '```json\n{"stale": true, "findings": [{"entity_stem": "e", "stale_claim": "", "rationale": ""}]}\n```'
    )
    assert verdict["stale"] is True


def test_non_string_finding_fields_are_stringified():
    verdict = parse_drift_propagator_verdict(
        '{"stale": true, "findings": [{"entity_stem": "e", "stale_claim": 7, "rationale": null}]}'
    )
    assert verdict["findings"][0]["stale_claim"] == "7"
    assert verdict["findings"][0]["rationale"] == "None"


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "   ",
        "I could not decide.",
        "{not json}",
        '{"stale": false, "findings": []}',
        '{"stale": true, "findings": []}',
        '{"stale": true, "findings": [{"stale_claim": "c"}]}',
        '{"stale": true, "findings": ["not a mapping"]}',
        '{"stale": true, "findings": null}',
        '{"stale": true, "findings": 5}',
        '{"stale": "false", "findings": [{"entity_stem": "e", "stale_claim": "c", "rationale": "r"}]}',
        '{"stale": true, "findings": [{"entity_stem": null, "stale_claim": "c", "rationale": "r"}]}',
    ],
)
def test_every_unusable_reply_fails_safe_to_not_stale(reply):
    assert parse_drift_propagator_verdict(reply) == {"stale": False, "findings": []}


# --------------------------------------------------------------------------
# The three semantic-linter system prompts.
# --------------------------------------------------------------------------

from graph_works_core.lint_drift.linter import (  # noqa: E402
    LINT_PRIORITY_ORDER,
    build_linter_adr_chain_system,
    build_linter_page_quality_system,
    build_linter_stale_claims_system,
)
from graph_works_core.prompts import CLAUDE_MD_DISAMBIGUATION, IRON_RULES, LOG_FORMAT  # noqa: E402

_BUILDERS = (
    build_linter_page_quality_system,
    build_linter_adr_chain_system,
    build_linter_stale_claims_system,
)


@pytest.mark.parametrize("build", _BUILDERS)
def test_every_linter_prompt_carries_the_shared_fragments(build):
    prompt = build()
    for fragment in (IRON_RULES, LINT_PRIORITY_ORDER, LOG_FORMAT, CLAUDE_MD_DISAMBIGUATION):
        assert fragment in prompt


@pytest.mark.parametrize("build", _BUILDERS)
def test_project_context_lands_right_after_the_role_intro(build):
    context = "## Project style (CLAUDE.md §Style)\n\nBe terse."
    prompt = build(project_context=context)
    assert context in prompt
    assert prompt.index(context) < prompt.index(IRON_RULES)


@pytest.mark.parametrize("build", _BUILDERS)
def test_an_empty_project_context_adds_nothing(build):
    assert build(project_context="") == build()


def test_the_three_prompts_are_distinct():
    prompts_set = {build() for build in _BUILDERS}
    assert len(prompts_set) == 3


def test_the_stale_claims_prompt_names_the_rebuilds_provenance_key():
    prompt = build_linter_stale_claims_system()
    assert "sources[]" in prompt
    assert "package_path" not in prompt


def test_every_prompt_forbids_writes():
    for build in _BUILDERS:
        assert "report only" in build()


def test_the_ingestor_prompt_states_the_index_and_log_rule_once(tmp_path):
    # Three sections of one assembled prompt used to disagree: iron rule 5
    # mandated touching index.md and log.md, LOG_FORMAT taught the syntax for
    # doing it, and the source-landing section said to omit both steps. The
    # code reconciles them at ingest.py:615 and :618, so the landing section is
    # the one that was right.
    from graph_works_core.ingest.prompts.ingestor import build_ingestor_system

    system = build_ingestor_system(
        layout=layout_for(tmp_path), kinds=("article",), schema_set=_full_schema_set(tmp_path)
    )
    assert "## Log format" not in system
    assert "reconciled automatically" in system
    assert "touches ≥3 files" not in system


def test_the_frontmatter_fragment_names_the_keys_the_ingest_path_reads():
    # The fragment was wrong in both directions: it asked for `category`,
    # `summary`, `package_path`, `language` and `updated` — none read by
    # anything, all landing in `fm.extra` — under a "Scanner stub pages"
    # heading describing work the same prompt forbids, and it never mentioned
    # the four keys `CARRIED_KEYS` does carry.
    from graph_works_core.ingest.commands import CARRIED_KEYS

    for key in ("title", "description", "source_kind", *CARRIED_KEYS):
        assert key in prompts.FRONTMATTER_RULES, key
    for dead in ("category", "package_path", "language", "Scanner stub"):
        assert dead not in prompts.FRONTMATTER_RULES, dead


def test_style_rules_does_not_mandate_a_key_frontmatter_rules_forbids():
    # STYLE_RULES told the model to "Update `updated:` frontmatter whenever you
    # touch a page" -- `updated` is not one of the keys FRONTMATTER_RULES lists,
    # and FRONTMATTER_RULES itself says any other key is dropped on read. The
    # librarian never writes at all, so the bullet has no referent there either.
    assert "updated" not in prompts.STYLE_RULES
