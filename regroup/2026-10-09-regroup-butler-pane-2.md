# Regroup: Butler Pane (correction)

- **Date:** 2026-10-09
- **Source read:** this repo, GitHub PRs and issues for edheltzel/Butler-Pane, no-mistakes run status, the GitButler workspace.
- **Corrects:** `2026-10-09-regroup-butler-pane.md`, which said no issue tracker exists and left out the two open GitHub issues. That file stays as written (check-ins are append-only).

## Last completed

- PR #18 merged 2026-10-09: graph, stacks and branches workspace views (`v` cycles them). Its five review findings shipped unfixed and are fixed on `ship/follow-ups`.
- PR #17 merged 2026-10-09: README screenshots.
- PR #16 and PR #14 merged 2026-10-08: GitHub CI and the pull/tui/lens hardening.

## Up next

- `ship/follow-ups`: blocked, waiting on its no-mistakes run and then a merge. Carries the pull result notice, grapheme-aware cell width, CI lint fixes, the PR #18 findings and the gate's review-1/review-2 findings.

## Remaining

- Issue #13, GitButler version of Tern's native git tree view: in progress. The graph view from PR #18 covers stacks, commits, uncommitted changes and conflicted commits; assigned changes per stack, ahead/behind in the tree and the plain-git fallback are not done.
- Issue #15, GitButler tab in Tern's Files pane: blocked on stencil-hq/tern-sdk#12 (plugins can't add a Files-pane tab or header button).
- Timer and status hooks disabled after one budget overrun; move root probing off window timers. Not started.
- Stale block size after opening shrinks view budgets. Not started.
- Remote-host status segment, per pane via `cx.hosts:list()`. Not started.
- Tern SDK issue: one overrun disables a hook until reload. Not started.
- Retake README screenshots to show the new views. Not started.
- Real-repo verification by Ed. Not started.
- Palette action timing, names starting with `-`, the 16 MiB output cap, diff render budget. Backlogged.
