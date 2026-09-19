"""Route description contract, anchored by a checked-in golden fixture.

The table-derived assertions make regenerating the golden meaningful; the app
suite separately establishes that every specification is actually mounted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from graph_works_serve.catalog import describe, render
from graph_works_serve.routes import ROUTES

GOLDEN = Path(__file__).parent / "fixtures" / "routes.golden.json"


def _without_version_bytes(data: bytes) -> bytes:
    return b"".join(line for line in data.splitlines(keepends=True) if not line.startswith(b'  "version": '))


def test_the_catalog_matches_the_golden() -> None:
    """A route or its rendered presentation cannot drift unnoticed."""
    golden = GOLDEN.read_bytes()
    rendered = render(ROUTES, "x").encode("utf-8")
    assert _without_version_bytes(rendered) == _without_version_bytes(golden)


def test_the_golden_is_lf_only() -> None:
    """The portable checked-in JSON fixture has no CRLF bytes."""
    assert b"\r" not in GOLDEN.read_bytes()


def test_routes_sorted_and_params_in_declaration_order() -> None:
    """Consumers receive stable route ordering while parameter order remains semantic."""
    doc = describe(ROUTES, "1.2.3")
    routes = cast(list[dict[str, object]], doc["routes"])
    assert [(entry["path"], entry["method"]) for entry in routes] == sorted((spec.path, spec.method) for spec in ROUTES)
    by_path = {spec.path: spec for spec in ROUTES}
    for entry in routes:
        params = cast(list[dict[str, object]], entry["params"])
        spec = by_path[cast(str, entry["path"])]
        assert [param["name"] for param in params] == [param.name for param in spec.params]
    assert doc["schema_version"] == 1
    assert doc["server"] == "graph-works-serve"
    assert doc["version"] == "1.2.3"


def test_param_entries_carry_type_required_default() -> None:
    """The catalog exposes the actual conversion contract, including optional defaults."""
    doc = describe(ROUTES, "v")
    routes = cast(list[dict[str, object]], doc["routes"])
    log = next(entry for entry in routes if entry["path"] == "/v1/log")
    params = cast(list[dict[str, object]], log["params"])
    assert params[0] == {
        "name": "last",
        "type": "int",
        "required": False,
        "default": 10,
        "location": "query",
        "summary": "Entries to return after filtering.",
    }


def test_render_is_indented_lf_json() -> None:
    """The CLI-facing renderer emits parseable, pretty, LF-terminated JSON."""
    text = render(ROUTES, "v")
    assert text.endswith("}\n")
    assert "\r" not in text
    assert json.loads(text) == describe(ROUTES, "v")


def test_every_route_declares_its_response_kind() -> None:
    routes = describe(ROUTES, "test")["routes"]
    assert all(route["response"] in {"json", "event-stream"} for route in routes)
    events = [route for route in routes if route["path"] == "/v1/events"]
    assert events == [
        {
            "method": "GET",
            "path": "/v1/events",
            "summary": "Stream workspace change events (Server-Sent Events).",
            "params": [],
            "response": "event-stream",
        }
    ]


def test_every_param_names_its_location() -> None:
    doc = describe(ROUTES, "test")
    for route in doc["routes"]:
        for param in route["params"]:
            assert param["location"] in {"query", "body"}
