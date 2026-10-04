"""A stat-validated cache of parsed gate receipts: `cache_dir/gate/receipts.json`.

The glob still names the files; a file whose `(mtime_ns, size)` matches its
cached entry is not re-read. Receipts that arrive without gw (a pull, an
archive move, a hand edit) change the signature and are re-parsed, so the
cache is correct by construction. Deleting it costs one re-parse. The cache
holds plain JSON run data; decoding into typed runs is the caller's.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from okf_ext.locking import locked

CACHE_VERSION = 1
Parse = Callable[[str], tuple[str, list[dict[str, Any]]]]


@dataclass(frozen=True, slots=True)
class CachedReceipt:
    rel: str
    owner: str | None
    runs: tuple[dict[str, Any], ...]
    error: str | None


@dataclass(frozen=True, slots=True)
class IndexRead:
    receipts: tuple[CachedReceipt, ...]
    parsed: tuple[str, ...]
    warnings: tuple[str, ...]


def cache_path(cache_dir: Path) -> Path:
    return cache_dir / "gate" / "receipts.json"


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _entry_ok(value: object) -> bool:
    return (
        isinstance(value, dict)
        and type(value.get("mtime_ns")) is int
        and type(value.get("size")) is int
        and isinstance(value.get("runs"), list)
        and all(isinstance(run, dict) for run in value["runs"])
        and (value.get("owner") is None or isinstance(value.get("owner"), str))
        and (value.get("error") is None or isinstance(value.get("error"), str))
    )


def _load(cache_file: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION or not isinstance(data.get("files"), dict):
        return {}
    return {rel: entry for rel, entry in data["files"].items() if isinstance(rel, str) and _entry_ok(entry)}


def _parse_file(path: Path, parse: Parse) -> tuple[dict[str, Any], bool]:
    """The entry for *path*, and whether it may be cached (an OSError may not)."""
    signature = _signature(path)
    if signature is None:
        return {"mtime_ns": 0, "size": 0, "owner": None, "runs": [], "error": f"{path}: not found"}, False
    owner: str | None = None
    runs: list[dict[str, Any]] = []
    error: str | None = None
    try:
        owner, runs = parse(path.read_bytes().decode("utf-8"))
    except OSError as exc:
        return {"mtime_ns": 0, "size": 0, "owner": None, "runs": [], "error": str(exc)}, False
    except (UnicodeError, ValueError) as exc:
        owner, runs, error = None, [], str(exc)
    return {"mtime_ns": signature[0], "size": signature[1], "owner": owner, "runs": runs, "error": error}, True


def _write(cache_file: Path, files: dict[str, dict[str, Any]]) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    temp = cache_file.with_name(f".{cache_file.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        temp.write_text(json.dumps({"version": CACHE_VERSION, "files": files}) + "\n", encoding="utf-8", newline="\n")
        temp.replace(cache_file)
    finally:
        temp.unlink(missing_ok=True)


def _warning(cache_file: Path, exc: OSError) -> str:
    return f"{cache_file}: gate receipt cache not written ({exc})"


def read_index(bundle_root: Path, cache_dir: Path, *, glob: str, parse: Parse) -> IndexRead:
    """Every receipt under *glob*, re-parsing only files whose signature changed."""
    cache_file = cache_path(cache_dir)
    cached = _load(cache_file)
    files: dict[str, dict[str, Any]] = {}
    uncacheable: dict[str, dict[str, Any]] = {}
    parsed: list[str] = []
    for path in sorted(bundle_root.glob(glob)):
        rel = path.relative_to(bundle_root).as_posix()
        entry = cached.get(rel)
        if entry is not None and (entry["mtime_ns"], entry["size"]) == _signature(path):
            files[rel] = entry
            continue
        parsed.append(rel)
        fresh, cacheable = _parse_file(path, parse)
        (files if cacheable else uncacheable)[rel] = fresh
    warnings: list[str] = []
    if files != cached:
        reparsed = {rel: files[rel] for rel in parsed if rel in files}
        gone = [rel for rel in cached if rel not in files and rel not in uncacheable]
        try:
            with locked(cache_file.with_name("receipts.lock")):
                # Re-load under the lock and merge only what this read changed, so a concurrent
                # write_through since our first load is not overwritten by a stale snapshot.
                current = _load(cache_file)
                merged_files = {rel: entry for rel, entry in current.items() if rel not in gone}
                merged_files.update(reparsed)
                if merged_files != current:
                    _write(cache_file, merged_files)
        except OSError as exc:
            warnings.append(_warning(cache_file, exc))
    merged = {**files, **uncacheable}
    receipts = tuple(
        CachedReceipt(rel, merged[rel]["owner"], tuple(merged[rel]["runs"]), merged[rel]["error"])
        for rel in sorted(merged)
    )
    return IndexRead(receipts, tuple(parsed), tuple(warnings))


def write_through(bundle_root: Path, cache_dir: Path, rel: str, *, parse: Parse) -> str | None:
    """Refresh *rel*'s entry after gw wrote it; a warning on failure, never an error."""
    cache_file = cache_path(cache_dir)
    fresh, cacheable = _parse_file(bundle_root / rel, parse)
    if not cacheable:
        return None
    try:
        with locked(cache_file.with_name("receipts.lock")):
            files = _load(cache_file)
            files[rel] = fresh
            _write(cache_file, files)
    except OSError as exc:
        return _warning(cache_file, exc)
    return None


__all__ = ["CACHE_VERSION", "CachedReceipt", "IndexRead", "Parse", "cache_path", "read_index", "write_through"]
