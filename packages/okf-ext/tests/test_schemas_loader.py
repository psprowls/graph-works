from __future__ import annotations

import json
import subprocess
import sys

import pytest
from ext_helpers import write
from okf_ext.schemas import (
    DEFAULT_IGNORE,
    DEFAULT_SCHEMA_DIRNAME,
    AboutMandate,
    SchemaError,
    declared_about,
    declared_directories,
    declared_members,
    load_schemas,
)
from okf_ext.schemas.loader import build_registry

BASE = """
$defs:
  actor:
    type: object
    required: [name]
    properties:
      name: {type: string}
    additionalProperties: false
"""

METRIC = """
type: object
required: [type, title, owner]
properties:
  type: {const: Metric}
  title: {type: string}
  owner: {$ref: "_base.schema.yaml#/$defs/actor"}
  tags:
    type: array
    items: {type: string}
"""


def write_set(root, **files):
    root.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        write(root / name.replace("__", "."), body)
    return root


def standard_set(tmp_path):
    root = tmp_path / "schema"
    write_set(
        root,
        **{
            "_base__schema__yaml": BASE,
            "metric__schema__yaml": METRIC,
            "reference__schema__json": json.dumps(
                {
                    "type": "object",
                    "required": ["type"],
                    "properties": {"type": {"const": "Reference"}},
                }
            ),
            "note__schema__yml": "type: object\n",
        },
    )
    return load_schemas(root)


def test_types_come_from_filenames_across_all_three_suffixes(tmp_path):
    assert standard_set(tmp_path).types == ("metric", "note", "reference")


def test_sources_name_the_file_that_says_so(tmp_path):
    schema_set = standard_set(tmp_path)
    assert schema_set.sources["metric"] == "metric.schema.yaml"
    assert schema_set.sources["reference"] == "reference.schema.json"
    assert schema_set.sources["note"] == "note.schema.yml"


def test_an_underscore_file_is_a_ref_target_not_a_type(tmp_path):
    """okf-schema v0.1 registered `_base` under the type key `_base`, which
    appears unintended: no concept can carry `type: _base`."""
    schema_set = standard_set(tmp_path)
    assert "_base" not in schema_set.types
    assert "_base.schema.yaml" in schema_set.documents


def test_a_ref_with_a_fragment_resolves(tmp_path):
    """okf-schema v0.1's hand-rolled inliner built `dir / ref` with the fragment
    still attached, failed to open it, and silently ignored the result. The
    registry handles fragments, cycles and sibling keys."""
    from jsonschema.validators import validator_for

    schema_set = standard_set(tmp_path)
    registry = build_registry(schema_set.documents)
    schema = dict(schema_set.schemas["metric"])
    validator = validator_for(schema)(schema, registry=registry)
    messages = [e.message for e in validator.iter_errors({"type": "Metric", "title": "T", "owner": {"name": 5}})]
    assert any("is not of type 'string'" in m for m in messages)


def test_the_root_is_carried(tmp_path):
    schema_set = standard_set(tmp_path)
    assert schema_set.root == tmp_path / "schema"


def test_the_mapping_fields_survive_json_dumps(tmp_path):
    schema_set = standard_set(tmp_path)
    assert json.loads(json.dumps(dict(schema_set.schemas)))["metric"]["type"] == "object"


def test_a_declared_draft_wins_over_the_default(tmp_path):
    """`validator_for` picks per schema, so a file that declares draft-07 is
    validated as draft-07 rather than as the 2020-12 default."""
    from jsonschema.validators import Draft7Validator, validator_for

    root = write_set(
        tmp_path / "schema",
        **{"old__schema__yaml": ('$schema: "http://json-schema.org/draft-07/schema#"\ntype: object\n')},
    )
    schema_set = load_schemas(root)
    assert validator_for(dict(schema_set.schemas["old"])) is Draft7Validator


def test_a_str_path_is_accepted(tmp_path):
    root = write_set(tmp_path / "schema", **{"metric__schema__yaml": METRIC, "_base__schema__yaml": BASE})
    assert load_schemas(str(root)).types == ("metric",)


