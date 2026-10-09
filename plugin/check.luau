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
assert(width.cells("☝🏽") == 2, "skin tone widens a text-width base")
assert(width.cells("⛹🏽") == 2, "skin tone widens a text-width base")
assert(width.cells("👨\u{200D}👩\u{200D}👧") == 2, "ZWJ family is one glyph")
assert(width.cells("🇺🇸🇬🇧") == 4, "flags are regional indicator pairs")
assert(width.cells("A\u{200D}B") == 2, "ZWJ joins only emoji")
assert(width.cells("👍\u{200D}B") == 3, "ZWJ before text joins nothing")
assert(width.cells("🏴\u{E0067}\u{E0062}\u{E0065}\u{E006E}\u{E0067}\u{E007F}") == 2, "tag flag is one glyph")
assert(width.cells("←\u{200D}→") == 2, "arrows outside Extended_Pictographic do not join")
assert(width.cells("a\u{FE0F}") == 1, "VS16 needs an emoji base")
assert(width.cells("a\u{1F3FD}") == 3, "skin tone without an emoji base is its own glyph")
assert(width.cells("1\u{FE0F}\u{20E3}") == 2, "keycap is one emoji")
assert(width.cells("🌍\u{1F3FD}") == 4, "skin tone needs an Emoji_Modifier_Base")
assert(width.cells("ש\u{05C1}") == 1, "Hebrew point is zero width")
assert(width.cells("→ x") == 3)
assert(width.cells("\255\254") == 2)

-- width.slice: rows around the cursor plus the markers it allows never
-- exceed the room, and the cursor's row always shows.
for _, room in { 1, 2, 3, 4, 7 } do
	for at = 1, 10 do
		local first, last, up, down = width.slice(10, at, room)
		local lines = (last - first + 1) + (if up then 1 else 0) + (if down then 1 else 0)
		assert(at >= first and at <= last, `cursor {at} hidden at room {room}`)
		assert(lines <= math.max(room, 1), `room {room}, cursor {at}: {lines} lines`)
		assert(not up or first > 1, "an up marker means rows are hidden above")
		assert(not down or last < 10, "a down marker means rows are hidden below")
	end
end
local a, b, c, d = width.slice(3, 2, 5)
assert(a == 1 and b == 3 and not c and not d, "everything fits: no markers")

print("parse: ok")
