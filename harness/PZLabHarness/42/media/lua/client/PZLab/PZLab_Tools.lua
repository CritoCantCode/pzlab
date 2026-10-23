-- PZLab client helpers for command chunks: teleport, projection to screen pixels, picking and
-- hover checks, UI inspection, items, Lua file loading. World access and inspection shared with
-- the server (player, square, describeObject, whereIs, ...) are in shared/PZLab_Inspect.lua.

require "PZLab/PZLab_Core"
require "PZLab/PZLab_Inspect"

local P = 0

local function className(obj)
    local ok, name = pcall(getClassSimpleName, obj)
    return ok and tostring(name) or "?"
end

-- Whole-number x/y land on the tile centre; fractional ones are used as given.
-- (Kahlua resolves teleportTo(x, y, z) to the int overload, which truncates to the tile
-- corner, so the exact position is set afterwards.)
-- Multiplayer: asks the server (`/teleportto`, admin) and returns at once with sent = true; the
-- player arrives a few frames later. Use PZLab.gotoLocation / lab.goto, which wait for it.
function PZLab.teleport(x, y, z)
    local player = PZLab.player()
    local fx = (x == math.floor(x)) and x + 0.5 or x
    local fy = (y == math.floor(y)) and y + 0.5 or y
    if isClient() then
        SendCommandToServer(string.format("/teleportto %.3f,%.3f,%d", fx, fy, math.floor(z or 0)))
        return { x = fx, y = fy, z = z or 0, sent = true }
    end
    player:teleportTo(math.floor(fx), math.floor(fy), z or 0)
    player:setX(fx)
    player:setY(fy)
    player:setLastX(fx)
    player:setLastY(fy)
    return { x = player:getX(), y = player:getY(), z = player:getZ() }
end

---------------------------------------------------------------------------------------------
-- Projection (world -> UI pixels, the coordinate space of getMouseX/Y and UI elements)
---------------------------------------------------------------------------------------------

function PZLab.toScreen(x, y, z)
    return { x = isoToScreenX(P, x, y, z or 0), y = isoToScreenY(P, x, y, z or 0) }
end

-- Centre of a tile object's visible sprite, accounting for zoom and stacking offset;
-- falls back to the floor centre of its square. Corpses and vehicles (3D models): their
-- world position; floor items: their position on the floor (B42 draws them as 3D models).
function PZLab.objectScreenPos(obj)
    if instanceof(obj, "IsoWorldInventoryObject") then
        return PZLab.toScreen(obj:getWorldPosX(), obj:getWorldPosY(), obj:getWorldPosZ())
    end
    if instanceof(obj, "IsoMovingObject") then
        return PZLab.toScreen(obj:getX(), obj:getY(), obj:getZ())
    end
    local sq = obj:getSquare()
    if not sq then return nil end
    local x, y, z = sq:getX(), sq:getY(), sq:getZ()
    local sprite = obj:getSprite()
    local tex = sprite and sprite:getTextureForCurrentFrame(obj:getDir())
    if not tex then return PZLab.toScreen(x + 0.5, y + 0.5, z) end
    local zoom = getCore():getZoom(P)
    local bx, by = isoToScreenX(P, x, y, z), isoToScreenY(P, x, y, z)
    local offsetY = obj:getOffsetY() + obj:getRenderYOffset() * Core.getTileScale()
    return {
        x = bx + (tex:getOffsetX() + tex:getWidth() / 2 - obj:getOffsetX()) / zoom,
        y = by + (tex:getOffsetY() + tex:getHeight() / 2 - offsetY) / zoom,
    }
end

