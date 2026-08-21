"""One file in, text plus a title guess plus a binary flag out.

Supported formats: `.md` `.txt` `.html` `.htm` `.json` `.csv`. Anything else
that decodes as UTF-8 is returned verbatim with no title. Anything that does
not decode is reported as binary with no text at all -- never as replacement
characters.
"""

from __future__ import annotations

import contextlib
import html.parser
import json
from pathlib import Path

JSON_CHARS = 100_000
CSV_LINES = 50
TITLE_SCAN_LINES = 20


class _HTMLTextExtractor(html.parser.HTMLParser):
    """Collects non-`script`/`style` text plus the `<title>` of an HTML document."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title: str | None = None
        self._in_title = False
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip = True
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = False
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title and self.title is None:
            self.title = data.strip() or None
        else:
            text = data.strip()
            if text:
                self.parts.append(text)

    def text(self) -> str:
        return "\n".join(self.parts)


def extract(path: Path) -> tuple[str, str | None, bool]:
    """One file in, `(text, title, binary)` out.

    *binary* is `True` for material that does not decode strictly as UTF-8. Its
    text is `""` in that case, never a string of replacement characters:
    manufacturing text that was never there is how a page gets composed from
    noise, and an empty extract is the truthful answer a caller can branch on.
    The strict decode runs before the format dispatch, so a PDF named `.md` is
    reported as binary rather than mojibake.
    """
    ext = path.suffix.lower()
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "", None, True
    if ext in {".md", ".txt"}:
        title = None
        for line in text.splitlines()[:TITLE_SCAN_LINES]:
            if line.startswith("# "):
                title = line[2:].strip()
                break
        return text, title, False
    if ext in {".html", ".htm"}:
        parser = _HTMLTextExtractor()
        with contextlib.suppress(Exception):
            parser.feed(text)
        return parser.text(), parser.title, False
    if ext == ".json":
        try:
            obj = json.loads(text)
            return json.dumps(obj, indent=2)[:JSON_CHARS], None, False
        except Exception:
            return text, None, False
    if ext == ".csv":
        return "\n".join(text.splitlines()[:CSV_LINES]), None, False
    return text, None, False
