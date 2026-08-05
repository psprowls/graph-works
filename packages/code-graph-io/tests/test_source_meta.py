"""Unit tests for code_graph_io.source_meta.extension_languages."""

from __future__ import annotations

from code_graph_io import extension_languages


def test_extension_languages_maps_known_extensions() -> None:
    langs = extension_languages()
    assert langs[".py"] == "python"
    assert all(ext.startswith(".") for ext in langs)
    assert all(isinstance(v, str) and v for v in langs.values())
