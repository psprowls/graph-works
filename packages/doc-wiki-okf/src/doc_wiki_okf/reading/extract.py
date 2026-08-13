"""One file in, text plus a title guess out.

Supported formats: `.md` `.txt` `.html` `.htm` `.json` `.csv`. Anything else
is decoded as UTF-8 with replacement and returned with no title.
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


def extract(path: Path) -> tuple[str, str | None]:
    ext = path.suffix.lower()
    data = path.read_bytes()
    if ext in {".md", ".txt"}:
        text = data.decode("utf-8", errors="replace")
        title = None
        for line in text.splitlines()[:TITLE_SCAN_LINES]:
            if line.startswith("# "):
                title = line[2:].strip()
                break
        return text, title
    if ext in {".html", ".htm"}:
        parser = _HTMLTextExtractor()
        with contextlib.suppress(Exception):
            parser.feed(data.decode("utf-8", errors="replace"))
        return parser.text(), parser.title
    if ext == ".json":
        try:
            obj = json.loads(data.decode("utf-8", errors="replace"))
            return json.dumps(obj, indent=2)[:JSON_CHARS], None
        except Exception:
            return data.decode("utf-8", errors="replace"), None
    if ext == ".csv":
        text = data.decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[:CSV_LINES]), None
    return data.decode("utf-8", errors="replace"), None
