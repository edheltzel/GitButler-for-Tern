--!strict

local gb = require("./gb")
local drag = require("./drag")

local WORKSPACE = "gitbutler.workspace"
local OPEN = "plugin.gitbutler.open"
local POLL_MS = 30 * 1000
local STALE_MS = 3 * 1000
local READ_TIMEOUT_MS = 15 * 1000
local BLOCK_START_MS = 1500
local SWEEP_MS = 10
-- What the status segment shows for one workspace root.
type Summary = {
	branches: number,
	unpushed: number,
	partial: boolean, -- some branch's unpushed count is unknown: show "N+"
	problem: string?, -- `but` failed (setup_required, timeout, …)
	at: number, -- tern.now() of the last answer
}

-- Root of each directory seen, false when it isn't GitButler-managed.
local roots: { [string]: string | false } = {}
local summaries: { [string]: Summary } = {}
local loading: { [string]: boolean } = {}
local tick: TimerHandle? = nil
local sweep_gen = 0
local function root_of(cwd: string?): string?
	if cwd == nil or cwd == "" then
		return nil
	end
	local known = roots[cwd]
	if known == nil then
		known = gb.root(cwd) or false
		roots[cwd] = known
	end
	return if known then known else nil
end

local function focused_cwd(cx: WindowCx): string?
	local id = cx.session:focused()
	if id == nil then
		return nil
	end
	local info = cx.session:pane(id)
	return if info then info.cwd else nil
end

-- Branch names whose unpushed count `but push --dry-run --json` reports.
local function dry_run_counts(data: any): { [string]: number }
	local out: { [string]: number } = {}
	if type(data) == "table" and type(data.branches) == "table" then
		for _, b in data.branches do
			if type(b.branchName) == "string" and type(b.unpushedCommits) == "number" then
				out[b.branchName] = b.unpushedCommits
			end
		end
	end
	return out
end

local function store(root: string, summary: Summary)
	summaries[root] = summary
	loading[root] = nil
	tern.chrome.refresh()
end

-- Reads the workspace at `root` in the background, at most once per
-- STALE_MS unless `force`.
local function fetch(root: string, force: boolean)
	local last = summaries[root]
	if loading[root] or (not force and last and tern.now() - last.at < STALE_MS) then
		return
	end
	loading[root] = true
	local spawn_err = gb.run(root, { "status", "--json" }, READ_TIMEOUT_MS, function(r: gb.Result)
		local model = if r.ok then gb.model(r.data) else nil
		if model == nil then
			store(root, { branches = 0, unpushed = 0, partial = false, problem = r.err or "unreadable status", at = tern.now() })
			return
		end
		local branches, unpushed = 0, 0
		local unknown: { string } = {}
		for _, stack in model.stacks do
			for _, b in stack.branches do
				branches += 1
				local n = gb.unpushed(b)
				if n then
					unpushed += n
				else
					table.insert(unknown, b.name)
				end
			end
		end
		if #unknown == 0 then
			store(root, { branches = branches, unpushed = unpushed, partial = false, at = tern.now() })
			return
		end
		-- Some branches are partly pushed: ask push for exact counts.
		local err = gb.run(root, { "push", "--dry-run", "--json" }, READ_TIMEOUT_MS, function(p: gb.Result)
			local counts = if p.ok then dry_run_counts(p.data) else {}
			local partial = false
			for _, name in unknown do
				local n = counts[name]
				if n then
					unpushed += n
				else
					partial = true
				end
			end
			store(root, { branches = branches, unpushed = unpushed, partial = partial, at = tern.now() })
		end)
		if err then
			store(root, { branches = branches, unpushed = unpushed, partial = true, at = tern.now() })
		end
	end)
	if spawn_err then
		store(root, { branches = 0, unpushed = 0, partial = false, problem = spawn_err, at = tern.now() })
	end
end

