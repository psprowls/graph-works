"""The epic's own acceptance clause for the repository-mirror lane, verbatim:
"the mirror structure matches a fixture repo's tree exactly, and a git
rename preserves prose and repairs inbound links."

One small, real fixture repo -- a `src/`-layout Python package with a
resolvable cross-module import, a test file, a config file, and a doc --
synced through the actual `plan_mirror`/`apply_mirror` pipeline (never the
CLI, matching `test_apply.py`/`test_plan.py`'s established convention in
this directory), then validated with `okf_io.validate` the way child 1's
`test_done_when.py` does, then put through a `git mv` to prove prose
survives and an inbound link gets repaired.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

from code_graph_io.handle import open_reader
from code_graph_io.update import run_workspace
from code_wiki_okf.config import RepoConfig
from code_wiki_okf.git_state import ls_files
from code_wiki_okf.init import install_bundle
from code_wiki_okf.mirror.apply import apply_mirror
from code_wiki_okf.mirror.plan import plan_mirror
from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import load_sections
from okf_ext.tags import load_vocabulary, vocabulary_rule
from okf_io import load_bundle, validate

_AT = datetime(2026, 1, 1, tzinfo=UTC)
_TODAY = date(2026, 1, 1)
_SECTIONS_DIR = Path(__file__).parents[2] / "src" / "code_wiki_okf" / "assets" / "_sections"
_NOTES_HEADING = "## Notes"
_REPO_NAME = "acme"

#: What `_fixture_repo` writes and commits -- a Python module with a real,
#: resolvable import (`user.py` -> `base.py`), a test file, a config file,
#: and a doc. This is also the independent expectation `test_
#: mirror_matches_fixture_tree_exactly` checks the synced mirror against.
#: It is hand-maintained, not derived from `git ls-files` -- that is what
#: makes it independent. `_sync` never reads this constant; it computes its
#: own `tracked` set from `git ls-files`, the same way `cli.py`'s `sync`
#: command does, so the two sides of that assertion do not share a single
#: source of truth that could be wrong in the same way twice.
_TRACKED_FILES: tuple[str, ...] = (
    "README.md",
    "pyproject.toml",
    "src/pkg/__init__.py",
    "src/pkg/base.py",
    "src/pkg/user.py",
    "tests/test_pkg.py",
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _head(repo: Path) -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _notes_body(text: str) -> str:
    """The `## Notes` section body of a page's text, verbatim."""
    start = text.index(_NOTES_HEADING) + len(_NOTES_HEADING)
    rest = text[start:]
    next_heading = rest.find("\n## ")
    section = rest[:next_heading] if next_heading != -1 else rest
    return section.strip()


def _set_notes(target: Path, text: str) -> None:
    content = target.read_text(encoding="utf-8")
    start = content.index(_NOTES_HEADING) + len(_NOTES_HEADING)
    rest = content[start:]
    next_heading = rest.find("\n## ")
    tail = rest[next_heading:] if next_heading != -1 else ""
    new_content = content[:start] + "\n\n" + text + "\n" + tail
    target.write_text(new_content, encoding="utf-8")