def test_non_schema_files_are_ignored(tmp_path):
    root = write_set(
        tmp_path / "schema",
        **{"metric__schema__yaml": METRIC, "_base__schema__yaml": BASE, "README__md": "# notes\n"},
    )
    assert load_schemas(root).types == ("metric",)
    assert "README.md" not in load_schemas(root).documents


def test_a_bare_suffix_filename_claims_no_type(tmp_path):
    root = write_set(
        tmp_path / "schema",
        **{
            "__schema__yaml": "type: object\n",
            "metric__schema__yaml": METRIC,
            "_base__schema__yaml": BASE,
        },
    )
    assert load_schemas(root).types == ("metric",)


def test_a_subdirectory_is_not_walked(tmp_path):
    root = write_set(tmp_path / "schema", **{"metric__schema__yaml": METRIC, "_base__schema__yaml": BASE})
    nested = root / "nested.schema.yaml"
    nested.mkdir()
    assert load_schemas(root).types == ("metric",)


# --- Every SchemaError branch, and the one OSError -------------------------


def test_a_missing_directory_raises_oserror(tmp_path):
    """A missing path is a different caller error and is not dressed up as a
    format one -- the rule `load_vocabulary` already follows."""
    with pytest.raises(OSError):
        load_schemas(tmp_path / "absent")


def test_a_directory_with_no_schemas_raises(tmp_path):
    root = write_set(tmp_path / "schema", **{"README__md": "# nothing here\n"})
    with pytest.raises(SchemaError, match="no schema files found"):
        load_schemas(root)


def test_non_utf8_bytes_raise(tmp_path):
    root = tmp_path / "schema"
    root.mkdir()
    (root / "metric.schema.yaml").write_bytes(b"type: object\ntitle: \xff\xfe\n")
    with pytest.raises(SchemaError, match="not valid UTF-8"):
        load_schemas(root)


def test_bad_yaml_raises(tmp_path):
    root = write_set(tmp_path / "schema", **{"metric__schema__yaml": "type: [unclosed\n"})
    with pytest.raises(SchemaError, match="not valid YAML"):
        load_schemas(root)


def test_bad_json_raises(tmp_path):
    root = write_set(tmp_path / "schema", **{"metric__schema__json": "{not json}"})
    with pytest.raises(SchemaError, match="not valid JSON"):
        load_schemas(root)


def test_a_schema_that_is_not_a_mapping_raises(tmp_path):
    root = write_set(tmp_path / "schema", **{"metric__schema__yaml": "- a\n- b\n"})
    with pytest.raises(SchemaError, match="must be a mapping"):
        load_schemas(root)


def test_a_malformed_schema_raises_at_load_naming_the_file(tmp_path):
    """okf-schema v0.1 built its validator inside the validation loop, so one
    broken schema produced one confusing complaint against every concept using
    it. Caller configuration is always an exception."""
    root = write_set(tmp_path / "schema", **{"metric__schema__yaml": "type: 123\n"})
    with pytest.raises(SchemaError, match=r"metric\.schema\.yaml: not a valid JSONSchema"):
        load_schemas(root)


def test_two_files_claiming_one_type_raise(tmp_path):
    root = write_set(
        tmp_path / "schema",
        **{"metric__schema__yaml": "type: object\n", "metric__schema__json": '{"type": "object"}'},
    )
    with pytest.raises(SchemaError, match="already claimed"):
        load_schemas(root)


def test_schema_error_is_a_value_error():
    assert issubclass(SchemaError, ValueError)


# --- The convention constants and the extra --------------------------------


def test_the_convention_constants_are_what_the_spec_names():
    assert DEFAULT_SCHEMA_DIRNAME == "schema"
    assert DEFAULT_IGNORE == ("schema/*", "*/schema/*")


