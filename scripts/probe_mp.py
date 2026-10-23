"""Multiplayer probe: the lab server and one lab client.

Checks (PASS/FAIL; the exit status is 1 when one fails): goto (lab.goto: server teleport, both
sides agree) to the tile the player stands on, twice; to the TruckBed area of a vehicle spawned
on the server on open ground nearby (target computed on the client); spawnVehicle refusing a spot
vanilla won't place a vehicle on (the world's start tile, when it is indoors); goto refusing a
target off the map; then an inventory-only transfer (an item into a bag in the inventory).

Then findings: a world container (a crate) with a bag inside it next to the player, made on the
server; the client moves items with vanilla ISInventoryTransferAction, one action at a time:
  bag -> inventory          an item out of the bag (in the crate) into the player's inventory
  inventory -> bag          an item from the inventory into that bag
  container -> inventory    control: an item straight out of the crate
  inventory -> container    control: an item straight into the crate
  floor bag -> inventory    control: an item out of a bag lying on the floor
For each it reports where the server says the item ended up (and where the client thinks it is),
when the server-side location changed, and how the client's action finished (PZLab.traceActions:
result, time to the server's duration reply, total time).

Run: python pzlab.py run scripts/probe_mp.py [--mod-dir PATH]... [--keep] [--json]
Starts the lab server (fresh world) and the lab client and joins it, unless they are running
already (a client in game must be on the lab server). Everything it started is stopped at the
end unless --keep; fixtures are removed either way."""

import argparse
import json
import sys
import time

parser = argparse.ArgumentParser(prog="probe_mp")
parser.add_argument("--mod-dir", action="append", default=[])
parser.add_argument("--keep", action="store_true", help="leave the server and client running")
parser.add_argument("--json", action="store_true", help="print the full report as JSON at the end")
args = parser.parse_args(sys.argv[1:])

CASES = [  # name, item key, source, destination
    ("bag -> inventory", "inBag", "bag", "inventory"),
    ("inventory -> bag", "forBag", "inventory", "bag"),
    ("container -> inventory", "inCrate", "crate", "inventory"),
    ("inventory -> container", "forCrate", "inventory", "crate"),
    ("floor bag -> inventory", "inFloorBag", "floorBag", "inventory"),
]
ACTION_TIMEOUT_S = 40  # an MP client gives a transaction up after its duration + 10 s

started_server = started_client = False
report = {"cases": [], "checks": []}


def check(name, ok, detail=""):
    report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", name, detail))


def near(a, b, tolerance=0.3):
    return (a and b and abs(a["x"] - b["x"]) <= tolerance and abs(a["y"] - b["y"]) <= tolerance
            and int(a["z"]) == int(b["z"]))


