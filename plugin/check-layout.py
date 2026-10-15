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
local calls = {}
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
function ui.code(text) return { k = "code", p = { text = text } } end
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
    process = { run = function(argv, _, done)
        table.insert(calls, table.clone(argv))
        done({ stdout = "{}", stderr = "", status = 0 })
    end },
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
local function row_key(state, handle)
    for _, row in state.rows do if row.key == handle then return handle end end
    local kind, id = string.match(handle, "^([^%-]+)%-(.+)$")
    for _, row in state.rows do
        if kind == "b" and row.kind == "branch" then
            for _, stack in state.model.stacks do
                for _, branch in stack.branches do
                    if branch.id == id and row.label == modules.gb.label(branch) then return row.key end
                end
            end
        elseif row.id == id and ((kind == "f" and row.kind == "file" and row.lane == 1)
            or (kind == "a" and row.kind == "file" and row.lane > 1)
            or (kind == "c" and row.kind == "commit") or (kind == "cf" and row.kind == "cfile")) then
            return row.key
        end
    end
    return handle
end
local function action_value(main, prefix, target)
    local value
    visit(main, function(node)
        local click = (node.p or {}).actions and node.p.actions.click
        if type(click) == "string" and string.sub(click, 1, #prefix) == prefix
            and (target == nil or string.sub(click, -#target) == target) then
            value = string.match(click, "^[^=]+=(.*)$")
        end
    end)
    return value
end
local function node_by_key(node, key, path)
    path = path or "main"
    if (node.p or {}).key == key then return node, path end
    for i, child in node.c or {} do
        local found, id = node_by_key(child, key, path .. "." .. tostring((child.p or {}).key or i))
        if found then return found, id end
    end
    return nil, nil
end
local function click_text(state, cx, text, column)
    local main = workspace.view(state, cx).main
    local scope = if column then node_by_key(main, column) else main
    local value
    if scope then
        visit(scope, function(node)
            local p = node.p or {}
            if type(p.text) == "string" and string.sub(p.text, 1, #text) == text and p.actions and p.actions.click then
                value = p.actions.click
            end
        end)
    end
    if value == nil then return false end
    local act, payload = string.match(value, "^([^=]+)=(.*)$")
    workspace.event(state, { ev = "action", act = act, value = payload }, cx)
    return true
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
    if has_class(node, "bp-top") then return if has_class(node, "bp-top2") then 58 else 29 end
    if has_class(node, "bp-colh") then return 23 end
    if has_class(node, "bp-card") then return 21 end
    if has_class(node, "bp-bcol") then return sum + 19 + math.max(0, #kids - 1) * 2 end
    if node.tag == "button" then return 21 end
    if has_class(node, "bp-lanes") then return sum + math.max(0, #kids - 1) * 6 end
    if has_class(node, "bp-lane") then return sum + 9 end
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
local function dock_height(result, cx)
    local dock = 0
    visit(result.dock, function(node)
        if node.k ~= "text" then return end
        local text = ""
        for _, span in node.p.spans do text ..= span.t end
        for line in string.gmatch(text .. "\n", "(.-)\n") do
            dock += math.max(1, math.ceil(width.cells(line) / (cx.cols - 12))) * 17
        end
    end)
    return dock
end
local function command_count(op)
    local count = 0
    for _, argv in calls do if argv[4] == op then count += 1 end end
    return count
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
    check(positions(workspace.view(state, cx).main)[row_key(state, "c-c5")] ~= nil,
        "the fifth commit must be visible before the dock notice")
    workspace.event(state, { ev = "action", act = "pick", value = row_key(state, "c-c5") }, cx)
    check(positions(workspace.view(state, cx).main)[row_key(state, "c-c5")] ~= nil,
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
    check(state.cursor == row_key(state, "c-c5") and positions(after.main)[row_key(state, "c-c5")] ~= nil,
        "the Nothing to commit notice hid the selected fifth commit")
    workspace.key(state, { name = "enter" }, cx)
    check(state.diff ~= nil and state.diff.id == "change5",
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
    check(positions(workspace.view(state, cx).main)[row_key(state, "c-c4")] ~= nil,
        "the reported fourth commit must be visible before the click")
    workspace.event(state, { ev = "action", act = "pick", value = row_key(state, "c-c4") }, cx)
    check(positions(workspace.view(state, cx).main)[row_key(state, "c-c4")] ~= nil,
        "pointer selection hid the reported fourth commit")
    check(positions(resize(state, cx, 78, 24).main)[row_key(state, "c-c4")] ~= nil,
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
        local target = if view == "branches" then row_key(state, "f-u11") else row_key(state, "f-u4")
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
    workspace.event(state, { ev = "action", act = "pick", value = row_key(state, "c-c1") }, cx)
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
            local label = if row.kind == "commit" then row.id .. " " .. row.label else row.label
            check(state.diff ~= nil and state.diff.label == label, view .. ": double-click opened the wrong diff")
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
                local dock = dock_height(result, cx)
                local split = result.main.c[2]
                local left, detail = height(split.c[1]), height(split.c[2])
                local drawn = if cols < 110 then left + detail else math.max(left, detail)
                local available = rows * 17 - height(result.main.c[1]) - 32 - dock
                check(drawn <= available, view .. " " .. cols .. "x" .. rows .. " " .. row.kind
                    .. ": needs " .. drawn .. "px, only " .. available .. "px available")
            end
        end
    end
end
for _, view in { "lanes", "graph", "stacks", "branches" } do
    local state, cx = init(view, 78, 60, 7)
    workspace.event(state, { ev = "action", act = "pick", value = row_key(state, "b-b1") }, cx)
    for _ = 1, 4 do
        workspace.key(state, { name = "v" }, cx)
        check(positions(workspace.view(state, cx).main)[state.cursor] ~= nil,
            state.view .. ": changing views hid the selected row")
    end
    state.view = "lanes"
    workspace.key(state, { name = "home" }, cx)
    workspace.event(state, { ev = "action", act = "pick", value = row_key(state, "c-c2") }, cx)
    fixture = status(7)
    fixture.uncommittedChanges = {}
    workspace.key(state, { name = "r" }, cx)
    check(positions(workspace.view(state, cx).main)[row_key(state, "c-c2")] ~= nil,
        "refreshing after the viewport anchor disappeared hid the selected commit")
    fixture = status(0)
    workspace.key(state, { name = "r" }, cx)
    check(positions(workspace.view(state, cx).main)[state.cursor] ~= nil,
        "refreshing after the selection disappeared hid the replacement row")
end
do
    local state, cx = init("lanes", 78, 60, 7)
    workspace.event(state, { ev = "action", act = "pick", value = row_key(state, "c-c1") }, cx)
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
    workspace.event(state, { ev = "action", act = "pick", value = row_key(state, "b-b1") }, cx)
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
    workspace.event(state, { ev = "action", act = "open", value = row_key(state, "c-c5") }, cx)
    check(state.diff ~= nil and state.diff.id == "change5", "the diff must open on the fifth commit")
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
            local dock = dock_height(result, cx)
            local split = result.main.c[2]
            local left, detail = height(split.c[1]), height(split.c[2])
            local room = cx.rows * 17 - 58 - 32 - dock
            if left + detail > room and overflow == nil then
                overflow = view .. " " .. row.key .. ": detail " .. detail .. "px exceeds the " .. (room - left) .. "px the list left"
            end
        end
    end
    check(overflow == nil, overflow or "capped detail fits under the list")
end
do
    local state, cx = init("board", 78, 40, 2)
    fixture = status(2)
    fixture.uncommittedChanges = { { cliId = "u2", filePath = "other.txt", changeType = "modified" } }
    workspace.key(state, { name = "r" }, cx)
    local cursor = state.cursor
    for _, id in { "main.2.cols.L1.f-u1", "malformed", false } do
        for _, act in { "Commit…", "Move…", "Absorb", "Discard…", "Discard branch…", "Push", "Create PR",
            "Rename…", "Reword…", "New stacked branch…", "Unapply", "Land", "Uncommit" } do
            local before = #calls
            workspace.event(state, { ev = "action", act = act, id = if id then id else nil }, cx)
            check(state.sheet == nil and #calls == before and state.cursor == cursor,
                act .. ": a missing menu target fell back to the selection")
        end
    end
    workspace.event(state, { ev = "action", act = "Discard…", id = "main.2.cols.L1." .. row_key(state, "f-u2") }, cx)
    check(state.sheet ~= nil and state.sheet.command == "but discard zz:other.txt" and state.sheet.danger,
        "a live menu target must keep its guarded file action")
    workspace.key(state, { name = "escape" }, cx)
    workspace.event(state, { ev = "action", act = "do", value = "discard" }, cx)
    check(state.sheet ~= nil and state.sheet.command == "but discard zz:other.txt",
        "the toolbar must still act on the selection")
end
for _, route in { "toolbar", "palette" } do
    local state, cx = init("board", 78, 40, 1)
    local before = command_count("redo")
    workspace.event(state, { ev = "action", act = if route == "toolbar" then "do" else "redo", value = "redo" }, cx)
    check(state.sheet ~= nil and state.sheet.danger, route .. ": redo must be dangerous")
    workspace.key(state, { name = "enter" }, cx)
    check(state.sheet ~= nil and command_count("redo") == before, route .. ": Enter ran redo")
    workspace.key(state, { name = "y", text = "y" }, cx)
    check(state.sheet == nil and command_count("redo") == before + 1, route .. ": y did not run redo")
end
for _, kind in { "commit", "cfile" } do
    for _, timing in { "prompt", "confirm", "absent" } do
        local state, cx = init("board", 78, 40, 2)
        local key = if kind == "commit" then row_key(state, "c-c1") else row_key(state, "cf-1:1")
        workspace.event(state, { ev = "action", act = "pick", value = key }, cx)
        workspace.event(state, { ev = "action", act = "drop", value = action_value(workspace.view(state, cx).main, "drop=newmove:") }, cx)
        check(state.sheet ~= nil and state.sheet.kind == "prompt", kind .. ": new move must ask for a name")
        workspace.key(state, { name = "paste", text = "destination" }, cx)
        local function apply_destination()
            fixture = status(2)
            table.insert(fixture.stacks, { cliId = "s2", assignedChanges = {}, branches = {
                { cliId = "above", name = "pushed-above", branchStatus = "nothingToPush", commits = {} },
                { cliId = "dest", name = "destination", branchStatus = "completelyUnpushed", commits = {} },
            } })
        end
        if timing == "prompt" then apply_destination() end
        workspace.key(state, { name = "enter" }, cx)
        local before = command_count("move")
        if timing == "confirm" then
            check(state.sheet ~= nil and not state.sheet.danger, kind .. ": a new destination should allow Enter")
            apply_destination()
            workspace.key(state, { name = "enter" }, cx)
        end
        if timing ~= "absent" then
            check(state.sheet ~= nil and state.sheet.danger and command_count("move") == before,
                kind .. ": a destination applied during the " .. timing .. " escaped the danger guard")
            local warned = false
            for _, line in (if state.sheet then state.sheet.body or {} else {}) do
                if string.find(line, "pushed-above", 1, true) then warned = true end
            end
            check(warned, kind .. ": the destination's pushed parent was omitted")
            workspace.key(state, { name = "enter" }, cx)
            check(command_count("move") == before, kind .. ": Enter accepted a dangerous move")
            workspace.key(state, { name = "y", text = "y" }, cx)
        else
            check(state.sheet ~= nil and not state.sheet.danger, kind .. ": an absent destination was treated as pushed")
            workspace.key(state, { name = "enter" }, cx)
        end
        check(command_count("move") == before + 1, kind .. ": confirmed move did not run")
        for _, argv in calls do
            if argv[4] == "move" then
                check(argv[5] == "change1" or argv[5] == "change1:file-1", "move lost its stable source")
            end
        end
    end
end
for _, kind in { "file", "assigned", "commit", "cfile", "branch" } do
    for _, transition in { "replace", "renumber" } do
        local state, cx = init("lanes", 78, 40, 2)
        local function data()
            local result = status(2)
            result.stacks[1].assignedChanges = { { cliId = "a1", filePath = "assigned.txt", changeType = "modified" } }
            return result
        end
        fixture = data()
        workspace.key(state, { name = "r" }, cx)
        local targets = {
            file = { row_key(state, "f-u1"), "zz:work.txt" }, assigned = { row_key(state, "a-a1"), "zz:assigned.txt" },
            commit = { row_key(state, "c-c1"), "change1" }, cfile = { row_key(state, "cf-1:1"), "change1:file-1" }, branch = { row_key(state, "b-b1"), "feature" },
        }
        local target = targets[kind]
        workspace.event(state, { ev = "action", act = "open", value = target[1] }, cx)
        check(calls[#calls][4] == "diff" and calls[#calls][5] == target[2], kind .. ": diff used a snapshot selector")
        fixture = data()
        local record = if kind == "file" then fixture.uncommittedChanges[1]
            elseif kind == "assigned" then fixture.stacks[1].assignedChanges[1]
            elseif kind == "branch" then fixture.stacks[1].branches[1]
            elseif kind == "commit" then fixture.stacks[1].branches[1].commits[1]
            else fixture.stacks[1].branches[1].commits[1].changes[1]
        if transition == "renumber" then record.cliId = "new-cli-id"
        elseif kind == "branch" then record.name = "replacement"
        elseif kind == "commit" then record.changeId = "replacement"
        else record.filePath = "replacement.txt" end
        workspace.key(state, { name = "r" }, cx)
        workspace.view(state, cx)
        if transition == "replace" then
            check(state.diff == nil, kind .. ": the diff survived CLI id reuse")
        else
            check(state.diff ~= nil, kind .. ": CLI id churn closed a surviving diff")
            workspace.key(state, { name = "r" }, cx)
            local last
            for _, argv in calls do if argv[4] == "diff" then last = argv[5] end end
            check(last == target[2], kind .. ": reload lost the stable diff selector")
        end
    end
end
do
    local state, cx = init("lanes", 78, 40, 1)
    fixture = status(1)
    fixture.stacks[1].branches[1].name = ""
    fixture.uncommittedChanges[1].filePath = "ambiguous:path"
    fixture.stacks[1].branches[1].commits[1].changes[1].filePath = "ambiguous:path"
    fixture.stacks[1].branches[1].commits[1].changeId = ""
    workspace.key(state, { name = "r" }, cx)
    for _, key in { row_key(state, "b-b1"), row_key(state, "f-u1"), row_key(state, "c-c1"), row_key(state, "cf-1:1") } do
        local before = command_count("diff")
        workspace.event(state, { ev = "action", act = "open", value = key }, cx)
        check(state.diff == nil and command_count("diff") == before, "an unsafe diff target reached but: " .. key)
    end
end
local function board_status()
    local result = status(2)
    result.stacks = {}
    for si = 1, 4 do
        local stack = status(2).stacks[1]
        stack.cliId = "s" .. si
        stack.branches[1].cliId, stack.branches[1].name = "b" .. si, "branch-" .. si
        for ci, commit in stack.branches[1].commits do
            commit.cliId, commit.changeId = "c" .. si .. "-" .. ci, "change" .. si .. "-" .. ci
            for fi, file in commit.changes do file.cliId = si .. "-" .. ci .. ":" .. fi end
        end
        table.insert(result.stacks, stack)
    end
    return result
end
local function check_board(state, cx, browsing)
    local result = workspace.view(state, cx)
    local board = result.main.c[2]
    local columns = board.c[2].c
    local per_line = math.max(1, cx.cols // 34)
    local drawn = 39
    for i = 1, #columns, per_line do
        local line = 0
        for j = i, math.min(#columns, i + per_line - 1) do line = math.max(line, height(columns[j])) end
        drawn += line + (if i == 1 then 0 else 8)
    end
    local available = cx.rows * 17 - height(result.main.c[1]) - 32 - dock_height(result, cx)
    check(drawn <= available, "board " .. cx.cols .. "x" .. cx.rows .. ": needs " .. drawn .. "px, only " .. available .. "px available")
    if not browsing then
        check(positions(result.main)[state.cursor] ~= nil, "board: keyboard cursor is outside the visible columns/cards")
    end
end
for _, cols in { 35, 67, 78, 109, 160 } do
    for _, rows in { 24, 30, 60 } do
        local state, cx = init("board", cols, rows, 2)
        fixture = board_status()
        workspace.key(state, { name = "r" }, cx)
        for _, key in { "end", "home", "right", "right", "right", "right", "left" } do
            workspace.key(state, { name = key }, cx)
            check_board(state, cx)
        end
        for _ = 1, #state.rows do
            workspace.key(state, { name = "down" }, cx)
            check_board(state, cx)
        end
        for _ = 1, #state.rows do
            workspace.key(state, { name = "up" }, cx)
            check_board(state, cx)
        end
    end
end
do
    local state, cx = init("board", 78, 33, 0)
    fixture = status(0)
    fixture.uncommittedChanges = {}
    for i = 1, 10 do
        table.insert(fixture.uncommittedChanges, { cliId = "u" .. i, filePath = "work-" .. i, changeType = "modified" })
    end
    workspace.key(state, { name = "r" }, cx)
    workspace.view(state, cx)
    for _ = 1, 4 do workspace.key(state, { name = "down" }, cx) end
    local before = positions(workspace.view(state, cx).main)
    check(before[row_key(state, "f-u4")] ~= nil and before[row_key(state, "f-u5")] ~= nil and before[row_key(state, "f-u6")] ~= nil
        and before[row_key(state, "f-u3")] == nil and before[row_key(state, "f-u7")] == nil,
        "board: the five-line window must show files four through six before the click")
    workspace.event(state, { ev = "action", act = "pick", value = row_key(state, "f-u6") }, cx)
    local after = positions(workspace.view(state, cx).main)
    for key, path in before do check(after[key] == path, "board: pointer selection moved " .. key) end
    workspace.event(state, { ev = "action", act = "open", value = row_key(state, "f-u6") }, cx)
    check(state.diff ~= nil and state.diff.id == "zz:work-6", "board: the second click opened another file")
end
for _, spec in {
    { "f-u1", "zz:work.txt" }, { "a-a1", "zz:assigned.txt" }, { "b-b1", "feature" },
    { "c-c1", "change1" }, { "cf-1:1", "change1:file-1" },
} do
    local state, cx = init("board", 78, 60, 2)
    fixture = status(2)
    table.insert(fixture.uncommittedChanges, { cliId = "other", filePath = "other.txt", changeType = "modified" })
    fixture.stacks[1].assignedChanges = { { cliId = "a1", filePath = "assigned.txt", changeType = "modified" } }
    workspace.key(state, { name = "r" }, cx)
    local target = row_key(state, spec[1])
    workspace.event(state, { ev = "action", act = "pick", value = target }, cx)
    workspace.event(state, { ev = "change", name = "mark", value = "other.txt", checked = true }, cx)
    local _, id = node_by_key(workspace.view(state, cx).main, target)
    check(id ~= nil, spec[1] .. ": the discard card must be visible")
    workspace.event(state, { ev = "action", act = if spec[1] == "b-b1" then "Discard branch…" else "Discard…", id = id }, cx)
    check(state.sheet ~= nil and state.sheet.command == "but discard " .. spec[2] and state.sheet.danger,
        spec[1] .. ": menu discard targeted marks instead of its card")
    local before = command_count("discard")
    workspace.key(state, { name = "enter" }, cx)
    check(command_count("discard") == before, "Enter ran a destructive menu discard")
    workspace.key(state, { name = "y", text = "y" }, cx)
    local last
    for _, argv in calls do if argv[4] == "discard" then last = argv[5] end end
    check(command_count("discard") == before + 1 and last == spec[2], "confirmed menu discard changed the wrong entity")
    workspace.event(state, { ev = "change", name = "mark", value = "other.txt", checked = true }, cx)
    workspace.event(state, { ev = "action", act = "do", value = "discard" }, cx)
    check(state.sheet ~= nil and state.sheet.command == "but discard zz:other.txt",
        spec[1] .. ": toolbar discard stopped honoring marks")
end
for _, kind in { "file", "assigned", "branch", "anonymous", "commit", "cfile", "cfile-commit" } do
    local state, cx = init("board", 160, 60, 2)
    local function data()
        local result = status(2)
        result.stacks[1].assignedChanges = { { cliId = "a1", filePath = "assigned.txt", changeType = "modified" } }
        if kind == "anonymous" then result.stacks[1].branches[1].name = "" end
        return result
    end
    fixture = data()
    workspace.key(state, { name = "r" }, cx)
    local handles = { file = "f-u1", assigned = "a-a1", branch = "b-b1", anonymous = "b-b1",
        commit = "c-c1", cfile = "cf-1:1", ["cfile-commit"] = "cf-1:1" }
    local target = row_key(state, handles[kind])
    workspace.event(state, { ev = "action", act = "pick", value = target }, cx)
    local card, id = node_by_key(workspace.view(state, cx).main, target)
    check(card ~= nil and id ~= nil, kind .. ": stale-event fixture must render its card")
    local pick = card and string.match(card.p.actions.click, "^[^=]+=(.*)$")
    local open = card and string.match(card.p.actions.dblclick, "^[^=]+=(.*)$")
    fixture = data()
    if kind == "file" then fixture.uncommittedChanges[1].filePath = "replacement.txt"
    elseif kind == "assigned" then fixture.stacks[1].assignedChanges[1].filePath = "replacement.txt"
    elseif kind == "branch" then fixture.stacks[1].branches[1].name = "replacement"
    elseif kind == "cfile" then fixture.stacks[1].branches[1].commits[1].changes[1].filePath = "replacement.txt"
    else fixture.stacks[1].branches[1].commits[1].changeId = "replacement" end
    workspace.key(state, { name = "r" }, cx)
    local cursor, before = state.cursor, #calls
    workspace.event(state, { ev = "action", act = if kind == "anonymous" then "Unapply" else "Discard…", id = id }, cx)
    check(state.sheet == nil and #calls == before and state.cursor == cursor,
        kind .. ": a reused CLI id retargeted a menu action")
    if state.sheet then workspace.key(state, { name = "escape" }, cx) end
    cursor = state.cursor
    workspace.event(state, { ev = "action", act = "pick", value = pick }, cx)
    workspace.event(state, { ev = "action", act = "open", value = open }, cx)
    check(state.cursor == cursor and state.diff == nil, kind .. ": stale pointer events selected or opened a replacement")
end
for _, change in { "destination-commit", "destination-branch", "source", "marks" } do
    local state, cx = init("board", 160, 60, 2)
    fixture = board_status()
    table.insert(fixture.uncommittedChanges, { cliId = "other", filePath = "other.txt", changeType = "modified" })
    workspace.key(state, { name = "r" }, cx)
    workspace.event(state, { ev = "action", act = "pick", value = row_key(state, "c-c1-1") }, cx)
    if change == "marks" then
        workspace.event(state, { ev = "change", name = "mark", value = "work.txt", checked = true }, cx)
    end
    local op = if change == "destination-branch" then "move" elseif change == "source" then "newmove"
        elseif change == "marks" then "newcommit" else "squash"
    local target = if change == "destination-branch" then row_key(state, "b-b2")
        elseif change == "destination-commit" then row_key(state, "c-c2-1") else nil
    local value = action_value(workspace.view(state, cx).main, "drop=" .. op .. ":", target)
    check(value ~= nil, change .. ": the drop must be rendered before refresh")
    if change == "marks" then
        workspace.event(state, { ev = "change", name = "mark", value = "work.txt", checked = false }, cx)
        workspace.event(state, { ev = "change", name = "mark", value = "other.txt", checked = true }, cx)
    else
        fixture = board_status()
        if change == "destination-branch" then fixture.stacks[2].branches[1].name = "replacement"
        else
            local stack = if change == "source" then 1 else 2
            fixture.stacks[stack].branches[1].commits[1].changeId = "replacement"
        end
        workspace.key(state, { name = "r" }, cx)
    end
    local before = #calls
    workspace.event(state, { ev = "action", act = "drop", value = value }, cx)
    check(state.sheet == nil and #calls == before, change .. ": a stale drop retargeted the operation")
end
do
    local state, cx = init("board", 160, 60, 0)
    fixture = status(0)
    fixture.uncommittedChanges = {}
    for i, path in { "a/b", "a_b", "a.b", "a:b", "a\nb" } do
        table.insert(fixture.uncommittedChanges, { cliId = "u" .. i, filePath = path, changeType = "modified" })
    end
    workspace.key(state, { name = "r" }, cx)
    local seen = {}
    for _, row in state.rows do
        check(not seen[row.key], "different file paths produced the same event key")
        seen[row.key] = true
        workspace.event(state, { ev = "action", act = "pick", value = row.key }, cx)
        check(state.cursor == row.key, "an encoded file key failed to select its row")
    end
end
for _, held in { "commit", "cfile", "file", "marks" } do
    local state, cx = init("board", 78, 24, 2)
    fixture = board_status()
    workspace.key(state, { name = "r" }, cx)
    local source = row_key(state, if held == "commit" then "c-c1-1" elseif held == "cfile" then "cf-1-1:1" else "f-u1")
    workspace.event(state, { ev = "action", act = "pick", value = source }, cx)
    if held == "marks" then workspace.event(state, { ev = "change", name = "mark", value = "work.txt", checked = true }, cx) end
    local new, seen = false, {}
    for _ = 1, 12 do
        local main = workspace.view(state, cx).main
        for key in positions(main) do seen[key] = true end
        check_board(state, cx, true)
        check(state.cursor == source and (held ~= "marks" or state.marked["work.txt"]), "horizontal browsing changed the held source")
        if node_by_key(main, "new") then new = true; break end
        if not click_text(state, cx, "→") then break end
    end
    check(new and seen[row_key(state, "b-b4")] and seen[row_key(state, "c-c4-1")], held .. ": later destinations are unreachable")
    local viewport = state.viewport
    fixture = board_status()
    fixture.upstreamState = { behind = 1 }
    workspace.key(state, { name = "r" }, cx)
    check(state.cursor == source and state.viewport == viewport, "refresh changed the held source or browsing viewport")
    resize(state, cx, 78, 30)
    check(state.cursor == source and state.viewport == viewport, "resize reset the independent viewport")
    local op = if held == "commit" or held == "cfile" then "newmove" else "newcommit"
    local value = action_value(workspace.view(state, cx).main, "drop=" .. op .. ":")
    check(value ~= nil, "the new-branch drop disappeared while browsing")
    workspace.event(state, { ev = "action", act = "drop", value = value }, cx)
    check(state.sheet ~= nil and state.sheet.kind == "prompt", "the browsed new-branch target did not open its action")
    if held == "commit" or held == "cfile" then
        workspace.key(state, { name = "paste", text = "created" }, cx)
        workspace.key(state, { name = "enter" }, cx)
        local ref = if held == "commit" then "change1-1" else "change1-1:file-1"
        check(state.sheet ~= nil and state.sheet.command == "but move " .. ref .. " -b created", "the browsed move lost its original source")
    end
    workspace.key(state, { name = "escape" }, cx)
    local uncommitted = false
    for _ = 1, 12 do
        if node_by_key(workspace.view(state, cx).main, "L1") then uncommitted = true; break end
        if not click_text(state, cx, "←") then break end
    end
    check(uncommitted and state.cursor == source, "browsing left could not return to Uncommitted with its source")
end
do
    local state, cx = init("board", 78, 24, 20)
    local source, target = row_key(state, "c-c1"), row_key(state, "c-c20")
    workspace.event(state, { ev = "action", act = "pick", value = source }, cx)
    local reached = false
    for _ = 1, #state.rows do
        local main = workspace.view(state, cx).main
        check_board(state, cx, true)
        check(state.cursor == source, "vertical browsing changed the held commit")
        if positions(main)[target] then reached = true; break end
        if not click_text(state, cx, "↓ ", "L2") then break end
    end
    check(reached, "an offscreen commit destination cannot be reached independently")
    local value = action_value(workspace.view(state, cx).main, "drop=squash:", target)
    check(value ~= nil, "the offscreen commit has no squash drop")
    workspace.event(state, { ev = "action", act = "drop", value = value }, cx)
    check(state.sheet ~= nil and state.sheet.command == "but squash change1 -t change20 --use-target-message",
        "vertical browsing changed the squash source or destination")
    workspace.key(state, { name = "enter" }, cx)
    local last
    for _, argv in calls do if argv[4] == "squash" then last = argv end end
    check(last ~= nil and last[5] == "change1" and last[7] == "change20", "the browsed squash did not execute on its original entities")
    local returned = false
    for _ = 1, #state.rows do
        if positions(workspace.view(state, cx).main)[source] then returned = true; break end
        if not click_text(state, cx, "↑ ", "L2") then break end
    end
    check(returned and state.cursor == source, "browsing up lost the held commit")
end
for _, rows in { 23, 24 } do
    for _, held in { "commit", "cfile", "file", "marks" } do
        local state, cx = init("board", 35, rows, 6)
        if held == "commit" or held == "cfile" then
            workspace.key(state, { name = "right" }, cx)
            workspace.key(state, { name = "down" }, cx)
            if held == "cfile" then workspace.key(state, { name = "down" }, cx) end
        else
            if held == "marks" then
                workspace.event(state, { ev = "change", name = "mark", value = "work.txt", checked = true }, cx)
            end
            check(click_text(state, cx, "→"), "short board: the stack must be reachable")
        end
        local source, target = state.cursor, row_key(state, "c-c6")
        local column = node_by_key(workspace.view(state, cx).main, "L2")
        check(column ~= nil and #column.c - 1 == rows - 22, "short board fixture must leave one or two body lines")
        local reached = false
        for _ = 1, #state.rows do
            local main = workspace.view(state, cx).main
            check_board(state, cx, true)
            check(state.cursor == source and (held ~= "marks" or state.marked["work.txt"]),
                "short board: downward browsing changed the held changes")
            if positions(main)[target] then reached = true; break end
            if not click_text(state, cx, "↓", "L2") then break end
        end
        check(reached, "short board: hidden destinations have no downward browsing control")
        local op = if held == "commit" or held == "cfile" then "squash" else "amend"
        local value = action_value(workspace.view(state, cx).main, "drop=" .. op .. ":", target)
        check(value ~= nil, "short board: the destination must retain its drop action")
        workspace.event(state, { ev = "action", act = "drop", value = value }, cx)
        local expected = if held == "commit" then "but squash change1 -t change6 --use-target-message"
            elseif held == "cfile" then "but squash change1:file-1 -t change6"
            else "but amend -t change6 zz:work.txt"
        check(state.sheet ~= nil and state.sheet.command == expected, "short board: browsing retargeted the drop")
        workspace.key(state, { name = "escape" }, cx)
        local returned = false
        for _ = 1, #state.rows do
            local main = workspace.view(state, cx).main
            check_board(state, cx, true)
            check(state.cursor == source, "short board: upward browsing changed the held changes")
            if positions(main)[row_key(state, "b-b1")] then returned = true; break end
            if not click_text(state, cx, "↑", "L2") then break end
        end
        check(returned, "short board: interior windows have no upward browsing control")
    end
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
