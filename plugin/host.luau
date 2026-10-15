--!strict

local gb = require("./gb")
local width = require("./width")
local ui = tern.ui
local letter = gb.letter
local STATUS_TEXT = gb.STATUS_TEXT

require("./lenses")

local POLL_MS = 5 * 1000
local READ_TIMEOUT_MS = 15 * 1000

-- One selectable line of the workspace: a file, a branch, a commit or a
-- committed file. `id` is the short CLI id shown in the list. Mutations
-- pass a stable ref (`zz:<path>`, full changeId) because CLI ids are
-- snapshot-local and a later `but` resolves them against a new snapshot.
type Row = {
	key: string,
	kind: string, -- "file" | "branch" | "commit" | "cfile"
	lane: number,
	id: string,
	label: string,
	branch: string?, -- the branch it belongs to (branch, commit and cfile rows); "" for an anonymous segment
	bkey: string?, -- key of that branch's row: identifies anonymous segments too
	path: string?,
	change: string?,
	changeId: string?, -- full change id of a commit, or of a committed file's commit
	message: string?, -- a commit's full message
}

-- A choice in a picker or list sheet.
type Option = { label: string, value: string, detail: string? }

-- The sheet over the workspace while an action asks for something:
-- `prompt` (text), `pick` (one of `options`), `confirm` (y/n, with the
-- exact `but` command shown) or `help`.
type Sheet = {
	kind: string,
	title: string,
	body: { string }?,
	command: string?,
	danger: boolean?,
	text: string?,
	options: { Option }?,
	index: number?,
	done: ((value: string) -> ())?,
}

type State = {
	cx: BlockCx,
	root: string,
	model: gb.Model?,
	seen: string?, -- the last status JSON, to skip renders when nothing changed
	reads: number, -- status reads started before this are dropped
	rows: { Row },
	lanes: { { Row } },
	cursor: string?,
	viewport: string?,
	list_budget: number?,
	marked: { [string]: boolean }, -- uncommitted file paths, not CLI ids
	loading: boolean,
	read_token: number, -- reads value of the in-flight status read
	refresh_callbacks: { (any) -> () },
	refresh_wait: { (any) -> () }, -- need a read started at the current reads
	refresh_again: boolean,
	fetched: number?,
	problem: string?,
	timer: TimerHandle?,
	sheet: Sheet?,
	busy: string?, -- what the running mutation is doing
	notice: { tone: string, text: string }?,
	view: string, -- "lanes" | "graph" | "stacks" | "branches" | "board"
	diff: DiffState?, -- the diff shown in place of the view (⏎), until Esc
}

-- `but diff <id> --json` for one file, commit or branch, shown in the panel.
type DiffState = {
	cx: BlockCx,
	root: string,
	id: string,
	label: string,
	changes: { gb.Change }?,
	problem: string?,
	gen: number, -- reloads started before this are dropped
	kind: string,
	key: string?,
	bind: string?, -- changeId, changeId+path, path, or branch name; nil if unnamed
}

local running: { [number]: State } = {}

local function basename(path: string): string
	return string.match(path, "([^/]+)/*$") or path
end

local function pane_alive(pane: number): boolean
	for _, p in tern.pane.list() do
		if p.pane == pane then
			return true
		end
	end
	return false
end

local MAX_DIFF_LINES = 5000

local function load_diff(d: DiffState)
	d.gen += 1
	local gen = d.gen
	local err = gb.run(d.root, { "diff", d.id, "--json" }, READ_TIMEOUT_MS, function(r: gb.Result)
		if not pane_alive(d.cx.pane) or gen ~= d.gen then
			return
		end
		local changes = if r.ok then gb.changes(r.data) else nil
		if changes then
			d.changes, d.problem = changes, nil
		else
			d.problem = r.err or "but diff printed something other than a diff"
		end
		d.cx:render()
	end)
	if err then
		d.problem = err
	end
end

-- CLI ids are snapshot-local and get reused. The diff follows this instead.
local function diff_bind(row: Row): string?
	if row.kind == "commit" and row.changeId and row.changeId ~= "" then
		return "c\0" .. row.changeId
	elseif row.kind == "cfile" and row.changeId and row.changeId ~= "" and row.path then
		return "f\0" .. row.changeId .. "\0" .. row.path
	elseif row.kind == "file" and row.path then
		return "u\0" .. row.path
	elseif row.kind == "branch" and row.branch and row.branch ~= "" then
		return "b\0" .. row.branch
	end
	return nil
end

-- Retarget an open diff after a status read. No cliId-key fallback: an unbound
-- diff (an unnamed branch) closes on this refresh, not on the first render.
local function follow_diff(state: State): DiffState?
	local d = state.diff
	if d == nil then
		return nil
	end
	local row: Row? = nil
	if d.bind then
		for _, r in state.rows do
			if diff_bind(r) == d.bind then
				row = r
				break
			end
		end
	end
	if row == nil then
		state.diff = nil
		return nil
	end
	local next = if row.kind == "commit" then row.id .. " " .. row.label else row.label
	-- The commit header leads with the snapshot cliId. A title change is a
	-- new patch; a cliId-only change is not. Reload by the stable id.
	local previous = if row.kind == "commit" then string.match(d.label, "^%S+ (.*)$") else d.label
	local retitle = if row.kind == "commit" then previous ~= row.label else previous ~= next
	d.key = row.key
	d.label = next
	if retitle then
		load_diff(d)
	end
	return d
end

local function diff_nodes(d: DiffState): { Node }
	if d.changes == nil then
		return { if d.problem then ui.code(d.problem) else ui.progress(nil, "Reading the diff…") }
	end
	local nodes: { Node } = {}
	if d.problem then
		table.insert(nodes, ui.text({ ui.span("Reload failed. The patch below is stale.", "warning") }))
		table.insert(nodes, ui.code(d.problem))
	end
	local budget = MAX_DIFF_LINES
	for i, c in d.changes :: { gb.Change } do
		local head = { ui.span(c.path, "strong"), ui.span("  " .. c.status, "muted") }
		local body: Node
		local text = c.text
		if text == nil then
			body = ui.text({ ui.span("No text diff (binary or too large).", "muted") })
		else
			local _, lines = string.gsub(text, "\n", "\n")
			if budget <= 0 then
				body = ui.overflow(lines)
			else
				budget -= lines
				body = ui.diff(text, c.path)
			end
		end
		local section = ui.section(head, { body })
		local p: { [string]: any } = section.p or {}
		p.key = "c" .. i
		p.collapsible = true
		section.p = p
		table.insert(nodes, section)
	end
	if #(d.changes :: { gb.Change }) == 0 then
		table.insert(nodes, ui.text({ ui.span("No changes.", "muted") }))
	end
	return nodes
end

-- This run is still the block's current one, and the pane still exists.
local function alive(state: State): boolean
	if running[state.cx.pane] ~= state then
		return false
	end
	if pane_alive(state.cx.pane) then
		return true
	end
	running[state.cx.pane] = nil
	if state.timer then
		state.timer:cancel()
		state.timer = nil
	end
	return false
end

-- Keys become node ids: keep them to safe characters.
local function key_of(kind: string, id: string): string
	return kind .. "-" .. (string.gsub(id, ".", function(c)
		return string.format("%02x", string.byte(c))
	end))
end

local function branch_key(b: gb.Branch, stack: gb.Stack): string
	if b.name ~= "" then
		return key_of("b", b.name)
	end
	local ids: { string } = { b.id }
	for _, branch in stack.branches do
		table.insert(ids, branch.name)
		for _, c in branch.commits do
			table.insert(ids, c.changeId .. ":" .. c.sha)
		end
		table.insert(ids, "\1")
	end
	return key_of("b", "\0" .. table.concat(ids, "\0"))
end

local function commit_key(c: gb.Commit): string
	return key_of("c", c.changeId .. "\0" .. c.sha)
end

