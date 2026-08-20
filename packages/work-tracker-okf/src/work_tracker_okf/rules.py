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
`RuleContext` deliberately carries no filesystem, and two of the 31 codes are
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


def lane_rules(*, repo_root: Path | None = None) -> tuple[Rule, ...]:
    """The lane's rule functions, in topic order.

    *repo_root* is where `affects` entries and plan-action tokens resolve.
    `None` **skips** `targets.affects-missing` and `plan.action-target-missing`
    rather than reporting them as failures -- work-io's behaviour, and the right
    one: not knowing where the repo is says nothing about whether the paths are
    good.

    Topic order rather than registration order, matching `okf_io`'s built-in
    rule ordering. It does not affect output -- `validate()` sorts findings
    after collection -- but a catalog whose iteration order is a dict literal's
    is a catalog that reorders when someone reformats it.
    """
    config = LaneConfig(repo_root=repo_root)
    return tuple(rule for topic in sorted(RULES_BY_TOPIC) for rule in RULES_BY_TOPIC[topic](config))


__all__ = ["CATALOG", "CODES_BY_TOPIC", "PLAN_TABLE_SPEC", "TOPICS", "LaneConfig", "lane_rules"]
