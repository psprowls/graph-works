"""Top-level mirror-lane sync: one `plan_mirror` / `apply_mirror` pass per
configured repo, plus one `log.md` entry per run. The mirror half of `sync`,
lifted out of `cli.py` so a second caller (`graph_works_core.scan`) can reach
it at all.

That extraction is the whole point. The entity half has always been a library
function (`entities/lanes.py::sync`) and the mirror half has always been an
inline loop in a CLI command body, so `graph_works_core.scan` could import one
and not the other -- it ran the entity lane, rendered `## Files` links into the
mirror lane, and left every one of them broken. Same rule ADR-0013 settled one
band up: a command module is a library; the CLI routes, parses and formats.

**`dry_run=True` plans and returns the plans, writing nothing.** That is the
opposite of `entities.lanes.sync`, whose `dry_run=True` calls nothing at all
and returns an empty summary, and both stances are deliberate.
`sync_entities` and `prune_lane` commit the moment they are called, so
skipping them is the only honest way for that function to promise "nothing
touches disk". `plan_mirror` is genuinely read-only -- `sync/snapshot.py`
already depends on that -- so a dry run here is a real preview, which is what
`code-wiki-okf sync --dry-run`'s per-repo plan output has always shown.

`sync/snapshot.py::snapshot_bundle` runs its own read-only per-repo
`plan_mirror` loop and is deliberately **not** folded in here: it computes
three resource *sets* for a rule to report against, not a plan list for a
writer to apply, and folding it in would put a writer's return type in a
rule's read path. The duplication is the cheaper of the two.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from code_graph_io import GraphReader
from okf_ext.shape import load_sections
from okf_io import append_log_entry, load_bundle

from code_wiki_okf.config import Config
from code_wiki_okf.git_state import head_commit
from code_wiki_okf.mirror.apply import apply_mirror
from code_wiki_okf.mirror.model import MirrorPlan, MirrorResult
from code_wiki_okf.mirror.plan import plan_mirror
from code_wiki_okf.mirror.walk import tracked_files

#: The bundle-root directory id, the key `okf_io.Bundle.logs` uses for it.
_ROOT = ""


@dataclass(frozen=True, slots=True)
class MirrorSummary:
    """What one `sync_mirror` run did, across every configured repo.

    `plans` is populated in both modes -- a dry run is a real preview here.
    `results` is `dry_run=False` only. `skipped_repos` names repos whose
    `head_commit` came back `None` (not a git checkout); `failed_repos`
    pairs a repo name with the error text that stopped it, reported rather
    than raised so one repo's filesystem trouble never costs the others
    their turn.
    """

    plans: tuple[MirrorPlan, ...] = field(default_factory=tuple)
    results: tuple[MirrorResult, ...] = field(default_factory=tuple)
    skipped_repos: tuple[str, ...] = field(default_factory=tuple)
    failed_repos: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (repo_name, error)

    @property
    def ok(self) -> bool:
        return not self.failed_repos

    @property
    def created(self) -> int:
        return sum(len(result.created) for result in self.results)

    @property
    def regenerated(self) -> int:
        return sum(len(result.regenerated) for result in self.results)

    @property
    def moved(self) -> int:
        return sum(len(result.moved) for result in self.results)

    @property
    def deleted(self) -> int:
        return sum(len(result.deleted) for result in self.results)

    @property
    def declined(self) -> int:
        return sum(len(result.declined_deletions) for result in self.results)

    @property
    def stranded(self) -> int:
        """Inbound `[[wikilink]]` references into every repo's moved set that
        `moves` could not repair. Read off `plans`, not `results`: it is a
        plan-time fact and `plans` is populated in both modes."""
        return sum(len(plan.moves.stranded) for plan in self.plans)


def _summary_text(summary: MirrorSummary) -> str:
    """One `log.md` bullet naming this run's counts.

    Wording carried over verbatim from `cli.py`'s inline loop: the log is a
    durable record and reflowing its phrasing would make every historical
    entry read as a different event from every new one.
    """
    return (
        f"mirror sync: {summary.created} created, {summary.regenerated} updated, "
        f"{summary.moved} moved, {summary.deleted} deleted, "
        f"{summary.declined} deletion(s) declined"
    )


def _append_log(bundle_root: Path, summary: MirrorSummary, *, today: date) -> None:
    log_document = load_bundle(bundle_root).logs.get(_ROOT)
    if log_document is None:
        raise ValueError(
            f"{bundle_root}: no root log.md -- every code-wiki-okf bundle is created with one by "
            "okf_ext.bundle's scaffold"
        )
    append_log_entry(log_document, _summary_text(summary), today=today, dry_run=False)


def sync_mirror(
    bundle_root: Path,
    config: Config,
    reader: GraphReader,
    *,
    today: date,
    at: datetime,
    dry_run: bool = True,
) -> MirrorSummary:
    """Run the mirror lane against *bundle_root*, once per configured repo.

    Five bindings here are load-bearing and were preserved verbatim from the
    loop this replaces:

    - **`tracked_files(config)` once, before the loop.** One `git ls-files`
      per repo, not one per file.
    - **`load_bundle(bundle_root)` inside the loop, per repo.** Repo *N*'s
      `apply_mirror` changes the member set repo *N+1*'s `plan_mirror` reads.
    - **`head_commit(...) is None` skips, never raises.** `git_state`'s
      never-raise contract, carried up one level.
    - **`except Exception` per repo.** One repo's filesystem error must not
      abort the others, and must not vanish either.
    - **`load_sections` reads the *bundle's* declarations**, not this
      package's own assets. Normally identical -- the install seeds
      byte-for-byte copies -- but they diverge the moment a human edits the
      bundle's `_sections/File.yaml`, and then `sync` writes pages from one
      shape while `validate` checks them against another. One bundle, one
      answer about what a `File` page looks like. `OSError` / `ValueError`
      from it propagate to the caller, matching `entities.lanes.sync`; a
      library does not decide how a caller-configuration problem is
      presented.
    """
    section_set = load_sections(config.declarations_dir / "_sections")
    walked = tracked_files(config)

    plans: list[MirrorPlan] = []
    results: list[MirrorResult] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []

    for repo in config.repos:
        sha = head_commit(repo.path)
        if sha is None:
            skipped.append(repo.name)
            continue
        try:
            bundle = load_bundle(bundle_root)
            plan = plan_mirror(bundle, reader, repo, tracked=walked[repo.name], sha=sha, at=at)
            plans.append(plan)
            if dry_run:
                continue
            results.append(apply_mirror(bundle, plan, repo, section_set=section_set))
        except Exception as exc:  # reported per repo, never raised
            failed.append((repo.name, str(exc)))

    summary = MirrorSummary(
        plans=tuple(plans),
        results=tuple(results),
        skipped_repos=tuple(skipped),
        failed_repos=tuple(failed),
    )

    # One entry for the whole run, and only when a repo was actually applied
    # -- the same condition `cli.py`'s `any_mirror_write` flag encoded. A dry
    # run writes nothing at all, log included.
    if not dry_run and results:
        _append_log(bundle_root, summary, today=today)
    return summary


__all__ = ["MirrorSummary", "sync_mirror"]
