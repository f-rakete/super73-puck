#!/usr/bin/env python3
"""Follow-up to the Phase 1 test: power-on behaviour, reconnects, idle auto-off.

    py tools/phase1b_test.py       (or double-click phase1b-test.cmd)

The first run proved the settings write, but the bike seems to have switched
itself off before the reconnect and power-cycle steps. This run starts from a
fresh power-on and measures:
- how long after power-on the bike advertises
- whether the bond from the first run is reused (no new pairing)
- whether the mode survives a power cycle (EU mode reset)
- how long a reconnect by address takes
- whether an open connection keeps the bike awake, and when it switches off
Log: logs/phase1b-<timestamp>.log
"""

from __future__ import annotations

import asyncio
import time

from bleak import BleakScanner

from phase1_test import (
    CHECKLIST_APP,
    CHECKLIST_HA,
    Session,
    ask,
    is_bike,
    log,
    pause,
    run,
    scan,
    step_idle_timer,
)
from set_mode import LEGAL_MODE, read_settings

TEST_MODE = 5  # EU MID, written only to see whether a power cycle resets it
IDLE_LIMIT_S = 20 * 60


async def wait_for_bike(timeout: float):
    """(seconds until the first SUPER73 advert, device), or (None, None)."""
    found = asyncio.Event()
    hit: list = []
    t0 = time.monotonic()

    def on_adv(device, adv) -> None:
        if not found.is_set() and is_bike(device, adv):
            hit.append((time.monotonic() - t0, device))
            found.set()

    async with BleakScanner(detection_callback=on_adv):
        try:
            await asyncio.wait_for(found.wait(), timeout)
        except asyncio.TimeoutError:
            return None, None
    return hit[0]


async def power_on(s: Session, prompt: str) -> None:
    """Ask for a power-on, wait for the first advert, connect."""
    await pause(prompt)
    s.t_on = time.monotonic()
    t_adv, device = await wait_for_bike(90)
    if device is None:
        raise RuntimeError("bike not seen within 90 s after power-on")
    s.address, s.device = device.address, device
    log(f"  first advert {t_adv:.1f} s after power-on ({device.address})")
    s.results["first advert after power-on"] = (s.results.get("first advert after power-on", "") + f"{t_adv:.1f} s ").strip()
    await s.connect()


def since_on(s: Session) -> str:
    return f"{(time.monotonic() - s.t_on) / 60:.1f} min after power-on"


async def step_fresh_power_on(s: Session) -> None:
    """Fresh power-on: advert delay, bond reuse, settings after power-on"""
    log("If the bike is on, switch it OFF now and wait ~5 s.")
    await power_on(s, "Switch the bike ON and press Enter at the same moment")
    s.original = await read_settings(s.client)
    s.results["settings after power-on"] = str(s.original)


async def step_power_cycle(s: Session) -> None:
    """Mode after a power cycle (EU mode reset)"""
    cur = await read_settings(s.client)
    await s.write(cur.light, cur.assist, TEST_MODE, f"mode {TEST_MODE} before power cycle")
    await s.disconnect()
    await power_on(s, "Switch the bike OFF, wait ~5 s, then switch it ON and press Enter at the same moment")
    after = await read_settings(s.client)
    if after.mode == LEGAL_MODE:
        verdict = "RESET to 4"
    elif after.mode == TEST_MODE:
        verdict = f"KEPT {TEST_MODE}"
    else:
        verdict = f"changed to {after.mode}"
    s.results["mode after power cycle"] = f"{verdict}: {after}"
    if after.mode != LEGAL_MODE:
        await s.write(after.light, after.assist, LEGAL_MODE, f"back to mode {LEGAL_MODE}")
    else:
        s.mode_dirty = False


async def step_reconnects(s: Session) -> None:
    """Reconnect by address, 3 times (cost of a Puck press)"""
    times = []
    for i in range(3):
        await s.disconnect()
        await asyncio.sleep(2)
        t0 = time.monotonic()
        try:
            await s.connect(by_address=True)
            times.append(f"{time.monotonic() - t0:.1f} s")
        except Exception as err:
            log(f"  reconnect {i + 1} failed: {type(err).__name__}: {err}")
            times.append("failed")
    s.results["reconnect by address -> first read"] = ", ".join(times)


async def step_idle_connected(s: Session) -> None:
    """Idle with the connection open: does it keep the bike awake? (up to 20 min, optional)"""
    log("Stays connected until the bike drops the connection or switches off.")
    log("Don't touch the bike (no buttons, no pedals). You can walk away; Ctrl+C stops it.")
    if await ask("Run the idle test?") == "n":
        s.results["idle, connected"] = "skipped"
        return
    await s.ensure_connected()
    s.quiet = True
    start = time.monotonic()
    next_report = 60
    while s.connected and time.monotonic() - start < IDLE_LIMIT_S:
        await asyncio.sleep(1)
        if time.monotonic() - start >= next_report:
            log(f"  still connected, {since_on(s)}")
            next_report += 60
    s.quiet = False

    if s.connected:
        s.results["idle, connected"] = f"still connected after {IDLE_LIMIT_S // 60} min ({since_on(s)})"
        log(f"  {s.results['idle, connected']}")
        await step_idle_timer(s)  # asks first; measures auto-off without a connection
        return

    dropped = since_on(s)
    log(f"  connection dropped {dropped}, checking whether the bike still advertises ...")
    still_on = s.address in await scan(15)
    s.results["idle, connected"] = f"dropped {dropped}; bike {'still advertising' if still_on else 'switched off (no adverts)'}"
    log(f"  {s.results['idle, connected']}")


STEPS = [step_fresh_power_on, step_power_cycle, step_reconnects, step_idle_connected]
REQUIRED = {step_fresh_power_on}


async def main() -> None:
    await run("SUPER73 Phase 1b test", "phase1b", [
        "  - bike parked next to this PC (on its stand, nobody riding it); on or off, the test tells you when",
        CHECKLIST_HA,
        CHECKLIST_APP,
        "Steps 1-3 take about 3 minutes; step 4 is optional (up to 20 min, you can walk away).",
        "'pairing required: no' in the results means the bond from the first run was reused.",
    ], STEPS, REQUIRED)


if __name__ == "__main__":
    asyncio.run(main())
