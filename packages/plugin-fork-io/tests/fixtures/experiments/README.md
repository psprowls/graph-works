# Archived experiment fixtures

Upstream: obra/superpowers, MIT copyright Jesse Vincent. LICENSE files are preserved.

Archive SHA-256: `8da6eb76802116c9bf0904fe0bebc3b27b9fcbcc8218b9d16c8e90e1fc78131b`. Original revisions:

- `v6.2.0`: `3dcbd5c4b48e02263fbf4a3c01e3fe4f81d584d9`
- `v6.3.0`: `b36e0829c6d0140e93cfef2ca599b1b07d4a7797`

Original source selections remain distinct from customized local selections. The incoming SDD definition is read from the nested archive and verified identical to the outer source selection; nested archive hash is pinned in manifest.json. No archived helper was executed. Helpers remain byte evidence; original permission modes are recorded in the manifest and applied only inside temporary test trees.

The fixtures retain the SDD skill and reviewer resource required for the minimal update selection, plus the three behavioral SDD definitions and recorded review/scenarios. Other experiment scripts, redundant merged support trees, and disposable clones are omitted. Recorded approval scenarios are static evidence, not a deterministic claim of model compliance.

Reviewed SDD full SHA-256: `fd62fd73a88814227eb26bc1939662646f2e0b75700644097a46db426c1ec15c`.

Each row below pins exact archive member and file hash; local paths and original modes are machine-readable in manifest.json.

The exercised selection is SDD plus the mapped reviewer resource and LICENSE. Other named workflow skills are explicit supplied test context. This does not reproduce the entire archived plugin: the old Codex reference was rewritten upstream and its old adaptation no longer has a unique anchor, so it is excluded instead of weakening replay.

`naming-adaptations.json` is an authored, explicit recipe derived from original 6.2 and the captured naming-only local SDD selection. Each changed byte span expands only until its original expected bytes occur exactly once in both pinned originals; overlapping spans combine, and replay must reproduce the complete archived local file bytes. These are reviewed fixture inputs, not a new global replacement algorithm. Original 6.3 additions remain outside the spans. The 40 records carry exact expected/replacement bytes and source positions.