-- One poll timer while the focused pane is in a GitButler workspace.
local function track(cx: WindowCx, force: boolean)
	local root = root_of(focused_cwd(cx))
	if root then
		fetch(root, force)
	end
	if root and tick == nil then
		tick = tern.timer(POLL_MS, function(tcx: WindowCx?)
			tick = nil
			if tcx then
				track(tcx, true)
			end
		end)
	elseif root == nil and tick then
		tick:cancel()
		tick = nil
	end
end

-- A reload doesn't emit window_start or focus: read the focused pane now.
tern.timer(0, function(cx: WindowCx?)
	if cx then
		track(cx, false)
	end
end)
tern.on("window_start", function(cx: WindowCx)
	track(cx, false)
end)
tern.on("focus", function(_ev: FocusEvent, cx: WindowCx)
	track(cx, false)
end)
tern.on("cwd", function(_ev: any, cx: WindowCx)
	track(cx, false)
end)
tern.on("command_finished", function(_ev: CommandFinishedEvent, cx: WindowCx)
	-- A command may have changed whether a folder is managed. Do not
	-- clear the cache first: the formatter must keep the previous entry
	-- until that cwd is overwritten. Probe only the focused cwd here
	-- (`but setup` / `but teardown`, including a child of a cached
	-- parent). Other panes are swept in short timer chunks so this
	-- handler stays inside the 50 ms budget.
	sweep_gen += 1
	local gen = sweep_gen
	local focused = focused_cwd(cx)
	if type(focused) == "string" and focused ~= "" then
		roots[focused] = gb.root(focused) or false
	end
	track(cx, true)
	tern.chrome.refresh()

	local pending: { string } = {}
	local seen: { [string]: boolean } = {}
	if type(focused) == "string" and focused ~= "" then
		seen[focused] = true
	end
	for _, p in cx.session:panes() do
		local dir = p.cwd
		if type(dir) == "string" and dir ~= "" and not seen[dir] then
			seen[dir] = true
			table.insert(pending, dir)
		end
	end

	local function drop_absent(tcx: WindowCx)
		local live: { [string]: boolean } = {}
		for _, p in tcx.session:panes() do
			local dir = p.cwd
			if type(dir) == "string" and dir ~= "" then
				live[dir] = true
			end
		end
		for cwd in roots do
			if not live[cwd] then
				roots[cwd] = nil
			end
		end
	end

	local i = 1
	local function step(tcx: WindowCx?)
		if gen ~= sweep_gen or tcx == nil then
			return
		end
		local start = tern.now()
		local changed = false
		local focused_now = focused_cwd(tcx)
		local focused_changed = false
		while i <= #pending do
			local dir = pending[i]
			i += 1
			local nextv = gb.root(dir) or false
			if roots[dir] ~= nextv then
				changed = true
				focused_changed = focused_changed or dir == focused_now
			end
			roots[dir] = nextv
			if tern.now() - start >= SWEEP_MS then
				break
			end
		end
		if focused_changed then
			-- Focus may have landed here before the sweep did: start the
			-- fetch and poll that the focus handler skipped.
			track(tcx, true)
		end
		if changed then
			tern.chrome.refresh()
		end
		if i <= #pending then
			tern.timer(0, step)
		else
			drop_absent(tcx)
		end
	end

	if #pending > 0 then
		tern.timer(0, step)
	else
		drop_absent(cx)
	end
end)

tern.chrome.status(function(pane: StatusPaneInfo): { StatusSegment }?
	local known = if pane.cwd then roots[pane.cwd] else nil
	if not known then
		return nil
	end
	local s = summaries[known]
	if s == nil then
		return { { text = "GitButler", icon = "git-branch", tone = "muted", command = OPEN } }
	end
	if s.problem then
		local text = if string.find(s.problem, "No GitButler project", 1, true)
			then "GitButler: run but setup"
			else "GitButler: " .. (string.match(s.problem, "^[^\n]*") or "error")
		return { { text = text, icon = "git-branch", tone = "warning", command = OPEN } }
	end
	local text = string.format(
		"%d %s · %d%s unpushed",
		s.branches,
		if s.branches == 1 then "branch" else "branches",
		s.unpushed,
		if s.partial then "+" else ""
	)
	return { { text = text, icon = "git-branch", tone = if s.unpushed > 0 then "accent" else "muted", command = OPEN } }
end)

