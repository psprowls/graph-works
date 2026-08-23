---
name: auto-drive
description: Drive a work item's full pipeline unattended via Orca-supervised workers — a parent's DAG or a lone item's phase sequence — dispatching each ready stage as a fresh Orca worker, handling attend (design) and relay (finish) human gates, and resuming cleanly after a restart. Re-running the same command is the resume path; there is no separate --resume flag. Usage /graph-works:auto-drive <work-path>
---

Invoke the graph-works:auto-drive skill with the given canonical work path and follow it exactly as presented to you.
