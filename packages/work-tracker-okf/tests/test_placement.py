"""The pure placement planner that survives computed placement: the execute baseline."""

from __future__ import annotations

from datetime import date

from okf_io import load, load_bundle
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.placement import BASELINE_REFUSALS, apply_baseline, plan_baseline


def test_the_baseline_refusal_vocabulary_is_closed() -> None:
    assert {
        "unknown-path",
        "invalid-item",
        "terminal",
        "not-execute",
        "invalid-baseline",
        "baseline-conflict",
        "no-repo",
        "outside-repository",
        "git-unavailable",
        "wrong-checkout",
        "already-started",
    } == BASELINE_REFUSALS


# --- execute baseline (start_sha) ---

A = "a" * 40
B = "b" * 40
TODAY_B = date(2026, 9, 28)
PATH = "work/bug-x"


def _page(tmp_path, *, phase="execute", worktree=None, branch=None, start_sha=None, repo_stamps=""):
    root = tmp_path / "okf"
    page = root / f"{PATH}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    extra = "".join(
        f"{k}: {v}\n" for k, v in (("worktree", worktree), ("branch", branch), ("start_sha", start_sha)) if v
    )
    page.write_text(
        "---\ntype: Bug\ntitle: x\ndescription: d\nstatus: stable\nwork_status: in-progress\n"
        f"phase: {phase}\neffort: small\nopened: 2026-09-01\nupdated: 2026-09-01\naffects:\n- packages/a\n"
        + extra
        + repo_stamps
        + "---\n\nbody\n",
        encoding="utf-8",
        newline="\n",
    )
    return root, page


def _items(tmp_path, **fm):
    root, _page_path = _page(tmp_path, **fm)
    return load_items(load_bundle(root, ignore=IGNORE)), PATH


def test_baseline_fills_an_absent_scalar(tmp_path):
    items, path = _items(tmp_path)
    plan = plan_baseline(items, path, observed_head=A, head_descends_from_recorded=False, today=TODAY_B)
    assert plan.refusal is None and plan.after == A and plan.changed


def test_baseline_keeps_a_recorded_ancestor(tmp_path):
    items, path = _items(tmp_path, start_sha=A)
    plan = plan_baseline(items, path, observed_head=B, head_descends_from_recorded=True, today=TODAY_B)
    assert plan.refusal is None and plan.after == A and not plan.changed


def test_baseline_refuses_a_diverged_head(tmp_path):
    items, path = _items(tmp_path, start_sha=A)
    plan = plan_baseline(items, path, observed_head=B, head_descends_from_recorded=False, today=TODAY_B)
    assert plan.refusal == "baseline-conflict"


def test_baseline_is_only_recorded_at_execute(tmp_path):
    items, path = _items(tmp_path, phase="plan")
    assert (
        plan_baseline(items, path, observed_head=A, head_descends_from_recorded=False, today=TODAY_B).refusal
        == "not-execute"
    )


def test_apply_baseline_writes_start_sha_and_updated(tmp_path):
    root, page = _page(tmp_path)
    items = load_items(load_bundle(root, ignore=IGNORE))
    document = load(page)
    apply_baseline(
        document, plan_baseline(items, PATH, observed_head=A, head_descends_from_recorded=False, today=TODAY_B)
    )
    assert document.fm_data()["start_sha"] == A
