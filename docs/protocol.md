# SUPER73 / Comodule Diamond Display – BLE protocol

What we know about the bike's GATT interface, put together from:

- **Walker73** source: `Assets/BikeManager.cs`, `Assets/BikeState.cs`,
  `NativeBLE/.../NativeBLE.java` (https://github.com/AxelFougues/Walker73)
- **Walker73 wiki** (https://github.com/AxelFougues/Walker73/wiki). Its home
  page says it moved to `sites.google.com/view/super73-reverse-engineering`,
  but that page returned **404** on 2026-11-16, so the GitHub wiki is what we have.
- **hasuper73** 0.1.4 `custom_components/super73/` (https://github.com/f-rakete/hasuper73)

Nothing in this file has been tested on our S2 yet. Tick off the checklist at
the end as things get confirmed.

## Discovery

| What | Value |
|---|---|
| Advertised name | `SUPER73…` (hasuper73 matches `SUPER73*`, `Super73*`, `super73*`) |
| Advertised service | metrics service `00001554-…` – **not on every bike**, so match by name as a fallback |
| GAP device name (`2a00`) | `SUPER73` (`53 55 50 45 52 37 33`) |
| Manufacturer (`2a29`) | `COMODULE` |
| Hardware rev (`2a27`) | e.g. `v3.2.0` |
| Firmware / software (`2a26` / `2a28`) | e.g. `221122` |

Example values are from comments in Walker73's `BikeManager.cs`.

## Services

| Service | UUID | Characteristics | Notes |
|---|---|---|---|
| Generic Access | `1800` | `2a00` name, `2a01` appearance (`00 04`), `2a04` PPCP, `2aa6` central address resolution (`01`) | PPCP `0C 00 24 00 00 00 00 01` = preferred interval 15–45 ms, latency 0, supervision timeout 2.56 s |
| Generic Attribute | `1801` | `2a05` service changed | |
| Device Information | `180a` | `2a29`, `2a27`, `2a26`, `2a28` | ASCII strings |
| Secure DFU | `fe59` | `8ec90003-f315-4f60-9fb8-838830daea50` buttonless DFU | Nordic nRF52 in the display. **Never write this**, it reboots the display into its bootloader |
| Unknown | `1580` | `1581` notify | Walker73 saw `0x5A499C34` on a BMZ bike |
| Security | `00002554-1212-efde-1523-785feabcd123` | `2555` private key (W), `2556` public key (R, 20 B), `2557` hash (W), `2558` auth (R) | Purpose unknown. Neither Walker73 nor hasuper73 touches it, and Walker73's writes work without it |
| **Metrics** | `00001554-1212-efde-1523-785feabcd123` | see below | Everything we need |

The full 128-bit form of the 16-bit UUIDs is `0000xxxx-0000-1000-8000-00805f9b34fb`.
The Security and Metrics UUIDs share the vendor base `0000xxxx-1212-efde-1523-785feabcd123`.

### Metrics service characteristics

| Name | UUID | Props | Use |
|---|---|---|---|
| `REGISTER_ID` | `00001564-1212-efde-1523-785feabcd123` | R/W | write a 2-byte register ID to pick what `REGISTER` returns |
| `REGISTER` | `0000155f-1212-efde-1523-785feabcd123` | R/W | read the picked register, **or write settings** |
| `REGISTER_NOTIFIER` | `0000155e-1212-efde-1523-785feabcd123` | R/Notify | the bike pushes any register frame when its value changes |

## Reading

1. Write the 2-byte ID to `REGISTER_ID` (with response).
2. Read `REGISTER` and get a 10-byte frame `[id0 id1 d2 d3 d4 d5 d6 d7 d8 d9]`.

Subscribing to `REGISTER_NOTIFIER` sends the same 10-byte frames whenever a value changes.
hasuper73 polls in the order SETTINGS, TOTAL, RIDE, POWER, MOTION, MYSTERY.

| Register | ID | Fields | Decoding |
|---|---|---|---|
| SETTINGS | `03 00` | d2 assist, d3 walk, d4 light, d5 mode | assist 0–4. light `01` = on. mode 0–7. walk: Walker73/hasuper73 set `walk = (d3 == 0x00)`, the wiki says "0/90?", so the meaning is unclear |
| MOTION | `02 01` | d2–d3 | u16 LE wheel speed, ÷100 = km/h |
| TOTAL | `02 02` | d6–d7 | u16 LE odometer, ÷10 = km. d3 flickers `4E`/`4D` while the motor runs |
| RIDE | `02 03` | d3 cadence, d8 range | cadence ×5 = pedal rpm. d8 = range in km (hasuper73 also derives battery % from it against a 60 km base range). d2 looks torque-related |
| POWER | `04 01` | d6–d7 | u16 LE charge current, ÷1000 = A. The wiki table marks d5–d6, but both codebases read `[6:8]` |
| MYSTERY | `00 00` | d2 light, d3 assist, d4 mode | Same field order as the settings write below. Probably the last commanded settings (guess) |
| – | `00 01` … `00 06` | – | Respond, always all zeros |

## Writing settings (mode / assist / light)

Write **10 bytes to `REGISTER`** (`0000155f-…`), **with response**
(Walker73 uses Android `WRITE_TYPE_DEFAULT`):

```
00 D1 LL AA MM 00 00 00 00 00
│  │  │  │  │  └──────────── d5–d9 ignored
│  │  │  │  └─ mode    00–07
│  │  │  └──── assist  00–04
│  │  └─────── light   00 off / 01 on
└──┴────────── settings-write ID
```

- **All three fields are written together.** Read SETTINGS (`03 00`) first and
  copy the fields you are not changing. Otherwise a mode change also turns the
  light off and resets assist.
- The bike answers with a SETTINGS (`03 00`) frame on the notifier. Walker73's
  wiki calls this the "3-0 notification".
- **Never write mode ≥ 8 or assist ≥ 5.** The wiki lists those as
  unimplemented, meaning 0 km/h / 0 W / 0 % assist.
- Walker73 does no pairing and no security-service handshake before writing.
  hasuper73 0.1.1 added "pair and retry on *Insufficient authentication*" for
  register reads, so at least some bike/backend combinations need an encrypted
  (bonded) link. `tools/set_mode.py` does the same.

### Other writable IDs (unknown – do not write on a bike you ride)

Brute-forcing `00 00`…`00 FF` with zero data showed these IDs also trigger a
SETTINGS notification:

`00 13`, `00 26`, `00 3A`, `00 4C`, `00 5F`, `00 73`, `00 86`, `00 99`, `00 AC`, `00 BF`, **`00 D1`**, `00 E4`, `00 F7`

They are spaced almost evenly, about 19 apart. That suggests byte 1 is a check
or key byte rather than a command ID (unverified guess). If `00 D1` is rejected
on our firmware, look here first.

## Ride modes

From the Walker73 wiki. Names in brackets are the ones hasuper73 shows.

| ID | Name | Region | Max assist speed | Max power | Throttle | Legal in AT |
|---|---|---|---|---|---|---|
| 0 | CLASS1 (US CLASS1) | US | 32.2 km/h | 750 W | no | no |
| 1 | CLASS2 (US CLASS2) | US | 32.2 km/h | 750 W | yes | no |
| 2 | CLASS3 (US CLASS3) | US | 45 km/h | 750 W | no | no |
| 3 | OFF_ROAD (US OFF-ROAD) | US | max | max | yes | no |
| **4** | **EPAC (EU EPAC)** | EU | **25 km/h** | **250 W** | no | **yes – EU default** |
| 5 | 250W (EU MID) | EU | 35 km/h | 250 W | no | no |
| 6 | 850W (EU HIGH) | EU | 45 km/h | 850 W | no | no |
| 7 | OFF_ROAD (EU OFF-ROAD) | EU | max | max | yes | no |
| 8+ | – | – | 0 | 0 | no | unimplemented |

The wiki says "Both EU and US modes work on all bikes."
In Austria a pedelec is limited to 25 km/h and 600 W, so **mode 4 is the only
road-legal mode**. Everything else needs a deliberate opt-in in our tools.

## Assist (PAS) levels

| ID | Level |
|---|---|
| 0 | 0 % |
| 1 | 25 % |
| 2 | 50 % |
| 3 | 75 % |
| 4 | 100 % |
| 5+ | unimplemented, 0 % |

## Behaviour

- **EU mode reset:** Walker73 advertises "auto applies previous settings, bypass
  EU mode reset". That implies EU bikes go back to mode 4 at every power-on, so
  the Puck has to re-apply its mode on every connect.
- **Auto-off:** the wiki says about 16 min of inactivity. Throttle counts as
  activity. Light toggles, PAS changes, a short wheel spin and pedalling under
  20 km/h without PAS do not. hasuper73 observed 5 min idle. Measure on our S2.
- **Connection count:** unknown. hasuper73 polls every 10 s and keeps its
  connection open, so if the display takes only one central, HA blocks both the
  Puck and the official app.
- **Connection interval:** the PPCP asks for 15–45 ms. A slow idle interval for
  the Puck (Phase 3, e.g. 200 ms+) may be rejected or renegotiated. Test it.

## To verify on our S2

- [ ] Advertising: name, metrics UUID advertised?, address type (public/random)
- [ ] Device info: hardware ___ / firmware ___ / software ___
- [ ] SETTINGS read works without pairing (Windows/bleak)
- [ ] Settings write `00 D1 …` accepted: light toggle
- [ ] Settings write `00 D1 …` accepted: mode change (verify via notifier and re-read)
- [ ] Pairing/bonding required? On Windows? On the Puck?
- [ ] Simultaneous centrals: Puck + HA + app
- [ ] Mode reset on power-on (EU)
- [ ] Idle auto-off: 5 min or 16 min?
- [ ] Slowest connection interval the bike accepts
