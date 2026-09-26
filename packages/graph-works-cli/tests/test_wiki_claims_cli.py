"""`gw wiki claims` — refresh, show, closure."""

from __future__ import annotations

import json
import re
from pathlib import Path

from code_graph_io import open_writer
from graph_works_cli.cli import app
from graph_works_core import graph_target, resolve
from typer.testing import CliRunner

runner = CliRunner()

PAGE = """\
---
type: Explanation
title: E
status: stable
claims:
  - id: C1
    claim: The repo claim.
    about: [repo:o/r]
---
"""


def _seed(workspace: Path) -> Path:
    target = workspace / "okf" / "explanations" / "e.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(PAGE, encoding="utf-8", newline="\n")
    return workspace


def test_refresh_text_and_json(workspace: Path) -> None:
    _seed(workspace)
    first = runner.invoke(app, ["wiki", "claims", "refresh", "--workspace", str(workspace)])
    assert first.exit_code == 0, first.output
    assert "no-manifest" in first.stdout
    again = runner.invoke(app, ["wiki", "claims", "refresh", "--json", "--workspace", str(workspace)])
    assert json.loads(again.stdout)["reason"] is None
    forced = runner.invoke(app, ["wiki", "claims", "refresh", "--force", "--json", "--workspace", str(workspace)])
    assert json.loads(forced.stdout)["reason"] == "forced"


def test_show_text_and_json(workspace: Path) -> None:
    _seed(workspace)
    text = runner.invoke(app, ["wiki", "claims", "show", "repo:o/r", "--workspace", str(workspace)])
    assert text.exit_code == 0, text.output
    assert "explanations/e#C1  The repo claim." in text.stdout
    payload = json.loads(
        runner.invoke(app, ["wiki", "claims", "show", "repo:o/r", "--json", "--workspace", str(workspace)]).stdout
    )
    assert [row["id"] for row in payload["rows"]] == ["C1"]


def test_closure_unknown_item_exits_nonzero(workspace: Path) -> None:
    result = runner.invoke(app, ["wiki", "claims", "closure", "work/nope", "--workspace", str(workspace)])
    assert result.exit_code != 0
    assert "unknown-item" in result.stderr


def test_closure_unknown_item_json_envelope(workspace: Path) -> None:
    result = runner.invoke(app, ["wiki", "claims", "closure", "work/nope", "--json", "--workspace", str(workspace)])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["reason"] == "unresolved"


def test_closure_without_graph_exits_zero_with_warning(workspace: Path) -> None:
    _seed(workspace)
    item = workspace / "okf" / "work" / "feature-t.md"
    item.parent.mkdir(parents=True, exist_ok=True)
    item.write_text(
        "---\ntype: Feature\ntitle: T\ndescription: d\nstatus: draft\nwork_status: open\n"
        "opened: 2026-09-24\nupdated: 2026-09-24\nrepo: r\naffects: [README.md]\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    text = runner.invoke(app, ["wiki", "claims", "closure", "work/feature-t", "--workspace", str(workspace)])
    assert text.exit_code == 0, text.output
    assert "no code graph" in text.output
    payload = json.loads(
        runner.invoke(
            app, ["wiki", "claims", "closure", "work/feature-t", "--json", "--workspace", str(workspace)]
        ).stdout
    )
    assert payload["matched"] == [] and payload["warnings"]


def test_show_text_with_no_matching_rows_says_so(workspace: Path) -> None:
    _seed(workspace)
    text = runner.invoke(app, ["wiki", "claims", "show", "repo:o/other", "--workspace", str(workspace)])
    assert text.exit_code == 0, text.output
    assert text.stdout == "no claims about repo:o/other\n"
    payload = json.loads(
        runner.invoke(app, ["wiki", "claims", "show", "repo:o/other", "--json", "--workspace", str(workspace)]).stdout
    )
    assert payload["rows"] == []


FILE_PAGE = """\
---
type: Explanation
title: F
status: stable
claims:
  - id: F1
    claim: The readme claim.
    about: [file:o/r/README.md]
---
"""


def test_closure_text_groups_matched_rows_by_tier_with_totals(workspace: Path) -> None:
    _seed(workspace)
    page = workspace / "okf" / "explanations" / "f.md"
    page.write_text(FILE_PAGE, encoding="utf-8", newline="\n")
    item = workspace / "okf" / "work" / "feature-t.md"
    item.parent.mkdir(parents=True, exist_ok=True)
    item.write_text(
        "---\ntype: Feature\ntitle: T\ndescription: d\nstatus: draft\nwork_status: open\n"
        "opened: 2026-09-24\nupdated: 2026-09-24\nrepo: r\naffects: [README.md]\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    store = open_writer(graph_dir=graph_target(resolve(workspace=workspace)).graph_dir, create=True)
    try:
        store._conn.execute(
            "INSERT INTO nodes (id, kind, name, path, uri, repo) VALUES "
            "(1,'repository','r','','repo:o/r','repo:o/r'),"
            "(2,'file','README.md','README.md','file:o/r/README.md','repo:o/r')"
        )
        store._conn.commit()
    finally:
        store.close()
    result = runner.invoke(app, ["wiki", "claims", "closure", "work/feature-t", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "tier 0  file:o/r/README.md" in out
    assert "\n# tier 0\nexplanations/f#F1  The readme claim.  [" in out
    assert "\n# tier 3\nexplanations/e#C1  The repo claim.  [" in out
    assert out.index("# tier 0") < out.index("# tier 3")
    rows = re.findall(r"\[(\d+) tokens\]", out)
    assert len(rows) == 2
    assert out.endswith(f"\n2 rows, {sum(int(n) for n in rows)} tokens\n")
