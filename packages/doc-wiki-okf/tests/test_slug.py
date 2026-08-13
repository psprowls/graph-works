"""slugify — five ported from wiki-io's test_ingest_source.py, one new."""

from __future__ import annotations

from doc_wiki_okf.reading import slugify


def test_slugify_simple() -> None:
    assert slugify("Hello World!") == "hello-world"


def test_slugify_unicode() -> None:
    # Non-alphanumeric replaced with hyphens; lowercase applied
    result = slugify("Héllo Wörld")
    assert result == result.lower()
    assert "-" in result or result.replace("-", "").isalnum()


def test_slugify_multi_space_and_trailing_punct() -> None:
    assert slugify("  Hello   World  !  ") == "hello-world"


def test_slugify_empty_string() -> None:
    # Empty or whitespace-only returns "untitled"
    assert slugify("") == "untitled"
    assert slugify("   ") == "untitled"


def test_slugify_already_slug() -> None:
    assert slugify("hello-world") == "hello-world"


# --- new coverage, not ported --------------------------------------------


def test_slugify_truncates_at_sixty_characters() -> None:
    """The `[:60]` cap. Not covered by any ported test."""
    assert slugify("a" * 100) == "a" * 60
