"""Byte-exact immutable source evidence and deliberately non-extracting archives."""

from __future__ import annotations

import gzip
import io
import json
import re
import stat
import tarfile
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from .git import GitError
from .machine import Services
from .records import Snapshot, SnapshotEntry, SourceIdentity, SourceSpec


class SnapshotError(ValueError):
    """Source or archive cannot safely represent portable evidence."""


def validate_path(path: str) -> str:
    if not path or path.startswith("/") or "\\" in path:
        raise SnapshotError(f"Unsafe path: {path!r}")
    for part in path.split("/"):
        if part in {"", ".", ".."} or part.endswith((".", " ")) or any(ord(c) < 32 or c in '<>:"|?*' for c in part):
            raise SnapshotError(f"Unsafe path: {path!r}")
        if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", part):
            raise SnapshotError(f"Reserved destination name: {path!r}")
    return path


def _validate(snapshot: Snapshot) -> None:
    seen: dict[str, SnapshotEntry] = {}
    for entry in snapshot.entries:
        validate_path(entry.path)
        key = entry.path.casefold()
        if key in seen:
            raise SnapshotError("Duplicate or case-colliding entry")
        if entry.kind not in {"file", "directory", "symlink"} or not 0 <= entry.mode <= 0o7777:
            raise SnapshotError("Unsupported entry kind or mode")
        if entry.kind == "directory" and entry.content:
            raise SnapshotError("Directory has content")
        seen[key] = entry
    for key in seen:
        for ancestor in PurePosixPath(key).parents:
            parent = seen.get(str(ancestor))
            if parent is not None and parent.kind != "directory":
                raise SnapshotError("Entry beneath a file or symlink")


def capture(source: SourceSpec, selected_paths: tuple[str, ...], *, services: Services) -> Snapshot:
    for path in selected_paths:
        validate_path(path)
    if len(set(selected_paths)) != len(selected_paths):
        raise SnapshotError("Duplicate selected path")
    if source.kind == "git":
        if services.git is None:
            raise GitError("git.missing", "No Git runner configured")
        if source.revision is None:
            raise GitError("git.revision", "Git capture requires an explicit revision")
        entire = services.git.acquire(source.locator, source.revision)
        entries = tuple(
            e
            for e in entire.entries
            if not selected_paths or any(e.path == p or e.path.startswith(p + "/") for p in selected_paths)
        )
        for path in selected_paths:
            if not any(e.path == path or e.path.startswith(path + "/") for e in entries):
                raise SnapshotError(f"Selected path missing: {path}")
        snapshot = Snapshot(entire.source, entries)
    elif source.kind == "local":
        root = Path(source.locator)
        if not stat.S_ISDIR(services.filesystem.mode(root)):
            raise SnapshotError("Source root must be a real directory")
        found: dict[str, SnapshotEntry] = {}

        def visit(path: Path) -> None:
            relative = validate_path(path.relative_to(root).as_posix())
            mode = services.filesystem.mode(path)
            if stat.S_ISLNK(mode):
                entry = SnapshotEntry(
                    relative, services.filesystem.readlink(path).encode("utf-8"), "symlink", stat.S_IMODE(mode)
                )
            elif stat.S_ISREG(mode):
                entry = SnapshotEntry(relative, services.filesystem.read_bytes(path), "file", stat.S_IMODE(mode))
            elif stat.S_ISDIR(mode):
                entry = SnapshotEntry(relative, b"", "directory", stat.S_IMODE(mode))
                for child in services.filesystem.children(path):
                    visit(child)
            else:
                raise SnapshotError(f"Unsupported source type: {relative}")
            found[relative] = entry

        for selected in selected_paths:
            for parent in PurePosixPath(selected).parents:
                if str(parent) != "." and not stat.S_ISDIR(services.filesystem.mode(root / str(parent))):
                    raise SnapshotError("Selected path traverses a source link or file")
            visit(root / selected)
        if not selected_paths:
            for child in services.filesystem.children(root):
                if child.name != ".git":
                    visit(child)
        snapshot = Snapshot(
            SourceIdentity("local", source.locator), tuple(sorted(found.values(), key=lambda e: e.path))
        )
    else:
        raise SnapshotError("Unsupported source kind")
    _validate(snapshot)
    return snapshot


