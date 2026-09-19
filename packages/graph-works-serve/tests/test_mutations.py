# packages/graph-works-serve/tests/test_mutations.py
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from graph_works_serve import mutations
from graph_works_serve.mutations import (
    BeforeApply,
    MutationSpec,
    Outcome,
    Params,
    canonical_json,
    digest,
    format_instant,
)
from graph_works_serve.params import Param, ParamError

NOW = datetime(2026, 9, 18, 14, 3, 7, 654321, tzinfo=UTC)
AS_OF = "2026-09-18T14:03:07Z"


class Store:
    """A fake workspace: `value` is what a plan would change, `writes` counts applies."""

    def __init__(self) -> None:
        self.value = "a"
        self.writes = 0
        self.refuse = False
        self.fail_apply = False
        self.apply_refuses = False
        self.raise_: BaseException | None = None
        self.calls: list[tuple[str, bool, bool]] = []  # (as_of, dry_run, lock held)


def make_spec(store: Store) -> MutationSpec:
    def run(
        _layout: object, params: Params, as_of: datetime, dry_run: bool, before_apply: BeforeApply | None = None
    ) -> dict[str, Any]:
        store.calls.append((format_instant(as_of), dry_run, mutations._LOCK.locked()))
        if store.raise_ is not None:
            raise store.raise_
        if not dry_run and before_apply is not None:
            before_apply(
                {
                    "target": params["name"],
                    "before": store.value,
                    "on": as_of.date().isoformat(),
                    "refusal": "no" if store.refuse else None,
                    "applied": False,
                    "rolled_back": False,
                    "failures": [],
                }
            )
        refusal = "no" if store.refuse or (not dry_run and store.apply_refuses) else None
        if not dry_run and refusal is None:
            store.writes += 1
        return {
            "target": params["name"],
            "before": store.value,
            "on": as_of.date().isoformat(),
            "refusal": refusal,
            "applied": not dry_run and refusal is None,
            "rolled_back": not dry_run and store.fail_apply,
            "failures": ["boom"] if not dry_run and store.fail_apply else [],
        }

    def project(result: object, _params: Params, _dry_run: bool) -> dict[str, Any]:
        assert isinstance(result, dict)
        return dict(result)

    def validate(params: Params) -> None:
        if params["name"] == "":
            raise ParamError("name must be non-empty")

    return MutationSpec(
        route="/v1/fake",
        command="fake",
        summary="Fake mutation",
        params=(
            Param("name", "str", required=True, location="body"),
            Param("flag", "bool", default=False, location="body"),
        ),
        run=run,
        project=project,
        refused=lambda plan, _params: "refused" if plan["refusal"] else None,
        validate=validate,
    )


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    monkeypatch.setattr(mutations, "now", lambda: NOW)
    return Store()


