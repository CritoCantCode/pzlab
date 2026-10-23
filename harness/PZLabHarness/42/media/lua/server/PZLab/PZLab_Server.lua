-- PZLab on a dedicated server (the lab server, see AGENT.md "Multiplayer"). The client loads
-- server/ files too, so everything here returns early unless isServer().
--
-- On the server the shared harness runs as on a client (PZLab_Core, PZLab_Bridge with its own
-- <server cachedir>/Lua/pzlab/ bridge directory, PZLab_Inspect, PZLab_Fixtures): commands from
-- `pzlab.py server lua` / lab.server.lua(...) run in the server's Lua state, i.e. on the
-- authoritative world. There is no UI, no local player (PZLab.player() picks a connected one) and
-- only the chunks around connected players are loaded.

if not isServer() then return end

require "PZLab/PZLab_Core"
require "PZLab/PZLab_Bridge"
require "PZLab/PZLab_Inspect"
require "PZLab/PZLab_Fixtures"

-- Makes PZLab.player() (and the fixture helpers that default to it) use this connected player.
function PZLab.usePlayer(username)
    PZLab.serverPlayerName = username
    return PZLab.player() ~= nil
end

-- The server's view of itself: { name, players, map, gameTime = {hours, ...}, sandbox = {Zombies} }.
function PZLab.serverInfo()
    local gt = getGameTime()
    local zombies = getSandboxOptions():getOptionByName("Zombies")
    return {
        name = getServerName(),
        players = PZLab.players(),
        tickEvent = PZLab.Bridge and PZLab.Bridge.tickEvent,
        worldAgeHours = gt and gt:getWorldAgeHours(),
        sandbox = { Zombies = zombies and zombies:getValue() },
    }
end

PZLab.log("server helpers ready")
