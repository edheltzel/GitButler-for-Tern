--!strict
-- Shared by both halves: the "is this GitButler-managed?" check, running
-- `but` asynchronously, and reading `but status --json` into a model.

local gb = {}

local HOME = tern.getenv("HOME") or ""

-- A daemon started by launchd often has a PATH without these directories.
local BUT = { "but", HOME .. "/.local/bin/but", "/opt/homebrew/bin/but", "/usr/local/bin/but" }

local function read_trim(path: string): string?
	-- A bounded read rejects directories, which tells a `.git` dir from a file.
	local ok, text = pcall(tern.fs.read, path, 4096)
	if not ok then
		return nil
	end
	return (string.gsub(text :: string, "%s+$", ""))
end

local function join(dir: string, rel: string): string
	return if string.sub(rel, 1, 1) == "/" then rel else dir .. "/" .. rel
end

-- The worktree root of the GitButler workspace holding `path`, or nil.
-- Managed means HEAD (of the main worktree) is on a `gitbutler/` branch
-- (`workspace`, or `edit` while resolving) and `.git/gitbutler/` exists:
-- such a repo must not take raw git writes, whether or not `but` has the
-- project registered (the block reports `setup_required` when it doesn't).
function gb.root(path: string?): string?
	if path == nil or path == "" or string.sub(path, 1, 1) ~= "/" then
		return nil
	end
	local dir: string? = string.gsub(path, "/+$", "")
	while dir ~= nil do
		local d = if dir == "" then "/" else dir
		local dotgit = (if d == "/" then "" else d) .. "/.git"
		if tern.fs.exists(dotgit) then
			local gitdir = dotgit
			local pointer = read_trim(dotgit)
			if pointer then
				local rel = string.match(pointer, "^gitdir:%s*(.+)$")
				if rel == nil then
					return nil
				end
				gitdir = join(d, rel)
			end
			local common = gitdir
			local rel = read_trim(gitdir .. "/commondir")
			if rel then
				common = join(gitdir, rel)
			end
			local head = read_trim(common .. "/HEAD") or ""
			if string.find(head, "^ref: refs/heads/gitbutler/") and tern.fs.exists(common .. "/gitbutler") then
				return d
			end
			return nil
		end
		if d == "/" then
			return nil
		end
		dir = string.match(d, "^(.*)/[^/]*$")
	end
	return nil
end

export type Result = {
	ok: boolean,
	data: any, -- decoded JSON stdout (also on failure, when `but` printed a JSON error)
	err: string?, -- a short message on failure
	timed_out: boolean,
}

local function first_lines(text: string, n: number): string
	local out = {}
	for line in string.gmatch(text, "[^\n]+") do
		table.insert(out, line)
		if #out == n then
			break
		end
	end
	return table.concat(out, "\n")
end

-- Runs `but -C root <args...>` without blocking; `done` gets a Result.
-- `args` should include `--json`. Returns a spawn error, or nil.
function gb.run(root: string, args: { string }, timeout_ms: number, done: (Result) -> ()): string?
	local last = "`but` is not on this machine's PATH"
	for _, but in BUT do
		if but == "but" or tern.fs.exists(but) then
			local argv: { string } = { but, "-C", root }
			for _, a in args do
				table.insert(argv, a)
			end
			local opts: ProcessOptions = {
				cwd = root,
				timeout_ms = timeout_ms,
				env = { NO_COLOR = "1", GIT_TERMINAL_PROMPT = "0", BUT_PAGER = "cat" },
			}
			local ok, spawn_err = pcall(function()
				tern.process.run(argv, opts, function(r: ProcessResult, _)
					local decoded: any = nil
					if string.find(r.stdout, "^%s*[%[{]") then
						local jok, value = pcall(tern.json.decode, r.stdout)
						if jok then
							decoded = value
						end
					end
					if r.timed_out then
						done({ ok = false, data = nil, err = "but timed out after " .. (timeout_ms // 1000) .. "s", timed_out = true })
					elseif r.status ~= 0 then
						local msg = if type(decoded) == "table" and type(decoded.message) == "string"
							then decoded.message .. (if type(decoded.hint) == "string" then " (" .. decoded.hint .. ")" else "")
							else first_lines((string.gsub(r.stderr .. "\n" .. r.stdout, "^%s+", "")), 6)
						done({ ok = false, data = decoded, err = msg, timed_out = false })
					else
						done({ ok = true, data = decoded, err = nil, timed_out = false })
					end
				end)
			end)
			if ok then
				return nil
			end
			last = tostring(spawn_err)
		end
	end
	return last
end

export type File = { id: string, path: string, change: string }
export type Commit = { id: string, sha: string, changeId: string, title: string, message: string, conflicted: boolean, files: { File } }
export type Branch = {
	id: string,
	name: string, -- "" for an anonymous segment
	status: string, -- branchStatus
	review: string?,
	commits: { Commit },
	upstream: number,
}
export type Stack = { id: string, branches: { Branch }, assigned: { File } }
export type Model = {
	unassigned: { File },
	stacks: { Stack },
	behind: number,
}

local function str(v: any): string
	return if type(v) == "string" then v elseif type(v) == "number" then tostring(v) else ""
end

local function files(list: any): { File }
	local out: { File } = {}
	if type(list) == "table" then
		for _, f in list do
			if type(f) == "table" then
				table.insert(out, { id = str(f.cliId), path = str(f.filePath), change = str(f.changeType) })
			end
		end
	end
	return out
end

-- `but status [-f] --json` as a Model, or nil when it isn't one.
function gb.model(data: any): Model?
	if type(data) ~= "table" or type(data.stacks) ~= "table" then
		return nil
	end
	local stacks: { Stack } = {}
	for _, s in data.stacks do
		local branches: { Branch } = {}
		for _, b in (if type(s.branches) == "table" then s.branches else {}) do
			local commits: { Commit } = {}
			for _, c in (if type(b.commits) == "table" then b.commits else {}) do
				local message = str(c.message)
				table.insert(commits, {
					id = str(c.cliId),
					sha = str(c.commitId),
					changeId = str(c.changeId),
					title = string.match(message, "^[^\n]*") or "",
					message = (string.gsub(message, "%s+$", "")),
					conflicted = c.conflicted == true,
					files = files(c.changes),
				})
			end
			table.insert(branches, {
				id = str(b.cliId),
				name = str(b.name),
				status = str(b.branchStatus),
				review = if b.reviewId ~= nil then str(b.reviewId) else nil,
				commits = commits,
				upstream = if type(b.upstreamCommits) == "table" then #b.upstreamCommits else 0,
			})
		end
		table.insert(stacks, { id = str(s.cliId), branches = branches, assigned = files(s.assignedChanges) })
	end
	local behind = if type(data.upstreamState) == "table" then tonumber(data.upstreamState.behind) or 0 else 0
	return { unassigned = files(data.uncommittedChanges), stacks = stacks, behind = behind }
end

-- Unpushed commits on a branch, or nil when `branchStatus` alone can't
-- tell (some but not all commits pushed).
function gb.unpushed(b: Branch): number?
	if b.status == "completelyUnpushed" then
		return #b.commits
	elseif b.status == "nothingToPush" or b.status == "integrated" then
		return 0
	end
	return nil
end

-- How a branch is labeled: its name, or the anonymous segment's id.
function gb.label(b: Branch): string
	return if b.name ~= "" then b.name else "(unnamed " .. b.id .. ")"
end

-- `but diff --json` changes, with each patch joined into unified diff text.
export type Change = { path: string, status: string, text: string? }

function gb.changes(data: any): { Change }?
	if type(data) ~= "table" or type(data.changes) ~= "table" then
		return nil
	end
	local out: { Change } = {}
	for _, c in data.changes do
		local path = tostring(c.path or "?")
		local text: string? = nil
		local diff = c.diff
		if type(diff) == "table" and diff.type == "patch" and type(diff.hunks) == "table" then
			local parts = { "--- a/" .. path, "+++ b/" .. path }
			for _, h in diff.hunks do
				table.insert(parts, (string.gsub(tostring(h.diff or ""), "\n$", "")))
			end
			text = table.concat(parts, "\n")
		end
		table.insert(out, { path = path, status = tostring(c.status or ""), text = text })
	end
	return out
end

local CHANGE: { [string]: string } = { modified = "M", added = "A", deleted = "D", renamed = "R" }

-- One letter for a change type (`modified` → `M`).
function gb.letter(change: string): string
	return CHANGE[change] or string.upper(string.sub(change, 1, 1))
end

-- How each `branchStatus` reads, and its tone: the GitButler desktop app's
-- branch labels ("Diverged" is its lite app's name for a force push).
gb.STATUS_TEXT = {
	completelyUnpushed = { "Unpushed branch", "warning" },
	unpushedCommits = { "Some unpushed", "warning" },
	unpushedCommitsRequiringForce = { "Diverged", "error" },
	nothingToPush = { "Nothing to push", "success" },
	integrated = { "Integrated", "accent" },
} :: { [string]: { string } }

return gb
