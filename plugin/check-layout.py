import json
from pathlib import Path
import subprocess
import tempfile


plugin = Path(__file__).parent
sources = {name: (plugin / f"{name}.luau").read_text() for name in ("gb", "width", "drag", "host")}
bootstrap = "local sources = {\n" + "\n".join(
    f"[{json.dumps(name)}] = {json.dumps(source, ensure_ascii=False)},"
    for name, source in sources.items()
) + "\n}\n"
checks = r'''
local blocks, modules, fixture = {}, {}, nil
local calls = {}
local pending_status, timers, replies, forced, hold_op, hold_done = nil, {}, nil, nil, nil, nil
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
function ui.progress() return { k = "progress" } end
local tern = {
    ui = ui,
    block = { define = function(name, definition) blocks[name] = definition end },
    getenv = function() return nil end,
    plugin = { data = "/nonexistent" },
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
        if pending_status and argv[4] == "status" then
            table.insert(pending_status, done)
            return
        end
        if hold_op and argv[4] == hold_op then
            table.insert(hold_done, done)
            return
        end
        if replies then
            local key = if argv[4] == "pull" and argv[5] == "--check" then "check" else argv[4]
            forced = replies[key]
        end
        done({ stdout = "{}", stderr = "", status = 0 })
    end },
    json = { decode = function() return if forced ~= nil then forced else fixture end, encode = function(data) return tostring(data) end },
    timer = function(_, done)
        table.insert(timers, done)
        return { cancel = function() end }
    end,
    kv = { get = function() return "lanes" end, set = function() end },
}
local env = setmetatable({ tern = tern }, { __index = getfenv() })
env.require = function(path) return modules[string.sub(path, 3)] or {} end
for _, name in { "gb", "width", "drag", "host" } do
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
local function controls_height(node, cols, gap)
    local rows, used = 1, 0
    for _, child in node.c or {} do
        local cells = width.cells((child.p or {}).text or "") + 3
        if used > 0 and used + cells > cols then rows += 1; used = 0 end
        used += cells
    end
    return rows * 21 + (rows - 1) * gap
end
local function height(node, cols)
    local kids, sum = node.c or {}, 0
    for _, child in kids do sum += height(child, cols) end
    if node.k == "kv" then return #(node.p.items) * 17 + 6 end
    if has_class(node, "bp-tabs") or has_class(node, "bp-stats") then return controls_height(node, cols, 8) end
    if has_class(node, "bp-top") then return if has_class(node, "bp-top2") then sum + 16 else 29 end
    if has_class(node, "bp-tools") then return controls_height(node, cols, 2) + 18 end
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
                local left, detail = height(split.c[1], cx.cols), height(split.c[2], cx.cols)
                local drawn = if cols < 110 then left + detail else math.max(left, detail)
                local available = rows * 17 - height(result.main.c[1], cx.cols) - 32 - dock
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
    -- A reused cliId must follow the changeId, and but diff keeps the stable selector.
    local state, cx = init("lanes", 78, 40, 5)
    workspace.event(state, { ev = "action", act = "open", value = row_key(state, "c-c5") }, cx)
    check(state.diff ~= nil and state.diff.id == "change5" and state.diff.bind == "c\0change5",
        "the diff must bind to the commit changeId")
    local swapped = status(5)
    for _, c in swapped.stacks[1].branches[1].commits do
        if c.changeId == "change5" then c.cliId = "c9"
        elseif c.changeId == "change1" then c.cliId = "c5" end
    end
    fixture = swapped
    calls = {}
    workspace.key(state, { name = "r", text = "r" }, cx)
    local shown = workspace.view(state, cx)
    local followed, stolen = false, false
    visit(shown.main, function(node)
        local text = (node.p or {}).text
        if text == "c9 Commit 5" then followed = true end
        if text == "c5 Commit 1" then stolen = true end
    end)
    local diff_ids = {}
    for _, argv in calls do
        for j, a in argv do
            if a == "diff" then table.insert(diff_ids, argv[j + 1]) end
        end
    end
    check(state.diff ~= nil and state.diff.bind == "c\0change5" and state.diff.id == "change5" and followed and not stolen,
        "a reused cliId retargeted the diff")
    check(#diff_ids > 0 and diff_ids[#diff_ids] == "change5",
        "reload used a snapshot cliId (" .. table.concat(diff_ids, ",") .. ")")
end
do
    -- A plain render must not re-resolve. Closing waits for a status rebuild.
    local state, cx = init("lanes", 78, 40, 5)
    workspace.event(state, { ev = "action", act = "open", value = row_key(state, "c-c5") }, cx)
    fixture = { uncommittedChanges = {}, stacks = {} }
    workspace.view(state, cx)
    check(state.diff ~= nil and state.diff.bind == "c\0change5", "a plain render re-resolved the diff")
end
do
    -- Squash into the open commit changes its title and its patch. The header
    -- follows the title; the patch must reload by changeId, not the new cliId.
    local state, cx = init("lanes", 78, 40, 5)
    workspace.event(state, { ev = "action", act = "open", value = row_key(state, "c-c5") }, cx)
    local before = command_count("diff")
    local rewritten = status(5)
    for _, c in rewritten.stacks[1].branches[1].commits do
        if c.changeId == "change5" then
            c.cliId = "c9"
            c.message = "Squashed title\nbody"
        end
    end
    fixture = rewritten
    timers[#timers]()
    local header = false
    visit(workspace.view(state, cx).main, function(node)
        if (node.p or {}).text == "c9 Squashed title" then header = true end
    end)
    local last
    for _, argv in calls do if argv[4] == "diff" then last = argv[5] end end
    check(header and command_count("diff") == before + 1 and last == "change5",
        "a rebuilt title left the patch stale or reloaded cliId " .. tostring(last) .. " (diffs " .. before .. "->" .. command_count("diff") .. ", header " .. tostring(header) .. ")")
end
do
    -- A pull can change hunks and leave the subject and cliId alone.
    -- The open diff must reload by changeId. The next unchanged poll must not.
    local state, cx = init("lanes", 78, 40, 5)
    workspace.event(state, { ev = "action", act = "open", value = row_key(state, "c-c5") }, cx)
    local before = command_count("diff")
    local pulled = status(5)
    for _, c in pulled.stacks[1].branches[1].commits do
        if c.changeId == "change5" then c.changes[1].filePath = "rebased.txt" end
    end
    pulled.upstreamState = { behind = 1 }
    fixture = pulled
    timers[#timers]()
    local header = false
    visit(workspace.view(state, cx).main, function(node)
        if (node.p or {}).text == "c5 Commit 5" then header = true end
    end)
    local last
    for _, argv in calls do if argv[4] == "diff" then last = argv[5] end end
    check(header and state.diff ~= nil and state.diff.id == "change5" and state.diff.bind == "c\0change5"
        and command_count("diff") == before + 1 and last == "change5",
        "same subject and cliId left the diff stale or reloaded " .. tostring(last)
        .. " (diffs " .. before .. "->" .. command_count("diff") .. ", header " .. tostring(header) .. ")")
    local steady = command_count("diff")
    timers[#timers]()
    check(command_count("diff") == steady,
        "steady poll refetched the open diff (" .. steady .. "->" .. command_count("diff") .. ")")
end
do
    -- r during a mutation must wait for the status read that mutation starts.
    local state, cx = init("lanes", 78, 40, 5)
    workspace.event(state, { ev = "action", act = "pick", value = row_key(state, "b-b1") }, cx)
    hold_op, hold_done = "unapply", {}
    workspace.key(state, { name = "u", text = "u" }, cx)
    workspace.key(state, { name = "y", text = "y" }, cx)
    local held = #hold_done == 1 and state.busy ~= nil
    check(held, "unapply did not stay busy (held " .. #hold_done .. ")")
    if held then
        workspace.event(state, { ev = "action", act = "open", value = row_key(state, "c-c5") }, cx)
        local diffs = command_count("diff")
        workspace.key(state, { name = "r", text = "r" }, cx)
        check(command_count("diff") == diffs and state.diff ~= nil and state.diff.id == "change5",
            "r during a mutation reloaded before the post-mutation read")
        pending_status = {}
        hold_op = nil
        hold_done[1]({ stdout = "{}", stderr = "", status = 0 })
        check(#pending_status == 1 and command_count("diff") == diffs,
            "r during a mutation started " .. #pending_status .. " status reads and "
            .. (command_count("diff") - diffs) .. " early diffs")
        if #pending_status == 1 then
            pending_status[1]({ stdout = "{}", stderr = "", status = 0 })
        end
        pending_status = nil
        local last
        for _, argv in calls do if argv[4] == "diff" then last = argv[5] end end
        check(command_count("diff") == diffs + 1 and last == "change5",
            "r during a mutation did not reload by changeId when the post-mutation read landed (diffs "
            .. diffs .. "->" .. command_count("diff") .. ", last " .. tostring(last) .. ")")
    end
    hold_op, hold_done = nil, nil
end
for _, interruption in { "before", "during", "busy" } do
    for _, outcome in { "conflicts", "clean", "failure", "timeout", "invalid", "spawn", "undo", "undo-failure", "undo-spawn" } do
        local function encode(v)
            local t = type(v)
            if t == "string" then return string.format("%q", v)
            elseif t == "number" or t == "boolean" then return tostring(v)
            elseif t == "nil" then return "null"
            elseif t ~= "table" then return string.format("%q", tostring(v)) end
            if #v > 0 then
                local parts = {}
                for i = 1, #v do parts[i] = encode(v[i]) end
                return "[" .. table.concat(parts, ",") .. "]"
            end
            local parts = {}
            for k, val in v do table.insert(parts, string.format("%q", tostring(k)) .. ":" .. encode(val)) end
            table.sort(parts)
            return "{" .. table.concat(parts, ",") .. "}"
        end
        local state, cx = init("lanes", 78, 40, 1)
        local notices = 0
        local pull_text = ""
        function cx:toast(_, text)
            if string.find(text, "Pulled main", 1, true) == 1 then
                notices += 1
                pull_text = text
            end
        end
        pending_status = {}
        timers[#timers]()
        local pre = pending_status[1]
        pending_status = nil
        local saved = tern.json.encode
        tern.json.encode = encode
        replies = {
            check = {
                upToDate = false,
                upstreamCommits = { count = 2 },
                baseBranch = { name = "main", remoteName = "origin", baseSha = "aaa", currentSha = "bbb" },
                hasWorktreeConflicts = false,
                branchStatuses = { { name = "feature", status = "unpushed", rebasable = true } },
            },
            pull = { status = "completed" },
        }
        local superseded = string.find(outcome, "undo", 1, true) == 1
        if superseded then
            replies.pull = { status = "completed_with_conflicts", conflicts = { { branch = "feature" } } }
        end
        workspace.key(state, { name = "f", text = "f" }, cx)
        workspace.key(state, { name = "y", text = "y" }, cx)
        replies, forced = nil, nil
        pending_status = {}
        if interruption ~= "before" then
            pre({ stdout = "{}", stderr = "", status = 0 })
            check(#pending_status == 1, interruption .. ": replacement read did not start")
        end
        if superseded then
            local interrupted = if interruption == "before" then pre else pending_status[1]
            replies = { oplog = { { id = "after-pull", details = { body = "pull" } } } }
            workspace.key(state, { name = "z", text = "z" }, cx)
            check(state.sheet ~= nil and state.sheet.command == "but undo", "Undo did not open its confirmation")
            if interruption == "during" then
                interrupted({ stdout = "{}", stderr = "", status = 0 })
            end
            pending_status = {}
            hold_op, hold_done = "undo", {}
            local process_run = tern.process.run
            tern.process.run = function(argv, opts, complete)
                if argv[4] == "undo" then
                    check(notices == 1 and string.find(pull_text, "an unconfirmed worktree status", 1, true) ~= nil,
                        interruption .. "/" .. outcome .. ": previous verification was not settled before Undo started")
                    if outcome == "undo-spawn" then error("undo spawn failed") end
                end
                return process_run(argv, opts, complete)
            end
            workspace.key(state, { name = "y", text = "y" }, cx)
            tern.process.run = process_run
            replies, forced = nil, nil
            if interruption == "busy" then
                interrupted({ stdout = "{}", stderr = "", status = 0 })
            end
            hold_op = nil
            if outcome ~= "undo-spawn" then
                check(state.busy ~= nil and #hold_done == 1, "Undo did not start its write")
                if #hold_done == 1 then
                    hold_done[1]({ stdout = "{}", stderr = "undo failed", status = if outcome == "undo-failure" then 1 else 0 })
                end
            end
            hold_done = nil
            if interruption == "before" then
                interrupted({ stdout = "{}", stderr = "", status = 0 })
            end
            timers[#timers]()
            check(#pending_status == 1, interruption .. "/" .. outcome .. ": missing post-undo status read")
            fixture = status(1)
            fixture.conflictedFiles = { "after-undo.txt" }
            if #pending_status == 1 then
                pending_status[1]({ stdout = "{}", stderr = "", status = 0 })
            end
            pending_status = nil
            tern.json.encode = saved
            local expected = if outcome == "undo" then "Undid pull"
                elseif outcome == "undo-failure" then "but undo: undo failed" else "undo spawn failed"
            local text = state.notice and state.notice.text or ""
            check(string.find(text, expected, 1, true) ~= nil
                and state.notice.tone == (if outcome == "undo" then "success" else "error"),
                interruption .. "/" .. outcome .. ": previous verification overwrote Undo (" .. text .. ")")
            timers[#timers]()
            check(notices == 1 and state.notice ~= nil and state.notice.text == text,
                interruption .. "/" .. outcome .. ": a superseded pull notice ran again")
            continue
        end
        if interruption == "busy" then
            hold_op, hold_done = "branch", {}
            workspace.key(state, { name = "a", text = "a" }, cx)
            check(state.busy ~= nil and #hold_done == 1, "apply did not hold its branch read")
        else
            workspace.key(state, { name = "?", text = "?" }, cx)
            check(state.sheet ~= nil and state.sheet.kind == "help", "help did not open during pull refresh")
        end
        if interruption == "before" then
            pre({ stdout = "{}", stderr = "", status = 0 })
        elseif #pending_status == 1 then
            pending_status[1]({ stdout = "{}", stderr = "", status = 0 })
        end
        local early = state.notice and state.notice.text or ""
        check(state.notice == nil and notices == 0, interruption .. ": pull notice ran on a nil workspace (" .. early .. ")")
        pending_status = {}
        local process_run = tern.process.run
        if outcome == "spawn" then
            tern.process.run = function() error("status spawn failed") end
        end
        if interruption == "busy" then
            hold_op = nil
            hold_done[1]({ stdout = "{}", stderr = "", status = 0 })
            hold_done = nil
            timers[#timers]()
        else
            workspace.key(state, { name = "escape" }, cx)
        end
        tern.process.run = process_run
        check(outcome == "spawn" or #pending_status == 1,
            interruption .. ": resuming did not start the parked pull read (" .. #pending_status .. ")")
        if #pending_status == 1 then
            local post = status(1)
            if outcome == "conflicts" then post.conflictedFiles = { "src/conflicted.txt" } end
            fixture = if outcome == "invalid" then nil else post
            pending_status[1]({ stdout = "{}", stderr = "status failed", status = if outcome == "failure" then 1 else 0,
                timed_out = outcome == "timeout" })
        end
        pending_status = nil
        tern.json.encode = saved
        local text = state.notice and state.notice.text or ""
        local expected = if outcome == "conflicts" then "src/conflicted.txt"
            elseif outcome == "clean" then "Pulled main" else "an unconfirmed worktree status"
        check(state.notice ~= nil and state.notice.tone == (if outcome == "clean" then "success" else "warning")
            and string.find(text, expected, 1, true) ~= nil and string.find(text, "Pulled main", 1, true) == 1,
            interruption .. "/" .. outcome .. ": pull notice lost its result (" .. text .. ")")
        fixture = status(1)
        timers[#timers]()
        check(notices == 1, interruption .. "/" .. outcome .. ": pull notice ran " .. notices .. " times")
    end
end
for _, target in { "c-c5", "cf-5:1", "f-u1", "b-b1" } do
    for _, outcome in { "unchanged", "renumbered", "removed", "failure", "timeout", "invalid", "cancelled", "sheet", "busy", "closed", "repeated" } do
        local state, cx = init("lanes", 78, 40, 5)
        workspace.event(state, { ev = "action", act = "open", value = row_key(state, target) }, cx)
        local original = state.diff
        local seen_fixture = fixture
        local diffs, statuses = command_count("diff"), command_count("status")
        pending_status = {}
        timers[#timers]()
        workspace.key(state, { name = "r", text = "r" }, cx)
        if outcome == "repeated" then workspace.key(state, { name = "r", text = "r" }, cx) end
        check(#pending_status == 1 and command_count("status") == statuses + 1 and command_count("diff") == diffs,
            target .. ": reload must join the poll before reading a patch")
        if outcome == "renumbered" then
            fixture = status(5)
            fixture.stacks[1].branches[1].cliId = "other-branch"
            fixture.uncommittedChanges[1].cliId = "other-file"
            for _, c in fixture.stacks[1].branches[1].commits do c.cliId = "other-" .. c.cliId end
        elseif outcome == "removed" then
            fixture = { uncommittedChanges = {}, stacks = {} }
        elseif outcome == "invalid" then
            fixture = nil
        elseif outcome == "cancelled" then
            state.reads += 1
        elseif outcome == "sheet" then
            workspace.event(state, { ev = "action", act = "do", value = "?" }, cx)
        elseif outcome == "busy" then
            state.busy = "Checking changes…"
        elseif outcome == "closed" then
            workspace.key(state, { name = "escape" }, cx)
        end
        local done = pending_status[1]
        done({ stdout = "{}", stderr = "status failed", status = if outcome == "failure" then 1 else 0,
            timed_out = outcome == "timeout" })
        local retry = pending_status[2]
        pending_status = nil
        local reloads = if outcome == "repeated" then 2 elseif outcome == "unchanged" or outcome == "renumbered" then 1 else 0
        check(command_count("diff") == diffs + reloads,
            target .. ": poll completion lost or incorrectly ran the " .. outcome .. " reload")
        if reloads > 0 then
            check(state.diff == original and state.diff.gen == 1 + reloads,
                target .. ": the joined reload lost its diff")
            local last
            for _, argv in calls do if argv[4] == "diff" then last = argv[5] end end
            check(last == original.id, target .. ": the joined reload changed its stable selector")
        elseif outcome == "removed" or outcome == "closed" then
            check(state.diff == nil, target .. ": a removed or closed target was reopened")
        end
        local before = command_count("diff")
        if outcome == "invalid" then fixture = seen_fixture end
        if outcome == "sheet" then workspace.key(state, { name = "escape" }, cx) end
        if outcome == "busy" then state.busy = nil end
        if outcome == "cancelled" then
            check(retry ~= nil, target .. ": invalidated reload did not start a replacement read")
            if retry then retry({ stdout = "{}", stderr = "", status = 0 }) end
        end
        timers[#timers]()
        local deferred = outcome == "sheet" or outcome == "busy" or outcome == "cancelled"
        check(command_count("diff") == before + (if deferred then 1 else 0),
            target .. ": the " .. outcome .. " reload did not resume exactly once")
        if deferred then
            local last
            for _, argv in calls do if argv[4] == "diff" then last = argv[5] end end
            check(state.diff == original and last == original.id, target .. ": deferred reload lost its stable selector")
        end
        before = command_count("diff")
        timers[#timers]()
        check(command_count("diff") == before, target .. ": a completed reload leaked into the next poll")
    end
end
do
    -- A status read can snapshot the worktree before pull and deliver after
    -- pull clears busy. The result must wait for a read started after pull.
    local function encode(v)
        local t = type(v)
        if t == "string" then return string.format("%q", v)
        elseif t == "number" or t == "boolean" then return tostring(v)
        elseif t == "nil" then return "null"
        elseif t ~= "table" then return string.format("%q", tostring(v)) end
        if #v > 0 then
            local parts = {}
            for i = 1, #v do parts[i] = encode(v[i]) end
            return "[" .. table.concat(parts, ",") .. "]"
        end
        local parts = {}
        for k, val in v do table.insert(parts, string.format("%q", tostring(k)) .. ":" .. encode(val)) end
        table.sort(parts)
        return "{" .. table.concat(parts, ",") .. "}"
    end
    local state, cx = init("lanes", 78, 40, 1)
    pending_status = {}
    timers[#timers]()
    local pre = pending_status[1]
    pending_status = {}
    local saved = tern.json.encode
    tern.json.encode = encode
    replies = {
        check = {
            upToDate = false,
            upstreamCommits = { count = 2 },
            baseBranch = { name = "main", remoteName = "origin", baseSha = "aaa", currentSha = "bbb" },
            hasWorktreeConflicts = false,
            branchStatuses = { { name = "feature", status = "unpushed", rebasable = true } },
        },
        pull = { status = "completed" },
    }
    workspace.key(state, { name = "f", text = "f" }, cx)
    workspace.key(state, { name = "y", text = "y" }, cx)
    replies, forced = nil, nil
    fixture = status(1)
    pre({ stdout = "{}", stderr = "", status = 0 })
    local early = state.notice and state.notice.text or ""
    check(state.notice == nil and #pending_status == 1,
        "pull used a pre-mutation status (" .. early .. "); follow-up reads " .. #pending_status)
    if #pending_status == 1 then
        local post = status(1)
        post.conflictedFiles = { "src/conflicted.txt" }
        fixture = post
        pending_status[1]({ stdout = "{}", stderr = "", status = 0 })
    end
    local text = state.notice and state.notice.text or ""
    check(state.notice ~= nil and state.notice.tone == "warning" and string.find(text, "src/conflicted.txt", 1, true) ~= nil
        and string.find(text, "Pulled main", 1, true) == 1,
        "pull notice ignored the post-pull conflict (" .. tostring(state.notice and state.notice.tone) .. " " .. text .. ")")
    tern.json.encode = saved
    pending_status = nil
    forced = nil
end
do
    -- An unnamed branch has no bind. The first render keeps it; the next refresh closes it.
    local function unnamed(change_id, message, seg)
        return {
            uncommittedChanges = {},
            stacks = { { cliId = "s1", assignedChanges = {}, branches = {
                { cliId = seg, name = "", branchStatus = "completelyUnpushed", commits = {
                    { cliId = "c1", commitId = "sha-" .. change_id, changeId = change_id, message = message, changes = {} },
                } },
            } } },
        }
    end
    local state, cx = init("lanes", 78, 40, 1)
    fixture = unnamed("orig", "original segment", "seg")
    workspace.key(state, { name = "r", text = "r" }, cx)
    workspace.event(state, { ev = "action", act = "open", value = row_key(state, "b-seg") }, cx)
    check(state.diff ~= nil and state.diff.bind == nil, "the unnamed branch diff must open without a stable bind")
    local shown = workspace.view(state, cx)
    local back = false
    visit(shown.main, function(node)
        if (node.p or {}).text == "← Back" then back = true end
    end)
    check(state.diff ~= nil and back, "the first render closed the unnamed branch diff")
    fixture = unnamed("other", "other segment", "seg")
    calls = {}
    workspace.key(state, { name = "r", text = "r" }, cx)
    workspace.view(state, cx)
    local diff_ids = {}
    for _, argv in calls do
        for j, a in argv do
            if a == "diff" then table.insert(diff_ids, argv[j + 1]) end
        end
    end
    check(state.diff == nil and #diff_ids == 0,
        "reused cliId showed the other unnamed segment (diffs=" .. table.concat(diff_ids, ",") .. ")")
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
            local left, detail = height(split.c[1], cx.cols), height(split.c[2], cx.cols)
            local room = cx.rows * 17 - height(result.main.c[1], cx.cols) - 32 - dock
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
    for _, key in { row_key(state, "f-u1"), row_key(state, "c-c1"), row_key(state, "cf-1:1") } do
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
    local drawn = height(board.c[1], cx.cols)
    for i = 1, #columns, per_line do
        local line = 0
        for j = i, math.min(#columns, i + per_line - 1) do line = math.max(line, height(columns[j], cx.cols)) end
        drawn += line + (if i == 1 then 0 else 8)
    end
    local available = cx.rows * 17 - height(result.main.c[1], cx.cols) - 32 - dock_height(result, cx)
    check(drawn <= available, "board " .. cx.cols .. "x" .. cx.rows .. ": needs " .. drawn .. "px, only " .. available .. "px available")
    if not browsing then
        check(positions(result.main)[state.cursor] ~= nil, "board: keyboard cursor is outside the visible columns/cards")
    end
end
for _, size in { { 35, 23, 0 }, { 35, 24, 0 }, { 78, 24, 7 }, { 78, 24, 8 }, { 78, 30, 8 } } do
    for _, held in { "branch", "file", "marks", "commit", "cfile" } do
        local state, cx = init("board", size[1], size[2], 2)
        fixture = board_status()
        fixture.stacks[2].branches = {}
        if held == "branch" then fixture.uncommittedChanges = {} end
        workspace.key(state, { name = "r" }, cx)
        local source = row_key(state, if held == "commit" then "c-c1-1" elseif held == "cfile" then "cf-1-1:1"
            elseif held == "branch" then "b-b1" else "f-u1")
        workspace.event(state, { ev = "action", act = "pick", value = source }, cx)
        if held == "marks" then
            workspace.event(state, { ev = "change", name = "mark", value = "work.txt", checked = true }, cx)
        end
        if size[3] > 0 then
            state.notice = { tone = "warning", text = string.rep("Conflicted file. ", math.floor((cx.cols - 12) * size[3] / 17)) }
        end
        for lane = 1, #state.lanes + 1 do
            workspace.event(state, { ev = "action", act = "browse", value = "L" .. lane }, cx)
            local result = workspace.view(state, cx)
            local board = result.main.c[2]
            local available = cx.rows * 17 - height(result.main.c[1], cx.cols) - 32
                - dock_height(result, cx) - height(board.c[1], cx.cols)
            check_board(state, cx, true)
            check(state.cursor == source and (held ~= "marks" or state.marked["work.txt"]),
                "notice board: browsing changed the held changes")
            for _, column in board.c[2].c do
                check((#column.c > 1) == (available >= 65),
                    "notice board: column body does not fit the remaining " .. available .. "px")
            end
        end
        workspace.key(state, { name = "escape" }, cx)
        check_board(state, cx, true)
        check(state.notice == nil and state.cursor == source, "notice board: dismissing the notice changed the selection")
        for _, column in workspace.view(state, cx).main.c[2].c[2].c do
            check(#column.c > 1, "notice board: dismissing the notice did not restore the column body")
        end
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
        check(column ~= nil and #column.c > 1, "short board fixture must leave a body line")
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
do
    local state, cx = init("board", 160, 60, 2)
    local source, branch = row_key(state, "f-u1"), row_key(state, "b-b1")
    workspace.event(state, { ev = "action", act = "native_drop", value = source .. ":" .. branch .. ":c-gone:0" }, cx)
    check(state.sheet == nil, "a stale native neighbour must not fall back to amending the branch tip")
end
do
    local saved_fs, saved_encode, saved_array = tern.fs, tern.json.encode, tern.json.array
    local disk = {}
    local path = modules.gb.board_path(root)
    tern.fs = {
        exists = function(p) return disk[p] ~= nil or saved_fs.exists(p) end,
        read = function(p) return disk[p] or saved_fs.read(p) end,
        mkdir = function() end,
        write = function(p, text) disk[p] = text end,
    }
    local function encode(value)
        if type(value) ~= "table" then return tostring(value) end
        local keys = {}
        for key in value do table.insert(keys, key) end
        table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
        local parts = {}
        for _, key in keys do table.insert(parts, tostring(key) .. "=" .. encode(value[key])) end
        return "{" .. table.concat(parts, ",") .. "}"
    end
    tern.json.encode = encode
    tern.json.array = function(value) return value or {} end
    pending_status = {}
    local state, cx = init("board", 160, 60, 2)
    workspace.event(state, { ev = "action", act = "native_board" }, cx)
    local initial = pending_status[1]
    pending_status = nil
    initial({ stdout = "{}", stderr = "", status = 0 })
    check(disk[path] ~= nil, "a board requested before status loads must be written after status succeeds")
    workspace.event(state, { ev = "action", act = "native_board" }, cx)
    workspace.key(state, { name = "?", text = "?" }, cx)
    disk[path] = "dragged while help was open"
    local source, branch, near = row_key(state, "f-u1"), row_key(state, "b-b1"), row_key(state, "c-c1")
    local drop = { ev = "action", act = "native_drop", value = source .. ":" .. branch .. ":" .. near .. ":0" }
    workspace.event(state, drop, cx)
    check(disk[path] == "dragged while help was open", "refusing a drop during help must not rewrite under the open sheet")
    workspace.key(state, { name = "escape" }, cx)
    check(disk[path] ~= "dragged while help was open", "a refused drop must reconcile when an unrelated sheet closes")
    workspace.event(state, drop, cx)
    check(state.sheet ~= nil, "the native file drop must open its amend confirmation")
    disk[path] = "pending amend"
    workspace.event(state, { ev = "action", act = "native_board" }, cx)
    check(disk[path] == "pending amend", "a forced resync must preserve the card while its confirmation is pending")
    workspace.key(state, { name = "escape" }, cx)
    check(disk[path] ~= "pending amend", "cancelling must release a deferred resync")
    tern.fs, tern.json.encode, tern.json.array = saved_fs, saved_encode, saved_array
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