def goto_checks(user):
    """goto to the same tile, beside a vehicle, refusals, then an inventory transfer."""
    here = lab.lua("local p = getSpecificPlayer(0) return { math.floor(p:getX()), math.floor(p:getY()), "
                   "math.floor(p:getZ()) }")
    for attempt in ("goto the tile the player stands on", "goto the same tile again (nothing to send)"):
        try:
            r = lab.goto(tuple(here))
            centre = {"x": here[0] + 0.5, "y": here[1] + 0.5, "z": here[2]}
            ok = near(r["player"], centre, 0.05) and near(r.get("server"), centre, 0.05)
            if attempt.endswith("(nothing to send)"):
                ok = ok and r.get("sent") is False
            check(attempt, ok, "client %s, server %s, sent %s, %d ms" % (r["player"], r.get("server"), r.get("sent"), r["ms"]))
        except Exception as e:
            check(attempt, False, str(e))

    # open ground nearby, a vehicle beside the player (server), goto its TruckBed area (client)
    area = lab.lua("return PZLab.findOpenArea(10, 10, { radius = 40 })")
    if not area:
        check("open ground within 40 tiles for the vehicle check", False, "none found")
        return
    lab.goto((area["center"][0] if isinstance(area["center"], list) else area["center"]["x"],
              area["center"][1] if isinstance(area["center"], list) else area["center"]["y"], 0))
    vehicle = lab.server.lua("""local p = PZLab.player()
        local v = PZLab.spawnVehicle("Base.CarNormal", math.floor(p:getX()) + 3, math.floor(p:getY()) + 3, 0, "E",
                                     { open = true })
        return { x = v:getX(), y = v:getY(), id = v:getId() }""")
    target = lab.wait_until("""for _, o in ipairs(PZLab.findObjects({ radius = 8, class = "BaseVehicle" })) do
            local v = PZLab.get(o.handle)
            if PZLab.vehiclePlaced(v) and math.abs(v:getX() - %r) < 1 and math.abs(v:getY() - %r) < 1 then
                local c = v:getAreaCenter("TruckBed")
                return { math.floor(c:getX()), math.floor(c:getY()), 0 }
            end
        end""" % (vehicle["x"], vehicle["y"]), timeout_s=15)
    try:
        r = lab.goto(tuple(target))
        centre = {"x": target[0] + 0.5, "y": target[1] + 0.5, "z": 0}
        check("goto beside a server-spawned vehicle (its TruckBed area)", near(r["player"], centre) and
              near(r.get("server"), r["player"]), "vehicle %s at %.1f,%.1f; target %s; client %s, server %s, %d ms"
              % (vehicle["id"], vehicle["x"], vehicle["y"], target, r["player"], r.get("server"), r["ms"]))
    except Exception as e:
        check("goto beside a server-spawned vehicle (its TruckBed area)", False, str(e))

    # a vehicle where vanilla won't place one: the world's start tile, if it is indoors
    start = lab.cfg["world"]
    indoors = lab.server.lua("local sq = PZLab.square(%d, %d, %d) return sq ~= nil and not sq:isOutside()"
                             % (start["x"], start["y"], start.get("z", 0)))
    if indoors:
        res = lab.server.bridge.exec("return PZLab.spawnVehicle('Base.CarNormal', %d, %d, %d)"
                                     % (start["x"], start["y"], start.get("z", 0)))
        check("spawnVehicle refuses an indoor spot", not res.get("ok") and "refused" in str(res.get("error")),
              res.get("error") or "it spawned one")
    else:
        print("skip  spawnVehicle refusal: the world's start tile is outdoors")

    # a target off the map
    if not lab.lua("return PZLab.onMap(-3, -1)"):
        before = lab.lua("local p = getSpecificPlayer(0) return { x = p:getX(), y = p:getY(), z = p:getZ() }")
        try:
            lab.goto((-3, -1, 0))
            check("goto refuses a target off the map", False, "it went there")
        except Exception as e:
            after = lab.lua("local p = getSpecificPlayer(0) return { x = p:getX(), y = p:getY(), z = p:getZ() }")
            check("goto refuses a target off the map", "not on the map" in str(e) and near(before, after, 0.01), str(e))

    # an inventory-only transfer after all that
    items = lab.server.lua("""local made = PZLab.addItems(PZLab.player(), { "Base.Bag_Schoolbag", "Base.Pencil" })
        return { bag = made[1]:getID(), pencil = made[2]:getID() }""")
    lab.wait_until("PZLab.findItem(%d) ~= nil and PZLab.findItem(%d) ~= nil" % (items["bag"], items["pencil"]))
    lab.lua("PZLab.traceActions({ 'ISInventoryTransferAction' }) return true")
    n = lab.lua("""local p = getSpecificPlayer(0)
        local a = ISInventoryTransferAction:new(p, PZLab.findItem(%d), p:getInventory(), PZLab.findItem(%d):getInventory())
        local rec = PZLab.traceAction(a, true) ISTimedActionQueue.add(a) return rec.n""" % (items["pencil"], items["bag"]))
    t0 = time.time()
    while time.time() - t0 < ACTION_TIMEOUT_S and lab.lua("return PZLab.playerBusy()"):
        time.sleep(0.1)
    rec = next((r for r in lab.lua("return PZLab.actionTrace()") if r["n"] == n), {})
    place = where("server", items["pencil"], user)
    check("inventory transfer afterwards (pencil into a bag in the inventory)",
          rec.get("result") == "performed" and place.startswith("item:Base.Bag_Schoolbag < character:"),
          "%s after %.1f s; server: %s" % (rec.get("result"), time.time() - t0, place))
    lab.lua("ISTimedActionQueue.clear(getSpecificPlayer(0)) PZLab.traceActions(false) return true")
    lab.server.clear_fixtures()  # while still near the vehicle
    lab.goto((start["x"], start["y"], start.get("z", 0)))


def where(side, item_id, user):
    """'item:Base.Bag_Schoolbag < object:carpentry_01_19' (innermost holder first) or 'nowhere'."""
    run = lab.server.lua if side == "server" else lab.lua
    return run("return PZLab.placeText(PZLab.locate(%d, { player = %s, radius = 3 }))" % (item_id, json.dumps(user)))


