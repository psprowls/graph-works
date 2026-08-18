"""Bounded catalog, page and chunk helpers over a loaded bundle."""

from __future__ import annotations

from graph_works_core.agent_substrate.agent_tools import (
    SourceChunks,
    build_catalog,
    chunk_text,
    filter_graph_tools,
    read_bounded_page,
    search_catalog,
    strip_code_fence,
    truncate_text,
)
from langchain_core.tools import tool
from okf_io import load_bundle


def _page(title="", description="", resource="", type_="", body="body text"):
    return f"---\ntitle: {title}\ndescription: {description}\nresource: {resource}\ntype: {type_}\n---\n\n{body}\n"


def _bundle(tmp_path, pages):
    for concept_id, text in pages.items():
        path = tmp_path / f"{concept_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return load_bundle(tmp_path)


def test_the_catalog_groups_concepts_by_lane(tmp_path):
    bundle = _bundle(
        tmp_path,
        {
            "concepts/auth": _page(title="Auth"),
            "sources/spec": _page(title="Spec"),
            "entities/pkg/okf-io": _page(title="okf-io"),
        },
    )
    catalog = build_catalog(bundle, lanes=("concepts", "sources", "entities"))
    assert [entry["slug"] for entry in catalog["concepts"]] == ["auth"]
    assert [entry["slug"] for entry in catalog["sources"]] == ["spec"]
    assert [entry["slug"] for entry in catalog["entities"]] == ["okf-io"]


def test_a_lane_that_was_not_asked_for_is_absent(tmp_path):
    bundle = _bundle(tmp_path, {"concepts/auth": _page(title="Auth"), "adrs/0001": _page(title="ADR")})
    catalog = build_catalog(bundle, lanes=("concepts",))
    assert list(catalog) == ["concepts"]


def test_a_requested_lane_with_no_members_is_still_a_key(tmp_path):
    catalog = build_catalog(_bundle(tmp_path, {"concepts/auth": _page()}), lanes=("concepts", "adrs"))
    assert catalog["adrs"] == []


def test_the_entry_carries_exactly_the_documented_fields(tmp_path):
    bundle = _bundle(
        tmp_path,
        {"concepts/auth": _page(title="Auth", description="How auth works", resource="pkg:auth", type_="concept")},
    )
    entry = build_catalog(bundle, lanes=("concepts",))["concepts"][0]
    assert set(entry) == {"kind", "slug", "path", "title", "summary", "uri", "entity_kind", "excerpt"}
    assert entry["kind"] == "concept"
    assert entry["path"] == "/concepts/auth.md"
    assert entry["title"] == "Auth"
    assert entry["summary"] == "How auth works"
    assert entry["uri"] == "pkg:auth"
    assert entry["entity_kind"] == "concept"


def test_the_catalog_serves_the_link_form_the_citation_rules_ask_for(tmp_path):
    # The finding: the planner was shown `concepts/auth.md` and asked for
    # `/concepts/auth.md`, which is the only form `_LINK_RE` matches.
    from graph_works_core.query.commands import _LINK_RE

    root = tmp_path / "okf"
    (root / "concepts").mkdir(parents=True)
    (root / "concepts" / "auth.md").write_text("---\ntitle: Auth\n---\n\nRotation.\n", encoding="utf-8")

    catalog = build_catalog(load_bundle(root), lanes=["concepts"])
    path = catalog["concepts"][0]["path"]
    assert path == "/concepts/auth.md"
    assert _LINK_RE.findall(f"[Auth]({path})") == ["concepts/auth"]


def test_the_entities_lane_keeps_its_singular_kind(tmp_path):
    bundle = _bundle(tmp_path, {"entities/pkg_okf": _page(title="okf")})
    assert build_catalog(bundle, lanes=("entities",))["entities"][0]["kind"] == "entity"


def test_a_titleless_page_falls_back_to_a_humanized_slug(tmp_path):
    bundle = _bundle(tmp_path, {"concepts/token_bucket-limits": _page()})
    assert build_catalog(bundle, lanes=("concepts",))["concepts"][0]["title"] == "Token Bucket Limits"


