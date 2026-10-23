-- PZLab command bridge. For each command the controller writes pzlab/cmd-<seq>.txt:
--   line 1: "PZLAB <seq> <timeoutMs>"
--   rest:   a Lua chunk, run synchronously within one tick.
-- then, once that file is closed, creates the empty flag pzlab/cmd-<seq>.go, and deletes both
-- after the reply. The harness only lists the directory (no open) to find flags, and opens a
-- command file once, after its flag exists and before the controller deletes it: a file the
-- harness opens is never being written, replaced or deleted at that moment. (On Windows an open
-- racing a delete/replace/truncate fails with a sharing violation, which getFileReader logs as
-- "SEVERE: null java.io.FileNotFoundException" with a stack trace.) seq is a decimal number,
-- increasing; only flags newer than the last command run are taken.
-- The chunk's first return value goes to pzlab/res.json as
--   {"seq", "ok", "value" | "error", "out", "frames", "ms"}.
-- A chunk that returns PZLab.steps{...} becomes a multi-tick job (see PZLab.steps).
-- pzlab/state.json is rewritten twice a second as a heartbeat.
--
-- No coroutines: Kahlua (42.21) throws an NPE in KahluaThread.luaMainloop when a coroutine
-- finishes inside pcall from an event handler, and the event then stops firing.

require "PZLab/PZLab_Core"

local POLL_MS = 50
local HEARTBEAT_MS = 500

local Bridge = PZLab.Bridge or {}
PZLab.Bridge = Bridge
Bridge.ticks = Bridge.ticks or 0
Bridge.nextPoll = 0
Bridge.nextBeat = 0
Bridge.errorCounts = Bridge.errorCounts or {}

-- Named functions run once per tick (e.g. autostart). Keyed by name so reloads replace them.
PZLab.tickHooks = PZLab.tickHooks or {}

-- Logs the first few failures of each kind, then stays quiet so a bug can't flood the log.
local function reportError(kind, err)
    local n = (Bridge.errorCounts[kind] or 0) + 1
    Bridge.errorCounts[kind] = n
    if n <= 3 then PZLab.log("ERROR in " .. kind .. " (" .. n .. "): " .. tostring(err)) end
end

---------------------------------------------------------------------------------------------
-- Helpers available to command chunks
---------------------------------------------------------------------------------------------

