-- PZLab harness core: namespace, logging, JSON encoding and the file helpers the external
-- controller (pzlab/pzlab.py) talks through. All files live under <cachedir>/Lua/pzlab/.

PZLab = PZLab or {}
PZLab.VERSION = "0.1.0"
PZLab.DIR = "pzlab/"

function PZLab.log(msg)
    print("[PZLab] " .. tostring(msg))
end

-- In -debug mode a Lua error opens the Lua debugger and halts all Lua (including this
-- harness) until someone clicks Resume, even an error caught by pcall. The lab must log errors
-- and keep running instead. GameWindow.enter() turns the option back on right after OnGameBoot,
-- i.e. after this file loads, so the bridge applies this again on its first tick.
function PZLab.disableDebuggerOnError()
    pcall(function() UIManager.setShowLuaDebuggerOnError(false) end)
end
PZLab.disableDebuggerOnError()

-- Repairs the compiler state a reloadLuaFile leaves behind when it ends in an exception (a
-- compile error, or LuaEventManager.reroute's NPE on a callback with a nil prototype name):
-- LuaCompiler.rewriteEvents stays on, so Events.X.Add/Remove do nothing and every new closure
-- is rerouted (see AGENT.md "Hot reload and events"). Reloading this file (reload-safe,
-- registers no events) runs LuaManager.RunLua to its end, which turns rewriting off again.
function PZLab.resetCompiler()
    return reloadLuaFile(getFilenameOfClosure(PZLab.log))
end

-- True while Events.X.Add/Remove are no-ops (LuaCompiler.rewriteEvents on): during a
-- reloadLuaFile, and after one that ended in an exception. Registers a probe on a private
-- event and fires it.
function PZLab.eventsRewriting()
    if not PZLab.probeEventAdded then
        LuaEventManager.AddEvent("PZLabEventProbe")
        PZLab.probeEventAdded = true
    end
    local hit = false
    local probe = function() hit = true end
    Events.PZLabEventProbe.Add(probe)
    triggerEvent("PZLabEventProbe")
    Events.PZLabEventProbe.Remove(probe)
    return not hit
end

-- Returns the whole file as a string, or nil when it doesn't exist.
function PZLab.readFile(name)
    local ok, reader = pcall(getFileReader, PZLab.DIR .. name, false)
    if not ok or not reader then return nil end
    local lines = {}
    local line = reader:readLine()
    while line ~= nil do
        lines[#lines + 1] = line
        line = reader:readLine()
    end
    reader:close()
    return table.concat(lines, "\n")
end

-- Writes (replaces) a file. Allowed extensions: txt, json, log, ini, cfg.
function PZLab.writeFile(name, content)
    local writer = getFileWriter(PZLab.DIR .. name, true, false)
    writer:write(content)
    writer:close()
end

---------------------------------------------------------------------------------------------
-- Handles: small integer ids for any Lua/Java value, so the controller can refer back to an
-- object across commands (PZLab.get(id)). The same object always gets the same id. Keys use
-- Lua table-key equality, i.e. Java equals(): value-equal Java objects share one handle.
---------------------------------------------------------------------------------------------

PZLab.handleTable = PZLab.handleTable or { byId = {}, byValue = {}, n = 0 }

function PZLab.handle(value)
    if value == nil then return nil end
    local H = PZLab.handleTable
    local id = H.byValue[value]
    if id then return id end
    H.n = H.n + 1
    H.byId[H.n] = value
    H.byValue[value] = H.n
    return H.n
end

function PZLab.get(id)
    return PZLab.handleTable.byId[tonumber(id)]
end

-- Forgets every handle (ids are not reused).
function PZLab.clearHandles()
    local H = PZLab.handleTable
    H.byId, H.byValue = {}, {}
    return H.n
end

---------------------------------------------------------------------------------------------
-- JSON encoding (results and state). Java objects become {"__java": class, "str": toString,
-- "handle": id}; java.util.List contents are expanded. UI element tables (anything with a
-- javaObject) become a summary {"__ui": Type, "handle", "title", "x", "y", "w", "h",
-- "visible"} instead of their whole field tree. Depth, size and cycles are bounded.
---------------------------------------------------------------------------------------------

local MAX_DEPTH = 8
local MAX_ITEMS = 400

local function escapeChar(c)
    if c == '"' then return '\\"' end
    if c == "\\" then return "\\\\" end
    if c == "\n" then return "\\n" end
    if c == "\r" then return "\\r" end
    if c == "\t" then return "\\t" end
    return string.format("\\u%04x", string.byte(c))
end

local function encodeString(s)
    return '"' .. string.gsub(s, '[%c"\\]', escapeChar) .. '"'
end

local function className(obj)
    local ok, name = pcall(getClassSimpleName, obj)
    if ok and name then return tostring(name) end
    return "?"
end

local encodeValue

local function encodeUIElement(t)
    local j = t.javaObject
    local summary = { __ui = t.Type or className(j), handle = PZLab.handle(t) }
    local ok = pcall(function()
        summary.x, summary.y = j:getAbsoluteX(), j:getAbsoluteY()
        summary.w, summary.h = j:getWidth(), j:getHeight()
        summary.visible = j:isVisible()
    end)
    if not ok then summary.instantiated = false end
    if type(t.title) == "string" then summary.title = t.title end
    local parts = {}
    for k, v in pairs(summary) do
        parts[#parts + 1] = encodeString(k) .. ":" .. encodeValue(v, 0, {})
    end
    return "{" .. table.concat(parts, ",") .. "}"
end

local function encodeTable(t, depth, seen)
    if seen[t] then return '"<cycle>"' end
    if rawget(t, "javaObject") ~= nil then return encodeUIElement(t) end
    seen[t] = true
    local n = #t
    local isArray = n > 0
    if isArray then
        local count = 0
        for _ in pairs(t) do count = count + 1 end
        isArray = count == n
    end
    local parts = {}
    if isArray then
        for i = 1, math.min(n, MAX_ITEMS) do
            parts[#parts + 1] = encodeValue(t[i], depth + 1, seen)
        end
        if n > MAX_ITEMS then parts[#parts + 1] = '"<+' .. (n - MAX_ITEMS) .. ' more>"' end
        seen[t] = nil
        return "[" .. table.concat(parts, ",") .. "]"
    end
    local count = 0
    for k, v in pairs(t) do
        count = count + 1
        if count > MAX_ITEMS then
            parts[#parts + 1] = '"<truncated>":true'
            break
        end
        parts[#parts + 1] = encodeString(tostring(k)) .. ":" .. encodeValue(v, depth + 1, seen)
    end
    seen[t] = nil
    return "{" .. table.concat(parts, ",") .. "}"
end

local function encodeJava(obj, depth, seen)
    local cls = className(obj)
    if string.find(cls, "List", 1, true) then
        local ok, size = pcall(function() return obj:size() end)
        if ok then
            local parts = {}
            for i = 0, math.min(size, MAX_ITEMS) - 1 do
                parts[#parts + 1] = encodeValue(obj:get(i), depth + 1, seen)
            end
            return '{"__java":' .. encodeString(cls) .. ',"size":' .. size .. ',"items":[' .. table.concat(parts, ",") .. "]}"
        end
    end
    local okHandle, handle = pcall(PZLab.handle, obj)
    return '{"__java":' .. encodeString(cls) .. ',"str":' .. encodeString(tostring(obj))
        .. (okHandle and handle and (',"handle":' .. handle) or "") .. "}"
end

encodeValue = function(v, depth, seen)
    local t = type(v)
    if v == nil then return "null" end
    if t == "boolean" then return v and "true" or "false" end
    if t == "number" then
        if v ~= v or v == math.huge or v == -math.huge then return "null" end
        if v == math.floor(v) and math.abs(v) < 1e15 then return string.format("%d", v) end
        return tostring(v)
    end
    if t == "string" then return encodeString(v) end
    if depth > MAX_DEPTH then return '"<max depth>"' end
    if t == "table" then return encodeTable(v, depth, seen) end
    if t == "function" then return '"<function>"' end
    return encodeJava(v, depth, seen)
end

function PZLab.json(v)
    return encodeValue(v, 0, {})
end
