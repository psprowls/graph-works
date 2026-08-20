"""The util vertical: `run_log` and `run_tokens_update`, one file.

1:1 with the `gw util` sub-app, which is what every other vertical here is to
its own sub-app. Layer 2 — independent of every other vertical; imports only
`workspace`.

The two commands share a directory rather than splitting into `logs/` and
`tokens/` because the correspondence to the sub-app is the organizing rule
(D-037), and neither is large enough to want its own.
"""

from __future__ import annotations
