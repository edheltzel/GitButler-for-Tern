--!strict
-- Pure diff of two reads of the drag-and-drop board (window.luau), free of
-- `tern` so `luau check.luau` can run it. A board is its lanes' native ids
-- and, per lane, its cards' ids in order.

local drag = {}

export type Board = { lanes: { string }, cards: { { string } } }

-- Whether the lanes themselves are unchanged (same ids, same order). A user
-- who adds, removes, renames or reorders a lane changes them; that is not a
-- drag, and the board must be rewritten instead.
function drag.same_lanes(a: Board, b: Board): boolean
	return table.concat(a.lanes, "\0") == table.concat(b.lanes, "\0")
end

local function without(ids: { string }, id: string): string
	local rest = table.clone(ids)
	table.remove(rest, table.find(rest, id) :: number)
	return table.concat(rest, "\0")
end

-- The card one drag moved between two reads with the same lanes, the lane
-- index it is in now, and the card next to it there: the one below it, else
-- the one above (then `below` is true). nil when no card moved.
function drag.moved(old: Board, new: Board): (string?, number, string?, boolean)
	if not drag.same_lanes(old, new) then
		return nil, 0, nil, false
	end
	local was: { [string]: number } = {}
	for i, ids in old.cards do
		for _, id in ids do
			was[id] = i
		end
	end
	local function at(i: number, j: number, id: string): (string?, number, string?, boolean)
		local ids = new.cards[i]
		return id, i, ids[j + 1] or ids[j - 1], ids[j + 1] == nil
	end
	for i, ids in new.cards do
		for j, id in ids do
			if was[id] ~= nil and was[id] ~= i then
				return at(i, j, id)
			end
		end
	end
	-- Within one lane: the card whose removal leaves both orders equal.
	for i, ids in new.cards do
		local before = old.cards[i]
		if before and #before == #ids and table.concat(before, "\0") ~= table.concat(ids, "\0") then
			for j, id in ids do
				if table.find(before, id) and without(before, id) == without(ids, id) then
					return at(i, j, id)
				end
			end
		end
	end
	return nil, 0, nil, false
end

return drag
