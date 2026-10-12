# Regroup: Butler Pane

- **Date:** 2026-10-09
- **Source read:** this repo (README, DESIGN.md, AGENTS.md), GitHub PRs for edheltzel/Butler-Pane, the no-mistakes gate status, and the GitButler workspace. No board or issue tracker exists.

## Last completed

- PR #16 merged 2026-10-08: GitHub CI (Luau parser and syntax checks) plus `.no-mistakes.yaml`.
- PR #14 merged 2026-10-08: pull guard, apply list `--no-check`, wrapped-chrome list cap, `-v` status lens, but tui focus and reuse, Butler Pane rename.
- PR #12 merged 2026-10-08: pull (`f`), "Set up this folder", but tui and but gui commands, tutorial README, DOX docs.

## Up next

- `ship/workspace-views` (graph, stacks and branches views, `v` to switch): blocked, waiting for a decision on 5 review findings at the no-mistakes gate.
- `ship/readme-screenshots` (README screenshots): blocked, waiting for test-gate approval at the no-mistakes gate.
- Merge both PRs once each reaches `checks-passed`, then `but pull` (user asked to land them).

## Remaining

- Follow-up fixes: pull conflict notice built from the pre-pull check; wide characters below U+2E80; CI lint word-splitting, `SyntaxError` substring, concurrency on `main`. Not started.
- Timer and status hooks disabled after one budget overrun in Tern's log; move root probing off window timers. Not started.
- Stale block size after opening (`cx.cols`/`cx.rows` report 80x24) shrinks view budgets. Not started.
- Remote-host status segment, per pane via `cx.hosts:list()`. Not started.
- Tern SDK issue to file: one overrun disables a hook until reload. Not started.
- Retake README screenshots to show the new views. Not started.
- Real-repo verification by Ed (all testing so far ran in scratch repos). Not started.
- Earlier backlog: palette action timing, names starting with `-`, the 16 MiB output cap, diff render budget. Backlogged.
