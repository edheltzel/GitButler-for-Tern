# Regroup: GitButler for Tern

Date: 2026-10-14
Source: the project's todo.txt and done.txt (+gitbutler-for-tern), the open pull requests, and the previous check-in. No handoff file exists for this project.

## Last completed

- The single-panel ship merged, carrying the board view and the inline diff work (pull request 23, 2026-10-14). The task list still shows its two ship tasks open.
- Codegraph cleanup and repository hygiene merged (pull request 21, 2026-10-14).
- Inline diff stable-identity binding is re-implemented on the repair branch (not merged yet).

## Up next

- Repair main after the merge: the layout check failed on main and the stable-identity diff work did not survive the merge; the fix branch is in the gate, parked on review questions. Not yet in the task list.
- Bump the plugin version to 0.1.8: blocked on the ship tasks, which the task list still shows open.
- Tag v0.1.8 and publish the release: blocked on the version bump.

## Remaining

- Decide whether to keep the new origin URL with a rule exception or revert it: blocked, awaiting a decision.
- Smoke-test one result toast per mutation: blocked on the board ship task.
- Retake README screenshots after the release: blocked on the release.
- Files-pane GitButler tab: blocked on an upstream Tern SDK issue.
- Stalled (open, unchanged since the previous check-in): regenerate the Tern type definitions for Tern 0.8.3; rename the old project name in DESIGN.md and the AGENTS.md files; check the workspace block's keys in a real pane; branches view clips its hint line in a 24-row pane; retake the TUI screenshot; drag-and-drop request to the Tern SDK; slow-load hooks issue; GitButler move data-loss report; inline commit-message box; remote-host status segment; stale block-size budgets; zero-width ZWJ break; off-screen board drop targets; Keyboard settings label request; GitButler version of Tern's git tree view.
