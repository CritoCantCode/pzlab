"""Re-verifies the Lua-level engine facts in AGENT.md ("Engine facts and traps") and exercises
lab.load_lua / lab.log.exceptions: file loading and hot reload, event rerouting, the Kahlua
standard library, errors caught by pcall, deferred UI removal, timers, wrapped event handlers.

Run after a game update: python pzlab.py run scripts/probe_engine.py
Needs a game in a world (python pzlab.py start). Registers a few event handlers that stay
registered for the session but do nothing once the probe ends."""

import json
import time

from labkit import LuaError

results = []
G = "PZLabProbeEngine"
RUN = "%x" % int(time.time())  # fresh file names: load_lua's first load of a name is boot-like


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", name, detail))


def hits(seconds=0.5):
    lab.lua("%s.hits = {} return true" % G)
    time.sleep(seconds)
    return lab.lua("return %s.hits" % G) or {}


lab.ensure_playable()
lab.lua("%s = { hits = {}, created = {} } return true" % G)

# --- Files through the engine's loader ---------------------------------------------------

outside = "C:/pzlab-probe-not-allowed/x.lua"
off = lab.log.size()
r = lab.lua('local ok, v = pcall(reloadLuaFile, %s) return { ok = ok, v = tostring(v) }' % json.dumps(outside))
ex = lab.log.exceptions(since=off)
check("reloadLuaFile refuses paths outside cachedir/mods/game dir (logged; returns nil, no Lua error)",
      r["ok"] is True and r["v"] == "nil" and len(ex) == 1 and "Invalid prefix found" in (ex[0]["message"] or ""),
      ex[0]["message"] if ex else r)
off = lab.log.size()
r = lab.lua('local ok, v = pcall(function() local sq = getCell():getGridSquare(nil, 0, 0) return "continued" end) '
            'return { ok = ok, v = v }')
ex = lab.log.exceptions(since=off)
check("an exception inside an exposed Java method is logged and the call returns nil",
      r == {"ok": True, "v": "continued"} and len(ex) == 1 and ex[0]["exception"] == "java.lang.NullPointerException",
      ex[0]["message"] if ex else r)

SOURCE = '''
local P = PZLabProbeEngine
local V = "%s"
local function hit(k) if not P.off then P.hits[k] = (P.hits[k] or 0) + 1 end end
local function onTickNamed() hit("named:" .. V) end
Events.OnTick.Add(onTickNamed)
P.named = onTickNamed
local function register(ev, tag)
    local handler = function() hit("helper:" .. tag .. ":" .. V) end
    Events[ev].Add(handler)
    P.created[#P.created + 1] = handler
end
register("OnTick", "a")
register("OnTick", "b")
register("OnPlayerUpdate", "c")
Events.OnTick.Add(function() hit("inline1:" .. V) end)
Events.OnPlayerUpdate.Add(function() hit("inline2:" .. V) end)
%s
if P.external then Events.OnTick.Remove(P.external) end
return V
'''
V1_EXTRA = 'function P.onThing() hit("dotted:" .. V) end\nEvents.OnTick.Add(P.onThing)'
V2_EXTRA = 'local onThing = function() hit("assigned:" .. V) end'

first = lab.load_lua("probe_reroute_" + RUN, SOURCE % ("v1", V1_EXTRA))
check("first load_lua is boot-like and returns the chunk's value",
      first["hot"] is False and first["value"] == "v1" and not first["errors"], first)
lab.lua("""local P = %s
    P.external = function() if not P.off then P.hits.external = (P.hits.external or 0) + 1 end end
    Events.OnTick.Add(P.external) return true""" % G)
names = lab.lua("return { named = getFilenameOfClosure(%s.named), bridge = getFilenameOfClosure(%s.external) }" % (G, G))
check("prototypes of the loaded file carry its path", names["named"] == first["path"], names["named"])
check("bridge chunks compiled after load_lua are attributed to _pzlab_bridge.lua",
      names["bridge"].endswith("/_pzlab_bridge.lua"), names["bridge"])
h1 = hits()
check("boot-like load: Events.X.Add registered every handler",
      set(h1) == {"named:v1", "helper:a:v1", "helper:b:v1", "helper:c:v1", "inline1:v1", "inline2:v1",
                  "dotted:v1", "external"}, sorted(h1))

second = lab.load_lua("probe_reroute_" + RUN, SOURCE % ("v2", V2_EXTRA))
check("second load_lua is a hot reload", second["hot"] is True and second["value"] == "v2" and second["loads"] == 2,
      {k: second[k] for k in ("hot", "value", "loads")})
