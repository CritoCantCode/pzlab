-- PZLab harness core: namespace, logging, JSON encoding and the file helpers the external
-- controller (pzlab/pzlab.py) talks through. All files live under <cachedir>/Lua/pzlab/.

PZLab = PZLab or {}
PZLab.VERSION = "0.1.0"
PZLab.DIR = "pzlab/"

function PZLab.log(msg)
    print("[PZLab] " .. tostring(msg))
end

-- In -debug mode a Lua error opens the Lua debugger and halts all Lua (including this
-- harness) until someone clicks Resume. The lab must log errors and keep running instead.
pcall(function() UIManager.setShowLuaDebuggerOnError(false) end)

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
-- JSON encoding (results and state). Java objects become {"__java": class, "str": toString};
-- java.util.List contents are expanded. Depth, size and cycles are bounded.
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

local function encodeTable(t, depth, seen)
    if seen[t] then return '"<cycle>"' end
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
    return '{"__java":' .. encodeString(cls) .. ',"str":' .. encodeString(tostring(obj)) .. "}"
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
