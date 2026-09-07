# Platform

| Channel | Supported |
|---|---|
| macOS, Linux | yes |
| Windows via WSL | yes (WSL is Linux) |
| Native Windows (`python.exe`, Git Bash, PowerShell) | committed, not yet |

This plugin's pipeline verbs (`gw work next`, `gw work advance`, auto-drive) are what the
platform question is actually about. For the full matrix and the per-capability answer
(durability tier, dispatch backend, file lock, process control), see the repository root
[`README.md`](../../README.md)'s `## Platform` section and run `gw util platform`.

This file exists because `README.md` in this directory is upstream's, vendored verbatim
and not reviewed by this fork (see `plugins/PATCHES.md`); this pointer is an ours-side
addition instead.
