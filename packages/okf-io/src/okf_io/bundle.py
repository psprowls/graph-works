"""The one-pass bundle loader (OKF v0.2 §3).

One walk. Every file is read at most once and the loaded model is what every
downstream operation shares -- which is why :func:`okf_io.validate.validate`
takes a ``Bundle`` rather than a path.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from okf_io.derive import effective_status
from okf_io.document import Document

#: §3.1. Recognised at any depth and never concepts.
INDEX_NAME = "index.md"
LOG_NAME = "log.md"

#: Never a member, at any depth.
GIT_DIR_NAME = ".git"


def canonical_id(value: str) -> str:
    """The form two bundle-relative paths are compared in.

    NFC, not NFKC: NFKC changes which characters are present (it would fold
    ``ﬁle.md`` onto ``file.md``, conflating two names a filesystem holds
    apart), where NFC only picks a canonical composition of the same
    characters. ``isascii()`` is the fast path and the whole risk story: an
    ASCII string is invariant under every normalization form, so a bundle
    with no non-ASCII member never pays for this at all.
    """
    return value if value.isascii() else unicodedata.normalize("NFC", value)


@dataclass(frozen=True, slots=True)
class Bundle:
    """Every member of a bundle directory, loaded once.

    ``concepts`` is keyed by **id**: the bundle-relative posix path minus
    ``.md``. ``indexes`` and ``logs`` are keyed by **directory id**, which is
    ``""`` for the bundle root.

    ``assets`` exists because non-markdown members are link and path-reference
    targets. An ``rglob("*.md")`` walk misses them, and path validation then
    reports false positives -- which is exactly the bug this field prevents.

    ``ignored`` is kept apart from ``assets`` rather than folded into it so a
    caller can tell "excluded by ``ignore=``" from "not markdown".

    ``_canonical`` maps :func:`canonical_id` of a member's path to that
    member's *raw* disk id -- one entry per member whose raw id is non-ASCII,
    none otherwise. Ids themselves stay raw disk bytes (§ADR-0027): a writer
    that reconstructs a path from an id must open the file that id names, on
    a filesystem that may not be normalization-insensitive the way APFS is.
    Matching is NFC-insensitive; identity is not.
    """

    root: Path
    concepts: Mapping[str, Document]
    indexes: Mapping[str, Document]
    logs: Mapping[str, Document]
    assets: frozenset[str]
    ignored: frozenset[str]
    unreadable: Mapping[str, str]
    _canonical: Mapping[str, str]

    def concept(self, concept_id: str) -> Document | None:
        return self.concepts.get(concept_id)

    def by_type(self, type_name: str) -> tuple[str, ...]:
        wanted = type_name.strip()
        return tuple(sorted(cid for cid, doc in self.concepts.items() if (doc.fm.type or "").strip() == wanted))

    def by_tag(self, tag: str) -> tuple[str, ...]:
        return tuple(sorted(cid for cid, doc in self.concepts.items() if tag in doc.fm.tags))

    def by_status(self, status: str) -> tuple[str, ...]:
        """Filter on the §5.4 *effective* status, not the raw field.

        A concept with no ``status`` answers to ``"stable"``. Computing the
        default in one place is the point: every caller re-deriving it is how
        two consumers end up disagreeing about the same document.
        """
        return tuple(sorted(cid for cid, doc in self.concepts.items() if effective_status(doc.fm) == status))

    def _raw_member(self, member: str) -> bool:
        """Whether *member* (already stripped) names something, by exact match."""
        if member in self.assets or member in self.ignored:
            return True
        if not member.endswith(".md"):
            return False
        if member[:-3] in self.concepts:
            return True
        pure = PurePosixPath(member)
        parent = pure.parent.as_posix()
        directory = "" if parent == "." else parent
        if pure.name == INDEX_NAME:
            return directory in self.indexes
        if pure.name == LOG_NAME:
            return directory in self.logs
        return False

    def has_member(self, path: str) -> bool:
        """Whether *path* (bundle-relative posix) names any member.

        Concepts, reserved files, assets **and** ignored members all count:
        ``ignore=`` declares "this is not a concept", not "this is not there",
        so a bundle that ignores ``_schema/`` and links into it has a working
        link, not a broken one.

        Matching is NFC-insensitive (§ADR-0027): the exact-match body above is
        the fast path, unchanged for every ASCII bundle-relative path that
        exists today; a non-ASCII query that misses it falls back to
        :attr:`_canonical`, which is empty unless the bundle carries a
        non-ASCII member.
        """
        return self.member_id(path) is not None

    def member_id(self, path: str) -> str | None:
        """The *raw* disk id *path* names, or ``None`` when it names nothing.

        The funnel every cross-origin lookup routes through: *path* may
        arrive from file content (a link destination, a §6.2 frontmatter
        value) rather than from the walk that built this ``Bundle``, and the
        two can disagree about Unicode normalization form while naming the
        same file. The raw id -- byte-identical to what ``readdir`` returned
        -- is what a caller must use to reopen the file or key a mapping
        built from :attr:`concepts` / :attr:`assets` / :attr:`indexes` /
        :attr:`logs`.
        """
        member = path.strip()
        if not member:
            return None
        if self._raw_member(member):
            return member
        if member.isascii():
            return None
        return self._canonical.get(canonical_id(member))


def _files(root: Path, *, unreadable: dict[str, str]) -> Iterator[Path]:
    """Yield every file under *root*, depth-first, in sorted order.

    Three walk defaults, documented because they are choices rather than
    deductions.

    A directory or file named ``.git`` is never a member, **at any depth**.
    This is the one name where over-inclusion is catastrophic rather than
    untidy: a bundle carrying a vendored or submodule checkout would otherwise
    walk that checkout's entire object store into ``assets``, and a real
    ``.git`` holds 10**4 to 10**5 files. It is the only name on the list --
    a deny-list of tool directories is incomplete by construction, and
    ``ignore=`` is the surface for every other exclusion a caller wants.

    Every *other* entry whose name begins with ``.`` is excluded **only at the
    bundle root**. The depth distinction is not a heuristic: the bundle root is
    where tooling parks its own dot-entries -- ``.obsidian/``, ``.templates/``,
    ``.DS_Store``, ``.gitignore`` -- because bundle-scoped tool config belongs
    beside the bundle. A dot-directory nested *inside* the tree only exists
    because something deliberately created a path there, which is exactly what
    a repository-mirror lane does when it writes
    ``repositories/<repo>/.agents/...``. The cut tracks the real difference
    between the bundle's housekeeping and the content the bundle carries, so
    it needs no list of names and cannot go stale. The OKF v0.2 spec says
    nothing about hidden entries; this is policy, not a spec requirement.

    Directory symlinks are not followed, for loop safety. A link into an
    excluded entry or an unfollowed symlink therefore reports broken, which is
    the honest answer given they are not in the model.

    *root* itself is read unguarded (``guarded=False``): a *root* that does
    not exist, or is not a directory, is a caller error, not bundle content,
    and letting ``iterdir()``'s ``OSError`` propagate is what stops a typo'd
    path from looking like a legitimately empty ``Bundle``. A *nested*
    directory that cannot be iterated -- permission denied, most often -- is
    the opposite: it is content the bundle carries, so it is recorded in
    *unreadable* keyed by its bundle-relative posix path and the walk
    continues past it, exactly as an unreadable file does a few lines below.
    """

    def walk(directory: Path, *, guarded: bool, depth: int) -> Iterator[Path]:
        if guarded:
            try:
                entries = sorted(directory.iterdir())
            except OSError as exc:
                relative = directory.relative_to(root).as_posix()
                unreadable[relative] = f"could not be read: {exc}"
                return
        else:
            entries = sorted(directory.iterdir())
        for entry in entries:
            if entry.name == GIT_DIR_NAME:
                continue
            if depth == 0 and entry.name.startswith("."):
                continue
            if entry.is_symlink() and entry.is_dir():
                continue
            if entry.is_dir():
                yield from walk(entry, guarded=True, depth=depth + 1)
            else:
                yield entry

    yield from walk(root, guarded=False, depth=0)


def _directory_id(relative: str) -> str:
    parent = PurePosixPath(relative).parent.as_posix()
    return "" if parent == "." else parent


def load(root: Path, *, ignore: Sequence[str] = ()) -> Bundle:
    """Walk *root* once and load every member.

    *ignore* patterns are ``fnmatch`` globs matched case-sensitively against
    bundle-relative posix paths (``"_schema/*"``, ``"**/*.tmp"``). ``*`` crosses
    ``/``, so ``"_schema/*"`` also excludes anything nested beneath it.
    ``fnmatchcase`` rather than ``fnmatch``: the latter folds case on macOS and
    Windows, so the same bundle would load differently on different machines.

    Spec §11 requires the walk to survive one bad file, and ``Document.parse``
    already guarantees it never raises for content. The remaining failure
    modes all live here: a member that is not valid UTF-8, an ``OSError`` on
    reading a file, and an ``OSError`` on iterating a nested directory. Each
    is recorded in ``unreadable`` and the walk continues. The one exception is
    *root* itself: a *root* that does not exist, or is not a directory, is a
    caller error and raises rather than producing a valid-looking empty
    ``Bundle`` (see :func:`_files`).
    """
    concepts: dict[str, Document] = {}
    indexes: dict[str, Document] = {}
    logs: dict[str, Document] = {}
    assets: set[str] = set()
    ignored: set[str] = set()
    unreadable: dict[str, str] = {}
    canonical: dict[str, str] = {}

    for path in _files(root, unreadable=unreadable):
        relative = path.relative_to(root).as_posix()
        if any(fnmatchcase(relative, pattern) for pattern in ignore):
            ignored.add(relative)
            if not relative.isascii():
                canonical[canonical_id(relative)] = relative
            continue
        if path.suffix != ".md":
            assets.add(relative)
            if not relative.isascii():
                canonical[canonical_id(relative)] = relative
            continue
        try:
            document = Document.load(path)
        except UnicodeDecodeError as exc:
            unreadable[relative] = f"not valid UTF-8: {exc}"
            continue
        except OSError as exc:
            unreadable[relative] = f"could not be read: {exc}"
            continue
        if path.name == INDEX_NAME:
            indexes[_directory_id(relative)] = document
        elif path.name == LOG_NAME:
            logs[_directory_id(relative)] = document
        else:
            concepts[relative[: -len(".md")]] = document
        if not relative.isascii():
            canonical[canonical_id(relative)] = relative

    return Bundle(
        root=root,
        concepts=MappingProxyType(dict(sorted(concepts.items()))),
        indexes=MappingProxyType(dict(sorted(indexes.items()))),
        logs=MappingProxyType(dict(sorted(logs.items()))),
        assets=frozenset(assets),
        ignored=frozenset(ignored),
        unreadable=MappingProxyType(dict(sorted(unreadable.items()))),
        _canonical=MappingProxyType(dict(sorted(canonical.items()))),
    )