-- The workspace block for `root`: a live one, else a new one.
-- Plugin panes omit `exited`; an ended block has no surface.
local function open_workspace(cx: WindowCx, root: string): (number?, boolean)
	for _, pane in cx.session:panes() do
		if pane.block == WORKSPACE and root_of(pane.cwd) == root then
			local snap = cx.session:surface(pane.pane, { nodes = 1 })
			if type(snap) == "table" and snap.surface ~= nil then
				cx.layout:focus(pane.pane)
				return pane.pane, true
			end
		end
	end
	local pane = cx:new_block(WORKSPACE, { root }, "beside")
	if pane == nil then
		cx:toast("error", "GitButler block is not loaded on this host", "Install the plugin where your panes run.")
	end
	return pane, false
end

local function focused_root(cx: WindowCx): string?
	local root = root_of(focused_cwd(cx))
	if root == nil then
		cx:toast("info", "Not a GitButler workspace", focused_cwd(cx) or "No focused pane")
	end
	return root
end

tern.command({
	id = "open",
	title = "Open GitButler workspace",
	icon = "git-branch",
	keys = { "cmd+alt+shift+g" },
	run = function(cx: WindowCx)
		local root = focused_root(cx)
		if root then
			open_workspace(cx, root)
		end
	end,
})

-- `but tui` in a new shell pane floated as picture-in-picture over the
-- focused pane; the shell's own PATH finds `but`. One per workspace root:
-- running the command again focuses the live one instead of stacking another.
local tui_panes: { [string]: number } = {}

tern.command({
	id = "tui",
	title = "GitButler: Open TUI",
	icon = "git-branch",
	keys = { "cmd+alt+shift+t" },
	run = function(cx: WindowCx)
		local root = focused_root(cx)
		local over = cx.session:focused()
		if root == nil or over == nil then
			return
		end
		local live = tui_panes[root]
		if live then
			local info = cx.session:pane(live)
			if info and info.exited == nil then
				cx.layout:focus(live)
				return
			end
			tui_panes[root] = nil
		end
		local pane = cx.layout:split(over, "right", { cwd = root, command = "but tui" })
		if pane == nil then
			cx:toast("error", "Could not open but tui")
			return
		end
		tui_panes[root] = pane
		local ok, err = cx.layout:float(pane, over)
		if not ok then
			cx:toast("error", "Could not float but tui", err)
		end
		-- Floating hands focus back to the pane underneath; typing belongs to the tui.
		cx.layout:focus(pane)
	end,
})

-- `but gui` opens the GitButler desktop app on this workspace and exits.
tern.command({
	id = "gui",
	title = "GitButler: Open GUI",
	icon = "git-branch",
	run = function(cx: WindowCx)
		local root = focused_root(cx)
		if root == nil then
			return
		end
		local err = gb.run(root, { "gui" }, READ_TIMEOUT_MS, function(r: gb.Result)
			if not r.ok then
				-- The command's cx expires with its handler; a timer gets a fresh one.
				tern.timer(0, function(tcx: WindowCx?)
					if tcx then
						tcx:toast("error", "but gui failed", r.err)
					end
				end)
			end
		end)
		if err then
			cx:toast("error", "but gui failed", err)
		end
	end,
})