-- A multi-tick job. Entries run in order, one per tick:
--   function(ctx)       called; its return value becomes ctx.last (the job's result at the end)
--   number n            wait n frames
--   PZLab.waitMs(ms)    wait ms milliseconds
--   PZLab.waitUntil(fn, timeoutMs)  wait until fn(ctx) is truthy (error on timeout)
-- ctx is a table shared by all entries.
function PZLab.steps(list)
    return { __pzlabSteps = true, list = list }
end

function PZLab.waitMs(ms)
    return { ms = ms }
end

function PZLab.waitUntil(fn, timeoutMs)
    return { untilFn = fn, timeout = timeoutMs or 5000 }
end

-- Appends a line to the running command's "out" list (or the log outside a command).
function PZLab.out(...)
    local args = { ... }
    local parts = {}
    for i = 1, #args do parts[i] = tostring(args[i]) end
    local line = table.concat(parts, " ")
    local c = PZLab.currentCommand
    if c then c.out[#c.out + 1] = line else PZLab.log(line) end
end

---------------------------------------------------------------------------------------------
-- Command lifecycle
---------------------------------------------------------------------------------------------

local function writeResult(c, ok, value, err)
    PZLab.writeFile("res.json", PZLab.json({
        seq = c.seq, ok = ok, value = value, error = err, out = c.out,
        frames = c.frames, ms = getTimestampMs() - c.started,
    }))
end

local function finish(c, ok, value, err)
    Bridge.current = nil
    local written, werr = pcall(writeResult, c, ok, value, err)
    if not written then
        pcall(writeResult, c, false, nil, "could not encode result: " .. tostring(werr))
    end
end

-- Runs fn(arg) with PZLab.currentCommand set so PZLab.out reaches the command's output.
local function run(c, fn, arg)
    PZLab.currentCommand = c
    local ok, value = pcall(fn, arg)
    PZLab.currentCommand = nil
    return ok, value
end

-- Advances a steps job by at most one entry. Returns nothing; finishes the command when done.
local function step(c)
    local now = getTimestampMs()
    if now > c.deadline then
        finish(c, false, nil, "timeout after " .. c.timeout .. " ms at step " .. c.index)
        return
    end
    if c.wakeAt then
        if now < c.wakeAt then return end
        c.wakeAt = nil
    end
    if c.waitFrames and c.waitFrames > 0 then
        c.waitFrames = c.waitFrames - 1
        return
    end

    local entry = c.list[c.index]
    if entry == nil then
        finish(c, true, c.ctx.last)
        return
    end
    local kind = type(entry)
    if kind == "function" then
        local ok, value = run(c, entry, c.ctx)
        if not ok then
            finish(c, false, nil, "step " .. c.index .. ": " .. tostring(value))
            return
        end
        c.ctx.last = value
    elseif kind == "number" then
        c.waitFrames = entry
    elseif kind == "table" and entry.ms then
        c.wakeAt = now + entry.ms
    elseif kind == "table" and entry.untilFn then
        c.untilDeadline = c.untilDeadline or (now + entry.timeout)
        local ok, done = run(c, entry.untilFn, c.ctx)
        if not ok then
            finish(c, false, nil, "step " .. c.index .. " (waitUntil): " .. tostring(done))
            return
        end
        if not done then
            if now > c.untilDeadline then
                finish(c, false, nil, "step " .. c.index .. ": waitUntil timed out")
            end
            return
        end
        c.untilDeadline = nil
    else
        finish(c, false, nil, "step " .. c.index .. ": unsupported entry type " .. kind)
        return
    end
    c.index = c.index + 1
    if c.index > #c.list and kind == "function" then
        finish(c, true, c.ctx.last)
    end
end

-- Decimal sequence strings without leading zeros compare by length, then lexically.
local function newer(a, b)
    if b == nil then return true end
    if #a ~= #b then return #a > #b end
    return a > b
end

-- The oldest command flag (cmd-<seq>.go) newer than the last command run, or nil.
local function nextFlag()
    local ok, files = pcall(listFilesInZomboidLuaDirectory, "pzlab")
    if not ok or not files then return nil end
    local best
    for i = 0, files:size() - 1 do
        local seq = string.match(tostring(files:get(i)), "^cmd%-(%d+)%.go$")
        if seq and newer(seq, Bridge.lastSeq) and (best == nil or newer(best, seq)) then best = seq end
    end
    return best
end

-- A fresh Lua state (game start, return to the main menu) must not rerun a command whose
-- flag the controller hasn't deleted yet (e.g. one sent fire-and-forget): resume after the
-- last answered one.
if Bridge.lastSeq == nil then
    local res = PZLab.readFile("res.json")
    Bridge.lastSeq = res and string.match(res, '"seq":"(%d+)"') or nil
end

local function poll()
    if Bridge.current then return end
    local flagSeq = nextFlag()
    if not flagSeq then return end
    Bridge.lastSeq = flagSeq
    local text = PZLab.readFile("cmd-" .. flagSeq .. ".txt")
    if not text or text == "" then return end
    local seq, timeout, body = string.match(text, "^PZLAB (%S+) (%d+)\n(.*)$")
    if seq ~= flagSeq then
        finish({ seq = flagSeq, out = {}, frames = 0, started = getTimestampMs() }, false, nil,
            "malformed command file cmd-" .. flagSeq .. ".txt (expected 'PZLAB " .. flagSeq .. " <timeoutMs>' on line 1)")
        return
    end

    local c = { seq = seq, timeout = tonumber(timeout), started = getTimestampMs(), out = {}, frames = 0 }
    c.deadline = c.started + c.timeout
    local fn, err = loadstring(body, "pzlab")
    if not fn and not string.match(tostring(err), "^pzlab:%d+:") then
        -- Not a syntax error ("pzlab:<line>: ..."): creating the chunk's closure failed. Seen
        -- after a reloadLuaFile ended in an exception with rewriteEvents left on, when a
        -- callback of the same file has a nil prototype name (reroute NPE): every command
        -- would fail. Reset the compiler and retry once.
        pcall(PZLab.resetCompiler)
        fn, err = loadstring(body, "pzlab")
        if fn then c.out[#c.out + 1] = "[pzlab] compiler reset: event rewriting was stuck on after a failed reloadLuaFile" end
    end
    if not fn then
        finish(c, false, nil, "compile error: " .. tostring(err))
        return
    end
    local ok, value = run(c, fn)
    if not ok then
        finish(c, false, nil, tostring(value))
    elseif type(value) == "table" and value.__pzlabSteps then
        c.list, c.index, c.ctx = value.list, 1, {}
        Bridge.current = c
    else
        finish(c, true, value)
    end
end

local function heartbeat()
    local player = getSpecificPlayer(0)
    local state = {
        v = PZLab.VERSION,
        t = getTimestampMs(),
        ticks = Bridge.ticks,
        state = player and "ingame" or "menu",
        seq = Bridge.lastSeq,
        busy = Bridge.current ~= nil,
        mouse = { getMouseX(), getMouseY() },
        screen = { getCore():getScreenWidth(), getCore():getScreenHeight() },
    }
    if player then
        state.player = { x = player:getX(), y = player:getY(), z = player:getZ(), dead = player:isDead() }
        local okSpeed, speed = pcall(getGameSpeed)
        if okSpeed then state.speed = speed end
        if PZLab.isPauseMenuOpen then
            local okMenu, open = pcall(PZLab.isPauseMenuOpen)
            if okMenu then state.pauseMenu = open end
        end
    end
    PZLab.writeFile("state.json", PZLab.json(state))
end

-- One tick per frame: from OnFETick at the menu, OnTickEvenPaused in game.
function PZLab.tick()
    Bridge.ticks = Bridge.ticks + 1
    if not Bridge.debuggerOff then
        Bridge.debuggerOff = true
        PZLab.disableDebuggerOnError()
    end
    local c = Bridge.current
    if c then
        c.frames = c.frames + 1
        local ok, err = pcall(step, c)
        if not ok then
            reportError("step", err)
            finish(c, false, nil, "harness error: " .. tostring(err))
        end
    end
    local now = getTimestampMs()
    if now >= Bridge.nextPoll then
        Bridge.nextPoll = now + POLL_MS
        local ok, err = pcall(poll)
        if not ok then reportError("poll", err) end
    end
    if now >= Bridge.nextBeat then
        Bridge.nextBeat = now + HEARTBEAT_MS
        local ok, err = pcall(heartbeat)
        if not ok then reportError("heartbeat", err) end
    end
    for name, hook in pairs(PZLab.tickHooks) do
        local ok, err = pcall(hook)
        if not ok then reportError("tickHook " .. name, err) end
    end
end

-- Reload-safe registration: the previous handler under the same name is removed first.
PZLab.handlers = PZLab.handlers or {}
local function bind(eventName, fn)
    if PZLab.handlers[eventName] then Events[eventName].Remove(PZLab.handlers[eventName]) end
    PZLab.handlers[eventName] = fn
    Events[eventName].Add(fn)
end

bind("OnFETick", function()
    if not getSpecificPlayer(0) then PZLab.tick() end
end)
bind("OnTickEvenPaused", function()
    if getSpecificPlayer(0) then PZLab.tick() end
end)

PZLab.log("bridge ready (v" .. PZLab.VERSION .. ")")
