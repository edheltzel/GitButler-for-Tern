# Regroup: GitButler for Tern

- **Date:** 2026-10-10 (second check-in today)
- **Source read:** the project's task list and the previous check-in `2026-10-10-regroup-gitbutler-for-tern.md`.

## Last completed

- No tracked task is done yet.
- Since the last check-in, the changelog task was dropped; the release now depends on the version bump.

## Up next

- Clear the follow-ups review gate: blocked, waiting on Ed. The tracked gate run has since failed at its push step, so the task needs a new run.
- Merge the follow-ups PR and integrate it with `but pull`: blocked on the gate.
- Ship and merge `ship/lanes-view`: blocked on the follow-ups merge.

## Remaining

- Release chain, each blocked on the step before: ship `ship/board-view`, ship the codegraph branch, bump `plugin.toml` to 0.1.8, tag v0.1.8 and publish the GitHub release.
- Decide the `plugin.toml` display name: blocked, waiting on Ed.
- Decide whether to keep the new origin URL or revert it: blocked, waiting on Ed.
- Rename "Butler Pane" in DESIGN.md and the AGENTS.md files: blocked on the name decision.
- Regenerate `tern.d.luau` for Tern 0.7.0: not started.
- Verify j/k, c and ⏎ with real key presses: not started.
- Board inline commit-message box: not started.
- Tern SDK issue: one slow load disables hooks until reload: not started.
- GitButler version of Tern's git tree view (issue #13): not started.
- Remote-host status segment: not started.
- Report the `but` 0.22.3 move data-loss bug: not started.
- Request drag-and-drop events for plugin blocks: not started.
- Retake the README screenshots after the release: blocked on the release.
- Files-pane GitButler tab (issue #15): blocked on stencil-hq/tern-sdk#12.
- Zero-width characters that should break an emoji join: not started.
- Views budget too small when the block size is stale: not started.
