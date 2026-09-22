"""The lane's rule bundle, as one `extra_rules=` argument.

    from okf_io import load_bundle, validate
    from work_tracker_okf import IGNORE
    from work_tracker_okf.rules import lane_rules

    report = validate(
        load_bundle(root, ignore=IGNORE),
        today=today,
        extra_rules=lane_rules(repo_root=repo_root),
    )

A **factory per capability** rather than okf-io's module-level `RULES` tuple:
`RuleContext` deliberately carries no filesystem, and two of the 37 codes are
questions about a repository. okf-ext's `schema_rule` / `section_rule` /
`health_rule` are the precedent this follows.

A submodule rather than a name at the package's front door, for the reason
`vocabulary`, `workflow` and `filing` already are: the module name is what says
which `rules` is meant.
"""

from __future__ import annotations

from pathlib import Path

from okf_io import Rule

from work_tracker_okf._rules import CATALOG, CODES_BY_TOPIC, RULES_BY_TOPIC, TOPICS
from work_tracker_okf._rules._common import LaneConfig
from work_tracker_okf._rules.plan import PLAN_TABLE_SPEC


def lane_rules(
    *, repo_root: Path | None = None, vault_root: Path | None = None, repo_roots: tuple[Path, ...] = ()
) -> tuple[Rule, ...]:
    """The lane's rule functions, in topic order.

    *repo_root* is where `affects` entries resolve; *repo_roots* is the same
    for a workspace declaring several code repositories -- an entry resolving
    under any of them (or under *repo_root*) is good. *vault_root* is the
    bundle root a plan-action token resolves under -- two different
    directories from *repo_root* in a split topology (workspace and code
    repo are different git repos). Either being
    `None` **skips** its rule (`targets.affects-missing` /
    `plan.action-target-missing` respectively; no repo root means *repo_root*
    `None` and *repo_roots* empty) rather than reporting it as a
    failure -- work-io's behaviour, and the right one: not knowing where a
    root is says nothing about whether the paths under it are good.

    Topic order rather than registration order, matching `okf_io`'s built-in
    rule ordering. It does not affect output -- `validate()` sorts findings
    after collection -- but a catalog whose iteration order is a dict literal's
    is a catalog that reorders when someone reformats it.
    """
    config = LaneConfig(repo_root=repo_root, vault_root=vault_root, repo_roots=repo_roots)
    return tuple(rule for topic in sorted(RULES_BY_TOPIC) for rule in RULES_BY_TOPIC[topic](config))


__all__ = ["CATALOG", "CODES_BY_TOPIC", "PLAN_TABLE_SPEC", "TOPICS", "LaneConfig", "lane_rules"]
