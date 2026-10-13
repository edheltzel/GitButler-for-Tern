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
local processes = {}
local snapshots, status_queue = {}, {}
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
    process = { run = function(argv, _, done)
        if argv[4] == "status" and #status_queue > 0 then fixture = table.remove(status_queue, 1) end
        table.insert(processes, table.clone(argv))
        snapshots[#processes] = fixture
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
    table.clear(processes)
    table.clear(snapshots)
    table.clear(status_queue)
    local cx = { pane = pane, cwd = root, cols = cols, rows = rows, opened = nil, toasts = {} }
    function cx:render() end
    function cx:open(url) self.opened = url end
    function cx:toast(tone, text, sub)
        table.insert(self.toasts, { tone = tone, text = text, sub = sub })
    end
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
        if p.key and p.actions and p.actions.click then found[p.key] = path end
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
local function mutations(command)
    local found = {}
    for _, argv in processes do
        if argv[4] == command then table.insert(found, argv) end
    end
    return found
end
local function with_destination(branch_status)
    local data = status(1)
    local target = status(1).stacks[1].branches[1].commits[1]
    target.cliId, target.commitId, target.changeId = "target", "target-sha", "destination-change"
    for fi, file in target.changes do file.cliId = "target:" .. fi end
    table.insert(data.stacks, { cliId = "dest-stack", assignedChanges = {}, branches = {
        { cliId = "upper", name = "pushed-upper", branchStatus = branch_status, commits = {} },
        { cliId = "dest", name = "destination", branchStatus = branch_status, commits = { target } },
    } })
    return data
end
for _, route in { "palette", "toolbar" } do
    local state, cx = init("board", 80, 24, 1)
    workspace.event(state, if route == "palette" then { ev = "action", act = "redo" }
        else { ev = "action", act = "do", value = "redo" }, cx)
    check(state.sheet and state.sheet.danger == true and state.sheet.command == "but redo",
        route .. ": redo must show a danger confirmation")
    for _, key in { { name = "enter" }, { name = "paste", text = "y" }, { name = "y", text = "y", shift = true } } do
        workspace.key(state, key, cx)
        check(#mutations("redo") == 0 and state.sheet ~= nil, route .. ": redo accepted a non-explicit y")
    end
    workspace.key(state, { name = "y", text = "y" }, cx)
    check(#mutations("redo") == 1 and state.sheet == nil, route .. ": explicit y must run redo once")
end
for _, source in { { key = "c-c1", ref = "change1" }, { key = "cf-1:1", ref = "change1:file-1" } } do
    for _, arrival in { "before-prompt", "before-confirm", "absent", "unpushed", "unread-first", "unread-final" } do
        local state, cx = init("board", 80, 24, 1)
        workspace.event(state, { ev = "action", act = "pick", value = source.key }, cx)
        workspace.event(state, { ev = "action", act = "drop", value = "newmove:" }, cx)
        check(state.sheet and state.sheet.kind == "prompt", source.key .. ": new move must ask for a branch name")
        if arrival == "before-prompt" then fixture = with_destination("nothingToPush") end
        if arrival == "unpushed" then fixture = with_destination("completelyUnpushed") end
        if arrival == "unread-first" then fixture = {} end
        workspace.key(state, { name = "paste", text = "destination" }, cx)
        workspace.key(state, { name = "enter" }, cx)
        local initial_danger = arrival == "before-prompt" or arrival == "unread-first"
        check(state.sheet and state.sheet.kind == "confirm" and state.sheet.danger == initial_danger,
            source.key .. " " .. arrival .. ": incorrect initial push guard")
        if arrival == "before-confirm" then fixture = with_destination("nothingToPush") end
        if arrival == "unread-final" then fixture = {} end
        workspace.key(state, { name = "enter" }, cx)
        if arrival == "unread-final" then
            check(state.sheet == nil and #mutations("move") == 0 and state.notice.tone == "error",
                source.key .. ": an unreadable final status must refuse the move")
            continue
        end
        local danger = initial_danger or arrival == "before-confirm" or arrival == "unread-final"
        if danger then
            check(state.sheet and state.sheet.danger == true and #mutations("move") == 0,
                source.key .. " " .. arrival .. ": Enter must not rewrite pushed or unreadable destinations")
            if arrival == "before-prompt" or arrival == "before-confirm" then
                local body = table.concat(state.sheet.body, "\n")
                check(string.find(body, "destination", 1, true) and string.find(body, "pushed-upper", 1, true),
                    source.key .. ": the warning must name the destination and its pushed descendant")
            else
                fixture = status(1)
            end
            workspace.key(state, { name = "y", text = "y" }, cx)
        end
        local writes = mutations("move")
        check(#writes == 1 and writes[1][5] == source.ref and writes[1][6] == "-b"
            and writes[1][7] == "destination" and writes[1][8] == "--json",
            source.key .. " " .. arrival .. ": the authorized move must keep its source and destination")
    end
end
for _, source in { "c-c1", "cf-1:1" } do
    for _, operation in { "move", "newmove", "above" } do
        if operation == "above" and source ~= "c-c1" then continue end
        for _, change in { "ordinary", "split-read", "destination-pushed", "source-pushed", "overlap", "unread" } do
            local state, cx = init("board", 80, 24, 1)
            local original = if operation == "newmove" then status(1) else with_destination("completelyUnpushed")
            fixture = original
            workspace.key(state, { name = "r" }, cx)
            workspace.event(state, { ev = "action", act = "pick", value = source }, cx)
            local target = if operation == "above" then "c-target" elseif operation == "move" then "b-dest" else ""
            workspace.event(state, { ev = "action", act = "drop", value = operation .. ":" .. target }, cx)
            if operation == "newmove" then
                workspace.key(state, { name = "paste", text = "destination" }, cx)
                workspace.key(state, { name = "enter" }, cx)
            end
            check(state.sheet and state.sheet.kind == "confirm" and not state.sheet.danger,
                source .. " " .. operation .. ": unpushed history must start with an ordinary confirm")
            local final = if operation == "newmove" then status(1) else with_destination("completelyUnpushed")
            if change == "destination-pushed" then final = with_destination("nothingToPush") end
            if change == "source-pushed" then final.stacks[1].branches[1].branchStatus = "nothingToPush" end
            if change == "overlap" then
                table.insert(final.uncommittedChanges, { cliId = "clash", filePath = "file-1", changeType = "modified" })
            end
            if change == "unread" then final = {} end
            table.insert(status_queue, final)
            if change == "split-read" then table.insert(status_queue, with_destination("nothingToPush")) end
            workspace.key(state, { name = "enter" }, cx)
            local label = source .. " " .. operation .. " " .. change
            if change == "overlap" or change == "unread" then
                check(#mutations("move") == 0 and state.sheet == nil and state.notice ~= nil,
                    label .. ": the final snapshot must refuse an unsafe or unreadable move")
                continue
            end
            if change == "destination-pushed" or change == "source-pushed" then
                check(#mutations("move") == 0 and state.sheet and state.sheet.danger,
                    label .. ": the final snapshot must require y for newly pushed history")
                workspace.key(state, { name = "enter" }, cx)
                check(#mutations("move") == 0, label .. ": Enter must not authorize the new danger sheet")
                workspace.key(state, { name = "y", text = "y" }, cx)
            end
            check(#mutations("move") == 1 and state.sheet == nil, label .. ": authorized move must run once")
            if change == "split-read" then
                for i, argv in processes do
                    if argv[4] ~= "move" then continue end
                    local safe = true
                    for _, stack in snapshots[i].stacks do
                        for _, branch in stack.branches do safe = safe and branch.branchStatus == "completelyUnpushed" end
                    end
                    check(safe, label .. ": an extra status read must not bypass push authorization")
                end
            end
        end
        for _, change in { "unchanged", "overlap", "new-warning" } do
            local state, cx = init("board", 80, 24, 1)
            fixture = if operation == "newmove" then status(1) else with_destination("completelyUnpushed")
            fixture.stacks[1].branches[1].branchStatus = "nothingToPush"
            workspace.key(state, { name = "r" }, cx)
            workspace.event(state, { ev = "action", act = "pick", value = source }, cx)
            local target = if operation == "above" then "c-target" elseif operation == "move" then "b-dest" else ""
            workspace.event(state, { ev = "action", act = "drop", value = operation .. ":" .. target }, cx)
            if operation == "newmove" then
                workspace.key(state, { name = "paste", text = "destination" }, cx)
                workspace.key(state, { name = "enter" }, cx)
            end
            check(state.sheet and state.sheet.danger, source .. " " .. operation .. ": pushed source must require y")
            local final = if change == "new-warning" then with_destination("nothingToPush")
                elseif operation == "newmove" then status(1) else with_destination("completelyUnpushed")
            final.stacks[1].branches[1].branchStatus = "nothingToPush"
            if change == "overlap" then
                table.insert(final.uncommittedChanges, { cliId = "clash", filePath = "file-1", changeType = "modified" })
            end
            table.insert(status_queue, final)
            workspace.key(state, { name = "y", text = "y" }, cx)
            local label = source .. " " .. operation .. " " .. change
            if change == "overlap" then
                check(#mutations("move") == 0 and state.sheet == nil, label .. ": y must not bypass fresh overlap checks")
            else
                if change == "new-warning" then
                    check(#mutations("move") == 0 and state.sheet and state.sheet.danger,
                        label .. ": accepting one warning must not authorize newly pushed branches")
                    workspace.key(state, { name = "y", text = "y" }, cx)
                end
                check(#mutations("move") == 1 and state.sheet == nil, label .. ": unchanged warning must not repeat")
            end
        end
    end
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
        if p.key and p.actions and p.actions.click then key = p.key end
    end)
    return key
end
do
    local state, cx = init("graph", 78, 30, 12)
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
    check(#cx.toasts == 1 and cx.toasts[1].tone == "info" and cx.toasts[1].text == "Nothing to commit.",
        "committing an empty workspace must report its outcome with a toast")
    check(state.cursor == "c-c5" and positions(after.main)["c-c5"] ~= nil,
        "the Nothing to commit notice hid the selected fifth commit")
    workspace.key(state, { name = "enter" }, cx)
    check(string.find(cx.opened, "&id=c5&label=", 1, true) ~= nil,
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
    local state, cx = init("graph", 78, 30, 12)
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
            local id = string.gsub(row.id, ":", "%%3A")
            check(cx.opened ~= nil and string.find(cx.opened, "&id=" .. id .. "&label=", 1, true) ~= nil,
                view .. ": double-click opened the wrong diff")
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
    local detail = workspace.view(state, cx).main.c[1].c[2]
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
    detail = workspace.view(state, cx).main.c[1].c[2]
    local count, commits = false, false
    visit(detail, function(node)
        if has_class(node, "bp-sub") then count = node.p.text == "Commits 7" end
        if has_class(node, "bp-files") then
            commits = #node.c == 7 and node.c[7].p.text == "+1 more"
        end
    end)
    check(count and commits, "a tall branch detail lost its commit summary")
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
