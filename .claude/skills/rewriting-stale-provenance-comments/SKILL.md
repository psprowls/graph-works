---
name: rewriting-stale-provenance-comments
description: Use when a migration, port, or vendor-drop has landed and source files still contain docstrings or comments naming the now-deleted package, module, repo, or plugin path they came from — before merging a branch that ports code, or when auditing a diff full of "Ported from", "Adopted from", or "# Source:" style comments.
---

# Rewriting Stale Provenance Comments

## Overview

Porting or migrating code often leaves behind comments that cite the origin:
`Ported from graph_wiki_core.agent_loop`, `# Source: plugins/foo/bar.md`,
`Adopted from workspace_io's template`. Useful *during* the port as an audit
trail against the source. Useless — actively misleading — once the source
package is deleted: nobody can grep for it, and a future reader (human or
agent) has no way to verify or even locate what's being cited.

Two different things usually hide in the same comment: a **citation**
(package/repo/line-count naming the source) and **rationale** (why the code
is shaped the way it is). The citation rots. The rationale doesn't. Don't
delete both because one is stale.

## When to Use

- A port/migration epic is about to merge and the source packages it ported
  from are being retired or were never part of this repo (e.g. a plugin
  directory, a prior-generation package, a vendored upstream).
- Reviewing a diff that introduces phrases like "ported from", "adopted
  from", "# Source:", "verbatim", "reshaped by", "trimmed from", or a bare
  module path (`graph_wiki_core.foo`) that doesn't resolve in this repo.
- NOT for comments citing something that still exists and is importable in
  this repo or a live dependency — those citations stay verifiable, leave
  them alone.

## Core Pattern

1. **Find candidates mechanically.** Grep the diff (or the whole tree) for
   citation language and dead module/package names:
   ```bash
   git diff main...HEAD | awk '
   /^diff --git/ { file=$0; sub(/^diff --git a\//,"",file); sub(/ b\/.*/,"",file); next }
   /^\+/ && !/^\+\+\+/ {
     if ($0 ~ /[Pp]orted from|[Aa]dopted from|# Source:|reshaped by|trimmed from|verbatim/)
       print file ": " $0
   }'
   ```
   Add the specific dead package/module names (e.g. the `RETIRED = {...}` set
   if the port left one) and any plugin/repo path prefixes as extra
   alternatives in the pattern.

2. **Classify each hit by judgment, not regex** — this step can't be
   automated:
   - **Citation only, no rationale** ("ported nearly verbatim from X") →
     delete the line entirely, or fold into the commit message / migration
     work item instead.
   - **Citation + real rationale** ("every reference to `.graph-wiki/` is
     rewritten as 'the workspace directory' because it's bound at runtime,
     not a fixed subdirectory") → keep the rationale, strip the source name,
     reword so the sentence stands on its own without the dead citation.
   - **Nothing left once the citation is stripped** → the comment was pure
     lineage; delete it rather than leaving a vague husk.

3. **Rewrite in place.** Prefer present-tense, declarative statements about
   what the code does and why, matching this repo's own comment style (WHY
   only, no narration of WHAT). Don't reintroduce "why the alternative was
   rejected" narratives — state the current invariant, not the history.

4. **Spot-check test files and fixtures too** — dead-package guard tests
   (e.g. `RETIRED = {...}` import-boundary tests) are legitimate to keep
   even after this sweep; they're mechanical, not narrative, and don't rot
   the same way.

## Common Mistakes

- Deleting the whole comment when it had load-bearing rationale mixed in —
  re-read for a second sentence before cutting.
- Leaving the citation but "softening" it (e.g. "legacy code" instead of
  `graph_wiki_core`) — still a dangling, unverifiable reference; cut it or
  replace with a concrete, resolvable fact about *this* repo.
- Running only on `*.py`/`*.md` — provenance comments also show up in
  `*.toml`, fixture golden files, and asset templates.
