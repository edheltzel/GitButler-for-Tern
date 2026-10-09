--!strict
-- `luau check.luau`: the lens parsers against output captured from but 0.22.3,
-- and the terminal width the dock is sized by. Prints `parse: ok` (CI greps it).

local parse = require("./parse")
local width = require("./width")

local function lines(text: string): { string }
	local out = {}
	for line in string.gmatch(text .. "\n", "(.-)\n") do
		table.insert(out, line)
	end
	return out
end

-- Stacked branches, an anonymous segment, upstream commits and -f files.
local status = parse.status(lines([[
╭┄ zz [uncommitted]
┊   nk M a.txt
┊   ws  A d dir/new file.md
┊
┊╭┄ g0
┊●   xuk fix(sync): keep legacy text
┊│
┊├┄ et [mc-delivery-policy]
┊┊
┊╭┄┄ (upstream: on origin/mc-delivery-policy)
┊●   c0b547b ci: validate destinations (#165)
┊-
┊●   zpl docs: record small commits
┊│     zpl:x M src/a.ts
┊│     zpl:p A src/b.ts
├╯
┊
┊╭┄ eat [feat-a]
┊●   yzs Ed Heltzel 2026-10-06 10:07:47 -0400 (sha fe24e6f)
┊│     a: more b
├╯
┊
┴ af94f7d (common base) 2026-10-06 initial

Hint: commits are listed newest first. The first token on each line is the ID to use in commands.]]), true)
assert(status, "status parsed")
assert(#status.unassigned == 2 and status.unassigned[2].path == "d dir/new file.md" and status.unassigned[2].change == "A")
assert(#status.stacks == 2, "two stacks")
local s1 = status.stacks[1]
assert(#s1.branches == 2 and s1.branches[1].name == "" and s1.branches[2].name == "mc-delivery-policy")
local policy = s1.branches[2]
assert(#policy.commits == 2 and policy.commits[1].upstream and not policy.commits[2].upstream and policy.upstream == 1)
assert(#policy.commits[2].files == 2 and policy.commits[2].files[2].id == "zpl:p")
local verbose = status.stacks[2].branches[1].commits[1]
assert(verbose.id == "yzs" and verbose.sha == "fe24e6f" and verbose.title == "a: more b", verbose.title)
assert(status.base == "af94f7d")
assert(parse.status(lines("No GitButler project found")) == nil)

local annotated = parse.status(lines([[
╭┄ ymn [feat-b]
● ymn Ed Heltzel 2026-10-06 10:07:42 -0400 (sha 79b9f88) (no changes)
  (no commit message)
]]), true)
assert(annotated, "annotated status parsed")
local note = annotated.stacks[1].branches[1].commits[1]
assert(note.id == "ymn" and note.sha == "79b9f88" and note.title == "(no commit message)", note.title)

local compact_sha = parse.status(lines([[
╭┄ xyz [feat-c]
● xyz fix (sha abc1234) handling
]]))
assert(compact_sha, "compact sha subject parsed")
local kept = compact_sha.stacks[1].branches[1].commits[1]
assert(kept.id == "xyz" and kept.sha == "" and kept.title == "fix (sha abc1234) handling", kept.title)

-- A compact subject shaped like a verbose header keeps its title without -v.
local dated = parse.status(lines([[
╭┄ rel [feat-d]
● rel release 2026-10-06 10:07:42 -0400 (sha abc1234) handling
● nxt next commit
]]))
assert(dated, "dated compact subject parsed")
local dc = dated.stacks[1].branches[1].commits
assert(#dc == 2 and dc[1].sha == "" and dc[1].title == "release 2026-10-06 10:07:42 -0400 (sha abc1234) handling", dc[1].title)

local diff = parse.diff(lines([[
────────────╮
 nk:a a.txt │
────────────╯

@@ -1,2 +1,3 @@
───────────────
1 ┊ 1 │  hello
2 ┊   │ -world
  ┊ 2 │ +WORLD
  ┊ 3 │ +again

────────────────╮
 modified b.txt │
────────────────╯

@@ -1,1 +1,2 @@
───────────────
1 ┊ 1 │  one
  ┊ 2 │ +two]]))
assert(diff and #diff == 2, "two files")
assert(diff[1].path == "a.txt" and diff[1].label == "nk:a")
assert(diff[1].text == "--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,3 @@\n hello\n-world\n+WORLD\n+again", diff[1].text)
assert(diff[2].path == "b.txt" and string.find(diff[2].text, "\n one\n%+two$"))

local commit = parse.show(lines([[
Commit:    fe24e6f815108b94fe81a90c0729b24275850625
Change-ID: yzsvrtoutynkvssozmnrmmqwqmoumttq
Author:    Ed Heltzel <ed@example.com>
Date:      2026-10-06 10:07:47 -0400 (1h ago)

a: more b

Body line.

Files changed:
  M b.txt
  A c d.txt]]))
assert(commit and commit.kind == "commit" and #commit.fields == 4 and commit.fields[3][2] == "Ed Heltzel <ed@example.com>")
assert(commit.message == "a: more b\n\nBody line.", commit.message)
assert(#commit.files == 2 and commit.files[2].path == "c d.txt")

local branch = parse.show(lines([[
Branch: feat-a

Commits:
  yzs a: more b
    2026-10-06 10:07:47 by Ed Heltzel
  znz a: world
    2026-10-06 10:07:41 by Ed Heltzel]]))
assert(branch and branch.kind == "branch" and branch.branch == "feat-a" and #branch.commits == 2)
assert(branch.commits[2].id == "znz" and branch.commits[2].author == "Ed Heltzel")

local verbose_branch = parse.show(lines([[
Branch: feat-a

Commits:
● yzs a: more b
│ 2026-10-06 10:07:47, 1h ago by Ed Heltzel
│
│ Files changed:
│ M b.txt  (+1, -0)
│
│ 1 file changed, 1 insertion, 0 deletions
│
┴ nss initial (base)
  2026-10-06 10:05:55, 1h ago by Ed Heltzel

Branch Summary:
  1 commits]]))
assert(verbose_branch and #verbose_branch.commits == 1 and #verbose_branch.commits[1].files == 1)
assert(verbose_branch.commits[1].files[1].path == "b.txt")

local ops = parse.oplog(lines([[
Operations History
──────────────────────────────────────────────────
a7d7700 2026-10-06 11:32:51 [UNDO] Restored from snapshot: Created commit (3a34003)
c5bdbeb 2026-10-06 11:32:47 [SNAPSHOT] pre-test]]))
assert(ops and #ops == 2 and ops[1].kind == "UNDO" and ops[2].title == "pre-test")

-- Terminal cells: wide below U+2E80 (watch, Hangul jamo), emoji, CJK,
-- zero-width combining marks, emoji sequences, and invalid UTF-8.
assert(width.cells("abc") == 3)
assert(width.cells("watch-⌚") == 8, tostring(width.cells("watch-⌚")))
assert(width.cells("ᄀ") == 2)
assert(width.cells("日本") == 4)
assert(width.cells("👍") == 2)
assert(width.cells("e\u{301}") == 1)
assert(width.cells("☺\u{FE0F}") == 2, "VS16 makes a text symbol an emoji")
assert(width.cells("⌚\u{FE0E}") == 1, "VS15 makes an emoji text")
assert(width.cells("👍🏽") == 2, "skin tone joins its emoji")
assert(width.cells("👨\u{200D}👩\u{200D}👧") == 2, "ZWJ family is one glyph")
assert(width.cells("🇺🇸🇬🇧") == 4, "flags are regional indicator pairs")
assert(width.cells("→ x") == 3)
assert(width.cells("\255\254") == 2)

print("parse: ok")