def _fixture_repo(tmp_path: Path) -> Path:
    """A tiny, real `src`-layout Python package: `pkg.user` imports
    `pkg.base` for real (a `pyproject.toml` with `[project].name = "pkg"`
    is what lets `code_graph_io` resolve it, not just parse it), plus a test
    file, the config file, and a doc -- one of each kind the acceptance
    clause asks for.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "pkg"\n', encoding="utf-8")
    (repo / "README.md").write_text(
        "# pkg\n\nA tiny fixture package for the repository-mirror lane's end-to-end test.\n", encoding="utf-8"
    )
    src_pkg = repo / "src" / "pkg"
    src_pkg.mkdir(parents=True)
    (src_pkg / "__init__.py").write_text("", encoding="utf-8")
    (src_pkg / "base.py").write_text("VALUE = 1\n", encoding="utf-8")
    (src_pkg / "user.py").write_text("from pkg.base import VALUE\n\nDOUBLED = VALUE * 2\n", encoding="utf-8")
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pkg.py").write_text(
        "from pkg.base import VALUE\n\n\ndef test_value() -> None:\n    assert VALUE == 1\n", encoding="utf-8"
    )
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _new_bundle(tmp_path: Path) -> Path:
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    return bundle_root


def _collect_mirror_paths(bundle_root: Path, repo_name: str) -> set[str]:
    """Every `.md` path under `repositories/<repo_name>`, mirror-root-relative,
    excluding `index.md` at any depth -- those are the lane's own reconciled
    directory indexes, not a mirrored tracked file.
    """
    root = bundle_root / "repositories" / repo_name
    if not root.exists():
        return set()
    return {path.relative_to(root).as_posix() for path in root.rglob("*.md") if path.name != "index.md"}


def _sync(bundle_root: Path, repo_root: Path, graph_dir: Path) -> None:
    """One full sync run against *bundle_root*: refresh the graph, walk
    *repo_root*'s tracked files with git itself (never a hardcoded list --
    this is what lets a rename's new tracked set show up for free on the
    next call), plan, apply. Calls the underlying functions directly, the
    way `cli.py`'s `sync` command does internally, matching this directory's
    established convention of exercising the pipeline without shelling out
    to the CLI.
    """
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    tracked = ls_files(repo_root)
    assert tracked is not None, f"{repo_root}: not a git checkout"
    repo = RepoConfig(name=_REPO_NAME, path=repo_root, ignore=())
    section_set = load_sections(_SECTIONS_DIR)
    with open_reader(graph_dir=graph_dir) as reader:
        bundle = load_bundle(bundle_root)
        plan = plan_mirror(bundle, reader, repo, tracked=tuple(tracked), sha=_head(repo_root), at=_AT)
        apply_mirror(bundle, plan, repo, section_set=section_set)


def test_mirror_matches_fixture_tree_exactly(tmp_path: Path) -> None:
    repo_root = _fixture_repo(tmp_path)
    bundle_root = _new_bundle(tmp_path)
    graph_dir = tmp_path / "graph"

    _sync(bundle_root, repo_root, graph_dir)

    mirrored = _collect_mirror_paths(bundle_root, _REPO_NAME)
    assert mirrored == {f"{tracked_path}.md" for tracked_path in _TRACKED_FILES}


def test_synced_bundle_validates_clean(tmp_path: Path) -> None:
    repo_root = _fixture_repo(tmp_path)
    bundle_root = _new_bundle(tmp_path)
    graph_dir = tmp_path / "graph"

    _sync(bundle_root, repo_root, graph_dir)

    # Loaded from the bundle's own seeded copies, not the package's asset
    # directory -- child 1's `test_done_when.py` pattern, and the one that
    # actually proves the *bundle* is self-describing.
    schema_set = load_schemas(bundle_root / "_schema")
    section_set = load_sections(bundle_root / "_sections")
    vocabulary = load_vocabulary(bundle_root / "_tags.yaml")

    bundle = load_bundle(bundle_root)
    report = validate(
        bundle,
        today=_TODAY,
        extra_rules=[
            schema_rule(schema_set),
            section_rule(section_set),
            vocabulary_rule(vocabulary),
        ],
    )
    assert report.ok, report.errors


def test_rename_preserves_prose_and_repairs_inbound_links(tmp_path: Path) -> None:
    repo_root = _fixture_repo(tmp_path)
    bundle_root = _new_bundle(tmp_path)
    graph_dir = tmp_path / "graph"

    _sync(bundle_root, repo_root, graph_dir)

    mirror_root = bundle_root / "repositories" / _REPO_NAME
    base_target = mirror_root / "src" / "pkg" / "base.py.md"
    user_target = mirror_root / "src" / "pkg" / "user.py.md"
    assert base_target.exists()
    assert user_target.exists()

    # A human's hand-edited prose on the page that is about to move...
    custom_notes = "The shared constant every other module in `pkg` imports."
    _set_notes(base_target, custom_notes)
    # ...and a second page's link into that page's mirrored path, added the
    # same way a person would: inside its own (human-owned) `## Notes`
    # section, not inside a `code-wiki-okf`-generated one, which the very
    # next sync would otherwise overwrite before the rename is even in play.
    _set_notes(user_target, "Doubles `base.py`'s constant.\n\nSee also [base.py](base.py.md).")

    # The rename itself, plus the source edit that keeps the import graph
    # coherent with it -- committed together, the way a real rename would be.
    _git(repo_root, "mv", "src/pkg/base.py", "src/pkg/renamed.py")
    (repo_root / "src" / "pkg" / "user.py").write_text(
        "from pkg.renamed import VALUE\n\nDOUBLED = VALUE * 2\n", encoding="utf-8"
    )
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "rename base.py to renamed.py, repoint user.py's import")

    _sync(bundle_root, repo_root, graph_dir)

    renamed_target = mirror_root / "src" / "pkg" / "renamed.py.md"
    assert renamed_target.exists()
    assert not base_target.exists()
    assert _notes_body(renamed_target.read_text(encoding="utf-8")) == custom_notes

    user_text = user_target.read_text(encoding="utf-8")
    assert "renamed.py.md" in user_text
    assert "base.py.md" not in user_text
