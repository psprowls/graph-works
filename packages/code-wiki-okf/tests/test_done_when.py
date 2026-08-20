"""The done-when: two lanes, one bundle, one clean validate.

The second lane here is synthetic on purpose. `diataxis-okf` does not exist
yet -- this item makes room for it and proves the room is real; it does not
build it. A `Topic` type disjoint from all seven code-wiki types, plus two
`_base-<lane>.schema.json` files it `$ref`s, is exactly enough to exercise the
three properties the whole design rests on: `load_schemas` keys the dispatch
table on type name, an `_`-prefixed file is a `$ref` target that stays out of
that table, and several such bases coexist in one `_schema/` directory.

**What this gate does not cover.** `_tags.yaml` merge coexistence is deferred
to the `2026-08-09-feature-tags-vocabulary-install-merge` work item. It also
does not exercise type-name collisions between lanes, and the two shapes that
word covers behave differently. `load_schemas` keys its dispatch table, and
its duplicate guard, on the *filename* it derives a type name from -- not on
the schema's own `"type": {"const": ...}` -- so two lanes can only trip
`SchemaError` by shipping the same filename, which `plan_install` already
catches first, as `foreign-content` when the content differs or a silent skip
when it is byte-identical; `SchemaError` from `load_schemas` is unreachable
through this API. The reachable hazard is the opposite one, and nothing
catches it: two lanes claiming the same frontmatter `type` under two
*different* filenames both install, both load, and one silently never
matches a page again -- no refusal, no finding, no error. `Topic` is
deliberately disjoint from all seven code-wiki types precisely to stay clear
of that hazard, not to exercise it.
"""

from datetime import date
from pathlib import Path

from code_wiki_okf.init import SEED_RELATIVE_PATHS, install_bundle
from okf_ext.bundle import SCAFFOLD_LOG_ENTRY, apply, plan_install, plan_scaffold
from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import load_sections, section_rule
from okf_ext.tags import load_vocabulary, vocabulary_rule
from okf_io import load_bundle, update_index, validate

_TODAY = date(2026, 1, 1)

_BASE_DIATAXIS = """{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$defs": {
    "diataxis_kind": {
      "type": "string",
      "enum": ["tutorial", "how-to", "reference", "explanation"]
    }
  }
}
"""

#: A second lane-prefixed base, alongside `_base-diataxis`. One base proves
#: the `$ref` mechanism; two prove S-H's actual convention -- several bases
#: coexisting in one `_schema/`, each reachable by its own filename.
_BASE_EXTRA = """{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$defs": {
    "topic_status": {
      "type": "string",
      "enum": ["draft", "published"]
    }
  }
}
"""

_TOPIC_SCHEMA = """{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["type", "title", "diataxis_kind"],
  "additionalProperties": true,
  "properties": {
    "type": { "const": "Topic" },
    "title": { "type": "string", "minLength": 1 },
    "description": { "type": "string" },
    "diataxis_kind": { "$ref": "_base-diataxis.schema.json#/$defs/diataxis_kind" },
    "topic_status": { "$ref": "_base-extra.schema.json#/$defs/topic_status" }
  },
  "x-okf-directory": "topics/"
}
"""

_TOPIC_SECTIONS = """frontmatter:
  owned: [diataxis_kind]
  provenance: []
sections:
  - heading: What this covers
    required: true
    placeholder: |
      > TODO: what this topic covers.
"""

_TOPIC_PAGE = """---
type: Topic
title: "Getting started"
description: "The first page of the second lane."
diataxis_kind: tutorial
topic_status: draft
---

## What this covers

The second lane's own page, written by nobody in this repository.
"""

_SECOND_LANE_DECLARATIONS = {
    "_schema/_base-diataxis.schema.json": _BASE_DIATAXIS,
    "_schema/_base-extra.schema.json": _BASE_EXTRA,
    "_schema/Topic.schema.json": _TOPIC_SCHEMA,
    "_sections/Topic.yaml": _TOPIC_SECTIONS,
}


def _install_second_lane(root: Path) -> None:
    """What a second tier-3 package's own `plan_install` would do -- it calls
    the same tier-2 entry point `code_wiki_okf.plan_install` wraps."""
    result = apply(plan_install(root, _SECOND_LANE_DECLARATIONS))
    assert result.ok, result.failed


def _write_topic_page(root: Path) -> None:
    (root / "topics").mkdir(exist_ok=True)
    (root / "topics" / "getting-started.md").write_text(_TOPIC_PAGE, encoding="utf-8")
    update_index(load_bundle(root), directories=["topics"], create_missing=True, dry_run=False)


