"""pzlab: command-line driver for a Project Zomboid lab instance. Output is JSON.

  init [--game-dir DIR] [--no-import]   write lab.local.json for this machine (run once)
  doctor                           environment report
  check PATH...                    Lua syntax check with the game's own compiler (no launch)
  setup [--mod NAME]... [--mod-dir PATH]...   prepare the profile without launching
  start [--mod NAME]... [--mod-dir PATH]... [--menu] [--no-wait] [--at X Y Z]
  world [--at X Y Z]               launch a fresh world from the main menu of a running game
  stop [--force] | status
  lua CODE | lua -f FILE [--timeout MS]    (single expressions are auto-returned)
  shot [--out PATH] [--crop X Y W H] [--scale F]
  move X Y | click X Y [--button B] [--double] | drag X1 Y1 X2 Y2 | wheel X Y N
  key NAME | keydown NAME | keyup NAME | chord NAME... | type TEXT
  log [--errors] [--all] [--tail N] [--grep RE] | mark
  window [--x X --y Y] [--back]
  reload FILE                      hot-reload a mod Lua file by its repo path
  run SCRIPT [ARGS...]             run a Python script with `lab` predefined
"""

import argparse
import json
import re
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from labkit import Lab, LuaError  # noqa: E402

STATEMENT = re.compile(r"^\s*(local|for|while|if|do|repeat|function)\b|\breturn\b|^\s*[\w.\[\]\"']+\s*=[^=]|\n|;")


def emit(value):
    print(json.dumps(value, indent=1, default=str))


def as_chunk(code):
    return code if STATEMENT.search(code) else "return " + code


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
            p.add_argument("--at", nargs=3, type=float, metavar=("X", "Y", "Z"))
            p.add_argument("--timeout", type=int, default=300)
    p = sub.add_parser("world", help="launch the configured world from the main menu")
    p.add_argument("--at", nargs=3, type=float, metavar=("X", "Y", "Z"))
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

    p = sub.add_parser("move")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p = sub.add_parser("click")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--button", default="left", choices=["left", "right", "middle"])
    p.add_argument("--double", action="store_true")
    p = sub.add_parser("drag")
    for a in ("x1", "y1", "x2", "y2"):
        p.add_argument(a, type=int)
    p.add_argument("--button", default="left", choices=["left", "right", "middle"])
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
            world = {"x": args.at[0], "y": args.at[1], "z": args.at[2]} if args.at else None
            emit(lab.start(args.mod, world=world, autostart=not args.menu, wait=not args.no_wait,
                           timeout_s=args.timeout, mod_dirs=args.mod_dir))
        elif args.cmd == "world":
            world = {"x": args.at[0], "y": args.at[1], "z": args.at[2]} if args.at else None
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
            else:
                emit({"path": lab.screenshot(args.out, args.crop, args.scale)})
        elif args.cmd == "move":
            lab.input.move(args.x, args.y)
            emit({"ok": True})
        elif args.cmd == "click":
            lab.input.click(args.x, args.y, args.button, double=args.double)
            emit({"ok": True})
        elif args.cmd == "drag":
            lab.input.drag(args.x1, args.y1, args.x2, args.y2, args.button)
            emit({"ok": True})
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
