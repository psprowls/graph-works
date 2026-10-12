# gw-work

Work tracking and archive commands over resolved `gw_kernel.workspace.layout.WorkspaceLayout` values. This package preserves the work mutation plans, read-session borrowing, transaction delegation, locks and commit behavior of the extracted kernel vertical.

The public facade is `gw_work`; archive commands live in `gw_work.archive.commands`. Workspace discovery remains in `gw-kernel`.
