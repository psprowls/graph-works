"""External edits at core's apply-time preparation must not escape review."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from graph_works_serve import mutation_specs, mutations
from mutation_helpers import make_client, serve_workspace, snapshot, write_item, write_proposal


@pytest.mark.parametrize("operation", ["archive", "advance", "decide"])
def test_changed_core_candidate_is_stale_before_any_serve_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    monkeypatch.setattr(mutations, "now", lambda: datetime(2026, 9, 18, 14, 3, 7, tzinfo=UTC))
    layout = serve_workspace(tmp_path)
    path = "work/feature-reviewed"
    if operation == "archive":
        write_item(layout, path, work_status="resolved", phase="done")
        route, params, command = "/v1/work/archive", {}, "run_archive"
    elif operation == "advance":
        write_item(layout, path, work_status="open", phase="design", effort="medium")
        route, params, command = "/v1/work/advance", {"path": path}, "run_stage_advance"
    else:
        write_proposal(layout, "reviewed", target="concepts/widget")
        route, params, command = (
            "/v1/wiki/proposal/decide",
            {"target": "concepts/widget", "decision": "approve"},
            "run_proposal_decide",
        )
    http, headers = make_client(layout)
    planned = http.post(f"{route}/plan", json=params, headers=headers).json()
    real = getattr(mutation_specs, command)
    after_external_edit: dict[str, bytes] | None = None

    def edit_then_run(*args: object, **kwargs: object) -> object:
        nonlocal after_external_edit
        if not kwargs["dry_run"] and after_external_edit is None:
            # The engine has already checked its independent dry run. Only
            # change real files; the candidate and projection remain real core.
            if operation == "archive":
                write_item(layout, "work/feature-unreviewed", work_status="resolved", phase="done")
            elif operation == "advance":
                write_item(layout, path, work_status="open", phase="plan", effort="medium")
            else:
                (layout.bundle_dir / "proposals/reviewed.md").unlink()
                write_proposal(layout, "replacement", target="concepts/widget")
            after_external_edit = snapshot(layout)
        return real(*args, **kwargs)

    monkeypatch.setattr(mutation_specs, command, edit_then_run)
    response = http.post(
        f"{route}/apply", json={**params, "as_of": planned["as_of"], "digest": planned["digest"]}, headers=headers
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["reason"] == "stale-plan"
    assert after_external_edit is not None
    assert snapshot(layout) == after_external_edit
    assert not (layout.cache_dir / "active-work.json").exists()
    fresh = response.json()["error"]["payload"]
    assert fresh["as_of"] == planned["as_of"]
    assert fresh["digest"] != planned["digest"]
    if operation == "archive":
        assert fresh["plan"]["path_mapping"] == {
            path: "work/_archive/feature-reviewed",
            "work/feature-unreviewed": "work/_archive/feature-unreviewed",
        }
    elif operation == "advance":
        assert planned["plan"]["phase"] == "plan"
        assert fresh["plan"]["phase"] == "execute"
    else:
        assert fresh["plan"]["proposal"] == "proposals/replacement.md"
    # The response is a usable new review, not a post-write error.
    retry = http.post(
        f"{route}/apply", json={**params, "as_of": fresh["as_of"], "digest": fresh["digest"]}, headers=headers
    )
    assert retry.status_code == 200, retry.text
