#!/usr/bin/python3 -I
"""Keyboard lighting + Windows-key test through the running NitroSense service (no sudo).

python3 -I tools/kbtest.py
Answer on the LAPTOP keyboard: typing on it keeps its backlight awake.
"""

import copy
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from nitrosense.client import Client, DaemonError  # noqa: E402

results = []


def ask(q: str) -> bool:
    while True:
        a = input(f"   ?  {q} [y/n] ").strip().lower()
        if a[:1] in ("y", "n"):
            return a.startswith("y")


def main() -> int:
    c = Client()
    st = c.request("status")
    saved_kb = copy.deepcopy(st["state"]["keyboard"])
    saved_win = st["state"]["win_key"]
    base = dict(saved_kb, mode="static", brightness=5, zones_on=[True] * 4)

    def kb(question: str, **changes) -> None:
        cfg = dict(base, **changes)
        try:
            c.request("set_keyboard", keyboard=cfg)
            ok = ask(question)
        except DaemonError as e:
            print("   error:", e)
            ok = False
        results.append((question, ok))
        print(f"   {'PASS' if ok else 'FAIL'}")
        time.sleep(0.2)

    print("Press any key on the laptop keyboard if its light goes off.\n")
    try:
        colors = ["#FF0000", "#00FF00", "#0000FF", "#FFFFFF"]
        kb("Zones left->right: RED, GREEN, BLUE, WHITE?", zone_colors=colors)
        kb("Zones 2 (green) and 4 (white) are now DARK, 1 and 3 still lit?",
           zone_colors=colors, zones_on=[True, False, True, False])
        kb("Brightness level 3: clearly dimmer than before?", zone_colors=colors, brightness=3)
        kb("Brightness level 1: keyboard light is OFF?", zone_colors=colors, brightness=1)
        dyn = dict(mode="dynamic", brightness=5)
        kb("BREATHING in BLUE?", **dyn, effect="breathing", speed=5, effect_color="#0000FF")
        kb("A rainbow WAVE moves across the keyboard?", **dyn, effect="wave", speed=7, direction=1)
        kb("The wave now moves the OTHER way?", **dyn, effect="wave", speed=7, direction=2)
        kb("Colours CYCLE over the whole keyboard (NEON)?", **dyn, effect="neon", speed=5)
        kb("A light-blue block SHIFTS across the zones?", **dyn, effect="shifting", speed=5,
           direction=1, effect_color="#0080FF")
        kb("ORANGE light ZOOMS in/out from the middle?", **dyn, effect="zoom", speed=5,
           effect_color="#FFA000")

        print("\n== Windows (Super) key lock")
        try:
            c.request("set_win_key", on=False)
            ok = ask("Press the Windows key on the LAPTOP keyboard: does NOTHING happen now?")
            c.request("set_win_key", on=True)
            ok2 = ask("Press it again: does the Activities overview open now?")
        except DaemonError as e:
            print("   error:", e)
            ok = ok2 = False
        results.extend([("Win key locked", ok), ("Win key unlocked", ok2)])
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        try:
            c.request("set_keyboard", keyboard=saved_kb)
            c.request("set_win_key", on=True if saved_win is None else saved_win)
        except DaemonError as e:
            print("restore failed:", e)
    print("\n== Summary")
    for q, ok in results:
        print(f"   {'PASS' if ok else 'FAIL'}  {q}")
    print(f"{sum(ok for _, ok in results)}/{len(results)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
