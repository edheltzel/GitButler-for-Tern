# Regroup: GitButler for Tern

Date: 2026-10-15
Source: the project's todo.txt and done.txt (+gitbutler-for-tern), compared with the earlier 2026-10-15 check-in, and the release branch diff. No handoff file exists for this project.

## Last completed

- Repair for main merged: stale pull result, diff header reload, short board rows, layout check in CI (pull request 25, 2026-10-15).
- Latest-only regroup check-ins merged (pull request 24, 2026-10-15).
- One start and one result toast per mutation, smoke-tested live (2026-10-15).
- Diff refresh and pull-result callback fixes implemented on the release branch; see [diff lifecycle](../DESIGN.md#surfaces) and [pull verification](../DESIGN.md#json-fields-relied-on).
- Release version bump implemented in [plugin.toml](../plugin/plugin.toml).
- Origin URL decision recorded in the [repository rules](../AGENTS.md#gitbutler-is-mandatory).

## Up next

- Tag v0.1.8 and publish the release: pending the release gate.

## Remaining

- Retake README screenshots after the release: blocked on the release.
- Files-pane GitButler tab: blocked on an upstream Tern SDK issue.
- Stalled (open, unchanged since the previous check-in): regenerate the Tern type definitions for Tern 0.8.3; rename the old project name in DESIGN.md and the AGENTS.md files; check the workspace block's keys in a real pane; branches view clips its hint line in a 24-row pane; retake the TUI screenshot; off-screen board drop targets; Keyboard settings label request; drag-and-drop request to the Tern SDK; slow-load hooks issue; GitButler move data-loss report; inline commit-message box; remote-host status segment; stale block-size budgets; zero-width ZWJ break; GitButler version of Tern's git tree view.
