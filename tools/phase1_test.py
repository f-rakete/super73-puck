#!/usr/bin/env python3
"""Guided Phase 1 test: prove the SUPER73 settings write from this PC.

    py tools/phase1_test.py        (or double-click phase1-test.cmd)

Walks through scan, GATT dump, register reads, light and mode writes and a few
behaviour checks (reconnect time, mode reset on power-on, idle auto-off),
asking you to confirm what you see on the bike. Everything is written to
logs/phase1-<timestamp>.log; bleak's own debug output goes to *-bleak.log.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import platform
import statistics
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner

from set_mode import (
    LEGAL_MODE,
    METRICS_SERVICE,
    MODE_NAMES,
    NOTIFIER_CHAR,
    REGISTER_CHAR,
    REGISTER_ID_CHAR,
    REGISTER_NAMES,
    SETTINGS_WRITE_ID,
    Settings,
    is_settings,
    read_settings,
)

TEST_MODE = 5  # EU MID, written briefly to prove a mode change; always followed by mode 4
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG = logging.getLogger("phase1")

ALL_REGISTERS = [bytes((0x03, 0x00)), bytes((0x02, 0x02)), bytes((0x02, 0x03)), bytes((0x04, 0x01)),
                 bytes((0x02, 0x01)), bytes((0x00, 0x00))] + [bytes((0x00, i)) for i in range(1, 7)]

DEVICE_INFO_CHARS = {
    "device name": "00002a00-0000-1000-8000-00805f9b34fb",
    "manufacturer": "00002a29-0000-1000-8000-00805f9b34fb",
    "hardware": "00002a27-0000-1000-8000-00805f9b34fb",
    "firmware": "00002a26-0000-1000-8000-00805f9b34fb",
    "software": "00002a28-0000-1000-8000-00805f9b34fb",
}


class Abort(Exception):
    """Stop the whole test."""


def setup_logging() -> Path:
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"phase1-{datetime.now():%Y%m%d-%H%M%S}.log"
    file_fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", "%H:%M:%S")
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(file_fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    sh.setLevel(logging.INFO)  # tracebacks and recorded answers go to the file only
    LOG.setLevel(logging.DEBUG)
    LOG.addHandler(fh)
    LOG.addHandler(sh)
    LOG.propagate = False

    bh = logging.FileHandler(path.with_name(path.stem + "-bleak.log"), encoding="utf-8")
    bh.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(name)s %(message)s", "%H:%M:%S"))
    blog = logging.getLogger("bleak")
    blog.setLevel(logging.DEBUG)
    blog.addHandler(bh)
    blog.propagate = False
    return path


def log(msg: str = "") -> None:
    LOG.info(msg)


async def ask(question: str, choices: tuple[str, ...] = ("y", "n")) -> str:
    while True:
        answer = (await asyncio.to_thread(input, f"\n>>> {question} [{'/'.join(choices)}] ")).strip().lower()
        if answer in choices:
            LOG.debug(f"Q: {question} -> {answer}")
            return answer


async def pause(message: str) -> None:
    await asyncio.to_thread(input, f"\n>>> {message} [Enter] ")
    LOG.debug(f"(Enter) {message}")


def is_bike(device, adv) -> bool:
    name = (adv.local_name or device.name or "").lower()
    return METRICS_SERVICE in adv.service_uuids or name.startswith("super73")


def address_type(device) -> str:
    try:
        return str(device.details.adv.bluetooth_address_type)
    except Exception:  # not exposed by every backend / Windows version
        return "unknown"


async def scan(seconds: float) -> dict:
    seen: dict = {}

    def on_adv(device, adv) -> None:
        entry = seen.setdefault(device.address, {"times": []})
        entry["device"], entry["adv"] = device, adv
        entry["times"].append(time.monotonic())

    async with BleakScanner(detection_callback=on_adv):
        await asyncio.sleep(seconds)
    return seen


async def wait_for_adv(address: str, timeout: float) -> float | None:
    """Seconds until the first advertisement from address, or None."""
    found = asyncio.Event()
    t0 = time.monotonic()
    t_found: list[float] = []

    def on_adv(device, _adv) -> None:
        if device.address == address and not found.is_set():
            t_found.append(time.monotonic() - t0)
            found.set()

    async with BleakScanner(detection_callback=on_adv):
        try:
            await asyncio.wait_for(found.wait(), timeout)
        except asyncio.TimeoutError:
            return None
    return t_found[0]


class Session:
    def __init__(self) -> None:
        self.address: str | None = None
        self.device = None
        self.client: BleakClient | None = None
        self.results: dict[str, str] = {}
        self.settings_q: asyncio.Queue = asyncio.Queue()
        self.frames: list[tuple[float, bytes]] = []
        self.original: Settings | None = None
        self.mode_dirty = False  # a mode other than LEGAL_MODE was written and not reverted

    # --- connection -------------------------------------------------------

    def _on_disconnect(self, _client) -> None:
        log("  [bike disconnected]")

    def _on_notify(self, _char, data: bytearray) -> None:
        frame = bytes(data)
        self.frames.append((time.monotonic(), frame))
        extra = f"  -> {Settings(frame)}" if is_settings(frame) else ""
        log(f"  notify {REGISTER_NAMES.get(frame[:2], '?'):8} {frame.hex(' ')}{extra}")
        if is_settings(frame):
            self.settings_q.put_nowait(frame)

    @property
    def connected(self) -> bool:
        return self.client is not None and self.client.is_connected

    async def connect(self, by_address: bool = False) -> None:
        target = self.address if by_address or self.device is None else self.device
        log(f"Connecting to {self.address} ({'by address' if by_address else 'from scan result'}) ...")
        t0 = time.monotonic()
        self.client = BleakClient(target, disconnected_callback=self._on_disconnect, timeout=30)
        await self.client.connect()
        t_conn = time.monotonic() - t0
        try:
            settings = await read_settings(self.client)
            if "pairing required" not in self.results:
                self.results["pairing required"] = "no"
        except Exception as err:
            if "authenticat" not in str(err).lower():
                raise
            log(f"  first read refused: {err}")
            log("  pairing - accept the Windows pairing prompt if one appears")
            await self.client.pair()
            settings = await read_settings(self.client)
            self.results["pairing required"] = "yes (Windows pair() worked)"
        t_read = time.monotonic() - t0
        await self.client.start_notify(NOTIFIER_CHAR, self._on_notify)
        log(f"  connected in {t_conn:.2f} s, first SETTINGS read after {t_read:.2f} s: {settings}")

    async def ensure_connected(self) -> None:
        if not self.connected:
            await self.connect(by_address=True)

    async def disconnect(self) -> None:
        if self.connected:
            await self.client.disconnect()
        self.client = None

    # --- settings write ---------------------------------------------------

    async def write(self, light: bool, assist: int, mode: int, label: str) -> bool:
        await self.ensure_connected()
        while not self.settings_q.empty():
            self.settings_q.get_nowait()
        payload = SETTINGS_WRITE_ID + bytes((int(light), assist, mode, 0, 0, 0, 0, 0))
        log(f"WRITE {label}: {payload.hex(' ')}")
        t0 = time.monotonic()
        await self.client.write_gatt_char(REGISTER_CHAR, payload, response=True)
        t_ack = time.monotonic() - t0
        if mode != LEGAL_MODE:
            self.mode_dirty = True

        t_notify = None
        deadline = t0 + 3.0
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                s = Settings(await asyncio.wait_for(self.settings_q.get(), remaining))
            except asyncio.TimeoutError:
                break
            if s.mode == mode and s.light == light and s.assist == assist:
                t_notify = time.monotonic() - t0
                break

        now = await read_settings(self.client)
        ok = now.mode == mode and now.light == light and now.assist == assist
        if ok and mode == LEGAL_MODE:
            self.mode_dirty = False
        notify = f"{t_notify * 1000:.0f} ms" if t_notify is not None else "none within 3 s"
        log(f"  write ack {t_ack * 1000:.0f} ms, matching notify {notify}")
        log(f"  re-read {now} -> {'OK' if ok else 'MISMATCH'}")
        return ok


# --- steps -----------------------------------------------------------------


async def step_scan(s: Session) -> None:
    """Scan for the bike (10 s)"""
    while True:
        seen = await scan(10)
        bikes = [(addr, e) for addr, e in seen.items() if is_bike(e["device"], e["adv"])]
        log(f"{len(seen)} BLE devices seen, {len(bikes)} SUPER73 candidate(s)")
        if bikes:
            break
        if await ask("No SUPER73 found. Is it on? Are HA (hasuper73) and the app disconnected? Retry?") == "n":
            raise Abort("bike not found")
    bikes.sort(key=lambda b: b[1]["adv"].rssi, reverse=True)
    for addr, e in bikes:
        adv, times = e["adv"], e["times"]
        gaps = [b - a for a, b in zip(times, times[1:])]
        interval = f"{statistics.median(gaps) * 1000:.0f} ms" if gaps else "n/a"
        log(f"  {addr}  name={adv.local_name or e['device'].name!r}  rssi={adv.rssi}  tx_power={adv.tx_power}")
        log(f"    address type: {address_type(e['device'])}")
        log(f"    service UUIDs: {adv.service_uuids}")
        log(f"    manufacturer data: { {k: v.hex(' ') for k, v in adv.manufacturer_data.items()} }")
        log(f"    service data: { {k: v.hex(' ') for k, v in adv.service_data.items()} }")
        log(f"    {len(times)} adverts in 10 s, median gap {interval} (approx, includes scan responses)")
    s.address, entry = bikes[0]
    s.device = entry["device"]
    s.results["address"] = s.address
    s.results["address type"] = address_type(s.device)
    s.results["advertised name"] = repr(entry["adv"].local_name or s.device.name)
    s.results["metrics UUID advertised"] = "yes" if METRICS_SERVICE in entry["adv"].service_uuids else "no"
    s.results["RSSI"] = str(entry["adv"].rssi)


async def step_connect(s: Session) -> None:
    """Connect, check pairing, dump the GATT table"""
    await s.connect()
    s.results["connect time"] = "see log"
    log(f"MTU {s.client.mtu_size}")
    for service in s.client.services:
        log(f"  service {service.uuid}  {service.description}")
        for char in service.characteristics:
            log(f"    char {char.uuid}  [{', '.join(char.properties)}]  {char.description}")


async def step_device_info(s: Session) -> None:
    """Read device information"""
    await s.ensure_connected()
    for label, uuid in DEVICE_INFO_CHARS.items():
        try:
            raw = bytes(await s.client.read_gatt_char(uuid))
            value = raw.decode("ascii", errors="replace").strip("\x00")
            log(f"  {label:12} {value!r}  [{raw.hex(' ')}]")
            s.results[label] = value
        except Exception as err:
            log(f"  {label:12} not readable: {err}")


async def step_registers(s: Session) -> None:
    """Read every known register"""
    await s.ensure_connected()
    for reg in ALL_REGISTERS:
        try:
            await s.client.write_gatt_char(REGISTER_ID_CHAR, reg, response=True)
            frame = bytes(await s.client.read_gatt_char(REGISTER_CHAR))
            log(f"  {REGISTER_NAMES.get(reg, 'reg'):8} {reg.hex(' ')} -> {frame.hex(' ')}")
        except Exception as err:
            log(f"  {reg.hex(' ')} failed: {err}")
    s.original = await read_settings(s.client)
    s.results["settings at start"] = str(s.original)


async def step_adv_while_connected(s: Session) -> None:
    """Does the bike keep advertising while connected? (hint for multiple connections, 6 s)"""
    await s.ensure_connected()
    seen = await scan(6)
    still = s.address in seen
    log(f"  bike advertising while connected: {'yes' if still else 'no'}")
    s.results["advertises while connected"] = "yes" if still else "no (probably single connection)"


async def step_light(s: Session) -> None:
    """Headlight write (safest write test)"""
    cur = await read_settings(s.client) if s.connected else s.original
    target = not cur.light
    await pause(f"Watch the headlight. Press Enter to switch it {'ON' if target else 'OFF'}")
    ok1 = await s.write(target, cur.assist, cur.mode, f"light {'on' if target else 'off'}")
    seen1 = await ask(f"Did the headlight switch {'on' if target else 'off'}?")
    ok2 = await s.write(cur.light, cur.assist, cur.mode, f"light back {'on' if cur.light else 'off'}")
    seen2 = await ask(f"Did it switch back {'on' if cur.light else 'off'}?")
    s.results["light write"] = (f"{'OK' if ok1 and ok2 else 'FAILED'} by re-read; "
                                f"user saw change: {seen1}/{seen2}")


async def step_mode(s: Session) -> None:
    """Ride mode write: mode 5 briefly, then back to mode 4"""
    log(f"This writes mode {TEST_MODE} ({MODE_NAMES[TEST_MODE]}, 35 km/h / 250 W) for a few seconds,")
    log(f"then mode {LEGAL_MODE} ({MODE_NAMES[LEGAL_MODE]}). Keep the bike parked, don't ride it.")
    if await ask("Bike is parked. Run the mode test?") == "n":
        s.results["mode write"] = "skipped"
        return
    cur = await read_settings(s.client) if s.connected else s.original
    ok1 = await s.write(cur.light, cur.assist, TEST_MODE, f"mode {TEST_MODE}")
    seen1 = await ask(f"Does the display show a change to mode {TEST_MODE}? (? = display doesn't show it)", ("y", "n", "?"))
    ok2 = await s.write(cur.light, cur.assist, LEGAL_MODE, f"mode {LEGAL_MODE}")
    seen2 = await ask(f"Does the display show mode {LEGAL_MODE} again?", ("y", "n", "?"))
    s.results["mode write"] = (f"{'OK' if ok1 and ok2 else 'FAILED'} by re-read; "
                               f"user saw change: {seen1}/{seen2}")


async def step_display_buttons(s: Session) -> None:
    """Do changes made on the bike's own buttons show up as notifications? (20 s)"""
    await s.ensure_connected()
    await pause("For the next 20 s press the assist +/- and light buttons on the bike a few times. Press Enter to start")
    start = time.monotonic()
    await asyncio.sleep(20)
    n = sum(1 for t, f in s.frames if t >= start and is_settings(f))
    log(f"  {n} SETTINGS notifications during the 20 s")
    s.results["bike buttons -> notifications"] = f"{n} SETTINGS frames in 20 s"


