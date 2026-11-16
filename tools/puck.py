#!/usr/bin/env python3
"""Talk to an Espruino Puck.js over its BLE console (Nordic UART service).

    py tools/puck.py "process.env"                  run JS, print the output
    py tools/puck.py "bike.read()" --wait 20        keep listening for async output
    py tools/puck.py --flash src/main.js            store as boot code (.bootcde), restart
    py tools/puck.py --scan                         list nearby Espruino consoles

Picks the strongest device advertising the Nordic UART service or a "Puck.js"
name, unless --address is given. Each run connects, sends, prints, disconnects.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from bleak import BleakClient, BleakScanner

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # we write here
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Puck notifies here
CHUNK = 20  # bytes per BLE write, what the Espruino IDE uses
FLASH_CHUNK = 256  # characters of code per Storage.write call


def is_console(device, adv) -> bool:
    name = (adv.local_name or device.name or "").lower()
    return NUS_SERVICE in adv.service_uuids or name.startswith("puck.js")


async def find(address: str | None):
    if address:
        # The Puck may advertise only every few seconds; connect right after an advert
        return await BleakScanner.find_device_by_address(address, timeout=60)
    found = await BleakScanner.discover(timeout=6.0, return_adv=True)
    consoles = [(d, a) for d, a in found.values() if is_console(d, a)]
    return max(consoles, key=lambda x: x[1].rssi)[0] if consoles else None


class Console:
    def __init__(self, client: BleakClient) -> None:
        self.client = client
        self.last = time.monotonic()
        self.text = ""

    def on_data(self, _char, data: bytearray) -> None:
        chunk = bytes(data).decode("latin-1")
        self.text += chunk
        self.last = time.monotonic()
        sys.stdout.write(chunk)
        sys.stdout.flush()

    async def send(self, text: str) -> None:
        data = text.encode("latin-1")
        for i in range(0, len(data), CHUNK):
            await self.client.write_gatt_char(NUS_RX, data[i:i + CHUNK], response=True)

    async def settle(self, wait: float, idle: float = 0.6) -> None:
        """Listen for at least `wait` s, then until the Puck is quiet for `idle` s."""
        start = time.monotonic()
        while time.monotonic() - start < wait or time.monotonic() - self.last < idle:
            await asyncio.sleep(0.05)

    async def run(self, js: str, wait: float, quiet: bool = False) -> None:
        # quiet: \x10 prefix - Espruino neither echoes the line nor prints its result
        await self.send(("\x10" if quiet else "") + js + "\n")
        await self.settle(wait)


async def flash(con: Console, path: Path) -> None:
    code = path.read_text(encoding="utf-8")
    if not code.isascii():
        sys.exit(f"{path} must be plain ASCII (Storage byte count would not match)")
    total = len(code)
    print(f"Writing {path} ({total} bytes) to .bootcde ...")
    for offset in range(0, total, FLASH_CHUNK):
        part = json.dumps(code[offset:offset + FLASH_CHUNK])
        size = f",{total}" if offset == 0 else ""
        con.text = ""
        await con.run(f'print(require("Storage").write(".bootcde",{part},{offset}{size}))', wait=0.2, quiet=True)
        if "true" not in con.text:
            sys.exit(f"\nStorage.write failed at offset {offset}")
    print("Restarting with load() ...")
    await con.run("load()", wait=3, quiet=True)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("js", nargs="*", help="JavaScript lines to run, one per argument")
    parser.add_argument("--address", help="Puck BLE address (default: strongest Espruino console)")
    parser.add_argument("--wait", type=float, default=1.0, help="seconds to listen after each line (default 1)")
    parser.add_argument("--flash", type=Path, metavar="FILE", help="store FILE as boot code and restart")
    parser.add_argument("--scan", action="store_true", help="list Espruino consoles and exit")
    args = parser.parse_args()

    if args.scan:
        found = await BleakScanner.discover(timeout=6.0, return_adv=True)
        for d, a in sorted(found.values(), key=lambda x: -x[1].rssi):
            if is_console(d, a):
                print(f"{a.rssi:5} {d.address} {(a.local_name or d.name)!r}")
        return

    device = await find(args.address)
    if device is None:
        sys.exit("No Espruino console found (is the Puck powered and not connected to the IDE?)")
    print(f"[{device.name} {device.address}]")
    async with BleakClient(device, timeout=30) as client:
        con = Console(client)
        await client.start_notify(NUS_TX, con.on_data)
        await con.send("\x03")  # clear any half-typed input
        await con.settle(0.3)
        con.text = ""
        if args.flash:
            await flash(con, args.flash)
        for js in args.js:
            await con.run(js, args.wait)
    print()


if __name__ == "__main__":
    asyncio.run(main())
