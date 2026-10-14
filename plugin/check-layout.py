import json
from pathlib import Path
import subprocess
import tempfile


plugin = Path(__file__).parent
sources = {name: (plugin / f"{name}.luau").read_text() for name in ("gb", "width", "host")}
bootstrap = "local sources = {\n" + "\n".join(
    f"[{json.dumps(name)}] = {json.dumps(source, ensure_ascii=False)},"
    for name, source in sources.items()
) + "\n}\n"
checks = r'''
local blocks, modules, fixture = {}, {}, nil
local pane = 0
local root = "/fixture"
local ui = {}
function ui.el(tag, props, kids)
    return { k = "el", p = props, c = kids, tag = tag }
end
function ui.span(text, style) return { t = text, s = style } end
function ui.badge(text, tone) return { k = "badge", p = { text = text, tone = tone } } end
function ui.kv(items) return { k = "kv", p = { items = items } } end
function ui.text(spans) return { k = "text", p = { spans = spans } } end
function ui.col(kids) return { k = "col", c = kids } end
function ui.lines(kids) return { k = "col", c = kids } end
local tern = {
    ui = ui,
    block = { define = function(name, definition) blocks[name] = definition end },
    getenv = function() return nil end,
    fs = {
        exists = function(path) return path == root .. "/.git" or path == root .. "/.git/gitbutler" end,
        read = function(path)
            if path == root .. "/.git/HEAD" then return "ref: refs/heads/gitbutler/workspace" end
            error("not a file")
        end,
    },
    pane = { list = function() return { { pane = pane } } end },
    process = { run = function(_, _, done) done({ stdout = "{}", stderr = "", status = 0 }) end },
    json = { decode = function() return fixture end, encode = function(data) return tostring(data) end },
    timer = function() return { cancel = function() end } end,
    kv = { get = function() return "lanes" end, set = function() end },
}
local env = setmetatable({ tern = tern }, { __index = getfenv() })
env.require = function(path) return modules[string.sub(path, 3)] or {} end
for _, name in { "gb", "width", "host" } do
    local chunk = assert(loadstring(sources[name], name))
    setfenv(chunk, env)
    modules[name] = chunk()
end
local width, workspace = modules.width, blocks.workspace
local function status(count)
    local commits = {}
    for ci = 1, count do
        local files = {}
        for fi = 1, 7 do
            table.insert(files, { cliId = ci .. ":" .. fi, filePath = "file-" .. fi, changeType = "modified" })
        end
        table.insert(commits, {
            cliId = "c" .. ci, commitId = "sha" .. ci, changeId = "change" .. ci,
            message = "Commit " .. ci .. "\nline 1\nline 2\nline 3\nline 4", changes = files,
        })
    end
    return {
        uncommittedChanges = { { cliId = "u1", filePath = "work.txt", changeType = "modified" } },
        stacks = { { cliId = "s1", assignedChanges = {}, branches = {
            { cliId = "b1", name = "feature", branchStatus = "completelyUnpushed", commits = commits },
        } } },
    }
end
local function init(view, cols, rows, count)
    pane += 1
    fixture = status(count)
    local cx = { pane = pane, cwd = root, cols = cols, rows = rows }
    function cx:render() end
    function cx:toast() end
    local state = workspace.init(cx, {}, nil)
    state.view = view
    return state, cx
end
local function visit(node, fn, path)
    path = path or "main"
    fn(node, path)
    for i, child in node.c or {} do visit(child, fn, path .. "." .. i) end
end
local function positions(main)
    local found = {}
    visit(main, function(node, path)
        local p = node.p or {}
        if p.actions and p.actions.click and p.key then found[p.key] = path end
    end)
    return found
end
local function has_class(node, name)
    local class = (node.p or {}).class or ""
    for token in string.gmatch(class, "%S+") do if token == name then return true end end
    return false
end
local function height(node)
    local kids, sum = node.c or {}, 0
    for _, child in kids do sum += height(child) end
    if node.k == "kv" then return #(node.p.items) * 17 + 6 end
    if has_class(node, "bp-lanes") then return sum + math.max(0, #kids - 1) * 6 end
    if has_class(node, "bp-lane") then return sum + 10 end
    if has_class(node, "bp-lh") then return 29 end
    if has_class(node, "bp-gh") then return 22 end
    if has_class(node, "bp-r") then return 23 end
    if has_class(node, "bp-dh") then return 22 end
    if has_class(node, "bp-badges") then return 20 end
    if has_class(node, "bp-detail") then return sum + 16 + math.max(0, #kids - 1) * 8 end
    if has_class(node, "bp-f") then return 17 end
    if node.tag == "span" then
        local _, newlines = string.gsub(node.p.text, "\n", "")
        return (newlines + 1) * 17
    end
    return sum
end
local failures, cases = {}, 0
local function check(ok, message)
    cases += 1
    if not ok then table.insert(failures, message) end
end
local function resize(state, cx, cols, rows)
    cx.cols, cx.rows = cols, rows
    if workspace.resize then workspace.resize(state, cols, rows, cx) end
    return workspace.view(state, cx)
end
local function last_target(main)
    local key
    visit(main, function(node)
        local p = node.p or {}
        if p.actions and p.actions.click then key = p.key end
    end)
    return key
end
do
    local state, cx = init("graph", 78, 34, 12) -- 30 rows plus the two-row top bar
    fixture = status(12)
    fixture.uncommittedChanges = {}
    workspace.key(state, { name = "r" }, cx)
    check(positions(workspace.view(state, cx).main)["c-c5"] ~= nil,
        "the fifth commit must be visible before the dock notice")
    workspace.event(state, { ev = "action", act = "pick", value = "c-c5" }, cx)
    check(positions(workspace.view(state, cx).main)["c-c5"] ~= nil,
        "pointer selection moved the fifth commit before the dock notice")
    workspace.key(state, { name = "c" }, cx)
    local after = workspace.view(state, cx)
    local notified = false
    visit(after.dock, function(node)
        if node.k == "text" then
            for _, span in node.p.spans do
                if span.t == "Nothing to commit." then notified = true end
            end
        end
    end)
    check(notified, "committing an empty workspace must show its notice")
    check(state.cursor == "c-c5" and positions(after.main)["c-c5"] ~= nil,
        "the Nothing to commit notice hid the selected fifth commit")
    workspace.key(state, { name = "enter" }, cx)
    check(state.diff ~= nil and state.diff.id == "c5",
        "the visible fifth commit must remain the action target")
end
for _, view in { "lanes", "graph", "stacks", "branches" } do
    for _, field in { "notice", "problem", "busy", "loading" } do
        local state, cx = init(view, 78, 30, 12)
        fixture = status(12)
        fixture.uncommittedChanges = {}
        workspace.key(state, { name = "r" }, cx)
        local target = last_target(workspace.view(state, cx).main)
        workspace.event(state, { ev = "action", act = "pick", value = target }, cx)
        if field == "notice" then
            state.notice = { tone = "warning", text = string.rep("n", 70) }
        elseif field == "problem" then
            state.problem = string.rep("e", 70)
        elseif field == "busy" then
            state.busy = string.rep("b", 70)
        else
            state.loading = true
        end
        check(state.cursor == target and positions(workspace.view(state, cx).main)[target] ~= nil,
            view .. ": growing the " .. field .. " dock output hid the selection")
        state[field] = if field == "loading" then false else nil
        check(state.cursor == target and positions(workspace.view(state, cx).main)[target] ~= nil,
            view .. ": removing the " .. field .. " dock output hid the selection")
    end
end
do
    local state, cx = init("graph", 78, 30, 12)
    state.notice = { tone = "info", text = "A notice" }
    local before = positions(workspace.view(state, cx).main)
    local target = last_target(workspace.view(state, cx).main)
    workspace.event(state, { ev = "action", act = "pick", value = target }, cx)
    state.notice = { tone = "warning", text = "B notice" }
    local after = positions(workspace.view(state, cx).main)
    for key, path in before do
        check(after[key] == path, "a same-height notice or pointer selection moved " .. key)
    end
end
do
    local state, cx = init("graph", 78, 34, 12) -- 30 rows plus the two-row top bar
    check(positions(workspace.view(state, cx).main)["c-c4"] ~= nil,
        "the reported fourth commit must be visible before the click")
    workspace.event(state, { ev = "action", act = "pick", value = "c-c4" }, cx)
    check(positions(workspace.view(state, cx).main)["c-c4"] ~= nil,
        "pointer selection hid the reported fourth commit")
    check(positions(resize(state, cx, 78, 24).main)["c-c4"] ~= nil,
        "shortening graph view hid the selected fourth commit")
end
for _, view in { "lanes", "graph", "stacks", "branches" } do
    for _, cols in { 78, 160 } do
        for _, size in { { 160, 24 }, { 78, 30 }, { 78, 24 }, { 160, 60 } } do
            local state, cx = init(view, cols, 30, 12)
            local target = last_target(workspace.view(state, cx).main)
            workspace.event(state, { ev = "action", act = "pick", value = target }, cx)
            local after = resize(state, cx, size[1], size[2])
            check(state.cursor == target and positions(after.main)[target] ~= nil,
                view .. ": resizing to " .. size[1] .. "x" .. size[2] .. " hid the selection")
        end
    end
    local state, cx = init(view, 160, 30, 12)
    local target = last_target(workspace.view(state, cx).main)
    workspace.event(state, { ev = "action", act = "pick", value = target }, cx)
    local changed = status(12)
    for i = 1, 20 do
        table.insert(changed.uncommittedChanges, 1, {
            cliId = "newf" .. i, filePath = "new-file-" .. i, changeType = "added",
        })
    end
    for i, commit in status(5).stacks[1].branches[1].commits do
        commit.cliId, commit.changeId = "newc" .. i, "newchange" .. i
        commit.commitId = "newsha" .. i
        for fi, file in commit.changes do file.cliId = "newcf" .. i .. ":" .. fi end
        table.insert(changed.stacks[1].branches[1].commits, 1, commit)
    end
    fixture = changed
    workspace.key(state, { name = "r" }, cx)
    check(state.cursor == target and positions(workspace.view(state, cx).main)[target] ~= nil,
        view .. ": inserting rows on refresh hid the surviving selection")
end
for _, view in { "branches", "stacks" } do
    for _, transition in { "resize", "refresh", "dock" } do
        local state, cx = init(view, 160, 30, 0)
        fixture = status(0)
        for i = 2, 12 do
            table.insert(fixture.uncommittedChanges, { cliId = "u" .. i, filePath = "work-" .. i, changeType = "modified" })
        end
        workspace.key(state, { name = "r" }, cx)
        local target = if view == "branches" then "f-u11" else "f-u4"
        check(positions(workspace.view(state, cx).main)[target] ~= nil, view .. ": file selection fixture is visible")
        workspace.event(state, { ev = "action", act = "pick", value = target }, cx)
        local after
        if transition == "resize" then
            after = resize(state, cx, 78, 24)
        elseif transition == "dock" then
            state.problem = string.rep("e", 70)
            after = workspace.view(state, cx)
        else
            local files = table.clone(fixture.uncommittedChanges)
            fixture = status(0)
            fixture.uncommittedChanges = files
            for i = 1, 20 do
                table.insert(files, 1, { cliId = "newu" .. i, filePath = "new-work-" .. i, changeType = "added" })
            end
            workspace.key(state, { name = "r" }, cx)
            after = workspace.view(state, cx)
        end
        local in_files = view ~= "branches"
        visit(after.main, function(node)
            if has_class(node, "bp-f") and has_class(node, "bp-sel") then in_files = true end
        end)
        check(state.cursor == target and positions(after.main)[target] ~= nil and in_files,
            view .. ": " .. transition .. " hid the selection in the tray, sidebar or file pane")
    end
end
do
    local state, cx = init("lanes", 78, 30, 1)
    workspace.event(state, { ev = "action", act = "pick", value = "c-c1" }, cx)
    local before = positions(workspace.view(state, cx).main)
    workspace.key(state, { name = "r" }, cx)
    local after = positions(workspace.view(state, cx).main)
    for key, path in before do
        check(after[key] == path, "an unchanged status refresh moved " .. key)
    end
end
for _, view in { "lanes", "graph", "stacks", "branches" } do
    for _, cols in { 78, 160 } do
        local state, cx = init(view, cols, 30, if view == "lanes" then 1 else 12)
        local before = positions(workspace.view(state, cx).main)
        for target, _ in before do
            workspace.event(state, { ev = "action", act = "pick", value = target }, cx)
            local after = positions(workspace.view(state, cx).main)
            for key, path in before do
                check(after[key] == path, view .. " " .. cols .. ": picking " .. target .. " moved " .. key)
            end
            workspace.event(state, { ev = "action", act = "open", value = target }, cx)
            local row
            for _, r in state.rows do if r.key == target then row = r end end
            check(state.diff ~= nil and state.diff.id == row.id, view .. ": double-click opened the wrong diff")
            state.diff = nil
        end
        for _, name in { "end", "home", "right", "down", "up", "left" } do
            workspace.key(state, { name = name }, cx)
            local visible = positions(workspace.view(state, cx).main)
            check(visible[state.cursor] ~= nil, view .. ": " .. name .. " hid the keyboard cursor")
        end
    end
end
for _, view in { "lanes", "graph" } do
    for _, cols in { 78, 109, 110, 160 } do
        for _, rows in { 18, 24, 30, 60 } do
            local state, cx = init(view, cols, rows, 7)
            for _, row in state.rows do
                if view == "graph" and (row.kind == "cfile" or row.kind == "branch") then continue end
                workspace.event(state, { ev = "action", act = "pick", value = row.key }, cx)
                local result = workspace.view(state, cx)
                local dock = 0
                visit(result.dock, function(node)
                    if node.k ~= "text" then return end
                    local text = ""
                    for _, span in node.p.spans do text ..= span.t end
                    for line in string.gmatch(text .. "\n", "(.-)\n") do
                        dock += math.max(1, math.ceil(width.cells(line) / (cols - 12))) * 17
                    end
                end)
                local split = result.main.c[1]
                local left, detail = height(split.c[1]), height(split.c[2])
                local drawn = if cols < 110 then left + detail else math.max(left, detail)
                local available = rows * 17 - 32 - dock
                check(drawn <= available, view .. " " .. cols .. "x" .. rows .. " " .. row.kind
                    .. ": needs " .. drawn .. "px, only " .. available .. "px available")
            end
        end
    end
end
for _, view in { "lanes", "graph", "stacks", "branches" } do
    local state, cx = init(view, 78, 60, 7)
    workspace.event(state, { ev = "action", act = "pick", value = "b-b1" }, cx)
    for _ = 1, 4 do
        workspace.key(state, { name = "v" }, cx)
        check(positions(workspace.view(state, cx).main)[state.cursor] ~= nil,
            state.view .. ": changing views hid the selected row")
    end
    state.view = "lanes"
    workspace.key(state, { name = "home" }, cx)
    workspace.event(state, { ev = "action", act = "pick", value = "c-c2" }, cx)
    fixture = status(7)
    fixture.uncommittedChanges = {}
    workspace.key(state, { name = "r" }, cx)
    check(positions(workspace.view(state, cx).main)["c-c2"] ~= nil,
        "refreshing after the viewport anchor disappeared hid the selected commit")
    fixture = status(0)
    workspace.key(state, { name = "r" }, cx)
    check(positions(workspace.view(state, cx).main)[state.cursor] ~= nil,
        "refreshing after the selection disappeared hid the replacement row")
end
do
    local state, cx = init("lanes", 78, 60, 7)
    workspace.event(state, { ev = "action", act = "pick", value = "c-c1" }, cx)
    local detail = workspace.view(state, cx).main.c[2].c[2] -- c[1] is the top bar
    local title, ids, body, files, hint = false, false, false, false, false
    visit(detail, function(node)
        if has_class(node, "bp-dh") then title = node.p.text == "Commit 1" end
        if node.k == "kv" then ids = #node.p.items == 3 end
        if has_class(node, "bp-body") then body = node.p.text == "line 1\nline 2\nline 3\n…" end
        if has_class(node, "bp-files") then
            files = #node.c == 7 and node.c[7].p.text == "+1 more"
        end
        if has_class(node, "bp-hint") then hint = string.find(node.p.text, "⏎ diff", 1, true) ~= nil end
    end)
    check(title and ids and body and files and hint, "a tall commit detail lost its ordinary contents")
    workspace.event(state, { ev = "action", act = "pick", value = "b-b1" }, cx)
    detail = workspace.view(state, cx).main.c[2].c[2]
    local count, commits = false, false
    visit(detail, function(node)
        if has_class(node, "bp-sub") then count = node.p.text == "Commits 7" end
        if has_class(node, "bp-files") then
            commits = #node.c == 7 and node.c[7].p.text == "+1 more"
        end
    end)
    check(count and commits, "a tall branch detail lost its commit summary")
end
do
    -- A diff stays bound to its row: keys under it never act on the hidden
    -- cursor, and it closes when a refresh removes the row it shows.
    local state, cx = init("lanes", 78, 40, 5)
    workspace.event(state, { ev = "action", act = "open", value = "c-c5" }, cx)
    check(state.diff ~= nil and state.diff.key == "c-c5", "the diff must open on the fifth commit")
    check(workspace.key(state, { name = "p", text = "p" }, cx) == false and state.sheet == nil,
        "a key under the diff started an action on the hidden cursor")
    fixture = status(3)
    workspace.key(state, { name = "r", text = "r" }, cx)
    workspace.view(state, cx)
    check(state.diff == nil, "the diff outlived the commit it showed")
end
do
    -- A long message weighs more than seven files, but the detail caps body
    -- lines at 4 and file lines at 7, so the seven-file commit is taller.
    -- Narrow panes stack that detail under the list; it has to fit in what
    -- the list left (pane minus the two-row top bar, the dock, and the 32px
    -- of pads that sit outside the list).
    local function one(id, message, nfiles)
        local files = {}
        for fi = 1, nfiles do
            table.insert(files, { cliId = id .. ":" .. fi, filePath = "f-" .. id .. "-" .. fi, changeType = "modified" })
        end
        return { cliId = id, commitId = "sha" .. id, changeId = "change" .. id, message = message, changes = files }
    end
    local commits = {
        one("x", "title x\n" .. string.rep("line\n", 30), 1),
        one("y", "seven files\n\none body line", 7),
    }
    for i = 1, 20 do
        table.insert(commits, one("f" .. i, "filler " .. i, 1))
    end
    local fx = {
        uncommittedChanges = {},
        stacks = { { cliId = "s1", assignedChanges = {}, branches = {
            { cliId = "b1", name = "feature", branchStatus = "completelyUnpushed", commits = commits },
        } } },
    }
    local overflow = nil
    for _, view in { "lanes", "graph" } do
        local state, cx = init(view, 78, 50, 1)
        fixture = fx
        workspace.key(state, { name = "r" }, cx)
        for _, row in state.rows do
            if view == "graph" and (row.kind == "cfile" or row.kind == "branch") then continue end
            workspace.event(state, { ev = "action", act = "pick", value = row.key }, cx)
            local result = workspace.view(state, cx)
            local dock = 0
            visit(result.dock, function(node)
                if node.k ~= "text" then return end
                local text = ""
                for _, span in node.p.spans do text ..= span.t end
                for line in string.gmatch(text .. "\n", "(.-)\n") do
                    dock += math.max(1, math.ceil(width.cells(line) / (cx.cols - 12))) * 17
                end
            end)
            local split = result.main.c[2]
            local left, detail = height(split.c[1]), height(split.c[2])
            local room = cx.rows * 17 - 56 - 32 - dock
            if left + detail > room and overflow == nil then
                overflow = view .. " " .. row.key .. ": detail " .. detail .. "px exceeds the " .. (room - left) .. "px the list left"
            end
        end
    end
    check(overflow == nil, overflow or "capped detail fits under the list")
end
for i = 1, math.min(12, #failures) do print(failures[i]) end
for i = math.max(13, #failures - 5), #failures do print(failures[i]) end
assert(#failures == 0, #failures .. " of " .. cases .. " layout assertions failed")
print("workspace layout: " .. cases .. " assertions passed")
'''
with tempfile.TemporaryDirectory(prefix=".layout-check-", dir=plugin.parent) as scratch:
    runner = Path(scratch) / "check.luau"
    runner.write_text(bootstrap + checks)
    subprocess.run(["luau", str(runner)], check=True)
