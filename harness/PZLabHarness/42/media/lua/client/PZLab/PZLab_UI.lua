-- PZLab UI helpers: search the whole UI tree (top-level elements and their children),
-- absolute geometry, and inventory list panes (ISInventoryPane, the item list vanilla hosts in
-- ISInventoryPage windows and any mod may host in its own) down to the screen rectangle of
-- each visible row. Plus a mouse-button recorder to check what the game saw.

require "PZLab/PZLab_Core"
require "PZLab/PZLab_Bridge"
require "PZLab/PZLab_Tools"

local function className(obj)
    local ok, name = pcall(getClassSimpleName, obj)
    return ok and tostring(name) or "?"
end

local function tableOf(j)
    local ok, t = pcall(function() return j:getTable() end)
    if ok then return t end
    return nil
end

-- Lua element table, Java UIElement or handle id -> Java UIElement.
local function javaOf(el)
    if type(el) == "number" then el = PZLab.get(el) end
    if type(el) == "table" then
        if not el.javaObject then error("UI element is not instantiated (no javaObject)") end
        return el.javaObject
    end
    return el
end

-- Handle id or element -> the element's Lua table (or the Java element when it has none).
function PZLab.uiElement(el)
    if type(el) == "number" then el = PZLab.get(el) end
    if type(el) == "table" then return el end
    return tableOf(el) or el
end

