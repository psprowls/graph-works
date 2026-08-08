"""The tracked-minus-ignore file walk, per configured repo.

Thin on purpose: `git_state.ls_files` already is the walk (each
`RepoConfig.ignore` pattern becomes a `:(exclude,glob)` pathspec git owns the
matching for). This module's only job is running it once per repo and
handing back a name-keyed mapping.
"""

from __future__ import annotations

from code_wiki_okf.config import Config
from code_wiki_okf.git_state import ls_files


def tracked_files(config: Config) -> dict[str, tuple[str, ...]]:
    """Every configured repo's tracked-minus-ignore paths, sorted.

    A repo `ls_files` cannot read (not a git checkout) contributes an empty
    tuple rather than raising — `git_state`'s own "never raise" contract,
    preserved one level up.
    """
    result: dict[str, tuple[str, ...]] = {}
    for repo in config.repos:
        found = ls_files(repo.path, exclude=repo.ignore)
        result[repo.name] = tuple(found) if found is not None else ()
    return result
