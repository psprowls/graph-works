"""Shared fixtures for code-graph-io tests."""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    yield c
    c.close()
