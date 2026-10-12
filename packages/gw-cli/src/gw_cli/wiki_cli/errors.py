"""Shared error exit policy for wiki CLI commands.

The implementation moved to `gw_cli.errors` in C6 (§4.6). This module stays as
the import path every C4 call site already uses, so none of them had to change.
"""

from __future__ import annotations

from gw_cli.errors import exit_error

__all__ = ["exit_error"]
