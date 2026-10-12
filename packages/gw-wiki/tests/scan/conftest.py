"""Session seeds for the scan suites -- built once per worker, copied per test."""

from __future__ import annotations

from pathlib import Path

import pytest
from scan_helpers import ScanSeed, build_scan_seed, make_repo


@pytest.fixture(scope="session")
def scan_seed(tmp_path_factory: pytest.TempPathFactory) -> ScanSeed:
    return build_scan_seed(tmp_path_factory.mktemp("scan_seed"))


@pytest.fixture(scope="session")
def repo_seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_repo(tmp_path_factory.mktemp("repo_seed"))
