from __future__ import annotations

import pytest
from okf_io import parse
from repositories_okf.pin import Generation, Pin, read_pin, write_pin

A = "a" * 40
B = "b" * 40
T = "c" * 40
PAGE = (
    "---\ntype: ReferenceRepository\ntitle: demo\ndescription: d"
    "\nurl: https://example.com/demo.git\n---\n\n## Summary\n\nx\n"
)


def test_frontmatter_omits_absent_keys_in_a_fixed_order() -> None:
    pin = Pin(commit=A, fetched_at="2026-09-29T20:40:00Z", ref="main", tree=T, commit_date="2026-09-10T14:02:11+00:00")
    assert list(pin.frontmatter()) == ["commit", "tree", "commit_date", "ref", "fetched_at"]


def test_write_then_read_round_trips_and_touches_only_pin() -> None:
    document = parse(PAGE)
    pin = Pin(
        commit=A,
        fetched_at="2026-09-29T20:40:00Z",
        ref="v1.0.0",
        describe="v1.0.0",
        tree=T,
        commit_date="2026-09-10T14:02:11+00:00",
        previous=B,
    )
    write_pin(document, pin)
    text = document.serialize()
    assert text.startswith("---\ntype: ReferenceRepository\ntitle: demo\n")
    assert text.endswith("## Summary\n\nx\n")
    assert read_pin(parse(text)) == pin


def test_timestamps_survive_as_strings_not_yaml_datetimes() -> None:
    document = parse(PAGE)
    write_pin(document, Pin(commit=A, fetched_at="2026-09-29T20:40:00Z"))
    reread = read_pin(parse(document.serialize()))
    assert reread is not None
    assert isinstance(reread.fetched_at, str) and reread.fetched_at.startswith("2026-09-29T20:40:00")


def test_read_pin_refuses_a_missing_or_short_commit() -> None:
    assert read_pin(parse(PAGE)) is None
    assert (
        read_pin(parse(PAGE.replace("url:", "pin:\n  commit: abc123\n  fetched_at: '2026-09-29T20:40:00Z'\nurl:")))
        is None
    )
    assert read_pin(parse(PAGE.replace("url:", "pin: nonsense\nurl:"))) is None


def test_generation_round_trips_and_is_emitted_last() -> None:
    pin = Pin(commit=A, fetched_at="2026-09-29T20:40:00Z", ref="main", generation=Generation("0.6.6", "f" * 64))
    assert list(pin.frontmatter()) == ["commit", "ref", "fetched_at", "generation"]
    assert pin.frontmatter()["generation"] == {"gw_version": "0.6.6", "scan_config_hash": "f" * 64}
    document = parse("---\ntype: ManagedRepository\ntitle: d\n---\n\n## Summary\n\nx\n")
    write_pin(document, pin)
    assert read_pin(parse(document.serialize())) == pin


@pytest.mark.parametrize("generation", ["x", "{}", "{gw_version: 1, scan_config_hash: h}", "{gw_version: v}"])
def test_a_malformed_generation_is_ignored_not_fatal(generation: str) -> None:
    document = parse(
        f"---\ntype: ManagedRepository\ntitle: d\npin:\n  commit: {A}\n"
        f"  fetched_at: '2026-09-29T20:40:00Z'\n  generation: {generation}\n---\n"
    )
    pin = read_pin(document)
    assert pin is not None and pin.generation is None
