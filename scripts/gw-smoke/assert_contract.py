"""Assert the deterministic code-wiki placement smoke contract."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from code_graph_io import open_reader
from code_wiki_okf.config import load_config
from code_wiki_okf.placement import placement_rule
from code_wiki_okf.sync import snapshot_bundle, sync_rule
from graph_works_core import resolve
from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import load_sections
from okf_ext.tags import VOCABULARY_FILENAME, load_vocabulary, vocabulary_rule
from okf_io import load_bundle, validate


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: assert_contract.py <workspace>", file=sys.stderr)
        return 2

    workspace = Path(sys.argv[1]).resolve()
    layout = resolve(workspace=workspace)
    config = load_config(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )
    if len(config.repos) != 1:
        print(f"expected one smoke repository, found {len(config.repos)}", file=sys.stderr)
        return 1

    repo = config.repos[0]
    prefix = f"code-graph/{repo.name}"
    expected = {
        "index.md",
        "code-graph/index.md",
        f"code-graph/{repo.name}.md",
        f"{prefix}/index.md",
        f"{prefix}/entities/index.md",
        f"{prefix}/entities/agent-plugins/gw.md",
        f"{prefix}/entities/agent-plugins/index.md",
        f"{prefix}/entities/apps/code-wiki-okf.md",
        f"{prefix}/entities/apps/index.md",
        f"{prefix}/entities/dependencies/index.md",
        f"{prefix}/entities/dependencies/pypi/index.md",
        f"{prefix}/entities/packages/graph-works-cli.md",
        f"{prefix}/entities/packages/index.md",
        f"{prefix}/entities/test-suites/index.md",
        f"{prefix}/entities/test-suites/packages__code-wiki-okf__tests.md",
        f"{prefix}/file-system/index.md",
        f"{prefix}/file-system/packages/code-wiki-okf/README.md.md",
    }
    members = {member.relative_to(layout.bundle_dir).as_posix() for member in layout.bundle_dir.rglob("*.md")}
    missing = sorted(expected - members)
    if missing:
        print("missing canonical smoke members:", file=sys.stderr)
        for member in missing:
            print(f"  {member}", file=sys.stderr)
        return 1
    legacy_roots = sorted(
        lane
        for lane in ("repositories", "dependencies", "files", "agent-plugins", "apps", "packages", "test-suites")
        if any(member.startswith(f"{lane}/") for member in members)
    )
    if legacy_roots:
        print(f"unexpected top-level pre-code-graph directories: {', '.join(legacy_roots)}", file=sys.stderr)
        return 1

    bundle = load_bundle(layout.bundle_dir)
    schema_set = load_schemas(config.declarations_dir / "schema")
    section_set = load_sections(config.declarations_dir / "sections")
    vocabulary = load_vocabulary(config.declarations_dir / VOCABULARY_FILENAME)
    now = datetime.now(UTC)
    with open_reader(graph_dir=config.graph_dir) as reader:
        snapshot = snapshot_bundle(bundle, config, reader, at=now)
    report = validate(
        bundle,
        today=now.date(),
        extra_rules=[
            sync_rule(snapshot),
            schema_rule(schema_set),
            section_rule(section_set),
            vocabulary_rule(vocabulary),
            placement_rule(severity="error"),
        ],
        strict=True,
    )
    if report.findings:
        for finding in report.findings:
            print(
                f"{finding.severity} {finding.code} {finding.path}: {finding.message}",
                file=sys.stderr,
            )
        return 1

    print("strict validation: clean (0 findings)")
    print("sorted okf markdown members:")
    for member in sorted(members):
        print(member)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