async def step_restore(s: Session) -> None:
    """Restore light/assist from the start, mode 4"""
    if s.original is None:
        return
    ok = await s.write(s.original.light, s.original.assist, LEGAL_MODE, "restore")
    s.results["restore"] = "OK" if ok else "FAILED"


async def step_reconnect_timing(s: Session) -> None:
    """Reconnect timing by known address (what a Puck press would cost)"""
    times = []
    for i in range(2):
        await s.disconnect()
        await asyncio.sleep(2)
        t0 = time.monotonic()
        await s.connect(by_address=True)
        times.append(time.monotonic() - t0)
    s.results["reconnect by address -> first read"] = ", ".join(f"{t:.2f} s" for t in times)


async def step_power_cycle(s: Session) -> None:
    """Mode after power-on: does the bike reset to mode 4? (~2 min, optional)"""
    log(f"Sets mode {TEST_MODE}, then you switch the bike off and on, and the test checks which mode it")
    log(f"comes back in. The test sets mode {LEGAL_MODE} again at the end.")
    if await ask("Run the power-cycle test?") == "n":
        s.results["mode after power-on"] = "skipped"
        return
    cur = await read_settings(s.client) if s.connected else s.original
    await s.write(cur.light, cur.assist, TEST_MODE, f"mode {TEST_MODE} before power cycle")
    await s.disconnect()
    await pause("Switch the bike OFF and wait ~5 s. Then switch it ON and press Enter at the same moment")
    t_adv = await wait_for_adv(s.address, 90)
    if t_adv is None:
        log("  no advert within 90 s after power-on")
        s.results["mode after power-on"] = "bike not seen after power-on"
        return
    log(f"  first advert {t_adv:.1f} s after power-on")
    await s.connect(by_address=True)
    after = await read_settings(s.client)
    verdict = "RESET to 4" if after.mode == LEGAL_MODE else f"KEPT {after.mode}" if after.mode == TEST_MODE else "changed"
    s.results["mode after power-on"] = f"{verdict}: {after}"
    s.results["first advert after power-on"] = f"{t_adv:.1f} s"
    if after.mode != LEGAL_MODE:
        await s.write(after.light, after.assist, LEGAL_MODE, f"back to mode {LEGAL_MODE}")
    else:
        s.mode_dirty = False