def test_a_missing_jsonschema_names_the_extra():
    """The policy the README states: a capability-specific dependency ships as
    an extra, and the capability's `__init__` names it. Asked of a subprocess
    with `jsonschema` blocked, because this one is installed here."""
    # A PEP 302 finder implementing only `find_module`/`load_module` is never
    # consulted by Python 3.12's import system -- only `find_spec` is. Blocking
    # `jsonschema` for real needs the modern `importlib.abc.MetaPathFinder`
    # protocol.
    code = (
        "import sys\n"
        "import importlib.abc\n"
        "import importlib.util\n"
        "class Loader(importlib.abc.Loader):\n"
        "    def create_module(self, spec):\n"
        "        raise ImportError(spec.name)\n"
        "    def exec_module(self, module):\n"
        "        pass\n"
        "class Finder(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path, target=None):\n"
        "        if name == 'jsonschema' or name.startswith('jsonschema.'):\n"
        "            return importlib.util.spec_from_loader(name, Loader())\n"
        "        return None\n"
        "sys.meta_path.insert(0, Finder())\n"
        "for mod in [m for m in sys.modules if m.startswith('jsonschema')]:\n"
        "    del sys.modules[mod]\n"
        "try:\n"
        "    import okf_ext.schemas\n"
        "except ImportError as exc:\n"
        "    print('okf-ext[schemas]' in str(exc))\n"
        "else:\n"
        "    print('NO RAISE')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "True"


# --- declared_directories ---------------------------------------------------


def _annotated_set(tmp_path, **annotations):
    """A schema directory whose per-type `x-okf-directory` the caller chooses.

    A value of `...` means the annotation is omitted entirely, which is a
    different fact from a blank one and has its own test below.
    """
    root = tmp_path / "schema"
    root.mkdir()
    for type_name, directory in annotations.items():
        document = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"type": {"const": type_name}},
        }
        if directory is not ...:
            document["x-okf-directory"] = directory
        write(root / f"{type_name}.schema.json", json.dumps(document))
    return load_schemas(root)


def test_declared_directories_returns_every_annotated_type(tmp_path):
    schema_set = _annotated_set(tmp_path, Widget="widgets/", Crate="crates/")
    assert declared_directories(schema_set) == {"Widget": "widgets/", "Crate": "crates/"}


def test_a_type_with_no_annotation_is_omitted(tmp_path):
    """`x-okf-directory` is an extension annotation, not a JSONSchema keyword:
    a hand-authored schema without it declares no lane."""
    schema_set = _annotated_set(tmp_path, Widget="widgets/", Crate=...)
    assert declared_directories(schema_set) == {"Widget": "widgets/"}


def test_a_blank_annotation_is_omitted(tmp_path):
    assert declared_directories(_annotated_set(tmp_path, Widget="   ")) == {}


def test_a_non_string_annotation_is_omitted(tmp_path):
    """Loading never raises for an annotation it does not understand -- the
    value is a caller's declaration, and an unusable one declares nothing."""
    assert declared_directories(_annotated_set(tmp_path, Widget=["widgets/"])) == {}


def test_the_result_is_a_plain_mutable_dict(tmp_path):
    """The caller passes it straight to `placement_rule`, which takes a
    `Mapping`; returning a proxy would make merging in extra entries awkward
    for no gain."""
    found = declared_directories(_annotated_set(tmp_path, Widget="widgets/"))
    found["Extra"] = "extra/"
    assert found == {"Widget": "widgets/", "Extra": "extra/"}


# --- declared_members -------------------------------------------------------


def _member_set(tmp_path, properties, *, name="Widget", extra=None):
    """One schema whose top-level `properties` the caller chooses verbatim."""
    root = tmp_path / "schema"
    root.mkdir(exist_ok=True)
    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"type": {"const": name}, **properties},
    }
    document.update(extra or {})
    write(root / f"{name}.schema.json", json.dumps(document))
    return load_schemas(root)


def test_declared_members_collects_literal_true_top_level_annotations_sorted(tmp_path):
    schema_set = _member_set(
        tmp_path,
        {
            "zeta": {"type": "string", "x-okf-member": True},
            "alpha": {"type": "string", "x-okf-member": True},
            "plain": {"type": "string"},
        },
    )
    assert declared_members(schema_set) == {"Widget": ("alpha", "zeta")}


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_only_the_literal_json_true_counts(tmp_path, value):
    schema_set = _member_set(tmp_path, {"path": {"type": "string", "x-okf-member": value}})
    assert declared_members(schema_set) == {}