h2 = hits()
n = h2.get("named:v2", 0)
check("hot reload: a named function's callback now runs the new version", n > 0 and "named:v1" not in h2, n)
check("hot reload: every closure of a generic helper now runs the last one created (helper c)",
      set(k for k in h2 if k.startswith("helper")) == {"helper:c:v2"} and h2["helper:c:v2"] >= 2.5 * n,
      {k: v for k, v in h2.items() if k.startswith("helper")})
check("hot reload: inline Events.X.Add(function ...) closures share the name 'Add' -> all run the last one",
      set(k for k in h2 if k.startswith("inline")) == {"inline2:v2"} and h2["inline2:v2"] >= 1.5 * n,
      {k: v for k, v in h2.items() if k.startswith("inline")})
check("prototype name is the last name before 'function': 'function P.onThing' rerouted to 'local onThing = function'",
      "assigned:v2" in h2 and "dotted:v1" not in h2, sorted(h2))
check("Events.X.Remove does nothing during a reload (external handler still runs)", h2.get("external", 0) > 0,
      h2.get("external"))

hot_first = lab.load_lua("probe_hot_first_" + RUN, 'local P = PZLabProbeEngine\n'
                         'P.hotFirst = function() if not P.off then P.hits.hotFirst = 1 end end\n'
                         'Events.OnTick.Add(P.hotFirst)\nreturn 1', hot=True)
check("hot=True on the first load: Events.X.Add does nothing", hot_first["hot"] and "hotFirst" not in hits(0.3))

err_src = 'local M = {}\nfunction M.explode()\n    local t = nil\n    return t.x\nend\nPZLabProbeEngine.M = M\nreturn true'
lab.load_lua("probe_errors_" + RUN, err_src)
off = lab.log.size()
lab.lua("pcall(%s.M.explode) return true" % G)
ex = lab.log.exceptions(since=off)
check("an error inside a loaded file names its file and line (log.exceptions)",
      len(ex) == 1 and ex[0].get("file") == "probe_errors_%s.lua" % RUN and ex[0].get("line") == 4
      and ex[0].get("function") == "explode" and ex[0]["message"] == "attempted index: x of non-table: null",
      ex[0] if ex else ex)
top = lab.load_lua("probe_errors_top_" + RUN, 'local function boom()\n    error("top-level boom")\nend\nboom()\nreturn 1', hot=True)
check("a hot reload that raises returns errors (logged by the engine), not an exception",
      len(top["errors"]) == 1 and top["errors"][0]["file"] == "probe_errors_top_%s.lua" % RUN
      and top["errors"][0]["message"] == "top-level boom", top["errors"])
try:
    lab.load_lua("probe_syntax_" + RUN, "local x = = 1")
    check("source that doesn't compile is rejected", False)
except LuaError as e:
    check("source that doesn't compile is rejected (LuaError, nothing loaded)",
          "unexpected symbol" in str(e) and not lab.lua_file_path("probe_syntax_" + RUN).exists(), str(e))

# reloadLuaFile on a file that doesn't compile leaves LuaCompiler.rewriteEvents on
bad = lab.lua_file_path("probe_stuck_" + RUN)
bad.parent.mkdir(parents=True, exist_ok=True)
bad.write_bytes(b"local x = = 1\n")
add_counter = """local P = %s
    P.counter = { n = 0 } P.counter.fn = function() P.counter.n = P.counter.n + 1 end
    Events.OnTick.Add(P.counter.fn) return true""" % G
read_counter = "local P = %s Events.OnTick.Remove(P.counter.fn) return P.counter.n" % G
rewriting = lab.lua("PZLab.reload(%s) return PZLab.eventsRewriting()" % json.dumps(bad.as_posix()))
lab.lua(add_counter)
time.sleep(0.3)
stuck = lab.lua(read_counter)
lab.lua("PZLab.resetCompiler() return true")
after_reset = lab.lua("return PZLab.eventsRewriting()")
lab.lua(add_counter)
time.sleep(0.3)
fixed = lab.lua(read_counter)
check("a reloadLuaFile compile error leaves Events.X.Add a no-op until a file load completes (PZLab.resetCompiler)",
      rewriting is True and stuck == 0 and after_reset is False and fixed > 0,
      {"eventsRewriting": rewriting, "ticks while stuck": stuck, "after resetCompiler": [after_reset, fixed]})

