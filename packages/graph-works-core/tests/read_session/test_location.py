from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from graph_works_core.read_session import location
from graph_works_core.workspace.bundle import CLONE_IGNORE, CLONE_PRUNE
from graph_works_core.workspace.init import apply_init, plan_init
from graph_works_core.workspace.layout import WorkspaceLayout, layout_for


def _layout(tmp_path: Path) -> WorkspaceLayout:
    return apply_init(plan_init(tmp_path / ".works", today=date(2026, 10, 2), topic="t")).layout


def test_database_lives_under_the_cache_dir(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    assert location.database_path(layout) == layout.cache_dir / "read-index" / "bundle.db"


def test_fingerprint_is_sha256_of_the_canonical_payload(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    payload = {"bundle_dir": "okf", "format": 1, "ignore": list(CLONE_IGNORE), "prune": list(CLONE_PRUNE)}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert location.fingerprint(layout) == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_fingerprint_moves_with_format_and_bundle_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)
    base = location.fingerprint(layout)
    assert location.fingerprint(layout_for(layout.root, bundle_dir="other")) != base
    monkeypatch.setattr(location, "SESSION_FORMAT", 2)
    assert location.fingerprint(layout) != base


def test_sidecar_paths(tmp_path: Path) -> None:
    db = tmp_path / "bundle.db"
    assert location.sidecar_paths(db) == (db, tmp_path / "bundle.db-wal", tmp_path / "bundle.db-shm")


def test_database_uses_relocated_cache_without_creating_it(tmp_path: Path) -> None:
    layout = layout_for(tmp_path / "workspace", cache_dir=str(tmp_path / "cache"))
    assert location.database_path(layout) == tmp_path / "cache" / "read-index" / "bundle.db"
    assert not layout.cache_dir.exists()


def test_fingerprint_external_unicode_bundle_uses_absolute_posix_path(tmp_path: Path) -> None:
    bundle = tmp_path / "café"
    layout = layout_for(tmp_path / "workspace", bundle_dir=str(bundle))
    payload = {"bundle_dir": bundle.as_posix(), "format": 1, "ignore": list(CLONE_IGNORE), "prune": list(CLONE_PRUNE)}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert location.fingerprint(layout) == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_fingerprint_relative_bundle_survives_workspace_relocation(tmp_path: Path) -> None:
    assert location.fingerprint(layout_for(tmp_path / "first")) == location.fingerprint(layout_for(tmp_path / "second"))
