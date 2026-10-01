"""Shared fixtures for repository integration tests."""

from __future__ import annotations

import os

import pytest
from gitrepo import GIT
from repositories_okf.git import Git


@pytest.fixture
def runner() -> Git:
    return Git(executable=GIT, environ=dict(os.environ))
