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

function PZLab.teleport(x, y, z)
    local player = PZLab.player()
    player:teleportTo(x + 0.5, y + 0.5, z or 0)
    return { x = player:getX(), y = player:getY(), z = player:getZ() }
end

---------------------------------------------------------------------------------------------
-- Projection (world -> UI pixels, the coordinate space of getMouseX/Y and UI elements)
---------------------------------------------------------------------------------------------

function PZLab.toScreen(x, y, z)
    return { x = isoToScreenX(P, x, y, z or 0), y = isoToScreenY(P, x, y, z or 0) }
end

-- Centre of a tile object's visible sprite, accounting for zoom and stacking offset;
-- falls back to the floor centre of its square.
function PZLab.objectScreenPos(obj)
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
-- objects), so callers try them in order (labkit Lab.hover does). Texture:isMaskSet takes
-- full-tile coordinates (verified in 42.21). Returns {} when the sprite has no mask.
function PZLab.aimPoints(obj, maxPoints, spacing)
    local sq = obj:getSquare()
    local sprite = obj:getSprite()
    local tex = sprite and sprite:getTextureForCurrentFrame(obj:getDir())
    if not (sq and tex and tex:getMask()) then return {} end
    maxPoints, spacing = maxPoints or 12, spacing or 10
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
    local chosen, out = {}, {}
    for _, p in ipairs(pts) do
        local farEnough = true
        for _, q in ipairs(chosen) do
            if math.abs(p[1] - q[1]) < spacing and math.abs(p[2] - q[2]) < spacing then
                farEnough = false
                break
            end
        end
        if farEnough then
            chosen[#chosen + 1] = p
            out[#out + 1] = { x = bx + (p[1] - offsetX) / zoom, y = by + (p[2] - offsetY) / zoom }
            if #out >= maxPoints then break end
        end
    end
    return out
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

function PZLab.describeObject(obj)
    if not obj then return nil end
    local d = { class = className(obj), str = tostring(obj) }
    local sq = obj:getSquare()
    if sq then d.at = { sq:getX(), sq:getY(), sq:getZ() } end
    d.sprite = obj:getSpriteName()
    d.name = obj:getObjectName()
    local count = obj:getContainerCount()
    if count > 0 then
        d.containers = {}
        for i = 0, count - 1 do
            d.containers[#d.containers + 1] = PZLab.describeContainer(obj:getContainerByIndex(i))
        end
    end
    if instanceof(obj, "IsoWorldInventoryObject") then
        local item = obj:getItem()
        d.item = item and item:getFullType()
    end
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