def _report(root: Path):
    """Validate with both lanes' schemas, sections and tags rules active."""
    schema_set = load_schemas(root / "_schema")
    section_set = load_sections(root / "_sections")
    vocabulary = load_vocabulary(root / "_tags.yaml")
    return validate(
        load_bundle(root),
        today=_TODAY,
        extra_rules=[
            schema_rule(schema_set),
            section_rule(section_set),
            vocabulary_rule(vocabulary),
        ],
    )


def test_freshly_installed_bundle_validates_clean(tmp_path: Path) -> None:
    """The single-lane case, unchanged in substance from before this item."""
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    report = _report(root)
    assert report.ok, report.errors


def test_code_wiki_installs_into_a_bundle_a_second_lane_already_created(tmp_path: Path) -> None:
    """The done-when that matters. The second lane scaffolds and installs
    first; `code-wiki-okf` arrives afterwards and refuses nothing."""
    root = tmp_path / "bundle"
    apply(plan_scaffold(root, today=_TODAY))
    _install_second_lane(root)
    _write_topic_page(root)

    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert result.ok, (result.scaffold.failed, result.install.failed)
    assert result.scaffold.written == ()  # the second lane already scaffolded
    # Stronger than a bare count, and self-documenting: an unrelated future
    # change to the seed set cannot break this file for reasons unconnected to
    # co-existence.
    assert set(result.install.written) == set(SEED_RELATIVE_PATHS)

    assert (root / "topics" / "getting-started.md").read_text(encoding="utf-8") == _TOPIC_PAGE

    # log.md carries two acts by two different owners in one file: tier 2's
    # neutral scaffold line (logged when the second lane scaffolded, before
    # code-wiki-okf ever arrived) and code-wiki-okf's own arrival line.
    log_text = (root / "log.md").read_text(encoding="utf-8")
    assert SCAFFOLD_LOG_ENTRY in log_text
    assert "installed by code-wiki-okf/" in log_text

    # Two lanes' *pages*, not just their declarations, coexist in one
    # reconciled bundle-root index.
    update_index(load_bundle(root), directories=[""], create_missing=True, dry_run=False)
    root_index = (root / "index.md").read_text(encoding="utf-8")
    assert "topics/index.md" in root_index

    report = _report(root)
    assert report.ok, report.errors


def test_a_second_lane_installs_into_a_bundle_code_wiki_already_created(tmp_path: Path) -> None:
    """Proved both ways round: whichever package arrives first, the second one
    installs rather than refuses."""
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    _install_second_lane(root)
    _write_topic_page(root)

    report = _report(root)
    assert report.ok, report.errors


def test_both_lanes_types_are_live_and_the_prefixed_base_stays_out_of_the_table(tmp_path: Path) -> None:
    """`load_schemas` keys on type name and holds `_`-prefixed files as `$ref`
    targets only -- the properties the whole co-existence design rests on,
    proved over *two* lane-prefixed bases so the plural in S-H's convention is
    not just asserted, it is exercised."""
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    _install_second_lane(root)

    schema_set = load_schemas(root / "_schema")
    assert "Topic" in schema_set.schemas
    assert {"Package", "App", "Dependency", "TestSuite", "Repository", "AgentPlugin", "File"} <= set(schema_set.schemas)
    assert "_base-diataxis" not in schema_set.schemas
    assert "_base-extra" not in schema_set.schemas
    assert "_base-diataxis.schema.json" in schema_set.documents
    assert "_base-extra.schema.json" in schema_set.documents


def test_the_prefixed_base_ref_actually_resolves(tmp_path: Path) -> None:
    """S-H's convention proved live, not merely present: a page violating the
    base's own enum is a finding, which it could not be if the `$ref` were
    being silently ignored."""
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    _install_second_lane(root)
    (root / "topics").mkdir(exist_ok=True)
    (root / "topics" / "bad.md").write_text(
        _TOPIC_PAGE.replace("diataxis_kind: tutorial", "diataxis_kind: not-a-kind"), encoding="utf-8"
    )
    update_index(load_bundle(root), directories=["topics"], create_missing=True, dry_run=False)

    report = _report(root)
    assert any(finding.code == "schemas.invalid" for finding in report.findings), report.findings


def test_the_second_prefixed_base_ref_also_resolves(tmp_path: Path) -> None:
    """The same property, proved over the *second* base. One `$ref` resolving
    could be luck in `build_registry`'s iteration order; two, into two
    different documents, is the convention actually working."""
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    _install_second_lane(root)
    (root / "topics").mkdir(exist_ok=True)
    (root / "topics" / "bad-status.md").write_text(
        _TOPIC_PAGE.replace("topic_status: draft", "topic_status: not-a-status"), encoding="utf-8"
    )
    update_index(load_bundle(root), directories=["topics"], create_missing=True, dry_run=False)

    report = _report(root)
    assert any(finding.code == "schemas.invalid" for finding in report.findings), report.findings
