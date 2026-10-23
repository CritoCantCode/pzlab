"""pzlab: command-line driver for a Project Zomboid lab instance. Output is JSON.

  init [--game-dir DIR] [--no-import]   write lab.local.json for this machine (run once)
  doctor                           environment report
  check PATH...                    Lua syntax check with the game's own compiler (no launch)
  setup [--mod NAME]... [--mod-dir PATH]...   prepare the profile without launching
  start [--mod NAME]... [--mod-dir PATH]... [--menu] [--no-wait] [--at X Y Z | --at LOCATION]
  world [--at X Y Z | --at LOCATION]   launch a fresh world from the main menu of a running game
  stop [--force] | status
  lua CODE | lua -f FILE [--timeout MS]    (single expressions are auto-returned)
  shot [--out PATH] [--crop X Y W H] [--scale F] [--check]
  ui [--menus]                     UI state: pause menu, context menus, modals, speed, focus, problems
  playable [--speed N]             close context menus + pause menu, set speed; reports modals
  move X Y | click X Y [--button B] [--double] [--mod KEY]... | drag X1 Y1 X2 Y2 [--mod KEY]...
  wheel X Y N | hover OBJ [--allow-ui] (Lua expression or handle id)
  zoom [Z]                         camera zoom: show it and the levels, or set the nearest level
  goto NAME | goto X Y [Z]         teleport to a catalogue location or tile, wait until loaded
  locations                        the test-location catalogue (labkit/locations.json)
  panes [--no-pin]                 show + expand + pin the vanilla inventory and loot windows
  layout save | layout restore ID [--hide-added]   snapshot / restore top-level UI windows
  wait EXPR [--timeout S] [--poll S]     poll a Lua expression until truthy
  drag-row SRC ROW DST [--mod KEY]...    drag an inventory-pane row onto another pane (handles)
  key NAME | keydown NAME | keyup NAME | chord NAME... | type TEXT
  log [--errors | --exceptions] [--all] [--tail N] [--grep RE] | mark
  window [--x X --y Y] [--back]
  reload FILE                      hot-reload a mod Lua file by its repo path
  load-lua NAME (SRC | --code CODE) [--hot | --boot] | load-lua --clear
                                   run Lua source as file NAME through the engine's file loader
  run SCRIPT [ARGS...]             run a Python script with `lab` predefined
"""

import argparse
import json
import re
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from labkit import Lab, LuaError, locations  # noqa: E402

STATEMENT = re.compile(r"^\s*(local|for|while|if|do|repeat|function)\b|\breturn\b|^\s*[\w.\[\]\"']+\s*=[^=]|\n|;")


def emit(value):
    print(json.dumps(value, indent=1, default=str))


def as_chunk(code):
    return code if STATEMENT.search(code) else "return " + code


