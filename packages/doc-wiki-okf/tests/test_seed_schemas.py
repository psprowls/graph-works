"""The four schemas load, ref, and annotate. Mirrors work-tracker-okf's own."""

import importlib.resources
from datetime import date
from pathlib import Path

import pytest
from okf_ext.schemas import declared_members, load_schemas, schema_rule
from okf_io import load_bundle
from okf_io import validate as okf_validate

#: The four the rubric classifies. `Source` is declared but is not one of them.
_DIATAXIS_TYPES = ("Explanation", "HowTo", "Reference", "Tutorial")

#: Every type this package ships a schema for, sorted.
_ALL_TYPES = ("Adr", "Explanation", "HowTo", "Reference", "Source", "Tutorial")

_LANES = {
    "Tutorial": "docs/tutorials/",
    "HowTo": "docs/how-tos/",
    "Reference": "docs/reference/",
    "Explanation": "docs/explanations/",
    "Source": "sources/",
    "Adr": "adrs/",
}


def _schema_set():
    assets = importlib.resources.files("doc_wiki_okf") / "assets" / "schema"
    return load_schemas(str(assets))


def test_seed_schemas_load_as_exactly_the_six_types() -> None:
    assert tuple(sorted(_schema_set().schemas)) == _ALL_TYPES


def test_the_base_is_a_ref_target_not_a_type() -> None:
    schema_set = _schema_set()
    assert "_base-diataxis" not in schema_set.schemas
    assert "_base-diataxis.schema.json" in schema_set.documents


def test_each_diataxis_wrapper_pins_the_const_to_its_own_stem() -> None:
    schema_set = _schema_set()
    for type_name in _DIATAXIS_TYPES:
        assert schema_set.schemas[type_name]["properties"]["type"] == {"const": type_name}
        assert schema_set.schemas[type_name]["$ref"] == "_base-diataxis.schema.json"


def test_each_type_declares_its_own_lane() -> None:
    """Spec §2.1: the four lanes differ, so the annotation is per type."""
    schema_set = _schema_set()
    for type_name, lane in _LANES.items():
        assert schema_set.schemas[type_name]["x-okf-directory"] == lane


def test_the_base_declares_no_directory() -> None:
    """A base-level annotation would be wrong for three of four types."""
    assert "x-okf-directory" not in _schema_set().documents["_base-diataxis.schema.json"]


def _findings(
    tmp_path: Path,
    frontmatter: str,
    code: str = "schemas.invalid",
    lane: str = "docs/tutorials",
    heading: str = "Steps",
) -> list[str]:
    root = tmp_path / "bundle"
    (root / lane).mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8")
    (root / lane / "page.md").write_text(f"---\n{frontmatter}---\n\n## {heading}\n", encoding="utf-8")
    report = okf_validate(
        load_bundle(root),
        today=date(2026, 8, 12),
        extra_rules=[schema_rule(_schema_set())],
    )
    return [finding.message for finding in report.by_code(code)]


_COMPLETE = "type: Tutorial\ntitle: A tutorial\ndescription: A tutorial.\n"


def test_the_base_required_keys_reach_the_wrapper_through_the_ref(tmp_path: Path) -> None:
    messages = _findings(tmp_path, "type: Tutorial\ntitle: A tutorial\n")
    assert any("'description' is a required property" in message for message in messages)


def test_the_three_added_keys_are_all_optional(tmp_path: Path) -> None:
    """Spec §2.3: no page that exists today can fail on one."""
    assert _findings(tmp_path, _COMPLETE) == []


def test_the_added_keys_are_typed_when_present(tmp_path: Path) -> None:
    messages = _findings(tmp_path, _COMPLETE + "prerequisites: not-a-list\n")
    assert any("is not of type 'array'" in message for message in messages)


def test_a_mismatched_const_is_a_finding(tmp_path: Path) -> None:
    messages = _findings(
        tmp_path, _COMPLETE.replace("type: Tutorial", "type: Tutorail"), code="schemas.no-schema-for-type"
    )
    assert len(messages) == 1
    assert "No schema for type `Tutorail`" in messages[0]


def test_source_is_standalone_and_pins_its_own_const() -> None:
    """S-B: `Source` is not a Diátaxis type, and inheriting the base's `status`
    enum and property set would assert a kinship that does not exist."""
    schema = _schema_set().schemas["Source"]
    assert "$ref" not in schema
    assert schema["properties"]["type"] == {"const": "Source"}
    assert schema["type"] == "object"


