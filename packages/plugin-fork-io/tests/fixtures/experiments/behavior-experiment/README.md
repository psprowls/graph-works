# Preserving a local approval preference across an update

Date: 2026-09-12  
Status: proposed reconciliation in scratch; not installed or accepted as an update.

The user agreed to test this customization on the older local fork:

> When the implementation plan contradicts its specification, ask before changing
> the plan. Hold affected work until the user responds; independent work may continue.

Ordinary implementation choices consistent with both documents remain outside this
gate. The test uses the same pinned 6.2.0 and 6.3.0 sources as the parent experiment.
The previous experiments remain unchanged.

## Result

**The text merge returned zero conflicts but produced contradictory instructions.**
The local approval rule survived near the top, while new upstream sections directed
the controller to decide plan corrections and limited stopping to four other cases.
The rule's survival in the file did not establish that the workflow preserved it.

A manually reconciled candidate now applies the approval gate throughout setup,
plan correction, fix loops, final review, and the workflow diagram. This is a proposed
resolution based on the recorded intent, not an automatic semantic merge algorithm.

## Review the artifacts

- [Older customized SDD](older-local/skills/psprowls-subagent-driven-development/SKILL.md)
- [Clean text merge with contradictory guidance](text-merge/skills/psprowls-subagent-driven-development/SKILL.md)
- [Resolved SDD candidate](resolved-preview/skills/psprowls-subagent-driven-development/SKILL.md)
- [Initial preference patch](local-preference.patch)
- [Semantic resolution patch](semantic-resolution.patch): changes needed beyond the clean text merge
- [Complete proposed SDD update](proposed-update.patch): older customized SDD to resolved candidate
- [Ledger](ledger.json): intent, hashes, exact resolution changes, and review evidence

The three directories contain complete five-skill selections. Only SDD differs from
the corresponding parent artifacts. Supporting files retain the previously tested
contents and permissions. The SDD-specific patches above do not repeat the other
upstream file changes already shown in the [parent update diff](../diffs/proposed-update.patch).

## Proposed policy reconciliation

| Situation | Intended action |
| --- | --- |
| Plan requires 90-day retention; spec requires 30; no reply yet | Show the contradiction, propose a correction, ask, and hold affected work |
| Plan and spec agree; worker chooses a variable name | Proceed without this approval gate |
| Fix-loop cap reached and a necessary correction involves a plan/spec contradiction | The cap does not bypass approval |
| User approves changing the plan from 90 to 30 days | Record approval, change the plan, and resume affected work |
| No spec is reachable; plan has an internal ambiguity | Record the missing spec and a provisional ruling, subject to other stopping conditions |

Other upstream improvements remain in the candidate, including preflight evidence,
small-task batching, worker/reviewer boundaries, and reporting recorded rulings.
The approval preference does not revert all of upstream's autonomy changes.

## Verification

All fifteen skill schema checks across the three directories passed. Relative Markdown
links, names, file hashes, and absence of text conflict markers were checked. Supporting
files and permissions match the parent artifacts; no helper logic changed here.

One independent agent inspected the resolved candidate and walked through the five
scenarios above. Its answers matched the intended gate. It identified one clarity
issue: the final-review fix dispatch relied on the global gate rather than explicitly
checking it. The candidate now explicitly requires that check before dispatch.

This is a small qualitative probe, not a repeated behavioral evaluation or a live SDD
run. In particular, the raw text merge's contradiction was established by reading its
instructions, not by demonstrating that an agent necessarily chooses the wrong rule.

## What this adds to the tool discussion

A successful merge needs two separate assessments: whether file edits combine and
whether the resulting instructions preserve recorded intent. A customization ledger
can supply the latter review's criteria. The preview should retain both the raw merge
and the proposed semantic resolution so the user can see what judgment was applied.

When a preference applies throughout a workflow, resolution may require several edits
outside the original customization's location. The tool should expose those edits and
their reason rather than reporting only that the preference paragraph survived.

No accepted merge base was advanced. The independent copies remain available for
review. [The one-off reproduction script](../behavior-experiment.py) records this
specific experiment; it is not a general updater.
