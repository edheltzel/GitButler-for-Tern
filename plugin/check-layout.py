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
    local cx = { pane = pane, cwd = root, cols = cols, rows = rows, opened = nil }
    function cx:render() end
    function cx:open(url) self.opened = url end
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
        if p.actions and p.actions.click then found[p.key] = path end
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
