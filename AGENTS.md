# AGENTS.md

Product introduction, source repository and user guide: `README.md`. Design and findings: `DESIGN.md`. The plugin id stays `gitbutler`, so command ids remain `plugin.gitbutler.*`; its display name in Tern (`plugin.toml` `name`, the palette group and `tern plugin list`) is `GitButler`.

## DOX

These `AGENTS.md` files are binding work contracts for their subtrees.

- **Before editing:** read this file, then every `AGENTS.md` on the path to each file you will touch (see the Child DOX Index). The closer doc controls local detail; no child may weaken this one. Re-read the chain in the current session; don't rely on memory.
- **After editing:** update the nearest owning `AGENTS.md` when a change affects purpose, structure, contracts, workflows, side effects or user preferences, plus any parent whose index or structure changed. Remove stale text. Small edits that change no contract may leave docs unchanged, but say so.
- **Style:** concise, current, operational. Stable contracts, not history. Broad rules here, concrete detail in children.

## GitButler is mandatory

This repo is a GitButler workspace (`gitbutler/workspace`, target `origin/main`, remote `origin`, public, default branch `main`), and the plugin requires `but` at run time. Work lands on `main` through a PR (`but pr new <branch>`).

- **VCS in this repo:** use `but` for every repository write except remote management (see below): `but commit -b <branch> -m "<msg>" <ids>`, `but push <branch>`, `but pr new <branch>`. Never run `git add`, `commit`, `checkout`, `switch`, `merge`, `rebase`, `reset`, `stash`, `cherry-pick` or `push`. Read-only git (`git log`, `git show`, `git diff`, `git blame`) is fine. Follow `skill://but`.
- **Git remotes:** `but` does not manage git remotes, so manage them with plain `git remote`. This is the one exception to `but`-only writes. Keep `origin` at `https://github.com/edheltzel/gitbutler-for-tern.git` (lowercase; GitHub redirects to GitButler-for-Tern).
- **Plugin runtime:** `but` (tested with 0.22.3) must be installed on the machine that runs the panes. `gb.luau` looks for it in `PATH`, `~/.local/bin`, `/opt/homebrew/bin` and `/usr/local/bin`.
- If HEAD is ever not `gitbutler/workspace`, stop and ask. Do not work around it with plain git or `but teardown`.
- Never add an agent as a commit co-author.
- **Shipping to `main` (user rule):** every PR goes through the no-mistakes gate, and no-mistakes must follow GitButler (no checkout, no worktree workaround). Only for that ship: `but config push-remote no-mistakes`, `but push <top branch>`, keep the top branch's run and abort the lower ones (`no-mistakes axi abort --run <id>`), then `but config push-remote origin`. One PR per ship.
- **Gate limits on a GitButler workspace:** a push can't carry `--intent`, so the gate infers it from transcripts. `no-mistakes axi respond` has no `--run` and resolves only the current branch, so it cannot answer a gate while HEAD is `gitbutler/workspace`. `no-mistakes axi status --run <id>` and `logs` can inspect that run. `axi respond` only works from a checkout of the run's branch; otherwise hand off with `no-mistakes attach --run <id>`.
- **Drive gates autonomously (user rule):** use `no-mistakes axi` (`status`, `respond`, `logs`). Answer `auto-fix` gates. Decide `ask-user` findings with judgment and this file's rules. Hand off only when `axi respond` cannot run from this HEAD, or a finding is a product decision these rules do not settle.
- **Gate runs unattended:** `.no-mistakes.yaml` sets `auto_fix.review: 3`, so review findings marked `auto-fix` are fixed and committed by the gate (the global default `0` parks them). A run still parks for `ask-user` findings, a spent fix budget, a failing test, or a protected-path refusal. Answer those with `axi respond` when this HEAD can; `no-mistakes attach --run <id>` is the handoff when it cannot.
- **Gate refs need new names to re-push:** `but push` to the gate fails with `Failed to derive forge information` when it must force-push. Rename the branches (`but reword <branch> -m <new-name>`) so they push as new refs.

## Commands

