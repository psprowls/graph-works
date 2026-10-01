"""Local upstreams for git tests: a work repository pushing to a bare remote served over `file://`.

`file://` (not a plain path) is what makes `--filter=blob:none` a real partial clone: a local-path clone ignores
the filter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT = shutil.which("git") or "git"
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [GIT, *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def missing_objects(repo: Path) -> int:
    """Objects a partial clone knows of but has not fetched (`?`-prefixed in `--missing=print`)."""
    out = git(repo, "rev-list", "--objects", "--all", "--missing=print")
    return sum(1 for line in out.splitlines() if line.startswith("?"))


@dataclass
class Upstream:
    work: Path
    bare: Path

    @property
    def url(self) -> str:
        return self.bare.as_uri()

    def commit(
        self, files: dict[str, str | None], message: str, *, tag: str | None = None, branch: str = "main"
    ) -> str:
        for rel, text in files.items():
            if text is None:
                git(self.work, "rm", "-q", rel)
                continue
            path = self.work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="")
            git(self.work, "add", rel)
        git(self.work, "commit", "-q", "-m", message)
        if tag is not None:
            git(self.work, "tag", "-a", tag, "-m", tag)
        git(self.work, "push", "-q", "--follow-tags", "origin", f"HEAD:{branch}")
        return git(self.work, "rev-parse", "HEAD")

    def rename(self, old: str, new: str, message: str) -> str:
        git(self.work, "mv", old, new)
        git(self.work, "commit", "-q", "-m", message)
        git(self.work, "push", "-q", "origin", "HEAD:main")
        return git(self.work, "rev-parse", "HEAD")

    def merge(self, branch: str, files: dict[str, str], message: str) -> str:
        """A first-parent merge commit on main, bringing *branch*'s one commit in."""
        git(self.work, "checkout", "-q", "-b", branch)
        self.commit(files, f"{message} (branch)", branch=branch)
        git(self.work, "checkout", "-q", "main")
        git(self.work, "merge", "-q", "--no-ff", "-m", message, branch)
        git(self.work, "push", "-q", "origin", "HEAD:main")
        return git(self.work, "rev-parse", "HEAD")

    def rewrite(self, files: dict[str, str | None], message: str) -> str:
        """Drop the tip and force-push a different one: `old` is no longer an ancestor of the new tip."""
        git(self.work, "reset", "-q", "--hard", "HEAD~1")
        for rel, text in files.items():
            if text is None:
                git(self.work, "rm", "-q", rel)
                continue
            path = self.work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="")
            git(self.work, "add", rel)
        git(self.work, "commit", "-q", "-m", message)
        git(self.work, "push", "-q", "--force", "origin", "HEAD:main")
        return git(self.work, "rev-parse", "HEAD")


def make_upstream(tmp: Path, *, default_branch: str = "main") -> Upstream:
    bare = tmp / "upstream.git"
    work = tmp / "upstream-work"
    git(tmp, "init", "-q", "--bare", "-b", default_branch, str(bare))
    git(bare, "config", "uploadpack.allowFilter", "true")
    git(bare, "config", "uploadpack.allowAnySHA1InWant", "true")
    git(tmp, "init", "-q", "-b", "main", str(work))
    git(work, "remote", "add", "origin", str(bare))
    return Upstream(work, bare)
