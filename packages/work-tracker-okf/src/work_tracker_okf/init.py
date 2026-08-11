"""Install `work-tracker-okf`'s own files into an OKF v0.2 bundle, additively.

Three acts, all idempotent: `plan_scaffold` -> `plan_install` -> one `log.md`
line when the install actually wrote something. In a bundle three tier-3
packages share, `log.md` then reads as a record of each arrival, which is what
a human opening a shared bundle wants it to say.

**There is no `SEED_ONLY` here.** `code-wiki-okf` exempts `_repositories.yaml`
from byte comparison because that file is the human's from birth; this package
ships no such file, so all fourteen members are owned templates -- created when
absent, skipped when byte-identical, refused per file when they differ.
`index.md`, `log.md` and `_tags.yaml` belong to `okf_ext.bundle`'s scaffold,
not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from okf_ext.bundle import (
    ApplyResult,
    FailureKind,
    InstallPlan,
    Plan,
    ScaffoldPlan,
    WriteFailure,
    apply,
    plan_scaffold,
)
from okf_ext.bundle import plan_install as plan_bundle_install
from okf_io import append_log_entry, load_bundle

from work_tracker_okf.resources import SEED_RELATIVE_PATHS, seed_files


class InitError(ValueError):
    """An install refused for a reason that is not bundle content.

    Exactly one case: a root that exists and is not a directory. That is caller
    error. Bundle content is never an exception here -- a conflicting file is a
    `WriteFailure` in the result, not a raise.
    """


@dataclass(frozen=True, slots=True)
class BundleInstall:
    """What `install_bundle()` did, in its three acts.

    Shares the `changed` / `diff()` vocabulary `IndexUpdate`, `LogAppend` and
    `Migration` already use: `diff()` renders on demand and writes nothing.
    """

    root: Path
    scaffold: ApplyResult
    install: ApplyResult
    logged: str | None
    log_failure: WriteFailure | None

    @property
    def ok(self) -> bool:
        return self.scaffold.ok and self.install.ok and self.log_failure is None

    @property
    def changed(self) -> bool:
        """False on a re-run, which is the point: an idempotent installer that
        reports "changed" every time tells a human nothing."""
        return bool(self.scaffold.written or self.install.written or self.logged)

    def diff(self) -> str:
        lines = [f"+ {member}" for member in (*self.scaffold.written, *self.install.written)]
        lines += [f"= {item.path}" for item in (*self.scaffold.skipped, *self.install.skipped)]
        lines += [f"! {failure.path}: {failure.error}" for failure in (*self.scaffold.failed, *self.install.failed)]
        if self.log_failure is not None:
            lines.append(f"! {self.log_failure.path}: {self.log_failure.error}")
        if self.logged is not None:
            lines.append(f"+ log.md: {self.logged}")
        return "\n".join(lines)


def plan_install(root: str | Path, *, declarations_dir: str | Path | None = None) -> InstallPlan:
    """Plan this package's fourteen files into *root*.

    A thin wrapper over `okf_ext.bundle.plan_install`: the skip/refuse
    semantics are tier 2's, and all this adds is which files are
    `work-tracker-okf`'s. No `seed_only=` -- this package owns every member it
    ships.
    """
    return plan_bundle_install(root, seed_files(), declarations_dir=declarations_dir)


def _preview(plan: Plan) -> ApplyResult:
    """The `ApplyResult` *plan* would produce, with nothing written -- which is
    what keeps `--dry-run` and a real run printing through one code path."""
    return ApplyResult(
        written=tuple(planned.member for planned in plan.writes),
        failed=plan.refusals,
        skipped=plan.skipped,
    )


def install_bundle(
    root: str | Path,
    *,
    today: date,
    declarations_dir: str | Path | None = None,
    dry_run: bool = True,
) -> BundleInstall:
    """Scaffold *root*, install this package's files into it, log the arrival.

    All three acts are idempotent, so installing into a bundle another package
    created simply works, and a second run writes nothing and refuses nothing.

    `today` is injected -- nothing below `cli.py` reads the clock. `dry_run`
    defaults to `True`, matching okf-io's writer convention.

    Raises `InitError` only for a *root* that exists and is not a directory.
    """
    root = Path(root)
    if root.exists() and not root.is_dir():
        raise InitError(f"{root}: exists and is not a directory")

    scaffold: ScaffoldPlan = plan_scaffold(root, today=today, declarations_dir=declarations_dir)
    install = plan_install(root, declarations_dir=declarations_dir)

    if dry_run:
        return BundleInstall(
            root=root, scaffold=_preview(scaffold), install=_preview(install), logged=None, log_failure=None
        )

    scaffold_result = apply(scaffold)
    install_result = apply(install)

    logged: str | None = None
    log_failure: WriteFailure | None = None
    if install_result.written:
        log_document = load_bundle(root).logs.get("")
        if log_document is not None and log_document.parse_error is None:
            from work_tracker_okf import __version__  # deferred: no dependency on __init__.py's assignment order

            entry = f"installed by work-tracker-okf/{__version__}"
            try:
                append_log_entry(log_document, entry, today=today, dry_run=False)
            except (ValueError, OSError) as exc:
                # A parsed, present `log.md` can still refuse an append for
                # reasons only the attempt discovers -- out-of-order dated
                # sections (`ValueError`) or a permissions problem (`OSError`).
                # An `OSError` is retry-worthy as-is; a disordered log needs a
                # human, so it is reported as content.
                kind: FailureKind = "commit-error" if isinstance(exc, OSError) else "foreign-content"
                log_failure = WriteFailure(path="log.md", kind=kind, error=str(exc))
            else:
                logged = entry

    return BundleInstall(
        root=root, scaffold=scaffold_result, install=install_result, logged=logged, log_failure=log_failure
    )


__all__ = [
    "SEED_RELATIVE_PATHS",
    "BundleInstall",
    "InitError",
    "install_bundle",
    "plan_install",
    "seed_files",
]