| Archive member | SHA-256 |
| --- | --- |
| `psprowls-superpowers-update-experiment/behavior-experiment/README.md` | `9afa2733605b3a868f8db5b25d36885322502a4a0119fb031f743504d294e21f` |
| `psprowls-superpowers-update-experiment/behavior-experiment/ledger.json` | `c9220db2b2da75f8be993c3f6f6e618657d67cccecedfec42408dd27cebe2b00` |
| `psprowls-superpowers-update-experiment/behavior-experiment/older-local/skills/psprowls-subagent-driven-development/SKILL.md` | `d217998deb488a2bba9bb773c28cf98b0f3ac7d20c0598efbe47c5bbb6640019` |
| `psprowls-superpowers-update-experiment/behavior-experiment/resolved-preview/skills/psprowls-subagent-driven-development/SKILL.md` | `fd62fd73a88814227eb26bc1939662646f2e0b75700644097a46db426c1ec15c` |
| `psprowls-superpowers-update-experiment/behavior-experiment/text-merge/skills/psprowls-subagent-driven-development/SKILL.md` | `ba7fa2445fcd7e04c6ceaf2cf483009b7f9a9b9f5ee7cdebdd86c340ac539709` |
| `psprowls-superpowers-update-experiment/fork-v6.2.0/LICENSE` | `a37e0e9697144819e1d965176ac4ae5bc3fa02d11e7812036bbcadf6dafe2400` |
| `psprowls-superpowers-update-experiment/fork-v6.2.0/skills/psprowls-requesting-code-review/code-reviewer.md` | `b2f2ec7596925fe52dac158fdfbca19b3a7d779d619c481e6706a6c0001662d3` |
| `psprowls-superpowers-update-experiment/fork-v6.2.0/skills/psprowls-subagent-driven-development/SKILL.md` | `b0628bffa8d1595cd24419e4c031bd7f344fb7b8360d014cb77e241b8df7f59b` |
| `psprowls-superpowers-update-experiment/fork-v6.2.0/skills/psprowls-subagent-driven-development/implementer-prompt.md` | `946601616a6f76ab3f165ef98377390968f1ec124ecef87422bc3553404e0332` |
| `psprowls-superpowers-update-experiment/fork-v6.2.0/skills/psprowls-subagent-driven-development/re-review-prompt.md` | `e1d8e0e65e58ccde6dc920843da9148d23e65aeb0f8932bcc30be1341a703c4c` |
| `psprowls-superpowers-update-experiment/fork-v6.2.0/skills/psprowls-subagent-driven-development/scripts/review-package` | `49081bf5e5303dbe183aa70175cd4ab1fc7c34276abdfafafc09a03af36f0f46` |
| `psprowls-superpowers-update-experiment/fork-v6.2.0/skills/psprowls-subagent-driven-development/scripts/sdd-workspace` | `55f981bbae7c30ba6be2571f68e69bbc5f635a33089a19cd0f056caeed3be156` |
| `psprowls-superpowers-update-experiment/fork-v6.2.0/skills/psprowls-subagent-driven-development/scripts/task-brief` | `478f89d5468d5fb42db817ef06ba3e7d4f14b86f7a48fcf98cca8b1618031781` |
| `psprowls-superpowers-update-experiment/fork-v6.2.0/skills/psprowls-subagent-driven-development/task-reviewer-prompt.md` | `e3b4a1bfe7cd55ed0459e5ca9cdddb6ff086c345b99e9097036db6a18545729e` |
| `psprowls-superpowers-update-experiment/sources/v6.2.0/.claude-plugin/plugin.json` | `5e9f9b99d9d009ccbcdbce82644a5bf902691c39f152b79540b4542533fe53c3` |
| `psprowls-superpowers-update-experiment/sources/v6.2.0/LICENSE` | `a37e0e9697144819e1d965176ac4ae5bc3fa02d11e7812036bbcadf6dafe2400` |
| `psprowls-superpowers-update-experiment/sources/v6.2.0/skills/requesting-code-review/code-reviewer.md` | `b2f2ec7596925fe52dac158fdfbca19b3a7d779d619c481e6706a6c0001662d3` |
| `psprowls-superpowers-update-experiment/sources/v6.2.0/skills/subagent-driven-development/SKILL.md` | `349a08ad8b59b19b86c13a7d2f34a1a38719bf88257004a863eefefa8d9f9e40` |
| `psprowls-superpowers-update-experiment/sources/v6.2.0/skills/subagent-driven-development/implementer-prompt.md` | `946601616a6f76ab3f165ef98377390968f1ec124ecef87422bc3553404e0332` |
| `psprowls-superpowers-update-experiment/sources/v6.2.0/skills/subagent-driven-development/re-review-prompt.md` | `e1d8e0e65e58ccde6dc920843da9148d23e65aeb0f8932bcc30be1341a703c4c` |
| `psprowls-superpowers-update-experiment/sources/v6.2.0/skills/subagent-driven-development/scripts/review-package` | `fac3d4bd7f94369e8037b9ead2a8a502dca6ab333902b560b9455dbb3c450ebe` |
| `psprowls-superpowers-update-experiment/sources/v6.2.0/skills/subagent-driven-development/scripts/sdd-workspace` | `95a09d9d3983ad1aafd093ca72b4587946dea885c6e302caa02a779a2f911c31` |
| `psprowls-superpowers-update-experiment/sources/v6.2.0/skills/subagent-driven-development/scripts/task-brief` | `d6954ef7841c7da3d77373e6ff5118b3f2f2e998606fd95d33e6527851bce044` |
| `psprowls-superpowers-update-experiment/sources/v6.2.0/skills/subagent-driven-development/task-reviewer-prompt.md` | `e3b4a1bfe7cd55ed0459e5ca9cdddb6ff086c345b99e9097036db6a18545729e` |
| `psprowls-superpowers-update-experiment/sources/v6.3.0/.claude-plugin/plugin.json` | `d0d56f9f3a6adbf341d5d49850f63def17bfb7e9a39704b517745b2c554498c3` |
| `psprowls-superpowers-update-experiment/sources/v6.3.0/LICENSE` | `a37e0e9697144819e1d965176ac4ae5bc3fa02d11e7812036bbcadf6dafe2400` |
| `psprowls-superpowers-update-experiment/sources/v6.3.0/skills/requesting-code-review/code-reviewer.md` | `5eca5fcfd48a50e0a526ce5ffd64bf625d6b81bb46d11795274dae451fe6ffd4` |
| `psprowls-superpowers/provenance/upstream-6.3.0.tar.gz!skills/subagent-driven-development/SKILL.md` | `8dd1b8e698edec3700c6d89517dbe96febd3bacd3f6ea21c1a3569c62ea104b5` |
| `psprowls-superpowers-update-experiment/sources/v6.3.0/skills/subagent-driven-development/implementer-prompt.md` | `81dd9d5a3fb0b09b96b5829f03b9796842f3199ef7591d1ba1c2db2a515f64d0` |
| `psprowls-superpowers-update-experiment/sources/v6.3.0/skills/subagent-driven-development/re-review-prompt.md` | `db0d5849478bc79cbde97b9b2cf0e58b50be8b8ed18464b0252c2bf27b6440a6` |
| `psprowls-superpowers-update-experiment/sources/v6.3.0/skills/subagent-driven-development/scripts/review-package` | `fac3d4bd7f94369e8037b9ead2a8a502dca6ab333902b560b9455dbb3c450ebe` |
| `psprowls-superpowers-update-experiment/sources/v6.3.0/skills/subagent-driven-development/scripts/sdd-workspace` | `95a09d9d3983ad1aafd093ca72b4587946dea885c6e302caa02a779a2f911c31` |
| `psprowls-superpowers-update-experiment/sources/v6.3.0/skills/subagent-driven-development/scripts/task-brief` | `d6954ef7841c7da3d77373e6ff5118b3f2f2e998606fd95d33e6527851bce044` |
| `psprowls-superpowers-update-experiment/sources/v6.3.0/skills/subagent-driven-development/task-reviewer-prompt.md` | `eea23e33ec570c3041f40e9569fa711d61b8029f9eecf908345138aa1c6e61ab` |

Fixture attributes disable text normalization. The archived behavior README alone retains its original Markdown hard-break whitespace, with a file-specific whitespace rule.