-- True if Lua table `t` is an instance of the ISBaseObject class named typeName or of a class
-- derived from it (walks the metatable chain comparing each class's Type).
function PZLab.isA(t, typeName)
    if type(t) ~= "table" then return false end
    local cur, depth = t, 0
    while cur and depth < 64 do
        if rawget(cur, "Type") == typeName then return true end
        cur = getmetatable(cur)
        depth = depth + 1
    end
    return false
end

-- Recursive UI search. match: a type name (Lua class Type, derived classes included unless
-- opts.exact; Java class name for Java-only elements) or a predicate function(el).
-- opts: visibleOnly (default true; hidden elements and their subtrees are skipped),
-- root (element/handle to search under instead of all top-level elements), exact, limit.
-- Returns Lua element tables (Java elements for Java-only UI), back to front.
function PZLab.findUI(match, opts)
    opts = opts or {}
    local visibleOnly = opts.visibleOnly ~= false
    local out = {}
    local function test(t, j)
        if type(match) == "function" then
            local ok, res = pcall(match, t or j)
            return ok and res
        end
        if t then
            if opts.exact then return t.Type == match end
            return PZLab.isA(t, match)
        end
        return className(j) == match
    end
    local function visit(j)
        if opts.limit and #out >= opts.limit then return end
        local okVisible, visible = pcall(function() return j:isVisible() end)
        if visibleOnly and not (okVisible and visible) then return end
        local t = tableOf(j)
        if test(t, j) then out[#out + 1] = t or j end
        -- getControls exists on UIElement only (top-level AtomUI elements lack it); checked
        -- rather than pcall'd because with "Break On Error" on, a caught error still halts Lua.
        if instanceof(j, "UIElement") then
            local kids = j:getControls()
            if kids then
                for i = 0, kids:size() - 1 do visit(kids:get(i)) end
            end
        end
    end
    if opts.root then
        visit(javaOf(opts.root))
    else
        local list = UIManager.getUI()
        for i = 0, list:size() - 1 do visit(list:get(i)) end
    end
    return out
end

-- Absolute rectangle {x, y, w, h} of an element (table, Java element or handle) in UI pixels.
function PZLab.rect(el)
    local j = javaOf(el)
    return { x = j:getAbsoluteX(), y = j:getAbsoluteY(), w = j:getWidth(), h = j:getHeight() }
end

-- The part of rect(el) that is actually drawn and clickable: clipped by every ancestor's (and
-- its own) maxDrawHeight (how collapsed windows such as ISInventoryPage hide their body) and
-- by the screen. h is 0 when nothing shows or an ancestor is hidden.
function PZLab.visibleRect(el)
    local j = javaOf(el)
    local r = PZLab.rect(j)
    local left, top, right, bottom = r.x, r.y, r.x + r.w, r.y + r.h
    local cur = j
    while cur do
        if not cur:isVisible() then bottom = top end
        local maxH = cur:getMaxDrawHeight()
        if maxH and maxH >= 0 then bottom = math.min(bottom, cur:getAbsoluteY() + maxH) end
        cur = cur:getParent()
    end
    left, top = math.max(left, 0), math.max(top, 0)
    right = math.min(right, getCore():getScreenWidth())
    bottom = math.min(bottom, getCore():getScreenHeight())
    return { x = left, y = top, w = math.max(0, right - left), h = math.max(0, bottom - top) }
end

-- Top-level ancestor (the window) of an element, as a Lua table when it has one.
function PZLab.uiRoot(el)
    local j = javaOf(el)
    while j:getParent() do j = j:getParent() end
    return tableOf(j) or j
end

---------------------------------------------------------------------------------------------
-- Inventory panes (ISInventoryPane and derived classes, "details" mode)
---------------------------------------------------------------------------------------------

-- Visible list area of a pane: below its column header, left of its scrollbar.
local function listArea(pane)
    local j = pane.javaObject
    local r = PZLab.rect(j)
    local vr = PZLab.visibleRect(j)
    local width = r.w
    if pane.vscroll and pane:isVScrollBarVisible() then width = width - pane.vscroll:getWidth() end
    local top = math.max(vr.y, r.y + (pane.headerHgt or 0))
    local bottom = vr.y + vr.h
    local right = math.min(vr.x + vr.w, r.x + width)
    return { x = vr.x, y = top, w = math.max(0, right - vr.x), h = math.max(0, bottom - top) }, r
end

-- x of the item-name column centre: where vanilla sets mouseOverOption. (Left of column2 is
-- the icon / expand toggle; right of column3 shows the Grab/Drop row buttons.)
local function nameColumnX(pane, r, area)
    local x = r.x + math.floor(((pane.column2 or 0) + (pane.column3 or r.w)) / 2)
    return math.max(area.x + 1, math.min(x, area.x + area.w - 2))
end

-- One pane (element or handle) described as in PZLab.inventoryPanes().
function PZLab.paneInfo(pane)
    pane = PZLab.uiElement(pane)
    local inv = pane.inventory
    local area, r = listArea(pane)
    local root = PZLab.uiRoot(pane)
    local owner
    if type(root) == "table" then
        owner = { type = root.Type, title = type(root.title) == "string" and root.title or nil,
                  name = root.javaObject and root.javaObject:getUIName() or nil,
                  handle = PZLab.handle(root) }
        if type(root.isCollapsed) == "boolean" then owner.collapsed = root.isCollapsed end
    else
        owner = { type = className(root), handle = PZLab.handle(root) }
    end
    return {
        handle = PZLab.handle(pane), type = pane.Type, rect = r, listRect = area,
        shown = area.h >= (pane.itemHgt or 1),
        containerType = inv and inv:getType() or nil, container = inv and PZLab.handle(inv) or nil,
        items = inv and inv:getItems():size() or 0, rows = pane.items and #pane.items or 0,
        mode = pane.mode, owner = owner,
        drop = { x = nameColumnX(pane, r, area), y = math.floor(area.y + area.h / 2) },
    }
end

-- Every visible inventory pane in the UI tree: { handle, type, rect, listRect (visible list
-- area), shown (listRect fits a row; false for a collapsed window), containerType, container
-- (handle of the ItemContainer), items (container item count), rows, mode, owner = { type,
-- title, name, handle, collapsed } of its top-level window, drop = {x, y} (a point in the
-- list area to drop dragged items on) }.
function PZLab.inventoryPanes()
    local out = {}
    for _, pane in ipairs(PZLab.findUI("ISInventoryPane")) do
        if type(pane) == "table" and pane.javaObject then out[#out + 1] = PZLab.paneInfo(pane) end
    end
    return out
end

-- The rows of a pane (element or handle) currently on screen, in list order:
-- { index (pane.items index, what vanilla's mouseOverOption counts), rect (visible part of
--   the row, absolute), point = {x, y} (hover/click/drag here: name column, row centre),
--   partial (row cut off by the header or the bottom edge), name, fullType, count (items the
--   row stands for), isGroup (header of a stack of 2+ items), collapsed (stack headers),
--   child (a row inside an expanded stack), equipped, isContainerItem, selected, hovered
--   (pane.mouseOverOption == index), handle (of the InventoryItem; a stack's first item) }
-- Geometry follows ISInventoryPane:renderdetails/onMouseMove: row i spans
--   absY + yScroll + headerHgt + (i-1)*itemHgt .. + itemHgt; the dummy first entry of every
-- stack (itemslist v.items[1]) is the stack's header row.
function PZLab.paneRows(pane)
    pane = PZLab.uiElement(pane)
    if type(pane) ~= "table" or not pane.javaObject then error("paneRows: not a Lua UI element") end
    if pane.mode ~= "details" then error("paneRows: pane is in '" .. tostring(pane.mode) .. "' mode") end
    local j = pane.javaObject
    local area, r = listArea(pane)
    local x = nameColumnX(pane, r, area)
    local yScroll = j:getYScroll()
    local player = getSpecificPlayer(pane.player or 0)
    local listBottom = area.y + area.h
    local rows = {}
    local items = pane.items or {}
    for i = 1, #items do
        local top = r.y + yScroll + pane.headerHgt + (i - 1) * pane.itemHgt
        local bottom = top + pane.itemHgt
        local vTop, vBottom = math.max(top, area.y), math.min(bottom, listBottom)
        if vBottom > vTop then
            local entry = items[i]
            local row = {
                index = i,
                rect = { x = area.x, y = vTop, w = area.w, h = vBottom - vTop },
                point = { x = x, y = math.floor((vTop + vBottom) / 2) },
                partial = (vTop > top or vBottom < bottom) or nil,
                selected = pane.selected ~= nil and pane.selected[i] ~= nil,
                hovered = pane.mouseOverOption == i,
            }
            local item
            if instanceof(entry, "InventoryItem") then
                item = entry
                row.count, row.isGroup, row.child = 1, false, true
            else
                item = entry.items[1]
                row.count = #entry.items - 1
                row.isGroup = row.count > 1
                if row.isGroup then row.collapsed = pane.collapsed ~= nil and pane.collapsed[entry.name] == true end
                row.equipped = entry.equipped or nil
            end
            row.name = item:getName(player)
            row.fullType = item:getFullType()
            row.isContainerItem = instanceof(item, "InventoryContainer")
            row.handle = PZLab.handle(item)
            rows[#rows + 1] = row
        end
    end
    return rows
end

---------------------------------------------------------------------------------------------
-- Vanilla inventory and loot windows (ISInventoryPage)
---------------------------------------------------------------------------------------------

local function inventoryPages(playerNum)
    return { inventory = getPlayerInventory(playerNum), loot = getPlayerLoot(playerNum) }
end

-- The player's vanilla inventory and loot windows now: { inventory = { window, pane (handles),
-- rect (window), info = PZLab.paneInfo(pane) }, loot = { ... } }.
function PZLab.inventoryPanesOf(playerNum)
    local out = {}
    for key, page in pairs(inventoryPages(playerNum or 0)) do
        if page then
            out[key] = { window = PZLab.handle(page), pane = PZLab.handle(page.inventoryPane),
                         rect = PZLab.rect(page), info = PZLab.paneInfo(page.inventoryPane) }
        end
    end
    return out
end

-- Makes a player's vanilla inventory and loot windows usable by a test: back in the UI if
-- something removed them (vanilla never re-adds them), visible (the Toggle Inventory key only
-- hides them), expanded, pinned (unless opts.pin == false; unpinned they collapse to their
-- title bar once the mouse leaves), inside the screen and in front of other windows.
-- opts = { player = 0, pin = true, inventory = {x, y, w, h}, loot = {x, y, w, h} } (optional
-- placement). Returns PZLab.inventoryPanesOf(player); the pane geometry is final a frame later.
-- Tests that change the windows should restore them afterwards (PZLab.saveLayout).
function PZLab.showInventoryPanes(opts)
    opts = opts or {}
    local playerNum = opts.player or 0
    local sw, sh = getCore():getScreenWidth(), getCore():getScreenHeight()
    for key, page in pairs(inventoryPages(playerNum)) do
        if not UIManager.getUI():contains(page.javaObject) then page:addToUIManager() end
        page:setVisible(true)
        if opts.pin ~= false then page:setPinned() end
        page.isCollapsed = false
        page:clearMaxDrawHeight()
        page.collapseCounter = 0
        local place = opts[key]
        if place then
            if place.w then page:setWidth(place.w) end
            if place.h then page:setHeight(place.h) end
            if place.x then page:setX(place.x) end
            if place.y then page:setY(place.y) end
        end
        page:setX(math.max(0, math.min(page:getX(), sw - page:getWidth())))
        page:setY(math.max(0, math.min(page:getY(), sh - page:getHeight())))
        page:bringToTop()
    end
    return PZLab.inventoryPanesOf(playerNum)
end

-- Older name: shows and pins both windows; returns { inventory pane handle, loot pane handle }.
function PZLab.openInventoryWindows(playerNum)
    local panes = PZLab.showInventoryPanes({ player = playerNum or 0 })
    return { panes.inventory.pane, panes.loot.pane }
end

---------------------------------------------------------------------------------------------
-- UI layout snapshots: tests pin, move, resize, expand and close windows; restoreLayout puts
-- every top-level element back as it was.
---------------------------------------------------------------------------------------------

PZLab.layouts = PZLab.layouts or { n = 0 }

local function luaWindow(el)
    return type(el) == "table" and rawget(el, "javaObject") ~= nil
end

-- Snapshot of every top-level UI element (shown or hidden): { id, windows = { { handle, type,
-- title, name, x, y, w, h, visible, attached, pinned (windows with a pin), collapsed (windows
-- that collapse), maxDrawHeight (others, -1 = none) } } }, back to front. Kept in the harness
-- under its id too. Elements are referred to by handle: don't clearHandles() in between.
function PZLab.saveLayout()
    local list = UIManager.getUI()
    local windows = {}
    for i = 0, list:size() - 1 do
        local j = list:get(i)
        local t = tableOf(j)
        local el = luaWindow(t) and t or j
        local w = { handle = PZLab.handle(el), attached = true, visible = j:isVisible() == true }
        if luaWindow(t) then
            w.type = t.Type
            if type(t.title) == "string" then w.title = t.title end
            if type(t.pin) == "boolean" then w.pinned = t.pin end
            if type(t.isCollapsed) == "boolean" then w.collapsed = t.isCollapsed end
        else
            w.type = className(j)
        end
        local name = tostring(j:getUIName() or "")
        if name ~= "" and name ~= w.type then w.name = name end
        if instanceof(j, "UIElement") then
            w.x, w.y, w.w, w.h = j:getX(), j:getY(), j:getWidth(), j:getHeight()
            if w.collapsed == nil then w.maxDrawHeight = j:getMaxDrawHeight() end
        end
        windows[#windows + 1] = w
    end
    local L = PZLab.layouts
    L.n = L.n + 1
    local snapshot = { id = L.n, windows = windows }
    L[L.n] = snapshot
    return snapshot
end

local function same(a, b)
    return a == b or (type(a) == "number" and type(b) == "number" and math.abs(a - b) < 0.5)
end

-- Puts one window back; returns the list of fields it changed.
local function restoreWindow(w, attached)
    local el = PZLab.get(w.handle)
    if el == nil then return nil end
    local t = luaWindow(el) and el or nil
    local j = t and t.javaObject or el
    local fields = {}
    if w.attached and not attached[w.handle] and t then
        t:addToUIManager()
        fields[#fields + 1] = "attached"
    end
    if instanceof(j, "UIElement") and w.x ~= nil then
        local target = t or j
        if not same(j:getWidth(), w.w) then target:setWidth(w.w) fields[#fields + 1] = "w" end
        if not same(j:getHeight(), w.h) then target:setHeight(w.h) fields[#fields + 1] = "h" end
        if not same(j:getX(), w.x) then target:setX(w.x) fields[#fields + 1] = "x" end
        if not same(j:getY(), w.y) then target:setY(w.y) fields[#fields + 1] = "y" end
    end
    if t and w.pinned ~= nil and t.pin ~= w.pinned then
        -- vanilla's pin / unpin button handlers (they also swap the two title-bar buttons)
        local page = PZLab.isA(t, "ISInventoryPage")
        if not (t.pinButton and t.collapseButton) then
            t.pin = w.pinned
        elseif w.pinned then
            if page then ISInventoryPage.setPinned(t) else ISCollapsableWindow.pin(t) end
        else
            if page then ISInventoryPage.collapse(t) else ISCollapsableWindow.collapse(t) end
        end
        t.pin = w.pinned -- ISInventoryPage.collapse returns early during an item drag
        fields[#fields + 1] = "pinned"
    end
    if t and w.collapsed ~= nil and t.isCollapsed ~= w.collapsed then
        if w.collapsed then
            t.isCollapsed = true
            t:setMaxDrawHeight(t:titleBarHeight())
        else
            t.isCollapsed = false
            t:clearMaxDrawHeight()
            t.collapseCounter = 0
        end
        fields[#fields + 1] = "collapsed"
    elseif w.maxDrawHeight ~= nil and instanceof(j, "UIElement") and not same(j:getMaxDrawHeight(), w.maxDrawHeight) then
        if w.maxDrawHeight == -1 then j:clearMaxDrawHeight() else j:setMaxDrawHeight(w.maxDrawHeight) end
        fields[#fields + 1] = "maxDrawHeight"
    end
    if (j:isVisible() == true) ~= w.visible then
        if t then t:setVisible(w.visible) else j:setVisible(w.visible) end
        fields[#fields + 1] = "visible"
    end
    return fields
end

-- Restores a snapshot (the table saveLayout returned, or its id): position, size, attached to
-- the UI, pinned, collapsed and visible state of every element it lists. opts.hideAdded also
-- hides top-level elements that appeared since. Returns { restored (count), changed = { { type,
-- title, fields } }, added = { {type, title, handle} } (still there unless hideAdded), missing
-- (handles that no longer resolve) }.
function PZLab.restoreLayout(snapshot, opts)
    opts = opts or {}
    if type(snapshot) == "number" then snapshot = PZLab.layouts[snapshot] end
    if type(snapshot) ~= "table" or type(snapshot.windows) ~= "table" then
        error("restoreLayout: give a snapshot from PZLab.saveLayout() or its id")
    end
    local attached, listed = {}, {}
    local list = UIManager.getUI()
    for i = 0, list:size() - 1 do
        local j = list:get(i)
        local t = tableOf(j)
        attached[PZLab.handle(luaWindow(t) and t or j)] = true
    end
    local result = { restored = 0, changed = {}, added = {}, missing = {} }
    for _, w in ipairs(snapshot.windows) do
        listed[w.handle] = true
        local fields = restoreWindow(w, attached)
        if fields == nil then
            result.missing[#result.missing + 1] = w.handle
        else
            result.restored = result.restored + 1
            if #fields > 0 then
                result.changed[#result.changed + 1] = { type = w.type, title = w.title, fields = fields }
            end
        end
    end
    for i = 0, list:size() - 1 do
        local j = list:get(i)
        local t = tableOf(j)
        local el = luaWindow(t) and t or j
        local h = PZLab.handle(el)
        if not listed[h] and j:isVisible() then
            result.added[#result.added + 1] = { type = luaWindow(t) and t.Type or className(j), handle = h,
                                                title = luaWindow(t) and type(t.title) == "string" and t.title or nil }
            if opts.hideAdded then
                if luaWindow(t) then t:setVisible(false) else j:setVisible(false) end
            end
        end
    end
    return result
end

---------------------------------------------------------------------------------------------
-- Input recorder: samples mouse buttons every tick and logs each press/release with the
-- modifier keys the game sees (isAltKeyDown / isShiftKeyDown / isCtrlKeyDown) at that moment.
---------------------------------------------------------------------------------------------

PZLab.inputLog = PZLab.inputLog or { on = false, events = {}, down = {} }

PZLab.tickHooks.inputLog = function()
    local L = PZLab.inputLog
    if not L.on then return end
    for button = 0, 2 do
        local down = isMouseButtonDown(button) and true or false
        if down ~= (L.down[button] or false) then
            L.down[button] = down
            if #L.events >= 200 then table.remove(L.events, 1) end
            L.events[#L.events + 1] = {
                kind = down and "down" or "up", button = button, x = getMouseX(), y = getMouseY(),
                alt = isAltKeyDown(), shift = isShiftKeyDown(), ctrl = isCtrlKeyDown(),
                tick = PZLab.Bridge.ticks,
            }
        end
    end
end

-- Starts (true, clearing the log) or stops (false) recording. Returns the recording state.
function PZLab.recordInput(on)
    local L = PZLab.inputLog
    L.on = on ~= false
    if L.on then L.events, L.down = {}, {} end
    return L.on
end

-- Recorded events { kind = "down"|"up", button (0 left, 1 right, 2 middle), x, y, alt,
-- shift, ctrl, tick }, oldest first (last 200).
function PZLab.inputEvents()
    return PZLab.inputLog.events
end
