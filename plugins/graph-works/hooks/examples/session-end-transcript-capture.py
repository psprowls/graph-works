"""SessionEnd hook: copy this session's transcript (+ any subagent sidechain
transcripts) into the active work item's owned references directory.

Add this to your project's .claude/settings.json (see README, or run
/graph-works:onboard -> Feature 4). `gw config hooks enable transcript`
does this for you and binds the command to the interpreter that ran it.

This is a thin shim over `graph_works_core.transcript_capture.main` --
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
