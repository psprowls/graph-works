"""Plain-data projections for `gw repo` results. Key by key, never `asdict`."""

from __future__ import annotations

from graph_works_core.repositories.adopt import RepoAdoptResult
from graph_works_core.repositories.commands import RepoAddResult, RepoAdvanceResult, RepoRefusal, RepoRestoreResult

from graph_works_wire._jsonable import commit_payload


def _refusal(refusal: RepoRefusal | None) -> dict[str, object] | None:
    return None if refusal is None else {"code": refusal.code, "detail": refusal.detail}


def repo_add_payload(result: RepoAddResult) -> dict[str, object]:
    return {
        "name": result.name,
        "url": result.url,
        "track": result.track,
        "ref": result.ref,
        "commit": result.commit,
        "describe": result.describe,
        "managed": result.managed,
        "checkout": result.checkout,
        "paths": list(result.paths),
        "dry_run": result.dry_run,
        "ok": result.ok,
        "refusal": _refusal(result.refusal),
        "workspace_commit": commit_payload(result.commit_outcome),
        "warnings": list(result.warnings),
    }


def repo_restore_payload(result: RepoRestoreResult) -> dict[str, object]:
    return {
        "dry_run": result.dry_run,
        "ok": result.ok,
        "refusal": _refusal(result.refusal),
        "outcomes": [
            {
                "name": outcome.name,
                "outcome": outcome.outcome,
                "commit": outcome.commit,
                "refusal": _refusal(outcome.refusal),
                "checkout": outcome.checkout,
            }
            for outcome in result.outcomes
        ],
    }


def repo_advance_payload(result: RepoAdvanceResult) -> dict[str, object]:
    facts = result.range
    return {
        "name": result.name,
        "outcome": result.outcome,
        "previous": result.previous,
        "commit": result.commit,
        "describe": result.describe,
        "managed": result.managed,
        "commits": result.commits,
        "dry_run": result.dry_run,
        "ok": result.ok,
        "range": None
        if facts is None
        else {
            "old": facts.old,
            "new": facts.new,
            "base": facts.base,
            "rewritten": facts.rewritten,
            "commits": facts.commits,
            "files_changed": facts.files_changed,
            "tags": list(facts.tags),
            "merges": list(facts.merges),
            "changes": [{"status": c.status, "path": c.path, "renamed_to": c.renamed_to} for c in facts.changes],
        },
        "flagged": [
            {
                "page": page.page,
                "title": page.title,
                "links": [
                    {
                        "target": link.target,
                        "changed": link.changed,
                        "status": link.status,
                        "renamed_to": link.renamed_to,
                    }
                    for link in page.links
                ],
            }
            for page in result.flagged
        ],
        "paths": list(result.paths),
        "proposals": list(result.proposals),
        "skipped": list(result.skipped),
        "refusal": _refusal(result.refusal),
        "workspace_commit": commit_payload(result.commit_outcome),
        "warnings": list(result.warnings),
    }


def repo_adopt_payload(result: RepoAdoptResult) -> dict[str, object]:
    return {
        "name": result.name,
        "source": result.source,
        "clone": result.clone,
        "checkout": result.checkout,
        "track": result.track,
        "commit": result.commit,
        "checkout_created": result.checkout_created,
        "repaired": list(result.repaired),
        "paths": list(result.paths),
        "dry_run": result.dry_run,
        "ok": result.ok,
        "refusal": _refusal(result.refusal),
        "workspace_commit": commit_payload(result.commit_outcome),
        "warnings": list(result.warnings),
    }


__all__ = ["repo_add_payload", "repo_adopt_payload", "repo_advance_payload", "repo_restore_payload"]
