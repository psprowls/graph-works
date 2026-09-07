"""SessionEnd hook: copy this session's transcript (+ any subagent sidechain
transcripts) into the active work item's owned references directory.

Not registered by hooks.json: this is an opt-in hook. `gw config hooks
enable transcript` (or /gw:onboard -> Q4) registers it in the repo's
.claude/settings.local.json, bound to the interpreter that installed gw.

This is a thin shim over `graph_works_core.transcript_capture` --
fail-open on its own terms, so that even an import failure exits 0 rather
than blocking session end.
"""

import os
import sys

try:
    from graph_works_core.transcript_capture import main
except BaseException:
    raise SystemExit(0)

raise SystemExit(main(sys.stdin, os.environ))