async def step_idle_timer(s: Session) -> None:
    """Idle auto-off time (up to 25 min, optional, you can walk away)"""
    log("Disconnects, then watches the bike's adverts until it switches itself off.")
    log("Don't touch the bike. Measured from now; the last activity was your button presses.")
    if await ask("Run the idle timer?") == "n":
        s.results["idle auto-off"] = "skipped"
        return
    await s.disconnect()
    logging.getLogger("bleak").setLevel(logging.INFO)  # per-advert debug lines would flood the file
    t0 = time.monotonic()
    last = [t0]

    def on_adv(device, _adv) -> None:
        if device.address == s.address:
            last[0] = time.monotonic()

    next_report = 60
    async with BleakScanner(detection_callback=on_adv):
        while time.monotonic() - t0 < 25 * 60:
            await asyncio.sleep(5)
            if time.monotonic() - last[0] > 60:
                break
            if time.monotonic() - t0 >= next_report:
                log(f"  still advertising after {next_report // 60} min")
                next_report += 60
    off = last[0] - t0
    if time.monotonic() - last[0] > 60:
        s.results["idle auto-off"] = f"last advert {off / 60:.1f} min after disconnect"
    else:
        s.results["idle auto-off"] = "still on after 25 min"
    log(f"  {s.results['idle auto-off']}")