- Link into Tern (reloads on every save): `tern plugin link plugin` (link `plugin/`, never the repo root)
- Status and load errors: `tern plugin list` (host half); window-half errors appear in `~/Library/Logs/Tern/tern.log`
- Force reload: `tern plugin reload`
- Regenerate types after a Tern upgrade: `tern plugin types plugin` (writes `plugin/tern.d.luau`; don't hand-edit it)
- Parser and width check: `cd plugin && luau check.luau` (must print `parse: ok`)
- Syntax check: run the `commands.lint` script in [.no-mistakes.yaml](.no-mistakes.yaml) from the repo root; it allows expected type noise but rejects syntax errors and analyzer failures.
- CI: `.no-mistakes.yaml` owns the check commands; `.github/workflows/ci.yml` mirrors them on Luau 0.741 (regenerate with `no-mistakes ci-workflow -f`, then restore the Install Luau step, the lint script and the concurrency block). A newer push cancels a PR's run; each `main` run has its own concurrency group (`github.run_id`), so none is cancelled or replaced while pending. The gate reads `commands.*` only from `main`, and GitHub Actions runs `run:` steps with `bash -e`, so test a changed script under both `sh -c` and `bash -e -c`.
- Logs: `~/Library/Logs/Tern/tern-daemon.log` (host half, `plugin handler failed`), `~/Library/Logs/Tern/tern.log` (window half)

## Testing

Run tests in the scratch repo and an isolated Tern, never in real repos.

- Sandbox repo (recreate if `/tmp` was cleared):
  `mkdir -p /tmp/gbs && cd /tmp/gbs && git init -q --bare remote.git && git init -q -b main work && cd work && echo hi > a.txt && git add . && git commit -qm initial && git remote add origin /tmp/gbs/remote.git && git push -q -u origin main && but setup`
  (Plain git is allowed here only to seed the scratch repo before `but setup`.)
- Isolated Tern: `printf '{"shell":"/opt/homebrew/bin/fish","status_bar":true}\n' > /tmp/ttc/settings.json`, `TERN_CONFIG_DIR=/tmp/ttc tern plugin link plugin`, then from `/tmp/ttc` run `TERN_CONFIG_DIR=/tmp/ttc TERN_DAEMON_SOCKET=/tmp/ttc/daemon.sock tern --control /tmp/ttc/ctl.sock /tmp/gbs/work` (long-running).
- **Never override `HOME`** (no fake home dirs). Tern's sign-in lives under the real home, so a fresh home opens Tern's closed-beta sign-in screen and loads no plugins. Isolate with `TERN_CONFIG_DIR`, `TERN_DAEMON_SOCKET` and `--control` only, as above. This applies to no-mistakes test agents too.
- Drive it with `TERN_CONFIG_DIR=/tmp/ttc TERN_DAEMON_SOCKET=/tmp/ttc/daemon.sock`:
  - `tern ls`
  - `tern send <pane> keys enter` / `tern send <pane> text c`
  - `timeout 8 tern ctl --control /tmp/ttc/ctl.sock plugins run plugin.gitbutler.open`
  - `timeout 8 tern ctl --control /tmp/ttc/ctl.sock shot NAME` (writes `/tmp/ttc/target/shots/tern/live/NAME.png`; the file can land a few seconds after the reply, so wait before reading it)
  - `tern ctl … key cmd+t` opens a tab with Tern's configured shell (fish), which lenses need
- Verify each mutation with `but status --json` in `/tmp/gbs/work`.
- Lenses only appear in shells spawned after the plugin loaded (they need the spawn env) and with Tern's shell integration. `tern new tab` from the CLI starts the login shell (bash here), which shows plain output; test lenses in a tab Tern opens with its configured shell (fish).
- The status bar is off unless `"status_bar": true` is in `settings.json`, and the setting takes effect only after Tern restarts.
- fish may pre-fill the prompt (`$EDITOR`): send `ctrl+u` before `tern run <pane> <command>`.
- ⏎ shows the diff inside the workspace block; Esc returns to the view, so keys sent to the workspace pane act on whichever is showing.
- Pane sizes in the isolated window (Tern 0.8.3): `tern ls` sizes only update when the test daemon restarts, so set the window size first, then restart the daemon (`pkill -f 'daemon --socket /tmp/ttc/daemon.sock'`; the window starts a new one) and open the workspace block right away. Measured recipe for a tall, narrow block: `tern ctl … size 800 1400`, restart the daemon, open the block (it splits at about 47×81), then `size 1600 1400` gives a 99×81 block. `size 1280 520` without a restart leaves the block at 78×26. To test layout limits, measure with `tern ctl … dump <selector>` (rects are [x, y, w, h]) against `.sf-main`; in this window text lines are 16px, so cells × 17 overstates the pane. Floated panes (`but tui`) don't appear in `shot`.

## Gotchas

- Saving any file under `plugin/` reloads every plugin in the user's real Tern as well as the test window. A reload that fails mid-edit kills open blocks; reopen them.
- Tern watches the linked package recursively, and `.git` writes count as changes (measured: each one reloads every plugin). That is why the package lives in `plugin/` with `.git` at the repo root. Never move plugin files to the root or link the root, or every `but` call in this repo will reload Tern's plugins.
- `tern ctl wait` and `tern ctl type` can hang: wrap `ctl` calls in `timeout`, and use `tern send` for keys.
- A plugin reload does not emit `window_start`/`focus`. Window state is re-bootstrapped with `tern.timer(0, …)`.
- `but` 0.22.3 cannot assign an uncommitted file to a branch without committing it (`rub` is retired). See `DESIGN.md`.
- `but` 0.22.3 `move` can lose work: when it fails with "Uncommitted files would be overwritten by checkout" it still drops the commit and the dependent uncommitted change, with no oplog entry. Recover with `but resolve <path>` then `but oplog restore <sha>`. See `DESIGN.md` finding 6.
- One slow plugin load (over 50 ms, e.g. two Terns reloading on the same save) makes Tern disable the plugin's window half until the next reload: palette commands, global keys and the status segment stop working. `tern plugin reload` brings them back.
- CLI ids (`nk`, `ymn`, `zn:n`) are snapshot-local. Don't hold them across a refresh for a later mutation.
- `but tui` and `but gui` are real commands; the plugin opens `but tui` in a floated pane and `but gui` as the desktop app. Tern picks a float's size; the plugin API can't set it.
- `but` 0.22.3 has no `but init`. `but setup --init` creates the git repo (empty commit) when there is none, then sets GitButler up; the plugin's "Set up this folder" command runs it.

## Boundaries

- **Never:** raw git writes other than managing remotes with `git remote` (see GitButler is mandatory), mutating a real repo while testing, editing `tern.d.luau` by hand, or editing `CHANGELOG.md` or other generated files.
- **Ask first:** new `[[lenses]]` match patterns (a claim also blocks Tern's built-in lens), changes to the spawn filter (it affects every shell Tern starts), new default keybinds, and adding a remote or changing the `origin/main` target.

## User Preferences

- Shipping goes through no-mistakes on top of GitButler, one PR per ship (see GitButler section).
- GitButler's push remote is `origin` except while shipping to `main`.
- `but tui` opens as picture-in-picture and `q` closes it; keep that.
- Keep every `GitButler: …` palette command: the plugin serves many users, not one workflow.
- Preferred default chords: only open (⌥⇧⌘G) and the TUI (⌥⇧⌘T), with every other action unbound. Currently shipped chords are in [README.md](README.md#keys).
- Preferred layout: one workspace panel with views switching in place (tabs, keys), diffs inside it, and sync state using GitButler's own words in colored chips. Current surfaces are in [DESIGN.md](DESIGN.md#surfaces).

## Child DOX Index

- `plugin/AGENTS.md`: the Tern package: file ownership, architecture boundaries (budgets, `but`-only writes, half separation), Luau coding standards, verification commands
- `regroup/AGENTS.md`: dated status check-ins; only the latest is kept

Owned here (no child doc): `DESIGN.md` (surfaces, JSON fields relied on, action → command map, findings), `README.md` (user tutorial), `docs/images/` (README screenshots: `tern ctl … shot NAME` in the isolated Tern against a demo repo, copied from `/tmp/ttc/target/shots/tern/live/`; keep them outside `plugin/` so they never reload Tern). Retake a screenshot when the UI it shows changes. `.codegraph/`: the codegraph index; only its `.gitignore` is tracked, the database stays local. `todo.txt` / `done.txt` at the root: the project's task list (tuxedo, tag `+gitbutler-for-tern`), git-ignored so it stays local. It is the source of truth: `~/todo.txt` routes to it (`@route @mirror`) and a local watcher copies its open tasks there, so edit tasks here, never in the global copy.

## References

- `DESIGN.md`: design note and findings
- Tern SDK: https://docs.stencil.so/tern/index.html (Blocks, Command Lenses, Host Hooks, Chrome, Commands, API Reference; append `.md` to any page for markdown)
- Example plugins: https://github.com/stencil-hq/tern-sdk (`plugins/examples/`)
- GitButler JSON: https://docs.gitbutler.com/cli-guides/cli-tutorial/scripting; CLI rules: `skill://but`
