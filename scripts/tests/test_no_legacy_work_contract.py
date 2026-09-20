"""Guard the path-native work-item cutover against stale runtime contracts."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_RUNTIME_TEXT = (
    "workflow_status",
    "01-design-spec.md",
    "02-plan-plan.md",
    "--slug-words",
    "--depends-on",
    "adopt-child-specs",
    "work/<YYYY-MM-DD>",
    "### `kind` (work)",
)
PLUGIN_ROOT = ROOT / "plugins" / "gw"
SOURCE_SUFFIXES = {".json", ".md", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml"}
LEGACY_DATE_WORK_PATH = re.compile(
    r"(?:wiki/)?work/(?:[a-z0-9_.-]+/)*(?:19|20)\d{2}-\d{2}-\d{2}[-/]",
    re.ASCII,
)
LEGACY_EXCLUSIONS = (
    # The harvested copy and its carried-over suite (D-013). These stay until
    # the script itself is retired (mono-repo cutover, a follow-on item).
    ROOT / "scripts" / "migrate_vault_work.py",
    ROOT / "scripts" / "tests" / "test_migrate_vault_work.py",
    # The sweep driver's own test suite exercises the legacy graph-wiki dialect
    # it converts away from -- synthetic fixtures necessarily spell out the
    # retired `work/<YYYY-MM-DD>-.../NN-*.md` shape and its stage-document
    # filenames as literal strings. Stays until migrate_vault.py is retired.
    ROOT / "scripts" / "tests" / "test_migrate_vault.py",
    # convert_wikilinks.py's ladder step 2 repairs legacy dated work-item
    # slugs (`work/2026-08-11-epic-...`); its test suite necessarily spells
    # out that retired shape as literal wikilink targets in fixtures. Stays
    # until the legacy-slug repair step is retired.
    ROOT / "scripts" / "tests" / "test_convert_wikilinks.py",
    # D-025/D-043: the relocated boundary test (`test_migration.py`'s
    # `_legacy_boundary_violations`) permanently scans package source for the
    # retired `workflow_status` key by AST identifier -- unlike the entries
    # above, this exclusion does not retire with anything: the guard itself
    # must keep naming the token it guards against.
    ROOT / "packages" / "work-tracker-okf" / "tests" / "test_legacy_boundary.py",
    # Same rationale one level up: the package's own AGENTS.md documents that
    # boundary guard, and cannot describe which retired marker the guard scans
    # for without naming it. Like the guard itself, this exclusion does not
    # retire with anything.
    ROOT / "packages" / "work-tracker-okf" / "AGENTS.md",
)
HARVESTED_LEGACY_FIXTURE = ROOT / "scripts" / "tests" / "fixtures" / "legacy_graph_wiki"


def _active_files() -> list[Path]:
    packages = ROOT / "packages"
    package_files = (
        path
        for path in packages.rglob("*")
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(packages).parts)
    )
    scripts = ROOT / "scripts"
    script_files = (
        path
        for path in scripts.rglob("*")
        if path.is_file()
        and path.resolve() != Path(__file__).resolve()
        and path.suffix in SOURCE_SUFFIXES
        and not any(part.startswith(".") for part in path.relative_to(scripts).parts)
    )
    plugin_files = (
        path
        for path in PLUGIN_ROOT.rglob("*")
        if path.is_file() and "node_modules" not in path.relative_to(PLUGIN_ROOT).parts
    )
    return sorted(
        {
            *(path for path in package_files if path.suffix in SOURCE_SUFFIXES),
            *script_files,
            *plugin_files,
        }
    )


def test_active_runtime_surfaces_use_only_the_path_native_work_contract() -> None:
    """A retired work-item token in an active runtime surface is a cutover regression."""
    findings: list[str] = []

    for path in _active_files():
        if path in LEGACY_EXCLUSIONS or path.is_relative_to(HARVESTED_LEGACY_FIXTURE):
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if match := LEGACY_DATE_WORK_PATH.search(line):
                findings.append(f"{path.relative_to(ROOT)}:{line_number}: {match.group(0)}")
            for forbidden in FORBIDDEN_RUNTIME_TEXT:
                if forbidden in line:
                    findings.append(f"{path.relative_to(ROOT)}:{line_number}: {forbidden}")

    assert findings == [], "stale work-item runtime contracts:\n" + "\n".join(findings)


def test_legacy_date_work_path_pattern_covers_active_and_archived_layouts() -> None:
    samples = (
        "work/2026-08-11-epic-example",
        "work/_archive/2026-08-11-epic-example",
        "work/release-example/children/2026-08-11-epic-example",
        "work/_archive/epic-example/children/_archive/2026-08-11-feature-example",
        "wiki/work/2026-08-11-epic-example",
        "wiki/work/_archive/2026-08-11-epic-example",
    )

    assert all(LEGACY_DATE_WORK_PATH.search(sample) is not None for sample in samples)


def test_active_plugin_scan_covers_every_maintained_surface() -> None:
    paths = {path.relative_to(ROOT).as_posix() for path in _active_files()}

    assert {
        "plugins/gw/.claude-plugin/plugin.json",
        "plugins/gw/hooks/session-start",
        "plugins/gw/hooks/skill-doc-routing",
        "plugins/gw/hooks/examples/session-end-transcript-capture.py",
        "plugins/gw/skills/ingest/SKILL.md",
        "plugins/gw/skills/using-graph-works/SKILL.md",
        "plugins/gw/skills/shared/resolve-workspace.sh",
        "plugins/gw/tests/test-entry-point-skills.sh",
    } <= paths


def test_transcript_capture_resolves_the_active_canonical_path() -> None:
    """Transcript capture must derive owned references from the active path."""
    module = (
        ROOT
        / "packages"
        / "graph-works-core"
        / "src"
        / "graph_works_core"
        / "transcript_capture.py"
    )
    text = module.read_text(encoding="utf-8")

    assert 'work_path = pointer["path"]' in text
    assert "location = parse_item_path(work_path)" in text
    assert 'layout.bundle_dir / location.path / "references"' in text


def test_public_managed_artifact_payloads_do_not_use_legacy_doc_keys() -> None:
    paths = (
        ROOT / "packages" / "graph-works-cli" / "src" / "graph_works_cli" / "work_cli" / "rendering.py",
        ROOT / "plugins" / "gw" / "skills" / "workflow" / "SKILL.md",
    )
    findings = [str(path.relative_to(ROOT)) for path in paths if "spec_doc" in path.read_text(encoding="utf-8")]
    findings += [str(path.relative_to(ROOT)) for path in paths if "plan_doc" in path.read_text(encoding="utf-8")]
    assert findings == []


def test_private_descriptor_loader_is_versioned_as_a_synchronized_dependency() -> None:
    manifests = {
        name: ROOT / "packages" / name / "pyproject.toml"
        for name in ("okf-io", "work-tracker-okf", "graph-works-core", "graph-works-cli")
    }
    with manifests["okf-io"].open("rb") as handle:
        assert tomllib.load(handle)["project"]["version"] == "0.2.5"

    for name in ("work-tracker-okf", "graph-works-core", "graph-works-cli"):
        with manifests[name].open("rb") as handle:
            dependencies = tomllib.load(handle)["project"]["dependencies"]
        assert "okf-io>=0.2.5,<0.3" in dependencies, name
