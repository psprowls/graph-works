from __future__ import annotations

from graph_works_cli.json_output import encode


def test_encode_preserves_the_cli_json_format() -> None:
    assert encode({"answer": 42, "nested": [True, None]}) == (
        '{\n  "answer": 42,\n  "nested": [\n    true,\n    null\n  ]\n}'
    )
