"""Stable exit codes from v1 forward. Script consumers depend on these."""

from __future__ import annotations

SUCCESS = 0
GENERIC = 1
STALE = 2
NOT_INITIALIZED = 3
SCHEMA_MISMATCH = 4
NOT_IN_GIT_REPO = 5
UPDATE_IN_PROGRESS = 6
AMBIGUOUS = 7
