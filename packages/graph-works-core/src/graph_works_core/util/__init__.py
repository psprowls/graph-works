"""The util vertical: `run_log`, `run_tokens_update`, and `build_report`.

1:1 with the `gw util` sub-app, which is what every other vertical here is to
its own sub-app. Layer 2 — independent of every other vertical; imports only
`workspace`.

`commands.py` holds the two verbs that mutate or count a bundle;
`platform.py` holds the read-only platform report and its provider seam. They
share a directory rather than splitting into `logs/`, `tokens/` and
`platform/` because the correspondence to the sub-app is the organizing rule
(D-037), and none is large enough to want its own.
"""

from __future__ import annotations