-- Rows by lane: lane 1 is the uncommitted changes, then one per stack.
local function build_rows(state: State)
	local model = state.model
	local lanes: { { Row } } = {}
	local rows: { Row } = {}
	local function add(lane: number, row: Row)
		lanes[lane] = lanes[lane] or {}
		table.insert(lanes[lane], row)
		table.insert(rows, row)
	end
	lanes[1] = {}
	if model then
		for _, f in model.unassigned do
			add(1, { key = key_of("f", f.path), kind = "file", lane = 1, id = f.id, label = f.path, path = f.path, change = f.change })
		end
		for i, stack in model.stacks do
			local lane = i + 1
			lanes[lane] = {}
			for _, f in stack.assigned do
				add(lane, { key = key_of("a", f.path), kind = "file", lane = lane, id = f.id, label = f.path, path = f.path, change = f.change })
			end
			for _, b in stack.branches do
				local name = gb.label(b)
				local bkey = branch_key(b, stack)
				add(lane, { key = bkey, kind = "branch", lane = lane, id = if b.name ~= "" then b.name else b.id, label = name, branch = b.name, bkey = bkey })
				for _, c in b.commits do
					add(lane, { key = commit_key(c), kind = "commit", lane = lane, id = c.id, label = c.title, branch = b.name, bkey = bkey, changeId = c.changeId, message = c.message })
					for _, f in c.files do
						add(lane, { key = key_of("cf", c.changeId .. "\0" .. c.sha .. "\0" .. f.path), kind = "cfile", lane = lane, id = f.id, label = f.path, branch = b.name, bkey = bkey, path = f.path, change = f.change, changeId = c.changeId })
					end
				end
			end
		end
	end
	state.lanes, state.rows = lanes, rows
	local found, anchored = false, false
	for _, r in rows do
		found = found or r.key == state.cursor
		anchored = anchored or r.key == state.viewport
	end
	if not found then
		local row = rows[1]
		state.cursor = if row then row.key else nil
	end
	local lane = tonumber(string.match(state.viewport or "", "^L(%d+)$"))
	if state.view ~= "board" or not found or (not anchored and not (lane and lane >= 1 and lane <= #lanes + 1)) then
		state.viewport = state.cursor
	end
	follow_diff(state)
end

local function index_of(state: State, key: string?): number
	for i, r in state.rows do
		if r.key == key then
			return i
		end
	end
	return 0
end

local function selected(state: State): Row?
	return state.rows[index_of(state, state.cursor)]
end

local function refresh(state: State, after: ((any) -> ())?)
	-- A sheet's closure already captured its targets. Swapping the model
	-- under it can retarget the action, so skip the read until it closes.
	if not alive(state) then
		return
	end
	if state.sheet ~= nil or state.busy ~= nil then
		if after then
			after(nil)
		end
		return
	end
	-- Join only a read started at the current `reads`. A mutation bumps
	-- `reads` first, so a status that snapshotted the pre-mutation worktree
	-- cannot be handed to its result.
	if state.loading and state.read_token == state.reads then
		if after then
			table.insert(state.refresh_callbacks, after)
		end
		return
	end
	if state.loading then
		if after then
			table.insert(state.refresh_wait, after)
		else
			state.refresh_again = true
		end
		return
	end
	if after then
		table.insert(state.refresh_callbacks, after)
	end
	state.loading = true
	state.read_token = state.reads
	local token = state.reads
	local function finish(data: any)
		state.loading = false
		local callbacks = state.refresh_callbacks
		state.refresh_callbacks = {}
		for _, callback in callbacks do
			callback(data)
		end
	end
	local function pump()
		local waiters = state.refresh_wait
		state.refresh_wait = {}
		local again = state.refresh_again
		state.refresh_again = false
		if #waiters > 0 then
			refresh(state, waiters[1])
			for i = 2, #waiters do
				refresh(state, waiters[i])
			end
		elseif again then
			refresh(state)
		end
	end
	local err = gb.run(state.root, { "status", "-f", "--json" }, READ_TIMEOUT_MS, function(r: gb.Result)
		if not alive(state) then
			return
		end
		if token ~= state.reads or state.sheet ~= nil or state.busy ~= nil then
			finish(nil)
			state.cx:render()
			pump()
			return
		end
		local model = if r.ok then gb.model(r.data) else nil
		if model == nil then
			state.problem = r.err or "but status printed something other than a workspace"
		else
			state.fetched = os.time()
			state.problem = nil
			local text = tern.json.encode(r.data)
			if text ~= state.seen then
				state.seen = text
				state.model = model
				build_rows(state)
			end
		end
		finish(if model then r.data else nil)
		state.cx:render()
	end)
	if err then
		state.problem = err
		finish(nil)
		pump()
	end
end

local function poll(state: State)
	if not alive(state) or state.timer then
		return
	end
	state.timer = tern.timer(POLL_MS, function()
		state.timer = nil
		if alive(state) then
			refresh(state)
			poll(state)
		end
	end)
end

-- Branch names each uncommitted path is assigned to.
local function assignments(model: gb.Model): { [string]: string }
	local out: { [string]: string } = {}
	for _, stack in model.stacks do
		local top = stack.branches[1]
		for _, f in stack.assigned do
			out[f.path] = if top then gb.label(top) else stack.id
		end
	end
	return out
end

-- A row is one terminal line plus 6px (`.bp-r`, --bp-row). main never
-- scrolls, so the cap is design px. 17 is --sf-lh's fallback: the block
-- only has cell counts, so a shorter line overstates cx.rows * LH.
-- Narrow lanes keep one row for that residual (lanes_view).
-- Lane card chrome (HEAD, BODY, GAP) matches .bp-lane/.bp-lh/.bp-lanes in
-- styles.css; the main padding and dock gap are Tern's, a static estimate.
-- 60 cells and 2 dock lines: floor((1020 - 29 - 41 - 34) / 23) = 39.
local LH = 17
local ROW = LH + 6
local HEAD = LH + 12
local BODY = 9 -- .bp-lane padding-bottom 8 + border-bottom 1
local PADS = BODY + 12 + 4 + 16 -- body, main padding, dock gap, block gap
local GAP = 6 -- .bp-lanes gap between lane cards
local TOP = 29 -- .bp-btn is 21px (1px border + 1px padding + line + 1px padding + 1px border), plus .bp-top's 8px padding-bottom

local KEYS_HELP = "↑↓ move  ←→ lanes  ⏎ diff  space mark  c commit  m move  A absorb  e reword  n/N branch  a/u apply/unapply  z undo  O oplog  p push  P PR  L land  f pull  v/1-5 view  ? help"

local function keys_help(state: State): string
	return if state.view == "board" and state.cx.cols < 68 then "↑↓ move  ←→ lanes  ⏎ diff  ? help" else KEYS_HELP
end

local PICK_FOOT = "↑↓ choose  ⏎ ok  1-9 pick  q/esc cancel"

-- Lines `text` takes in the dock: its own lines, each wrapped at the pane
-- width minus the block's side padding (about 12 cells, measured at 78 cols).
local function wrapped(state: State, text: string): number
	local cols = math.max(1, state.cx.cols - 12)
	local n = 0
	for line in string.gmatch(text .. "\n", "(.-)\n") do
		n += math.max(1, math.ceil(width.cells(line) / cols))
	end
	return n
end

-- The dock's first row: counts and refresh state, as (text, tone). Upstream
-- and push state are the chips at the top of the panel.
local function status_parts(state: State): { { string } }
	local parts: { { string } } = {}
	local model = state.model
	if model then
		local branches = 0
		for _, s in model.stacks do
			branches += #s.branches
		end
		table.insert(parts, { string.format("%d stacks · %d branches · %d uncommitted", #model.stacks, branches, #model.unassigned), "strong" })
	end
	if state.busy then
		table.insert(parts, { "  " .. state.busy, "accent" })
	elseif state.loading then
		table.insert(parts, { "  refreshing…", "muted" })
	elseif state.fetched then
		table.insert(parts, { "  updated " .. os.date("%H:%M:%S", state.fetched), "muted" })
	end
	return parts
end

local function dock_px(state: State): number
	local status = ""
	for _, part in status_parts(state) do
		status ..= part[1]
	end
	local n = wrapped(state, status) + wrapped(state, keys_help(state))
	if state.notice then
		n += wrapped(state, state.notice.text)
	end
	if state.problem and state.model then
		n += wrapped(state, state.problem)
	end
	return n * LH
end

local function fit_lines(px: number): number
	return math.max(1, math.floor(px / ROW))
end

-- The sync state as chips, { text, tone, click action? }, in GitButler's own
-- words: `N upstream commits` (the desktop header) or `Up to date` (`but
-- pull`), both pulling on click, which re-checks the target first; then what
-- there is to push, from the desktop's branch labels.
local function sync_chips(state: State, model: gb.Model): { { string } }
	local out: { { string } } = {}
	local conflicted, unpushed, diverged = false, 0, 0
	for _, s in model.stacks do
		for _, b in s.branches do
			if b.status == "unpushedCommitsRequiringForce" then
				diverged += 1
			elseif b.status == "completelyUnpushed" or b.status == "unpushedCommits" then
				unpushed += 1
			end
			for _, c in b.commits do
				conflicted = conflicted or c.conflicted
			end
		end
	end
	if state.busy then
		table.insert(out, { state.busy, "busy" })
	end
	if conflicted then
		table.insert(out, { "Conflicted", "bad" })
	end
	if model.behind > 0 then
		table.insert(out, { string.format("%d upstream %s", model.behind, if model.behind == 1 then "commit" else "commits"), "warn", "do=f" })
	else
		table.insert(out, { "Up to date", "ok", "do=f" })
	end
	if diverged > 0 then
		table.insert(out, { diverged .. " diverged", "bad" })
	end
	if unpushed > 0 then
		table.insert(out, { string.format("%d unpushed %s", unpushed, if unpushed == 1 then "branch" else "branches"), "warn" })
	elseif diverged == 0 then
		table.insert(out, { "Nothing to push", "ok" })
	end
	return out
end

local TAB_LABELS = { "1 Lanes", "2 Graph", "3 Stacks", "4 Branches", "5 Board" }

local function control_rows(state: State, labels: { string }): number
	local available = math.max(1, state.cx.cols)
	local rows, used = 1, 0
	for _, label in labels do
		local cells = width.cells(label) + 3
		if used > 0 and used + cells > available then
			rows += 1
			used = 0
		end
		used += cells
	end
	return rows
end

local function top_rows(state: State): number
	local model = state.model
	if model == nil then
		return 1
	end
	local chips, all = {}, table.clone(TAB_LABELS)
	for _, c in sync_chips(state, model) do
		table.insert(chips, c[1])
		table.insert(all, c[1])
	end
	return if control_rows(state, all) == 1 then 1 else control_rows(state, TAB_LABELS) + control_rows(state, chips)
end

-- Pane height minus the top bar, one card head, pads and the dock.
local function list_px(state: State): number
	return state.cx.rows * LH - top_rows(state) * TOP - HEAD - PADS - dock_px(state)
end

-- Picker: the same conversion, minus the key row above the list (the 20px
-- Cancel button sets its height, plus the 8px card gap; it never wraps).
local function picker_lines(state: State): number
	return fit_lines(list_px(state) - 28)
end


-- The views share the rows and cursor, so every action works in each. `v`
-- cycles them; the choice is kept in tern.kv for new blocks. They draw with
-- `el` nodes styled by styles.css (scoped to this block). See DESIGN.md
-- for viewport anchoring and height budgets.
local VIEWS = { "lanes", "graph", "stacks", "branches", "board" }

-- Which rows the keyboard walks in each view.
local function walkable(state: State, row: Row): boolean
	local view = state.view
	if view == "graph" then
		if row.kind == "cfile" then
			return false
		end
		if row.kind == "branch" then
			-- a branch with commits is a pill on its first commit row
			for _, r in state.lanes[row.lane] or {} do
				if r.kind == "commit" and r.bkey == row.key then
					return false
				end
			end
		end
		return true
	elseif view == "stacks" then
		return row.kind ~= "cfile"
	elseif view == "branches" then
		return row.kind == "file" or row.kind == "branch"
	elseif view == "board" and row.kind == "cfile" then
		-- the selected commit opens to its files
		local cur = selected(state)
		return cur ~= nil and (cur.kind == "commit" or cur.kind == "cfile") and cur.changeId == row.changeId
	end
	return true
end

local function el(class: string, kids: { Node }?, props: { [string]: any }?): Node
	local p: { [string]: any } = props or {}
	p.class = class
	return ui.el("div", p, kids or {})
end

local function sp(text: string, class: string?): Node
	return ui.el("span", { class = class or "", text = text }, {})
end

local function lane_class(lane: number): string
	return "bp-l" .. ((lane - 2) % 5)
end

-- A row element that selects on click and opens the diff on double-click.
local function pickable(state: State, row: Row, class: string, kids: { Node }): Node
	local on = row.key == state.cursor
	return el(class .. (if on then " bp-sel" else ""), kids, {
		key = row.key,
		mark = if on then "pick" else nil,
		actions = { click = "pick=" .. row.key, dblclick = "open=" .. row.key },
	})
end

local function branch_of_row(model: gb.Model, row: Row): gb.Branch?
	for _, stack in model.stacks do
		for _, b in stack.branches do
			if row.bkey ~= nil and branch_key(b, stack) == row.bkey then
				return b
			end
		end
	end
	return nil
end

-- What a row's branch is called (anonymous segments get gb.label's name).
local function branch_label(model: gb.Model, row: Row): string
	local b = branch_of_row(model, row)
	return if b then gb.label(b) else (row.branch or row.label)
end

local function commit_of_row(model: gb.Model, row: Row): gb.Commit?
	for _, stack in model.stacks do
		for _, b in stack.branches do
			for _, c in b.commits do
				if commit_key(c) == row.key then
					return c
				end
			end
		end
	end
	return nil
end

-- The stack branch this branch sits on, if any (status lists stacks top-first).
local function below_of(model: gb.Model, b: gb.Branch): gb.Branch?
	for _, stack in model.stacks do
		for i, candidate in stack.branches do
			if candidate == b then
				return stack.branches[i + 1]
			end
		end
	end
	return nil
end

local function status_badge(b: gb.Branch?): Node
	local s = if b then STATUS_TEXT[b.status] else nil
	return ui.badge(if s then s[1] else (if b then b.status else "?"), if s then s[2] else "muted")
end

local function file_line(state: State, row: Row, assigned: { [string]: string }?): { Node }
	local marked = row.path ~= nil and state.marked[row.path] == true
	local kids: { Node } = {
		sp(if marked then "●" else " ", "bp-mark"),
		sp(letter(row.change or ""), "bp-ch bp-ch-" .. letter(row.change or "")),
		sp(row.label, "bp-path"),
		sp(row.id, "bp-id"),
	}
	if assigned and row.kind == "file" then
		local where = assigned[row.label]
		table.insert(kids, sp(if where then "→ " .. where else "unassigned", if where then "bp-asg bp-acc" else "bp-asg bp-dim"))
	end
	return kids
end

-- Rows in [first, last] of `list` around the supplied anchor, and whether the
-- "↑ N more" / "↓ N more" lines fit (width.slice): callers draw a marker only
-- when its flag is set, so rows and markers never take more than `room` lines.
local function window(list: { Row }, cursor: string?, room: number): (number, number, boolean, boolean)
	if room < 1 then
		return 1, 0, false, false
	end
	local at = 1
	for i, r in list do
		if r.key == cursor then
			at = i
		end
	end
	return width.slice(#list, at, room)
end

local function hint(keys: string): Node
	return sp(keys, "bp-hint")
end

-- The detail for `pick` (default: the selection), its height cap, and the
-- height it uses.
local function detail_pane(state: State, model: gb.Model, assigned: { [string]: string }, pick: Row?): (Node, number, number)
	local available = math.max(0, list_px(state))
	local budget = if state.cx.cols < 110 then math.min(384, available // 2) else available
	if budget < 16 then
		return el(""), budget, budget
	end
	local row = pick or selected(state)
	local kids: { Node } = {}
	local px = 8
	local limit = budget - (LH + 8)
	local keys: string? = nil
	local function add(node: Node, lines: number, extra: number?)
		local cost = 8 + lines * LH + (extra or 0)
		if lines > 0 and px + cost <= limit then
			table.insert(kids, node)
			px += cost
		end
	end
	local function capacity(maximum: number): number
		return math.max(0, math.min(maximum, (limit - px - 8) // LH))
	end
	if row == nil then
		add(sp("Nothing selected", "bp-dim"), 1)
	elseif row.kind == "file" or row.kind == "cfile" then
		add(sp(row.label, "bp-dh"), 1, 5)
		add(el("bp-badges", {
			ui.badge(row.change or "changed", "warning"),
			el("bp-label", { ui.badge(if row.kind == "cfile" then "in " .. (row.branch or "commit") else (assigned[row.label] and ("→ " .. assigned[row.label]) or "unassigned"), "muted") }),
		}), 1, 3)
		keys = if row.kind == "file" then "⏎ diff  space mark  c commit  m move  A absorb" else "⏎ diff  m move"
	else
		local b = branch_of_row(model, row)
		local c = if row.kind == "commit" then commit_of_row(model, row) else nil
		add(sp(if c then c.title else row.label, "bp-dh"), 1, 5)
		local badges: { Node } = { el("bp-label", { ui.badge("⎇ " .. branch_label(model, row), "accent") }), status_badge(b) }
		if c and c.conflicted then
			table.insert(badges, ui.badge("conflicted", "error"))
		end
		if b and b.review then
			table.insert(badges, ui.badge("PR " .. b.review, "info"))
		end
		add(el("bp-badges", badges), 1, 3)
		if c then
			limit -= LH + 8
			add(ui.kv({
				{ { ui.span("id", "muted") }, { ui.span(c.id, "mono") } },
				{ { ui.span("change", "muted") }, { ui.span(string.sub(c.changeId, 1, 12), "mono") } },
				{ { ui.span("sha", "muted") }, { ui.span(string.sub(c.sha, 1, 12), "mono") } },
			}), 3, 6)
			local body = string.match(c.message, "^[^\n]*\n+(.+)$")
			local cap = capacity(4)
			if body and cap > 0 then
				local kept: { string } = {}
				for line in string.gmatch(body .. "\n", "(.-)\n") do
					if #kept == math.min(3, cap) then
						if #kept == cap then
							kept[cap] = "…"
						else
							table.insert(kept, "…")
						end
						break
					end
					table.insert(kept, line)
				end
				add(sp(table.concat(kept, "\n"), "bp-body"), #kept)
			end
			limit += LH + 8
			add(sp("Files " .. #c.files, "bp-sub"), 1)
			local files: { Node } = {}
			local file_cap = capacity(7)
			local shown = math.min(6, #c.files, math.max(0, file_cap - (if #c.files > file_cap then 1 else 0)))
			for _, r in state.rows do
				if #files == shown then
					break
				end
				if r.kind == "cfile" and r.changeId == c.changeId then
					table.insert(files, el("bp-f", file_line(state, r, nil)))
				end
			end
			if #c.files > #files and file_cap > 0 then
				table.insert(files, sp("+" .. (#c.files - #files) .. " more", "bp-dim"))
			end
			add(el("bp-files", files), #files)
			keys = "⏎ diff  e reword  m move  p push  P PR  z undo"
		elseif b and #b.commits > 0 then
			-- a branch row (lanes view): where it sits and its newest commits
			local below = below_of(model, b)
			limit -= LH + 8
			add(sp(if below then "stacked on " .. gb.label(below) else "on the target", "bp-dim bp-clip"), 1)
			limit += LH + 8
			add(sp("Commits " .. #b.commits, "bp-sub"), 1)
			local lines: { Node } = {}
			local cap = capacity(7)
			local shown = math.min(6, #b.commits, math.max(0, cap - (if #b.commits > cap then 1 else 0)))
			for i = 1, shown do
				local bc = b.commits[i]
				table.insert(lines, el("bp-f", { sp(bc.title, "bp-t"), sp(bc.id, "bp-id") }))
			end
			if #b.commits > shown and cap > 0 then
				table.insert(lines, sp("+" .. (#b.commits - shown) .. " more", "bp-dim"))
			end
			add(el("bp-files", lines), #lines)
			keys = "⏎ diff  e rename  p push  P PR  N stack  L land"
		else
			add(sp("No commits yet.", "bp-dim"), 1)
			keys = "c commit here  e rename  p push"
		end
	end
	limit = budget
	if keys then
		add(hint(keys), 1)
	end
	return el("bp-detail", kids), budget, px + 8 -- padding-bottom; px already counted the top 8
end

-- Height a narrow pane keeps under the list so every selectable row's
-- detail fits. Same kind and the same capped body and file lines draw the
-- same detail, so each shape is measured once. It depends on the workspace,
-- not the selection, so picking a row never moves the rows under the pointer.
local function detail_reserve(state: State, model: gb.Model, assigned: { [string]: string }): number
	local commits: { [string]: gb.Commit } = {}
	local branches: { [string]: gb.Branch } = {}
	for _, stack in model.stacks do
		for _, b in stack.branches do
			branches[branch_key(b, stack)] = b
			for _, c in b.commits do
				commits[commit_key(c)] = c
			end
		end
	end
	local function body_lines(message: string): number
		local body = string.match(message, "^[^\n]*\n+(.+)$")
		if body == nil then
			return 0
		end
		local n = 0
		for _ in string.gmatch(body .. "\n", "(.-)\n") do
			n += 1
			if n == 4 then
				return 4
			end
		end
		return n
	end
	local seen: { [string]: boolean } = {}
	local picks: { Row } = {}
	local function take(row: Row, shape: string)
		if seen[shape] then
			return
		end
		seen[shape] = true
		table.insert(picks, row)
	end
	for _, r in state.rows do
		if r.kind == "commit" then
			local c = commits[r.key]
			if c then
				take(r, "c" .. body_lines(c.message) .. "x" .. math.min(#c.files, 7))
			else
				take(r, "c?" .. r.key)
			end
		elseif r.kind == "branch" then
			local b = if r.bkey then branches[r.bkey] else nil
			take(r, "b" .. (if b then math.min(#b.commits, 7) else 0))
		else
			take(r, r.kind)
		end
	end
	local most = 0
	for _, r in picks do
		local _, _, used = detail_pane(state, model, assigned, r)
		most = math.max(most, used)
	end
	if most == 0 then
		local _, _, used = detail_pane(state, model, assigned, nil)
		most = used
	end
	return most
end

-- Side by side when the pane is wide, stacked when narrow. CSS decides with
-- flex-wrap: the block's reported size can lag the pane after a resize.
local function split(_state: State, left: Node, right: Node, side: string): Node
	local lp: { [string]: any } = left.p or {}
	local rp: { [string]: any } = right.p or {}
	lp.class = (lp.class or "") .. " bp-split-a bp-split-" .. side
	rp.class = (rp.class or "") .. " bp-split-b bp-split-" .. side
	left.p, right.p = lp, rp
	return el("bp-split", { left, right }, { key = "split" })
end

-- Lanes: a card per lane (uncommitted, then one per stack), each stack a
-- line of branches and commits with their files, and the selection's detail
-- beside it (under it when narrow).
local function lanes_view(state: State, model: gb.Model): Node
	local assigned = assignments(model)
	local current = selected(state)
	-- A narrow pane puts the detail under the lanes.
	local detail = detail_pane(state, model, assigned)
	local budget = list_px(state)
	if state.cx.cols < 110 then
		-- list_px is cx.rows * 17. The block never receives --sf-lh, so a
		-- shorter line overstates the pane by about 1px per reported row.
		-- Drawn lines are shorter by the same amount and cancel most of it;
		-- the residual (this window: the detail box 5px past main) is under
		-- one row. One ROW covers that, and costs one row when the line
		-- really is 17. It does not depend on the selection.
		budget -= detail_reserve(state, model, assigned) + ROW
	end
	local function order_lanes(first: number): { number }
		local order: { number } = {}
		if state.lanes[first] then
			table.insert(order, first)
		end
		for lane = 1, #state.lanes do
			if lane ~= first then
				table.insert(order, lane)
			end
		end
		return order
	end
	local anchor = state.rows[index_of(state, state.viewport)]
	local first = if anchor then anchor.lane else 1
	local order = order_lanes(first)

	-- One `.bp-r` line per row. A stack's line runs from its first branch
	-- (assigned files sit above it) to its last row.
	local function lane_row(rows: { Row }, i: number, start: number): Node
		local r = rows[i]
		if r.kind == "file" then
			local kids = file_line(state, r, if r.lane == 1 then assigned else nil)
			if r.lane > 1 then
				table.insert(kids, 1, el("bp-g bp-ind"))
			end
			return pickable(state, r, "bp-r", { el("bp-msg", kids) })
		end
		local parts: { Node } = {}
		if i > start then
			table.insert(parts, el("bp-lt"))
		end
		if i < #rows then
			table.insert(parts, el("bp-lb"))
		end
		local msg: { Node } = {}
		if r.kind == "branch" then
			table.insert(parts, el("bp-dot bp-ring"))
			local b = branch_of_row(model, r)
			table.insert(msg, sp("⎇ " .. r.label, "bp-bh-n"))
			local badges: { Node } = {}
			if b and b.review then
				table.insert(badges, ui.badge("PR " .. b.review, "info"))
			end
			if b and b.upstream > 0 then
				table.insert(badges, ui.badge(b.upstream .. " upstream", "warning"))
			end
			table.insert(badges, status_badge(b))
			table.insert(msg, el("bp-badges bp-end", badges))
		elseif r.kind == "commit" then
			table.insert(parts, el("bp-dot"))
			local c = commit_of_row(model, r)
			table.insert(msg, sp(r.label, "bp-t"))
			table.insert(msg, sp(r.id, "bp-id"))
			if c and c.conflicted then
				table.insert(msg, ui.badge("conflicted", "error"))
			end
		else
			table.insert(msg, el("bp-ind"))
			for _, k in file_line(state, r, nil) do
				table.insert(msg, k)
			end
		end
		return pickable(state, r, "bp-r", { el("bp-g", { el("bp-ln " .. lane_class(r.lane), parts) }), el("bp-msg", msg) })
	end

	local function lane_card(lane: number, rows: { Row }, cap: number): Node
		local on = current ~= nil and current.lane == lane
		local head: { Node } = {}
		if lane == 1 then
			head = { sp("Uncommitted", "bp-lh-n"), sp(tostring(#model.unassigned), "bp-warn") }
		else
			local stack = model.stacks[lane - 1]
			local top = stack.branches[1]
			head = { sp(if top then gb.label(top) else stack.id, "bp-lh-n") }
			if #stack.branches > 1 then
				table.insert(head, sp("stack of " .. #stack.branches, "bp-dim"))
			end
		end
		local start = #rows + 1
		for i, r in rows do
			if r.kind ~= "file" then
				start = i
				break
			end
		end
		local lo, hi, up, down = window(rows, state.viewport, cap)
		local body: { Node } = {}
		if up then
			table.insert(body, el("bp-r", { el("bp-msg", { sp("↑ " .. (lo - 1) .. " more", "bp-dim") }) }))
		end
		for i = lo, hi do
			table.insert(body, lane_row(rows, i, start))
		end
		if down then
			table.insert(body, el("bp-r", { el("bp-msg", { sp("↓ " .. (#rows - hi) .. " more", "bp-dim") }) }))
		end
		if #rows == 0 then
			table.insert(body, el("bp-r", { el("bp-msg", { sp(if lane == 1 then "No uncommitted changes." else "No commits yet.", "bp-dim") }) }))
		end
		return el("bp-lane " .. lane_class(lane) .. (if on then " bp-cur" else ""), {
			el("bp-lh", head),
			el("bp-rows", body),
		}, { key = "L" .. lane })
	end

	local left = budget
	local cards: { Node } = {}
	for _, lane in order do
		local rows = state.lanes[lane]
		local extra = if #cards == 0 then 0 else GAP + HEAD + BODY
		local room = left - extra
		if room < ROW then
			break
		end
		local cap = fit_lines(room)
		local shown = math.max(1, math.min(#rows, cap))
		table.insert(cards, lane_card(lane, rows, cap))
		left -= extra + shown * ROW
	end
	return split(state, el("bp-lanes", cards, { key = "lanes" }), detail, "lanes")
end

-- Graph: every commit top-down with a lane per stack, joined at the base.
local function graph_view(state: State, model: gb.Model): Node
	local assigned = assignments(model)
	local list: { Row } = {}
	for _, r in state.rows do
		if walkable(state, r) then
			table.insert(list, r)
		end
	end
	local n = #model.stacks
	local first: { [number]: number } = {}
	for i, r in list do
		if r.lane > 1 and first[r.lane] == nil then
			first[r.lane] = i
		end
	end
	local base = #list + 1
	local function graph(i: number, r: Row?): Node
		local cells: { Node } = {}
		for lane = 2, n + 1 do
			local parts: { Node } = {}
			local start = first[lane]
			if start and i >= start then
				if i > start then
					table.insert(parts, el("bp-lt"))
				end
				if i < base then
					table.insert(parts, el("bp-lb"))
				end
			end
			if r and r.lane == lane then
				local head = r.kind == "branch"
				if r.kind == "commit" then
					local prev = list[i - 1]
					head = prev == nil or prev.bkey ~= r.bkey
				end
				table.insert(parts, el(if r.kind == "branch" then "bp-dot bp-ring bp-hollow" elseif head then "bp-dot bp-ring" else "bp-dot"))
			elseif r == nil and lane == 2 then
				table.insert(parts, el("bp-dot bp-base"))
			elseif r == nil and start then
				-- reaches from this lane's center to lane 2's, however many stacks
				local w = ((lane - 2) * 2) .. "ch"
				table.insert(parts, el("bp-join", nil, { min = { w = w }, max = { w = w } }))
			end
			table.insert(cells, el("bp-ln " .. lane_class(lane), parts))
		end
		return el("bp-g", cells, { min = { w = (n * 2) .. "ch" }, max = { w = (n * 2) .. "ch" } })
	end

	-- A narrow pane puts the detail under the list.
	local detail = detail_pane(state, model, assigned)
	local budget = list_px(state)
	if state.cx.cols < 110 then
		budget -= detail_reserve(state, model, assigned)
	end
	local room = fit_lines(budget) - 2
	local lo, hi, up, down = window(list, state.viewport, room)
	local lines: { Node } = {}
	if up then
		table.insert(lines, el("bp-more", { sp("↑ " .. (lo - 1) .. " more", "bp-dim") }))
	end
	for i = lo, hi do
		local r = list[i]
		local ref: { Node } = {}
		local msg: { Node } = {}
		if r.kind == "file" then
			if i == 1 then
				table.insert(ref, ui.badge("uncommitted", "warning"))
			end
			for _, k in file_line(state, r, assigned) do
				table.insert(msg, k)
			end
		else
			local prev = list[i - 1]
			if prev == nil or prev.bkey ~= r.bkey then
				local b = branch_of_row(model, r)
				table.insert(ref, ui.badge(branch_label(model, r), if b and b.status == "nothingToPush" then "success" else "accent"))
			end
			if r.kind == "commit" then
				local c = commit_of_row(model, r)
				table.insert(msg, sp(r.label, "bp-t"))
				table.insert(msg, sp(r.id, "bp-id"))
				if c and c.conflicted then
					table.insert(msg, ui.badge("conflicted", "error"))
				end
			else
				table.insert(msg, sp("no commits yet", "bp-dim"))
			end
		end
		table.insert(lines, pickable(state, r, "bp-r", { el("bp-ref", ref), graph(i, r), el("bp-msg", msg) }))
	end
	if down then
		table.insert(lines, el("bp-more", { sp("↓ " .. (#list - hi) .. " more", "bp-dim") }))
	elseif room > 0 and hi == #list then
		table.insert(lines, el("bp-r", { el("bp-ref", { ui.badge("base", "muted") }), graph(base, nil), el("bp-msg", { sp("common base", "bp-dim") }) }, { key = "base" }))
	end
	local left = el("bp-ga", {
		el("bp-gh", { sp("Branch", "bp-gh1"), sp("Graph", "bp-gh2"), sp("Commit", "") }),
		el("bp-rows", lines),
	})
	return split(state, left, detail, "graph")
end

-- Stacks: the uncommitted tray, then one column per stack.
local function stacks_view(state: State, model: gb.Model): Node
	local assigned = assignments(model)
	-- Columns wrap (CSS) about every 32 cells, and wrapped rows share the
	-- height; a stale small cx.cols only shrinks the budget, never overflows.
	local ncols = math.max(1, #state.lanes - 1)
	local per_line = math.max(1, state.cx.cols // 32)
	local col_lines = math.ceil(ncols / per_line)
	-- floor of 2 cards: a new block can report a stale, too-small size
	local room = math.max(2, fit_lines(list_px(state) - 5 * LH) // 2 // col_lines) -- cards are two lines
	local tray: { Node } = { sp("Uncommitted " .. #model.unassigned, "bp-tray-h") }
	local chips = state.lanes[1] or {}
	local tlo, thi, tup, tdown = window(chips, state.viewport, math.max(1, (state.cx.cols - 20) // 24))
	if tup then
		table.insert(tray, sp("+" .. (tlo - 1), "bp-dim"))
	end
	for i = tlo, thi do
		table.insert(tray, pickable(state, chips[i], "bp-chip", file_line(state, chips[i], nil)))
	end
	if tdown then
		table.insert(tray, sp("+" .. (#chips - thi) .. " more", "bp-dim"))
	end
	local cols: { Node } = {}
	for lane = 2, #state.lanes do
		local rows: { Row } = {}
		for _, r in state.lanes[lane] do
			if r.kind ~= "cfile" then
				table.insert(rows, r)
			end
		end
		local lo, hi, up, down = window(rows, state.viewport, room)
		local kids: { Node } = {}
		if up then
			table.insert(kids, sp("↑ " .. (lo - 1) .. " more", "bp-dim"))
		end
		for i = lo, hi do
			local r = rows[i]
			if r.kind == "branch" then
				local b = branch_of_row(model, r)
				local below = if b then below_of(model, b) else nil
				table.insert(kids, pickable(state, r, "bp-bh", {
					sp("⎇ " .. r.label, "bp-bh-n"),
					status_badge(b),
				}))
				if below then
					table.insert(kids, sp("stacked on " .. gb.label(below), "bp-dim bp-clip"))
				end
			elseif r.kind == "commit" then
				local c = commit_of_row(model, r)
				table.insert(kids, pickable(state, r, "bp-cc", {
					sp(r.label, "bp-t"),
					el("bp-cc-meta", {
						sp(r.id, "bp-id"),
						sp(if c then (#c.files .. (if #c.files == 1 then " file" else " files")) else "", "bp-dim"),
					}),
				}))
			else
				table.insert(kids, pickable(state, r, "bp-f", file_line(state, r, assigned)))
			end
		end
		if down then
			table.insert(kids, sp("↓ " .. (#rows - hi) .. " more", "bp-dim"))
		end
		table.insert(cols, el("bp-sc " .. lane_class(lane), kids))
	end
	local row = selected(state)
	local foot: { Node } = {}
	if row then
		table.insert(foot, sp(row.label, "bp-t"))
		if row.kind == "commit" or row.kind == "cfile" then
			table.insert(foot, sp("on " .. branch_label(model, row), "bp-dim"))
		end
	end
	local columns = el("bp-cols", cols, { key = "cols" })
	return ui.col({ el("bp-tray", tray, { key = "tray" }), columns, el("bp-foot", foot, { key = "foot" }) })
end

-- Branches: a sidebar of branches by stack, the selected one on the right.
local function branches_view(state: State, model: gb.Model): Node
	local assigned = assignments(model)
	local side: { Node } = {}
	local list: { Row } = {}
	for _, r in state.rows do
		if walkable(state, r) then
			table.insert(list, r)
		end
	end
	local side_room = fit_lines(list_px(state)) - #state.lanes
	if state.cx.cols < 110 then
		side_room = math.max(3, side_room // 2) -- the branch pane stacks below
	end
	local lo, hi, up, down = window(list, state.viewport, side_room)
	local last = 0
	-- Sidebar height in px (styles.css .bp-side): 6px padding top and bottom,
	-- a 2px gap per item, a .bp-sh header's 8px margin, a .bp-sb row's 6px padding.
	local side_px = 12 + (hi - lo + 1) * (LH + 8) + ((if up then 1 else 0) + (if down then 1 else 0)) * (LH + 2)
	if up then
		table.insert(side, sp("↑ " .. (lo - 1) .. " more", "bp-dim"))
	end
	for i = lo, hi do
		local r = list[i]
		if r.lane ~= last then
			last = r.lane
			side_px += LH + 10
			table.insert(side, sp(if r.lane == 1 then "Uncommitted " .. #model.unassigned else "Stack " .. (r.lane - 1), if r.lane == 1 then "bp-sh bp-warn" else "bp-sh"))
		end
		if r.kind == "file" then
			table.insert(side, pickable(state, r, "bp-sb bp-sb-file", file_line(state, r, nil)))
		else
			local b = branch_of_row(model, r)
			local n = if b then #b.commits else 0
			table.insert(side, pickable(state, r, "bp-sb " .. lane_class(r.lane), {
				sp(r.label, "bp-sb-n"),
				sp(tostring(n), "bp-dim"),
				el("bp-sb-dot " .. (if b and b.status == "nothingToPush" then "bp-ok" else "bp-warnbg")),
			}))
		end
	end
	if down then
		table.insert(side, sp("↓ " .. (#list - hi) .. " more", "bp-dim"))
	end

	local row = selected(state)
	local main: { Node } = {}
	-- Side by side only when the reported width surely fits both panes;
	-- otherwise the sidebar and this pane stack and share the height.
	-- .bp-cmain: 8px padding top and bottom, the 1.4em title, the badge row,
	-- the hint and the three 10px gaps between them.
	local main_px = list_px(state) - (16 + (LH + 9) + (LH + 1) + LH + 30)
	if state.cx.cols < 110 then
		main_px -= side_px
	end
	local room = fit_lines(main_px)
	if row and row.kind == "file" then
		-- the files of the selected file's own lane: uncommitted, or assigned to a stack
		local all: { Row } = {}
		for _, r in state.lanes[row.lane] or {} do
			if r.kind == "file" then
				table.insert(all, r)
			end
		end
		local stack = if row.lane > 1 then model.stacks[row.lane - 1] else nil
		table.insert(main, sp(if stack and stack.branches[1] then "Assigned to " .. gb.label(stack.branches[1]) else "Uncommitted changes", "bp-h1"))
		table.insert(main, el("bp-badges", { ui.badge(#all .. (if #all == 1 then " file" else " files"), "warning") }))
		local files: { Node } = {}
		local flo, fhi, fup, fdown = window(all, state.viewport, room)
		if fup then
			table.insert(files, sp("↑ " .. (flo - 1) .. " more", "bp-dim"))
		end
		for i = flo, fhi do
			local r = all[i]
			table.insert(files, el("bp-f" .. (if r.key == state.cursor then " bp-sel" else ""), file_line(state, r, assigned)))
		end
		if fdown then
			table.insert(files, sp("↓ " .. (#all - fhi) .. " more", "bp-dim"))
		end
		table.insert(main, el("bp-files", files))
		table.insert(main, hint("⏎ diff  space mark  c commit  m move  A absorb"))
	elseif row then
		local b = branch_of_row(model, row)
		local below = if b then below_of(model, b) else nil
		table.insert(main, sp(row.label, "bp-h1"))
		-- the commit count leads, so a clipped badge row still shows hidden commits
		local badges: { Node } = {}
		local hidden = 0
		local rest: { Node } = { status_badge(b) }
		if b and b.review then
			table.insert(rest, ui.badge("PR " .. b.review, "info"))
		end
		table.insert(rest, el("bp-label", { ui.badge(if below then "stacked on " .. gb.label(below) else "on the target", "muted") }))
		local spine: { Node } = {}
		-- Budgeted in px, not list rows: a commit block costs its title line, the
		-- .bp-sp-b padding (2 + 10) and title gap (2) plus 3 for a badge in the
		-- title; each file line costs one LH (styles.css .bp-sp-b / .bp-files).
		local SPINE_COMMIT, SPINE_FILE = LH + 17, LH
		local budget, used = main_px, 0
		for ci, c in (if b then b.commits else {}) do
			if used + SPINE_COMMIT > budget then
				hidden = #(b :: gb.Branch).commits - ci + 1
				break
			end
			-- a commit shows as many of its files as still fit; the rest are
			-- counted on its title line, so the count never takes a line itself
			local fit = math.max(0, math.min(#c.files, (budget - used - SPINE_COMMIT) // SPINE_FILE))
			local files: { Node } = {}
			for fi = 1, fit do
				local f = c.files[fi]
				table.insert(files, el("bp-f", { sp(letter(f.change), "bp-ch bp-ch-" .. letter(f.change)), sp(f.path, "bp-path"), sp(f.id, "bp-id") }))
			end
			used += SPINE_COMMIT + fit * SPINE_FILE
			table.insert(spine, el("bp-sp " .. lane_class(row.lane), {
				el("bp-sp-g", { el("bp-sp-line"), el(if ci == 1 then "bp-dot bp-ring" else "bp-dot") }),
				el("bp-sp-b", {
					el("bp-sp-t", {
						sp(c.title, "bp-t"),
						sp(c.id, "bp-id"),
						if fit < #c.files then sp("+" .. (#c.files - fit) .. " files", "bp-dim") else sp("", ""),
						if c.conflicted then ui.badge("conflicted", "error") else sp("", ""),
					}),
					el("bp-files", files),
				}),
			}))
		end
		if b == nil or #b.commits == 0 then
			table.insert(spine, sp("No commits yet. Mark files and press c to commit here.", "bp-dim bp-clip"))
		end
		if b then
			local count = #b.commits .. (if #b.commits == 1 then " commit" else " commits")
			table.insert(badges, ui.badge(if hidden > 0 then count .. ", " .. hidden .. " not shown" else count, if hidden > 0 then "warning" else "neutral"))
		end
		for _, badge in rest do
			table.insert(badges, badge)
		end
		table.insert(main, el("bp-badges", badges))
		table.insert(main, el("bp-spine", spine))
		table.insert(main, hint("p push  P PR  e rename  N stack a branch  u unapply  L land"))
	end
	return split(state, el("bp-side", side), el("bp-cmain", main), "branches")
end

local function dock_view(state: State): Node
	local status: { Span | string } = {}
	for _, part in status_parts(state) do
		table.insert(status, ui.span(part[1], part[2]))
	end
	local lines: { Node } = { ui.text(status) }
	if state.notice then
		table.insert(lines, ui.text({ ui.span(state.notice.text, state.notice.tone) }))
	end
	if state.problem and state.model then
		table.insert(lines, ui.text({ ui.span(state.problem, "warning") }))
	end
	table.insert(lines, ui.text({ ui.span(keys_help(state), "dim") }))
	return ui.col({ ui.lines(lines) })
end

local function move_to(state: State, i: number)
	local row = state.rows[math.clamp(i, 1, math.max(1, #state.rows))]
	if row then
		state.cursor = row.key
		state.viewport = row.key
	end
end

local function move_lane(state: State, delta: number)
	local row = selected(state)
	local lane = (if row then row.lane else 1) + delta
	while state.lanes[lane] ~= nil do
		for _, r in state.lanes[lane] do
			if walkable(state, r) then
				state.cursor = r.key
				state.viewport = r.key
				return
			end
		end
		lane += delta
	end
end

-- Moves the cursor `delta` walkable rows; past either end it stays put.
local function step(state: State, delta: number)
	local i = index_of(state, state.cursor) + delta
	while state.rows[i] do
		if walkable(state, state.rows[i]) then
			move_to(state, i)
			return
		end
		i += delta
	end
end

-- The first (or last) walkable row.
local function move_end(state: State, last: boolean)
	local from, to, by = 1, #state.rows, 1
	if last then
		from, to, by = #state.rows, 1, -1
	end
	for i = from, to, by do
		if walkable(state, state.rows[i]) then
			move_to(state, i)
			return
		end
	end
end

-- After a view change or refresh, keep the cursor on a row the view shows.
local function settle_cursor(state: State)
	local row = selected(state)
	if row == nil or walkable(state, row) then
		return
	end
	local key = row.key
	step(state, 1)
	if state.cursor == key then
		step(state, -1)
	end
end

-- Shows `view` (closing a diff), remembered for new blocks.
local function set_view(state: State, view: string)
	state.view = view
	state.diff = nil
	tern.kv.set("view", view)
	settle_cursor(state)
	state.viewport = state.cursor
end

local function cycle_view(state: State)
	local at = table.find(VIEWS, state.view) or 1
	set_view(state, VIEWS[at % #VIEWS + 1])
end

-- The panel's top bar: a tab per view and the sync chips (`sync_chips`),
-- on one row when they fit, else chips above tabs (`top_rows`).
local function top_bar(state: State, model: gb.Model): Node
	local tabs: { Node } = {}
	for i, v in VIEWS do
		local on = v == state.view and state.diff == nil
		table.insert(tabs, ui.el("button", {
			class = "bp-btn bp-tab" .. (if on then " bp-on" else ""),
			text = TAB_LABELS[i],
			actions = { click = "view=" .. v },
		}, {}))
	end
	local chips: { Node } = {}
	for _, c in sync_chips(state, model) do
		local p: { [string]: any } = { class = "bp-stat bp-stat-" .. c[2], text = c[1] }
		if c[3] then
			p.actions = { click = c[3] }
		end
		table.insert(chips, ui.el(if c[3] then "button" else "span", p, {}))
	end
	local stats, row = el("bp-stats", chips), el("bp-tabs", tabs)
	if top_rows(state) > 1 then
		return el("bp-top bp-top2", { stats, row }, { key = "top" })
	end
	return el("bp-top", { row, stats }, { key = "top" })
end

-- Actions. Every write is one `but` command, run in the background; one at
-- a time per block. Destructive ones confirm first, showing the command.

local MUTATE_TIMEOUT_MS = 60 * 1000
local NETWORK_TIMEOUT_MS = 120 * 1000

-- The dock line and a toast for every outcome, so an action run from the
-- palette or with the block out of sight still says what happened. Toasts
-- have no warning level: warnings show as info. `sub` is the toast's second line.
local function notify(state: State, tone: string, text: string, sub: string?)
	state.notice = { tone = tone, text = text }
	state.cx:toast(if tone == "success" then "success" elseif tone == "error" then "error" else "info", text, sub)
end

-- POSIX single quotes when an argument is not a plain token. Lua %q is not
-- paste-safe: $ and backticks still expand, and `;` can stay unquoted.
local function command_line(args: { string }): string
	local parts = { "but" }
	for _, a in args do
		if string.match(a, "^[%w@%%+=:,./-]+$") then
			table.insert(parts, a)
		else
			table.insert(parts, "'" .. (string.gsub(a, "'", "'\\''")) .. "'")
		end
	end
	return table.concat(parts, " ")
end

-- Runs `but <args> --json`; on success shows `done` (or what `done` makes of
-- the reply: text and a tone), clears the marks and re-reads the workspace.
local function mutate(state: State, label: string, args: { string }, timeout_ms: number, done: string | (data: any, workspace: any) -> (string, string?))
	if state.busy then
		return
	end
	local argv = table.clone(args)
	table.insert(argv, "--json")
	-- but 0.22.3 releases the status write guard before it writes JSON, so a
	-- read started before this mutation can deliver a pre-mutation snapshot
	-- after `busy` clears. Drop it; the result waits for a later read.
	state.reads += 1
	state.busy = label
	state.notice = nil
	state.cx:toast("info", label, command_line(args))
	local err = gb.run(state.root, argv, timeout_ms, function(r: gb.Result)
		if running[state.cx.pane] ~= state then
			return
		end
		state.busy = nil
		if r.ok then
			local function show(workspace: any)
				local text, tone = "", "success"
				if type(done) == "function" then
					local t, tn = done(r.data, workspace)
					text, tone = t, tn or "success"
				else
					text = done
				end
				notify(state, tone, text, command_line(args))
			end
			table.clear(state.marked)
			if type(done) == "function" then
				refresh(state, show)
			else
				show(nil)
				refresh(state)
			end

		else
			local why = r.err or "failed"
			state.notice = { tone = "error", text = command_line(args) .. ": " .. why }
			state.cx:toast("error", (string.gsub(label, "…$", "")) .. " failed", string.match(why, "^[^\n]*"))
			refresh(state)
		end
		state.cx:render()
	end)
	if err then
		state.busy = nil
		notify(state, "error", err)
	end
end

-- Runs a read (`--json` included in `args`) for an action that needs data first.
-- Escape bumps `state.reads` while `busy`; this callback is dropped so it
-- cannot open a sheet or take a fast path after cancel. `fail` is the closed
-- path when `but` itself fails (callers that omit it just notify).
local function read(state: State, label: string, args: { string }, use: (data: any) -> (), fail: ((string) -> ())?)
	if state.busy then
		return
	end
	state.busy = label
	local token = state.reads
	local err = gb.run(state.root, args, READ_TIMEOUT_MS, function(r: gb.Result)
		if not alive(state) then
			return
		end
		state.busy = nil
		if token ~= state.reads then
			state.cx:render()
			return
		end
		if r.ok then
			use(r.data)
		elseif fail then
			fail(r.err or "failed")
		else
			notify(state, "error", command_line(args) .. ": " .. (r.err or "failed"))
		end
		state.cx:render()
	end)
	if err then
		state.busy = nil
		if fail then
			fail(err)
		else
			notify(state, "error", err)
		end
	end
end

local function prompt(state: State, title: string, initial: string, done: (string) -> ())
	state.sheet = { kind = "prompt", title = title, text = initial, done = done }
end

local function confirm(state: State, title: string, body: { string }, args: { string }, danger: boolean, yes: () -> ())
	state.sheet = {
		kind = "confirm",
		title = title,
		body = body,
		command = command_line(args),
		danger = danger,
		done = function(_)
			yes()
		end,
	}
end

local function pick(state: State, title: string, options: { Option }, done: (string) -> ())
	state.sheet = { kind = "pick", title = title, options = options, index = 1, done = done }
end

local NEW_BRANCH = "\0new"

-- Applied branches that `-b` can name (anonymous segments can't be targeted).
local function branch_options(state: State, with_new: boolean): { Option }
	local out: { Option } = {}
	local model = state.model
	if model then
		for i, stack in model.stacks do
			for _, b in stack.branches do
				if b.name ~= "" then
					table.insert(out, { label = b.name, value = b.name, detail = "lane " .. (i + 1) })
				end
			end
		end
	end
	if with_new then
		table.insert(out, { label = "New branch…", value = NEW_BRANCH })
	end
	return out
end

-- The branch a row belongs to: its own for branch, commit and committed
-- file rows, the top branch of its lane for an assigned file.
local function branch_of(state: State, row: Row?): string?
	if row == nil then
		return nil
	end
	if row.kind == "branch" then
		return row.id
	elseif row.branch and row.branch ~= "" then
		return row.branch
	end
	local model = state.model
	local stack = if model and row.lane > 1 then model.stacks[row.lane - 1] else nil
	return if stack and stack.branches[1] then stack.branches[1].name else nil
end

local function apply_status(state: State, data: any): gb.Model?
	local model = gb.model(data)
	if model == nil then
		return nil
	end
	state.problem = nil
	state.model = model
	state.seen = tern.json.encode(data)
	state.fetched = os.time()
	build_rows(state)
	return model
end

local function has_marks(state: State): boolean
	for _, on in state.marked do
		if on then
			return true
		end
	end
	return false
end

-- `but` interprets colons in paths as selector separators. Callers refuse
-- those names instead of risking a different file or hunk.
local function file_arg(path: string): string
	return "zz:" .. path
end

-- Marked paths, else `picked` (the file selected before the fresh read).
-- nil ids mean every uncommitted change, and only when nothing was marked
-- and no file was picked. The third value refuses the action: a chosen
-- path is no longer uncommitted, so do not fall back to all files.
local function resolve_files(state: State, picked: string?, wanted: { [string]: boolean }): ({ string }?, string, string?)
	local any = false
	for path, on in wanted do
		if on then
			if string.find(path, ":", 1, true) then
				return nil, "", "Cannot safely target a file containing ':': " .. path .. ". Nothing was modified."
			end
			any = true
		end
	end
	if any then
		local ids: { string } = {}
		local seen: { [string]: boolean } = {}
		local stale: { string } = {}
		for _, row in state.rows do
			if row.kind == "file" and row.path and wanted[row.path] and not seen[row.path] then
				table.insert(ids, file_arg(row.path))
				seen[row.path] = true
			end
		end
		for path, _ in wanted do
			if not seen[path] then
				table.insert(stale, path)
				state.marked[path] = nil
			end
		end
		if #stale > 0 then
			local noun = if #stale == 1 then "Marked file no longer uncommitted: " else "Marked files no longer uncommitted: "
			return nil, "", noun .. table.concat(stale, ", ")
		end
		return ids, #ids .. (if #ids == 1 then " file" else " files"), nil
	end
	if picked then
		if string.find(picked, ":", 1, true) then
			return nil, "", "Cannot safely target a file containing ':': " .. picked .. ". Nothing was modified."
		end
		for _, row in state.rows do
			if row.kind == "file" and row.path == picked then
				return { file_arg(picked) }, picked, nil
			end
		end
		return nil, "", "Selected file no longer uncommitted: " .. picked
	end
	return nil, "all uncommitted changes", nil
end

-- Re-reads status before acting on specific files, so a poll that has not
-- landed yet cannot hide a mark that no longer matches.
local function fresh_files(state: State, picked: string?, use: ({ string }?, string) -> (), only_picked: boolean?)
	local wanted = if only_picked then {} else table.clone(state.marked)
	state.reads += 1
	read(state, "Checking changes…", { "status", "-f", "--json" }, function(data: any)
		if apply_status(state, data) == nil then
			notify(state, "error", "but status printed something other than a workspace")
			return
		end
		local ids, what, stale = resolve_files(state, picked, wanted)
		if stale ~= nil then
			notify(state, "warning", stale)
			return
		end
		use(ids, what)
	end)
end

local function with_branch(state: State, title: string, with_new: boolean, done: (string) -> ())
	local options = branch_options(state, with_new)
	if #options == 0 then
		notify(state, "warning", "No named branch is applied. Press n to create one.")
		return
	end
	pick(state, title, options, function(value: string)
		if value == NEW_BRANCH then
			prompt(state, "New branch name", "", done)
		else
			done(value)
		end
	end)
end

-- A fixed target (a board drop) skips the branch picker; NEW_BRANCH asks
-- for the new branch's name.
local function to_branch(state: State, target: string?, title: string, with_new: boolean, done: (string) -> ())
	if target == NEW_BRANCH then
		prompt(state, "New branch name", "", done)
	elseif target ~= nil and target ~= "" then
		done(target)
	else
		with_branch(state, title, with_new, done)
	end
end

local function append(list: { string }, more: { string }?): { string }
	local out = table.clone(list)
	for _, v in more or {} do
		table.insert(out, v)
	end
	return out
end

-- Status lists a stack's branches top-first. Rewriting the branch at index
-- i rebases every branch above it (1..i-1). Rename does not.
local function span_from(branches: { gb.Branch }, index: number, above: boolean): { gb.Branch }
	local out: { gb.Branch } = {}
	local from = if above then 1 else index
	for j = from, index do
		table.insert(out, branches[j])
	end
	return out
end

local function branch_span(model: gb.Model, name: string, above: boolean): { gb.Branch }?
	if name == "" then
		return nil
	end
	for _, stack in model.stacks do
		for i, b in stack.branches do
			if b.name == name then
				return span_from(stack.branches, i, above)
			end
		end
	end
	return nil
end

local function name_applied(model: gb.Model, name: string): boolean
	return branch_span(model, name, false) ~= nil
end

local function commit_span(model: gb.Model, id: string, above: boolean): { gb.Branch }?
	if id == "" then
		return nil
	end
	for _, stack in model.stacks do
		for i, b in stack.branches do
			for _, c in b.commits do
				if c.sha == id or c.changeId == id then
					return span_from(stack.branches, i, above)
				end
			end
		end
	end
	return nil
end

-- Names a rewrite would force-push. A missing target is included so a
-- stale or failed lookup cannot take the fast path.
local function pushed_labels(model: gb.Model, names: { string }, ids: { string }, above: boolean): { string }
	local out: { string } = {}
	local seen: { [string]: boolean } = {}
	local function add(label: string)
		if not seen[label] then
			seen[label] = true
			table.insert(out, label)
		end
	end
	local function consider(span: { gb.Branch }?, missing: string)
		if span == nil then
			add(missing)
			return
		end
		for _, b in span do
			if b.status ~= "completelyUnpushed" then
				add(if b.name ~= "" then b.name else gb.label(b))
			end
		end
	end
	for _, name in names do
		if name ~= "" then
			consider(branch_span(model, name, above), name)
		end
	end
	for _, id in ids do
		if id ~= "" then
			consider(commit_span(model, id, above), "a target commit")
		end
	end
	return out
end

local function force_line(labels: { string }, rename: boolean): string
	local who = table.concat(labels, ", ")
	local be = if #labels == 1 then " is" else " are"
	if rename then
		return who .. be .. " already pushed. The remote branch and any PR keep the old name."
	end
	return who .. be .. " already pushed and will need a force push."
end

local UNREAD = "Push state could not be read, so this is treated as already pushed and will need a force push."

local function warn_line(labels: { string }, unread: boolean, rename: boolean): string?
	if unread then
		return UNREAD
	end
	if #labels == 0 then
		return nil
	end
	return force_line(labels, rename)
end

-- Re-reads status immediately before the push decision. The mutation still
-- uses the captured changeId / branch name / zz: path, not a new selection.
-- The bump drops an older refresh. Escape cancellation lives in `read`.
local function fresh_labels(state: State, names: { string }, ids: { string }, above: boolean, use: ({ string }, boolean) -> (), new_branch: string?)
	state.reads += 1
	read(state, "Checking push state…", { "status", "-f", "--json" }, function(data: any)
		local model = gb.model(data)
		if model == nil then
			use({}, true)
			return
		end
		local targets = table.clone(names)
		if new_branch and name_applied(model, new_branch) then
			table.insert(targets, new_branch)
		end
		use(pushed_labels(model, targets, ids, above), false)
	end)
end

-- Unpushed: run `go`. Pushed, missing, or unreadable: danger confirm, then `go`.
-- `rename` checks only that branch; other rewrites also check branches stacked above.
local function guard_rewrite(state: State, title: string, body: { string }, names: { string }, ids: { string }, args: { string }, rename: boolean, go: () -> (), new_branch: string?)
	fresh_labels(state, names, ids, not rename, function(labels: { string }, unread: boolean)
		local line = warn_line(labels, unread, rename)
		if line == nil then
			go()
			return
		end
		local shown = table.clone(body)
		table.insert(shown, line)
		confirm(state, title, shown, args, true, go)
	end, new_branch)
end

-- Confirm a rewrite. Danger comes from a fresh status read, not the model
-- frozen while the picker was open. Enter on a non-danger confirm re-reads
-- again before mutating, so a push during the sheet cannot slip through.
local function confirm_rewrite(state: State, title: string, body: { string }, names: { string }, ids: { string }, args: { string }, go: () -> (), new_branch: string?)
	fresh_labels(state, names, ids, true, function(labels: { string }, unread: boolean)
		local line = warn_line(labels, unread, false)
		if line == nil then
			confirm(state, title, body, args, false, function()
				guard_rewrite(state, title, body, names, ids, args, false, go, new_branch)
			end)
			return
		end
		local shown = table.clone(body)
		table.insert(shown, line)
		confirm(state, title, shown, args, true, go)
	end, new_branch)
end



local function action_commit(state: State, target: string?)
	local marked = has_marks(state)
	local picked: string? = nil
	if not marked then
		local row = selected(state)
		if row and row.kind == "file" then
			picked = row.path
		end
	end
	local function start(ids: { string }?, what: string)
		local any = false
		for _, row in state.rows do
			any = any or row.kind == "file"
		end
		if not any then
			notify(state, "info", "Nothing to commit.")
			return
		end
		to_branch(state, target, "Commit " .. what .. " to", true, function(branch: string)
			prompt(state, "Commit message for " .. branch, "", function(message: string)
				if message == "" then
					notify(state, "warning", "Commit cancelled: empty message")
					return
				end
				local args = append({ "commit", "-b", branch, "-m", message }, ids)
				local function go()
					mutate(state, "Committing…", args, MUTATE_TIMEOUT_MS, "Committed " .. what .. " to " .. branch)
				end
				local title = "Commit onto " .. branch
				local body = {
					"Creates a commit on " .. branch .. ". Branches stacked above it are rebased.",
				}
				local function guard()
					guard_rewrite(state, title, body, { branch }, {}, args, false, go)
				end
				-- The loaded model can miss a branch created while the prompt was open.
				-- A failed read does not take the no-confirm path.
				read(state, "Checking push state…", { "status", "-f", "--json" }, function(data: any)
					local model = gb.model(data)
					if model == nil or name_applied(model, branch) then
						guard()
						return
					end
					go()
				end, function()
					guard()
				end)
			end)
		end)
	end
	if marked or picked ~= nil then
		fresh_files(state, picked, start)
	else
		start(nil, "all uncommitted changes")
	end
end

-- `but` 0.22.3: a `move` that fails with "Uncommitted files would be
-- overwritten by checkout" still drops the commit and those uncommitted
-- changes, and records no oplog entry (reproduced with the bare CLI). So a
-- move is refused while an uncommitted file shares a path with the change.
local function move_blocked(state: State, row: Row): boolean
	local moved: { [string]: boolean } = {}
	for _, r in state.rows do
		if r.kind == "cfile" and r.changeId == row.changeId and r.path and (row.kind == "commit" or r.path == row.path) then
			moved[r.path] = true
		end
	end
	local clash: { string } = {}
	for _, r in state.rows do
		if r.kind == "file" and r.path and moved[r.path] and not table.find(clash, r.path) then
			table.insert(clash, r.path)
		end
	end
	if #clash == 0 then
		return false
	end
	notify(state, "warning", table.concat(clash, ", ") .. " has uncommitted changes in a file this commit touches. "
		.. "Commit, absorb or discard them first: but 0.22.3 can lose the commit and those changes if the move fails. Nothing was modified.")
	return true
end

-- The same check on a fresh status read, just before the move runs: polling
-- pauses while a sheet is open, so the cached rows can miss an edit made
-- meanwhile. A failed read refuses the move.
local function fresh_move(state: State, row: Row, go: () -> ())
	state.reads += 1
	read(state, "Checking changes…", { "status", "-f", "--json" }, function(data: any)
		if apply_status(state, data) == nil then
			notify(state, "error", "but status printed something other than a workspace. Nothing was modified.")
			return
		end
		if not move_blocked(state, row) then
			go()
		end
	end)
end

-- The stable ref `but` takes for a commit or committed-file row, or nil and
-- the reason it can't be targeted.
local function change_ref(row: Row): (string?, string?)
	if row.kind == "cfile" and row.path and string.find(row.path, ":", 1, true) then
		return nil, "Cannot safely target a file containing ':': " .. row.path .. ". Nothing was modified."
	end
	local cid = row.changeId
	if cid == nil or cid == "" then
		return nil, "That commit has no stable id. Nothing was modified."
	end
	return (if row.kind == "cfile" and row.path then cid .. ":" .. row.path else cid), nil
end

local function diff_ref(row: Row): string?
	if row.kind == "branch" then
		return if row.branch ~= "" then row.branch else nil
	elseif row.kind == "file" then
		return if row.path and not string.find(row.path, ":", 1, true) then file_arg(row.path) else nil
	end
	local ref = change_ref(row)
	return ref
end

local function open_diff(state: State)
	local row = selected(state)
	if row == nil then
		return
	end
	local ref = diff_ref(row)
	local unnamed = row.kind == "branch" and (row.branch == nil or row.branch == "")
	if ref == nil and not unnamed then
		notify(state, "warning", "That row has no safe, stable diff target.")
		return
	end
	local d: DiffState = {
		cx = state.cx,
		root = state.root,
		id = ref or row.id,
		label = if row.kind == "commit" then row.id .. " " .. row.label else row.label,
		changes = nil,
		problem = nil,
		gen = 0,
		kind = row.kind,
		key = row.key,
		bind = diff_bind(row),
	}
	state.diff = d
	load_diff(d)
end

local function change_what(row: Row): string
	return if row.kind == "commit" then "commit " .. row.id .. " " .. row.label else row.label .. " (" .. row.id .. ")"
end

-- Moves the selected change to a branch. `but` 0.22 has no command that
-- only assigns an uncommitted file (`rub` is retired), so an uncommitted
-- file is amended into the branch's tip commit, or committed when the
-- branch has none; committed files and commits use `but move`.
local function action_move(state: State, target: string?)
	local row = selected(state)
	if row == nil or row.kind == "branch" then
		notify(state, "warning", "Select a file or commit to move.")
		return
	end
	if row.kind == "file" then
		local picked = if has_marks(state) then nil else row.path
		fresh_files(state, picked, function(ids: { string }?, what: string)
			if ids == nil then
				notify(state, "warning", "Select a file to move.")
				return
			end
			to_branch(state, target, "Move " .. what .. " to", false, function(branch: string)
				local empty = true
				for _, stack in (if state.model then state.model.stacks else {}) do
					for _, b in stack.branches do
						if b.name == branch and #b.commits > 0 then
							empty = false
						end
					end
				end
				if empty then
					prompt(state, branch .. " has no commits yet: message for a new commit", "", function(message: string)
						if message ~= "" then
							local args = append({ "commit", "-b", branch, "-m", message }, ids)
							guard_rewrite(state, "Commit onto " .. branch, {
								"Creates a commit on " .. branch .. ". Branches stacked above it are rebased.",
							}, { branch }, {}, args, false, function()
								mutate(state, "Committing…", args, MUTATE_TIMEOUT_MS, "Moved " .. what .. " to " .. branch)
							end)
						end
					end)
					return
				end
				local args = append({ "amend", "-t", branch }, ids)
				confirm_rewrite(state, "Move " .. what .. " into " .. branch, {
					"Amends the change into the newest commit on " .. branch .. ", rewriting that commit.",
					"but 0.22 has no way to assign an uncommitted change without committing it.",
				}, { branch }, {}, args, function()
					mutate(state, "Amending…", args, MUTATE_TIMEOUT_MS, "Moved " .. what .. " into " .. branch)
				end)
			end)
		end)
		return
	end
	local arg, why = change_ref(row)
	if arg == nil then
		notify(state, "warning", why :: string)
		return
	end
	local cid = row.changeId :: string
	local what = change_what(row)
	local source = row.branch or ""
	if move_blocked(state, row) then
		return
	end
	to_branch(state, target, "Move " .. what .. " to", false, function(branch: string)
		local args = { "move", arg, "-b", branch }
		local names: { string } = {}
		if target ~= NEW_BRANCH then
			table.insert(names, branch)
		end
		if source ~= "" then
			table.insert(names, source)
		end
		confirm_rewrite(state, "Move " .. what .. " to " .. branch, {
			"Rewrites both branches' history" .. (if row.kind == "cfile" then "; the file becomes a new commit on " .. branch .. "." else "."),
		}, names, { cid :: string }, args, function()
			fresh_move(state, row, function()
				mutate(state, "Moving…", args, MUTATE_TIMEOUT_MS, "Moved " .. what .. " to " .. branch)
			end)
		end, if target == NEW_BRANCH then branch else nil)
	end)
end

-- Branches a rewrite of these rows touches, for the push-state guard.
local function names_of(rows: { Row }): { string }
	local out: { string } = {}
	for _, r in rows do
		if r.branch and r.branch ~= "" and not table.find(out, r.branch) then
			table.insert(out, r.branch)
		end
	end
	return out
end

-- The selected commit or committed file and its stable ref, or nil after
-- saying why it can't be used.
local function held_change(state: State): (Row?, string?)
	local row = selected(state)
	if row == nil or (row.kind ~= "commit" and row.kind ~= "cfile") then
		notify(state, "warning", "Select a commit or a committed file.")
		return nil, nil
	end
	local ref, why = change_ref(row)
	if ref == nil then
		notify(state, "warning", why :: string)
		return nil, nil
	end
	return row, ref
end

-- Squashes the selected commit into `target`, keeping the target's message
-- (no editor opens), or moves the selected committed file's changes into it.
local function action_squash(state: State, target: Row)
	local row, ref = held_change(state)
	if row == nil or ref == nil then
		return
	end
	local tid = target.changeId
	if tid == nil or tid == "" or tid == row.changeId then
		notify(state, "warning", "Pick a different commit to squash into.")
		return
	end
	local args = { "squash", ref, "-t", tid }
	if row.kind == "commit" then
		table.insert(args, "--use-target-message")
	end
	local what = change_what(row)
	local verb = if row.kind == "commit" then "Squash " else "Move "
	confirm_rewrite(state, verb .. what .. " into " .. target.id, {
		if row.kind == "commit"
			then "The two commits become one with " .. target.id .. "'s message (" .. target.label .. ")."
			else "The file's changes leave their commit and join " .. target.id .. " " .. target.label .. ".",
	}, names_of({ row, target }), { row.changeId :: string, tid }, args, function()
		mutate(state, "Squashing…", args, MUTATE_TIMEOUT_MS, (if row.kind == "commit" then "Squashed " else "Moved ") .. what .. " into " .. target.id)
	end)
end

-- Moves the selected commit to sit directly above `target`, on its branch.
local function action_place_above(state: State, target: Row)
	local row, ref = held_change(state)
	if row == nil or ref == nil then
		return
	end
	local tid = target.changeId
	if move_blocked(state, row) then
		return
	end
	if row.kind ~= "commit" or tid == nil or tid == "" or tid == row.changeId then
		notify(state, "warning", "Pick a different commit to place it above.")
		return
	end
	local args = { "move", ref, "--above", tid }
	local what = change_what(row)
	local names = names_of({ row, target })
	confirm_rewrite(state, "Move " .. what .. " above " .. target.id, {
		"Reorders history on " .. table.concat(names, " and ") .. ".",
	}, names, { row.changeId :: string, tid }, args, function()
		fresh_move(state, row, function()
			mutate(state, "Moving…", args, MUTATE_TIMEOUT_MS, "Moved " .. what .. " above " .. target.id)
		end)
	end)
end

local function action_uncommit(state: State)
	local row, ref = held_change(state)
	if row == nil or ref == nil then
		return
	end
	local args = { "uncommit", ref }
	local what = change_what(row)
	confirm_rewrite(state, "Uncommit " .. what, {
		"Turns it back into uncommitted changes and rewrites " .. (row.branch or "its branch") .. ".",
	}, names_of({ row }), { row.changeId :: string }, args, function()
		mutate(state, "Uncommitting…", args, MUTATE_TIMEOUT_MS, "Uncommitted " .. what)
	end)
end

-- Amends the checked (or selected) uncommitted files into `target`, any
-- commit, not only a branch tip.
local function action_amend(state: State, target: Row)
	local tid, why = change_ref(target)
	if tid == nil then
		notify(state, "warning", why :: string)
		return
	end
	local row = selected(state)
	local picked = if not has_marks(state) and row and row.kind == "file" then row.path else nil
	if picked == nil and not has_marks(state) then
		notify(state, "warning", "Select or check the files to amend.")
		return
	end
	fresh_files(state, picked, function(ids: { string }?, what: string)
		if ids == nil then
			notify(state, "warning", "Select or check the files to amend.")
			return
		end
		local args = append({ "amend", "-t", tid }, ids)
		confirm_rewrite(state, "Amend " .. what .. " into " .. target.id, {
			"Rewrites commit " .. target.id .. " " .. target.label .. " and the commits above it.",
		}, names_of({ target }), { tid }, args, function()
			mutate(state, "Amending…", args, MUTATE_TIMEOUT_MS, "Amended " .. what .. " into " .. target.id)
		end)
	end)
end

-- Discards the checked or selected uncommitted files, or the selected
-- commit, committed file or branch. Always a danger confirm; `but undo`
-- brings it back.
local function action_discard(state: State, target: Row?)
	local row = target or selected(state)
	local marked = target == nil and has_marks(state)
	if marked or (row and row.kind == "file") then
		local picked = if marked or row == nil then nil else row.path
		fresh_files(state, picked, function(ids: { string }?, what: string)
			if ids == nil then
				notify(state, "warning", "Select or check the files to discard.")
				return
			end
			local args = append({ "discard" }, ids)
			confirm(state, "Discard " .. what, {
				"Deletes these uncommitted changes from the working tree. but undo restores them.",
			}, args, true, function()
				mutate(state, "Discarding…", args, MUTATE_TIMEOUT_MS, "Discarded " .. what)
			end)
		end, target ~= nil)
		return
	end
	if row and row.kind == "branch" then
		if row.branch == nil or row.branch == "" then
			notify(state, "warning", "That branch has no name to target. Nothing was modified.")
			return
		end
		local args = { "discard", row.branch }
		confirm(state, "Discard branch " .. row.label, {
			"Deletes the branch and its commits from the workspace. but undo restores it.",
		}, args, true, function()
			mutate(state, "Discarding…", args, MUTATE_TIMEOUT_MS, "Discarded branch " .. row.label)
		end)
		return
	end
	local change, ref = held_change(state)
	if change == nil or ref == nil then
		return
	end
	local args = { "discard", ref }
	local what = change_what(change)
	confirm(state, "Discard " .. what, {
		"Drops it from " .. (change.branch or "its branch") .. "; the commits above are rebased. but undo restores it.",
	}, args, true, function()
		mutate(state, "Discarding…", args, MUTATE_TIMEOUT_MS, "Discarded " .. what)
	end)
end

local function action_redo(state: State)
	confirm(state, "Redo the last undo", { "Re-applies the operation the last but undo reverted." }, { "redo" }, true, function()
		mutate(state, "Redoing…", { "redo" }, MUTATE_TIMEOUT_MS, "Redone")
	end)
end

-- Commit ids and file paths the dry-run would touch. JSON so a path
-- containing a newline cannot collide with another row.
local function absorb_sig(plan: any): ({ string }, { string }, string)
	local body: { string } = {}
	local ids: { string } = {}
	local rows: { { any } } = {}
	for _, c in (if type(plan) == "table" and type(plan.commits) == "table" then plan.commits else {}) do
		local files: { string } = {}
		for _, f in (if type(c.files) == "table" then c.files else {}) do
			table.insert(files, tostring(f.path))
		end
		local id = tostring(c.commit_id or "")
		local sorted = table.clone(files)
		table.sort(sorted)
		table.insert(rows, { id, sorted })
		table.insert(ids, id)
		table.insert(body, string.format("→ %s  %s  (%s)", string.sub(id, 1, 7), tostring(c.commit_summary or ""), tostring(c.reason_description or c.reason or "")))
		table.insert(body, "    " .. table.concat(files, ", "))
	end
	table.sort(rows, function(a, b)
		return a[1] < b[1]
	end)
	return body, ids, tern.json.encode(rows)
end

local function action_absorb(state: State)
	local row = selected(state)
	if row and row.kind == "file" and row.path and string.find(row.path, ":", 1, true) then
		notify(state, "warning", "Cannot safely target a file containing ':': " .. row.path .. ". Nothing was modified.")
		return
	end
	local source = if row and row.kind == "file" and row.path then file_arg(row.path) else nil
	local what = if row and row.kind == "file" then row.label else "all uncommitted changes"
	local args = { "absorb" }
	if source then
		table.insert(args, source)
	end
	local dry = append(args, { "--dry-run", "--json" })
	local function present(plan: any)
		local body, ids, sig = absorb_sig(plan)
		if #body == 0 then
			notify(state, "info", "Nothing to absorb.")
			return
		end
		confirm_rewrite(state, "Absorb " .. what, body, {}, ids, args, function()
			read(state, "Rechecking absorb…", dry, function(again: any)
				local next_body, _, next_sig = absorb_sig(again)
				if next_sig ~= sig then
					if #next_body == 0 then
						notify(state, "warning", "Absorb plan changed: nothing left to absorb. Nothing was modified.")
						return
					end
					notify(state, "warning", "Absorb plan changed. Nothing was modified.")
					present(again)
					return
				end
				mutate(state, "Absorbing…", args, MUTATE_TIMEOUT_MS, "Absorbed " .. what)
			end)
		end)
	end
	read(state, "Planning absorb…", dry, present)
end

local function action_reword(state: State)
	local row = selected(state)
	if row and row.kind == "commit" then
		local arg = row.changeId
		if arg == nil or arg == "" then
			notify(state, "warning", "That commit has no stable id. Nothing was modified.")
			return
		end
		prompt(state, "Reword " .. row.id, row.message or row.label, function(message: string)
			if message ~= "" and message ~= row.message then
				local args = { "reword", arg, "-m", message }
				guard_rewrite(state, "Reword " .. row.id, {}, {}, { arg }, args, false, function()
					mutate(state, "Rewording…", args, MUTATE_TIMEOUT_MS, "Reworded " .. row.id)
				end)
			end
		end)
	elseif row and row.kind == "branch" and row.branch ~= "" then
		prompt(state, "Rename branch " .. row.id, row.id, function(name: string)
			if name ~= "" and name ~= row.id then
				local args = { "reword", row.id, "-m", name }
				guard_rewrite(state, "Rename " .. row.id, {}, { row.id }, {}, args, true, function()
					mutate(state, "Renaming…", args, MUTATE_TIMEOUT_MS, "Renamed " .. row.id .. " to " .. name)
				end)
			end
		end)
	else
		notify(state, "warning", "Select a commit to reword or a branch to rename.")
	end
end

local function action_new_branch(state: State, stacked: boolean)
	local above = if stacked then branch_of(state, selected(state)) else nil
	if stacked and above == nil then
		notify(state, "warning", "Select a branch to stack the new one on.")
		return
	end
	prompt(state, if above then "New branch stacked on " .. above else "New parallel branch", "", function(name: string)
		if name == "" then
			return
		end
		local args = { "branch", "new", name }
		if above then
			table.insert(args, "-A")
			table.insert(args, above)
		end
		mutate(state, "Creating branch…", args, MUTATE_TIMEOUT_MS, "Created " .. name)
	end)
end

local function same_ids(a: { string }, b: { string }): boolean
	if #a ~= #b then
		return false
	end
	for i, v in a do
		if v ~= b[i] then
			return false
		end
	end
	return true
end

local function stack_change_ids(stack: gb.Stack): { string }
	local out: { string } = {}
	for _, b in stack.branches do
		for _, c in b.commits do
			table.insert(out, c.changeId)
		end
	end
	return out
end

-- `id` is a snapshot CLI id (anonymous branch or stack). `but` has no
-- stable handle for those, so re-read status and abort if it now labels
-- different commits.
local function guard_cli(state: State, id: string, commits: { string }, go: () -> ())
	read(state, "Checking target…", { "status", "-f", "--json" }, function(data: any)
		local model = gb.model(data)
		if model == nil then
			notify(state, "error", "but status printed something other than a workspace")
			return
		end
		for _, stack in model.stacks do
			if stack.id == id then
				if same_ids(stack_change_ids(stack), commits) then
					go()
				else
					notify(state, "warning", "That stack changed. Nothing was modified.")
				end
				return
			end
			for _, b in stack.branches do
				if b.id == id then
					local now: { string } = {}
					for _, c in b.commits do
						table.insert(now, c.changeId)
					end
					if same_ids(now, commits) then
						go()
					else
						notify(state, "warning", "That branch changed. Nothing was modified.")
					end
					return
				end
			end
		end
		notify(state, "warning", "That target changed. Nothing was modified.")
	end)
end

local function action_unapply(state: State)
	local row = selected(state)
	local model = state.model
	if row == nil or row.lane == 1 or model == nil then
		notify(state, "warning", "Select a branch lane to unapply.")
		return
	end
	local stack = model.stacks[row.lane - 1]
	local target = branch_of(state, row) or stack.id
	if target == "" then
		target = stack.id
	end
	local named = false
	for _, b in stack.branches do
		if b.name ~= "" and b.name == target then
			named = true
		end
	end
	local watched: { string }? = nil
	if not named then
		if target == stack.id then
			watched = stack_change_ids(stack)
		else
			for _, b in stack.branches do
				if b.id == target then
					local ids: { string } = {}
					for _, c in b.commits do
						table.insert(ids, c.changeId)
					end
					watched = ids
				end
			end
		end
	end
	local args = { "unapply", target }
	confirm(state, "Unapply " .. target, {
		"Removes the whole stack (" .. #stack.branches .. " branch" .. (if #stack.branches == 1 then "" else "es") .. ") from the working directory.",
		"Its commits stay on the branch; press a to apply it again.",
	}, args, true, function()
		local function go()
			mutate(state, "Unapplying…", args, MUTATE_TIMEOUT_MS, "Unapplied " .. target)
		end
		if watched then
			guard_cli(state, target, watched, go)
		else
			go()
		end
	end)
end

local function action_apply(state: State)
	read(state, "Listing branches…", { "branch", "list", "--local", "--empty", "--all", "--no-check", "--json" }, function(data: any)
		local options: { Option } = {}
		for _, b in (if type(data) == "table" and type(data.branches) == "table" then data.branches else {}) do
			if type(b.name) == "string" then
				local ahead = if type(b.commitsAhead) == "number" then b.commitsAhead .. " ahead" else nil
				table.insert(options, { label = b.name, value = b.name, detail = ahead })
			end
		end
		if #options == 0 then
			notify(state, "info", "No unapplied local branches.")
			return
		end
		pick(state, "Apply branch", options, function(name: string)
			mutate(state, "Applying…", { "apply", name }, MUTATE_TIMEOUT_MS, "Applied " .. name)
		end)
	end)
end

local function op_text(e: any): (string, string)
	local d = if type(e) == "table" and type(e.details) == "table" then e.details else {}
	local when = if type(e.createdAt) == "number" then os.date("%m-%d %H:%M:%S", e.createdAt // 1000) else ""
	local title = tostring(d.body or d.title or d.operation or "?")
	return when, title
end

local function action_undo(state: State)
	read(state, "Reading oplog…", { "oplog", "list", "--json" }, function(data: any)
		local last = if type(data) == "table" then data[1] else nil
		if type(last) ~= "table" or type(last.id) ~= "string" then
			notify(state, "info", "Nothing to undo.")
			return
		end
		local id = last.id
		local when, title = op_text(last)
		confirm(state, "Undo the last operation", { title .. "  (" .. when .. ")" }, { "undo" }, true, function()
			-- `but undo` always reverts the current head. Restoring this
			-- snapshot instead would also revert anything that landed after it.
			read(state, "Checking oplog…", { "oplog", "list", "--json" }, function(fresh: any)
				local head = if type(fresh) == "table" then fresh[1] else nil
				if type(head) ~= "table" or head.id ~= id then
					notify(state, "warning", "A newer operation landed. Nothing was modified.")
					refresh(state)
					return
				end
				mutate(state, "Undoing…", { "undo" }, MUTATE_TIMEOUT_MS, "Undid " .. title)
			end)
		end)
	end)
end

local function action_oplog(state: State)
	read(state, "Reading oplog…", { "oplog", "list", "--json" }, function(data: any)
		local options: { Option } = {}
		for _, e in (if type(data) == "table" then data else {}) do
			if type(e.id) == "string" then
				local when, title = op_text(e)
				table.insert(options, { label = title, value = e.id, detail = string.sub(e.id, 1, 7) .. "  " .. when })
			end
		end
		if #options == 0 then
			notify(state, "info", "The oplog is empty.")
			return
		end
		pick(state, "Restore the workspace to before…", options, function(sha: string)
			local label = sha
			for _, o in options do
				if o.value == sha then
					label = o.label
				end
			end
			local args = { "oplog", "restore", sha }
			confirm(state, "Restore to before: " .. label, {
				"Branches, commits and uncommitted changes go back to the snapshot " .. string.sub(sha, 1, 7) .. ".",
				"The restore is itself recorded: z undoes it.",
			}, args, true, function()
				mutate(state, "Restoring…", args, MUTATE_TIMEOUT_MS, "Restored to " .. string.sub(sha, 1, 7))
			end)
		end)
	end)
end

-- `but push <branch> --dry-run` only reports that branch (but 0.22.3). The
-- real push also pushes every branch below it, so the check is untargeted
-- and then filtered to this branch and those below it. The span comes from
-- a fresh status read (the block model can be a poll behind; status lists a
-- stack top-first). A branch omitted from the dry-run has nothing to push.
-- A missing field, an unnamed branch we cannot match, a missing branch, or
-- a failed read is a force-push.
local function names_at_and_below(model: gb.Model, name: string): { string }?
	for _, stack in model.stacks do
		for i, b in stack.branches do
			if b.name == name then
				local out: { string } = {}
				for j = i, #stack.branches do
					local below = stack.branches[j]
					if below.name == "" then
						return nil
					end
					table.insert(out, below.name)
				end
				return out
			end
		end
	end
	return nil
end

local function force_required(data: any, names: { string }): boolean
	if type(data) ~= "table" or type(data.branches) ~= "table" then
		return true
	end
	local by_name: { [string]: any } = {}
	for _, b in data.branches do
		if type(b) == "table" and type(b.branchName) == "string" then
			by_name[b.branchName] = b
		end
	end
	for _, name in names do
		local b = by_name[name]
		if b ~= nil and (type(b.requiresForce) ~= "boolean" or b.requiresForce) then
			return true
		end
	end
	return false
end

local function confirm_publish(state: State, title: string, body: { string }, args: { string }, branch: string, label: string, done: string)
	-- Fresh topology plus the full dry-run; `use(force)` gets true when the
	-- branch or one below it would force-push, or when either read fails.
	local function check(use: (boolean) -> ())
		read(state, "Checking push…", { "status", "-f", "--json" }, function(status: any)
			local model = gb.model(status)
			local names = if model then names_at_and_below(model, branch) else nil
			if names == nil then
				use(true)
				return
			end
			read(state, "Checking push…", { "push", "--dry-run", "--json" }, function(data: any)
				use(force_required(data, names :: { string }))
			end, function()
				use(true)
			end)
		end, function()
			use(true)
		end)
	end
	local function show(force: boolean)
		local shown = table.clone(body)
		if force then
			table.insert(shown, "This will force-push.")
		end
		confirm(state, title, shown, args, force, function()
			if force then
				mutate(state, label, args, NETWORK_TIMEOUT_MS, done)
				return
			end
			-- The sheet may have sat open while the branch was rewritten
			-- elsewhere: check again, and ask for `y` if it now force-pushes.
			check(function(now: boolean)
				if now then
					show(true)
				else
					mutate(state, label, args, NETWORK_TIMEOUT_MS, done)
				end
			end)
		end)
	end
	check(show)
end

-- Push, PR and land act on the selected branch (or its lane's top branch).
local function remote_action(state: State, kind: string)
	local branch = branch_of(state, selected(state))
	if branch == nil or branch == "" then
		notify(state, "warning", "Select a named branch.")
		return
	end
	if kind == "push" then
		confirm_publish(state, "Push " .. branch, { "Pushes " .. branch .. " and the branches below it in its stack to the remote." }, { "push", branch }, branch, "Pushing…", "Pushed " .. branch)
	elseif kind == "pr" then
		confirm_publish(state, "Open a pull request for " .. branch, {
			"Pushes the branch (and those below it), then opens the review on the forge.",
			"Title and description come from the commits (-t).",
		}, { "pr", "new", branch, "-t" }, branch, "Opening PR…", "Opened a PR for " .. branch)
	else
		local args = { "land", branch, "--yes" }
		confirm(state, "Land " .. branch .. " on the target branch", {
			"Updates the target branch directly, without a pull request, and pushes it.",
			"This is not easily reversible.",
		}, args, true, function()
			mutate(state, "Landing…", args, NETWORK_TIMEOUT_MS, "Landed " .. branch)
		end)
	end
end

-- `but pull` rebases every applied branch onto the updated target, so it
-- confirms as a rewrite. `--check` first says whether there is anything to
-- take; a reply missing any field this relies on stops the pull instead of
-- confirming blind. Pressing y runs `--check` again: if anything changed
-- while the sheet was open, the sheet reopens with the new facts instead of
-- pulling.
type PullPlan = { base: string, count: number, conflicted: { string }, worktree: boolean, upToDate: boolean, snapshot: string }

local function pull_check(state: State, use: (PullPlan) -> ())
	read(state, "Checking upstream…", { "pull", "--check", "--json" }, function(data: any)
		local d = if type(data) == "table" then data else {}
		local up = d.upstreamCommits
		local base = if type(d.baseBranch) == "table" then d.baseBranch else {}
		local n = if type(up) == "table" and type(up.count) == "number" then up.count else nil
		if type(d.upToDate) ~= "boolean" or n == nil or type(base.name) ~= "string" or type(base.remoteName) ~= "string"
			or type(base.baseSha) ~= "string" or type(base.currentSha) ~= "string"
			or type(d.hasWorktreeConflicts) ~= "boolean" or type(d.branchStatuses) ~= "table" then
			notify(state, "error", "but pull --check returned an unexpected reply; not pulling.")
			return
		end
		local conflicted: { string } = {}
		local branches: { string } = {}
		for _, b in d.branchStatuses do
			-- every record must name its branch and state, or the warning could miss one
			if type(b) ~= "table" or type(b.name) ~= "string" or type(b.status) ~= "string"
				or (b.rebasable ~= nil and type(b.rebasable) ~= "boolean") then
				notify(state, "error", "but pull --check returned an unexpected branch status; not pulling.")
				return
			end
			table.insert(branches, tern.json.encode({ b.name, b.status, b.rebasable }))
			if b.status == "conflicted" then
				table.insert(conflicted, b.name)
			end
		end
		table.sort(conflicted)
		table.sort(branches)
		local snapshot = tern.json.encode({ base.name, base.remoteName, base.baseSha, base.currentSha, branches })
		use({ base = base.name, count = n, conflicted = conflicted, worktree = d.hasWorktreeConflicts, upToDate = d.upToDate, snapshot = snapshot })
	end)
end

local function same_plan(a: PullPlan, b: PullPlan): boolean
	return a.snapshot == b.snapshot and a.count == b.count and a.worktree == b.worktree and a.upToDate == b.upToDate
end

local function pull_result(plan: PullPlan, result: any, workspace: any): (string, string?)
	local base = plan.base
	local status = if type(result) == "table" then result.status else nil
	local list = if type(result) == "table" then result.conflicts else nil
	local names: { string } = {}
	for _, c in (if type(list) == "table" then list else {}) do
		table.insert(names, if type(c) == "table" and type(c.branch) == "string" then c.branch else "?")
	end
	local parts: { string } = {}
	if #names > 0 then
		table.insert(parts, "conflicts in " .. table.concat(names, ", "))
	elseif status == "completed_with_conflicts" then
		table.insert(parts, "conflicts but did not name (check the lanes)")
	elseif status ~= "completed" and status ~= "up_to_date" then
		table.insert(parts, "an unconfirmed result (but reported status " .. tostring(status) .. "; check the lanes)")
	end
	local files = if type(workspace) == "table" then workspace.conflictedFiles else nil
	if type(workspace) ~= "table" or (files ~= nil and type(files) ~= "table") then
		table.insert(parts, "an unconfirmed worktree status (check the Uncommitted lane)")
	elseif files ~= nil then
		local unresolved: { string } = {}
		local valid = true
		for _, path in files do
			if type(path) == "string" then
				table.insert(unresolved, path)
			else
				valid = false
			end
		end
		if not valid then
			table.insert(parts, "an unconfirmed worktree status (check the Uncommitted lane)")
		elseif #unresolved > 0 then
			table.insert(parts, "unresolved file conflicts in " .. table.concat(unresolved, ", "))
		end
	end
	local prefix = if status == "up_to_date" then "Already up to date with " .. base else "Pulled " .. base
	if #parts > 0 then
		return prefix .. " with " .. table.concat(parts, " and ") .. (if status == "up_to_date" then "." else ". z undoes the pull."), "warning"
	end
	return prefix .. (if status == "up_to_date" then "." else ""), if status == "up_to_date" then "info" else nil
end

local function confirm_pull(state: State, plan: PullPlan, changed: boolean)
	if plan.upToDate then
		notify(state, "info", "Already up to date with " .. plan.base .. ".")
		return
	end
	local body: { string } = {}
	if changed then
		table.insert(body, "Upstream or your workspace changed while this was open; this is the new picture.")
	end
	table.insert(body, plan.count .. " new commit(s) on " .. plan.base .. ".")
	table.insert(body, "Fetches, then rebases every applied branch onto it.")
	if #plan.conflicted > 0 then
		table.insert(body, "Will conflict: " .. table.concat(plan.conflicted, ", ") .. ". Their commits become conflicted; z undoes the pull.")
	end
	if plan.worktree then
		table.insert(body, "Uncommitted changes conflict with upstream.")
	end
	confirm(state, "Pull " .. plan.base, body, { "pull" }, true, function()
		pull_check(state, function(now: PullPlan)
			if not same_plan(plan, now) then
				confirm_pull(state, now, true)
				return
			end
			mutate(state, "Pulling…", { "pull" }, NETWORK_TIMEOUT_MS, function(result: any, workspace: any): (string, string?)
				return pull_result(now, result, workspace)
			end)
		end)
	end)
end

local function action_pull(state: State)
	pull_check(state, function(plan: PullPlan)
		confirm_pull(state, plan, false)
	end)
end

local HELP: { { string } } = {
	{ "↑↓ / j k, ← → / h l", "move; jump between lanes" },
	{ "⏎", "show the diff of a file, commit or branch here; esc goes back" },
	{ "space", "mark an uncommitted file (c, m act on marks)" },
	{ "c", "commit marked/selected files (or all) to a branch" },
	{ "m", "move a file or commit to another branch" },
	{ "A", "absorb the selected file (or everything), after a dry run" },
	{ "e", "reword a commit, rename a branch" },
	{ "n / N", "new parallel branch / new branch stacked on the selected one" },
	{ "a / u", "apply a branch / unapply the selected stack" },
	{ "z / O", "undo the last operation / restore from the oplog" },
	{ "p / P / L", "push / open a PR for / land the selected branch" },
	{ "f", "pull: rebase applied branches onto the updated target" },
	{ "v / 1-5", "switch view: lanes, graph, stacks, branches, board (or click a tab)" },
	{ "r", "refresh now (it also polls every 5 s)" },
}

-- The sheet an action shows at the top of the workspace, or nil.
local function sheet_view(state: State): Node?
	local sheet = state.sheet
	if sheet == nil then
		return nil
	end
	local children: { Node } = {}
	local foot = ""
	if sheet.kind == "prompt" then
		local lines: { Node } = {}
		local text = (sheet.text or "") .. "▌"
		for line in string.gmatch(text .. "\n", "(.-)\n") do
			table.insert(lines, ui.text({ ui.span(line, "mono") }))
		end
		table.insert(children, ui.lines(lines))
		foot = "⏎ ok  ⇧⏎ new line  esc cancel"
	elseif sheet.kind == "confirm" then
		for _, line in sheet.body or {} do
			table.insert(children, ui.text({ ui.span(line, "") }))
		end
		table.insert(children, ui.code(sheet.command or "", "sh"))
		foot = "y confirm  n/q/esc cancel"
	elseif sheet.kind == "pick" then
		local items: { Item } = {}
		for i, o in sheet.options or {} do
			table.insert(items, { label = { ui.span(if i <= 9 then i .. "  " else "   ", "muted num"), o.label }, value = o.detail })
		end
		local list = ui.list(items)
		local kids = list.c or {}
		for i in sheet.options or {} do
			local p: { [string]: any } = kids[i].p or {}
			p.key = "o" .. i
			kids[i].p = p
		end
		local lp: { [string]: any } = list.p or {}
		lp.key = "opts"
		lp.selected = "main.sheet.opts.o" .. (sheet.index or 1)
		lp.max = { lines = picker_lines(state) }
		list.p = lp
		table.insert(children, list)
		foot = PICK_FOOT
	else
		local rows: { { Cell } } = {}
		for _, h in HELP do
			table.insert(rows, { ui.span(h[1], "mono accent"), h[2] })
		end
		table.insert(children, ui.table({ { id = "key", head = "Key" }, { id = "does", head = "Does", grow = 1 } }, rows))
		foot = "esc/q close"
	end
	-- A pick list holds keyboard focus and can keep esc from reaching the block,
	-- so every sheet also closes by click. The button and the key hints share
	-- one line directly under the title: main never scrolls, so in a short
	-- pane the bottom of the sheet clips first and Cancel must not be there.
	table.insert(children, 1, el("bp-sheet-foot", {
		ui.el("button", {
			class = "bp-btn",
			text = if sheet.kind == "help" then "Close" else "Cancel",
			actions = { click = "cancel" },
		}, {}),
		sp(foot, "bp-hint"),
	}))
	local card = ui.card({ ui.span(sheet.title, "strong") }, children)
	local p: { [string]: any } = card.p or {}
	p.key = "sheet"
	p.tone = if sheet.danger then "error" elseif sheet.kind == "confirm" then "warning" else "accent"
	card.p = p
	return card
end

-- Closes the sheet, then runs its continuation (which may open another).
-- Polling resumes once no sheet and no read/mutation is in flight.
local function finish_sheet(state: State, value: string?)
	local sheet = state.sheet
	state.sheet = nil
	if sheet and value ~= nil and sheet.done then
		sheet.done(value)
	end
	if state.sheet == nil and state.busy == nil then
		refresh(state)
	end
end

local function sheet_key(state: State, key: Key)
	local sheet = state.sheet :: Sheet
	if key.name == "escape" then
		finish_sheet(state, nil)
		return
	end
	-- A paste is text, not a keypress: confirm and pick must ignore it.
	-- Those sheets also ignore shift/ctrl/alt/meta. A prompt still takes paste and shift+enter.
	local chord = key.ctrl or key.alt or key.meta
	if sheet.kind ~= "prompt" and (chord or key.shift or key.name == "paste") then
		return
	end
	if chord and key.name ~= "paste" then
		return
	end
	local k = key.text or key.name
	-- A plain q closes every sheet but a prompt, where q is text. It comes after
	-- the guard above, so a pasted "q" or a modified q never closes a sheet.
	if sheet.kind ~= "prompt" and k == "q" then
		finish_sheet(state, nil)
		return
	end
	if sheet.kind == "prompt" then
		local text = sheet.text or ""
		if key.name == "enter" and key.shift then
			sheet.text = text .. "\n"
		elseif key.name == "enter" then
			finish_sheet(state, (string.gsub(string.gsub(text, "^%s+", ""), "%s+$", "")))
		elseif key.name == "backspace" then
			local last = utf8.offset(text, -1)
			sheet.text = if last then string.sub(text, 1, last - 1) else ""
		elseif key.name == "paste" or key.text then
			sheet.text = text .. (key.text or "")
		end
	elseif sheet.kind == "confirm" then
		-- Enter confirms only what is easy to take back.
		if k == "y" or k == "Y" or (key.name == "enter" and not sheet.danger) then
			finish_sheet(state, "yes")
		elseif k == "n" or k == "N" then
			finish_sheet(state, nil)
		end
	elseif sheet.kind == "pick" then
		local options = sheet.options or {}
		local i = sheet.index or 1
		local n = tonumber(k)
		if key.name == "down" or k == "j" then
			sheet.index = math.min(#options, i + 1)
		elseif key.name == "up" or k == "k" then
			sheet.index = math.max(1, i - 1)
		elseif key.name == "enter" and options[i] then
			finish_sheet(state, options[i].value)
		elseif n and options[n] then
			finish_sheet(state, options[n].value)
		end
	end
end

-- Board: the GitButler desktop layout. Blocks get no drag and drop, so the
-- GUI's drag is select-then-drop: the selected card, or the checked files,
-- is what moves, and every card that can take it shows drop buttons. Cards
-- are one row (ROW px with the 2px column gap).
local BOARD_TOOLS = LH + 22 -- toolbar buttons, padding, the gap below
local BOARD_ACTIONS = {
	{ "Commit", "do=c" }, { "Absorb", "do=A" }, { "New branch", "do=n" }, { "Pull", "do=f" },
	{ "Undo", "do=z" }, { "Redo", "do=redo" }, { "Oplog", "do=O" }, { "Discard…", "do=discard" },
}
local COL_CHROME = 3 + 16 + ROW

local function held_key(state: State): string
	local paths: { string } = {}
	for path, on in state.marked do
		if on then
			table.insert(paths, path)
		end
	end
	table.sort(paths)
	return if #paths > 0 then key_of("marks", table.concat(paths, "\0")) else state.cursor or ""
end

local function board_view(state: State, model: gb.Model): Node
	local row = selected(state)
	local held = if has_marks(state) or (row ~= nil and row.kind == "file") then "files" elseif row then row.kind else ""
	local source = held_key(state)
	local function button(label: string, act: string, class: string?): Node
		return ui.el("button", { class = "bp-btn" .. (if class then " " .. class else ""), text = label, actions = { click = act } }, {})
	end
	local function drop(label: string, op: string, key: string): Node
		return button(label, "drop=" .. op .. ":" .. source .. ":" .. key, "bp-drop")
	end
	-- Tern shows a menu item's action id as its label, so items are plain
	-- labels (MENU maps them); the event's node id names the card's row.
	local function card(r: Row, class: string, kids: { Node }, menu: { string }): Node
		local node = pickable(state, r, "bp-card " .. class, kids)
		local p: { [string]: any } = node.p or {}
		p.actions.menu = menu
		node.p = p
		return node
	end
	local function file_card(r: Row): Node
		return card(r, "bp-fcard", {
			ui.el("input", { type = "checkbox", name = "mark", value = r.path or "", checked = r.path ~= nil and state.marked[r.path] == true }, {}),
			sp(letter(r.change or ""), "bp-ch bp-ch-" .. letter(r.change or "")),
			sp(r.label, "bp-path"),
			sp(r.id, "bp-id"),
		}, { "Commit…", "Move…", "Absorb", "Discard…" })
	end
	local function branch_card(r: Row): Node
		local b = branch_of_row(model, r)
		local named = r.branch ~= nil and r.branch ~= ""
		local tail: { Node } = {}
		if named and held == "files" then
			table.insert(tail, drop("Commit here", "commit", r.key))
		elseif named and (held == "cfile" or (held == "commit" and row ~= nil and row.bkey ~= r.key)) then
			table.insert(tail, drop("Move here", "move", r.key))
		else
			if b and b.review then
				table.insert(tail, ui.badge("PR " .. b.review, "info"))
			end
			table.insert(tail, status_badge(b))
		end
		return card(r, "bp-bcard", { sp("⎇ " .. r.label, "bp-bh-n"), el("bp-badges bp-end", tail) }, {
			"Push", "Create PR", "Rename…", "New stacked branch…", "Unapply", "Land", "Discard branch…",
		})
	end
	local function commit_card(r: Row): Node
		local c = commit_of_row(model, r)
		local kids: { Node } = { sp(r.label, "bp-t"), sp(r.id, "bp-id") }
		if c and c.conflicted then
			table.insert(kids, ui.badge("conflicted", "error"))
		end
		local other = row ~= nil and row.changeId ~= r.changeId
		local tail: { Node } = {}
		if held == "files" then
			table.insert(tail, drop("Amend", "amend", r.key))
		elseif held == "commit" and other then
			table.insert(tail, drop("Squash", "squash", r.key))
			table.insert(tail, drop("Above", "above", r.key))
		elseif held == "cfile" and other then
			table.insert(tail, drop("Move in", "squash", r.key))
		elseif c then
			table.insert(tail, sp(#c.files .. (if #c.files == 1 then " file" else " files"), "bp-dim"))
		end
		table.insert(kids, el("bp-badges bp-end", tail))
		return card(r, "bp-ccard", kids, { "Reword…", "Move…", "Uncommit", "Discard…" })
	end
	local function cfile_card(r: Row): Node
		return card(r, "bp-cfcard", {
			sp(letter(r.change or ""), "bp-ch bp-ch-" .. letter(r.change or "")),
			sp(r.label, "bp-path"),
			sp(r.id, "bp-id"),
		}, { "Move…", "Uncommit", "Discard…" })
	end
	local function render(r: Row): Node
		if r.kind == "branch" then
			return branch_card(r)
		elseif r.kind == "commit" then
			return commit_card(r)
		elseif r.kind == "cfile" then
			return cfile_card(r)
		end
		return file_card(r)
	end

	-- Columns wrap (CSS) about every 34 cells and wrapped rows share the height.
	local per_line = math.max(1, state.cx.cols // 34)
	local tool_labels = { "←", "→" }
	for _, action in BOARD_ACTIONS do
		table.insert(tool_labels, action[1])
	end
	local budget = list_px(state) + HEAD + BODY - BOARD_TOOLS - (control_rows(state, tool_labels) - 1) * ROW
	local col_lines = math.min(math.ceil((#state.lanes + 1) / per_line), math.max(1, (budget + 8) // (COL_CHROME + 3 * ROW + 8)))
	local capacity = per_line * col_lines
	local anchor = state.rows[index_of(state, state.viewport)]
	local lane = if anchor then anchor.lane else tonumber(string.match(state.viewport or "", "^L(%d+)$")) or 1
	local first = math.clamp(lane - capacity // 2, 1, math.max(1, #state.lanes + 2 - capacity))
	local last = math.min(#state.lanes + 1, first + capacity - 1)
	local room = math.max(0, math.floor(((budget - (col_lines - 1) * 8) / col_lines - COL_CHROME) / ROW))
	local function column(lane: number, head: { Node }, rows: { Row }, empty: string): Node
		local lo, hi, up, down = window(rows, state.viewport, room)
		if room > 0 and hi < #rows and not down then
			table.insert(head, 1, button("↓", "browse=" .. rows[hi + 1].key))
		end
		if room > 0 and lo > 1 and not up then
			table.insert(head, 1, button("↑", "browse=" .. rows[lo - 1].key))
		end
		local kids: { Node } = { el("bp-colh", head) }
		if up then
			table.insert(kids, button("↑ " .. (lo - 1) .. " more", "browse=" .. rows[lo - 1].key))
		end
		for i = lo, hi do
			table.insert(kids, render(rows[i]))
		end
		if down then
			table.insert(kids, button("↓ " .. (#rows - hi) .. " more", "browse=" .. rows[hi + 1].key))
		end
		if #rows == 0 and room > 0 then
			table.insert(kids, sp(empty, "bp-dim"))
		end
		return el("bp-sc bp-bcol " .. lane_class(lane), kids, { key = "L" .. lane })
	end

	local cols: { Node } = {}
	local unc = state.lanes[1] or {}
	local uhead: { Node } = { sp("Uncommitted", "bp-lh-n"), sp(tostring(#unc), "bp-warn") }
	if held == "commit" or held == "cfile" then
		table.insert(uhead, el("bp-end", { drop("Uncommit", "uncommit", "") }))
	end
	if first == 1 then
		table.insert(cols, column(1, uhead, unc, "No uncommitted changes."))
	end
	for lane = math.max(2, first), math.min(last, #state.lanes) do
		local stack = model.stacks[lane - 1]
		local rows: { Row } = {}
		for _, r in state.lanes[lane] do
			if walkable(state, r) then
				table.insert(rows, r)
			end
		end
		local top = stack and stack.branches[1]
		local head: { Node } = { sp(if top then gb.label(top) else (if stack then stack.id else "?"), "bp-lh-n") }
		if stack and #stack.branches > 1 then
			table.insert(head, sp("stack of " .. #stack.branches, "bp-dim"))
		end
		table.insert(cols, column(lane, head, rows, "No commits yet."))
	end
	if last > #state.lanes then
		local new: { Node } = { el("bp-colh", { sp("New branch", "bp-lh-n") }) }
		if room > 0 then
			if held == "files" then
				table.insert(new, drop("Commit to a new branch", "newcommit", ""))
			elseif held == "commit" or held == "cfile" then
				table.insert(new, drop("Move to a new branch", "newmove", ""))
			else
				table.insert(new, button("+ Create branch", "do=n"))
			end
		end
		table.insert(cols, el("bp-sc bp-bcol bp-newcol", new, { key = "new" }))
	end

	local tools = {
		button("←", "browse=L" .. math.max(1, first - 1)),
		button("→", "browse=L" .. math.min(#state.lanes + 1, last + 1)),
	}
	for _, action in BOARD_ACTIONS do
		table.insert(tools, button(action[1], action[2]))
	end
	return ui.col({ el("bp-tools", tools, { key = "tools" }), el("bp-cols", cols, { key = "cols" }) })
end

local ACTIONS: { [string]: (State) -> () } = {
	c = action_commit,
	m = action_move,
	A = action_absorb,
	e = action_reword,
	n = function(state: State)
		action_new_branch(state, false)
	end,
	N = function(state: State)
		action_new_branch(state, true)
	end,
	a = action_apply,
	u = action_unapply,
	z = action_undo,
	O = action_oplog,
	p = function(state: State)
		remote_action(state, "push")
	end,
	P = function(state: State)
		remote_action(state, "pr")
	end,
	L = function(state: State)
		remote_action(state, "land")
	end,
	f = action_pull,
	["?"] = function(state: State)
		state.sheet = { kind = "help", title = "GitButler keys" }
	end,
}

-- Actions reachable from outside the block (palette commands and the
-- user's `keybinds` send these as `action` events): ACTIONS and OPS, by name.
local NAMED: { [string]: string } = {
	commit = "c",
	move = "m",
	absorb = "A",
	reword = "e",
	branch = "n",
	stack = "N",
	apply = "a",
	unapply = "u",
	undo = "z",
	oplog = "O",
	push = "p",
	pr = "P",
	land = "L",
	pull = "f",
	help = "?",
	redo = "redo",
	discard = "discard",
	uncommit = "uncommit",
}

-- Operations with no block key, by name (board toolbar, right-click menus, palette).
local OPS: { [string]: (State, Row?) -> () } = {
	redo = action_redo,
	discard = action_discard,
	uncommit = action_uncommit,
}

local function guarded(state: State, go: () -> ())
	if state.busy then
		notify(state, "warning", "Wait for " .. state.busy)
	elseif state.model == nil then
		notify(state, "warning", "The workspace isn't loaded yet.")
	else
		go()
	end
end

local function run_action(state: State, k: string, target: Row?): boolean
	local action, op = ACTIONS[k], OPS[k]
	if action == nil and op == nil then
		return false
	end
	guarded(state, function()
		if op then
			op(state, target)
		elseif action then
			action(state)
		end
	end)
	return true
end

-- Board right-click labels: what each runs (an action key or a board op).
local MENU: { [string]: string } = {
	["Commit…"] = "c",
	["Move…"] = "m",
	["Absorb"] = "A",
	["Discard…"] = "discard",
	["Discard branch…"] = "discard",
	["Push"] = "p",
	["Create PR"] = "P",
	["Rename…"] = "e",
	["Reword…"] = "e",
	["New stacked branch…"] = "N",
	["Unapply"] = "u",
	["Land"] = "L",
	["Uncommit"] = "uncommit",
}

local function board_do(state: State, k: string, key: string?)
	for _, r in state.rows do
		if r.key == key then
			state.cursor = key
			run_action(state, k, r)
			return
		end
	end
	notify(state, "warning", "That menu target is gone: the workspace changed.")
end

-- A board drop button: `<op>:<source key>:<target row key>`. What moves is the selection
-- (or the checked files); the target is captured by key, not position.
local function drop_on(state: State, value: string)
	local op, source, key = string.match(value, "^([^:]+):([^:]*):(.*)$")
	if source ~= held_key(state) then
		notify(state, "warning", "The selected changes changed. Choose the drop again.")
		return
	end
	local target: Row? = nil
	for _, r in state.rows do
		if r.key == key then
			target = r
		end
	end
	guarded(state, function()
		if op == "newcommit" then
			action_commit(state, NEW_BRANCH)
		elseif op == "newmove" then
			action_move(state, NEW_BRANCH)
		elseif op == "uncommit" then
			action_uncommit(state)
		elseif target == nil then
			notify(state, "warning", "That drop target is gone: the workspace changed.")
		elseif op == "commit" then
			action_commit(state, target.branch)
		elseif op == "move" then
			action_move(state, target.branch)
		elseif op == "amend" then
			action_amend(state, target)
		elseif op == "squash" then
			action_squash(state, target)
		elseif op == "above" then
			action_place_above(state, target)
		end
	end)
end

tern.block.define("workspace", {
	init = function(cx: BlockCx, args: { string }, _saved: any): State
		local previous = running[cx.pane]
		if previous and previous.timer then
			previous.timer:cancel()
		end
		local root = gb.root(args[1] or cx.cwd) or args[1] or cx.cwd or ""
		local state: State = {
			cx = cx,
			root = root,
			model = nil,
			seen = nil,
			reads = 0,
			rows = {},
			lanes = {},
			cursor = nil,
			viewport = nil,
			list_budget = nil,
			marked = {},
			loading = false,
			read_token = 0,
			refresh_callbacks = {},
			refresh_wait = {},
			refresh_again = false,
			fetched = nil,
			problem = if gb.root(root) then nil else "Not a GitButler workspace: " .. root,
			timer = nil,
			sheet = nil,
			busy = nil,
			notice = nil,
			view = if table.find(VIEWS, tern.kv.get("view")) then tern.kv.get("view") else "lanes",
		}
		running[cx.pane] = state
		if state.problem == nil then
			refresh(state)
			poll(state)
		end
		return state
	end,

	title = function(state: State): string?
		return "GitButler · " .. basename(state.root)
	end,

	resize = function(state: State)
		if state.view ~= "board" then
			state.viewport = state.cursor
		end
	end,

	view = function(state: State, _cx: BlockCx): BlockView
		local model = state.model
		if model == nil then
			if state.problem then
				local card = ui.card({ ui.span("GitButler workspace unavailable", "strong") }, {
					ui.code(state.problem),
					ui.text({ ui.span("Press r to retry.", "muted") }),
				})
				local p: { [string]: any } = card.p or {}
				p.tone = "error"
				card.p = p
				return { main = ui.col({ card }) }
			end
			return { main = ui.col({ ui.progress(nil, "Reading the workspace…") }) }
		end
		local main: { Node } = { top_bar(state, model) }
		local sheet = sheet_view(state)
		local diff = state.diff
		if sheet then
			table.insert(main, sheet)
		elseif diff then
			table.insert(main, el("bp-sheet-foot", {
				ui.el("button", { class = "bp-btn", text = "← Back", actions = { click = "back" } }, {}),
				sp(diff.label, "bp-t bp-clip"),
				sp("esc back  r reload", "bp-hint"),
			}))
			for _, n in diff_nodes(diff) do
				table.insert(main, n)
			end
		else
			settle_cursor(state)
			local budget = list_px(state)
			if state.list_budget ~= budget then
				if state.view ~= "board" then
					state.viewport = state.cursor
				end
				state.list_budget = budget
			end
			local view = state.view
			table.insert(main, if view == "graph" then graph_view(state, model)
				elseif view == "stacks" then stacks_view(state, model)
				elseif view == "branches" then branches_view(state, model)
				elseif view == "board" then board_view(state, model)
				else lanes_view(state, model))
		end
		local root = ui.col(main)
		local rp: { [string]: any } = root.p or {}
		rp.role = "gitbutler.view." .. state.view
		root.p = rp
		return { main = root, dock = dock_view(state) }
	end,

	key = function(state: State, key: Key, cx: BlockCx): boolean?
		if state.sheet then
			sheet_key(state, key)
			return true
		end
		-- A paste is text, not a keypress: it never starts an action.
		if key.ctrl or key.alt or key.meta or key.name == "paste" then
			return false
		end
		local k = key.text or key.name
		local n = tonumber(k)
		if n and VIEWS[n] then
			set_view(state, VIEWS[n])
			return true
		end
		if state.diff then
			if key.name == "escape" or key.name == "backspace" or k == "q" then
				state.diff = nil
			elseif k == "r" then
				refresh(state, function(data)
					local d = if data then follow_diff(state) else nil
					if d then
						load_diff(d)
					end
				end)
			elseif k == "v" then
				cycle_view(state)
			else
				-- arrows and the rest scroll the diff; actions wait for the list,
				-- where the cursor they act on is visible
				return false
			end
			return true
		end
		if key.name == "down" or k == "j" then
			step(state, 1)
		elseif key.name == "up" or k == "k" then
			step(state, -1)
		elseif key.name == "left" or k == "h" then
			move_lane(state, -1)
		elseif key.name == "right" or k == "l" then
			move_lane(state, 1)
		elseif key.name == "home" or k == "g" then
			move_end(state, false)
		elseif key.name == "end" or k == "G" then
			move_end(state, true)
		elseif k == "v" then
			cycle_view(state)
		elseif key.name == "enter" or k == "o" then
			open_diff(state)
		elseif k == "r" then
			if state.problem and state.model == nil then
				state.problem = if gb.root(state.root) then nil else state.problem
			end
			state.notice = nil
			refresh(state)
			poll(state)
		elseif key.name == "space" then
			local row = selected(state)
			if row and row.kind == "file" and row.path then
				state.marked[row.path] = not state.marked[row.path] or nil
				step(state, 1)
			end
		elseif key.name == "escape" then
			if state.busy then
				state.reads += 1
			end
			state.notice = nil
			table.clear(state.marked)
		else
			return run_action(state, k)
		end
		return true
	end,

	event = function(state: State, ev: UiEvent, cx: BlockCx)
		if ev.ev == "action" and ev.act == "cancel" then
			if state.sheet then
				finish_sheet(state, nil)
			end
			return
		end
		if ev.ev == "action" and ev.act == "back" then
			state.diff = nil
			return
		end
		if ev.ev == "action" and ev.act == "view" then
			local v = if type(ev.value) == "string" and table.find(VIEWS, ev.value) then ev.value else nil
			if v then
				set_view(state, v)
			else
				cycle_view(state)
			end
			return
		end
		if ev.ev == "action" and (ev.act == "pick" or ev.act == "open") and type(ev.value) == "string" then
			-- A row in any view (`el` rows with click and double-click actions).
			if state.sheet == nil and index_of(state, ev.value) > 0 then
				state.cursor = ev.value
				if ev.act == "open" then
					open_diff(state)
				end
			end
			return
		end
		if ev.ev == "change" and ev.name == "mark" and type(ev.value) == "string" and ev.value ~= "" then
			-- a board file's checkbox: the same marks as space
			for _, row in state.rows do
				if row.kind == "file" and row.path == ev.value then
					state.marked[ev.value] = if ev.checked then true else nil
					break
				end
			end
			return
		end
		if ev.ev == "action" and ev.act == "browse" and type(ev.value) == "string" then
			local lane = tonumber(string.match(ev.value, "^L(%d+)$"))
			if state.sheet == nil and state.diff == nil and state.view == "board"
				and (index_of(state, ev.value) > 0 or (lane ~= nil and lane >= 1 and lane <= #state.lanes + 1)) then
				state.viewport = ev.value
			end
			return
		end
		if ev.ev == "action" and ev.act == "drop" and type(ev.value) == "string" then
			if state.sheet == nil then
				drop_on(state, ev.value)
			end
			return
		end
		if ev.ev == "action" and ev.act == "do" and type(ev.value) == "string" then
			if state.sheet == nil then
				run_action(state, ev.value)
			end
			return
		end
		if ev.ev == "action" and type(ev.act) == "string" and MENU[ev.act] then
			-- a board card's right-click item; the card's node id carries its
			-- row key (`main.….L<lane>.<key>`, then any child index)
			local key = if type(ev.id) == "string" then string.match(ev.id, "%.L%d+%.([^%.]+)") else nil
			if state.sheet == nil then
				board_do(state, MENU[ev.act], key)
			end
			return
		end
		if ev.ev == "action" and type(ev.act) == "string" and NAMED[ev.act] then
			-- From the palette: back to the list, so the selection it acts on shows.
			if state.sheet == nil then
				state.diff = nil
				run_action(state, NAMED[ev.act])
			end
			return
		end
		if (ev.ev == "select" or ev.ev == "activate") and type(ev.item) == "string" then
			local opt = string.match(ev.item, "^main%.sheet%.opts%.o(%d+)$")
			local sheet = state.sheet
			if opt and sheet and sheet.kind == "pick" then
				local n = tonumber(opt) :: number
				sheet.index = n
				if ev.ev == "activate" and sheet.options and sheet.options[n] then
					finish_sheet(state, sheet.options[n].value)
				end
			end
		end
	end,
})
