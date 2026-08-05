"""Core SourceTree data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Span:
    start_byte: int
    end_byte: int
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    start_col: int  # 0-indexed
    end_col: int  # 0-indexed


@dataclass(frozen=True)
class Reference:
    kind: str  # 'call' | 'import' | 'export'
    target_name: str
    target_module: str | None
    site: Span
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceNode:
    kind: str  # 'file' | 'class' | 'function' | 'method'
    name: str | None
    span: Span
    path: Path
    language: str  # 'python' | 'javascript' | 'typescript'
    package: str | None
    attrs: dict[str, Any] = field(default_factory=dict)
    children: list["SourceNode"] = field(default_factory=list)
    refs: list[Reference] = field(default_factory=list)
