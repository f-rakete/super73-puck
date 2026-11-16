# super73-puck

A one-button BLE ride-mode switch for a SUPER73 S2, built on an Espruino
Puck.js v2. Pressing the button changes the ride mode, the same thing the
official app does over Bluetooth. Later phases add Home Assistant presence,
battery and theft alerts via BTHome.

Builds on [hasuper73](https://github.com/f-rakete/hasuper73), which is
read-only, and the protocol work in
[Walker73](https://github.com/AxelFougues/Walker73).

## Layout

| Path | What |
|---|---|
| `docs/protocol.md` | GATT services, register frames, settings-write payload, ride modes, open questions |
| `tools/set_mode.py` | PC test tool (Python + bleak): read settings, write mode/light, verify |
| `src/main.js` | Puck.js REPL helpers that replay the same write (Phase 1, untested) |

## Status

Phase 1: the write command from Walker73 (`00 D1 <light> <assist> <mode> 00…`
written to `0000155f-…`) **works on our S2** (2026-11-16). It needs Just Works
bonding first. The mode resets to 4 at every power-on but survives a
disconnect. Still open: idle auto-off (`phase1b-test.cmd`, step 4) and whether
mode 5 really changes the assist. See the "Confirmed on our S2" section in
`docs/protocol.md`.

## Phase 1 test from Windows

1. Install Python 3.12+ from python.org, with the `py` launcher. Then run
   `py -m pip install -r tools/requirements.txt`.
2. Free the bike's BLE connection. Disable the hasuper73 integration in Home
   Assistant (or move out of proxy range) and close the SUPER73 app.
3. Switch the bike on next to the PC, standing still, and double-click
   `phase1-test.cmd` (or run `py tools\phase1_test.py`).

The guided test runs every check below in order, asks you what you see on the
bike, and logs everything to `logs/phase1-<timestamp>.log`. The `logs/` folder
is git-ignored. Steps:
scan and advertising data, GATT dump, device info, all registers, advertising
while connected, light write, mode write (5 → 4), the bike's own buttons,
restore, reconnect timing, and two optional ones: mode after power-on and idle
auto-off. It always leaves the bike in mode 4.

To run single commands by hand instead:

   ```powershell
   py tools/set_mode.py                                # read only: address + current settings
   py tools/set_mode.py --light on                     # safest write test
   py tools/set_mode.py --light off
   py tools/set_mode.py --mode 5 --allow-unrestricted  # mode change proof ...
   py tools/set_mode.py --mode 4                       # ... and back to EU EPAC
   py tools/set_mode.py --listen 60 -v                 # watch all frames
   ```

Note the printed address, the device-info values and every result in the
checklist at the end of `docs/protocol.md`.

Modes other than 4 (EU EPAC, 25 km/h / 250 W) are refused unless you pass
`--allow-unrestricted`. In Austria anything above 25 km/h counts as a moped.