def req(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


JSON = "application/json"


def do_plan(store: Store, body: object) -> Outcome:
    return mutations.plan(make_spec(store), object(), req(body), JSON)  # type: ignore[arg-type]


def do_apply(store: Store, body: object) -> Outcome:
    return mutations.apply(make_spec(store), object(), req(body), JSON)  # type: ignore[arg-type]


def test_canonical_json_is_sorted_compact_utf8() -> None:
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()


def test_digest_is_sha256_of_route_params_as_of_and_plan() -> None:
    import hashlib

    expected = hashlib.sha256(
        canonical_json({"route": "/r", "params": {"x": 1}, "as_of": AS_OF, "plan": {"p": 2}})
    ).hexdigest()
    assert digest("/r", {"x": 1}, AS_OF, {"p": 2}) == f"sha256:{expected}"


def test_instant_round_trip_and_strictness() -> None:
    assert format_instant(NOW) == AS_OF
    assert mutations.parse_instant(AS_OF) == NOW.replace(microsecond=0)
    for bad in ("2026-09-18T14:03:07+00:00", "2026-09-18T14:03:07.1Z", "2026-02-30T00:00:00Z", "x"):
        assert mutations.parse_instant(bad) is None


def test_plan_pins_the_instant_and_writes_nothing(store: Store) -> None:
    outcome = do_plan(store, {"name": "n"})
    assert outcome.status == 200
    assert outcome.body["as_of"] == AS_OF
    assert outcome.body["plan"]["on"] == "2026-09-18"
    assert outcome.body["digest"] == digest("/v1/fake", {"name": "n", "flag": False}, AS_OF, outcome.body["plan"])
    assert store.writes == 0
    assert store.calls == [(AS_OF, True, False)]  # plan never takes the lock


def test_a_refused_plan_is_still_200(store: Store) -> None:
    store.refuse = True
    assert do_plan(store, {"name": "n"}).status == 200


def test_plan_is_deterministic_and_default_filling(store: Store) -> None:
    first = do_plan(store, {"name": "n"}).body
    second = do_plan(store, {"name": "n", "flag": False}).body
    assert first == second


def test_apply_round_trip(store: Store) -> None:
    planned = do_plan(store, {"name": "n"}).body
    outcome = do_apply(store, {"name": "n", "as_of": planned["as_of"], "digest": planned["digest"]})
    assert outcome.status == 200
    assert outcome.body["as_of"] == AS_OF
    assert outcome.body["digest"] == planned["digest"]
    assert outcome.body["result"]["applied"] is True
    assert store.writes == 1
    # re-plan and apply both use the echoed instant, both under the lock
    assert store.calls[1:] == [(AS_OF, True, True), (AS_OF, False, True)]


def test_apply_with_a_changed_workspace_is_409_with_a_fresh_plan(store: Store) -> None:
    planned = do_plan(store, {"name": "n"}).body
    store.value = "b"
    outcome = do_apply(store, {"name": "n", "as_of": planned["as_of"], "digest": planned["digest"]})
    assert outcome.status == 409
    error = outcome.body["error"]
    assert error["reason"] == "stale-plan" and error["command"] == "fake"
    fresh = error["payload"]
    assert fresh["as_of"] == AS_OF and fresh["plan"]["before"] == "b"
    assert store.writes == 0
    # the fresh digest applies straight away
    again = do_apply(store, {"name": "n", "as_of": fresh["as_of"], "digest": fresh["digest"]})
    assert again.status == 200 and store.writes == 1


@pytest.mark.parametrize("body_change", [{"flag": True}, {"name": "other"}])
def test_tampered_params_are_409(store: Store, body_change: dict[str, object]) -> None:
    planned = do_plan(store, {"name": "n"}).body
    body = {"name": "n", **body_change, "as_of": planned["as_of"], "digest": planned["digest"]}
    assert do_apply(store, body).status == 409
    assert store.writes == 0


def test_a_digest_from_another_route_is_409(store: Store) -> None:
    planned = do_plan(store, {"name": "n"}).body
    foreign = digest("/v1/other", {"name": "n", "flag": False}, planned["as_of"], planned["plan"])
    assert do_apply(store, {"name": "n", "as_of": planned["as_of"], "digest": foreign}).status == 409


@pytest.mark.parametrize(
    "as_of",
    [
        format_instant(NOW - timedelta(minutes=15, seconds=8)),  # expired
        format_instant(NOW + timedelta(seconds=1)),  # future
    ],
)
def test_as_of_outside_the_window_is_409_with_a_new_instant(store: Store, as_of: str) -> None:
    outcome = do_apply(store, {"name": "n", "as_of": as_of, "digest": "sha256:00"})
    assert outcome.status == 409
    assert outcome.body["error"]["payload"]["as_of"] == AS_OF
    assert store.writes == 0
    assert all(dry_run for _, dry_run, _ in store.calls)  # only plans ran; nothing applied


def test_as_of_at_the_window_edge_is_accepted(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    planned = do_plan(store, {"name": "n"}).body  # as_of = 14:03:07
    # 14:18:07 is exactly TTL later; the window is inclusive at both ends.
    monkeypatch.setattr(mutations, "now", lambda: NOW.replace(microsecond=0) + mutations.TTL)
    outcome = do_apply(store, {"name": "n", "as_of": planned["as_of"], "digest": planned["digest"]})
    assert outcome.status == 200
    # the re-plan used the echoed instant, not the later clock
    assert store.calls[-2][0] == AS_OF


def test_a_refused_plan_applies_as_422_and_writes_nothing(store: Store) -> None:
    store.refuse = True
    planned = do_plan(store, {"name": "n"}).body
    outcome = do_apply(store, {"name": "n", "as_of": planned["as_of"], "digest": planned["digest"]})
    assert outcome.status == 422
    assert outcome.body["error"]["reason"] == "refused"
    assert outcome.body["error"]["payload"] == planned["plan"]
    assert store.writes == 0
    assert [dry for _, dry, _ in store.calls] == [True, True]


def test_a_refusal_that_appears_only_at_apply_is_422(store: Store) -> None:
    store.apply_refuses = True
    planned = do_plan(store, {"name": "n"}).body
    outcome = do_apply(store, {"name": "n", "as_of": planned["as_of"], "digest": planned["digest"]})
    assert outcome.status == 422 and store.writes == 0


def test_an_incomplete_apply_is_500_with_the_apply_projection(store: Store) -> None:
    store.fail_apply = True
    planned = do_plan(store, {"name": "n"}).body
    outcome = do_apply(store, {"name": "n", "as_of": planned["as_of"], "digest": planned["digest"]})
    assert outcome.status == 500
    assert outcome.body["error"]["reason"] == "incomplete-apply"
    assert outcome.body["error"]["payload"]["failures"] == ["boom"]


@pytest.mark.parametrize(
    ("body", "content_type", "status"),
    [
        ({"name": "n", "digest": "sha256:0"}, JSON, 400),  # missing as_of
        ({"name": "n", "as_of": AS_OF}, JSON, 400),  # missing digest
        ({"name": "n", "as_of": "yesterday", "digest": "d"}, JSON, 400),
        ({"name": "", "as_of": AS_OF, "digest": "d"}, JSON, 400),  # validate hook
        ({"name": "n", "as_of": AS_OF, "digest": "d"}, "text/plain", 415),
        ({"name": "n", "as_of": AS_OF, "digest": "d"}, None, 415),
    ],
)
def test_malformed_apply_requests(store: Store, body: object, content_type: str | None, status: int) -> None:
    outcome = mutations.apply(make_spec(store), object(), req(body), content_type)  # type: ignore[arg-type]
    assert outcome.status == status
    assert outcome.body["error"]["reason"] == "usage"
    assert store.calls == []


def test_content_type_parameters_are_accepted(store: Store) -> None:
    outcome = mutations.plan(make_spec(store), object(), req({"name": "n"}), "Application/JSON; charset=utf-8")  # type: ignore[arg-type]
    assert outcome.status == 200


def test_plan_rejects_apply_only_fields(store: Store) -> None:
    assert do_plan(store, {"name": "n", "digest": "d"}).status == 400


@pytest.mark.parametrize(
    ("exc", "reason", "status"),
    [(ValueError("unknown item"), "unresolved", 404), (OSError("disk"), "io", 500)],
)
def test_core_exceptions_map_to_envelopes(store: Store, exc: BaseException, reason: str, status: int) -> None:
    store.raise_ = exc
    outcome = do_plan(store, {"name": "n"})
    assert (outcome.status, outcome.body["error"]["reason"]) == (status, reason)


def test_apply_serializes_through_one_lock(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """Thread B's apply cannot re-plan while thread A is inside its apply."""
    in_apply = threading.Event()
    release = threading.Event()
    second_attempt = threading.Event()

    class SignalingLock:
        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.attempts = 0

        def __enter__(self) -> None:
            self.attempts += 1
            if self.attempts == 2:
                second_attempt.set()
            self.lock.acquire()

        def __exit__(self, *_exc: object) -> None:
            self.lock.release()

        def locked(self) -> bool:
            return self.lock.locked()

    monkeypatch.setattr(mutations, "_LOCK", SignalingLock())
    spec = make_spec(store)
    inner = spec.run

    def gated(
        layout: object, params: Params, as_of: datetime, dry_run: bool, before_apply: BeforeApply | None = None
    ) -> object:
        if not dry_run and not in_apply.is_set():
            in_apply.set()
            assert release.wait(timeout=10)
        result = inner(layout, params, as_of, dry_run, before_apply)
        if not dry_run:
            store.value = "after-" + store.value  # the write changes the plan
        return result

    import dataclasses

    gated_spec = dataclasses.replace(spec, run=gated)
    planned = mutations.plan(gated_spec, object(), req({"name": "n"}), JSON).body  # type: ignore[arg-type]
    body = req({"name": "n", "as_of": planned["as_of"], "digest": planned["digest"]})
    results: dict[str, Outcome] = {}
    a = threading.Thread(target=lambda: results.__setitem__("a", mutations.apply(gated_spec, object(), body, JSON)))  # type: ignore[arg-type]
    a.start()
    assert in_apply.wait(timeout=10)
    b = threading.Thread(target=lambda: results.__setitem__("b", mutations.apply(gated_spec, object(), body, JSON)))  # type: ignore[arg-type]
    b.start()
    try:
        assert second_attempt.wait(timeout=10)
        assert store.writes == 0
        assert store.calls == [(AS_OF, True, False), (AS_OF, True, True)]
    finally:
        release.set()
        a.join(timeout=10)
        b.join(timeout=10)
    assert not a.is_alive() and not b.is_alive()
    assert sorted(outcome.status for outcome in results.values()) == [200, 409]
    assert store.writes == 1
    # every re-plan and apply ran under the lock
    assert all(held for _, _, held in store.calls[1:])


def test_exception_envelopes_preserve_reason_exit_codes(store: Store) -> None:
    from graph_works_core.workspace.errors import WorkspaceError

    for exc, reason, code in ((ValueError("missing"), "unresolved", 7), (WorkspaceError("workspace"), "workspace", 4)):
        store.raise_ = exc
        outcome = do_plan(store, {"name": "n"})
        assert outcome.body["error"]["reason"] == reason
        assert outcome.body["error"]["exit_code"] == code


def test_expired_plan_reads_clock_once(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    reads = 0

    def clock() -> datetime:
        nonlocal reads
        reads += 1
        return NOW

    monkeypatch.setattr(mutations, "now", clock)
    outcome = do_apply(store, {"name": "n", "as_of": "2026-09-17T14:03:07Z", "digest": "d"})
    assert outcome.status == 409
    assert outcome.body["error"]["payload"]["as_of"] == AS_OF
    assert reads == 1


def test_unexpected_exceptions_propagate(store: Store) -> None:
    store.raise_ = RuntimeError("unexpected")
    with pytest.raises(RuntimeError, match="unexpected"):
        do_plan(store, {"name": "n"})
