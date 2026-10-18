"""Run the real window watcher with scheduled board reads and file updates."""
import json
from pathlib import Path
import subprocess
import tempfile

plugin = Path(__file__).parent
sources = {name: (plugin / f"{name}.luau").read_text() for name in ("gb", "drag", "window")}
bootstrap = "local sources = {\n" + "\n".join(
    f"[{json.dumps(name)}] = {json.dumps(source, ensure_ascii=False)}," for name, source in sources.items()
) + "\n}\n"
checks = r'''
local root, data = "/fixture", "/plugin-data"
local commands, timers, files, decoded, events, modules = {}, {}, {}, {}, {}, {}
local snapshot
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
print("board watcher: stale snapshots rejected; hand edits resynced; displayed commit targeted")
'''
with tempfile.TemporaryDirectory(prefix=".board-check-", dir=plugin.parent) as tmp:
    runner = Path(tmp) / "check.luau"
    runner.write_text(bootstrap + checks)
    subprocess.run(["luau", str(runner)], check=True)
