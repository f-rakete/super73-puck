# super73-puck – notes for Claude sessions

One-button BLE ride-mode switch for Flo's SUPER73 S2, running on an Espruino
Puck.js v2.1. Status: README.md. Protocol and test results: docs/protocol.md.
History: CHANGELOG.md.

## Working with Flo

- Flo does not want to work through test steps by hand. Write a guided script
  that prompts, asks what Flo sees, and logs everything to `logs/`
  (git-ignored). Flo replies "done"; then read the log.
- Physical actions (bike on/off, button presses) need Flo. Give a short
  numbered list of exactly what to do, then wait.
- Flo approves admin (UAC) prompts on the machine when asked.
- Make minimal, targeted changes to existing code: no unrequested
  restructuring, hardening or new dependencies.
- Legal (Austria): mode 4 (EU EPAC, 25 km/h / 250 W) is the only road-legal
  mode and the bike's power-on default. Any other mode must be a deliberate act
  (long press, `--allow-unrestricted`, `bike.setMode(m, true)`).
- Commits are authored as `Florian Rakete <gh@rak.dev>`. Never put model names
  in commits.

## Machine (Windows 10, the user account is not admin)

- Python 3.14 (per-user): `%LOCALAPPDATA%\Programs\Python\Python314\python.exe`,
  `py` launcher; bleak 3.1 installed.
- git: `C:\Program Files\Git\cmd\git.exe`.
- gh: `%LOCALAPPDATA%\Microsoft\WinGet\Packages\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\bin\gh.exe`,
  logged in as `f-rakete`.
- winget: `%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe`, not on PATH.
  winget installs work; running a downloaded installer directly was blocked
  by the permission check.
- Shells opened before the installs may not have py/git/gh on PATH; use the
  full paths.
- The PC's Intel BLE adapter is bonded with the bike.

## Devices

| | Bike | Puck |
|---|---|---|
| Model | SUPER73 S2, Comodule display hw `v3.2.0`, fw `221122` | Puck.js **v2.1**, firmware **2v27** (latest: 2v29) |
| Address | `f5:14:76:b9:41:ba`, random static | `fb:10:30:41:7f:86` |
| Advertises | `SUPER73`, no service UUID, about every 0.4–1.5 s | `S73 Puck` + BTHome v2 (battery, V, °C, button: press / long_press / hold_press), every 2 s |
| Notes | Just Works bonding required; one connection at a time | Firmware in `.bootcde`; came with the stock "BTHome Button" app (replaced) |

## Puck workflow

```powershell
py tools\puck.py --address FB:10:30:41:7F:86 "bike.log()"            # last presses with timings
py tools\puck.py --address FB:10:30:41:7F:86 --flash src\main.js     # write .bootcde, load()
py tools\puck.py --address FB:10:30:41:7F:86 "bike.read()" --wait 20 # bike must be on
```

- `src/main.js` must stay plain ASCII (the flash byte count depends on it).
- **When the Puck connects to the bike, the PC's console link to the Puck
  drops.** Never rely on live output during a bike operation; read
  `bike.log()` afterwards.
- After a disconnect, Windows holds the link for about 30–60 s and the Puck
  doesn't advertise meanwhile. puck.py waits up to 60 s for an advert.
- Console lines prefixed with `\x10` print neither echo nor result.
- With the bike off, `NRF.connect` rejects after about 4 s
  (`Connection Timeout (3)`).

## Gotchas found (details in docs/protocol.md)

- The SETTINGS register lags a write by about 0.3–1.5 s, so poll before
  calling it a failure.
- Connect with `{minInterval:15, maxInterval:45}` (the bike's PPCP);
  Espruino's default made discovery take about 4.6 s.
- `gatt.disconnect()`'s promise sometimes never resolves, so there's a 1.5 s
  fallback.
- The Puck re-pairs on every press (`getSecurityStatus().bonded` is false).
  It costs about 0.4 s; not investigated yet.
- The mode resets to 4 at power-on and is kept across BLE disconnects.
- The bike stops advertising while connected. hasuper73 keeps its connection
  open, so **Puck presses fail while HA is connected**.
- Espruino 2v26+ adds its manufacturer ID (4 bytes) to every advert. With
  BTHome data that cut the name to `S73 Puc`, so `main.js` sets
  `manufacturer: false`. The advert is 28 bytes, 30 with a button event (max 31).
- This PC's scanner (bleak/WinRT, active or passive) catches only a fraction
  of the Puck's 2 s adverts, sometimes none for 30–50 s, while the Puck logs no
  connections. Don't judge the Puck's advertising from the PC; HA is the real
  receiver.
- HA (habluetooth) marks a device unavailable ~3 min after its last advert,
  checked every 5 min, so presence "away" lags 3–8 min. The BTHome "sleepy"
  flag must stay clear, or HA keeps the entities available forever.
- Event latency in HA: `hold_press` ~1 s; `press` / `long_press` with the bike
  off 6–15 s (the Puck's adverts barely get out while it tries to connect).
  HA's recorder writes in batches: query the DB a minute after an event, and
  allow for HA restarts by the hasuper73 session.

## Next steps

**Done: Phase 4, BTHome** (2026-11-16): battery %, voltage, temperature every
10 min, button events, presence via HA availability. Fields agreed with Flo:
no ride mode (the Puck can't know it), keep the name `S73 Puck`, include
temperature. Verified in HA's history: values every 10 min, one event per
`press` / `long_press`, presence (unavailable 6.7 min after going silent,
back immediately). HA
device renamed to `S73 Puck`: `sensor.s73_puck_batterie`, `…_spannung`,
`…_temperatur`, `event.s73_puck_taste`. The HA config entry is
`01M7JW9HRDVJQKZ6VJGMGZ3F0C`.

**HA access:** `ssh -i ~/.ssh/id_ed25519_hass root@192.168.1.3` (SSH add-on,
HA 2026.11, German UI). The add-on has no Core API access; read state history
from `/config/home-assistant_v2.db` with `sqlite3 -readonly` (`apk add sqlite`
first; it's gone after an add-on restart). Don't edit `.storage` while HA runs.

Next (recommended order; Flo hasn't picked yet):
1. **hasuper73 coexistence:** in `f-rakete/hasuper73`, connect, read and
   disconnect instead of holding the connection, so the Puck can get in.
   A separate Claude session handles hasuper73; handover in
   `docs/handover-hasuper73.md`.
2. **Theft alert** (motion while parked): accelerometer, about +40 µA, so
   battery life drops from ~1–1.5 years to ~5 months. Flo's call.
3. **Phase 3, pre-connect on motion:** about 1 s per press, at the cost of
   battery and holding the bike's only connection.

Also open:
- idle auto-off time (`phase1b-test.cmd`, step 4)
- whether modes 5/7 really change the assist (stand test)
- why the Puck doesn't store its bond
- two hasuper73 fixes: the walk flag looks inverted, and POWER d2–d3 is battery voltage in mV
