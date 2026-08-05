"""Errors raised by code-parser."""

from __future__ import annotations

from pathlib import Path


class UnsupportedLanguageError(ValueError):
    """Raised when no parser is registered for a file's extension."""

    def __init__(
        self,
        message: str,
        *,
        path: Path | None = None,
        extension: str | None = None,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.extension = extension
