# Handover: super73-puck → hasuper73 session

From the Claude session working on **super73-puck** (Puck.js ride-mode
button, https://github.com/f-rakete/super73-puck, local
`C:\claude\super73-puck`), for the session that maintains **hasuper73**
(https://github.com/f-rakete/hasuper73). Written 2026-11-16, super73-puck
v0.2.0.

## The one thing we need from hasuper73

**Let go of the bike's BLE connection between polls.** The S2 takes one
connection at a time and stops advertising while connected. hasuper73 polls
every 10 s and keeps its connection open. While HA is connected, every Puck
press fails (3× red), and the official app can't connect either.

Recommended: **connect, read, disconnect** on each poll, and keep the link
up only for as long as a read takes.

## What the Puck does, so you know the timing you have to leave room for

A press (short = mode 4, long = mode 7) runs on the Puck:

1. `NRF.connect("f5:14:76:b9:41:ba random", {minInterval:15, maxInterval:45})`.
   If the bike isn't advertising, this **rejects after about 4 s**.
2. Just Works bonding (the Puck re-pairs every time, ~0.4 s).
3. Read SETTINGS, write `00 D1 <light> <assist> <mode> 00…` to
   `0000155f-…`, poll SETTINGS until the new mode shows (0.3–1.5 s lag).
4. Disconnect.

One press holds the bike **about 3 s**. On failure it **retries once**, so it
gives up about 8 s after the press if the bike never advertises in that time.
So:
- If hasuper73's link is down for most of each poll cycle, presses get
  through, worst case on the retry.
- **hasuper73 must handle "bike busy" gracefully.** A connect attempt can
  fail because the Puck holds the bike for ~3 s. Treat that as "retry next
  poll", not as unavailable, and don't raise errors in HA.
- The Puck changes the mode behind hasuper73's back. Read it fresh on each
  poll and don't cache the last-known mode as truth.

## Optional: event-driven yield (only if you want to keep a persistent link)

The Puck now advertises **BTHome**, and HA already has it. Every press shows
up as an event on `event.s73_puck_taste` (`event_type`: `press` /
`long_press`). The event is advertised at the start of the press, before the
Puck tries to connect. An HA automation could call a hasuper73 "release
connection" action on that event. But latency is tight: the Puck advertises
every 2 s, then the proxy and HA add their delay, and the Puck gives up after
~8 s. If you go this way, tell the super73-puck session: the Puck could
advertise faster while an event is pending. Connect-read-disconnect is
simpler and is the recommendation.

## Bike facts (confirmed on our S2, details in super73-puck `docs/protocol.md`)

- Address `F5:14:76:B9:41:BA`, random static. Advertises as `SUPER73` with
  no service UUID, about every 0.4–1.5 s; first advert 2.6–4.4 s after power-on.
- **Just Works bonding is required.** The first read without it fails with
  Insufficient Authentication. Bonds are reused on reconnect.
- PPCP asks for a 15–45 ms connection interval. With Espruino's default,
  GATT discovery took ~4.6 s, so request 15–45 if your stack lets you.
- The SETTINGS register lags a write by 0.3–1.5 s.
- **The mode resets to 4 at every power-on and is kept across BLE disconnects.**
  So disconnecting between polls doesn't change the mode.
- POWER notifications come about every 1.2 s while connected.
- **Open: does a connection keep the bike awake?** The wiki says auto-off
  after ~16 min idle; hasuper73 saw 5 min. Unmeasured on our S2. Worth
  noting what happens to auto-off once hasuper73 stops holding the link.

## Two hasuper73 bugs we noticed (not fixed)

- The **walk flag looks inverted**.
- **POWER bytes d2–d3 are the battery voltage in mV.** Check how hasuper73
  maps them.

## Legal (Austria)

Mode 4 (EU EPAC, 25 km/h / 250 W) is the only road-legal mode and the
power-on default. hasuper73 should never set another mode on its own.

## Home Assistant facts

- HA 2026.11.1 (HA OS, qemux86-64), German UI.
- The **SUPER73 config entry `01KTV2056843TQZ88T68RGAQ5C` is currently
  disabled by Flo** (from the Puck tests). Re-enable it for testing.
- Bluetooth: two ESPHome proxies (`esp32-bluetooth-proxy-f7d4d0`, `…-f810b4`)
  plus Shelly Plus 2PM / BLU gateway scanners.
- Puck entities: `sensor.s73_puck_batterie`, `…_spannung` (V),
  `…_temperatur`, `event.s73_puck_taste`. The Puck is BTHome device
  `FB:10:30:41:7F:86`, config entry `01M7JW9HRDVJQKZ6VJGMGZ3F0C`.
- **Shell access** (used by the super73-puck session):
  `ssh -i ~/.ssh/id_ed25519_hass root@192.168.1.3` (Terminal & SSH add-on).
  The add-on has **no Core API access** (401). For state history:
  `apk add sqlite`, then `sqlite3 -readonly /config/home-assistant_v2.db`
  (tables `states` + `states_meta` + `state_attributes`). Don't edit
  `/config/.storage` while HA Core runs.

## How to test coexistence

1. hasuper73 enabled and polling, bike on.
2. Ask Flo for a few short presses on the Puck (mode 4 is the legal default,
   so short presses are harmless) and note the LED: green = OK, 3× red =
   failed.
3. Read the Puck's press log with timings from the PC (the super73-puck repo):
   `py tools\puck.py --address FB:10:30:41:7F:86 "bike.log()"`.
   Each press logs connect, bond, read, write, verify, and the error if it
   failed (`Connection Timeout` = the bike wasn't advertising).
4. Check in HA's history that hasuper73 kept updating and picked up the
   mode the Puck set.

Flo prefers a guided script (prompts, asks what Flo sees, logs to a file)
over manual test steps. Flo says "done", then you read the log.