-- `but setup --init` in the focused pane's folder: registers the repo with
-- GitButler and switches it to `gitbutler/workspace`, creating the git repo
-- first when there is none. Home and `/` are refused so a stray palette run
-- can't turn them into repositories.
tern.command({
	id = "setup",
	title = "GitButler: Set up this folder",
	icon = "git-branch",
	run = function(cx: WindowCx)
		local cwd = focused_cwd(cx)
		if cwd == nil or cwd == "" then
			cx:toast("info", "No focused folder")
			return
		end
		if root_of(cwd) then
			cx:toast("info", "Already a GitButler workspace", cwd)
			return
		end
		local dir = string.gsub(cwd, "/+$", "")
		if dir == "" or dir == tern.getenv("HOME") then
			cx:toast("error", "Refusing to set up " .. cwd, "cd into a project folder first")
			return
		end
		local err = gb.run(cwd, { "setup", "--init", "--json" }, READ_TIMEOUT_MS, function(r: gb.Result)
			-- The command's cx expires with its handler; a timer gets a fresh one.
			tern.timer(0, function(tcx: WindowCx?)
				if tcx == nil then
					return
				end
				if r.ok then
					roots[cwd] = gb.root(cwd) or false
					tcx:toast("success", "GitButler is set up", cwd)
					track(tcx, true)
					tern.chrome.refresh()
				else
					tcx:toast("error", "but setup failed", r.err)
				end
			end)
		end)
		if err then
			cx:toast("error", "but setup failed", err)
		end
	end,
})

-- Palette commands for the block's actions: open (or focus) the workspace,
-- then hand it the action as a view event, so it runs on the block's
-- selection with the block's confirmations. A block that is only now
-- starting gets the event once its first view is up.
-- No default chords: bind them in Tern's keybinds (`plugin.gitbutler.<id>`).
local ACTIONS = {
	{ "commit", "GitButler: Commit to a branch…" },
	{ "absorb", "GitButler: Absorb uncommitted changes" },
	{ "undo", "GitButler: Undo last operation" },
	{ "redo", "GitButler: Redo last undone operation" },
	{ "oplog", "GitButler: Restore from oplog…" },
	{ "move", "GitButler: Move selection to a branch…" },
	{ "reword", "GitButler: Reword commit / rename branch…" },
	{ "branch", "GitButler: New parallel branch…" },
	{ "stack", "GitButler: New stacked branch…" },
	{ "apply", "GitButler: Apply a branch…" },
	{ "unapply", "GitButler: Unapply selected stack…" },
	{ "push", "GitButler: Push selected branch…" },
	{ "pr", "GitButler: Open pull request…" },
	{ "land", "GitButler: Land selected branch…" },
	{ "view", "GitButler: Switch workspace view" },
	{ "pull", "GitButler: Pull upstream into applied branches…" },
	{ "uncommit", "GitButler: Uncommit selected commit or file…" },
	{ "discard", "GitButler: Discard selection…" },
	{ "help", "GitButler: Show workspace keys" },
}

-- Opens (or focuses) the workspace block and hands it `ev`. A block that is
-- only now starting gets it once its first view is up.
local function deliver(cx: WindowCx, root: string, ev: { [string]: any })
	local pane, existed = open_workspace(cx, root)
	if pane and existed then
		cx.session:event(pane, ev)
	elseif pane then
		tern.timer(BLOCK_START_MS, function(tcx: WindowCx?)
			if tcx then
				tcx.session:event(pane :: number, ev)
			end
		end)
	end
end

for _, a in ACTIONS do
	local act, title = a[1], a[2]
	tern.command({
		id = act,
		title = title,
		icon = "git-branch",
		run = function(cx: WindowCx)
			local root = focused_root(cx)
			if root then
				deliver(cx, root, { ev = "action", act = act })
			end
		end,
	})
end

-- The native task board and its sidecar are read asynchronously. Row keys
-- live in local Markdown anchors, so drag.bind can prove that a snapshot
-- belongs to its metadata before native ids are rebound. Reloads change all
-- native ids; hand lane edits preserve some and request one rewrite.
type Watch = {
	pane: number,
	keys: { [string]: string },
	lane_keys: { string },
	board: drag.Board?, -- the last read the keys were mapped onto
	key_text: string?, -- the key file those keys came from
	resynced: string?, -- lane ids a rewrite was already asked for
}
local watches: { [string]: Watch } = {}
local WATCH_MS = 400

