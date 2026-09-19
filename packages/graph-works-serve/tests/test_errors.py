from __future__ import annotations

import code_graph_io.exit_codes as codes
import pytest
from graph_works_serve.context import Reply
from graph_works_serve.errors import EXIT, Catch, call, refusal


def test_exit_codes_keep_their_cli_meaning() -> None:
    assert EXIT == {
        "usage": codes.GENERIC,
        "unresolved": codes.AMBIGUOUS,
        "workspace": codes.SCHEMA_MISMATCH,
        "io": codes.GENERIC,
        "refused": codes.GENERIC,
    }


@pytest.mark.parametrize(
    ("reason", "status"),
    [("usage", 400), ("unresolved", 404), ("workspace", 500), ("io", 500)],
)
def test_reason_maps_to_status(reason: str, status: int) -> None:
    reply = refusal("/v1/x", reason, "m")
    assert reply.status == status
    assert reply.body == {
        "error": {
            "command": "/v1/x",
            "reason": reason,
            "message": "m",
            "exit_code": EXIT[reason],
            "payload": None,
        }
    }


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        ("stale-plan", 409),
        ("refused", 422),
        ("conflict", 422),
        ("incomplete", 422),
        ("incomplete-apply", 500),
        ("usage", 400),
        ("unresolved", 404),
        ("workspace", 500),
        ("io", 500),
    ],
)
def test_handler_reasons_map_to_http_statuses(reason: str, status: int) -> None:
    reply = refusal("/v1/x", reason, "m")
    assert reply.status == status


def test_an_explicit_status_wins() -> None:
    assert refusal("/v1/x", "refused", "m", status=401).status == 401


def test_call_maps_the_first_matching_catch_in_order() -> None:
    class Narrow(ValueError):
        pass

    def boom() -> Reply:
        raise Narrow("nope")

    reply = call("/v1/x", boom, (Catch(Narrow, "workspace", 4), Catch(ValueError, "unresolved", 7)))
    assert reply.status == 500 and reply.body["error"]["reason"] == "workspace"  # type: ignore[index]


def test_call_passes_success_and_reraises_the_unmapped() -> None:
    assert call("/v1/x", lambda: Reply(200, {}), ()).status == 200

    def boom() -> Reply:
        raise KeyError("k")

    with pytest.raises(KeyError):
        call("/v1/x", boom, (Catch(ValueError, "io", 1),))
