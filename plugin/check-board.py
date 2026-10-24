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
local window_starts, pane_closures = {}, {}
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
    on = function(name, handler)
        if name == "window_start" then table.insert(window_starts, handler) end
        if name == "pane_closed" then table.insert(pane_closures, handler) end
    end,
    override = function() end,
    chrome = { status = function() end }, route = { open = function() end },
}
local env = setmetatable({ tern = tern }, { __index = getfenv() })
env.require = function(path) return assert(modules[string.sub(path, 3)]) end
for _, name in { "gb", "drag" } do
    local chunk = assert(loadstring(sources[name], name)); setfenv(chunk, env); modules[name] = chunk()
end
local path = modules.gb.board_path(root)
local boardPane = { pane = 2, kind = "board", title = string.match(path, "([^/]+)$"), path = path }
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
for _, trigger in { "reload", "window_start" } do
    for _, invalid in { "partial", "unreadable", "nonobject", "foreign" } do
        local saved_panes, saved_read, saved_board_read = cx.session.panes, tern.fs.read, cx.board.read
        regression("reattach " .. trigger .. " after " .. invalid .. " sidecar", function()
            table.clear(timers); table.clear(events); table.clear(window_starts)
            files[path .. ".json"] = "initial"
            snapshot = snap("reattach", "c-a1", "c-b1")
            local broken_path = modules.gb.board_path("/partial-fixture")
            local broken = { pane = 3, kind = "board", title = string.match(broken_path, "([^/]+)$"), path = broken_path }
            files[broken_path .. ".json"] = if invalid == "nonobject" then "nonobject" elseif invalid == "foreign" then "foreign-entry" else '{"root":'
            decoded.nonobject = "not a root record"
            decoded["foreign-entry"] = projection("c-a1", "c-b1")
            cx.session.panes = function() return { workspace, broken, boardPane } end
            tern.fs.read = function(target)
                if invalid == "unreadable" and target == broken_path .. ".json" then error("read interrupted") end
                return saved_read(target)
            end
            cx.board.read = function(_, pane)
                assert(pane == boardPane.pane, "an invalid sidecar must not attach a watcher")
                return saved_board_read()
            end
            local window = assert(loadstring(sources.window, "window")); setfenv(window, env); window()
            local startup = table.clone(timers); table.clear(timers)
            if trigger == "reload" then
                for _, timer in startup do if timer.ms == 0 then timer.fn(cx) end end
            else
                for _, handler in window_starts do handler(cx) end
            end
            poll()
            assert(#events == 0, "the valid later board must bind without resync")
            local moved = table.remove(snapshot.lanes[2].cards, 1); table.insert(snapshot.lanes[1].cards, moved)
            poll()
            assert(#events == 1 and events[1].act == "native_drop" and events[1].value == "c-a1:U::1", "a bad sidecar must not block a later board's ordinary drag")
        end)
        cx.session.panes, tern.fs.read, cx.board.read = saved_panes, saved_read, saved_board_read
    end
end
regression("distinct workspace board paths", function()
    local first, second = "/work/Ab/repo", "/work/BA/repo"
    assert(modules.gb.board_path(first) ~= modules.gb.board_path(second), "distinct roots must never share a projection path")
    local long = modules.gb.board_path("/work/" .. string.rep("long-parent/", 30) .. "repo")
    for component in string.gmatch(long, "[^/]+") do
        assert(#component <= 255, "board paths must fit the filesystem's component limit")
    end
end)
for _, change in { "reload", "metadata" } do
    regression("foreign sidecar during " .. change, function()
        start("owned", projection("c-a1", "c-b1"))
        local foreign = projection("c-a1", "c-b1")
        foreign.root = "/other-workspace"
        decoded.foreign = foreign; files[path .. ".json"] = "foreign"
        if change == "reload" then snapshot = snap("foreign", "c-a1", "c-b1") end
        poll()
        local moved = table.remove(snapshot.lanes[2].cards, 1); table.insert(snapshot.lanes[1].cards, moved)
        poll()
        for _, event in events do assert(event.act ~= "native_drop", "foreign metadata must never authorize a drop") end
        files[path .. ".json"] = "regression"
        snapshot = snap("restored", "c-a1", "c-b1")
        poll(); table.clear(events)
        moved = table.remove(snapshot.lanes[2].cards, 1); table.insert(snapshot.lanes[1].cards, moved)
        poll()
        assert(#events == 1 and events[1].value == "c-a1:U::1", "matching workspace metadata must retain ordinary dragging")
    end)
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
    local host_panes = { { pane = 1 } }
    local hold_status, pending_status = false, {}
    local managed = { [root] = true, ["/work/Ab/repo"] = true, ["/work/BA/repo"] = true }
    local saved_exists, saved_read = tern.fs.exists, tern.fs.read
    tern.fs.exists = function(target)
        for managed_root in managed do
            if target == managed_root .. "/.git" or target == managed_root .. "/.git/gitbutler" then return true end
        end
        return saved_exists(target)
    end
    tern.fs.read = function(target)
        local managed_root = string.match(target, "^(.*)/%.git/HEAD$")
        if managed_root and managed[managed_root] then return "ref: refs/heads/gitbutler/workspace" end
        return saved_read(target)
    end
    tern.ui = {}
    tern.block = { define = function(name, definition) blocks[name] = definition end }
    tern.pane = { list = function() return host_panes end }
    tern.kv = { get = function() return "board" end }
    tern.fs.mkdir = function() end
    local writes = 0
    tern.fs.write = function(target, text) writes += 1; files[target] = text end
    tern.json.array = function(value) return value or {} end
    local function encode(value)
        if type(value) ~= "table" then return string.format("%q", tostring(value)) end
        local keys, parts = {}, {}
        for key in value do table.insert(keys, key) end
        table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
        for _, key in keys do table.insert(parts, encode(key) .. ":" .. encode(value[key])) end
        return "{" .. table.concat(parts, ",") .. "}"
    end
    tern.json.encode = function(value)
        local text = encode(value)
        decoded[text] = value
        return text
    end
    tern.process = { run = function(argv, _, done)
        assert(argv[4] == "status", "projection checks must not mutate the repository")
        decoded["{}"] = status
        if hold_status then table.insert(pending_status, done); return end
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
    regression("two workspaces keep separate generated boards", function()
        host_panes = { { pane = 3 }, { pane = 4 } }
        status = { uncommittedChanges = { { cliId = "u1", filePath = "work.txt", changeType = "modified" } }, stacks = {} }
        local first_path, first_text
        for i, managed_root in { "/work/Ab/repo", "/work/BA/repo" } do
            local bcx = { pane = i + 2, cwd = managed_root, cols = 80, rows = 40, render = function() end, toast = function() end }
            local state = blocks.workspace.init(bcx, {}, nil)
            blocks.workspace.event(state, { ev = "action", act = "native_board" }, bcx)
            local target = modules.gb.board_path(managed_root)
            assert(decoded[files[target .. ".json"]].root == managed_root, "each written sidecar must belong to its workspace")
            if i == 1 then first_path, first_text = target, files[target .. ".json"] end
        end
        assert(files[first_path .. ".json"] == first_text, "writing the second workspace must not overwrite the first sidecar")
    end)
    for _, timing in { "before", "during" } do
        for _, phase in { "sheet", "busy", "closed", "closed-busy", "closed-sheet" } do
            regression("pending drop across panels while " .. phase .. ", sibling opened " .. timing, function()
                host_panes = { { pane = 1 }, { pane = 2 } }
                pending_status = {}
                status = { uncommittedChanges = { { cliId = "u1", filePath = "work.txt", changeType = "modified" } }, stacks = {} }
                local function panel(pane)
                    local bcx = { pane = pane, cwd = root, cols = 80, rows = 40, render = function() end, toast = function() end }
                    return blocks.workspace.init(bcx, {}, nil), bcx
                end
                local owner, ocx = panel(1)
                blocks.workspace.event(owner, { ev = "action", act = "native_board" }, ocx)
                local original, original_keys = files[path], files[path .. ".json"]
                local sibling, scx
                if timing == "before" then
                    sibling, scx = panel(2)
                    local before = writes
                    blocks.workspace.key(sibling, { text = "r", name = "r" }, scx)
                    assert(writes == before, "an unchanged refresh must not rewrite an already-synced board")
                end
                local key = decoded[files[path .. ".json"]].lanes[1].cards[1]
                local drop = { ev = "action", act = "native_drop", value = key .. ":N::1" }
                if phase == "closed-sheet" then
                    blocks.workspace.key(owner, { text = "?", name = "?" }, ocx)
                    assert(owner.sheet and owner.sheet.kind == "help", "an unrelated sheet must already be open")
                end
                files[path] = "pending card position"
                hold_status = phase == "busy" or phase == "closed-busy"
                blocks.workspace.event(owner, drop, ocx)
                hold_status = false
                assert(owner.sheet or owner.busy, "the first drop must own a pending action")
                local pending_keys = files[path .. ".json"]
                if timing == "during" then sibling, scx = panel(2) end
                assert(files[path] == "pending card position" and files[path .. ".json"] == pending_keys, "a sibling's initial refresh must preserve a pending projection")
                blocks.workspace.key(sibling, { text = "r", name = "r" }, scx)
                if phase == "sheet" or phase == "busy" then
                    blocks.workspace.event(sibling, { ev = "action", act = "native_board" }, scx)
                end
                assert(files[path] == "pending card position" and files[path .. ".json"] == pending_keys, "polls and forced resyncs must preserve another panel's pending projection")
                if phase == "sheet" or phase == "busy" then
                    blocks.workspace.event(sibling, drop, scx)
                end
                assert(sibling.sheet == nil and sibling.busy == nil and not sibling.drop_pending, "a second panel must not take ownership of a pending drop")
                host_panes = { { pane = 1 }, { pane = 2 }, { pane = 3 } }
                local other_root = "/work/Ab/repo"
                local other_cx = { pane = 3, cwd = other_root, cols = 80, rows = 40, render = function() end, toast = function() end }
                local other = blocks.workspace.init(other_cx, {}, nil)
                blocks.workspace.event(other, { ev = "action", act = "native_board" }, other_cx)
                assert(decoded[files[modules.gb.board_path(other_root) .. ".json"]].root == other_root, "a pending drop must not block another workspace")
                assert(files[path] == "pending card position", "another workspace must not disturb the pending projection")
                if phase == "closed" or phase == "closed-busy" or phase == "closed-sheet" then
                    local closing_cx = { session = {
                        panes = function() return { { pane = 2, block = "gitbutler.workspace" } } end,
                        event = function(_, pane, event)
                            assert(pane == 2, "closure must reach the surviving workspace panel")
                            blocks.workspace.event(sibling, event, scx)
                        end,
                    } }
                    for _, handler in pane_closures do handler({ pane = 1 }, closing_cx) end
                    if phase == "closed-busy" then
                        assert(#pending_status == 1, "the closed owner must retain its original status read")
                        pending_status[1]({ stdout = "{}", stderr = "", status = 0 })
                    end
                    blocks.workspace.key(sibling, { text = "r", name = "r" }, scx)
                else
                    if phase == "busy" then
                        assert(#pending_status == 1, "the first drop must retain its original status read")
                        pending_status[1]({ stdout = "{}", stderr = "", status = 0 })
                    end
                    blocks.workspace.key(owner, { name = "escape" }, ocx)
                end
                assert(files[path] == original and files[path .. ".json"] == original_keys, "cancellation or closing the owner must restore the authoritative projection on an ordinary refresh")
                blocks.workspace.event(sibling, drop, scx)
                assert(sibling.sheet and sibling.sheet.title == "New branch name", "the sibling must accept the next drop after release")
                blocks.workspace.key(sibling, { name = "escape" }, scx)
            end)
            hold_status = false
        end
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
-- Tern 0.9.5's plugin API keeps only a pane id's low 44 bits (DESIGN.md
-- finding 8). `tui` must then split and float through the `tern` CLI with
-- full ids, and reuse the pane it made instead of opening a second TUI.
local HIGH = 2 ^ 44
local function id(n) return string.format("%.0f", n) end
local calls, native_splits, listing = {}, 0, { sessions = { { tabs = { { blocks = {} } } } } }
local cli_failures, native_floats, native_focuses = {}, {}, {}
local native_pane
local function list(...) listing.sessions[1].tabs[1].blocks = { ... } end
local function block(n) return { id = n } end
decoded.ls, decoded.split = listing, { block = HIGH + 9 }
-- Callbacks queue like real processes, so two presses can overlap before any finishes.
local queued = {}
tern.process = { run = function(argv, _, cb)
    table.insert(calls, table.concat(argv, " ", 2))
    local failure = cli_failures[argv[2]]
    table.insert(queued, function()
        cb({ status = if failure then 1 else 0, stdout = if argv[2] == "ls" or argv[2] == "split" then argv[2] else "", stderr = failure or "" }, nil)
    end)
end }
local function drain()
    while #queued > 0 do table.remove(queued, 1)() end
end
local function press() commands.tui(cx); drain() end
local function tui_window(focused, split, float_ok)
    table.clear(timers); table.clear(calls); table.clear(notices); table.clear(queued); native_splits = 0
    table.clear(cli_failures); table.clear(native_floats); table.clear(native_focuses); native_pane = nil
    chunk = assert(loadstring(sources.window, "window")); setfenv(chunk, env); chunk()
    table.clear(timers)
    cx.session.focused = function() return focused end
    cx.session.panes = function()
        local panes = { { pane = focused, cwd = root } }
        if native_pane then table.insert(panes, native_pane) end
        return panes
    end
    cx.session.pane = function(_, pane)
        return if native_pane and pane == native_pane.pane and float_ok then native_pane else nil
    end
    cx.layout.split = function()
        native_splits += 1
        native_pane = if split then { pane = split, cwd = root } else nil
        return split
    end
    cx.layout.float = function(_, pane, over)
        table.insert(native_floats, { pane = pane, over = over })
        return float_ok, if float_ok then nil else "picture in picture needs another tiled block on the same host"
    end
    cx.layout.focus = function(_, pane) table.insert(native_focuses, pane) end
end
local function expect(want, why)
    assert(table.concat(calls, "\n") == table.concat(want, "\n"), why .. ":\n" .. table.concat(calls, "\n"))
end
-- A pane numbered past 2^44: the native split misses it, the CLI uses its full id.
tui_window(7, nil, true)
list(block(HIGH + 7))
press()
expect({ "ls --json", "split " .. id(HIGH + 7) .. " right --json --cwd " .. root .. " -- but tui",
    "pip " .. id(HIGH + 9) .. " --over " .. id(HIGH + 7), "focus " .. id(HIGH + 9) }, "a high-id pane must split and float through the CLI")
table.clear(calls); list(block(HIGH + 7), block(HIGH + 9))
press()
expect({ "ls --json", "focus " .. id(HIGH + 9) }, "running tui again must focus the CLI-opened TUI")
assert(native_splits == 1, "a CLI-opened TUI must not be duplicated by a native split")
-- An old pane under 2^44 splits natively, but its new pane is past 2^44:
-- float that same pane through the CLI, never a second split.
tui_window(5, 9, false)
list(block(5), block(HIGH + 9))
press()
expect({ "ls --json", "pip " .. id(HIGH + 9) .. " --over 5", "focus " .. id(HIGH + 9) }, "a failed native float must float the pane it split")
-- Two listed panes share the low bits (or the pane is in another window): refuse.
tui_window(7, nil, true)
list(block(HIGH + 7), block(2 * HIGH + 7))
press()
expect({ "ls --json" }, "an ambiguous id must not split or float anything")
for _, timer in timers do timer.fn(cx) end
assert(notices[1] == "Could not open but tui", "an ambiguous id must report why the TUI did not open")
table.clear(calls); table.clear(timers); list(block(HIGH + 7))
press()
assert(#calls == 4, "a refusal must release the latch so the next press opens the TUI:\n" .. table.concat(calls, "\n"))
-- Two quick presses before any CLI call returns open one TUI, not two.
tui_window(7, nil, true)
list(block(HIGH + 7))
commands.tui(cx); commands.tui(cx); drain()
local splits = 0
for _, call in calls do if string.sub(call, 1, 5) == "split" then splits += 1 end end
assert(splits == 1 and native_splits == 1, "a second press during a CLI open must not split again")
tui_window(5, 9, false)
list(block(5), block(HIGH + 9))
commands.tui(cx); commands.tui(cx); drain()
assert(native_splits == 1, "a second press during a CLI refloat must not split natively again")
for _, failure in { "ls", "missing", "ambiguous" } do
    regression("native TUI retained after " .. failure .. " lookup", function()
        tui_window(5, 9, false)
        if failure == "ls" then cli_failures.ls = "daemon unavailable" end
        if failure == "ambiguous" then list(block(5), block(HIGH + 9), block(2 * HIGH + 9)) else list(block(5)) end
        press()
        table.clear(calls)
        cx.session.focused = function() return 9 end
        press()
        expect({ "ls --json" }, "a failed lookup must retry the existing native TUI without splitting")
        assert(native_splits == 1 and #native_floats == 2, "lookup failure must retain the native split")
        assert(native_floats[2].pane == 9 and native_floats[2].over == 5, "native retry must retain the original pane and owner after focus changes")
        table.clear(calls); cli_failures.ls = nil; list(block(5), block(HIGH + 9))
        commands.tui(cx); commands.tui(cx); drain()
        expect({ "ls --json", "pip " .. id(HIGH + 9) .. " --over 5", "focus " .. id(HIGH + 9) }, "lookup recovery must float the retained TUI")
        assert(native_splits == 1, "lookup recovery must not open another TUI")
        table.clear(calls); press()
        expect({ "ls --json", "focus " .. id(HIGH + 9) }, "a recovered TUI must be focused without floating again")
    end)
end
for _, creation in { "native", "cli" } do
    regression(creation .. " TUI retained after pip failure", function()
        local owner = if creation == "native" then 5 else HIGH + 7
        tui_window(if creation == "native" then 5 else 7, if creation == "native" then 9 else nil, false)
        list(block(owner), block(HIGH + 9))
        cli_failures.pip = "float unavailable"
        press()
        table.clear(calls)
        cx.session.focused = function() return 9 end
        cx.session.panes = function() return { { pane = 9, cwd = root } } end
        cli_failures.ls = "daemon unavailable"
        press()
        expect({ "ls --json" }, "a failed listing must retain the CLI TUI for another retry")
        table.clear(calls); cli_failures.ls = nil
        press()
        expect({ "ls --json", "pip " .. id(HIGH + 9) .. " --over " .. id(owner), "focus " .. id(HIGH + 9) }, "failed pip must be retried on the same TUI over its original owner")
        table.clear(calls); cli_failures.pip = nil
        commands.tui(cx); commands.tui(cx); drain()
        expect({ "ls --json", "pip " .. id(HIGH + 9) .. " --over " .. id(owner), "focus " .. id(HIGH + 9) }, "pip recovery must float and focus the existing TUI")
        assert(native_splits == 1, "pip retry must never split another native pane")
        table.clear(calls); press()
        expect({ "ls --json", "focus " .. id(HIGH + 9) }, "successful pip retry must return to ordinary focus-only reuse")
    end)
end
regression("native float retry without the CLI", function()
    tui_window(5, 9, false)
    list(block(5)); press()
    table.clear(calls)
    cx.layout.float = function(_, pane, over)
        assert(pane == 9 and over == 5, "native float recovery must reuse the original pane and owner")
        return true
    end
    press()
    expect({}, "a recovered native float must not need the CLI")
    assert(native_splits == 1 and native_focuses[1] == 9, "native float recovery must focus its existing TUI")
end)
for _, ended in { "exited", "absent" } do
    regression("replace " .. ended .. " native TUI", function()
        tui_window(5, 9, false)
        list(block(5)); press()
        if ended == "exited" then native_pane.exited = 0 else native_pane = nil end
        list(block(5), block(HIGH + 9)); press()
        assert(native_splits == 2, "an ended native TUI must not block opening its replacement")
    end)
    regression("replace " .. ended .. " CLI TUI", function()
        tui_window(7, nil, true)
        list(block(HIGH + 7)); cli_failures.pip = "float unavailable"; press()
        local old = block(HIGH + 9)
        if ended == "exited" then old.exited = 0; list(block(HIGH + 7), old) else list(block(HIGH + 7)) end
        table.clear(calls); cli_failures.pip = nil; press()
        expect({ "ls --json", "split " .. id(HIGH + 7) .. " right --json --cwd " .. root .. " -- but tui",
            "pip " .. id(HIGH + 9) .. " --over " .. id(HIGH + 7), "focus " .. id(HIGH + 9) }, "an ended CLI TUI must allow a replacement")
    end)
end
-- A healthy Tern floats natively without the CLI.
tui_window(7, 10, true)
press()
expect({}, "a native split and float must not run the CLI")
press()
assert(native_splits == 1 and #native_floats == 1 and #native_focuses == 2, "healthy native reuse must only focus its live TUI")
if #failures > 0 then error(table.concat(failures, "\n")) end
print("board watcher: stale snapshots rejected; lane edits and reloads resynced; read limits reported; assigned files projected; tui ids past 2^44 opened through the CLI")
'''
with tempfile.TemporaryDirectory(prefix=".board-check-", dir=plugin.parent) as tmp:
    runner = Path(tmp) / "check.luau"
    runner.write_text(bootstrap + checks)
    subprocess.run(["luau", str(runner)], check=True)
