---
name: using-git-worktrees
description: Use when starting feature work that needs isolation from current workspace or before executing implementation plans - ensures an isolated workspace exists via native tools or git worktree fallback
---

# Using Git Worktrees

## Overview

This skill is the Code-Change Gate: the checkpoint every code-writing path runs before any Write/Edit. It has two parts, in order — Part 1 confirms writing code is *authorized*, Part 2 (Steps 0–4 below) ensures the change happens in an isolated workspace. Prefer your platform's native worktree tools for the isolation half. Fall back to manual git worktrees only when no native tool is available.

**Core principle:** No code without a direct implement directive. Once authorized, isolation is mandatory, not optional — detect existing isolation first, then native tools, then fall back to git. Never fight the harness.

**Announce at start:** "I'm using the using-git-worktrees skill as the Code-Change Gate."

## Part 1: Authorization

Before anything else — before Step 0 below, before creating any worktree, before any Write or Edit, confirm you're authorized to write code.

**Confirm a direct implement directive.** Examples that satisfy it:
- "implement this", "make the change", "write the code", "fix it in code"
- "execute the plan", "start building", "go ahead and implement it"
- selecting an execution option from a `writing-plans` Execution Handoff

What does **NOT** satisfy it:
- Approving or praising a design, spec, or plan ("looks good", "ship it", "I like this")
- Asking a question, or asking you to investigate, analyze, or explain
- Silence, or ambiguous enthusiasm

**No direct implement directive → STOP.** Do not create a worktree. Do not Write or Edit code. Stay read-only, and ask the user whether they want you to implement.

**Authorized → continue to Part 2, below.**

## Part 2: Isolation

Once Part 1 authorizes writing code, isolation is required — not a consent question. Steps 0 through 4 below carry it out: detect existing isolation, create an isolated workspace if none exists, set up the project, verify a clean baseline, and — when the work is done — exit the worktree.

## Step 0: Detect Existing Isolation

**Before creating anything, check if you are already in an isolated workspace.**

```bash
GIT_DIR=$(cd "$(git rev-parse --git-dir)" 2>/dev/null && pwd -P)
GIT_COMMON=$(cd "$(git rev-parse --git-common-dir)" 2>/dev/null && pwd -P)
BRANCH=$(git branch --show-current)
```

**Submodule guard:** `GIT_DIR != GIT_COMMON` is also true inside git submodules. Before concluding "already in a worktree," verify you are not in a submodule:

```bash
# If this returns a path, you're in a submodule, not a worktree — treat as normal repo
git rev-parse --show-superproject-working-tree 2>/dev/null
```

**If `GIT_DIR != GIT_COMMON` (and not a submodule):** You are already in a linked worktree. Skip to Step 2 (Project Setup). Do NOT create another worktree.

Report with branch state:
- On a branch: "Already in isolated workspace at `<path>` on branch `<name>`."
- Detached HEAD: "Already in isolated workspace at `<path>` (detached HEAD, externally managed). Branch creation needed at finish time."

**If `GIT_DIR == GIT_COMMON` (or in a submodule):** You are in the main checkout. **Creating an isolated worktree is required** — proceed to Step 1. Do not ask for consent.

**The only exception:** the user has explicitly told you to work in the main checkout (e.g. "just work on the current branch", "don't make a worktree", "edit in place"). In that case work in place, say so — "Working in the main checkout at your request on branch `<name>`." — and skip to Step 2.

## Step 1: Create Isolated Workspace

**You have two mechanisms. Try them in this order.**

### 1a. Native Worktree Tools (preferred)

Step 0 determined an isolated workspace is required. Do you already have a way to create a worktree? It might be a tool with a name like `EnterWorktree`, `WorktreeCreate`, a `/worktree` command, or a `--worktree` flag. If you do, use it and skip to Step 2.

Native tools handle directory placement, branch creation, and cleanup automatically. Using `git worktree add` when you have a native tool creates phantom state your harness can't see or manage.

Only proceed to Step 1b if you have no native worktree tool available.

### 1b. Git Worktree Fallback

**Only use this if Step 1a does not apply** — you have no native worktree tool available. Create a worktree manually using git.

#### Directory Selection

Follow this priority order. Explicit user preference always beats observed filesystem state.

1. **Check your instructions for a declared worktree directory preference.** If the user has already specified one, use it without asking.

2. **Check for an existing project-local worktree directory:**
   ```bash
   ls -d .worktrees 2>/dev/null     # Preferred (hidden)
   ls -d worktrees 2>/dev/null      # Alternative
   ```
   If found, use it. If both exist, `.worktrees` wins.

