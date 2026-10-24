# pzlab

Test apparatus that drives a real Project Zomboid (Build 42) instance for automated mod testing. Everything about using it — commands, protocol, APIs, engine traps — is in [AGENT.md](AGENT.md); read it first.

## Purpose of every change

pzlab serves every future AI instance and the people they work for, whatever mod they are building. When a task for one mod reveals a missing capability, build it as a general tool (generic names, documented in AGENT.md, verified live), never as a shortcut shaped around that mod.

AGENT.md opens with "Steer the mod toward performance, safety and compatibility": agents using pzlab should propose fast, safe directions for the mod that coexist with vanilla and other mods (vanilla paths over re-implementations, a public API for what others may extend, wraps that tolerate other wraps), not just make its tests pass. When you verify a new engine trap that bears on a mod's speed, stability or interoperability, add it there too (one line, citing the trap).

## Rules

- Stay agnostic: no machine paths, user names or mod-specific code in committed files. Machine settings go in `lab.local.json` (gitignored, written by `pzlab.py init`); mods arrive via `--mod-dir`. Mod-specific test scripts live in the mod's own repo.
- Only write inside this repository (the runtime profile is `profile/`, the lab server's `profile-server/`). Everything outside — the game install, Steam's library list, `~/Zomboid` — is read-only.
- Never accept the game's Terms of Service on anyone's behalf; only carry over an acceptance the person already made in their own profile.
- Harness Lua is Kahlua (Lua 5.1): no `goto`, no integer division, and **no coroutines** (they crash the VM); no `next`, `xpcall`, `os.clock`, `math.random`; `%` truncates like Java; errors caught by `pcall` are still logged (~100 lines) and counted, so harness probes check types instead of pcall'ing; see AGENT.md "Engine facts and traps". Run `python pzlab.py check harness` after every Lua change.
- Python is stdlib-only (Pillow optional, with the `labkit/png.py` fallback). Keep it that way.
- `labkit/luacheck/LuaCheck.class` is committed so no JDK is needed; after editing `LuaCheck.java`, rebuild with `javac --release 17 -d labkit/luacheck labkit/luacheck/LuaCheck.java`.
- Verify changes against the live game (`doctor`, `start`, the affected command, `stop`), not just by reading the code.
