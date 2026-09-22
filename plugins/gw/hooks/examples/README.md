# Session transcript capture

`session-end-transcript-capture.py` is an opt-in `SessionEnd` hook. Enable it with
`gw config hooks enable transcript` (or answer "Yes" to Q4 in `/gw:onboard`); disable it
with `gw config hooks disable transcript`. It registers nothing on its own — an uninstalled
or disabled hook produces no transcripts and no trace of their absence.

## What gets captured

When enabled, every Claude Code session end copies:

- the full session transcript (every message, tool call, and tool result in the session,
  not just the parts relevant to the work item)
- every subagent sidechain transcript spawned during the session

into the active work item's `references/` directory, as
`<NN>-<phase>-transcript.jsonl` (and `<NN>-<phase>-transcript-subagent-<id>.jsonl` per
sidechain). "Active" means `layout.cache_dir / "active-work.json"` names a pointer at session
end — stamped by `gw work touch-active-work`, which `/gw:workflow` runs before every stage
skill, and by a dispatch (entry) `gw work advance`; an exit advance never re-stamps it. For a
stage session driven by `/gw:workflow`, that means the transcript is filed under the phase
the session ran. But the pointer is a session-end mechanism, not a `/gw:workflow`-only one:
any other session that ends while the pointer is still set — an ad hoc session against the
same work item, or a `/gw:workflow` run that stops on a blocker before reaching its exit
advance — is captured too, under whatever phase the pointer names, and overwrites that
phase's existing transcript file. No pointer means no copy.

**This is a raw, unfiltered copy.** There is no redaction pass. Anything typed or pasted
into the session — credentials, unrelated scratch discussion, material that has nothing to
do with the work item — is captured verbatim along with everything else.

## What gets committed

The work-tracker bundle (`okf/`) is ordinarily committed to git. If your bundle is
version-controlled, a captured transcript is committed the same way any other bundle file
is, the next time you commit `work/<item>/references/`. `*/references/*` files are bundle
**members** — the bundle walker never reads them as content and no lint rule inspects their
contents — but git does not know that distinction; a `.jsonl` transcript sized in the
megabytes both grows the repository and (if pushed) leaves whatever it captured wherever the
remote lives.

Consider a `.gitattributes` entry (Git LFS, or excluding `references/*transcript*.jsonl`
from the working tree entirely) before enabling this in a shared or public repository. There
is currently no built-in retention policy — an archived item keeps its transcripts
indefinitely unless something outside this hook prunes them.

## What gets recorded on the item page

Each phase's capture stamps one `sources[]` entry on the work item's own page (not one per
subagent sidechain):

```yaml
sources:
  - id: execute-transcript
    resource: /work/<slug>/references/03-execute-transcript.jsonl
    title: Execute session transcript
```

A rerun of the same phase overwrites the file and its entry in place — it does not
accumulate duplicates.
