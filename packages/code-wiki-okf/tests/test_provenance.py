from datetime import UTC, datetime

import pytest
from code_wiki_okf.provenance import generated_value, last_updated_commit_value, tokens_value
from okf_io import parse


def test_generated_value_shape() -> None:
    at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert generated_value(by="code-wiki-okf/0.1.0", at=at) == {
        "by": "code-wiki-okf/0.1.0",
        "at": "2026-01-01T12:00:00+00:00",
    }


def test_generated_value_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        generated_value(by="code-wiki-okf/0.1.0", at=datetime(2026, 1, 1))


def test_generated_value_coerces_without_okf_io_findings() -> None:
    at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    value = generated_value(by="code-wiki-okf/0.1.0", at=at)
    text = f"---\ntype: Package\ntitle: example\ngenerated:\n  by: {value['by']}\n  at: '{value['at']}'\n---\n"
    document = parse(text)
    assert document.parse_error is None
    assert document.fm.generated is not None
    assert document.fm.generated.at == value["at"]
    assert "generated" not in document.fm.coercion_failures


def test_last_updated_commit_value_passes_through() -> None:
    sha = "a" * 40
    assert last_updated_commit_value(sha) == sha


def test_last_updated_commit_value_rejects_blank() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        last_updated_commit_value("   ")


def test_tokens_value_passes_through() -> None:
    assert tokens_value(42) == 42


def test_tokens_value_rejects_negative() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        tokens_value(-1)
