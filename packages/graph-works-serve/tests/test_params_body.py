from __future__ import annotations

import json

import pytest
from graph_works_serve.params import Param, ParamError, parse_body

FIELDS = (
    Param("path", "str", required=True, location="body"),
    Param("return", "bool", default=False, location="body"),
    Param("released_at", "date", location="body"),
    Param("paths", "list[str]", location="body"),
)


def body(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


def test_defaults_fill_omitted_and_null_fields() -> None:
    assert parse_body(FIELDS, body({"path": "work/x", "paths": None})) == {
        "path": "work/x",
        "return": False,
        "released_at": None,
        "paths": None,
    }


def test_explicit_default_normalizes_to_the_same_value() -> None:
    assert parse_body(FIELDS, body({"path": "p"})) == parse_body(FIELDS, body({"path": "p", "return": False}))


def test_values_stay_json_native() -> None:
    parsed = parse_body(FIELDS, body({"path": "p", "released_at": "2026-09-18", "paths": ["a", "b"]}))
    assert parsed["released_at"] == "2026-09-18"
    assert parsed["paths"] == ["a", "b"]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"not json", "not JSON"),
        (b"\xff", "not JSON"),
        (body(["path"]), "JSON object"),
        (body({}), "missing required field 'path'"),
        (body({"path": "p", "bogus": 1}), "unknown field(s): bogus"),
        (body({"path": 1}), "'path' must be str"),
        (body({"path": "p", "return": "yes"}), "'return' must be bool"),
        (body({"path": "p", "released_at": "20260918"}), "'released_at' must be date"),
        (body({"path": "p", "released_at": "2026-02-30"}), "'released_at' must be date"),
        (body({"path": "p", "paths": ["a", 1]}), "'paths' must be list[str]"),
        (body({"path": "p", "paths": "a"}), "'paths' must be list[str]"),
    ],
)
def test_malformed_bodies_raise(raw: bytes, message: str) -> None:
    with pytest.raises(ParamError, match=message.replace("(", r"\(").replace(")", r"\)").replace("[", r"\[")):
        parse_body(FIELDS, raw)


def test_an_empty_body_is_not_an_object() -> None:
    with pytest.raises(ParamError, match="JSON object"):
        parse_body(FIELDS, b"")


def test_bool_is_not_accepted_as_str_nor_int_as_bool() -> None:
    with pytest.raises(ParamError):
        parse_body(FIELDS, body({"path": True}))
    with pytest.raises(ParamError):
        parse_body(FIELDS, body({"path": "p", "return": 1}))