-- Screen points on opaque pixels of the object's sprite, nearest to the mask centroid first,
-- spaced at least `spacing` texture pixels apart. Moving the mouse to one of them makes the
-- engine pick this object unless something is drawn in front there (cutaway walls, other
-- objects). Texture:isMaskSet takes full-tile coordinates (verified in 42.21).
local function spriteAimPoints(obj, maxPoints, spacing)
    local sq = obj:getSquare()
    local sprite = obj:getSprite()
    local tex = sprite and sprite:getTextureForCurrentFrame(obj:getDir())
    if not (sq and tex and tex:getMask()) then return {} end
    local pts, sx, sy = {}, 0, 0
    for py = 0, tex:getHeightOrig() - 1, 3 do
        for px = 0, tex:getWidthOrig() - 1, 3 do
            if tex:isMaskSet(px, py) then
                pts[#pts + 1] = { px, py }
                sx, sy = sx + px, sy + py
            end
        end
    end
    if #pts == 0 then return {} end
    local cx, cy = sx / #pts, sy / #pts
    for _, p in ipairs(pts) do
        p[3] = (p[1] - cx) * (p[1] - cx) + (p[2] - cy) * (p[2] - cy)
    end
    table.sort(pts, function(a, b) return a[3] < b[3] end)

    local zoom = getCore():getZoom(P)
    local x, y, z = sq:getX(), sq:getY(), sq:getZ()
    local bx, by = isoToScreenX(P, x, y, z), isoToScreenY(P, x, y, z)
    local offsetX = obj:getOffsetX()
    local offsetY = obj:getOffsetY() + obj:getRenderYOffset() * Core.getTileScale()
    local sw, sh = getCore():getScreenWidth(), getCore():getScreenHeight()
    local chosen, out = {}, {}
    for _, p in ipairs(pts) do
        local farEnough = true
        for _, q in ipairs(chosen) do
            if math.abs(p[1] - q[1]) < spacing and math.abs(p[2] - q[2]) < spacing then
                farEnough = false
                break
            end
        end
        local px, py = bx + (p[1] - offsetX) / zoom, by + (p[2] - offsetY) / zoom
        if farEnough and px >= 0 and py >= 0 and px < sw and py < sh then
            chosen[#chosen + 1] = p
            out[#out + 1] = { x = px, y = py }
            if #out >= maxPoints then break end
        end
    end
    return out
end

-- IsoObjectPicker coordinates (42.21, verified): every Pick* method multiplies its arguments by
-- Core.getZoom(0) itself, i.e. takes UI pixels (getMouseX/Y), except PickVehicle, which hands
-- them straight to IsoUtils.XToIso and so needs UI pixels x zoom (getMouseXScaled/YScaled, as
-- ISVehicleMenu does). PickVehicle only tests at the floor player 0 stands on.
-- Both helpers below take UI pixels; all of pzlab picks through them.
function PZLab.pickCorpse(x, y)
    return IsoObjectPicker.Instance:PickCorpse(math.floor(x), math.floor(y))
end

function PZLab.pickVehicle(x, y)
    local zoom = getCore():getZoom(P)
    return IsoObjectPicker.Instance:PickVehicle(math.floor(x * zoom), math.floor(y * zoom))
end

local pickCorpse, pickVehicle = PZLab.pickCorpse, PZLab.pickVehicle

-- Corpses and vehicles are 3D models with their own pickers (PZLab.pickCorpse / pickVehicle
-- resolve any screen point), so their aim points are found by asking the picker over a grid
-- around the object's projected position: hits nearest the hit centroid first.
local function pickerAimPoints(obj, pick, halfW, halfH, step, maxPoints, spacing)
    local zoom = getCore():getZoom(P)
    local c = PZLab.objectScreenPos(obj)
    local sw, sh = getCore():getScreenWidth(), getCore():getScreenHeight()
    halfW, halfH, step = halfW / zoom, halfH / zoom, math.max(2, step / zoom)
    local hits, sx, sy = {}, 0, 0
    for y = math.max(0, c.y - halfH), math.min(sh - 1, c.y + halfH), step do
        for x = math.max(0, c.x - halfW), math.min(sw - 1, c.x + halfW), step do
            if pick(math.floor(x), math.floor(y)) == obj then
                hits[#hits + 1] = { math.floor(x), math.floor(y) }
                sx, sy = sx + math.floor(x), sy + math.floor(y)
            end
        end
    end
    if #hits == 0 then return {} end
    local cx, cy = sx / #hits, sy / #hits
    for _, p in ipairs(hits) do
        p[3] = (p[1] - cx) * (p[1] - cx) + (p[2] - cy) * (p[2] - cy)
    end
    table.sort(hits, function(a, b) return a[3] < b[3] end)
    local out = {}
    for _, p in ipairs(hits) do
        local farEnough = true
        for _, q in ipairs(out) do
            if math.abs(p[1] - q.x) < spacing and math.abs(p[2] - q.y) < spacing then
                farEnough = false
                break
            end
        end
        if farEnough then
            out[#out + 1] = { x = p[1], y = p[2] }
            if #out >= maxPoints then break end
        end
    end
    return out
end

-- The visible UI element at (x, y) that keeps the engine from picking the world there, click-
-- through elements included, or false (PZLab.pickBlockedBy, defined in PZLab_UIState, which
-- loads after this file). Clicks are a different question: PZLab.uiAt.
local function pickBlocker(x, y)
    return PZLab.pickBlockedBy ~= nil and PZLab.pickBlockedBy(x, y) or false
end

-- Screen points where moving the mouse should make the engine pick `obj`, best first; callers
-- try them in order and confirm with PZLab.isHovered (labkit Lab.hover does).
--   tile objects: opaque pixels of the sprite (empty when it has no mask)
--   corpses / vehicles: points the corpse / vehicle picker resolves to this object
--   floor items: the item's floor position first (B42 draws them as 3D models, which the
--                object picker never returns; see PZLab.isHovered), then sprite pixels
-- Only points on the screen. Up to maxPoints points clear of UI come first; then up to
-- maxPoints points covered by visible UI (PZLab.pickBlockedBy), each with ui = <that element's
-- type> and clickThrough (true when that element lets clicks through): the engine picks no world
-- object while the cursor is over a visible top-level element, consuming mouse events or not.
function PZLab.aimPoints(obj, maxPoints, spacing)
    maxPoints, spacing = maxPoints or 12, spacing or 10
    local wanted = maxPoints * 4 -- extra candidates, so UI covering part of the object still leaves some
    local candidates
    if instanceof(obj, "IsoDeadBody") then
        candidates = pickerAimPoints(obj, pickCorpse, 128, 96, 4, wanted, spacing)
    elseif instanceof(obj, "BaseVehicle") then
        candidates = pickerAimPoints(obj, pickVehicle, 320, 224, 8, wanted, spacing)
    elseif instanceof(obj, "IsoWorldInventoryObject") then
        local base = PZLab.objectScreenPos(obj)
        candidates = { { x = base.x, y = base.y - 2 } }
        for _, p in ipairs(spriteAimPoints(obj, wanted - 1, spacing)) do candidates[#candidates + 1] = p end
    else
        candidates = spriteAimPoints(obj, wanted, spacing)
    end
    local clear, covered = {}, {}
    for _, p in ipairs(candidates) do
        local ui = pickBlocker(p.x, p.y)
        if ui then
            p.ui, p.clickThrough = ui.type, ui.clickThrough
            if #covered < maxPoints then covered[#covered + 1] = p end
        elseif #clear < maxPoints then
            clear[#clear + 1] = p
        end
    end
    for _, p in ipairs(covered) do clear[#clear + 1] = p end
    return clear
end

-- Whether a real cursor at screen point (x, y) (default: the mouse) would have `obj` picked:
--   corpses: PZLab.pickCorpse; vehicles: PZLab.pickVehicle;
--   floor items: the picked object is the item itself or stands on its square (B42 renders
--     floor items as 3D models the object picker never returns; the world context menu
--     collects floor items from the picked square, ISWorldObjectContextMenuLogic.fetch);
--   everything else: UIManager.getLastPicked() (mouse only).
-- False when visible UI covers the point (PZLab.pickBlockedBy: click-through elements too),
-- unless allowUI: getLastPicked() is nil while the cursor is over any visible top-level element.
function PZLab.isHovered(obj, x, y, allowUI)
    if type(obj) == "number" then obj = PZLab.get(obj) end
    if obj == nil then return false end
    x, y = math.floor(x or getMouseX()), math.floor(y or getMouseY())
    if not allowUI and pickBlocker(x, y) then return false end
    if instanceof(obj, "IsoDeadBody") then return pickCorpse(x, y) == obj end
    if instanceof(obj, "BaseVehicle") then return pickVehicle(x, y) == obj end
    local picked = UIManager.getLastPicked()
    if picked == obj then return true end
    if instanceof(obj, "IsoWorldInventoryObject") and picked then
        local sq = obj:getSquare()
        return sq ~= nil and picked:getSquare() == sq
    end
    return false
end

---------------------------------------------------------------------------------------------
-- Inspection
---------------------------------------------------------------------------------------------

-- Top-level UI elements, front-most last.
function PZLab.ui(includeHidden)
    local list = UIManager.getUI()
    local out = {}
    for i = 0, list:size() - 1 do
        local e = list:get(i)
        local visible = e:isVisible()
        if visible or includeHidden then
            local t = e:getTable()
            out[#out + 1] = {
                type = t and t.Type or className(e),
                name = e:getUIName(),
                title = t and t.title or nil,
                x = e:getX(), y = e:getY(), w = e:getWidth(), h = e:getHeight(),
                visible = visible,
            }
        end
    end
    return out
end

-- Closes every top-level UI element whose Lua Type (or, for Java-only elements such as
-- UIDebugConsole, Java class name) matches. Lua elements are removed; Java ones are hidden.
-- Returns the count.
function PZLab.closeUI(typeName)
    local list = UIManager.getUI()
    local luaTargets, javaTargets = {}, {}
    for i = 0, list:size() - 1 do
        local e = list:get(i)
        local t = e:getTable()
        if t and t.Type == typeName then
            luaTargets[#luaTargets + 1] = t
        elseif not t and className(e) == typeName then
            javaTargets[#javaTargets + 1] = e
        end
    end
    for _, t in ipairs(luaTargets) do
        if t.close then pcall(t.close, t) end
        t:setVisible(false)
        t:removeFromUIManager()
    end
    for _, e in ipairs(javaTargets) do
        e:setVisible(false)
    end
    return #luaTargets + #javaTargets
end

-- Mouse position and the object the engine currently has picked under it.
function PZLab.mouse()
    return {
        x = getMouseX(), y = getMouseY(),
        picked = PZLab.describeObject(UIManager.getLastPicked()),
    }
end

---------------------------------------------------------------------------------------------
-- Items and misc (lab setup only; mods under test must use vanilla timed actions)
---------------------------------------------------------------------------------------------

function PZLab.give(fullType, count)
    local inv = PZLab.player():getInventory()
    local added = {}
    for _ = 1, count or 1 do
        added[#added + 1] = inv:AddItem(fullType)
    end
    return #added
end

function PZLab.screenshot(name)
    takeScreenshot(name or "pzlab")
    return true
end

---------------------------------------------------------------------------------------------
-- Lua files through the engine's own file loader (LuaManager.RunLua). reloadLuaFile only takes
-- paths under a prefix ZomboidFileSystem.validatePrefix accepts: the cachedir (the lab
-- profile), an active mod's folder or the game folder. The controller writes the files
-- (lab.load_lua: <profile>/lua-scratch/); Lua can't (getFileWriter refuses .lua).
---------------------------------------------------------------------------------------------

-- Hot-reloads a Lua file by absolute path (vanilla reloadLuaFile).
function PZLab.reload(path)
    return reloadLuaFile(path)
end

-- path -> times PZLab.loadLua ran it in this Lua session
PZLab.luaFileLoads = PZLab.luaFileLoads or {}

local function fileName(path)
    return string.match(path, "([^/\\]+)$") or path
end

-- Checked before PZLab.loadLua: { loads = PZLab.luaFileLoads[path] or 0, compileError = the
-- compiler's message or nil }. A loadstring compile error logs nothing, whereas reloadLuaFile
-- on a file that doesn't compile logs it and leaves event rewriting on (see AGENT.md traps).
function PZLab.luaFileInfo(path, source)
    local _, err = loadstring(source, fileName(path))
    return { loads = PZLab.luaFileLoads[path] or 0, compileError = err }
end

-- Runs the Lua file at path (absolute, '/'-separated) through the engine's file loader.
-- hot = true: reloadLuaFile(path), the engine's hot reload: LuaCompiler.rewriteEvents is on
--   while it runs, so Events.X.Add/Remove do nothing and every closure it creates replaces
--   registered event callbacks with the same prototype file and name (LuaEventManager.reroute).
-- hot = false: like the engine's boot-time load of a mod file (no rewriting: Events.X.Add
--   registers). The file at path must be empty now: reloadLuaFile(path) then only points the
--   compiler at path (FuncState.currentFile/currentfullFile, which every compiled function
--   records), and source, compiled right after, gets the prototypes RunLua would give it.
-- afterPath (optional): an empty file run last, so that code compiled later (bridge chunks)
--   is attributed to it rather than to path.
-- Returns { path, hot, loads, value (the chunk's first return value), error (hot = false: the
-- chunk's runtime error; the engine logs it either way) }.
function PZLab.loadLua(path, source, hot, afterPath)
    local loads = (PZLab.luaFileLoads[path] or 0) + 1
    PZLab.luaFileLoads[path] = loads
    local result = { path = path, hot = hot == true, loads = loads }
    if hot then
        result.value = reloadLuaFile(path)
    else
        reloadLuaFile(path)
        local fn, err = loadstring(source, fileName(path))
        if not fn then
            result.error = "compile error: " .. tostring(err)
        else
            local ok, value = pcall(fn)
            if ok then result.value = value else result.error = tostring(value) end
        end
    end
    if afterPath then reloadLuaFile(afterPath) end
    return result
end

---------------------------------------------------------------------------------------------
-- Timed actions
---------------------------------------------------------------------------------------------

-- True while the player has timed actions queued or running (walk-to, transfers, ...) or is in
-- an action state (climbing, opening windows); the same test vanilla uses plus the Lua queue.
function PZLab.playerBusy(player)
    player = player or PZLab.player()
    if not player then return false end
    local queue = ISTimedActionQueue.queues[player]
    if queue and #queue.queue > 0 then return true end
    return ISTimedActionQueue.isPlayerDoingAction(player) and true or false
end

-- The player's timed-action queue: { busy, queued = { Type, ... } } (first = running).
function PZLab.actionQueue(player)
    player = player or PZLab.player()
    local queue = ISTimedActionQueue.queues[player]
    local types = {}
    if queue then
        for i, action in ipairs(queue.queue) do types[i] = action.Type or tostring(action) end
    end
    return { busy = PZLab.playerBusy(player), queued = types }
end

-- Timed-action tracing: the lifecycle of every action of the traced classes, as it happens. Wraps
-- the classes' methods (waitToStart, start, update, perform, stop, forceComplete, forceStop) until
-- PZLab.traceActions(false); the originals are restored exactly (inherited methods stay
-- inherited). Records: PZLab.actionTrace() -> { n, type, item = {type, id}, src, dest (container
-- types), queued, first (first call), started, ended (ms since tracing began), result =
-- "performed" | "stopped" | nil (still running), waits (waitToStart calls), updates, maxTime
-- (latest; -1 = an MP client still waiting for the server's duration), events = { {name, t,
-- maxTime} } }. In multiplayer an ISInventoryTransferAction asks the server for an item
-- transaction in start(), waits for its duration (maxTime -1 until then) and ends with
-- forceComplete (done) or forceStop (rejected); one that never hears back never ends.
PZLab.actionTraces = PZLab.actionTraces or {}
PZLab.actionTraceOriginals = PZLab.actionTraceOriginals or {}
local TRACED_METHODS = { "waitToStart", "start", "update", "perform", "stop", "forceComplete", "forceStop" }
local MAX_TRACE_EVENTS = 40

local function traceNow()
    return getTimestampMs() - (PZLab.actionTraceStart or getTimestampMs())
end

-- The trace record of an action (created on first sight); `queued` when called before it runs.
function PZLab.traceAction(action, queued)
    local rec = rawget(action, "__pzlabTrace")
    if rec then return rec end
    rec = { n = #PZLab.actionTraces + 1, type = action.Type, events = {}, waits = 0, updates = 0 }
    local item = rawget(action, "item")
    if item and instanceof(item, "InventoryItem") then rec.item = { type = item:getFullType(), id = item:getID() } end
    local src, dest = rawget(action, "srcContainer"), rawget(action, "destContainer")
    if src and instanceof(src, "ItemContainer") then rec.src = src:getType() end
    if dest and instanceof(dest, "ItemContainer") then rec.dest = dest:getType() end
    if queued then rec.queued = traceNow() else rec.first = traceNow() end
    rawset(action, "__pzlabTrace", rec)
    PZLab.actionTraces[#PZLab.actionTraces + 1] = rec
    return rec
end

local function traceEvent(action, name)
    local rec = PZLab.traceAction(action)
    local t = traceNow()
    rec.first = rec.first or t
    local maxTime = rawget(action, "maxTime")
    if name == "waitToStart" then
        rec.waits = rec.waits + 1
        return
    end
    if name == "update" then
        rec.updates = rec.updates + 1
        if rec.updates > 1 and maxTime == rec.maxTime then return end
    end
    rec.maxTime = maxTime
    if name == "start" then rec.started = t end
    if name == "perform" then rec.result, rec.ended = "performed", t end
    if name == "stop" and not rec.result then rec.result, rec.ended = "stopped", t end
    if #rec.events < MAX_TRACE_EVENTS then rec.events[#rec.events + 1] = { name = name, t = t, maxTime = maxTime } end
end

-- classNames: list of action class names (default { "ISInventoryTransferAction" }); false stops
-- tracing and restores the methods. Clears the records when starting. Returns the traced names.
function PZLab.traceActions(classNames)
    for name, saved in pairs(PZLab.actionTraceOriginals) do
        local cls = _G[name]
        if cls then
            for _, m in ipairs(TRACED_METHODS) do rawset(cls, m, saved[m] or nil) end
        end
    end
    PZLab.actionTraceOriginals = {}
    if classNames == false then return {} end
    PZLab.actionTraces = {}
    PZLab.actionTraceStart = getTimestampMs()
    local traced = {}
    for _, name in ipairs(classNames or { "ISInventoryTransferAction" }) do
        local cls = _G[name]
        if type(cls) ~= "table" then error("no timed-action class " .. tostring(name)) end
        local saved = {}
        for _, m in ipairs(TRACED_METHODS) do
            saved[m] = rawget(cls, m)
            local original = cls[m]
            if original then
                local method = m
                rawset(cls, m, function(self, ...)
                    traceEvent(self, method)
                    return original(self, ...)
                end)
            end
        end
        PZLab.actionTraceOriginals[name] = saved
        traced[#traced + 1] = name
    end
    return traced
end

-- The records so far (see traceActions); clear = true empties the list afterwards.
function PZLab.actionTrace(clear)
    local out = PZLab.actionTraces
    if clear then PZLab.actionTraces = {} end
    return out
end

-- Clear weather for screenshots, the way the admin panel's climate controls do it: admin
-- overrides for fog and precipitation set to 0 (the fog fades out over a few seconds). In
-- multiplayer they are sent to the server (transmitClientChangeAdminVars), which applies them for
-- everyone; that needs an admin account. Returns true.
function PZLab.clearWeather()
    local cm = getClimateManager()
    for _, index in ipairs({ ClimateManager.FLOAT_FOG_INTENSITY, ClimateManager.FLOAT_PRECIPITATION_INTENSITY }) do
        local option = cm:getClimateFloat(index)
        option:setEnableAdmin(true)
        option:setAdminValue(0)
    end
    if isClient() then cm:transmitClientChangeAdminVars() end
    return true
end