local function board_of(snap: any): drag.Board?
	if (snap.more or 0) > 0 then
		return nil
	end
	local board: drag.Board = { lanes = {}, cards = {}, titles = {}, texts = {} }
	for _, lane in (if type(snap.lanes) == "table" then snap.lanes else {}) do
		if (lane.more or 0) > 0 then
			return nil
		end
		local ids: { string }, texts: { string } = {}, {}
		for _, card in (if type(lane.cards) == "table" then lane.cards else {}) do
			table.insert(ids, tostring(card.id))
			table.insert(texts, card.text or "")
		end
		table.insert(board.lanes, tostring(lane.id))
		table.insert(board.cards, ids)
		table.insert(board.titles :: { string }, lane.title or "")
		table.insert(board.texts :: { { string } }, texts)
	end
	return board
end

local function map_keys(root: string, text: string?, board: drag.Board): ({ [string]: string }?, { string }?)
	if text == nil then
		return nil, nil
	end
	local ok, data = pcall(tern.json.decode, text)
	if not ok or type(data) ~= "table" or data.root ~= root then
		return nil, nil
	end
	return drag.bind(board, data)
end

local function resync(cx: WindowCx, root: string, w: Watch, board: drag.Board)
	local sig = table.concat(board.lanes, "\0")
	if w.resynced ~= sig then
		w.resynced = sig
		deliver(cx, root, { ev = "action", act = "native_board" })
	end
end

local function on_board(cx: WindowCx, root: string, path: string, snap: any)
	local w = watches[root]
	if w == nil then
		return
	end
	local board = board_of(snap)
	if board == nil then
		watches[root] = nil
		cx:toast("error", "The GitButler board exceeds Tern's 500-row read limit", "Drag-and-drop is disabled. Use the GitButler workspace panel.")
		return
	end
	local ok, read = pcall(tern.fs.read, path .. ".json", 4194304)
	local key_text = if ok then read :: string else nil
	local last = w.board
	local change = if last then drag.change(last, board) else "reload"
	if change == "edit" then
		resync(cx, root, w, board)
		return
	end
	if change == "reload" then
		local keys, lane_keys = map_keys(root, key_text, board)
		if keys and lane_keys then
			w.keys, w.lane_keys, w.board, w.key_text = keys, lane_keys, board, key_text
		else
			resync(cx, root, w, board)
		end
		return
	end
	if last and key_text ~= w.key_text then
		local keys, lane_keys = map_keys(root, key_text, last)
		if keys == nil or lane_keys == nil then
			-- New metadata arrived before its Markdown. Never reinterpret the
			-- displayed cards; wait for a snapshot carrying matching identities.
			return
		end
		w.keys, w.lane_keys, w.key_text = keys, lane_keys, key_text
	end
	local id, lane, near, below, ambiguous = drag.moved(last :: drag.Board, board)
	if ambiguous then
		resync(cx, root, w, board)
		return
	end
	w.board = board
	local source = if id then w.keys[id] else nil
	local target = w.lane_keys[lane]
	if source and target then
		local value = source .. ":" .. target .. ":" .. (if near then w.keys[near] or "" else "") .. ":" .. (if below then "1" else "0")
		deliver(cx, root, { ev = "action", act = "native_drop", value = value })
	end
end

local function watch_board(root: string, path: string)
	tern.timer(WATCH_MS, function(cx: WindowCx?)
		local w = watches[root]
		if w == nil then
			return
		end
		if cx and cx.session:pane(w.pane) == nil then
			watches[root] = nil
			return
		end
		if cx then
			cx.board:read(w.pane, { rows = 500 }):next(function(snap: any, _err: any, tcx: WindowCx?)
				if snap and tcx then
					on_board(tcx, root, path, snap)
				end
			end)
		end
		watch_board(root, path)
	end)
