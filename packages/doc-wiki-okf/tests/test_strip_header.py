"""The header strip: shape recognition, scope, byte fidelity, apply guards."""

import pytest
from doc_wiki_okf.proposals import strip_header as sh
from doc_wiki_okf.proposals.strip_header import apply, plan_strip_header, strip_header_body
from okf_io import load_bundle
from proposal_helpers import build_bundle

CURRENT = sh._CURRENT_HEADER
LEGACY = (
    "<!-- Body regenerated from origins[] while status: proposed. Do not edit here;\n"
    "     approve via `gw wiki proposal approve foo-bar`. -->\n"
)
IGNORE = ("schema/*", "*/schema/*", "sections/*", "*/sections/*")


def _page(body: str, status: str = "proposed") -> str:
    return f"---\ntype: Proposal\ntitle: T\ndescription: d\ntarget: adrs/x.md\npage_status: {status}\n---\n{body}"


def test_current_header_is_stripped() -> None:
    assert strip_header_body(f"{CURRENT}\n\n## Suggested Action\n\nx\n") == "## Suggested Action\n\nx\n"


def test_legacy_header_is_stripped() -> None:
    assert strip_header_body(f"{LEGACY}\n## Suggested Action\n") == "## Suggested Action\n"


def test_crlf_body_keeps_every_remaining_byte() -> None:
    body = f"{CURRENT}\r\n\r\n## Suggested Action\r\n\r\nx\r\n"
    assert strip_header_body(body) == "## Suggested Action\r\n\r\nx\r\n"
    legacy = (LEGACY + "\n## X\r\ny\r\n").replace("\n", "\r\n").replace("\r\r\n", "\r\n")
    assert strip_header_body(legacy) == "## X\r\ny\r\n"


def test_no_leading_comment_is_skipped() -> None:
    assert strip_header_body("## Suggested Action\n") is None
    assert strip_header_body("") is None


def test_header_not_on_first_line_is_skipped() -> None:
    assert strip_header_body(f"\n{CURRENT}\n\nx\n") is None


@pytest.mark.parametrize(
    "body",
    [
        "<!-- note -->\n\nx\n",
        LEGACY.splitlines(keepends=True)[0],
        LEGACY.splitlines(keepends=True)[0] + "## X\n",
        CURRENT.replace("machine-owned", "edited") + "\n\nx\n",
        LEGACY.replace("foo-bar", "a`b") + "\nx\n",
    ],
)
def test_unknown_comment_shapes_refuse(body: str) -> None:
    assert strip_header_body(body) == "unrecognised-header"


@pytest.mark.parametrize("body", [f"{CURRENT}\n## X\n", f"{CURRENT}\n\n\n## X\n", f"{CURRENT}\n", f"{CURRENT}"])
def test_bad_separator_refuses(body: str) -> None:
    assert strip_header_body(body) == "missing-separator"


def test_plan_scope(tmp_path) -> None:
    body = f"{CURRENT}\n\n## S\n"
    bundle = build_bundle(
        tmp_path / "b",
        {
            "proposals/a": _page(body),
            "proposals/b": _page(body, "rejected"),
            "proposals/_archive/c": _page(body),
            "repositories/x/references/git/proposals/d": _page(body),
        },
    )
    plan = plan_strip_header(bundle)
    assert [w.member for w in plan.writes] == ["proposals/a.md"]
    assert plan.ok


def test_planned_text_is_original_minus_header_lines(tmp_path) -> None:
    original = _page(f"{LEGACY}\n## S\n\nkeep\n")
    bundle = build_bundle(tmp_path / "b", {"proposals/a": original})
    write = plan_strip_header(bundle).writes[0]
    assert write.text == original.replace(LEGACY + "\n", "", 1)


def test_plan_reports_refusals_and_unreadable(tmp_path) -> None:
    bundle = build_bundle(
        tmp_path / "b",
        {
            "proposals/a": _page("<!-- note -->\n\nx\n"),
            "proposals/b": _page(f"{CURRENT}\n## X\n"),
            "proposals/c": "---\ntype: Proposal\npage_status: proposed\ntitle: [unclosed\n---\n<!-- x -->\n",
        },
    )
    plan = plan_strip_header(bundle)
    assert not plan.ok and plan.is_empty
    assert {r.member: r.kind for r in plan.refusals} == {
        "proposals/a.md": "unrecognised-header",
        "proposals/b.md": "missing-separator",
        "proposals/c.md": "unreadable",
    }


def test_apply_writes_and_rerun_plans_nothing(tmp_path) -> None:
    bundle = build_bundle(tmp_path / "b", {"proposals/a": _page(f"{CURRENT}\n\n## S\n")})
    plan = plan_strip_header(bundle)
    result = apply(bundle, plan)
    assert result.ok and result.written == ("proposals/a.md",)
    assert (tmp_path / "b/proposals/a.md").read_text(encoding="utf-8") == plan.writes[0].text
    again = plan_strip_header(load_bundle(tmp_path / "b", ignore=IGNORE))
    assert again.is_empty and again.ok


def test_apply_refuses_drifted_document(tmp_path) -> None:
    bundle = build_bundle(tmp_path / "b", {"proposals/a": _page(f"{CURRENT}\n\n## S\n")})
    plan = plan_strip_header(bundle)
    path = tmp_path / "b/proposals/a.md"
    drifted = path.read_text(encoding="utf-8") + "extra\n"
    path.write_text(drifted, encoding="utf-8")
    result = apply(load_bundle(tmp_path / "b", ignore=IGNORE), plan)
    assert result.failed[0].kind == "stale"
    assert path.read_text(encoding="utf-8") == drifted


def test_apply_rejects_foreign_plan(tmp_path) -> None:
    one = build_bundle(tmp_path / "one", {"proposals/a": _page(f"{CURRENT}\n\n## S\n")})
    two = build_bundle(tmp_path / "two", {})
    with pytest.raises(ValueError):
        apply(two, plan_strip_header(one))
