"""Fixture discovery for the okf-ext suite.

Named `ext_helpers` rather than `helpers`: both test directories are on
`pythonpath`, and okf-io's `tests/helpers.py` already owns that module name.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from okf_io import Bundle, load_bundle

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TAGGED = FIXTURES / "tagged"
VOCABULARY = TAGGED / "_tags.yaml"
BAD_VERSION = FIXTURES / "bad_vocab_version.yaml"
BAD_DANGLING = FIXTURES / "bad_vocab_dangling.yaml"

#: Concepts the tag functions cannot read, and why. Tests assert against this
#: rather than restating the reasons, so a fixture change cannot leave a test
#: quietly asserting the old corpus.
UNUSABLE = {"broken": "parse-error", "scalar_tags": "tags-not-a-sequence"}


def read(path: Path) -> str:
    """Read without newline translation, so CRLF and a BOM would survive."""
    return path.read_bytes().decode("utf-8")


def tagged_bundle() -> Bundle:
    """The read-only corpus. Never pass this to `apply()`."""
    return load_bundle(TAGGED)


def bundle_copy(tmp_path: Path) -> Path:
    """A writable byte-identical copy of the corpus.

    Every mutating test goes through this. `copy2` preserves bytes, which the
    round-trip differential depends on.
    """
    target = tmp_path / "tagged"
    shutil.copytree(TAGGED, target, copy_function=shutil.copy2)
    return target


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under *root*, keyed by relative posix path."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


SCHEMAD = FIXTURES / "schemad"
SCHEMA_DIR = SCHEMAD / "_schema"

#: What the schema walk must report, as `(code, path)`. Asserted against rather
#: than restated inside the test, so a fixture change cannot leave a test
#: quietly asserting the old corpus.
SCHEMA_EXPECTED = {
    ("schemas.invalid", "metrics/orders.md"),
    ("schemas.invalid", "metrics/orphan.md"),
    ("schemas.no-schema-for-type", "glossary/term.md"),
}


def schemad_bundle(*, ignore=()):
    """The schema corpus. Read-only -- nothing here mutates a bundle."""
    return load_bundle(SCHEMAD, ignore=ignore)


UNRENDERED = FIXTURES / "unrendered"

#: What the render walk must report, as `(code, path)`. Asserted against rather
#: than restated inside the test, so a fixture change cannot leave a test
#: quietly asserting the old corpus -- the habit `SCHEMA_EXPECTED` set.
#:
#: Every one of the four codes appears, which is what makes the "every member of
#: CODES is emitted" contract test meaningful over this one bundle.
RENDER_EXPECTED = {
    ("render.angle-bracket", "angle_placeholder.md"),
    ("render.callout", "callout_malformed.md"),
    ("render.callout", "callout_unknown.md"),
    ("render.callout", "index.md"),
    ("render.table-pipe", "table_overwide.md"),
    ("render.wikilink", "log.md"),
    ("render.wikilink", "wikilink_empty.md"),
    ("render.wikilink", "wikilink_unbalanced.md"),
}


def unrendered_bundle():
    """The render corpus. Read-only -- nothing here mutates a bundle."""
    return load_bundle(UNRENDERED)


UNHEALTHY = FIXTURES / "unhealthy"

#: What the health walk must report over `unhealthy/` at `today=2026-08-06` and
#: the default 14-day gap, as `(code, path)`. `hub.md` is uncited because
#: nothing cites *it*; `untitled.md` is absent because a concept with no title
#: never joins a duplicate group.
HEALTH_EXPECTED = {
    ("health.duplicate-title", "dup-a.md"),
    ("health.duplicate-title", "dup-b.md"),
    ("health.log-gap", "log.md"),
    ("health.uncited", "hub.md"),
    ("health.uncited", "orphan.md"),
}


def unhealthy_bundle():
    """The health corpus. Read-only -- nothing here mutates a bundle."""
    return load_bundle(UNHEALTHY)


#: The two bundles vendored verbatim from `GoogleCloudPlatform/knowledge-catalog`
#: into okf-io's fixture tree. Reached by derived path rather than by importing
#: okf-io's `helpers` module: both test directories are on `pythonpath`, so the
#: import would work, but it would bind this suite to okf-io's test-module import
#: order for one constant. Do not edit these bundles -- see
#: `packages/okf-io/tests/fixtures/FIXTURES.md`.
VENDORED_BUNDLES = Path(__file__).resolve().parents[2] / "okf-io" / "tests" / "fixtures" / "bundles"
ACME_RETAIL = VENDORED_BUNDLES / "acme_retail"
GA4 = VENDORED_BUNDLES / "ga4"
