"""LanguageParser abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import tree_sitter

from code_parser.tree import Reference, SourceNode


class LanguageParser(ABC):
    """A per-language parser. Each subclass owns its grammar and AST traversal."""

    name: str
    file_extensions: tuple[str, ...]

    @property
    @abstractmethod
    def grammar(self) -> tree_sitter.Language:
        """The tree-sitter grammar for this language."""

    @abstractmethod
    def parse(
        self,
        path: Path,
        source: bytes,
        *,
        package: str | None = None,
    ) -> SourceNode:
        """Parse `source` bytes into a SourceTree rooted at a 'file' node."""

    def resolve_call_target(self, ref: Reference, file_tree: SourceNode) -> Reference:
        """Best-effort within-file resolution of a call reference. Default is no-op."""
        return ref