def test_the_excerpt_is_collapsed_and_bounded(tmp_path):
    bundle = _bundle(tmp_path, {"concepts/auth": _page(body="one\n\n  two   three")})
    entry = build_catalog(bundle, lanes=("concepts",), excerpt_chars=7)["concepts"][0]
    assert entry["excerpt"] == "one two"


def test_a_root_level_concept_lands_in_the_empty_lane(tmp_path):
    bundle = _bundle(tmp_path, {"README": _page(title="Readme")})
    assert build_catalog(bundle, lanes=("",))[""][0]["slug"] == "README"


def test_reading_a_page_is_a_key_lookup(tmp_path):
    bundle = _bundle(tmp_path, {"concepts/auth": _page(title="Auth", body="the body")})
    assert read_bounded_page(bundle, "concepts/auth") == "# Auth\n\nthe body"


def test_a_trailing_md_is_tolerated(tmp_path):
    bundle = _bundle(tmp_path, {"concepts/auth": _page(title="Auth", body="the body")})
    assert read_bounded_page(bundle, "concepts/auth.md").startswith("# Auth")


def test_a_missing_concept_returns_the_error_string_the_loop_expects(tmp_path):
    # Traversal is unrepresentable — a concept id is a Bundle key — so the only
    # failure left is "not there", and the ERROR: shape is how the loop tells
    # the model it asked for something that does not exist.
    bundle = _bundle(tmp_path, {"concepts/auth": _page()})
    assert read_bounded_page(bundle, "../../etc/passwd").startswith("ERROR: ")


def test_a_bodyless_page_reads_as_its_title_alone(tmp_path):
    bundle = _bundle(tmp_path, {"concepts/auth": _page(title="Auth", body="")})
    assert read_bounded_page(bundle, "concepts/auth") == "# Auth"


def test_a_long_page_is_truncated_with_a_marker(tmp_path):
    bundle = _bundle(tmp_path, {"concepts/auth": _page(title="Auth", body="x" * 200)})
    excerpt = read_bounded_page(bundle, "concepts/auth", max_chars=40)
    assert excerpt.endswith("\n\n[TRUNCATED]")
    assert len(excerpt) == 40


def test_the_truncation_marker_fits_inside_the_bound():
    # The bound is the contract: callers budget a context window against it.
    assert len(truncate_text("x" * 200, 50)) == 50
    assert truncate_text("x" * 200, 50).endswith("\n\n[TRUNCATED]")


def test_a_bound_too_small_for_the_marker_returns_exactly_the_bound():
    out = truncate_text("x" * 200, 5)
    assert out == "xxxxx"
    assert "[TRUNCATED]" not in out


def test_truncate_leaves_short_text_alone():
    assert truncate_text("short", 100) == "short"


def test_search_matches_title_summary_and_slug(tmp_path):
    bundle = _bundle(
        tmp_path,
        {
            "concepts/auth": _page(title="Auth", description="tokens and sessions"),
            "concepts/cache": _page(title="Cache", description="memoization"),
        },
    )
    catalog = build_catalog(bundle, lanes=("concepts",))
    assert [row["slug"] for row in search_catalog(catalog, "tokens")] == ["auth"]
    assert [row["slug"] for row in search_catalog(catalog, "cache")] == ["cache"]
    assert len(search_catalog(catalog, "")) == 2


def test_search_filters_by_kind_and_honors_the_limit(tmp_path):
    bundle = _bundle(
        tmp_path,
        {"concepts/auth": _page(title="Auth"), "sources/auth-spec": _page(title="Auth spec")},
    )
    catalog = build_catalog(bundle, lanes=("concepts", "sources"))
    assert [row["slug"] for row in search_catalog(catalog, "auth", kind="source")] == ["auth-spec"]
    assert len(search_catalog(catalog, "auth", limit=1)) == 1


