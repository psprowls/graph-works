"""`jsonable` is `json.dumps(..., default=str)`'s value rule, applied before encoding."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import IntEnum
from pathlib import PurePosixPath

import pytest
from graph_works_wire._jsonable import jsonable


class IntKey(int):
    def __str__(self) -> str:
        return "custom-int-str"

    def __repr__(self) -> str:
        return "custom-int-repr"


class FloatKey(float):
    def __str__(self) -> str:
        return "custom-float-str"

    def __repr__(self) -> str:
        return "custom-float-repr"


class StringValue(str):
    def __str__(self) -> str:
        return "custom-string"


class IntegerValue(int):
    def __str__(self) -> str:
        return "custom-integer"


class FloatValue(float):
    def __str__(self) -> str:
        return "custom-float"


class IntegerMode(IntEnum):
    FIRST = 1


@dataclass(frozen=True)
class NestedValue:
    label: str
    values: tuple[object, ...]


@pytest.mark.parametrize(
    "value",
    [
        None,
        "s",
        3,
        2.5,
        True,
        ["a", 1],
        ("a", ("b", None)),
        {"k": ("v", PurePosixPath("/x"))},
        PurePosixPath("/ws/okf"),
        {1, 2},
        object.__new__(type("Opaque", (), {"__str__": lambda self: "opaque"})),
    ],
)
def test_encoding_jsonable_equals_encoding_with_default_str(value: object) -> None:
    assert json.dumps(jsonable(value), indent=2) == json.dumps(value, indent=2, default=str)


def test_output_is_plain_and_round_trips() -> None:
    converted = jsonable({"a": (PurePosixPath("/p"), [1, {"b": None}])})
    assert converted == {"a": ["/p", [1, {"b": None}]]}
    assert json.loads(json.dumps(converted)) == converted


def test_scalar_subclasses_become_exact_json_builtins_without_using_their_string_overrides() -> None:
    value = [StringValue("actual"), IntegerValue(3), FloatValue(2.5), IntegerMode.FIRST]

    converted = jsonable(value)

    assert converted == ["actual", 3, 2.5, 1]
    assert [type(item) for item in converted] == [str, int, float, int]
    assert json.dumps(converted, indent=2) == json.dumps(value, indent=2, default=str)


def test_nested_dataclasses_follow_asdict_through_containers() -> None:
    value = {"outer": [NestedValue("x", (PurePosixPath("/p"), IntegerMode.FIRST))]}

    converted = jsonable(value)

    assert converted == {"outer": [{"label": "x", "values": ["/p", 1]}]}
    assert json.loads(json.dumps(converted)) == converted


def test_mapping_keys_use_the_encoder_spellings_without_changing_bytes() -> None:
    value = {"text": "s", 7: "int", 2.5: "float", False: "bool", None: "none"}

    converted = jsonable(value)

    assert converted == {
        "text": "s",
        "7": "int",
        "2.5": "float",
        "false": "bool",
        "null": "none",
    }
    assert json.dumps(converted, indent=2) == json.dumps(value, indent=2, default=str)


@pytest.mark.parametrize(
    ("key", "encoded_key"),
    [(IntKey(7), "7"), (FloatKey(2.5), "2.5")],
)
def test_numeric_subclass_mapping_keys_use_base_spelling(key: object, encoded_key: str) -> None:
    value = {key: "value"}

    converted = jsonable(value)

    assert converted == {encoded_key: "value"}
    assert json.dumps(converted) == json.dumps(value, default=str)


@pytest.mark.parametrize(
    ("key", "encoded_key"),
    [
        (True, "true"),
        (float("nan"), "NaN"),
        (float("inf"), "Infinity"),
        (float("-inf"), "-Infinity"),
    ],
)
def test_mapping_keys_cover_special_encoder_spellings(key: object, encoded_key: str) -> None:
    value = {key: "value"}

    converted = jsonable(value)

    assert converted == {encoded_key: "value"}
    assert json.dumps(converted) == json.dumps(value, default=str)


def test_mapping_key_conversion_refuses_a_lossy_collision() -> None:
    with pytest.raises(ValueError, match="same JSON object name"):
        jsonable({1: "number", "1": "string"})


def test_mapping_key_conversion_rejects_keys_the_encoder_rejects() -> None:
    with pytest.raises(TypeError, match="JSON object keys"):
        jsonable({("not", "supported"): "value"})
