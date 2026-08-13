"""A skill directory gathered into one markdown blob.

A skill is frequently a directory: a SKILL.md that links out to companion
reference markdown. `gather_skill_sources` follows those links transitively and
reports the non-markdown files it did not read.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from doc_wiki_okf.reading.links import iter_link_targets, resolve_companion


@dataclass(frozen=True)
class SkillBundle:
    """Result of gathering a skill directory into one combined markdown blob.

    Fields:
        combined_text:   SKILL.md, then linked companion files in DFS link order,
                         each prefixed with an `<!-- skill-file: <rel> -->` marker.
        skill_dir:       the resolved directory containing the anchor SKILL.md.
        anchor:          the resolved SKILL.md the bundle is anchored on.
        title:           SKILL.md frontmatter `name:` -> first `# ` heading -> None.
        included_files:  skill_dir-relative POSIX paths, SKILL.md first, DFS order.
        excluded_files:  every non-.md file under skill_dir (POSIX rel, sorted).
        scripts_dominant: True when a top-level `scripts/` dir exists OR there are
                         more excluded files than included.
    """

    combined_text: str
    skill_dir: Path
    anchor: Path
    title: str | None
    included_files: tuple[str, ...]
    excluded_files: tuple[str, ...]
    scripts_dominant: bool


def resolve_skill_anchor(source_path: Path) -> Path | None:
    """Return the SKILL.md to anchor a skill ingest on, or None.

    - a directory containing `SKILL.md` -> `<dir>/SKILL.md`
    - a file named `SKILL.md`           -> the file itself
    - anything else                     -> None (caller falls back to the file path)
    """
    if source_path.is_dir():
        candidate = source_path / "SKILL.md"
        return candidate if candidate.is_file() else None
    if source_path.is_file() and source_path.name == "SKILL.md":
        return source_path
    return None


def _skill_title(anchor_text: str) -> str | None:
    """Title from a SKILL.md: frontmatter `name:` -> first `# ` heading -> None.

    Stdlib-only (this subpackage takes no dependency): the frontmatter `name:`
    is read line-by-line from the leading `---`-fenced block.
    """
    stripped = anchor_text.lstrip()
    if stripped.startswith("---"):
        after = stripped[3:].lstrip("\n")
        end = after.find("\n---")
        if end != -1:
            for line in after[:end].splitlines():
                if line.strip().startswith("name:"):
                    value = line.split(":", 1)[1].strip().strip("\"'")
                    if value:
                        return value
    for line in anchor_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def gather_skill_sources(anchor: Path) -> SkillBundle:
    """Gather a skill directory into one combined markdown blob.

    Reads `anchor` (a SKILL.md) plus every companion `.md` it links to,
    transitively, and concatenates them with `<!-- skill-file: <rel> -->`
    markers. Non-markdown files under the skill directory are recorded in
    `excluded_files` (not read).
    """
    skill_dir = anchor.parent.resolve()

    # DFS preorder from the anchor, visited-set keyed by resolved abs path so
    # cycles terminate and each file is included at most once.
    visited: set[Path] = set()
    included: list[tuple[Path, str]] = []  # (resolved_abs_path, content), DFS order

    def visit(md_file: Path) -> None:
        resolved = md_file.resolve()
        if resolved in visited:
            return
        visited.add(resolved)
        content = resolved.read_text(encoding="utf-8", errors="replace")
        included.append((resolved, content))
        for target in iter_link_targets(content):
            child = resolve_companion(target, resolved.parent, skill_dir)
            if child is not None:
                visit(child)

    visit(anchor)

    parts: list[str] = []
    for abs_path, content in included:
        rel = abs_path.relative_to(skill_dir).as_posix()
        parts.append(f"<!-- skill-file: {rel} -->\n{content}")
    combined_text = "\n\n".join(parts)

    included_files = tuple(abs_path.relative_to(skill_dir).as_posix() for abs_path, _ in included)
    excluded_files = tuple(
        sorted(
            p.relative_to(skill_dir).as_posix()
            for p in skill_dir.rglob("*")
            if p.is_file() and p.suffix.lower() != ".md"
        )
    )
    scripts_dominant = (skill_dir / "scripts").is_dir() or len(excluded_files) > len(included_files)

    return SkillBundle(
        combined_text=combined_text,
        skill_dir=skill_dir,
        anchor=anchor.resolve(),
        title=_skill_title(included[0][1]),
        included_files=included_files,
        excluded_files=excluded_files,
        scripts_dominant=scripts_dominant,
    )
