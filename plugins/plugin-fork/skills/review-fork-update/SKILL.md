---
name: review-fork-update
description: Use when the user explicitly requests an upstream update, review of a fork update candidate, acceptance, or rollback of maintained agent skills.
---

# Review a fork update

Use the standalone `plugin-fork` CLI for one named variant at a time. Keep the
user's intent and local adaptations visible throughout the review.

## Prepare and compare

1. Read status and the recorded intent. Confirm the variant, content/state locations,
   and incoming source. Unknown origin needs original-base evidence reconciliation
   through adoption before a three-way update is available.

```bash
plugin-fork status "$VARIANT" --state-dir "$STATE" --json
plugin-fork update "$VARIANT" --source "$INCOMING" --state-dir "$STATE" --json
```

2. Keep the returned update ID and candidate path, even when blocked. Read both
   comparisons: original base → original incoming, and current maintained local →
   proposed candidate. Explain upstream changes, preserved adaptations, conflicts,
   dependency findings, and scan limits against the recorded intent.
3. Offer behavioral review as an optional step. Report its status explicitly:
   `not_requested`, `completed_without_findings`, or `completed_with_findings`. Preserve supplied
   review findings and evidence even after reconciling them. A clean merge or a Git
   commit does not establish behavioral compatibility or acceptance.

## Resolve the final candidate

4. Edit only the returned staging candidate. Keep original archives, comparison
   evidence, ledger, and accepted base under CLI ownership. Address required
   dependencies and discuss behavioral findings using the user's intended behavior.
5. After the final edit, inventory the candidate again. Bind any supplied review to
   the digest returned **after the final edit**; even a supplied post-review digest
   becomes historical if you make another correction. Re-inspect after that correction
   and use the newly returned digest, rather than assuming a scenario hash is final. Retain each original
   finding and describe its resolution in the review evidence. Use the closed Review
   JSON contract in the package README, including all fields.

```bash
plugin-fork inspect "$CANDIDATE" --json
```

6. Record **every conflict ID**, its final candidate-relative path, and SHA-256 of
   the final file, or explicit deletion (`hash: null`, `deleted: true`). Removing
   markers alone does not resolve recorded conflicts. The README gives the exact
   Resolution JSON. Recompute hashes after edits; an editable update candidate may
   keep its update ID, while old review hashes and acceptance previews become stale.

## Preview, approve, apply

7. Prepare a fresh acceptance after all edits and review reconciliation. Omit
   `--review` when behavioral review was not requested; omit `--resolutions` when
   there are no conflicts.

```bash
plugin-fork accept "$VARIANT" --candidate "$UPDATE_ID" --review "$REVIEW_JSON" --resolutions "$RESOLUTIONS_JSON" --state-dir "$STATE" --json
```

8. Present required approval fields: variant and storage owners, both diff summaries,
   final candidate digest, explicit review status and retained findings, conflict
   resolutions, remaining warnings, and the **new acceptance preview ID**. Ask for
   approval of that concrete preview. Earlier conceptual approval does not name it.
   Apply exactly the approved acceptance ID and report status.

```bash
plugin-fork accept --apply "$ACCEPT_ID" --state-dir "$STATE" --json
plugin-fork status "$VARIANT" --state-dir "$STATE" --json
```

## Rollback

Preview restoration of the latest acceptance. Explain the immediate preaccept
content and tracking restored, including preserved preaccept local edits and dirty
warnings; generation advances. Incomplete transaction recovery takes priority.
Present the affected variant/owners and returned rollback ID, ask for approval,
then apply that exact ID and report status.

```bash
plugin-fork rollback "$VARIANT" --state-dir "$STATE" --json
plugin-fork rollback --apply "$ROLLBACK_ID" --state-dir "$STATE" --json
plugin-fork status "$VARIANT" --state-dir "$STATE" --json
```

Shared bindings observe the maintained root. Independent copies require their own
review and approval. Finish this variant before any separately requested work;
keep installing this optional companion outside the maintenance workflow.
