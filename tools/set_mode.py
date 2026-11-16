#!/usr/bin/env python3
"""Read or change SUPER73 ride settings over BLE (Comodule Diamond Display).

Phase 1 gate for the Puck.js button: prove the settings write from a PC.

    py tools/set_mode.py                          read only: print address + settings
    py tools/set_mode.py --light on               safest first write (headlight)
    py tools/set_mode.py --mode 4                 EU EPAC, 25 km/h / 250 W
    py tools/set_mode.py --mode 5 --allow-unrestricted
    py tools/set_mode.py --listen 60 -v           stay connected, print every frame

Protocol: docs/protocol.md. Constants match hasuper73 / Walker73.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from bleak import BleakClient, BleakScanner

METRICS_SERVICE = "00001554-1212-efde-1523-785feabcd123"
REGISTER_ID_CHAR = "00001564-1212-efde-1523-785feabcd123"
REGISTER_CHAR = "0000155f-1212-efde-1523-785feabcd123"
NOTIFIER_CHAR = "0000155e-1212-efde-1523-785feabcd123"

SETTINGS_ID = bytes((0x03, 0x00))
SETTINGS_WRITE_ID = bytes((0x00, 0xD1))

REGISTER_NAMES = {
    bytes((0x00, 0x00)): "MYSTERY",
    bytes((0x02, 0x01)): "MOTION",
    bytes((0x02, 0x02)): "TOTAL",
    bytes((0x02, 0x03)): "RIDE",
    bytes((0x03, 0x00)): "SETTINGS",
    bytes((0x04, 0x01)): "POWER",
}

MODE_NAMES = (
    "US CLASS1",
    "US CLASS2",
    "US CLASS3",
    "US OFF-ROAD",
    "EU EPAC",
    "EU MID",
    "EU HIGH",
    "EU OFF-ROAD",
)
LEGAL_MODE = 4  # EU EPAC (25 km/h, 250 W): the only pedelec-legal mode in Austria


class Settings:
    """Decoded SETTINGS (03 00) frame."""

    def __init__(self, frame: bytes) -> None:
        self.frame = frame
        self.assist = frame[2]
        self.light = frame[4] == 0x01
        self.mode = frame[5]

    def __str__(self) -> str:
        name = MODE_NAMES[self.mode] if self.mode < len(MODE_NAMES) else "?"
        light = "on" if self.light else "off"
        return f"mode {self.mode} ({name}), assist {self.assist}, light {light}  [{self.frame.hex(' ')}]"


def is_settings(frame: bytes) -> bool:
    return len(frame) == 10 and frame[:2] == SETTINGS_ID


async def find_bike(address: str | None, timeout: float):
    if address:
        return await BleakScanner.find_device_by_address(address, timeout=timeout)

    def match(device, adv) -> bool:
        name = (adv.local_name or device.name or "").lower()
        return METRICS_SERVICE in adv.service_uuids or name.startswith("super73")

    return await BleakScanner.find_device_by_filter(match, timeout=timeout)


async def read_settings(client: BleakClient) -> Settings:
    await client.write_gatt_char(REGISTER_ID_CHAR, SETTINGS_ID, response=True)
    frame = bytes(await client.read_gatt_char(REGISTER_CHAR))
    if not is_settings(frame):
        raise RuntimeError(f"unexpected SETTINGS frame: {frame.hex(' ')}")
    return Settings(frame)


async def wait_for_settings(
    client: BleakClient, frames: asyncio.Queue, mode: int, light: bool, timeout: float = 3.0
) -> Settings:
    """Wait for a SETTINGS notification with the new values, else re-read the register."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while (remaining := deadline - loop.time()) > 0:
        try:
            settings = Settings(await asyncio.wait_for(frames.get(), remaining))
        except asyncio.TimeoutError:
            break
        if settings.mode == mode and settings.light == light:
            print("  confirmed by notification")
            return settings
    print("  no matching notification, re-reading register")
    return await read_settings(client)


async def run(args: argparse.Namespace) -> int:
    print("Scanning ...")
    device = await find_bike(args.address, args.scan_timeout)
    if device is None:
        print(
            "Bike not found. Is it switched on and close by? Is Home Assistant "
            "(hasuper73) or the SUPER73 app connected to it? The bike may accept "
            "only one connection."
        )
        return 2
    print(f"Found {device.name} at {device.address}")

    settings_frames: asyncio.Queue = asyncio.Queue()

    def on_notify(_char, data: bytearray) -> None:
        frame = bytes(data)
        if args.verbose or is_settings(frame):
            print(f"  notify {REGISTER_NAMES.get(frame[:2], '?'):8} {frame.hex(' ')}")
        if is_settings(frame):
            settings_frames.put_nowait(frame)

    rc = 0
    async with BleakClient(device, timeout=args.connect_timeout) as client:
        try:
            current = await read_settings(client)
        except Exception as err:  # backends word this differently
            if "authenticat" not in str(err).lower():
                raise
            print("Bike requires authentication, pairing ...")
            await client.pair()
            current = await read_settings(client)
        print(f"Current: {current}")

        await client.start_notify(NOTIFIER_CHAR, on_notify)

        if args.mode is not None or args.light is not None:
            mode = current.mode if args.mode is None else args.mode
            light = current.light if args.light is None else args.light == "on"
            payload = SETTINGS_WRITE_ID + bytes((int(light), current.assist, mode, 0, 0, 0, 0, 0))
            print(f"Write:   {payload.hex(' ')}")
            await client.write_gatt_char(REGISTER_CHAR, payload, response=True)
            result = await wait_for_settings(client, settings_frames, mode, light)
            ok = result.mode == mode and result.light == light
            print(f"{'OK' if ok else 'FAILED'}:      {result}")
            rc = 0 if ok else 1

        if args.listen:
            print(f"Listening for {args.listen} s ...")
            await asyncio.sleep(args.listen)

    return rc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--address", help="bike BLE address (default: scan by service UUID / name)")
    parser.add_argument("--mode", type=int, choices=range(len(MODE_NAMES)), help="ride mode to write")
    parser.add_argument("--allow-unrestricted", action="store_true", help=f"allow modes other than {LEGAL_MODE} (EU EPAC)")
    parser.add_argument("--light", choices=("on", "off"), help="headlight to write")
    parser.add_argument("--listen", type=float, default=0, metavar="SECONDS", help="stay connected and print notifications")
    parser.add_argument("-v", "--verbose", action="store_true", help="print every notification, not just SETTINGS")
    parser.add_argument("--scan-timeout", type=float, default=15.0)
    parser.add_argument("--connect-timeout", type=float, default=20.0)
    args = parser.parse_args()

    if args.mode is not None and args.mode != LEGAL_MODE and not args.allow_unrestricted:
        parser.error(
            f"mode {args.mode} ({MODE_NAMES[args.mode]}) is above the Austrian pedelec limit; "
            "add --allow-unrestricted to write it anyway"
        )

    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
