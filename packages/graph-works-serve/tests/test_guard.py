from __future__ import annotations

import json

import pytest
from graph_works_serve.guard import check

TOKEN = "t0k3n-secret"
PORT = 4321
OK_HOST = {"host": f"127.0.0.1:{PORT}"}


def _auth(value: str) -> dict[str, str]:
    return {**OK_HOST, "authorization": f"Bearer {value}"}


def test_a_header_token_on_loopback_passes() -> None:
    assert check(_auth(TOKEN), b"", token=TOKEN, port=PORT) is None


def test_a_query_token_passes_only_for_the_get_event_stream() -> None:
    assert (
        check(
            OK_HOST,
            f"token={TOKEN}&x=1".encode(),
            token=TOKEN,
            port=PORT,
            method="GET",
            path="/v1/events",
        )
        is None
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/v1/health"), ("POST", "/v1/work/archive/plan")],
    ids=["health", "mutation"],
)
def test_a_query_token_is_refused_off_the_get_event_stream(method: str, path: str) -> None:
    reply = check(OK_HOST, f"token={TOKEN}".encode(), token=TOKEN, port=PORT, method=method, path=path)
    assert reply is not None and reply.status == 401


def test_a_query_token_is_refused_when_scope_is_not_supplied() -> None:
    reply = check(OK_HOST, f"token={TOKEN}".encode(), token=TOKEN, port=PORT)
    assert reply is not None and reply.status == 401


def test_a_bearer_token_takes_precedence_over_an_event_query_token() -> None:
    reply = check(
        _auth("wrong"),
        f"token={TOKEN}".encode(),
        token=TOKEN,
        port=PORT,
        method="GET",
        path="/v1/events",
    )
    assert reply is not None and reply.status == 401


def test_localhost_host_passes() -> None:
    headers = {"host": f"localhost:{PORT}", "authorization": f"Bearer {TOKEN}"}
    assert check(headers, b"", token=TOKEN, port=PORT) is None


@pytest.mark.parametrize(
    "headers",
    [{}, {"authorization": "Basic abc"}, {"authorization": "Bearer wrong"}],
    ids=["missing", "wrong-scheme", "wrong-token"],
)
def test_token_failures_are_401(headers: dict[str, str]) -> None:
    reply = check({**OK_HOST, **headers}, b"token=also-wrong", token=TOKEN, port=PORT)
    assert reply is not None and reply.status == 401
    assert reply.body["error"]["reason"] == "refused"  # type: ignore[index]


@pytest.mark.parametrize("credential", ["not-ascii-\u00e9", "\ud800"], ids=["non-ascii", "surrogate"])
def test_malformed_supplied_credentials_are_refused(credential: str) -> None:
    reply = check(_auth(credential), b"", token=TOKEN, port=PORT)
    assert reply is not None and reply.status == 401


@pytest.mark.parametrize(
    "host",
    [None, "evil.example", f"evil.example:{PORT}", "127.0.0.1:9", f"127.0.0.1.nip.io:{PORT}"],
)
def test_foreign_or_missing_host_is_403_even_with_a_good_token(host: str | None) -> None:
    headers = {"authorization": f"Bearer {TOKEN}"}
    if host is not None:
        headers["host"] = host
    reply = check(headers, b"", token=TOKEN, port=PORT)
    assert reply is not None and reply.status == 403


def test_a_refusal_never_echoes_the_token() -> None:
    for reply in (
        check({**OK_HOST, "authorization": f"Bearer {TOKEN}x"}, b"", token=TOKEN, port=PORT),
        check({"host": "evil", "authorization": f"Bearer {TOKEN}"}, b"", token=TOKEN, port=PORT),
    ):
        assert reply is not None
        assert TOKEN not in json.dumps(reply.body)
