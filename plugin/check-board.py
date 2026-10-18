"""Run the real window watcher with scheduled board reads and file updates."""
import json
from pathlib import Path
import subprocess
import tempfile

plugin = Path(__file__).parent
sources = {name: (plugin / f"{name}.luau").read_text() for name in ("gb", "width", "drag", "host", "window")}
bootstrap = "local sources = {\n" + "\n".join(
    f"[{json.dumps(name)}] = {json.dumps(source, ensure_ascii=False)}," for name, source in sources.items()
) + "\n}\n"
checks = r'''
local root, data = "/fixture", "/plugin-data"
local commands, timers, files, decoded, events, modules = {}, {}, {}, {}, {}, {}
local snapshot
local failures, notices = {}, {}
local function regression(name, run)
    local ok, err = pcall(run)
    if not ok then table.insert(failures, name .. ": " .. tostring(err)) end
end
local tern = {
    plugin = { data = data }, getenv = function() return nil end,
    now = function() return 0 end,
    fs = {
        exists = function(path) return files[path] ~= nil or path == root .. "/.git" or path == root .. "/.git/gitbutler" end,
        read = function(path)
            if path == root .. "/.git/HEAD" then return "ref: refs/heads/gitbutler/workspace" end
            return assert(files[path], "missing " .. path)
        end,
    },
    json = { decode = function(text) return assert(decoded[text], text) end },
    timer = function(ms, fn) table.insert(timers, { ms = ms, fn = fn }); return { cancel = function() end } end,
    command = function(def) commands[def.id] = def.run end,
    on = function() end, override = function() end,
    chrome = { status = function() end }, route = { open = function() end },
}
local env = setmetatable({ tern = tern }, { __index = getfenv() })
env.require = function(path) return assert(modules[string.sub(path, 3)]) end
for _, name in { "gb", "drag" } do
    local chunk = assert(loadstring(sources[name], name)); setfenv(chunk, env); modules[name] = chunk()
end
local path = modules.gb.board_path(root)
local boardPane = { pane = 2, kind = "board", title = string.match(path, "([^/]+)$") }
local workspace = { pane = 1, block = "gitbutler.workspace", cwd = root }
local cx = {
    toast = function(_, _, title, detail) table.insert(notices, { title = title, detail = detail }) end,
    session = {
        panes = function() return { workspace, boardPane } end,
        pane = function(_, pane) return if pane == 2 then boardPane else workspace end,
        focused = function() return nil end,
        surface = function() return { surface = {} } end,
        event = function(_, _, event) table.insert(events, event) end,
    },
    layout = { focus = function() end },
    board = {},
}
-- The callback closes over cx, so bind it after the table declaration.
cx.board.read = function() return { next = function(_, fn) fn(snapshot, nil, cx) end } end
local function label(text, key)
    return modules.drag.label(text, key)
end
local function projection(a, b)
    return { root = root, lanes = {
        { key = "U", title = "Uncommitted", cards = {} },
        { key = "b-A", title = "Branch: A", cards = { a } },
        { key = "b-B", title = "Branch: B", cards = { b } },
        { key = "N", title = "New branch", cards = {} },
    } }
end
local function snap(generation, a, b)
    return { lanes = {
        { id = generation .. "U", title = "Uncommitted", cards = {} },
        { id = generation .. "A", title = "Branch: A", cards = { { id = generation .. "cA", text = label("Same subject", a) } } },
        { id = generation .. "B", title = "Branch: B", cards = { { id = generation .. "cB", text = label("Same subject", b) } } },
        { id = generation .. "N", title = "New branch", cards = {} },
    } }
end
local function poll()
    for i, timer in timers do
        if timer.ms == 400 then table.remove(timers, i); timer.fn(cx); return end
    end
    error("no board watcher")
end
files[path .. ".json"] = "initial"
decoded.initial = projection("c-a1", "c-b1")
snapshot = snap("1", "c-a1", "c-b1")
local chunk = assert(loadstring(sources.window, "window")); setfenv(chunk, env); chunk()
local startup = table.clone(timers); table.clear(timers)
for _, timer in startup do if timer.ms == 0 then timer.fn(cx) end end
poll()
table.clear(events)
-- Newer metadata arrives before its Markdown reload. Both lanes have one
-- card with the same subject. The old displayed A must never become B.
local newer = projection("c-a2", "c-b2")
newer.lanes[2], newer.lanes[3] = newer.lanes[3], newer.lanes[2]
decoded.newer = newer; files[path .. ".json"] = "newer"
poll()
local card = table.remove(snapshot.lanes[2].cards, 1)
table.insert(snapshot.lanes[1].cards, card)
poll()
for _, event in events do
    assert(event.act ~= "native_drop", "old snapshot rebound to newer swapped metadata: " .. tostring(event.value))
end
-- The matching reload becomes usable; dragging displayed A uncommits A,
-- not the equal-subject commit in B.
snapshot = snap("2", "c-a2", "c-b2")
snapshot.lanes[2], snapshot.lanes[3] = snapshot.lanes[3], snapshot.lanes[2]
poll(); table.clear(events)
card = table.remove(snapshot.lanes[3].cards, 1); table.insert(snapshot.lanes[1].cards, card)
poll()
assert(#events == 1 and events[1].act == "native_drop" and events[1].value == "c-a2:U::1", "matching reload must preserve A's identity")
-- A live hand swap keeps lane ids; neither the swap nor the following read
-- may dispatch a GitButler operation or rebind equal-sized lanes.
snapshot = snap("3", "c-a2", "c-b2")
snapshot.lanes[2], snapshot.lanes[3] = snapshot.lanes[3], snapshot.lanes[2]
poll(); table.clear(events)
snapshot.lanes[2], snapshot.lanes[3] = snapshot.lanes[3], snapshot.lanes[2]
poll(); poll()
assert(#events == 1 and events[1].act == "native_board", "hand swap must request one rewrite, not a GitButler operation")
-- A same-order external rewrite also needs matching card identities before
-- it can replace the prior mapping, even though every subject is identical.
snapshot = snap("4", "c-a2", "c-b2")
snapshot.lanes[2], snapshot.lanes[3] = snapshot.lanes[3], snapshot.lanes[2]
poll(); table.clear(events)
decoded.rewritten = projection("c-a3", "c-b3")
decoded.rewritten.lanes[2], decoded.rewritten.lanes[3] = decoded.rewritten.lanes[3], decoded.rewritten.lanes[2]
files[path .. ".json"] = "rewritten"
poll()
card = table.remove(snapshot.lanes[3].cards, 1); table.insert(snapshot.lanes[1].cards, card)
poll()
assert(#events == 0, "same-subject metadata must not rebind an old displayed commit")
snapshot = snap("5", "c-a3", "c-b3")
snapshot.lanes[2], snapshot.lanes[3] = snapshot.lanes[3], snapshot.lanes[2]
poll()
card = table.remove(snapshot.lanes[3].cards, 1); table.insert(snapshot.lanes[1].cards, card)
poll()
assert(#events == 1 and events[1].value == "c-a3:U::1", "same-subject reload must target the new A identity")
decoded.batched = projection("c-a4", "c-b4")
decoded.batched.lanes[1].cards = { "f-u1" }
files[path .. ".json"] = "batched"
snapshot = snap("6", "c-a4", "c-b4")
snapshot.lanes[1].cards = { { id = "6f", text = label("work.txt", "f-u1") } }
poll(); table.clear(events)
-- Two physical moves before one read: A's commit and an uncommitted file
-- both end up in B. No single amend can safely represent that arrangement.
card = table.remove(snapshot.lanes[2].cards, 1); table.insert(snapshot.lanes[3].cards, 1, card)
card = table.remove(snapshot.lanes[1].cards, 1); table.insert(snapshot.lanes[3].cards, 1, card)
poll()
assert(#events == 1 and events[1].act == "native_board", "multiple drags in one read must reconcile without an amend")
local function start(generation, metadata, live)
    table.clear(timers); table.clear(events); table.clear(notices)
    decoded.regression = metadata; files[path .. ".json"] = "regression"
    snapshot = live or snap(generation, "c-a1", "c-b1")
    local window = assert(loadstring(sources.window, "window")); setfenv(window, env); window()
    local startup = table.clone(timers); table.clear(timers)
    for _, timer in startup do if timer.ms == 0 then timer.fn(cx) end end
    poll()
    assert(#events == 0, "initial projection must bind without resync")
end
for renamed = 1, 4 do
    for _, separate_read in { false, true } do
        regression("lane rename " .. renamed .. " separate=" .. tostring(separate_read), function()
            local metadata, live = projection("c-a1", "c-b1"), snap("rename", "c-a1", "c-b1")
            metadata.lanes[1].cards = { "f-u1" }
            live.lanes[1].cards = { { id = "rename-f", text = label("work.txt", "f-u1") } }
            metadata.lanes[2].cards, live.lanes[2].cards = {}, {}
            start("rename", metadata, live)
            snapshot.lanes[renamed].title = "Branch: renamed"
            if separate_read then poll() end
            local moved = table.remove(snapshot.lanes[1].cards, 1)
            table.insert(snapshot.lanes[2].cards, moved)
            poll(); poll()
            assert(#events == 1 and events[1].act == "native_board", "renamed lanes must reconcile once without dispatching a drop")
        end)
    end
end
regression("drag before reload binding", function()
    start("before", projection("c-a1", "c-b1"))
    decoded.reloaded = projection("c-a2", "c-b2"); files[path .. ".json"] = "reloaded"
    snapshot = snap("unbound", "c-a2", "c-b2")
    local moved = table.remove(snapshot.lanes[2].cards, 1)
    table.insert(snapshot.lanes[1].cards, moved)
    poll(); poll()
    assert(#events == 1 and events[1].act == "native_board", "an unmatched reload must request exactly one rewrite")
    snapshot = snap("reconciled", "c-a2", "c-b2")
    poll()
    moved = table.remove(snapshot.lanes[2].cards, 1); table.insert(snapshot.lanes[1].cards, moved)
    poll()
    assert(#events == 2 and events[2].value == "c-a2:U::1", "a reconciled reload must accept the next ordinary drag")
end)
for omitted = 0, 4 do
    regression("truncated snapshot " .. omitted, function()
        start("limit", projection("c-a1", "c-b1"))
        if omitted == 0 then snapshot.more = 1 else snapshot.lanes[omitted].more = 1 end
        local moved = table.remove(snapshot.lanes[2].cards, 1); table.insert(snapshot.lanes[1].cards, moved)
        poll(); poll()
        assert(#events == 0, "incomplete snapshots must neither dispatch drops nor request an impossible resync")
        assert(#notices == 1 and string.find(notices[1].title, "500", 1, true), "the unsupported read limit must be reported once")
    end)
end
regression("complete snapshot at the read limit", function()
    local metadata, live = projection("c-a1", "c-b1"), snap("full", "c-a1", "c-b1")
    live.more = 0
    for _, lane in live.lanes do lane.more = 0 end
    for i = 1, 500 do
        local key = "f-" .. i
        table.insert(metadata.lanes[1].cards, key)
        table.insert(live.lanes[1].cards, { id = key, text = label("file-" .. i, key) })
    end
    start("full", metadata, live)
    local moved = table.remove(snapshot.lanes[1].cards, 1); table.insert(snapshot.lanes[4].cards, moved)
    poll()
    assert(#events == 1 and events[1].value == "f-1:N::1" and #notices == 0, "a complete 500-card lane must still support ordinary drags")
end)
do
    local blocks, status = {}, nil
    tern.ui = {}
    tern.block = { define = function(name, definition) blocks[name] = definition end }
    tern.pane = { list = function() return { { pane = 1 } } end }
    tern.kv = { get = function() return "board" end }
    tern.fs.mkdir = function() end
    tern.fs.write = function(target, text) files[target] = text end
    tern.json.array = function(value) return value or {} end
    local serial = 0
    tern.json.encode = function(value)
        serial += 1
        local text = "encoded-" .. serial
        decoded[text] = value
        return text
    end
    tern.process = { run = function(argv, _, done)
        assert(argv[4] == "status", "projection checks must not mutate the repository")
        decoded["{}"] = status
        done({ stdout = "{}", stderr = "", status = 0 })
    end }
    modules.lenses = {}
    local width = assert(loadstring(sources.width, "width")); setfenv(width, env); modules.width = width()
    local host = assert(loadstring(sources.host, "host")); setfenv(host, env); host()
    for _, mode in { "unassigned", "assigned", "mixed" } do
        regression("generated " .. mode .. " files", function()
            status = { uncommittedChanges = {}, stacks = {} }
            if mode ~= "assigned" then
                status.uncommittedChanges = { { cliId = "u1", filePath = "work.txt", changeType = "modified" } }
            end
            for i = 1, 2 do
                table.insert(status.stacks, { cliId = "s" .. i,
                    assignedChanges = if mode == "unassigned" then {} else {
                        { cliId = "a" .. i, filePath = "assigned-" .. i .. ".txt", changeType = "modified" },
                    },
                    branches = { { cliId = "b" .. i, name = "branch-" .. i, commits = {
                        { cliId = "c" .. i, commitId = "sha" .. i, changeId = "change" .. i, message = "Commit " .. i,
                            changes = { { cliId = "cf" .. i, filePath = "committed-" .. i .. ".txt", changeType = "modified" } } },
                    } } },
                })
            end
            local bcx = { pane = 1, cwd = root, cols = 80, rows = 40, render = function() end, toast = function() end }
            local state = blocks.workspace.init(bcx, {}, nil)
            blocks.workspace.event(state, { ev = "action", act = "native_board" }, bcx)
            local projection = decoded[files[path .. ".json"]]
            local expected = if mode == "mixed" then 3 elseif mode == "assigned" then 2 else 1
            assert(#projection.lanes[1].cards == expected, "Uncommitted must include every assigned and unassigned file")
            local seen = {}
            for _, key in projection.lanes[1].cards do
                assert(not seen[key], "a changed file must appear once")
                seen[key] = true
            end
            for _, row in state.rows do
                if row.kind == "file" then
                    assert(seen[row.key], "the sidecar must preserve each file's existing row key")
                    assert(string.find(files[path], "[" .. row.path .. "](#gb-row-" .. row.key .. ")", 1, true), "Markdown must expose the same file identity")
                elseif row.kind == "commit" then
                    local found = false
                    for _, lane in projection.lanes do
                        if lane.key == row.bkey and table.find(lane.cards, row.key) then found = true end
                    end
                    assert(found and not seen[row.key], "commits must stay in their branch lanes")
                elseif row.kind == "cfile" then
                    assert(not string.find(files[path], row.path, 1, true), "committed files must not become uncommitted cards")
                end
            end
            if mode == "assigned" then
                blocks.workspace.event(state, { ev = "action", act = "native_drop", value = projection.lanes[1].cards[1] .. ":N::1" }, bcx)
                assert(state.sheet and state.sheet.title == "New branch name", "an assigned file must remain actionable through its projected row key")
            end
        end)
    end
end
for _, failure in failures do print(failure) end
assert(#failures == 0, #failures .. " board regressions failed")
-- A six-second initial status read is within the host's supported timeout.
-- The opener must still reveal its board when that read finally writes it.
table.clear(timers)
local clock, opened, notices = 0, 0, {}
tern.now = function() return clock end
cx.session.focused = function() return 1 end
cx.session.panes = function() return { workspace } end
cx.session.pane = function(_, id) return if id == 1 then workspace else nil end
cx.toast = function(_, _, title) table.insert(notices, title) end
cx.board.open = function(_, target) assert(target == path); opened += 1; return 2 end
files[path] = nil
chunk = assert(loadstring(sources.window, "window")); setfenv(chunk, env); chunk()
commands.board(cx)
for elapsed = 200, 6000, 200 do
    clock = elapsed
    if elapsed == 6000 then files[path] = "generated after status read" end
    for i, timer in timers do
        if timer.ms == 200 then table.remove(timers, i); timer.fn(cx); break end
    end
end
assert(opened == 1 and #notices == 0, "board opener timed out before a supported six-second status read completed")
print("board watcher: stale snapshots rejected; lane edits and reloads resynced; read limits reported; assigned files projected")
'''
with tempfile.TemporaryDirectory(prefix=".board-check-", dir=plugin.parent) as tmp:
    runner = Path(tmp) / "check.luau"
    runner.write_text(bootstrap + checks)
    subprocess.run(["luau", str(runner)], check=True)