def test_search_bucket_match_with_double_s_ending(tmp_path):
    # Guards the second `removesuffix` site in `_catalog_bucket_matches` (line 149).
    # `rstrip("s")` would strip every trailing `s`, so `"class"` → `"cla"`; a
    # bucket with no trailing `s` at all would not discriminate: both bug and fix
    # would pass. The name `"class"` is what catches the bug when searched with
    # `kind="cla"` (the rstrip result). With removesuffix, `kind="cla"` should
    # not match; with rstrip, it wrongly does.
    bundle = _bundle(
        tmp_path,
        {"class/x": _page(title="Class X"), "concepts/y": _page(title="Concept Y")},
    )
    catalog = build_catalog(bundle, lanes=("class", "concepts"))
    assert [row["slug"] for row in search_catalog(catalog, "class", kind="cla")] == []


def test_chunking_returns_the_whole_text_when_it_fits():
    assert chunk_text("abcdef", max_chars=10, chunk_chars=2) == SourceChunks(
        full_text="abcdef", chunks=[], over_budget=False
    )


def test_chunking_splits_when_over_budget():
    chunks = chunk_text("abcdef", max_chars=3, chunk_chars=2)
    assert chunks.full_text is None
    assert chunks.chunks == ["ab", "cd", "ef"]
    assert chunks.over_budget is True


def test_graph_tools_are_filtered_by_name():
    @tool
    def keep() -> str:
        """Keep me."""
        return "k"

    @tool
    def drop() -> str:
        """Drop me."""
        return "d"

    assert [t.name for t in filter_graph_tools([keep, drop], {"keep"})] == ["keep"]


def test_a_lane_name_ending_in_a_double_s_loses_only_one_of_them(tmp_path):
    # `rstrip("s")` strips every trailing `s`, not one, so a lane named `class`
    # yielded `cla` and `process` yielded `proce`. It happens to be right for
    # concepts/sources/adrs, which is why nobody noticed — and lane names are
    # bundle-declared, so a name that is not a regular plural is expected input.
    #
    # `clas` is not a good kind either. What the fix buys is a bounded, stable
    # transformation instead of one that eats an arbitrary run of trailing
    # letters. Real singularization is a separate question.
    #
    # `class` is the case that discriminates: a lane with no trailing `s` at all
    # would pass under both the bug and the fix, and guard nothing.
    bundle = _bundle(
        tmp_path,
        {
            "class/x": _page(title="Class X"),
            "concepts/y": _page(title="Concept Y"),
            "entities/z": _page(title="Entity Z"),
        },
    )
    catalog = build_catalog(bundle, lanes=("class", "concepts", "entities"))
    assert catalog["class"][0]["kind"] == "clas"
    assert catalog["concepts"][0]["kind"] == "concept"
    assert catalog["entities"][0]["kind"] == "entity"


# --------------------------------------------------------------------------
# The shared fence stripper. It had two private rivals -- `commands.ingest`
# and `commands.suggest_pages` each kept a copy, and the shared docstring
# recorded that they disagreed on degenerate input. One function, one
# behaviour, and a test that says so.
# --------------------------------------------------------------------------


def test_a_fenced_payload_is_unwrapped():
    assert strip_code_fence("```json\n{}\n```") == "{}"


def test_an_unfenced_payload_comes_back_stripped():
    assert strip_code_fence("  {}  \n") == "{}"


def test_a_bare_opening_fence_carries_no_payload():
    assert strip_code_fence("```json") == ""


def test_a_fence_opened_but_never_closed_keeps_its_remainder():
    assert strip_code_fence("```json\n{}") == "{}"


def test_no_command_module_keeps_a_private_fence_stripper():
    # Three implementations of one function is the drift this consolidation
    # removed; a fourth would reintroduce it silently.
    from graph_works_core.ingest import commands as ingest
    from graph_works_core.ingest import suggest_pages

    for module in (ingest, suggest_pages):
        assert not hasattr(module, "_strip_fence"), f"{module.__name__} kept a private fence stripper"