STEPS = [
    step_scan,
    step_connect,
    step_device_info,
    step_registers,
    step_adv_while_connected,
    step_light,
    step_mode,
    step_display_buttons,
    step_restore,
    step_reconnect_timing,
    step_power_cycle,
    step_idle_timer,
]
REQUIRED = {step_scan, step_connect}


async def main() -> None:
    path = setup_logging()
    log("SUPER73 Phase 1 test")
    log(f"log: {path}")
    LOG.debug(f"python {sys.version.split()[0]}, bleak {importlib.metadata.version('bleak')}, {platform.platform()}")
    log("")
    log("Before starting:")
    log("  - bike switched ON, parked next to this PC (on its stand, nobody riding it)")
    log("  - Home Assistant: hasuper73 integration DISABLED (Settings > Devices & services)")
    log("  - phone: SUPER73 app closed (or phone Bluetooth off)")
    log("Steps 1-10 take about 5 minutes; 11 and 12 are optional. Ctrl+C aborts (then press Enter).")
    await pause("Ready?")

    s = Session()
    try:
        for i, step in enumerate(STEPS, 1):
            log("")
            log(f"=== {i}/{len(STEPS)} {step.__doc__} ===")
            try:
                await step(s)
            except Abort:
                raise
            except Exception as err:
                log(f"  STEP FAILED: {type(err).__name__}: {err}")
                LOG.debug(traceback.format_exc())
                s.results[f"step {i} error"] = f"{type(err).__name__}: {err}"
                if step in REQUIRED:
                    raise Abort(f"required step {i} failed") from err
                if await ask("Continue with the next step?") == "n":
                    raise Abort("stopped by user") from err
    except Abort as err:
        log(f"ABORTED: {err}")
    finally:
        if s.mode_dirty:
            try:
                cur = await read_settings(s.client) if s.connected else None
                if cur is not None:
                    await s.write(cur.light, cur.assist, LEGAL_MODE, "safety: back to mode 4")
            except Exception as err:
                log(f"  safety reset failed: {err}")
        await s.disconnect()

        log("")
        log("=== RESULTS ===")
        for key, value in s.results.items():
            log(f"  {key:36} {value}")
        if s.mode_dirty:
            log("")
            log(f"!!! The bike may still be in mode {TEST_MODE}. Run:  py tools\\set_mode.py --mode {LEGAL_MODE}")
        log("")
        log(f"Done. Tell Claude the test finished; the log is {path}")


if __name__ == "__main__":
    asyncio.run(main())