3. **Check for a resolved workspace directory.** Run the shared resolver:
   ```bash
   WORKSPACE=$(bash "${CLAUDE_PLUGIN_ROOT}/skills/shared/resolve-workspace.sh" 2>/dev/null)
   # When a workspace resolves, worktrees live under "$WORKSPACE/worktrees/".
   ```
   If it returns a non-empty path, use `$WORKSPACE/worktrees/<branch>`. If it returns empty, fall through to the next option below.

4. **If there is no other guidance available**, default to `.worktrees/` at the project root.

#### Safety Verification (project-local directories only)

**MUST verify directory is ignored before creating worktree:**

```bash
git check-ignore -q .worktrees 2>/dev/null || git check-ignore -q worktrees 2>/dev/null
```

**If NOT ignored:** Add to .gitignore, commit the change, then proceed.

**Why critical:** Prevents accidentally committing worktree contents to repository.

#### Create the Worktree

```bash
# Determine path based on chosen location
path="$LOCATION/$BRANCH_NAME"

git worktree add "$path" -b "$BRANCH_NAME"
cd "$path"
```

**Sandbox fallback:** If `git worktree add` fails with a permission error (sandbox denial), tell the user the sandbox blocked worktree creation and you're working in the current directory instead. Then run setup and baseline tests in place.

## Step 2: Project Setup

Auto-detect and run appropriate setup:

```bash
# Node.js
if [ -f package.json ]; then npm install; fi

# Rust
if [ -f Cargo.toml ]; then cargo build; fi

# Python
if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
if [ -f pyproject.toml ]; then poetry install; fi

# Go
if [ -f go.mod ]; then go mod download; fi
```

## Step 3: Verify Clean Baseline

Run tests to ensure workspace starts clean:

```bash
# Use project-appropriate command
npm test / cargo test / pytest / go test ./...
```

**If tests fail:** Report failures, ask whether to proceed or investigate.

**If tests pass:** Report ready.

### Report

```
Worktree ready at <full-path>
Tests passing (<N> tests, 0 failures)
Ready to implement <feature-name>
```

## Quick Reference

| Situation | Action |
|-----------|--------|
| No direct implement directive | STOP — stay read-only, ask before any code (Part 1) |
| Authorized, in the main checkout | Isolation is required — create a worktree, no consent prompt (Part 2 / Step 0) |
| User explicitly asked to work in the main checkout | Work in place, say so (Step 0 exception) |
| Already in linked worktree | Skip creation (Step 0) |
| In a submodule | Treat as normal repo (Step 0 guard) |
| Native worktree tool available | Use it (Step 1a) |
| No native tool | Git worktree fallback (Step 1b) |
| `.worktrees/` exists | Use it (verify ignored) |
| `worktrees/` exists | Use it (verify ignored) |
| Both exist | Use `.worktrees/` |
| Neither exists, a workspace resolves | Use `$WORKSPACE/worktrees/` |
| Neither exists, no workspace | Check instruction file, then default `.worktrees/` |
| Directory not ignored | Add to .gitignore + commit |
| Permission error on create | Sandbox fallback, work in place |
| Tests fail during baseline | Report failures + ask |
| No package.json/Cargo.toml | Skip dependency install |

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "The user obviously wants this implemented" | A direct implement directive is the authorization. Absent one, stay read-only and ask (Part 1). |
| "They approved the plan, that's enough to start coding" | Approving a design or plan is not a direct implement directive. Confirm explicitly before any Write/Edit. |
| "I'll just ask if they want a worktree, to be polite" | Isolation is mandatory once authorized, not a consent question. Create it and say so — don't ask. |
| "I'm obviously not in a worktree — no need to check" | Run Step 0. Harness-created isolation and submodules both fool eyeballing; the detection commands settle it. |
| "`git worktree add` is quicker than hunting for a native tool" | A native tool (e.g. `EnterWorktree`) owns placement, branching, and cleanup. Bypassing it is the #1 mistake — it creates phantom state your harness can't see or manage. |
| "The worktree directory is surely ignored already" | Run `git check-ignore`. An unignored worktree directory commits the whole tree into the repo. |
| "Any directory name works" | Explicit instructions beat an existing project-local directory, which beats a resolved workspace, which beats the `.worktrees/` default. |
| "The workspace is fresh — baseline tests can wait" | A dirty baseline makes every later failure ambiguous. Run the tests now; proceeding past failures is your human partner's call. |
