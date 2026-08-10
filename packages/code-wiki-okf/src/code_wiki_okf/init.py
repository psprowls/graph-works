"""Install `code-wiki-okf`'s own files into an OKF v0.2 bundle, additively.

**The narrowed contract.** This module used to be fresh-only: *"any existing
file in the target is a refusal ... no `--force`, no reconcile"*. That was
right while `code-wiki-okf` was the only package writing into a bundle. It is
not right now that three are designed to share one, so the refusal narrows
from *"the directory is non-empty"* to *"a file I own exists with content I
did not write."* Everything the wider contract protected is still protected:
nothing here can clobber anything, and a refusal still names the obstruction
and writes nothing over it.

`index.md`, `log.md` and `_tags.yaml` are no longer this package's to write.
They belong to `okf_ext.bundle`'s scaffold, which every tier-3 package calls
and any of them may be the first to run. `_tags.yaml` in particular is the
vault's vocabulary, not a package's, so the collision is removed rather than
arbitrated.
"""

from __future__ import annotations

import importlib.resources
import json
from dataclasses import dataclass
from datetime import date
from importlib.resources.abc import Traversable
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

#: Every file this package owns, bundle-relative posix, in write order.
#: `_tags.yaml` is deliberately absent -- see the module docstring.
SEED_RELATIVE_PATHS: tuple[str, ...] = (
    "_schema/Package.schema.json",
    "_schema/App.schema.json",
    "_schema/Dependency.schema.json",
    "_schema/TestSuite.schema.json",
    "_schema/Repository.schema.json",
    "_schema/AgentPlugin.schema.json",
    "_schema/File.schema.json",
    "_sections/Package.yaml",
    "_sections/App.yaml",
    "_sections/Dependency.yaml",
    "_sections/TestSuite.yaml",
    "_sections/Repository.yaml",
    "_sections/AgentPlugin.yaml",
    "_sections/File.yaml",
    "_repositories.yaml",
)

#: The one member above that is the human's from birth. Seeded when absent and
#: never compared: byte-comparing someone's own configuration file and
#: refusing on their edits would invert the ownership the refusal protects.
SEED_ONLY: tuple[str, ...] = ("_repositories.yaml",)


class InitError(ValueError):
    """An install refused for a reason that is not bundle content.

    One case remains: a root that exists and is not a directory. "Not empty"
    is no longer one of them -- narrowing that refusal is what this module was
    rewritten for.
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
        reports "changed" every time tells a human nothing. A failed log
        append does not flip this back to `False`: it is only ever attempted
        once `install.written` is non-empty, so the 15 owned files already
        landed on disk by the time there is anything left to fail."""
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


def _assets_root() -> Traversable:
    return importlib.resources.files("code_wiki_okf") / "assets"


def seed_files(*, declarations_dir: str | Path | None = None) -> dict[str, str]:
    """Every file this package installs, read from its own package data.

    When *declarations_dir* is given, the `_repositories.yaml` template is
    stamped with it, so every later command reads the same answer without the
    flag being retyped -- at `init` there is no config file yet to hold it, so
    the flag is the only place it can come from and the file it writes is the
    only place it can go. Appended rather than substituted into the template's
    commented-out line: a sentinel replace needs a "sentinel missing" branch
    that can only fire on a packaging defect, and unreachable code costs
    coverage against a 95% floor.
    """
    assets = _assets_root()
    files = {relative: (assets / relative).read_text(encoding="utf-8") for relative in SEED_RELATIVE_PATHS}
    if declarations_dir is not None:
        resolved = Path(declarations_dir).resolve().as_posix()
        # `json.dumps` is the minimal correct YAML double-quoted scalar: an
        # unquoted plain scalar breaks on an ordinary path containing " #"
        # (read back truncated at the comment) or ": " (unparseable YAML), and
        # a JSON string is always a valid YAML double-quoted one.
        files["_repositories.yaml"] += (
            f"\n# Written by `code-wiki-okf init --config-dir`.\ndeclarations_dir: {json.dumps(resolved)}\n"
        )
    return files


def plan_install(root: str | Path, *, declarations_dir: str | Path | None = None) -> InstallPlan:
    """Plan this package's own files into *root*.

    A thin wrapper over `okf_ext.bundle.plan_install`: the skip/refuse
    semantics are tier 2's, and all this adds is which files are
    `code-wiki-okf`'s and which one of them is the human's.
    """
    return plan_bundle_install(
        root,
        seed_files(declarations_dir=declarations_dir),
        seed_only=SEED_ONLY,
        declarations_dir=declarations_dir,
    )


def _preview(plan: Plan) -> ApplyResult:
    """The `ApplyResult` *plan* would produce, with nothing written.

    Reusing `ApplyResult` rather than inventing a preview type is what keeps
    `--dry-run` and a real run printing through one code path. The one thing
    it means differently is `written`, which reads "would write" under a dry
    run -- the caller already prints that distinction.
    """
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
    The log line is appended only when the install actually wrote something --
    in a bundle three packages share, `log.md` then reads as a record of each
    arrival, which is what a human opening a shared bundle wants it to say.

    A `log.md` the scaffold could not write is never appended to --
    `scaffold.failed` already names that obstruction. One that *is* present
    and parses can still fail the append itself: out-of-order dated sections,
    a permissions problem, anything else `okf_io.append_log_entry` can raise.
    That failure is caught and reported as `log_failure`, never raised --
    content never raises here, and 15 files have already landed by that point.

    `today` is injected -- nothing below `cli.py` reads the clock. `dry_run`
    defaults to `True`, matching okf-io's writer convention: the default call
    plans, and only an explicit `dry_run=False` touches disk.

    Raises `InitError` only for a *root* that exists and is not a directory.
    That is caller error, not bundle content -- the same line `ConfigError`
    draws.
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
            from code_wiki_okf import __version__  # deferred: no dependency on __init__.py's assignment order

            entry = f"installed by code-wiki-okf/{__version__}"
            try:
                append_log_entry(log_document, entry, today=today, dry_run=False)
            except (ValueError, OSError) as exc:
                # Bounding the call, not enumerating its preconditions: a
                # parsed, present `log.md` can still fail to accept an append
                # for reasons discovered only by attempting it -- out-of-order
                # dated sections (`ValueError`) or a permissions problem
                # (`OSError`). Either way this is content, so it is reported,
                # not raised.
                #
                # The two kinds differ in what a caller should do next, which
                # is what `FailureKind` is read for: an `OSError` is
                # `commit-error`, which that union documents as worth retrying
                # as-is, and fixing the mode and re-running does work. Retrying
                # a disordered log would fail identically forever -- the file
                # needs a human -- so it is reported as content instead.
                kind: FailureKind = "commit-error" if isinstance(exc, OSError) else "foreign-content"
                log_failure = WriteFailure(path="log.md", kind=kind, error=str(exc))
            else:
                logged = entry

    return BundleInstall(
        root=root, scaffold=scaffold_result, install=install_result, logged=logged, log_failure=log_failure
    )


__all__ = [
    "SEED_ONLY",
    "SEED_RELATIVE_PATHS",
    "BundleInstall",
    "InitError",
    "install_bundle",
    "plan_install",
    "seed_files",
]
