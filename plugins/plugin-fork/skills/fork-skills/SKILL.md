---
name: fork-skills
description: Use when the user explicitly requests forking upstream agent skills, adopting an existing customized skill directory, or installing a managed variant for Codex, Claude Code, or Pi.
---

# Fork, adopt, and install skills

Use the standalone `plugin-fork` CLI. Each prepared operation has its own preview
and approval; adoption records ownership, while installation is a separate operation.
Read the relevant command's `--help` for available options.

## Prepare the decision

1. Inspect the available source with JSON:

```bash
plugin-fork inspect "$SOURCE" --json
```

For adoption whose original source is lost, inspect the existing content directory.
Explain its dependency evidence, missing requirements, and discovery scan limits.

2. Fill these decision fields with the user's choices before preparing a mutation:

| Required field | Record |
| --- | --- |
| Selection | Source-relative skill directories, explicit shared resources, required dependencies |
| Names | Local standalone names; resolve reported collisions with the user |
| Intent | Custom behavior that later updates must preserve |
| Locations | Maintained content root and state/history root; project or personal scope |
| Sharing | Shared single owner or independent copies; requested agents and destinations |
| Requirements | Supplied requirements, or a concrete user-approved adaptation for each missing requirement |

Preserve choices already supplied. Gather missing choices rather than inventing a
copy policy or removing required behavior. Unrelated existing destination content
belongs to its owner; offer adoption or another destination.

3. Prepare the applicable ownership operation:

```bash
plugin-fork fork "$SOURCE" --selection selection.json --intent intent.json --content-dir "$CONTENT" --state-dir "$STATE" --json
plugin-fork adopt --selection selection.json --intent intent.json --content-dir "$CONTENT" --state-dir "$STATE" --json
```

Use only the applicable command. An adoption without verified original-base evidence
has **uncertain origin and no original base**. Three-way upstream updates remain
unavailable. Merely assigning a new source does not establish the lost original;
known adoption/reconciliation needs original-base evidence through `--base`, `--evidence`,
and, for an existing uncertain variant, `--variant`.

4. Present the returned preview: operation, variant/preview IDs, exact changes,
content/state locations, owners, findings and remaining scan limits. Obtain approval
for that exact allowed preview, then apply it using its command:

```bash
plugin-fork fork --apply "$PREVIEW" --state-dir "$STATE" --json
plugin-fork adopt --apply "$PREVIEW" --state-dir "$STATE" --json
```

5. For requested installation, prepare a **separate** preview using the selected
sharing mode, agents, scope and destinations:

```bash
plugin-fork install "$VARIANT" --agent codex --agent claude --scope project --mode shared --project "$PROJECT" --state-dir "$STATE" --json
```

Explain the returned destination-to-owner mapping: shared bindings retain one ID;
distinct copy roots receive independent IDs; identical roots share one owner.
Resolve missing requirements through the chosen supply/adaptation path before
installation. Present this installation preview and obtain its own approval.

```bash
plugin-fork install --apply "$INSTALL_PREVIEW" --state-dir "$STATE" --json
plugin-fork status "$VARIANT" --state-dir "$STATE" --json
```

Report status for each created copy ID too. After a refusal or incomplete journal,
report its evidence and next explicit recovery step. The CLI owns ledger/base writes;
Git commits are ordinary local content history. This companion neither installs
itself nor edits another variant as an implied follow-up.
