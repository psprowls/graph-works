from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

from .git import GitError
from .machine import Services
from .records import Finding, JsonValue, Result, SourceSpec
from .references import dependency_json, scan_host_metadata, scan_markdown
from .snapshots import SnapshotError, capture
from .validation import metadata_json, parse_skill_metadata


def inspect_source(source: SourceSpec, *, services: Services) -> Result:
    if source.kind == "local" and not Path(source.locator).is_dir():
        finding = Finding("source.missing", "error", source.locator, None, "Local source directory does not exist")
        return _result(False, (finding,), [])
    try:
        snapshot = capture(source, (), services=services)
    except (GitError, SnapshotError) as exc:
        code = exc.code if isinstance(exc, GitError) else "source.unsafe"
        return _result(False, (Finding(code, "error", None, None, str(exc)),), [])
    findings: list[Finding] = []
    skills: list[JsonValue] = []
    for entry in snapshot.entries:
        if Path(entry.path).name != "SKILL.md":
            continue
        if entry.kind != "file":
            findings.append(Finding("source.link", "warn", entry.path, None, "Skill entry is evidence only"))
            continue
        metadata, problems = parse_skill_metadata(entry.content, path=entry.path)
        findings.extend(problems)
        if metadata is not None:
            skills.append(metadata_json(metadata, Path(entry.path).parent.as_posix()))
    dependencies: list[JsonValue] = []
    for entry in snapshot.entries:
        if entry.kind != "file" or any(f.path == entry.path and f.severity == "error" for f in findings):
            continue
        if entry.path.endswith(".md"):
            found, problems = scan_markdown(entry.content, entry.path)
        elif entry.path.endswith("agents/openai.yaml"):
            found, problems = scan_host_metadata(entry.content, entry.path)
        else:
            continue
        dependencies.extend(dependency_json(dependency) for dependency in found)
        findings.extend(problems)
    result = _result(not any(f.severity == "error" for f in findings), tuple(findings), skills)
    entries: list[JsonValue] = [
        {"path": e.path, "kind": e.kind, "mode": e.mode, "hash": e.hash} for e in snapshot.entries
    ]
    return replace(
        result,
        data={
            "skills": skills,
            "source": asdict(snapshot.source),
            "digest": snapshot.digest,
            "entries": entries,
            "dependencies": dependencies,
        },
    )


def _result(allowed: bool, findings: tuple[Finding, ...], skills: list[JsonValue]) -> Result:
    return Result(
        operation="inspect",
        variant_id=None,
        preview_id=None,
        applied=False,
        allowed=allowed,
        findings=findings,
        affected_paths=(),
        data={"skills": skills},
    )