def materialize_link(
    snapshot: Snapshot, path: str, *, evidence_paths: set[str] | None = None
) -> tuple[SnapshotEntry, ...]:
    """Resolve a selected link within captured evidence; caller must approve activation.

    Returns renamed regular/directory evidence without touching the source or disk.
    Nested links are resolved under the same containment and cycle rules.
    """
    _validate(snapshot)
    validate_path(path)
    entries = {e.path: e for e in snapshot.entries}

    # A selected inventory may omit ancestor directory entries. They remain
    # traversable containers, with no invented native permission metadata.
    for entry in snapshot.entries:
        for parent in PurePosixPath(entry.path).parents:
            key = "" if str(parent) == "." else str(parent)
            entries.setdefault(key, SnapshotEntry(key, b"", "directory", 0))

    def locate(parts: tuple[str, ...], base: tuple[str, ...], active: frozenset[str]) -> tuple[str, ...]:
        current = base
        for part in parts:
            if evidence_paths is not None and current:
                evidence_paths.add("/".join(current))
            parent = entries.get("/".join(current))
            if parent is None or parent.kind != "directory":
                raise SnapshotError("Source link traverses a non-directory")
            if part in {"", "."}:
                continue
            if part == "..":
                if not current:
                    raise SnapshotError("Escaping source link")
                current = current[:-1]
                continue
            candidate = (*current, part)
            key = "/".join(candidate)
            entry = entries.get(key)
            if evidence_paths is not None:
                evidence_paths.add(key)
            if entry is None:
                raise SnapshotError("Dangling source link")
            if entry.kind != "symlink":
                current = candidate
                continue
            if key in active:
                raise SnapshotError("Cyclic source link")
            try:
                target = entry.content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SnapshotError("Invalid source link text") from exc
            if not target or target.startswith("/") or "\\" in target or ":" in target:
                raise SnapshotError("Escaping source link")
            current = locate(tuple(target.split("/")), current, active | {key})
        return current

    def resolve(current: str, output: str, visiting: frozenset[str]) -> list[SnapshotEntry]:
        current = "/".join(locate(tuple(current.split("/")), (), frozenset()))
        if current in visiting:
            raise SnapshotError("Cyclic source link")
        entry = entries[current]
        result = [SnapshotEntry(output, entry.content, entry.kind, entry.mode)]
        if entry.kind == "directory":
            for child in entries.values():
                parent = str(PurePosixPath(child.path).parent)
                if child.path and ("" if parent == "." else parent) == current:
                    result.extend(
                        resolve(child.path, output + "/" + PurePosixPath(child.path).name, visiting | {current})
                    )
        return result

    return tuple(resolve(path, path, frozenset()))


def write_snapshot(snapshot: Snapshot, destination: Path) -> None:
    _validate(snapshot)
    ordered = sorted(snapshot.entries, key=lambda e: e.path)
    metadata = {
        "schema_version": 1,
        "source": asdict(snapshot.source),
        "digest": snapshot.digest,
        "entries": [{"path": e.path, "kind": e.kind, "mode": e.mode, "hash": e.hash} for e in ordered],
    }
    buffer = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0, filename="") as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,  # text-io-ok: tar mode w writes binary data
    ):
        members = [("manifest.json", json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8"))]
        members.extend((f"objects/{index}", entry.content) for index, entry in enumerate(ordered))
        for name, content in members:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o600
            archive.addfile(member, io.BytesIO(content))
    destination.write_bytes(buffer.getvalue())


def read_snapshot(path: Path) -> Snapshot:
    try:
        with tarfile.open(path, "r:gz") as archive:
            contents: dict[str, bytes] = {}
            for member in archive:
                validate_path(member.name)
                if not member.isfile() or member.name in contents:
                    raise SnapshotError("Unsupported or duplicate archive member")
                stream = archive.extractfile(member)
                if stream is None:
                    raise SnapshotError("Unreadable archive member")
                contents[member.name] = stream.read()
        metadata = json.loads(contents.pop("manifest.json"))
        if (
            not isinstance(metadata, dict)
            or set(metadata) != {"schema_version", "source", "digest", "entries"}
            or type(metadata["schema_version"]) is not int
            or metadata["schema_version"] != 1
        ):
            raise SnapshotError("Unsupported snapshot schema")
        source = metadata["source"]
        if (
            not isinstance(source, dict)
            or set(source) != {"kind", "locator", "resolved_commit", "label"}
            or source["kind"] not in {"local", "git"}
            or not isinstance(source["locator"], str)
        ):
            raise SnapshotError("Invalid source identity")
        if source["label"] is not None and not isinstance(source["label"], str):
            raise SnapshotError("Invalid source label")
        commit = source["resolved_commit"]
        if (source["kind"] == "local" and commit is not None) or (
            source["kind"] == "git"
            and (not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) is None)
        ):
            raise SnapshotError("Invalid resolved commit")
        if not isinstance(metadata["entries"], list):
            raise SnapshotError("Invalid entries")
        entries: list[SnapshotEntry] = []
        for index, value in enumerate(metadata["entries"]):
            if (
                not isinstance(value, dict)
                or set(value) != {"path", "kind", "mode", "hash"}
                or not isinstance(value["path"], str)
                or type(value["mode"]) is not int
                or value["kind"] not in {"file", "symlink", "directory"}
            ):
                raise SnapshotError("Invalid entry")
            entry = SnapshotEntry(value["path"], contents.pop(f"objects/{index}"), value["kind"], value["mode"])
            if entry.hash != value["hash"]:
                raise SnapshotError("Entry hash mismatch")
            entries.append(entry)
        snapshot = Snapshot(SourceIdentity(**source), tuple(entries))
        _validate(snapshot)
        if contents or snapshot.digest != metadata["digest"]:
            raise SnapshotError("Snapshot digest or members mismatch")
        return snapshot
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, tarfile.TarError, EOFError) as exc:
        raise SnapshotError("Invalid snapshot archive") from exc
