"""Shared substrate for display reads over an OKF workspace bundle."""

from okf_ext.readindex import PageContent

from gw_kernel.read_session.bundle_backend import BundleSession
from gw_kernel.read_session.index_backend import IndexSession
from gw_kernel.read_session.location import SESSION_FORMAT, database_path, fingerprint
from gw_kernel.read_session.model import (
    Backend,
    FallbackReason,
    IndexRevision,
    ReadSession,
    index_revision,
    reconciled_document,
)
from gw_kernel.read_session.open import borrow_read_session, open_read_session
from gw_kernel.read_session.snapshot import SnapshotSession, materialize

__all__ = [
    "SESSION_FORMAT",
    "Backend",
    "BundleSession",
    "FallbackReason",
    "IndexRevision",
    "IndexSession",
    "PageContent",
    "ReadSession",
    "SnapshotSession",
    "borrow_read_session",
    "database_path",
    "fingerprint",
    "index_revision",
    "materialize",
    "open_read_session",
    "reconciled_document",
]
