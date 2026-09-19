from __future__ import annotations

from datetime import date

import pytest
from graph_works_serve.params import Param, ParamError, parse

SPEC = (
    Param("path", "str", required=True),
    Param("descend", "bool", default=False),
    Param("last", "int", default=10, minimum=0),
    Param("since", "date"),
    Param("live", "csv"),
)


def test_every_type_parses() -> None:
    got = parse(
        SPEC,
        [
            ("path", "work/a"),
            ("descend", "true"),
            ("last", "3"),
            ("since", "2026-09-01"),
            ("live", "a, b,,"),
        ],
    )
    assert got == {
        "path": "work/a",
        "descend": True,
        "last": 3,
        "since": date(2026, 9, 1),
        "live": ("a", "b"),
    }


def test_absent_optionals_take_defaults_and_token_is_ignored() -> None:
    assert parse(SPEC, [("path", "p"), ("token", "t")]) == {
        "path": "p",
        "descend": False,
        "last": 10,
        "since": None,
        "live": (),
    }


@pytest.mark.parametrize(
    ("items", "fragment"),
    [
        ([], "path is required"),
        ([("path", "p"), ("nope", "1")], "unknown parameter nope"),
        ([("path", "p"), ("path", "q")], "path given more than once"),
        ([("path", "p"), ("descend", "yes")], "descend: expected true|false"),
        ([("path", "p"), ("last", "x")], "last: expected an integer"),
        ([("path", "p"), ("last", "-1")], "last: must be >= 0"),
        ([("path", "p"), ("since", "2026-9-1")], "since: expected YYYY-MM-DD"),
        ([("path", "p"), ("since", "2026-W01-1")], "since: expected YYYY-MM-DD"),
        ([("path", "")], "path is required"),
    ],
)
def test_bad_input_is_a_param_error(items: list[tuple[str, str]], fragment: str) -> None:
    with pytest.raises(ParamError, match=fragment):
        parse(SPEC, items)
