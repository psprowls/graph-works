from __future__ import annotations

from ext_helpers import write
from okf_ext.schemas import frontmatter_errors, load_schemas

THING = """
type: object
required: [type, title]
properties:
  type: {const: Thing}
  title: {type: string}
"""


def test_frontmatter_errors(tmp_path) -> None:
    write(tmp_path / "Thing.schema.yaml", THING)
    schemas = load_schemas(tmp_path)
    assert frontmatter_errors(schemas, "Thing", {"type": "Thing", "title": "x"}) == ()
    assert frontmatter_errors(schemas, "Thing", {"type": "Thing"}) == ("'title' is a required property",)
    assert frontmatter_errors(schemas, "Thing", {"type": "Thing", "title": 3}) == (
        "at `title`: 3 is not of type 'string'",
    )
    assert frontmatter_errors(schemas, "Nope", {}) is None
