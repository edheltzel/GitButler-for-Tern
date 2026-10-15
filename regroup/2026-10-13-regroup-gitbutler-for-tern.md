# Regroup: GitButler for Tern

Date: 2026-10-13
Source: the project's todo.txt tasks (+gitbutler-for-tern) and the workspace state.

## Last completed

- Follow-ups PR #19 merged into main and pulled into the workspace (2026-10-12).
- The parked follow-ups gate was cleared (2026-10-12).
- Plugin display name decided: GitButler (2026-10-12).

## Up next

- Ship the lanes view: in the gate; the test step is parked waiting on Ed in the gate TUI (remove the generated codegraph database and scratch folders, revert the branches file pane to follow the selection).
- Ship the board view: blocked on the lanes view.
- Bump the plugin version to 0.1.8, then tag v0.1.8 and publish the release: blocked on the board view and codegraph ships.

## Remaining

- Ship the codegraph branch: blocked on the board view.
- Decide whether to keep the new origin URL with a rule exception or revert it: blocked, waiting on Ed.
- Rename the old project name in DESIGN.md and the AGENTS.md files: not started.
- Regenerate the Tern type definitions for Tern 0.7.0: not started.
- Check the workspace block's keys in a real pane: not started.
- Ask the Tern SDK for drag-and-drop events in plugin blocks: backlogged.
- Report that one slow load disables a plugin's hooks until reload: backlogged.
- Report the GitButler move data-loss bug: backlogged.
- Inline commit-message box on the board: backlogged.
- Remote-host status segment: backlogged.
- Fix view budgets when the block size is stale: backlogged.
- Width: zero-width characters that are not Extend should break a ZWJ join: backlogged.
- Retake README screenshots after the release: blocked on the release.
- Files-pane GitButler tab: blocked, waiting on an upstream Tern SDK issue.
- GitButler version of Tern's git tree view: backlogged.
