-- PZLab world autostart. At the main menu, reads pzlab/autostart.txt (a Lua chunk returning a
-- config table, written by the controller) and launches a fresh world through vanilla's debug
-- scenario path (DebugScenarios:launchScenario; IsoWorld spawns the player at
-- selectedDebugScenario.startLoc). The file is emptied once read so it fires only once.
--
-- Config: { map = "Muldraugh, KY", x = ..., y = ..., z = 0, sandbox = { ...SandboxVars overrides },
--           god = true, onStart = "<lua source run after spawn>",
--           closeUI = { "<UI Type>", ... } closed once the in-game UI has been built }

require "PZLab/PZLab_Core"
require "PZLab/PZLab_Bridge"

local MENU_SETTLE_TICKS = 30

local function merge(dst, src)
    for k, v in pairs(src) do
        if type(v) == "table" and type(dst[k]) == "table" then
            merge(dst[k], v)
        else
            dst[k] = v
        end
    end
end

function PZLab.onWorldStart(cfg)
    local player = getSpecificPlayer(0)
    if cfg.god ~= false then
        player:setGodMod(true)
        player:setInvisible(true)
        player:setZombiesDontAttack(true)
        player:setUnlimitedEndurance(true)
    end
    if cfg.onStart then
        local fn, err = loadstring(cfg.onStart)
        if fn then
            local ok, runErr = pcall(fn)
            if not ok then PZLab.log("autostart onStart failed: " .. tostring(runErr)) end
        else
            PZLab.log("autostart onStart compile error: " .. tostring(err))
        end
    end
    PZLab.pendingCloseUI = cfg.closeUI
    PZLab.log("world started at " .. player:getX() .. "," .. player:getY() .. "," .. player:getZ())
end

-- The in-game UI is built over the first frames after spawn; close requested panels after it.
local closeDelay = 0
PZLab.tickHooks.closeUI = function()
    if not PZLab.pendingCloseUI or not getSpecificPlayer(0) then return end
    closeDelay = closeDelay + 1
    if closeDelay < 30 then return end
    for _, typeName in ipairs(PZLab.pendingCloseUI) do
        PZLab.closeUI(typeName)
    end
    PZLab.pendingCloseUI = nil
end

-- Launches a new world. Callable from a bridge command while at the main menu.
function PZLab.launchWorld(cfg)
    if not (DebugScenarios and DebugScenarios.instance) then
        error("main menu not ready (DebugScenarios.instance missing; is -debug on?)")
    end
    local scenario = {
        name = "PZLab",
        world = cfg.map or "Muldraugh, KY",
        startLoc = { x = cfg.x, y = cfg.y, z = cfg.z or 0 },
        setSandbox = function()
            if cfg.sandbox then merge(SandboxVars, cfg.sandbox) end
        end,
        onStart = function()
            PZLab.onWorldStart(cfg)
        end,
    }
    debugScenarios = debugScenarios or {}
    debugScenarios.PZLab = scenario
    PZLab.log("launching world '" .. scenario.world .. "' at " .. tostring(cfg.x) .. "," .. tostring(cfg.y) .. "," .. tostring(cfg.z or 0))
    DebugScenarios.instance:launchScenario(scenario)
end

local settleTicks = 0

PZLab.tickHooks.autostart = function()
    if PZLab.autostartChecked or getSpecificPlayer(0) then return end
    if not (DebugScenarios and DebugScenarios.instance and MainScreen and MainScreen.instance) then return end
    settleTicks = settleTicks + 1
    if settleTicks < MENU_SETTLE_TICKS then return end
    PZLab.autostartChecked = true

    local src = PZLab.readFile("autostart.txt")
    if not src or src == "" then
        PZLab.log("main menu ready; no autostart requested")
        return
    end
    PZLab.writeFile("autostart.txt", "")
    local fn, err = loadstring(src)
    if not fn then
        PZLab.log("autostart compile error: " .. tostring(err))
        return
    end
    local ok, cfg = pcall(fn)
    if not ok or type(cfg) ~= "table" then
        PZLab.log("autostart config failed: " .. tostring(cfg))
        return
    end
    local launched, launchErr = pcall(PZLab.launchWorld, cfg)
    if not launched then PZLab.log("autostart launch failed: " .. tostring(launchErr)) end
end
