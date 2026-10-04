"""Liveness rows as data: Orca's mixed timestamps, ages, and the JSON projection."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from workflow_orca._liveness import (
    LivenessRow,
    age_s,
    as_object,
    instant,
    iso,
    liveness_data,
    read_failed,
    show_failed,
    stamp,
    transcript_unavailable,
)
from workflow_orca._progress import SddProgress

MOMENT = datetime(2026, 9, 27, 17, 32, 20, tzinfo=UTC)


@pytest.mark.parametrize(
    "raw",
    [
        "2026-09-27T17:32:20Z",
        "2026-09-27T17:32:20+00:00",
        "2026-09-27T19:32:20+02:00",
        "2026-09-27 17:32:20",
        MOMENT.timestamp() * 1000,
        int(MOMENT.timestamp() * 1000),
    ],
)
def test_every_orca_spelling_normalises_to_one_utc_instant(raw):
    assert instant(raw) == MOMENT


@pytest.mark.parametrize("raw", [None, "", "  ", "yesterday", True, [], {}, 10**30])
def test_anything_else_is_not_an_instant(raw):
    assert instant(raw) is None


def test_iso_is_whole_seconds_utc_with_z():
    assert iso(MOMENT.astimezone(timezone(timedelta(hours=-7)))) == "2026-09-27T17:32:20Z"
    assert iso(MOMENT.replace(microsecond=999_999)) == "2026-09-27T17:32:20Z"


def test_age_is_whole_seconds_and_never_negative():
    assert age_s(MOMENT + timedelta(seconds=90, milliseconds=900), MOMENT) == 90
    assert age_s(MOMENT - timedelta(seconds=30), MOMENT) == 0


def test_stamp_notes_only_an_unparseable_value():
    notes: list[str] = []
    now = MOMENT + timedelta(seconds=5)
    assert stamp(None, "lastHeartbeatAt", now, notes) == (None, None)
    assert stamp("2026-09-27T17:32:20Z", "lastHeartbeatAt", now, notes) == ("2026-09-27T17:32:20Z", 5)
    assert stamp("garbage", "lastOutputAt", now, notes) == (None, None)
    assert notes == ["lastOutputAt unparseable"]


def test_note_formatters():
    assert show_failed("dispatch_not_found") == "worker-show failed: dispatch_not_found"
    assert read_failed("worker_identity_changed") == "worker-read failed: worker_identity_changed"
    assert transcript_unavailable("terminal") == "transcript unavailable (terminal source)"


def test_as_object():
    assert as_object({"a": 1}) == {"a": 1}
    assert as_object(None) == {}
    assert as_object(["a"]) == {}


def test_projection_is_plain_json_with_progress_nested_or_null():
    progress = SddProgress(ledger="/w/.superpowers/sdd/p/progress.md", plan="p.md", completed=3, total=4, last_line="x")
    rows = [
        LivenessRow(
            key="k#execute",
            handle="ctx_1",
            state="running",
            heartbeat_at="2026-09-27T17:32:20Z",
            heartbeat_age_s=5,
            transcript_at=None,
            transcript_age_s=None,
            output_at=None,
            output_age_s=None,
            worktree_path="/w",
            progress=progress,
            notes=("no terminal",),
        ),
        LivenessRow(
            key="k#design",
            handle="ctx_2",
            state="pending",
            heartbeat_at=None,
            heartbeat_age_s=None,
            transcript_at=None,
            transcript_age_s=None,
            output_at=None,
            output_age_s=None,
            worktree_path=None,
            progress=None,
            notes=(),
        ),
    ]
    data = liveness_data(rows)
    assert json.loads(json.dumps(data)) == data
    assert data[0]["progress"] == {
        "ledger": "/w/.superpowers/sdd/p/progress.md",
        "plan": "p.md",
        "completed": 3,
        "total": 4,
        "last_line": "x",
    }
    assert data[0]["notes"] == ["no terminal"]
    assert data[1]["progress"] is None
    assert data[0]["terminal"] is None
    assert list(data[0]) == [
        "key",
        "handle",
        "state",
        "heartbeat_at",
        "heartbeat_age_s",
        "transcript_at",
        "transcript_age_s",
        "output_at",
        "output_age_s",
        "worktree_path",
        "progress",
        "notes",
        "terminal",
    ]


@pytest.mark.parametrize(
    "raw",
    [
        "0001-01-01T00:00:00+01:00",
        "9999-12-31T23:59:59-01:00",
        float("nan"),
        float("inf"),
        float("-inf"),
        10**1000,
    ],
)
def test_out_of_range_and_nonfinite_values_are_unparseable(raw):
    assert instant(raw) is None
    notes: list[str] = []
    assert stamp(raw, "timestamp", MOMENT, notes) == (None, None)
    assert notes == ["timestamp unparseable"]


def test_ancient_fractional_age_floors_exactly():
    assert age_s(datetime(2026, 9, 27, tzinfo=UTC), datetime(1, 1, 1, microsecond=1, tzinfo=UTC)) == 63926063999


@pytest.mark.parametrize("year", [1, 999, 1000, 9999])
def test_iso_four_digit_year_is_independent_of_platform_strftime(year):
    class UnpaddedYear(datetime):
        # Model the documented libc variation on hosts that pad %Y already.
        def strftime(self, fmt):
            return super().strftime(fmt).replace(f"{self.year:04d}", str(self.year), 1)

    moment = UnpaddedYear(year, 1, 2, 3, 4, 5, 999999, tzinfo=timezone(timedelta(hours=2)))
    assert iso(moment) == f"{year:04d}-01-02T01:04:05Z"