def test_source_requires_the_four_keys() -> None:
    """S-J, minus `source_kind`: K-C drops it from `required` because nothing
    behavioural depends on the value, so forcing a classification on material
    that genuinely has no genre buys nothing."""
    assert sorted(_schema_set().schemas["Source"]["required"]) == [
        "description",
        "source_path",
        "title",
        "type",
    ]


def test_source_kind_is_a_closed_vocabulary_of_seven() -> None:
    """K-B: nine values cut to seven -- `example` and `note` dropped, `pr`
    widened to `code-review`. This is the one authored copy; a follow-up
    deletes the duplicated `SOURCE_TYPES` tuple and the duplicated prose copy
    in the ingestor prompt so that every consumer derives from this enum at
    run time."""
    enum = _schema_set().schemas["Source"]["properties"]["source_kind"]["enum"]
    assert enum == ["spec", "article", "ticket", "skill", "doc", "transcript", "code-review"]


def test_source_type_is_gone() -> None:
    """K-A: the old key collided with OKF's own `type: Source` three lines up."""
    assert "source_type" not in _schema_set().schemas["Source"]["properties"]


def test_source_rejects_the_keys_it_does_not_declare() -> None:
    """The graph-wiki-era vault keys (`last_sync_commit`, `source_url`, `summary`,
    `source_type`) are retired, so `Source` is strict: an undeclared key is a finding."""
    assert _schema_set().schemas["Source"]["additionalProperties"] is False


def test_source_path_is_the_one_declared_member_property() -> None:
    """C1: `source_path` must name a bundle member (the ingest contract's
    `sources/references/` copy). No other shipped property is annotated."""
    assert declared_members(_schema_set()) == {"Source": ("source_path",)}


_SOURCE = "type: Source\ntitle: S\ndescription: A source.\nsource_path: {path}\n"