end

local function attach(root: string, path: string, pane: number)
	local fresh = watches[root] == nil
	watches[root] = { pane = pane, keys = {}, lane_keys = {} }
	if fresh then
		watch_board(root, path)
	end
end

-- Opens the board once the block has written it (a fresh block needs a moment).
local function open_board(root: string, path: string, deadline: number)
	tern.timer(200, function(cx: WindowCx?)
		if cx == nil then
			return
		end
		if not tern.fs.exists(path) then
			if tern.now() < deadline then
				open_board(root, path, deadline)
			else
				cx:toast("error", "The GitButler board was not written", path)
			end
			return
		end
		attach(root, path, cx.board:open(path, { how = "beside" }))
	end)
end

local function reattach(cx: WindowCx)
	local dir = tern.plugin.data .. "/boards/"
	for _, p in cx.session:panes() do
		local path = p.path
		if p.kind == "board" and path and string.sub(path, 1, #dir) == dir and tern.fs.exists(path .. ".json") then
			local ok, data = pcall(function()
				return tern.json.decode(tern.fs.read(path .. ".json", 4194304))
			end)
			local root = if ok and type(data) == "table" and type(data.root) == "string" then data.root else nil
			if root and gb.board_path(root) == path and (watches[root] == nil or watches[root].pane ~= p.pane) then
				attach(root, path, p.pane)
			end
		end
	end
end
tern.timer(0, function(cx: WindowCx?)
	if cx then
		reattach(cx)
	end
end)
tern.on("window_start", reattach)

tern.command({
	id = "board",
	title = "GitButler: Open drag-and-drop board",
	icon = "git-branch",
	run = function(cx: WindowCx)
		local root = focused_root(cx)
		if root == nil then
			return
		end
		local w = watches[root]
		if w and cx.session:pane(w.pane) then
			cx.layout:focus(w.pane)
			return
		end
		deliver(cx, root, { ev = "action", act = "native_board" })
		open_board(root, gb.board_path(root), tern.now() + BLOCK_START_MS + READ_TIMEOUT_MS)
	end,
})

-- Tern's git block writes with raw git (stage, commit, checkout, stash),
-- which a GitButler workspace must not take. Its `git.*` keys are view
-- chords that plugins can't override, so the commands that open it are
-- routed to the workspace block instead, and a git block opened some other
-- way on a GitButler repo gets a warning.
tern.override("new_git_block", function(cx: WindowCx): boolean
	local root = root_of(focused_cwd(cx))
	if root == nil then
		return false
	end
	open_workspace(cx, root)
	return true
end)

local warned: { [number]: boolean } = {}

local function check_git_block(cx: WindowCx, pane: number)
	if warned[pane] then
		return
	end
	local info = cx.session:pane(pane)
	if info == nil or info.kind ~= "git" then
		return
	end
	local root = gb.root(info.path or info.cwd)
	if root then
		warned[pane] = true
		cx:toast(
			"error",
			"Tern's git block is open on a GitButler workspace",
			"It stages, commits and checks out with raw git. Use GitButler (⌥⇧⌘G) for " .. root
		)
	end
end

tern.on("pane_created", function(ev: PaneEvent, cx: WindowCx)
	check_git_block(cx, ev.pane)
end)
tern.on("focus", function(ev: FocusEvent, cx: WindowCx)
	if ev.pane then
		check_git_block(cx, ev.pane)
	end
end)
tern.on("pane_closed", function(ev: PaneEvent, cx: WindowCx)
	warned[ev.pane] = nil
	for _, pane in cx.session:panes() do
		if pane.block == WORKSPACE and pane.pane ~= ev.pane then
			cx.session:event(pane.pane, { ev = "action", act = "native_closed", value = ev.pane })
		end
	end
end)
