from __future__ import annotations

import importlib.resources
from datetime import date
from pathlib import Path

import pytest
from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import load_sections
from okf_io import load_bundle, parse
from okf_io import validate as okf_validate
from repositories_okf.git import HeadState
from repositories_okf.lifecycle import (
    checkout_action,
    clone_path,
    default_name,
    lane_pages,
    managed_page_text,
    page_path,
    reference_page_text,
    restore_action,
    scan_config_hash,
    valid_name,
)
from repositories_okf.pin import Pin, read_pin

ASSETS = importlib.resources.files("repositories_okf") / "assets"


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("https://github.com/org/Some-Upstream.git", "some-upstream"),
        ("https://github.com/org/some-upstream/", "some-upstream"),
        ("git@github.com:org/some_upstream.git", "some_upstream"),
        ("file:///tmp/x/upstream.git", "upstream"),
    ],
)
def test_default_name_is_the_last_segment_without_dot_git(url: str, name: str) -> None:
    assert default_name(url) == name


@pytest.mark.parametrize("name", ["demo", "a", "a.b-c_d", "0x"])
def test_valid_names(name: str) -> None:
    assert valid_name(name)


@pytest.mark.parametrize("name", ["", "-x", ".x", "Demo", "a/b", "a b", "..", "_x"])
def test_invalid_names(name: str) -> None:
    assert not valid_name(name)


def test_paths() -> None:
    assert page_path("demo") == "repositories/demo.md"
    assert clone_path("demo") == "repositories/demo/references/git"


def test_the_reference_page_validates_against_the_owned_schema_and_sections(tmp_path: Path) -> None:
    pin = Pin(
        commit="a" * 40,
        fetched_at="2026-09-29T20:40:00Z",
        ref="main",
        tree="b" * 40,
        commit_date="2026-09-10T14:02:11+00:00",
    )
    text = reference_page_text(name="demo", url="https://example.com/demo.git", track="main", pin=pin)
    root = tmp_path / "bundle"
    (root / "repositories").mkdir(parents=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# b\n", encoding="utf-8", newline="")
    (root / "repositories" / "demo.md").write_text(text, encoding="utf-8", newline="")
    rules = [
        schema_rule(load_schemas(str(ASSETS / "schema")), severity="error"),
        section_rule(load_sections(str(ASSETS / "sections")), severity="error"),
    ]
    report = okf_validate(load_bundle(root), today=date(2026, 9, 29), extra_rules=rules)
    assert report.errors == ()
    assert read_pin(parse(text)) == pin


def test_managed_page_text_is_a_valid_managed_page_linking_its_code_graph_page() -> None:
    text = managed_page_text(
        name="demo",
        url="https://example.com/demo.git",
        track="main",
        pin=Pin(commit="b" * 40, fetched_at="2026-09-29T20:40:00Z", ref="main"),
    )
    document = parse(text)
    data = document.fm_data(dates="iso")
    assert (data["type"], data["title"], data["url"], data["track"]) == (
        "ManagedRepository",
        "demo",
        "https://example.com/demo.git",
        "main",
    )
    assert data["description"] == "Managed repository https://example.com/demo.git"
    assert data["pin"] == {"commit": "b" * 40, "ref": "main", "fetched_at": "2026-09-29T20:40:00Z"}
    assert "## Summary" in text and "(/code-graph/demo.md)" in text


def test_scan_config_hash_is_stable_and_sensitive() -> None:
    base = scan_config_hash("okf/repositories/demo/references/git", ["*.lock", "dist/"])
    assert base == scan_config_hash("okf/repositories/demo/references/git", ["*.lock", "dist/"])
    assert len(base) == 64 and int(base, 16) >= 0
    assert base != scan_config_hash("okf/repositories/demo/references/git", ["dist/", "*.lock"])
    assert base != scan_config_hash("okf/repositories/other/references/git", ["*.lock", "dist/"])


@pytest.mark.parametrize(
    ("exists", "linked", "decision"),
    [(False, False, "checkout-create"), (True, True, "checkout-present"), (True, False, "checkout-foreign")],
)
def test_checkout_action(exists: bool, linked: bool, decision: str) -> None:
    assert checkout_action(exists=exists, linked=linked) == decision


def test_lane_pages_selects_direct_lane_pages_of_either_type(tmp_path: Path) -> None:
    lane = tmp_path / "repositories"
    (lane / "nested").mkdir(parents=True)
    page = "---\ntype: {t}\ntitle: x\ndescription: d\nurl: u\n---\n\n## Summary\n\nx\n"
    (lane / "m.md").write_text(page.format(t="ManagedRepository"), encoding="utf-8", newline="")
    (lane / "r.md").write_text(page.format(t="ReferenceRepository"), encoding="utf-8", newline="")
    (lane / "c.md").write_text(page.format(t="Concept"), encoding="utf-8", newline="")
    (lane / "nested" / "n.md").write_text(page.format(t="ManagedRepository"), encoding="utf-8", newline="")
    assert sorted(lane_pages(load_bundle(tmp_path))) == ["m", "r"]


PIN = "a" * 40
URL = "https://example.com/demo.git"


@pytest.mark.parametrize(
    ("kwargs", "decision"),
    [
        ({"pin_commit": None, "clone_exists": True}, "no-pin"),
        ({"clone_exists": False}, "clone"),
        ({"origin": "https://example.com/other.git"}, "url-mismatch"),
        ({"origin": None}, "url-mismatch"),
        ({}, "present"),
        ({"clean": False}, "present"),  # at the pin: restore has nothing to do, dirty or not
        ({"head": HeadState("b" * 40, True)}, "redetach"),
        ({"head": HeadState(PIN, False)}, "redetach"),  # on a branch at the pin is not "detached at pin"
        ({"head": HeadState("b" * 40, True), "clean": False}, "clone-dirty"),
    ],
)
def test_restore_action(kwargs: dict[str, object], decision: str) -> None:
    arguments: dict[str, object] = {
        "pin_commit": PIN,
        "clone_exists": True,
        "origin": URL,
        "url": URL,
        "head": HeadState(PIN, True),
        "clean": True,
    }
    arguments.update(kwargs)
    assert restore_action(**arguments) == decision  # type: ignore[arg-type]
