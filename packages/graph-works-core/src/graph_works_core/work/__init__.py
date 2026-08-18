"""The work vertical: composed commands over `work-tracker-okf` -- `file`,
`next`, `status`, `lint`, `regen-index`, and decision-ledger CRUD. `advance`,
`archive` and `sync-children` are not here; see `commands.py`'s module
docstring for why. Layer 2 -- independent of every other vertical; imports
only `workspace`.
"""

from __future__ import annotations
