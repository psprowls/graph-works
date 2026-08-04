"""The mutable document object binding raw bytes to the typed view."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarbool import ScalarBoolean

from okf_io import _yaml
from okf_io._yaml import Split, Style
from okf_io.models import PREFERRED_KEY_ORDER, Frontmatter, build_frontmatter

_DELIM = "---"

DateMode = Literal["iso", "native"]


@dataclass(frozen=True, slots=True)
class ParseError:
    """Why a concept's frontmatter could not be read.

    Its presence never prevents a ``Document`` from existing: spec §11 requires
    a bundle walk to survive one bad file.
    """

    kind: Literal["unterminated", "yaml", "not-a-mapping"]
    message: str
    line: int | None = None


def _error_line(exc: YAMLError) -> int | None:
    """Locate *exc* as a 1-based line number in the original document.

    Prefers ``context_mark`` when it precedes ``problem_mark``. For the errors
    a human actually makes by hand -- an unterminated quote, an unclosed flow
    collection -- ``problem_mark`` sits where the parser gave up, which is the
    closing delimiter or EOF, while ``context_mark`` sits on the token that
    failed to close. Reporting the former sends the author to a line that looks
    fine.
    """
    problem = getattr(exc, "problem_mark", None)
    context = getattr(exc, "context_mark", None)
    mark = problem
    if context is not None and (problem is None or int(context.line) < int(problem.line)):
        mark = context
    if mark is None:
        return None
    # +1 for ruamel's 0-based line, +1 for the opening delimiter line.
    return int(mark.line) + 2


def _plain(value: Any, dates: DateMode) -> Any:  # noqa: ANN401 -- flattens an arbitrary raw YAML value
    """Flatten ruamel types to builtins, optionally rendering dates as ISO.

    Order matters: ``str`` before ``Sequence`` (str is a Sequence), ``bool``
    and ``ScalarBoolean`` before ``int`` (ruamel's ScalarBoolean subclasses
    int, not bool), and ``datetime`` before ``date``.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, ScalarBoolean):
        return bool(value)
    if isinstance(value, datetime):
        return value.isoformat() if dates == "iso" else value
    if isinstance(value, date):
        return value.isoformat() if dates == "iso" else value
    if isinstance(value, Mapping):
        return {str(k): _plain(v, dates) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return [_plain(v, dates) for v in value]
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return value


def _splice(orig: str, pristine: str, mutated: str) -> str | None:
    """Apply the *pristine* → *mutated* line delta onto *orig*.

    ``pristine`` is what ruamel emits for the **unmodified** parsed data;
    ``mutated`` is what it emits after the edit. The difference between those
    two renderings is exactly the edit. Everything else is dialect noise —
    re-folded plain scalars, unpadded flow braces — and must never reach the
    file, so unchanged regions are copied from *orig* verbatim.

    This is what makes the mutation-diff property hold for dialects ruamel
    cannot reproduce at all. Returns ``None`` when the two renderings cannot be
    aligned to *orig*, in which case the caller falls back to full re-emission.
    """
    orig_lines = _yaml._lines(orig)
    pristine_lines = _yaml._lines(pristine)
    mutated_lines = _yaml._lines(mutated)

    blocks = SequenceMatcher(None, pristine_lines, orig_lines, autojunk=False).get_matching_blocks()

    def to_orig(index: int) -> int | None:
        """Map a pristine line index onto *orig*, or None if it is not anchored.

        An index inside a matching block maps exactly. An index in an unmatched
        gap does not: ruamel rendered that line differently from the source, so
        there is no line in *orig* it corresponds to. Snapping such an index
        forward to the next anchor silently reattributes it -- the edit lands
        beside the original line instead of replacing it (a duplicate key) and
        the cursor jumps over whatever sat at the anchor (a deleted key). None
        sends the caller to full re-emission, which is merely untidy.
        """
        for start, orig_start, size in blocks:
            if start <= index < start + size:
                return orig_start + (index - start)
            if index < start:
                return None
        return len(orig_lines)

    out: list[str] = []
    cursor = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(
        None, pristine_lines, mutated_lines, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            continue
        start = to_orig(i1)
        if start is None:
            return None
        # Map the LAST replaced line, not the exclusive end: `i2` is one past
        # the range and may land on a line the edit does not own.
        if i2 > i1:
            last = to_orig(i2 - 1)
            if last is None:
                return None
            end = last + 1
        else:
            end = start
        if start < cursor or end < start:
            return None
        out.extend(orig_lines[cursor:start])
        out.extend(mutated_lines[j1:j2])
        cursor = end
    out.extend(orig_lines[cursor:])
    return "".join(out)


@dataclass(slots=True)
class Document:
    """A concept file as raw text plus a round-trippable frontmatter map.

    ``fm_raw`` is the storage layer and ``fm`` the view layer (ADR-0001).
    Nested edits go through ``fm_raw`` directly; a caller doing so must call
    :meth:`refresh` and is responsible for the dirty flag, which is what
    :meth:`mark_dirty` is for. That pairing is the documented escape hatch —
    there is deliberately no dotted-path setter.
    """

    path: Path | None
    raw_text: str
    fm_raw: CommentedMap
    body: str
    parse_error: ParseError | None
    _split: Split
    _style: Style
    _dirty: bool = False
    _view: Frontmatter | None = field(default=None, repr=False)

    @classmethod
    def parse(cls, text: str, *, path: Path | None = None) -> Document:
        """Parse concept *text*. **Never raises for content reasons.**

        A malformed concept yields a Document with ``fm_raw == {}``, the body
        still populated where the splitter could determine it, and a populated
        ``parse_error``.
        """
        split = _yaml.split(text)
        style = _yaml.sniff_style(split.fm_text)
        if not split.has_frontmatter and "\r\n" in split.body:
            # Nothing to sniff: `fm_text` is empty, so the sniffer defaulted to
            # LF. Synthesizing a block onto this document would then staple LF
            # delimiters to a CRLF body. The body is the only evidence there is.
            style = replace(style, newline="\r\n")
        fm_raw = CommentedMap()
        error: ParseError | None = None

        if split.unterminated:
            # Always line 1: the fault is that the block never closes, not that
            # anything is wrong at the opening delimiter.
            error = ParseError("unterminated", "Unterminated YAML frontmatter block", 1)
        elif split.has_frontmatter:
            try:
                fm_raw = _yaml.load_fm(split.fm_text)
            except _yaml.NotAMappingError as exc:
                error = ParseError("not-a-mapping", str(exc), None)
            except YAMLError as exc:
                error = ParseError("yaml", str(exc), _error_line(exc))
            except RecursionError as exc:
                # Deeply nested frontmatter blows the parser's stack. That is a
                # property of the content, so it must not escape: a bundle walk
                # has to survive one pathological file.
                error = ParseError("yaml", f"Frontmatter nested too deeply to parse: {exc}", None)

        return cls(
            path=path,
            raw_text=text,
            fm_raw=fm_raw,
            body=split.body,
            parse_error=error,
            _split=split,
            _style=style,
        )

    @classmethod
    def load(cls, path: Path) -> Document:
        """Read and parse the concept at *path*.

        Reads bytes rather than text so CRLF endings and a BOM survive; a
        missing or unreadable file raises ``OSError``, which is a caller error,
        not a content error.

        Two exception classes escape, both deliberately. ``OSError`` as above,
        and ``UnicodeDecodeError`` for a file that is not valid UTF-8 -- OKF
        concepts are UTF-8 by definition, so a non-UTF-8 file is not a
        malformed concept but a different kind of file, and silently
        substituting replacement characters would break the byte-fidelity
        guarantee on save. A caller walking an untrusted tree should catch it.
        """
        return cls.parse(path.read_bytes().decode("utf-8"), path=path)

    @property
    def fm(self) -> Frontmatter:
        """The memoized typed view."""
        if self._view is None:
            self._view = build_frontmatter(self.fm_raw, body=self.body)
        return self._view

    @property
    def has_frontmatter(self) -> bool:
        """Whether the file carries a frontmatter block at all.

        Distinct from ``fm_raw == {}``: ``---\\n---`` is a block containing
        nothing, and a file with no delimiters is no block. The
        ``frontmatter.missing`` rule needs to tell those apart, and reaching
        into ``_split`` from another module would be worse than exposing it.
        """
        return self._split.has_frontmatter

    @property
    def body_line_offset(self) -> int:
        """How many whole lines precede ``body`` in the file.

        A 1-based body line ``n`` sits at file line ``n + body_line_offset``.
        markdown-it's token maps are body-relative, so this is what makes a
        finding's line number point at the right line of the right file.

        Computed from the verbatim pieces the splitter kept, never from
        ``raw_text.find(body)``: a search would mis-locate on the BOM and CRLF
        fixtures built to catch exactly that. Only a run ending in a
        line break counts, so a lone BOM -- which is a fragment, not a line --
        contributes nothing.
        """
        split = self._split
        prefix = split.bom + split.open_delim + split.fm_text + split.close_delim + split.gap
        return sum(1 for line in _yaml._lines(prefix) if line.endswith(("\n", "\r")))

    def mark_dirty(self) -> None:
        """Declare that ``fm_raw`` was mutated directly. Pairs with the escape hatch.

        Required after any direct edit of ``fm_raw``. **Skipping it means
        ``save()`` silently writes the pre-edit bytes** -- ``serialize()``
        short-circuits to ``raw_text`` whenever the document is clean, and it
        has no way to notice a mutation nobody declared.
        """
        self._dirty = True
        self._view = None

    def refresh(self) -> None:
        """Discard the memoized view without marking the document dirty.

        Rebuilding the view is *not* the same as declaring an edit. After a
        direct ``fm_raw`` mutation, calling only ``refresh()`` leaves ``fm``
        and ``fm_raw`` agreeing with each other while ``save()`` still writes
        the original bytes -- the most confusing shape this class can be in.
        Use :meth:`mark_dirty` for that case; ``refresh()`` is for discarding a
        view you no longer trust without asserting the document changed.
        """
        self._view = None

    def _require_mutable(self) -> None:
        if self.parse_error is not None:
            raise ValueError(
                f"Cannot mutate a document that failed to parse "
                f"({self.parse_error.kind}): {self.parse_error.message}"
            )

    def _insert_position(self, key: str) -> int:
        """Where a new *key* goes.

        Immediately after the last present key that precedes it in
        ``PREFERRED_KEY_ORDER``; the front when none precedes it; the end when
        the key is not in the preferred order at all.
        """
        keys = list(self.fm_raw.keys())
        if key not in PREFERRED_KEY_ORDER:
            return len(keys)
        rank = PREFERRED_KEY_ORDER.index(key)
        position = 0
        for i, existing in enumerate(keys):
            name = str(existing)
            if name in PREFERRED_KEY_ORDER and PREFERRED_KEY_ORDER.index(name) < rank:
                position = i + 1
        return position

    def set(self, key: str, value: Any) -> None:  # noqa: ANN401 -- stores an arbitrary raw YAML value
        """Set a top-level frontmatter key.

        An existing key is edited in place; a new one is inserted per
        ``PREFERRED_KEY_ORDER``. Existing documents are never reordered.
        Nested edits go through ``fm_raw`` plus :meth:`mark_dirty`.
        """
        self._require_mutable()
        if key in self.fm_raw:
            self.fm_raw[key] = value
        else:
            self.fm_raw.insert(self._insert_position(key), key, value)
        self.mark_dirty()

    def delete(self, key: str) -> None:
        """Delete a top-level frontmatter key. A no-op when it is absent."""
        self._require_mutable()
        if key in self.fm_raw:
            del self.fm_raw[key]
            self.mark_dirty()

    def set_body(self, body: str) -> None:
        """Replace the body.

        Writes through to the ``Split`` as well as the ``body`` field:
        :func:`_yaml.join` reassembles the file from ``_split.body``, so an
        assignment to ``body`` alone leaves ``save()`` writing the pre-edit
        body while every reader agrees it changed.
        """
        self._require_mutable()
        self.body = body
        self._split = replace(self._split, body=body)
        self.mark_dirty()

    def _pristine_render(self) -> str | None:
        """Render the *unmodified* parsed data.

        Re-loaded from the original text rather than cached, so it stays
        pristine no matter what a caller did to ``fm_raw`` directly.
        """
        try:
            data = _yaml.load_fm(self._split.fm_text)
        except (YAMLError, _yaml.NotAMappingError):
            return None
        return _yaml.dump_fm(data, style=self._style)

    def serialize(self) -> str:
        """Render the document.

        Clean documents return ``raw_text`` verbatim. Dirty documents go
        through :func:`_splice`, so an edit to one key leaves its neighbours
        untouched even in dialects ruamel cannot reproduce.

        When the edited lines cannot be anchored in the original, ``_splice``
        declines and the whole frontmatter is re-emitted instead. That output
        is correct and re-parses, but carries a larger diff. The guarantee is
        "always valid, never lossy, usually minimal" -- not "minimal always".
        """
        if not self._dirty:
            return self.raw_text

        mutated = _yaml.dump_fm(self.fm_raw, style=self._style)
        split = self._split

        if split.has_frontmatter and not split.unterminated:
            pristine = self._pristine_render()
            if pristine is not None:
                spliced = _splice(split.fm_text, pristine, mutated)
                if spliced is not None:
                    return _yaml.join(split, spliced)
            return _yaml.join(split, mutated)

        if not split.has_frontmatter and not self.fm_raw:
            # A body-only edit must not staple a frontmatter block onto a file
            # that never carried one -- which is every index file (§8). The
            # synthesis below is for a document that gained a *key*.
            return _yaml.join(split)

        newline = self._style.newline
        synthesized = replace(
            split,
            open_delim=f"{_DELIM}{newline}",
            close_delim=f"{_DELIM}{newline}",
            gap=split.gap or newline,
            has_frontmatter=True,
            unterminated=False,
        )
        return _yaml.join(synthesized, mutated)

    def save(self, path: Path | None = None) -> None:
        """Write the document. Writes bytes, so no newline translation occurs."""
        target = path or self.path
        if target is None:
            raise ValueError("Document has no path; pass one to save()")
        target.write_bytes(self.serialize().encode("utf-8"))

    def fm_data(self, dates: DateMode = "iso") -> dict[str, Any]:
        """Extension point #2 (ADR-0005): a plain projection of the frontmatter.

        External validators cannot consume a ``CommentedMap`` containing
        ``date`` objects. With ``dates="iso"`` the result survives
        ``json.dumps`` with no custom encoder; ``dates="native"`` flattens the
        ruamel types but leaves date objects alone.
        """
        result = _plain(self.fm_raw, dates)
        return result if isinstance(result, dict) else {}


def rendered_with_body(document: Document, body: str) -> str:
    """Render *document* as it would stand carrying *body*. Does not mutate it.

    The writers need the "after" text of an edit they may never perform --
    ``dry_run=True`` is their default -- and the ``Document`` they are handed
    belongs to a shared :class:`~okf_io.bundle.Bundle`. Editing it in order to
    read the result would leave the in-memory bundle carrying a change the
    caller declined.

    **Aliasing hazard:** The clone uses ``dataclasses.replace`` (a shallow copy),
    so ``clone.fm_raw is document.fm_raw`` is ``True`` — the shared mutable
    ``CommentedMap`` could be corrupted by a future extension that edits
    frontmatter through this clone. The contract holds today only because
    ``set_body`` never touches ``fm_raw``.
    """
    clone = replace(document)
    clone.set_body(body)
    return clone.serialize()


def parse(text: str, *, path: Path | None = None) -> Document:
    """Parse concept *text*. Never raises for content reasons."""
    return Document.parse(text, path=path)


def load(path: Path) -> Document:
    """Read and parse the concept at *path*. Propagates ``OSError``."""
    return Document.load(path)