def _source_findings(tmp_path: Path, source_path: str, *, with_target: bool) -> list[str]:
    root = tmp_path / "bundle"
    (root / "sources" / "references").mkdir(parents=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8", newline="")
    if with_target:
        (root / "sources" / "references" / "2026-08-x.md").write_text("# X\n", encoding="utf-8", newline="")
    (root / "sources" / "2026-08-x.md").write_text(
        f"---\n{_SOURCE.format(path=source_path)}---\n\n# S\n", encoding="utf-8", newline=""
    )
    report = okf_validate(load_bundle(root), today=date(2026, 8, 12), extra_rules=[schema_rule(_schema_set())])
    return [finding.path for finding in report.by_code("schemas.unresolved-member")]


def test_a_dangling_source_path_is_reported(tmp_path: Path) -> None:
    assert _source_findings(tmp_path, "sources/references/2026-08-x.md", with_target=False) == ["sources/2026-08-x.md"]


def test_a_copied_source_path_is_quiet(tmp_path: Path) -> None:
    assert _source_findings(tmp_path, "sources/references/2026-08-x.md", with_target=True) == []


_COMPLETE_SOURCE = (
    "type: Source\n"
    "title: A source\n"
    "description: What it is.\n"
    "source_kind: spec\n"
    "source_path: sources/references/2026-08-a-source.md\n"
)


def test_a_complete_source_page_produces_no_finding(tmp_path: Path) -> None:
    assert _findings(tmp_path, _COMPLETE_SOURCE, lane="sources", heading="TL;DR") == []


def test_a_source_page_missing_source_path_is_a_finding(tmp_path: Path) -> None:
    """S-J's cost, measured: exactly one of 237 live pages fails this way."""
    without = _COMPLETE_SOURCE.replace("source_path: sources/references/2026-08-a-source.md\n", "")
    messages = _findings(tmp_path, without, lane="sources", heading="TL;DR")
    assert any("'source_path' is a required property" in message for message in messages)


def test_an_unknown_source_kind_is_a_finding(tmp_path: Path) -> None:
    bad_source = _COMPLETE_SOURCE.replace("source_kind: spec", "source_kind: blog")
    messages = _findings(tmp_path, bad_source, lane="sources", heading="TL;DR")
    assert any("'blog' is not one of" in message for message in messages)


def test_an_absent_source_kind_is_no_finding(tmp_path: Path) -> None:
    """K-C: optional means an unclassified page is valid, not a warning."""
    without = _COMPLETE_SOURCE.replace("source_kind: spec\n", "")
    assert _findings(tmp_path, without, lane="sources", heading="TL;DR") == []


# --- the curated-page claims contract (design §3) ---------------------------

_PKG = "pkg:acme/demo/widgets"

_CURATED = {
    "Adr": (
        "adrs",
        "type: Adr\ntitle: A\ndescription: d\ndecision_date: 2026-01-01\n"
        f"about: [{_PKG}]\n"
        "decisions:\n  - id: D1\n    claim: Widgets are frozen.\n"
        "    constrains: [src/widgets.py]\n    phase: [plan, execute]\n",
    ),
    "Explanation": (
        "docs/explanations",
        f"type: Explanation\ntitle: E\ndescription: d\nabout: [{_PKG}]\n"
        "claims:\n  - id: C1\n    claim: Widgets are frozen.\n    about: [file:acme/demo/src/widgets.py]\n",
    ),
    "Reference": (
        "docs/reference",
        f"type: Reference\ntitle: R\ndescription: d\nabout: [{_PKG}]\n"
        "claims:\n  - id: C1\n    claim: The CLI has three verbs.\n",
    ),
    "HowTo": (
        "docs/how-tos",
        f"type: HowTo\ntitle: H\ndescription: d\nabout: [{_PKG}]\nclaims:\n  - id: C1\n    claim: Run it twice.\n",
    ),
}


@pytest.mark.parametrize("type_name", sorted(_CURATED))
def test_a_well_formed_curated_page_validates_clean(tmp_path: Path, type_name: str) -> None:
    lane, frontmatter = _CURATED[type_name]
    assert _findings(tmp_path, frontmatter, lane=lane) == []


@pytest.mark.parametrize("scheme", ["repo", "pkg", "app", "agent_plugin", "test_suite", "file", "dependency"])
def test_every_scanner_scheme_is_accepted(tmp_path: Path, scheme: str) -> None:
    lane, frontmatter = _CURATED["Reference"]
    assert _findings(tmp_path, frontmatter.replace(_PKG, f"{scheme}:acme/demo/x"), lane=lane) == []


def test_a_dependency_uri_carries_org_and_repo(tmp_path: Path) -> None:
    lane, frontmatter = _CURATED["Reference"]
    assert _findings(tmp_path, frontmatter.replace(_PKG, "dependency:acme/demo/pypi/httpx"), lane=lane) == []


def test_about_on_a_tutorial_is_accepted_but_claims_is_not(tmp_path: Path) -> None:
    """`about` lives on the base, so the `$ref` evaluates it; `claims` does not."""
    assert _findings(tmp_path, _COMPLETE + f"about: [{_PKG}]\n") == []
    messages = _findings(tmp_path, _COMPLETE + "claims:\n  - id: C1\n    claim: x\n")
    assert any("'claims' was unexpected" in message for message in messages)


def test_a_page_with_no_about_is_not_a_schema_finding(tmp_path: Path) -> None:
    """D-001: presence is `about_rule`'s job, never JSON-schema `required`."""
    for type_name, (lane, frontmatter) in _CURATED.items():
        stripped = "".join(line + "\n" for line in frontmatter.splitlines() if not line.startswith("about:"))
        assert _findings(tmp_path / type_name, stripped, lane=lane) == [], type_name


def _broken(type_name: str, old: str, new: str) -> tuple[str, str]:
    lane, frontmatter = _CURATED[type_name]
    assert old in frontmatter, (type_name, old)
    return lane, frontmatter.replace(old, new)


_INVALID = {
    "applies_to on a Reference": _broken("Reference", "about:", "applies_to: [plugin-fork-io]\nabout:"),
    "claims on an Adr": _broken("Adr", "decisions:", "claims:"),
    "decisions on an Explanation": _broken("Explanation", "claims:", "decisions:"),
    "a bad scheme": _broken("Reference", _PKG, "module:acme/demo/widgets"),
    "a scheme with no payload": _broken("Reference", _PKG, '"pkg:"'),
    "an empty about": _broken("Reference", f"about: [{_PKG}]", "about: []"),
    "a repeated about uri": _broken("Reference", f"about: [{_PKG}]", f"about: [{_PKG}, {_PKG}]"),
    "an Adr entry missing claim": _broken("Adr", "    claim: Widgets are frozen.\n", ""),
    "an Explanation entry missing claim": _broken("Explanation", "    claim: Widgets are frozen.\n", ""),
    "a Reference entry missing claim": _broken("Reference", "    claim: The CLI has three verbs.\n", ""),
    "a HowTo entry missing claim": _broken("HowTo", "    claim: Run it twice.\n", ""),
    "an entry missing id": _broken("Explanation", "  - id: C1\n    claim:", "  - claim:"),
    "an unknown entry key": _broken("Reference", "    claim: The CLI", "    status: stable\n    claim: The CLI"),
    "an Adr id with a C": _broken("Adr", "id: D1", "id: C1"),
    "an Adr id D0": _broken("Adr", "id: D1", "id: D0"),
    "an Explanation id with a D": _broken("Explanation", "id: C1", "id: D1"),
    "a constrains path with ..": _broken("Adr", "[src/widgets.py]", "[src/../../etc]"),
    "a constrains path with a leading /": _broken("Adr", "[src/widgets.py]", "[/etc/passwd]"),
    "an unknown phase": _broken("Adr", "[plan, execute]", "[done]"),
    "an empty decisions list": _broken(
        "Adr",
        "decisions:\n  - id: D1\n    claim: Widgets are frozen.\n"
        "    constrains: [src/widgets.py]\n    phase: [plan, execute]\n",
        "decisions: []\n",
    ),
}


@pytest.mark.parametrize("case", sorted(_INVALID))
def test_a_contract_violation_is_schemas_invalid(tmp_path: Path, case: str) -> None:
    lane, frontmatter = _INVALID[case]
    assert _findings(tmp_path, frontmatter, lane=lane) != [], case


def test_the_base_declares_about_and_the_entry_shape() -> None:
    base = _schema_set().documents["_base-diataxis.schema.json"]
    assert base["properties"]["about"] == {"$ref": "#/$defs/about"}
    assert base["$defs"]["about"]["items"]["pattern"] == (
        r"^(repo|pkg|app|agent_plugin|test_suite|file|dependency):\S.*$"
    )
    assert base["$defs"]["entry"]["required"] == ["id", "claim"]
    assert base["$defs"]["entry"]["additionalProperties"] is False


def test_x_okf_about_sits_on_exactly_the_four_mandated_types() -> None:
    schema_set = _schema_set()
    declared = {name: schema["x-okf-about"] for name, schema in schema_set.schemas.items() if "x-okf-about" in schema}
    assert declared == {
        "Adr": {"entries": "decisions"},
        "Explanation": {"entries": "claims"},
        "Reference": {},
        "HowTo": {},
    }
    assert "x-okf-about" not in schema_set.documents["_base-diataxis.schema.json"]


def test_reference_no_longer_declares_applies_to() -> None:
    assert "applies_to" not in _schema_set().schemas["Reference"]["properties"]


# --- the drain ledger (S-source-drain) --------------------------------------

_SOURCE_HEAD = "type: Source\ntitle: S\ndescription: d\nsource_path: sources/references/s.md\n"


@pytest.mark.parametrize(
    "ledger",
    [
        "drain:\n  - claim: 1\n    landed: [/adrs/a.md#D1]\n",
        "drain:\n  - claim: 2\n    dropped: history\n",
        "drain:\n  - claim: 1\n    landed: [/adrs/a.md#D1, /docs/explanations/e.md#C2]\n"
        "  - claim: 2\n    dropped: evidence\n",
    ],
)
def test_the_source_seed_accepts_both_ledger_shapes(tmp_path: Path, ledger: str) -> None:
    assert _findings(tmp_path, _SOURCE_HEAD + ledger, lane="sources", heading="TL;DR") == []


@pytest.mark.parametrize(
    "ledger",
    [
        "drain:\n  - claim: 1\n    landed: [/adrs/a.md#D1]\n    dropped: history\n",  # both
        "drain:\n  - claim: 1\n",  # neither
        "drain:\n  - claim: 1\n    dropped: boring\n",  # enum
        "drain:\n  - claim: 0\n    dropped: history\n",  # minimum
        "drain:\n  - claim: 1\n    landed: []\n",  # empty landed
        "drain:\n  - claim: 1\n    dropped: history\n    note: x\n",  # extra key
    ],
)
def test_the_source_seed_rejects_a_bad_ledger_entry(tmp_path: Path, ledger: str) -> None:
    assert _findings(tmp_path, _SOURCE_HEAD + ledger, lane="sources", heading="TL;DR")
