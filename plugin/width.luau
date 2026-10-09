--!strict
-- Terminal cells a string takes, and which list rows fit, for sizing the workspace views. Pure: no
-- `tern`, so `luau check.luau` can run it. Wide is Unicode East Asian Width
-- W/F plus emoji presentation, zero is combining marks, format controls and
-- variation selectors; ranges are block-level, so a rare unassigned code point
-- inside one counts like its neighbors. `cells` also reads emoji sequences:
-- VS16/VS15 presentation, skin-tone modifiers, ZWJ joins and flag pairs.

local width = {}

-- Sorted, non-overlapping {first, last} ranges.
local ZERO: { { number } } = {
	{ 0x0000, 0x001F }, { 0x007F, 0x009F }, { 0x0300, 0x036F }, { 0x0483, 0x0489 },
	{ 0x0591, 0x05BD }, { 0x0610, 0x061A }, { 0x064B, 0x065F }, { 0x0E31, 0x0E31 },
	{ 0x0E34, 0x0E3A }, { 0x0E47, 0x0E4E }, { 0x1AB0, 0x1AFF }, { 0x1DC0, 0x1DFF },
	{ 0x200B, 0x200F }, { 0x2028, 0x202E }, { 0x2060, 0x2064 }, { 0x20D0, 0x20FF },
	{ 0xFE00, 0xFE0F }, { 0xFE20, 0xFE2F }, { 0xFEFF, 0xFEFF }, { 0xE0100, 0xE01EF },
}

local WIDE: { { number } } = {
	{ 0x1100, 0x115F }, { 0x231A, 0x231B }, { 0x2329, 0x232A }, { 0x23E9, 0x23EC },
	{ 0x23F0, 0x23F0 }, { 0x23F3, 0x23F3 }, { 0x25FD, 0x25FE }, { 0x2614, 0x2615 },
	{ 0x2648, 0x2653 }, { 0x267F, 0x267F }, { 0x2693, 0x2693 }, { 0x26A1, 0x26A1 },
	{ 0x26AA, 0x26AB }, { 0x26BD, 0x26BE }, { 0x26C4, 0x26C5 }, { 0x26CE, 0x26CE },
	{ 0x26D4, 0x26D4 }, { 0x26EA, 0x26EA }, { 0x26F2, 0x26F3 }, { 0x26F5, 0x26F5 },
	{ 0x26FA, 0x26FA }, { 0x26FD, 0x26FD }, { 0x2705, 0x2705 }, { 0x270A, 0x270B },
	{ 0x2728, 0x2728 }, { 0x274C, 0x274C }, { 0x274E, 0x274E }, { 0x2753, 0x2755 },
	{ 0x2757, 0x2757 }, { 0x2795, 0x2797 }, { 0x27B0, 0x27B0 }, { 0x27BF, 0x27BF },
	{ 0x2B1B, 0x2B1C }, { 0x2B50, 0x2B50 }, { 0x2B55, 0x2B55 }, { 0x2E80, 0x303E },
	{ 0x3041, 0x33FF }, { 0x3400, 0x4DBF }, { 0x4E00, 0x9FFF }, { 0xA000, 0xA4CF },
	{ 0xA960, 0xA97F }, { 0xAC00, 0xD7A3 }, { 0xF900, 0xFAFF }, { 0xFE10, 0xFE19 },
	{ 0xFE30, 0xFE6F }, { 0xFF00, 0xFF60 }, { 0xFFE0, 0xFFE6 }, { 0x16FE0, 0x16FE4 },
	{ 0x17000, 0x18AFF }, { 0x1B000, 0x1B2FF }, { 0x1F004, 0x1F004 }, { 0x1F0CF, 0x1F0CF },
	{ 0x1F18E, 0x1F18E }, { 0x1F191, 0x1F19A }, { 0x1F200, 0x1F2FF }, { 0x1F300, 0x1F64F },
	{ 0x1F680, 0x1F6FF }, { 0x1F7E0, 0x1F7EB }, { 0x1F90C, 0x1F9FF }, { 0x1FA70, 0x1FAFF },
	{ 0x20000, 0x2FFFD }, { 0x30000, 0x3FFFD },
}

local function within(ranges: { { number } }, c: number): boolean
	local lo, hi = 1, #ranges
	while lo <= hi do
		local mid = (lo + hi) // 2
		local r = ranges[mid]
		if c < r[1] then
			hi = mid - 1
		elseif c > r[2] then
			lo = mid + 1
		else
			return true
		end
	end
	return false
end

-- Cells of one code point: 0, 1 or 2.
function width.char(c: number): number
	if c >= 0x20 and c < 0x7F then
		return 1
	end
	if within(ZERO, c) then
		return 0
	end
	return if within(WIDE, c) then 2 else 1
end

local function is_flag_letter(c: number): boolean
	return c >= 0x1F1E6 and c <= 0x1F1FF
end

local function is_skin_tone(c: number): boolean
	return c >= 0x1F3FB and c <= 0x1F3FF
end

-- Cells of one line, one emoji sequence at a time. Invalid UTF-8 counts one
-- cell per byte.
function width.cells(line: string): number
	local ok, n = pcall(function(): number
		local w = 0
		local last = 0 -- cells of the previous visible glyph
		local joining = false -- a ZWJ just passed: the next code point joins that glyph
		local flag_open = false -- one regional indicator is waiting for its pair
		for _, c in utf8.codes(line) do
			if c == 0x200D then
				joining = true
			elseif joining then
				joining = false
			elseif c == 0xFE0F then
				-- emoji presentation: a text-default symbol becomes two cells
				if last == 1 then
					w += 1
					last = 2
				end
			elseif c == 0xFE0E then
				if last == 2 then
					w -= 1
					last = 1
				end
			elseif is_skin_tone(c) and last > 0 then
				-- part of the glyph before it, which it makes a two-cell emoji
				if last == 1 then
					w += 1
					last = 2
				end
			elseif is_flag_letter(c) then
				if flag_open then
					flag_open = false
				else
					w += 2
					last = 2
					flag_open = true
				end
			else
				flag_open = false
				local cw = width.char(c)
				w += cw
				if cw > 0 then
					last = cw
				end
			end
		end
		return w
	end)
	return if ok then n else #line
end

-- Rows [first, last] of `total` that fit `room` lines around row `at`, and
-- whether the "↑ more" / "↓ more" lines fit too: rows plus the markers it
-- allows never exceed `room`, and row `at` always shows.
function width.slice(total: number, at: number, room: number): (number, number, boolean, boolean)
	if total <= room then
		return 1, total, false, false
	end
	local n = math.max(1, room - 2)
	local first = math.clamp(at - n // 2, 1, math.max(1, total - n + 1))
	local last = math.min(total, first + n - 1)
	local up, down = first > 1, last < total
	local spare = room - (last - first + 1)
	if up and down and spare < 2 then
		up = false -- one line left: keep the marker below
		down = spare >= 1
	elseif spare < 1 then
		up, down = false, false
	end
	return first, last, up, down
end

return width
