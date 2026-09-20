"""okf-io — read, derive from, and write back OKF v0.2 concept documents.

The core carries exactly **two runtime dependencies**, ``ruamel.yaml`` and
``markdown-it-py``. Adding a third is a deliberate decision: the list is a
scope boundary, not an implementation detail.

Two layers. :class:`Document` owns the raw text and a
round-trippable ``CommentedMap``; :class:`Frontmatter`, reachable as
``doc.fm``, is a frozen view built from it. Neither parsing nor building a view
raises for content reasons — a malformed concept still yields a Document, with
the failure in ``parse_error`` and lossy fields in ``fm.coercion_failures``.

A bundle is walked once (:func:`load_bundle`), the markdown link graph is
derived from that one walk (:func:`build_link_graph`), and :func:`validate`
runs a catalog of 29 conformance rules over both. Nothing rejects a bundle:
every rule reports and the caller decides. Three ways to load: :func:`parse`
reads in-memory text, :func:`load` reads one file, :func:`load_bundle` reads
a whole directory. The names are deliberately different for deliberately
different things.

Three write paths. :func:`update_index` reconciles a directory's ``index.md``
against the bundle (§8), :func:`append_log_entry` appends to ``log.md`` (§9),
and :func:`migrate` rewrites a v0.1 bundle into v0.2 form (§13.1). All three
default to ``dry_run=True``: writing is something a caller asks for. Under the
default ``descriptions="preserve"``, :func:`update_index` does not rewrite text
a person wrote -- okf-io owns which entries appear, the human owns what they
say. ``descriptions="refresh"`` rewrites entry text from concept descriptions,
or from a supplied ``describe=`` hook.

**Shadowing note:** ``okf_io.validate`` and ``okf_io.migrate`` are the
functions, not the submodules. Import submodule names with ``from
okf_io.validate import Finding, Report, RuleContext`` and ``from okf_io.migrate
import Migration, Unmigrated``. The idioms ``import okf_io.validate as m`` and
``from okf_io import validate as m`` both bind the function, so ``m.Finding``
will fail. This is deliberate: the function is what callers want at the front
door.
"""

from __future__ import annotations

from okf_io.bundle import Bundle
from okf_io.bundle import load as load_bundle
from okf_io.derive import (
    TrustTier,
    effective_status,
    is_stale,
    last_verified_at,
    trust_tier,
)
from okf_io.document import Document, ParseError, load, parse
from okf_io.index import (
    ChangeKind,
    Describe,
    Descriptions,
    Drift,
    EntryKind,
    EntryTarget,
    IndexChange,
    IndexEntry,
    IndexHeading,
    IndexOutline,
    IndexUpdate,
)
from okf_io.index import outline as outline_index
from okf_io.index import update as update_index
from okf_io.links import Link, LinkGraph
from okf_io.links import build as build_link_graph
from okf_io.log import Log, LogAppend, LogEntry, LogSection
from okf_io.log import append as append_log_entry
from okf_io.log import parse as parse_log
from okf_io.migrate import (
    Migration,
    MigrationChange,
    MigrationChangeKind,
    Unmigrated,
    UnmigratedReason,
    migrate,
)
from okf_io.models import (
    Actor,
    ActorKind,
    Attester,
    Executor,
    Frontmatter,
    Generated,
    Parameter,
    Source,
    UsageWindow,
    Verified,
    build_frontmatter,
    value_shape,
)
from okf_io.validate import Finding, Report, Rule, RuleContext, Severity, validate

__version__ = "0.2.5"

__all__ = [
    "Actor",
    "ActorKind",
    "Attester",
    "Bundle",
    "ChangeKind",
    "Describe",
    "Descriptions",
    "Document",
    "Drift",
    "EntryKind",
    "EntryTarget",
    "Executor",
    "Finding",
    "Frontmatter",
    "Generated",
    "IndexChange",
    "IndexEntry",
    "IndexHeading",
    "IndexOutline",
    "IndexUpdate",
    "Link",
    "LinkGraph",
    "Log",
    "LogAppend",
    "LogEntry",
    "LogSection",
    "Migration",
    "MigrationChange",
    "MigrationChangeKind",
    "Parameter",
    "ParseError",
    "Report",
    "Rule",
    "RuleContext",
    "Severity",
    "Source",
    "TrustTier",
    "Unmigrated",
    "UnmigratedReason",
    "UsageWindow",
    "Verified",
    "__version__",
    "append_log_entry",
    "build_frontmatter",
    "build_link_graph",
    "effective_status",
    "is_stale",
    "last_verified_at",
    "load",
    "load_bundle",
    "migrate",
    "outline_index",
    "parse",
    "parse_log",
    "trust_tier",
    "update_index",
    "validate",
    "value_shape",
]
