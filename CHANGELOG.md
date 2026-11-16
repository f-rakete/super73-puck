# Changelog

## 0.1.0 - 2026-11-16

First working version: the button changes the bike's ride mode.

- **Puck.js firmware** (`src/main.js`):
  - Short press sets mode 4 (EU EPAC, the power-on default). Long press
    (hold until blue, ≥ 1 s) sets mode 7 (EU OFF-ROAD).
  - Each press bonds, keeps light and assist, verifies by polling, and
    disconnects, in about 3 s.
  - LED feedback: green / blue / 3× red. 15 s timeout and one retry.
  - RAM log of presses (`bike.log()`). Advertises as `S73 Puck`.
- **`tools/puck.py`:** Puck BLE console from the PC. Runs JS, prints the
  output, flashes a file as `.bootcde`.
- **`tools/set_mode.py`:** reads and writes the bike's ride settings from a PC
  with bleak.
- **`tools/phase1_test.py`, `tools/phase1b_test.py`:** guided bike tests that
  log to `logs/`.
- **`docs/protocol.md`:** the SUPER73 / Comodule BLE protocol from hasuper73
  and Walker73, plus what was confirmed on our S2. The settings write works;
  Just Works bonding is required; one connection at a time; the mode resets to
  4 at power-on and survives disconnects.
