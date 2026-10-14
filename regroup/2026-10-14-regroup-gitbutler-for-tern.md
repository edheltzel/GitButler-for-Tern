# Regroup: GitButler for Tern

Date: 2026-10-14
Source: the project's todo.txt and done.txt (+gitbutler-for-tern), compared with the 2026-10-13 check-in.

## Last completed

- Detail pane reserve fixed: narrow panes now fit the tallest detail of any row, verified live in an isolated Tern (2026-10-14).
- Detail pane reserve made linear: narrow renders no longer slow down with many commits (2026-10-14).
- Lanes view shipped and merged (2026-10-13).

## Up next

- Bind the inline diff to a stable change id and path, not the snapshot id: not started.
- Ship the single-panel branch, with the board-view branch below it, through the gate: in the gate; blocked on the inline diff fix.
- Bump the plugin version to 0.1.8, then tag and publish the release: blocked on the single-panel and codegraph ships.

## Remaining

- Ship the codegraph cleanup branch: in the gate.
- Decide whether to keep the new origin URL with a rule exception or revert it: blocked, awaiting a decision.
- Regenerate the Tern type definitions for Tern 0.8.3: not started.
- Rename the old project name in DESIGN.md and the AGENTS.md files: not started.
- Check the workspace block's keys in a real pane: not started.
- Smoke-test one result toast per mutation: blocked on the board-view ship.
- Branches view clips its hint line in a 24-row pane: not started.
- Retake the TUI screenshot: not started.
- Retake README screenshots after the release: blocked on the release.
- Files-pane GitButler tab: blocked on an upstream Tern SDK issue.
- Backlogged: drag-and-drop request to the Tern SDK, slow-load hooks issue, GitButler move data-loss report, inline commit-message box, remote-host status segment, stale block-size budgets, zero-width ZWJ break, off-screen board drop targets, Keyboard settings label request, GitButler version of Tern's git tree view.
