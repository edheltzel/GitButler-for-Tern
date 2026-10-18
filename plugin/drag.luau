--!strict
-- Pure diff of two reads of the drag-and-drop board (window.luau), free of
-- `tern` so `luau check.luau` can run it. A board is its lanes' native ids
-- and, per lane, its cards' ids in order.

local drag = {}

export type Board = {
	lanes: { string },
	cards: { { string } },
	titles: { string }?,
	texts: { { string } }?,
}

-- Local Markdown anchors carry identity through Tern's task model without
-- making the sidecar's position an authority over an older live snapshot.
function drag.label(text: string, key: string): string
	local title = string.gsub(text, "%s+", " ")
	title = string.gsub(title, "[\\%[%]]", "\\%0")
	return "[" .. title .. "](#gb-row-" .. key .. ")"
end

local function identity(text: any): string?
	return if type(text) == "string" then string.match(text, "%]%(%#gb%-row%-([%w%-]+)%)$") else nil
end

function drag.bind(board: Board, data: any): ({ [string]: string }?, { string }?)
	if type(data) ~= "table" or type(data.lanes) ~= "table"
		or #data.lanes ~= #board.lanes or board.titles == nil or board.texts == nil then
		return nil, nil
	end
	local keys: { [string]: string }, lanes: { string } = {}, {}
	local seen: { [string]: boolean } = {}
	for i, ids in board.cards do
		local lane = data.lanes[i]
		local texts = board.texts[i]
		if type(lane) ~= "table" or type(lane.key) ~= "string" or type(lane.cards) ~= "table"
			or type(lane.title) ~= "string" or board.titles[i] ~= lane.title or #lane.cards ~= #ids
			or texts == nil or #texts ~= #ids or seen[lane.key] then
			return nil, nil
		end
		seen[lane.key], lanes[i] = true, lane.key
		for j, id in ids do
			local key = lane.cards[j]
			if type(key) ~= "string" or identity(texts[j]) ~= key or seen[key] or keys[id] then
				return nil, nil
			end
			seen[key], keys[id] = true, key
		end
	end
	return keys, lanes
end

-- Whether the lanes themselves are unchanged (same ids, same order). A user
-- who adds, removes or reorders a lane changes them; that is not a drag.
function drag.same_lanes(a: Board, b: Board): boolean
	return table.concat(a.lanes, "\0") == table.concat(b.lanes, "\0")
end

-- How the board changed between two reads: "same" lanes (cards may have
-- moved), "reload" (every lane id is new: the block rewrote the file), or
-- "edit" (some lane ids survive in a new set or order: the user added,
-- removed or reordered lanes; its cards must not be remapped by position).
function drag.change(old: Board, new: Board): string
	if drag.same_lanes(old, new) then
		return "same"
	end
	for _, id in new.lanes do
		if table.find(old.lanes, id) then
			return "edit"
		end
	end
	return "reload"
end

local function equal_without(a: { string }, b: { string }, omit: string): boolean
	local i, j = 1, 1
	while i <= #a or j <= #b do
		if a[i] == omit then i += 1 end
		if b[j] == omit then j += 1 end
		if a[i] ~= b[j] then return false end
		i += 1
		j += 1
	end
	return true
end

-- At the first mismatch a single moved card must be either the old or the
-- new card. Removing that candidate must leave every lane's order unchanged.
-- The fifth return requests reconciliation for edits that aren't one drag.
function drag.moved(old: Board, new: Board): (string?, number, string?, boolean, boolean)
	if not drag.same_lanes(old, new) then
		return nil, 0, nil, false, true
	end
	local candidates: { string } = {}
	for i, ids in old.cards do
		local after = new.cards[i]
		for j = 1, math.max(#ids, #after) do
			if ids[j] ~= after[j] then
				if ids[j] then table.insert(candidates, ids[j]) end
				if after[j] then table.insert(candidates, after[j]) end
				break
			end
		end
		if #candidates > 0 then break end
	end
	for _, candidate in candidates do
		local valid, old_count, new_count, lane, slot = true, 0, 0, 0, 0
		for i, ids in old.cards do
			if not equal_without(ids, new.cards[i], candidate) then valid = false; break end
			for _, id in ids do if id == candidate then old_count += 1 end end
			for j, id in new.cards[i] do
				if id == candidate then new_count += 1; lane, slot = i, j end
			end
		end
		if valid and old_count == 1 and new_count == 1 then
			local ids = new.cards[lane]
			return candidate, lane, ids[slot + 1] or ids[slot - 1], ids[slot + 1] == nil, false
		end
	end
	return nil, 0, nil, false, #candidates > 0
end

return drag