def test_a_non_mapping_sub_schema_is_ignored_not_reported(tmp_path):
    """`true` is a valid JSONSchema sub-schema; it carries no annotation."""
    schema_set = _member_set(tmp_path, {"anything": True})
    assert declared_members(schema_set) == {}


def test_nested_and_array_item_properties_are_not_scanned(tmp_path):
    """A stated boundary (spec §3.1): only top-level `properties`."""
    schema_set = _member_set(
        tmp_path,
        {
            "owner": {"type": "object", "properties": {"path": {"type": "string", "x-okf-member": True}}},
            "paths": {"type": "array", "items": {"type": "string", "x-okf-member": True}},
        },
    )
    assert declared_members(schema_set) == {}


def test_a_ref_reached_property_is_not_scanned(tmp_path):
    root = tmp_path / "schema"
    root.mkdir()
    write(
        root / "_base.schema.json",
        json.dumps({"properties": {"path": {"type": "string", "x-okf-member": True}}}),
    )
    write(
        root / "Widget.schema.json",
        json.dumps({"$ref": "_base.schema.json", "properties": {"type": {"const": "Widget"}}}),
    )
    assert declared_members(load_schemas(root)) == {}


def test_a_type_with_no_member_property_is_omitted(tmp_path):
    root = tmp_path / "schema"
    root.mkdir()
    write(root / "A.schema.json", json.dumps({"properties": {"p": {"x-okf-member": True}}}))
    write(root / "B.schema.json", json.dumps({"properties": {"p": {"type": "string"}}}))
    assert declared_members(load_schemas(root)) == {"A": ("p",)}


# --- declared_about ---------------------------------------------------------


def _about_set(tmp_path, annotation, *, properties=None, name="Widget"):
    """One schema whose top-level `x-okf-about` the caller chooses; `...` omits it."""
    root = tmp_path / "schema"
    root.mkdir(exist_ok=True)
    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"type": {"const": name}, **(properties or {})},
    }
    if annotation is not ...:
        document["x-okf-about"] = annotation
    write(root / f"{name}.schema.json", json.dumps(document))
    return load_schemas(root)


def test_declared_about_reads_an_entries_annotation(tmp_path):
    schema_set = _about_set(tmp_path, {"entries": "claims"}, properties={"claims": {"type": "array"}})
    assert declared_about(schema_set) == {"Widget": AboutMandate(entries="claims")}


def test_an_empty_annotation_mandates_about_alone(tmp_path):
    assert declared_about(_about_set(tmp_path, {})) == {"Widget": AboutMandate(entries=None)}


def test_no_annotation_is_no_mandate(tmp_path):
    assert declared_about(_about_set(tmp_path, ...)) == {}


@pytest.mark.parametrize("annotation", [True, "claims", ["claims"], None])
def test_a_non_object_annotation_is_ignored(tmp_path, annotation):
    assert declared_about(_about_set(tmp_path, annotation)) == {}


@pytest.mark.parametrize("entries", [1, ["claims"], True, ""])
def test_a_non_string_or_blank_entries_is_ignored(tmp_path, entries):
    schema_set = _about_set(tmp_path, {"entries": entries}, properties={"claims": {"type": "array"}})
    assert declared_about(schema_set) == {}


def test_entries_naming_an_undeclared_property_is_ignored(tmp_path):
    assert declared_about(_about_set(tmp_path, {"entries": "claims"})) == {}


def test_entries_reached_only_through_a_ref_is_ignored(tmp_path):
    """Only top-level `properties` count, the boundary `declared_members` states."""
    root = tmp_path / "schema"
    root.mkdir()
    write(root / "_base.schema.json", json.dumps({"properties": {"claims": {"type": "array"}}}))
    write(
        root / "Widget.schema.json",
        json.dumps(
            {
                "$ref": "_base.schema.json",
                "properties": {"type": {"const": "Widget"}},
                "x-okf-about": {"entries": "claims"},
            }
        ),
    )
    assert declared_about(load_schemas(root)) == {}


def test_about_mandate_is_frozen():
    with pytest.raises(AttributeError):
        AboutMandate(entries="claims").entries = "decisions"  # type: ignore[misc]