# functions defined in a table constructor get a nil prototype name: reroute throws on them
TABLE_SRC = """local P = PZLabProbeEngine
local V = "%s"
local T = { onA = function() if not P.off then P.hits["tableA:" .. V] = 1 end end }
P.tableA = T.onA
Events.OnTick.Add(T.onA)
return V"""
lab.load_lua("probe_table_" + RUN, TABLE_SRC % "v1")
t2 = lab.load_lua("probe_table_" + RUN, TABLE_SRC % "v2")
npe = [e for e in t2["errors"] if "prototype.name" in (e["message"] or "")]
h = hits(0.3)
check("hot reload of a file with a callback defined in a table constructor: reroute NPE, nothing reloaded",
      t2["value"] is None and len(npe) == 1 and "tableA:v1" in h and "tableA:v2" not in h,
      {"errors": [e["message"] for e in t2["errors"]], "hits": sorted(h)})
check("load_lua leaves event rewriting off after a failed reload", lab.lua("return PZLab.eventsRewriting()") is False)
table_path = lab.lua_file_path("probe_table_" + RUN).as_posix()
stuck_npe = lab.lua("PZLab.reload(%s) return PZLab.eventsRewriting()" % json.dumps(table_path))
res = lab.bridge.exec("return PZLab.eventsRewriting()")
check("after a raw reload hits that NPE, the next bridge command resets the compiler itself",
      stuck_npe is True and res.get("ok") and res.get("value") is False
      and any("compiler reset" in line for line in (res.get("out") or [])), {"stuck": stuck_npe, "reply": res})

lab.lua("""local P = %s P.off = true
    for _, ev in ipairs({"OnTick", "OnPlayerUpdate"}) do
        for _ = 1, 4 do
            for _, f in ipairs(P.created) do Events[ev].Remove(f) end
            for _, k in ipairs({"named", "onThing", "external", "hotFirst", "tableA"}) do if P[k] then Events[ev].Remove(P[k]) end end
        end
    end return true""" % G)

# --- Kahlua standard library -----------------------------------------------------------

EXPECTED_MISSING = ["next", "xpcall", "load", "loadfile", "dofile", "module", "gcinfo", "newproxy", "rawlen",
                    "io", "debug", "package", "bit", "bit32", "utf8", "table.unpack", "table.pack", "table.maxn",
                    "table.getn", "table.setn", "table.foreach", "table.foreachi", "table.move", "math.random",
                    "math.randomseed", "math.mininteger", "math.maxinteger", "math.tointeger", "math.type",
                    "os.clock", "os.getenv", "os.exit", "os.remove", "os.rename", "os.tmpname", "os.execute",
                    "coroutine.isyieldable"]
PRESENT = ["pcall", "select", "unpack", "setfenv", "getfenv", "loadstring", "loadstream", "collectgarbage",
           "string.gmatch", "string.split", "string.trim", "string.contains", "table.wipe", "table.isempty",
           "table.newarray", "math.huge", "math.fmod", "math.modf", "math.frexp", "math.ldexp", "os.time",
           "os.date", "os.difftime", "coroutine.wrap"]
lookup = """local out = {}
for _, n in ipairs(%s) do
    local a, b = string.match(n, "^([%%w_]+)%%.([%%w_]+)$")
    local v
    if a then local t = _G[a] v = type(t) == "table" and rawget(t, b) or nil else v = _G[n] end
    out[#out + 1] = v ~= nil
end
return out"""
present = lab.lua(lookup % ("{" + ",".join(json.dumps(n) for n in EXPECTED_MISSING + PRESENT) + "}"))
missing_now = [n for n, p in zip(EXPECTED_MISSING, present) if p]
present_now = [n for n, p in zip(PRESENT, present[len(EXPECTED_MISSING):]) if not p]
check("Kahlua lacks the listed functions", not missing_now, "now present: %s" % missing_now if missing_now else "")
check("Kahlua has the listed functions", not present_now, "now missing: %s" % present_now if present_now else "")
q = lab.lua("""local it = string.gmatch("abc", "x*")
    local gc = { pcall(collectgarbage, "count") }
    local stop = { pcall(collectgarbage, "stop") }
    return { mod = -7 % 3, fmod = math.fmod(-7, 3), gm = { it(), it(), it() },
             closure = tostring(function() end), osTime = os.time(), gcCount = gc[1], gcStop = stop[1] }""")
