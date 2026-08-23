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
    prefix = f"repositories/{repo.name}"
    expected = {
        "agent-plugins/index.md",
        "apps/index.md",
        "dependencies/index.md",
        "dependencies/npm/index.md",
        "dependencies/pypi/code-graph-io.md",
        "dependencies/pypi/index.md",
        "index.md",
        "packages/index.md",
        "repositories/index.md",
        "test-suites/index.md",
        f"{prefix}/agent-plugins/graph-works.md",
        f"{prefix}/agent-plugins/index.md",
        f"{prefix}/apps/code-wiki-okf.md",
        f"{prefix}/apps/index.md",
        f"{prefix}/files/index.md",
        f"{prefix}/files/packages/code-wiki-okf/README.md.md",
        f"{prefix}/index.md",
        f"{prefix}/packages/graph-works-cli.md",
        f"{prefix}/packages/index.md",
        f"{prefix}/repository.md",
        f"{prefix}/test-suites/index.md",
        f"{prefix}/test-suites/packages__code-wiki-okf__tests.md",
    }
    members = {member.relative_to(layout.bundle_dir).as_posix() for member in layout.bundle_dir.rglob("*.md")}
    missing = sorted(expected - members)
    if missing:
        print("missing canonical smoke members:", file=sys.stderr)
        for member in missing:
            print(f"  {member}", file=sys.stderr)
        return 1
    if "files/index.md" in members:
        print("unexpected global files/index.md", file=sys.stderr)
        return 1
    discovery_extras = sorted(
        member
        for lane in ("agent-plugins", "apps", "packages", "test-suites")
        for member in members
        if member.startswith(f"{lane}/") and member != f"{lane}/index.md"
    )
    if discovery_extras:
        print("unexpected concept pages in discovery-only lanes:", file=sys.stderr)
        for member in discovery_extras:
            print(f"  {member}", file=sys.stderr)
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