def at_position(values):
    """--at X Y Z or --at LOCATION -> world position dict (None when absent)."""
    if not values:
        return None
    if len(values) == 1:
        return locations.position(values[0])
    try:
        return locations.position([float(v) for v in values])
    except ValueError:
        raise ValueError("--at takes X Y Z or a location name, got %s" % " ".join(values))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pzlab", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="config file (default: lab.json + lab.local.json next to pzlab.py)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--game-dir")
    p.add_argument("--no-import", action="store_true",
                   help="don't copy ToS acceptance/language from this machine's Zomboid profile")
    sub.add_parser("doctor")
    p = sub.add_parser("check")
    p.add_argument("paths", nargs="+")

    for name in ("setup", "start"):
        p = sub.add_parser(name)
        p.add_argument("--mod", action="append", help="mod folder name / ID to enable (repeatable)")
        p.add_argument("--mod-dir", action="append",
                       help="path to a mod folder or Workshop item to link and enable (repeatable)")
        if name == "start":
            p.add_argument("--menu", action="store_true", help="stop at the main menu (no world)")
            p.add_argument("--no-wait", action="store_true")
            p.add_argument("--at", nargs="+", metavar="X Y Z | LOCATION",
                           help="spawn tile, or a name from labkit/locations.json")
            p.add_argument("--timeout", type=int, default=300)
    p = sub.add_parser("world", help="launch the configured world from the main menu")
    p.add_argument("--at", nargs="+", metavar="X Y Z | LOCATION")
    p.add_argument("--no-wait", action="store_true")
    p.add_argument("--timeout", type=int, default=300)
    p = sub.add_parser("stop")
    p.add_argument("--force", action="store_true")
    sub.add_parser("status")

    p = sub.add_parser("lua")
    p.add_argument("code", nargs="?")
    p.add_argument("-f", "--file")
    p.add_argument("--timeout", type=int, default=10000)

    p = sub.add_parser("shot")
    p.add_argument("--os", action="store_true", help="capture via PrintWindow (works without the harness)")
    p.add_argument("--out")
    p.add_argument("--crop", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    p.add_argument("--scale", type=float)
    p.add_argument("--check", action="store_true", help="also report uiState problems (pause menu, modals, ...)")

    p = sub.add_parser("ui", help="UI state: what would intercept input right now")
    p.add_argument("--menus", action="store_true", help="include visible context menus with their options")
    p = sub.add_parser("playable", help="close context menus and the pause menu, set game speed")
    p.add_argument("--speed", type=int, default=1, choices=range(0, 5))

    p = sub.add_parser("move")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p = sub.add_parser("click")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--button", default="left", choices=["left", "right", "middle"])
    p.add_argument("--double", action="store_true")
    p.add_argument("--mod", action="append", default=[], help="modifier key held during the click (repeatable)")
    p = sub.add_parser("drag")
    for a in ("x1", "y1", "x2", "y2"):
        p.add_argument(a, type=int)
    p.add_argument("--button", default="left", choices=["left", "right", "middle"])
    p.add_argument("--mod", action="append", default=[], help="modifier key held during the drag (repeatable)")
    p = sub.add_parser("hover", help="move the mouse onto a world object until the engine picks it")
    p.add_argument("obj", help="Lua expression for the object, or a handle id")
    p.add_argument("--allow-ui", action="store_true", help="also try points covered by UI windows")
    p = sub.add_parser("zoom", help="show the camera zoom and levels, or set the nearest level")
    p.add_argument("z", nargs="?", type=float)
    p = sub.add_parser("goto", help="teleport to a catalogue location or X Y [Z], wait until loaded")
    p.add_argument("target", nargs="+", metavar="NAME | X Y [Z]")
    p.add_argument("--radius", type=int, default=10, help="tiles around the target that must be loaded")
    p.add_argument("--timeout", type=int, default=30)
    sub.add_parser("locations", help="list the test-location catalogue")
    p = sub.add_parser("panes", help="show, expand and pin the vanilla inventory and loot windows")
    p.add_argument("--no-pin", action="store_true")
    p = sub.add_parser("layout", help="snapshot / restore the layout of top-level UI windows")
    p.add_argument("action", choices=["save", "restore"])
    p.add_argument("id", nargs="?", type=int)
    p.add_argument("--hide-added", action="store_true", help="restore: hide windows opened since")
    p = sub.add_parser("wait", help="poll a Lua expression until it is truthy")
    p.add_argument("expr")
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--poll", type=float, default=0.1)
    p = sub.add_parser("drag-row", help="drag an inventory-pane row onto another pane")
    p.add_argument("src", type=int, help="source pane handle")
    p.add_argument("row", type=int, help="row index (PZLab.paneRows)")
    p.add_argument("dst", type=int, help="destination pane handle")
    p.add_argument("--mod", action="append", default=[], help="modifier key held during the drag (repeatable)")
    p = sub.add_parser("wheel")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("notches", type=float)
    for name in ("key", "keydown", "keyup"):
        p = sub.add_parser(name)
        p.add_argument("name")
    p = sub.add_parser("chord")
    p.add_argument("names", nargs="+")
    p = sub.add_parser("type")
    p.add_argument("text")

    p = sub.add_parser("log")
    p.add_argument("--errors", action="store_true")
    p.add_argument("--exceptions", action="store_true",
                   help="engine-logged exceptions as JSON, one per 'Exception thrown' block")
    p.add_argument("--all", action="store_true", help="whole log instead of since the last mark")
    p.add_argument("--tail", type=int)
    p.add_argument("--grep")
    p.add_argument("--noise", action="store_true", help="keep known vanilla noise in --errors")
    sub.add_parser("mark")

    p = sub.add_parser("window")
    p.add_argument("--x", type=int)
    p.add_argument("--y", type=int)
    p.add_argument("--back", action="store_true")

    p = sub.add_parser("reload")
    p.add_argument("file")
    p = sub.add_parser("load-lua", help="run Lua source as a file through the engine's file loader")
    p.add_argument("name", nargs="?", help="file name in the lab's Lua scratch folder, e.g. probe.lua")
    p.add_argument("src", nargs="?", help="file holding the source")
    p.add_argument("--code", help="the source itself")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--hot", action="store_true", help="reloadLuaFile even on the first load")
    mode.add_argument("--boot", action="store_true", help="load like the engine's boot-time load")
    p.add_argument("--clear", action="store_true", help="delete the scratch files (and load nothing)")

    p = sub.add_parser("run")
    p.add_argument("script")
    p.add_argument("args", nargs=argparse.REMAINDER)

    args = parser.parse_args(argv)
    lab = Lab(args.config)

    try:
        if args.cmd == "init":
            emit(lab.init(args.game_dir, import_personal=not args.no_import))
        elif args.cmd == "doctor":
            emit(lab.doctor())
        elif args.cmd == "check":
            result = lab.check(args.paths)
            print("\n".join(result["output"]))
            return 0 if result["ok"] else 1
        elif args.cmd == "setup":
            emit(lab.setup(args.mod, args.mod_dir))
        elif args.cmd == "start":
            world = at_position(args.at)
            emit(lab.start(args.mod, world=world, autostart=not args.menu, wait=not args.no_wait,
                           timeout_s=args.timeout, mod_dirs=args.mod_dir))
        elif args.cmd == "world":
            world = at_position(args.at)
            emit(lab.launch_world(world, wait=not args.no_wait, timeout_s=args.timeout))
        elif args.cmd == "stop":
            emit(lab.stop(force=args.force))
        elif args.cmd == "status":
            emit(lab.status())
        elif args.cmd == "lua":
            code = Path(args.file).read_text(encoding="utf-8") if args.file else as_chunk(args.code)
            emit(lab.bridge.exec(code, args.timeout))
        elif args.cmd == "shot":
            if args.os:
                emit({"path": lab.os_screenshot(args.out, args.crop, args.scale)})
            elif args.check:
                emit(lab.screenshot(args.out, args.crop, args.scale, check=True))
            else:
                emit({"path": lab.screenshot(args.out, args.crop, args.scale)})
        elif args.cmd == "ui":
            state = lab.ui_state()
            if args.menus:
                state["menus"] = lab.context_menus()
            emit(state)
        elif args.cmd == "playable":
            emit(lab.ensure_playable(args.speed))
        elif args.cmd == "move":
            lab.input.move(args.x, args.y)
            emit({"ok": True})
        elif args.cmd == "click":
            lab.input.click(args.x, args.y, args.button, double=args.double, modifiers=args.mod)
            emit({"ok": True})
        elif args.cmd == "drag":
            lab.input.drag(args.x1, args.y1, args.x2, args.y2, args.button, modifiers=args.mod)
            emit({"ok": True})
        elif args.cmd == "hover":
            lab.hover(int(args.obj) if args.obj.isdigit() else args.obj, allow_ui=args.allow_ui)
            emit(lab.last_hover)
        elif args.cmd == "zoom":
            if args.z is not None:
                emit({"zoom": lab.set_zoom(args.z), "levels": lab.zoom_levels()})
            else:
                emit({"zoom": lab.get_zoom(), "levels": lab.zoom_levels()})
        elif args.cmd == "goto":
            target = args.target[0] if len(args.target) == 1 else [float(v) for v in args.target]
            emit(lab.goto(target, radius=args.radius, timeout_s=args.timeout))
        elif args.cmd == "locations":
            emit(lab.locations())
        elif args.cmd == "panes":
            emit(lab.show_inventory_panes(pin=not args.no_pin))
        elif args.cmd == "layout":
            if args.action == "save":
                snap = lab.save_layout()
                emit({"id": snap["id"], "windows": len(snap["windows"])})
            elif args.id is None:
                raise ValueError("layout restore needs the snapshot id from `layout save`")
            else:
                emit(lab.restore_layout(args.id, hide_added=args.hide_added))
        elif args.cmd == "wait":
            emit({"value": lab.wait_until(args.expr, args.timeout, args.poll)})
        elif args.cmd == "drag-row":
            emit(lab.drag_row(args.src, args.row, args.dst, modifiers=args.mod))
        elif args.cmd == "wheel":
            lab.input.wheel(args.x, args.y, args.notches)
            emit({"ok": True})
        elif args.cmd == "key":
            lab.input.tap(args.name)
            emit({"ok": True})
        elif args.cmd == "keydown":
            lab.input.key_down(args.name)
            emit({"ok": True})
        elif args.cmd == "keyup":
            lab.input.key_up(args.name)
            emit({"ok": True})
        elif args.cmd == "chord":
            lab.input.chord(*args.names)
            emit({"ok": True})
        elif args.cmd == "type":
            lab.input.type_text(args.text)
            emit({"ok": True})
        elif args.cmd == "log":
            since = 0 if args.all else None
            if args.exceptions:
                found = lab.log.exceptions(since)
                if args.grep:
                    found = [e for e in found if re.search(args.grep, json.dumps(e))]
                emit(found[-args.tail:] if args.tail else found)
                return 0
            lines = lab.log.errors(since, args.noise) if args.errors else lab.log.lines(since)
            if args.grep:
                lines = [ln for ln in lines if re.search(args.grep, ln)]
            if args.tail:
                lines = lines[-args.tail:]
            print("\n".join(lines))
        elif args.cmd == "mark":
            emit({"offset": lab.log.mark()})
        elif args.cmd == "window":
            emit({"rect": lab.place_window(args.x, args.y, args.back)})
        elif args.cmd == "reload":
            emit(lab.reload(args.file))
        elif args.cmd == "load-lua":
            if args.clear:
                emit({"removed": lab.clear_lua_files()})
            elif not args.name or (args.src is None) == (args.code is None):
                raise ValueError("load-lua NAME needs the source: a file SRC or --code CODE")
            else:
                source = args.code if args.code is not None else Path(args.src).read_text(encoding="utf-8")
                emit(lab.load_lua(args.name, source, hot=True if args.hot else False if args.boot else None))
        elif args.cmd == "run":
            sys.argv = [args.script] + args.args
            runpy.run_path(args.script, init_globals={"lab": lab}, run_name="__main__")
    except (LuaError, TimeoutError, RuntimeError, ValueError, FileNotFoundError) as e:
        emit({"error": type(e).__name__, "message": str(e),
              "result": getattr(e, "result", None)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
