from __future__ import annotations

import json
import subprocess
import sys

import pytest
from okf_ext.schemas import DEFAULT_IGNORE, DEFAULT_SCHEMA_DIRNAME, SchemaError, load_schemas
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
        (root / name.replace("__", ".")).write_text(body, encoding="utf-8")
    return root


def standard_set(tmp_path):
    root = tmp_path / "_schema"
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
    messages = [
        e.message
        for e in validator.iter_errors({"type": "Metric", "title": "T", "owner": {"name": 5}})
    ]
    assert any("is not of type 'string'" in m for m in messages)


def test_the_root_is_carried(tmp_path):
    schema_set = standard_set(tmp_path)
    assert schema_set.root == tmp_path / "_schema"


def test_the_mapping_fields_survive_json_dumps(tmp_path):
    schema_set = standard_set(tmp_path)
    assert json.loads(json.dumps(dict(schema_set.schemas)))["metric"]["type"] == "object"


def test_a_declared_draft_wins_over_the_default(tmp_path):
    """`validator_for` picks per schema, so a file that declares draft-07 is
    validated as draft-07 rather than as the 2020-12 default."""
    from jsonschema.validators import Draft7Validator, validator_for

    root = write_set(
        tmp_path / "_schema",
        **{
            "old__schema__yaml": (
                '$schema: "http://json-schema.org/draft-07/schema#"\ntype: object\n'
            )
        },
    )
    schema_set = load_schemas(root)
    assert validator_for(dict(schema_set.schemas["old"])) is Draft7Validator


def test_a_str_path_is_accepted(tmp_path):
    root = write_set(
        tmp_path / "_schema", **{"metric__schema__yaml": METRIC, "_base__schema__yaml": BASE}
    )
    assert load_schemas(str(root)).types == ("metric",)


def test_non_schema_files_are_ignored(tmp_path):
    root = write_set(
        tmp_path / "_schema",
        **{"metric__schema__yaml": METRIC, "_base__schema__yaml": BASE, "README__md": "# notes\n"},
    )
    assert load_schemas(root).types == ("metric",)
    assert "README.md" not in load_schemas(root).documents


def test_a_bare_suffix_filename_claims_no_type(tmp_path):
    root = write_set(
        tmp_path / "_schema",
        **{
            "__schema__yaml": "type: object\n",
            "metric__schema__yaml": METRIC,
            "_base__schema__yaml": BASE,
        },
    )
    assert load_schemas(root).types == ("metric",)


def test_a_subdirectory_is_not_walked(tmp_path):
    root = write_set(
        tmp_path / "_schema", **{"metric__schema__yaml": METRIC, "_base__schema__yaml": BASE}
    )
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
    root = write_set(tmp_path / "_schema", **{"README__md": "# nothing here\n"})
    with pytest.raises(SchemaError, match="no schema files found"):
        load_schemas(root)


def test_non_utf8_bytes_raise(tmp_path):
    root = tmp_path / "_schema"
    root.mkdir()
    (root / "metric.schema.yaml").write_bytes(b"type: object\ntitle: \xff\xfe\n")
    with pytest.raises(SchemaError, match="not valid UTF-8"):
        load_schemas(root)


def test_bad_yaml_raises(tmp_path):
    root = write_set(tmp_path / "_schema", **{"metric__schema__yaml": "type: [unclosed\n"})
    with pytest.raises(SchemaError, match="not valid YAML"):
        load_schemas(root)


def test_bad_json_raises(tmp_path):
    root = write_set(tmp_path / "_schema", **{"metric__schema__json": "{not json}"})
    with pytest.raises(SchemaError, match="not valid JSON"):
        load_schemas(root)


def test_a_schema_that_is_not_a_mapping_raises(tmp_path):
    root = write_set(tmp_path / "_schema", **{"metric__schema__yaml": "- a\n- b\n"})
    with pytest.raises(SchemaError, match="must be a mapping"):
        load_schemas(root)


def test_a_malformed_schema_raises_at_load_naming_the_file(tmp_path):
    """okf-schema v0.1 built its validator inside the validation loop, so one
    broken schema produced one confusing complaint against every concept using
    it. Caller configuration is always an exception."""
    root = write_set(tmp_path / "_schema", **{"metric__schema__yaml": "type: 123\n"})
    with pytest.raises(SchemaError, match=r"metric\.schema\.yaml: not a valid JSONSchema"):
        load_schemas(root)


def test_two_files_claiming_one_type_raise(tmp_path):
    root = write_set(
        tmp_path / "_schema",
        **{"metric__schema__yaml": "type: object\n", "metric__schema__json": '{"type": "object"}'},
    )
    with pytest.raises(SchemaError, match="already claimed"):
        load_schemas(root)


def test_schema_error_is_a_value_error():
    assert issubclass(SchemaError, ValueError)


# --- The convention constants and the extra --------------------------------


def test_the_convention_constants_are_what_the_spec_names():
    assert DEFAULT_SCHEMA_DIRNAME == "_schema"
    assert DEFAULT_IGNORE == ("_schema/*", "*/_schema/*")


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
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "True"