check("'%' truncates like Java (-7 % 3 == -1, Lua 5.1 gives 2)", q["mod"] == -1, q["mod"])
check("string.gmatch with a pattern matching '' never ends (returns '' forever)", q["gm"] == ["", "", ""], q["gm"])
check("tostring(function) is 'closure 0x...'", q["closure"].startswith("closure 0x"), q["closure"])
check("os.time() has a millisecond fraction", q["osTime"] != int(q["osTime"]), q["osTime"])
check("collectgarbage: 'count' works, 'stop' raises", q["gcCount"] is True and q["gcStop"] is False)
lit = chr(252) + chr(8364)  # u-umlaut, euro sign: sent raw (UTF-8) inside a Lua string literal
u = lab.lua("return { literal = { string.byte('%s', 1, -1) }, char = string.char(252, 8364) }" % lit)
check("non-ASCII in string literals compiles to U+FFFD; string.char keeps it",
      u["literal"] == [65533, 65533] and u["char"] == lit, ascii(u))

# --- Errors caught by pcall --------------------------------------------------------------

PCALL = """local c = getLuaDebuggerErrorCount()
    local r = { pcall(%s) }
    return { n = select("#", unpack(r)), ok = r[1], msg = tostring(r[2]), trace = tostring(r[3]),
             java = tostring(r[4]), counted = getLuaDebuggerErrorCount() - c }"""
for label, call, expect_msg in (("error('x') in a Lua function", 'function() error("probe x") end', "probe x"),
                                ("runtime error", "function() local t = nil return t.x end",
                                 "attempted index: x of non-table: null java.lang.RuntimeException")):
    off = lab.log.size()
    r = lab.lua(PCALL % call)
    lines = lab.log.lines(since=off)
    blocks = lab.log.exceptions(since=off)
    check("pcall(%s): 4 results, message %r, logged and counted" % (label, expect_msg),
          r["n"] == 4 and r["ok"] is False and r["msg"] == expect_msg and r["trace"].startswith("at ")
          and len(blocks) == 1 and len(lines) >= 80 and r["counted"] > 0,
          {"log lines": len(lines), "counter +": r["counted"], "trace": r["trace"]})
off = lab.log.size()
r = lab.lua(PCALL % 'error, "direct"')
check("pcall(error, 'x') with no Lua frame in between: not logged, not counted",
      r["ok"] is False and r["counted"] == 0 and not lab.log.lines(since=off), r["msg"])

# --- UI, timers, event registrations -----------------------------------------------------

r = lab.lua("""local w = ISPanel:new(10, 10, 40, 40) w:initialise()
    local before = UIManager.getUI():size()
    w:addToUIManager()
    local afterAdd = UIManager.getUI():size()
    PZLabProbeEngine.panel = w
    return { before = before, afterAdd = afterAdd }""")
r2 = lab.lua("""local w = PZLabProbeEngine.panel local s = UIManager.getUI():size()
    w:removeFromUIManager()
    return { nextFrame = s, afterRemove = UIManager.getUI():size(), contains = UIManager.getUI():contains(w.javaObject) }""")
r3 = lab.lua("return UIManager.getUI():contains(PZLabProbeEngine.panel.javaObject)")
check("UIManager add and remove are deferred to the next UI update",
      r["afterAdd"] == r["before"] and r2["nextFrame"] == r["before"] + 1 and r2["afterRemove"] == r2["nextFrame"]
      and r2["contains"] and r3 is False, [r, r2, r3])

t = lab.lua("""local t0, last, mn, changes = getTimestampMs(), getTimestampMs(), 1e9, 0
    while getTimestampMs() - t0 < 30 do
        local v = getTimestampMs()
        if v ~= last then changes = changes + 1 if v - last < mn then mn = v - last end last = v end
    end
    return { minStep = mn, changes = changes }""")
check("getTimestampMs ticks in 1 ms steps", t["minStep"] == 1, t)

r = lab.lua("""local orig, n = ISFPS.onKeyPressed, 0
    ISFPS.onKeyPressed = function(key) n = n + 1 return orig(key) end
    triggerEvent("OnKeyPressed", 250)
    local viaEvent = n
    ISFPS.onKeyPressed(250)
    ISFPS.onKeyPressed = orig
    return { viaEvent = viaEvent, direct = n - viaEvent }""")
check("wrapping ISFPS.onKeyPressed doesn't reach the closure Events.OnKeyPressed stored",
      r["viaEvent"] == 0 and r["direct"] == 1, r)

print("\n%d/%d passed" % (sum(results), len(results)))