try:
    # 1. server and client --------------------------------------------------------------------
    if not lab.server.pid():
        info = lab.server.start(mod_dirs=args.mod_dir)
        started_server = True
        print("server started: pid %s, %s:%s, mods %s, %.0f s" % (info["pid"], info["address"]["ip"],
              info["address"]["port"], info["active_mods"], info["ready_s"]))
    state = lab.status()
    if not state["running"]:
        result = lab.start(mod_dirs=args.mod_dir, join=True)
        started_client = True
        print("client joined as %s in %.1f s (character created: %s)" % (
            result["join"]["user"], result["join"]["ms"] / 1000, result["join"]["created"]))
    elif (state["harness"] or {}).get("state") == "menu":
        joined = lab.join()
        print("client joined as %s in %.1f s" % (joined["user"], joined["ms"] / 1000))
    elif not lab.lua("return isClient()"):
        raise RuntimeError("the lab client is in a single-player game; stop it first")
    user = lab.lua("return getSpecificPlayer(0):getUsername()")
    lab.ensure_playable()
    lab.server.lua("PZLab.usePlayer(%s) return true" % json.dumps(user))
    client_log, server_log = lab.log.size(), lab.server.log.size()

    # 2. checks: goto, vehicles, transfer --------------------------------------------------------
    goto_checks(user)
    print()

    # 3. findings: fixtures on the server: a crate next to the player with a bag inside -------
    ids = lab.server.lua("""
        local p = PZLab.player()
        local px, py, pz = math.floor(p:getX()), math.floor(p:getY()), math.floor(p:getZ())
        local free = {}
        for _, d in ipairs({ {1,0}, {0,1}, {-1,0}, {0,-1}, {1,1}, {-1,1}, {1,-1}, {-1,-1} }) do
            local sq = PZLab.square(px + d[1], py + d[2], pz)
            if sq and sq:isFree(false) then free[#free + 1] = sq end
        end
        if #free < 2 then error("need two free squares next to the player") end
        local spot = free[1]
        local crate = PZLab.spawnContainer(nil, spot:getX(), spot:getY(), pz, { contents = {
            { "Base.Bag_Schoolbag", 1, contents = { "Base.Apple" } }, "Base.Apple" } })
        local items = crate:getContainer():getItems()
        local bag, inCrate
        for i = 0, items:size() - 1 do
            local it = items:get(i)
            if it:getFullType() == "Base.Bag_Schoolbag" then bag = it else inCrate = it end
        end
        local mine = PZLab.addItems(p, { "Base.Notebook", "Base.Pencil" })
        local floorBag = PZLab.spawnWorldItem("Base.Bag_Schoolbag", free[2]:getX(), free[2]:getY(), pz,
                                              { contents = { "Base.Apple" } }):getItem()
        return { crateAt = { spot:getX(), spot:getY(), pz }, player = { px, py, pz },
                 floorBagAt = { free[2]:getX(), free[2]:getY(), pz }, floorBag = floorBag:getID(),
                 inFloorBag = floorBag:getInventory():getItems():get(0):getID(),
                 bag = bag:getID(), inBag = bag:getInventory():getItems():get(0):getID(),
                 inCrate = inCrate:getID(), forBag = mine[1]:getID(), forCrate = mine[2]:getID() }
    """)
    report["fixtures"] = ids
    print("server: crate at %s (player at %s), bag %d" % (ids["crateAt"], ids["player"], ids["bag"]))

    # the client sees them (same item ids)
    keys = ("bag", "inBag", "inCrate", "forBag", "forCrate", "floorBag", "inFloorBag")
    seen = lab.wait_until("return (%s) or nil" % " and ".join(
        "PZLab.findItem(%d, { radius = 3 }) ~= nil" % ids[k] for k in keys), timeout_s=15)
    print("client sees all %d items" % len(keys))

    # one transfer at a time, traced on the client; the server watches where the items are
    # every tick (both stamp getTimestampMs, one clock on one machine)
    lab.server.lua("PZLab.watchItems(%s, { player = %s, radius = 3 }) return true"
                   % ("{ %s }" % ", ".join(str(ids[k]) for k in keys), json.dumps(user)))
    lab.lua("PZLab.traceActions({ 'ISInventoryTransferAction' }) return true")
    trace_start = lab.lua("return PZLab.actionTraceStart")
    containers = {
        "inventory": "getSpecificPlayer(0):getInventory()",
        "bag": "PZLab.findItem(%d, { radius = 3 }):getInventory()" % ids["bag"],
        "crate": "PZLab.findItem(%d, { radius = 3 }):getContainer()" % ids["bag"],  # the bag stays in it
        "floorBag": "PZLab.findItem(%d, { radius = 3 }):getInventory()" % ids["floorBag"],
    }
    for name, key, src, dst in CASES:
        item_id = ids[key]
        before = where("server", item_id, user)
        n = lab.lua("""
            local p = getSpecificPlayer(0)
            local item = PZLab.findItem(%d, { radius = 3 })
            local action = ISInventoryTransferAction:new(p, item, %s, %s)
            local rec = PZLab.traceAction(action, true)
            ISTimedActionQueue.add(action)
            return rec.n
        """ % (item_id, containers[src], containers[dst]))
        t0 = time.time()
        while time.time() - t0 < ACTION_TIMEOUT_S and lab.lua("return PZLab.playerBusy()"):
            time.sleep(0.1)
        time.sleep(0.5)  # a late server-side change still gets recorded
        busy = lab.lua("return PZLab.playerBusy()")
        rec = next((r for r in lab.lua("return PZLab.actionTrace()") if r["n"] == n), {})
        start = rec.get("started")
        # the server's duration reply: the first update with maxTime > 0 (an MP client starts at -1)
        replied = next((e["t"] for e in rec.get("events", [])
                        if e["name"] == "update" and (e.get("maxTime") or -1) > 0), None)
        duration = next((e["maxTime"] for e in rec.get("events", [])
                         if e["name"] == "update" and (e.get("maxTime") or -1) > 0), None)
        changes = (lab.server.lua("return PZLab.itemWatch()") or {}).get(str(item_id), {}).get("changes") or []
        server_changed = (changes[0]["t"] - (trace_start + start)) if changes and start is not None else None
        case = {
            "case": name, "item": "%s %d" % (lab.lua("return PZLab.findItem(%d, { radius = 3 }):getFullType()"
                                                      % item_id), item_id),
            "before": before, "server": where("server", item_id, user), "client": where("client", item_id, user),
            "serverChangedMs": server_changed,
            "action": {"result": rec.get("result") or ("still running" if busy else "never ran"),
                       "durationReplyMs": (replied - start) if replied is not None and start is not None else None,
                       "duration": duration,
                       "totalMs": (rec["ended"] - start) if rec.get("ended") is not None and start is not None else None,
                       "events": [(e["name"], e["t"] - (start or 0), e.get("maxTime")) for e in rec.get("events", [])]},
        }
        moved = case["server"] != before
        case["verdict"] = ("moved" if moved else
                           "rejected by the server" if case["action"]["result"] == "stopped" else
                           "NOT moved, client reported success" if case["action"]["result"] == "performed" else
                           "NOT moved, action " + case["action"]["result"])
        report["cases"].append(case)
        a = case["action"]
        print("\n%-24s %s" % (name, case["item"]))
        print("  server: %s  ->  %s%s" % (before, case["server"],
              "  (changed %d ms after the action started)" % server_changed if server_changed is not None else ""))
        print("  client: %s" % case["client"])
        print("  action: %s after %s ms; server duration reply (maxTime %s) after %s ms" % (
            a["result"], a["totalMs"], a["duration"], a["durationReplyMs"]))
        print("  => %s" % case["verdict"])
        if busy:
            lab.lua("ISTimedActionQueue.clear(getSpecificPlayer(0)) return true")

    # diagnostics: what the engine logged meanwhile
    report["clientExceptions"] = [e for e in lab.log.exceptions(since=client_log)]
    report["serverExceptions"] = [e for e in lab.server.log.exceptions(since=server_log)]
    report["dupeItemIds"] = [l for l in lab.log.lines(since=client_log) if "Dupe item ID" in l]
    print("\nengine exceptions during the probe: client %d, server %d; 'Dupe item ID' lines: %d" % (
        len(report["clientExceptions"]), len(report["serverExceptions"]), len(report["dupeItemIds"])))
finally:
    # cleanup: each step on its own, and none may hide the probe's own error
    for what, step in (("client trace", lambda: lab.status()["running"] and lab.lua("PZLab.traceActions(false) return true")),
                       ("server watch", lambda: lab.server.pid() and lab.server.lua("PZLab.itemWatch(true) return true")),
                       ("fixtures", lambda: lab.server.pid() and print("fixtures removed on the server: %s"
                                                                       % lab.server.clear_fixtures()))):
        try:
            step()
        except Exception as e:
            print("cleanup (%s) failed: %s" % (what, e))
    if not args.keep:
        if started_client:
            print("client:", lab.stop())
        if started_server:
            print("server:", lab.server.stop())

if args.json:
    print(json.dumps(report, indent=1, default=str))
failed = [c["check"] for c in report["checks"] if not c["ok"]]
print("%d/%d checks passed" % (len(report["checks"]) - len(failed), len(report["checks"])))
if failed:
    sys.exit(1)
