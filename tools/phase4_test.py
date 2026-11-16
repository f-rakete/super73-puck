#!/usr/bin/env python3
"""Guided Phase 4 test: the Puck's BTHome adverts, Home Assistant, presence.

    py tools/phase4_test.py        (or double-click phase4-test.cmd)

Checks that the Puck's adverts decode as BTHome (battery, voltage,
temperature), that a press adds a button event for ~10 s, that Home Assistant
picks the Puck up, and (optional) that HA marks it unavailable when it goes
silent. Log: logs/phase4-<timestamp>.log
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
import traceback
from pathlib import Path

from bleak import BleakScanner

from phase1_test import LOG, ask, log, pause, setup_logging

PUCK = "FB:10:30:41:7F:86"
BTHOME = "0000fcd2-0000-1000-8000-00805f9b34fb"
PUCK_PY = Path(__file__).resolve().parent / "puck.py"
EVENTS = {1: "press", 4: "long_press"}
SILENT_S = 600  # presence test: how long the Puck stops advertising


def decode(data: bytes) -> dict:
    """BTHome v2 service data -> {info, pid, battery, volt, temp, event}; only the ids we send."""
    out = {"info": data[0]}
    i = 1
    while i < len(data):
        oid = data[i]
        if oid == 0x00:
            out["pid"] = data[i + 1]; i += 2
        elif oid == 0x01:
            out["battery"] = data[i + 1]; i += 2
        elif oid == 0x02:
            out["temp"] = int.from_bytes(data[i + 1:i + 3], "little", signed=True) / 100; i += 3
        elif oid == 0x0C:
            out["volt"] = int.from_bytes(data[i + 1:i + 3], "little") / 1000; i += 3
        elif oid == 0x3A:
            out["event"] = EVENTS.get(data[i + 1], data[i + 1]); i += 2
        else:
            out["unknown"] = data[i:].hex(" "); break
    return out


async def watch(seconds: float, until_silent: bool = False) -> list[tuple[float, dict, str]]:
    """Log each distinct Puck advert for `seconds`. Returns [(t, fields, name)]."""
    seen: list[tuple[float, dict, str]] = []
    last: list = [None]
    t0 = time.monotonic()

    def on_adv(device, adv) -> None:
        if device.address.upper() != PUCK:
            return
        data = adv.service_data.get(BTHOME)
        fields = decode(data) if data else {}
        t = time.monotonic() - t0
        seen.append((t, fields, adv.local_name or device.name or ""))
        LOG.debug(f"  adv +{t:5.1f}s rssi {adv.rssi} {data.hex(' ') if data else '-'}")
        if fields != last[0]:
            last[0] = fields
            log(f"  +{t:5.1f}s {fields}")

    async with BleakScanner(detection_callback=on_adv):
        await asyncio.sleep(seconds)
    return seen


def puck(js: str, wait: float = 1.0) -> str:
    """Run JS on the Puck via tools/puck.py, log and return the output."""
    r = subprocess.run([sys.executable, str(PUCK_PY), "--address", PUCK, "--wait", str(wait), js],
                       capture_output=True, text=True, timeout=180)
    LOG.debug(f"puck.py {js!r} -> rc {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r.stdout


async def step_adverts(res: dict) -> None:
    """Adverts as this PC sees them"""
    # This PC's scanner catches only a fraction of the Puck's 2 s adverts, so this
    # checks the content, not the rate. HA is the real receiver.
    log("Listening to the Puck for 60 s ...")
    seen = [x for x in await watch(60) if x[1]]
    if not seen:
        raise RuntimeError("no BTHome adverts from the Puck in 60 s (connected to something? out of range?)")
    t, f, name = seen[-1]
    log(f"  {len(seen)} adverts received, name {name!r}")
    ok = f.get("info") == 0x40 and all(k in f for k in ("pid", "battery", "volt", "temp")) and "unknown" not in f
    res["adverts"] = f"{'OK' if ok else 'BAD'} {f}"
    res["advert name"] = name


async def step_ha(res: dict) -> None:
    """Home Assistant discovers the Puck"""
    log("In Home Assistant open Settings > Devices & services.")
    log("Under 'Discovered' there should be a BTHome device 'S73 Puck 7F86'.")
    log("Click Add / Configure and confirm (no key needed).")
    res["HA discovered"] = await ask("Did HA discover and add 'S73 Puck 7F86'?")
    log("Open the device page. It should show Battery, Voltage and Temperature with the values above.")
    res["HA values"] = await ask("Do Battery, Voltage and Temperature show (roughly) those values?")


async def press(res: dict, key: str, what: str, expect: str, prompt: str) -> None:
    await pause(prompt)
    log(f"  {what} the Puck button now. Listening 40 s ...")
    seen = await watch(40)
    hits = [t for t, f, _ in seen if f.get("event") == expect]
    if hits:
        after = [t for t, f, _ in seen if t > hits[-1] and "event" not in f and f]
        gone = f", gone at +{after[0]:.1f}s" if after else ", still there after 40 s"
        res[key] = f"event '{expect}' seen +{hits[0]:.1f}s..+{hits[-1]:.1f}s{gone}"
    else:
        res[key] = f"'{expect}' not caught by this PC (it misses many adverts; HA is the real check)"
    log(f"  {res[key]}")


async def step_short(res: dict) -> None:
    """Short press: button event"""
    await press(res, "short press (PC)", "Short-press", "press",
                "Bike stays OFF (the Puck will blink red, that's expected). Press Enter, then short-press the Puck")
    res["short press LED"] = await ask("Did the Puck blink red 3x at the end?")
    log("In HA, on the S73 Puck device page, look at the Button event entity (or the Activity list).")
    res["HA short press"] = await ask("Does HA show a 'press' event from just now?")


async def step_long(res: dict) -> None:
    """Long press: button event"""
    await press(res, "long press (PC)", "Hold (until blue), then release", "long_press",
                "Bike stays OFF. Press Enter, then HOLD the Puck button until it lights blue, then release")
    res["HA long press"] = await ask("Does HA show a 'long_press' event from just now?")


async def step_bike_on(res: dict) -> None:
    """Optional: a real press with the bike on still works"""
    if await ask("Run the optional bike-ON check? (hasuper73 must be disabled in HA)") == "n":
        res["bike on press"] = "skipped"
        return
    await pause("Switch the bike ON, wait until the display is up, then press Enter")
    await press(res, "bike on event (PC)", "Short-press", "press",
                "Press Enter, then short-press the Puck (mode 4, the legal default)")
    res["bike on LED"] = await ask("Did the Puck end with a green blink?")
    res["HA bike on press"] = await ask("Does HA show another 'press' event?")
    log("Reading bike.log() from the Puck (waits for Windows to release the link) ...")
    out = puck("bike.log()", wait=2)
    for line in out.splitlines():
        log(f"  | {line}")
    log("You can switch the bike off again.")


async def step_presence(res: dict) -> None:
    """Optional: HA marks the Puck unavailable when it goes silent"""
    if await ask(f"Run the optional presence check? (takes about {SILENT_S // 60 + 2} min)") == "n":
        res["presence"] = "skipped"
        return
    log(f"Telling the Puck to stop advertising for {SILENT_S // 60} min ...")
    # Pause the 10-min sampler too, so it can't restart the adverts early. Delay so puck.py disconnects first.
    out = puck(f"setTimeout(function(){{var a=advertise;advertise=function(){{}};NRF.sleep();"
               f"setTimeout(function(){{advertise=a;NRF.wake();sample();}},{SILENT_S * 1000});}},3000)")
    if "S73 Puck" not in out:
        raise RuntimeError("could not reach the Puck console")
    t_cmd = time.monotonic()
    await asyncio.sleep(10)
    if await watch(15):
        raise RuntimeError("the Puck is still advertising")
    log("  The Puck is silent.")
    log("In HA, keep the S73 Puck device page open. The entities should turn 'Unavailable' within ~3-8 min.")
    left = SILENT_S - (time.monotonic() - t_cmd) - 60
    log(f"  waiting {left / 60:.1f} min ...")
    await asyncio.sleep(left)
    res["HA unavailable"] = await ask("Are the S73 Puck entities 'Unavailable' in HA now?")
    log("  Waiting for the Puck to come back ...")
    seen = await watch(150)
    back = next((t for t, f, _ in seen if f), None)
    res["adverts back"] = f"+{back:.0f}s" if back is not None else "NOT seen within 150 s"
    log(f"  adverts back: {res['adverts back']}")
    res["HA available again"] = await ask("Are the entities back (values, not 'Unavailable') in HA?")
    log("Optional: the entity's history in HA shows the exact time it went unavailable.")


STEPS = [step_adverts, step_ha, step_short, step_long, step_bike_on, step_presence]


async def main() -> None:
    path = setup_logging("phase4")
    log("SUPER73 Puck Phase 4 test: BTHome")
    log(f"log: {path}")
    log("")
    log("Before starting:")
    log("  - Puck next to this PC; the bike switched OFF")
    log("  - Home Assistant open in a browser")
    log("Steps 1-4 take about 5 minutes; 5 and 6 are optional. Ctrl+C aborts.")
    await pause("Ready?")
    res: dict[str, str] = {}
    try:
        for i, step in enumerate(STEPS, 1):
            log("")
            log(f"=== {i}/{len(STEPS)} {step.__doc__} ===")
            try:
                await step(res)
            except Exception as err:
                log(f"  STEP FAILED: {type(err).__name__}: {err}")
                LOG.debug(traceback.format_exc())
                res[f"step {i} error"] = f"{type(err).__name__}: {err}"
                if i == 1 or await ask("Continue with the next step?") == "n":
                    break
    finally:
        log("")
        log("=== RESULTS ===")
        for key, value in res.items():
            log(f"  {key:22} {value}")
        log("")
        log(f"Done. Tell Claude the test finished; the log is {path}")


if __name__ == "__main__":
    asyncio.run(main())
