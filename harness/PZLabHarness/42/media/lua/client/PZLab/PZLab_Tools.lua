-- PZLab helpers for command chunks: player/world access, projection to screen pixels,
-- object and UI inspection, item spawning. Everything returns plain tables so results
-- serialize cleanly through PZLab.json.

require "PZLab/PZLab_Core"

local P = 0

local function className(obj)
    local ok, name = pcall(getClassSimpleName, obj)
    return ok and tostring(name) or "?"
end

function PZLab.player()
    return getSpecificPlayer(P)
end

function PZLab.square(x, y, z)
    return getCell():getGridSquare(x, y, z or 0)
end

-- Whole-number x/y land on the tile centre; fractional ones are used as given.
-- (Kahlua resolves teleportTo(x, y, z) to the int overload, which truncates to the tile
-- corner, so the exact position is set afterwards.)
function PZLab.teleport(x, y, z)
    local player = PZLab.player()
    local fx = (x == math.floor(x)) and x + 0.5 or x
    local fy = (y == math.floor(y)) and y + 0.5 or y
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

-- The visible UI element that would take the mouse at (x, y) instead of the world, or false
-- (PZLab.uiAt, defined in PZLab_UIState, which loads after this file).
local function uiOver(x, y)
    return PZLab.uiAt ~= nil and PZLab.uiAt(x, y) or false
end

-- Screen points where moving the mouse should make the engine pick `obj`, best first; callers
-- try them in order and confirm with PZLab.isHovered (labkit Lab.hover does).
--   tile objects: opaque pixels of the sprite (empty when it has no mask)
--   corpses / vehicles: points the corpse / vehicle picker resolves to this object
--   floor items: the item's floor position first (B42 draws them as 3D models, which the
--                object picker never returns; see PZLab.isHovered), then sprite pixels
-- Only points on the screen. Up to maxPoints points clear of UI come first; then up to
-- maxPoints points covered by visible UI (PZLab.uiAt), each with ui = <that element's type>: a
-- real cursor there is over the window, and the engine doesn't update its world pick while it is.
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
        local ui = uiOver(p.x, p.y)
        if ui then
            p.ui = ui.type
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
-- False when visible UI covers the point (PZLab.uiAt), unless allowUI: the cursor would be over
-- that window, and the engine doesn't update getLastPicked while the mouse is over UI.
function PZLab.isHovered(obj, x, y, allowUI)
    if type(obj) == "number" then obj = PZLab.get(obj) end
    if obj == nil then return false end
    x, y = math.floor(x or getMouseX()), math.floor(y or getMouseY())
    if not allowUI and uiOver(x, y) then return false end
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

function PZLab.describeContainer(c)
    if not c then return nil end
    local d = { type = c:getType(), items = c:getItems():size() }
    local holder = c:getContainingItem()
    if holder then d.inItem = holder:getFullType() end
    return d
end

-- Item containers an object holds: tile object containers, a corpse's inventory, a vehicle's
-- part containers, or a floor item's own inventory (bags).
function PZLab.containersOf(obj)
    local out = {}
    if instanceof(obj, "BaseVehicle") then
        for i = 0, obj:getPartCount() - 1 do
            local c = obj:getPartByIndex(i):getItemContainer()
            if c then out[#out + 1] = c end
        end
    elseif instanceof(obj, "IsoWorldInventoryObject") then
        local item = obj:getItem()
        if item and instanceof(item, "InventoryContainer") then out[1] = item:getInventory() end
    else
        for i = 0, obj:getContainerCount() - 1 do out[#out + 1] = obj:getContainerByIndex(i) end
        if #out == 0 and instanceof(obj, "IsoDeadBody") and obj:getContainer() then out[1] = obj:getContainer() end
    end
    return out
end

function PZLab.describeObject(obj)
    if not obj then return nil end
    local d = { class = className(obj), str = tostring(obj) }
    local sq = obj:getSquare()
    if sq then d.at = { sq:getX(), sq:getY(), sq:getZ() } end
    d.sprite = obj:getSpriteName()
    d.name = obj:getObjectName()
    local containers = PZLab.containersOf(obj)
    if #containers > 0 then
        d.containers = {}
        for i, c in ipairs(containers) do d.containers[i] = PZLab.describeContainer(c) end
    end
    if instanceof(obj, "IsoWorldInventoryObject") then
        local item = obj:getItem()
        d.item = item and item:getFullType()
    elseif instanceof(obj, "IsoDeadBody") then
        d.female = obj:isFemale()
    elseif instanceof(obj, "BaseVehicle") then
        d.script = obj:getScriptName()
    end
    if instanceof(obj, "IsoMovingObject") then d.pos = { obj:getX(), obj:getY(), obj:getZ() } end
    d.screen = PZLab.objectScreenPos(obj)
    return d
end

-- Everything on a square: tile objects, world items, corpses.
function PZLab.objectsAt(x, y, z)
    local sq = PZLab.square(x, y, z)
    if not sq then return nil end
    local out = {}
    local objects = sq:getObjects()
    for i = 0, objects:size() - 1 do
        out[#out + 1] = PZLab.describeObject(objects:get(i))
    end
    local statics = sq:getStaticMovingObjects()
    for i = 0, statics:size() - 1 do
        out[#out + 1] = PZLab.describeObject(statics:get(i))
    end
    return out
end

-- Tile objects with containers within `radius` tiles of the player (same floor), nearest first.
function PZLab.findContainers(radius)
    local player = PZLab.player()
    local px, py, pz = math.floor(player:getX()), math.floor(player:getY()), math.floor(player:getZ())
    radius = radius or 6
    local out = {}
    for dy = -radius, radius do
        for dx = -radius, radius do
            local sq = PZLab.square(px + dx, py + dy, pz)
            if sq then
                local objects = sq:getObjects()
                for i = 0, objects:size() - 1 do
                    local obj = objects:get(i)
                    if obj:getContainerCount() > 0 then
                        local d = PZLab.describeObject(obj)
                        d.dist = math.sqrt(dx * dx + dy * dy)
                        out[#out + 1] = d
                    end
                end
            end
        end
    end
    table.sort(out, function(a, b) return a.dist < b.dist end)
    return out
end

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

function PZLab.addToContainer(container, fullType, count)
    for _ = 1, count or 1 do container:AddItem(fullType) end
    return container:getItems():size()
end

function PZLab.screenshot(name)
    takeScreenshot(name or "pzlab")
    return true
end

function PZLab.reload(path)
    return reloadLuaFile(path)
end
