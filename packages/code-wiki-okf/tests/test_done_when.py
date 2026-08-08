from datetime import date
from pathlib import Path

from code_wiki_okf.init import init_bundle
from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import load_sections, section_rule
from okf_ext.tags import load_vocabulary, vocabulary_rule
from okf_io import load_bundle, validate

_TODAY = date(2026, 1, 1)


def test_freshly_initialized_bundle_validates_clean(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    init_bundle(root, today=_TODAY, dry_run=False)

    schema_set = load_schemas(root / "_schema")
    section_set = load_sections(root / "_sections")
    vocabulary = load_vocabulary(root / "_tags.yaml")

    bundle = load_bundle(root)
    report = validate(
        bundle,
        today=_TODAY,
        extra_rules=[
            schema_rule(schema_set),
            section_rule(section_set),
            vocabulary_rule(vocabulary),
        ],
    )
    assert report.ok, report.errors
